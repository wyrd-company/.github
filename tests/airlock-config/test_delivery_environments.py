# relationships: validates: delivery-environments
"""The delivery-environments check refuses each breach it names, so a passing render proves something."""
import unittest

from delivery_environments import violations

DECLARATION = {'environments': {'package-tags': {'variables': ['AZURE_CLIENT_ID']}}}
CALLER = '''
permissions: {}
jobs:
  audit:
    permissions:
      id-token: write
    uses: ./.github/workflows/delivery-package-tags.yml
'''
CALLED = '''
on:
  workflow_call:
permissions: {}
jobs:
  audit:
    runs-on: ubuntu-latest
    environment:
      name: package-tags
      deployment: false
    permissions:
      id-token: write
    steps:
      - uses: example/action@0000000000000000000000000000000000000000 # 1.0.0
        with:
          client: ${{ vars.PACKAGE_TAGS__AZURE_CLIENT_ID }}
          token: ${{ secrets.GITHUB_TOKEN }}
'''
PLAIN = '''
permissions: {}
jobs:
  audit:
    runs-on: ubuntu-latest
    permissions:
      contents: read
    steps:
      - run: echo hello
'''


def job(body):
    return 'permissions: {}\njobs:\n  audit:\n    runs-on: ubuntu-latest\n' + body


class DeliveryEnvironmentsCheck(unittest.TestCase):
    def test_conforming_workflows_pass(self):
        self.assertEqual(violations({'caller.yml': CALLER, 'delivery-package-tags.yml': CALLED, 'plain.yml': PLAIN},
                                    DECLARATION), [])

    def assert_one(self, workflows, declaration, fragment):
        found = violations(workflows, declaration)
        self.assertEqual(len(found), 1, found)
        self.assertIn(fragment, found[0])

    def test_secret_other_than_the_run_token(self):
        self.assert_one({'plain.yml': job('    steps:\n      - run: echo "$T"\n        env:\n          T: ${{ secrets.NPM_TOKEN }}\n')},
                        DECLARATION, 'secrets.NPM_TOKEN')

    def test_secret_by_index(self):
        self.assert_one({'plain.yml': job("    if: secrets['NPM_TOKEN'] != ''\n    steps: []\n")},
                        DECLARATION, 'secrets.NPM_TOKEN')

    def test_secret_in_another_case(self):
        self.assert_one({'plain.yml': job('    steps:\n      - run: echo ${{ SECRETS.npm_token }}\n')},
                        DECLARATION, 'secrets.NPM_TOKEN')

    def test_run_token_by_index_or_case_passes(self):
        self.assertEqual(violations({'plain.yml': job(
            "    steps:\n      - run: echo \"${{ secrets['GITHUB_TOKEN'] }} ${{ secrets.github_token }}\"\n")},
            DECLARATION), [])

    def test_whole_secrets_context_through_to_json(self):
        self.assert_one({'plain.yml': job('    steps:\n      - run: echo "$S"\n        env:\n'
                                          '          S: ${{ toJSON(secrets) }}\n')},
                        DECLARATION, 'the whole secrets context')

    def test_whole_secrets_context_alone(self):
        self.assert_one({'plain.yml': job('    steps:\n      - uses: example/action@'
                                          '0000000000000000000000000000000000000000 # 1.0.0\n'
                                          '        with:\n          all: ${{ secrets }}\n')},
                        DECLARATION, 'the whole secrets context')

    def test_secret_by_computed_index(self):
        self.assert_one({'plain.yml': job('    steps:\n      - run: echo ${{ secrets[inputs.name] }}\n')},
                        DECLARATION, 'the whole secrets context')

    def test_whole_secrets_context_in_a_bare_condition(self):
        self.assert_one({'plain.yml': job("    if: contains(toJSON(secrets), 'x')\n    steps: []\n")},
                        DECLARATION, 'the whole secrets context')

    def test_whole_secrets_context_outside_any_job(self):
        self.assert_one({'plain.yml': 'env:\n  S: ${{ toJSON(secrets) }}\n' + job('    steps: []\n')},
                        DECLARATION, 'the whole secrets context outside a job')

    def test_inherited_secrets(self):
        self.assert_one({'caller.yml': CALLER + '    secrets: inherit\n'}, DECLARATION, 'secrets: inherit')

    def test_whole_vars_context_even_in_its_credential_workflow(self):
        self.assert_one({'delivery-package-tags.yml': CALLED.replace(
            '${{ vars.PACKAGE_TAGS__AZURE_CLIENT_ID }}', '${{ toJSON(vars) }}')},
            DECLARATION, 'the whole vars context')

    def test_whole_vars_context_elsewhere(self):
        self.assert_one({'plain.yml': job('    steps:\n      - run: echo ${{ toJSON(vars) }}\n')},
                        DECLARATION, 'the whole vars context')

    def test_names_in_prose_and_literals_are_not_reads(self):
        self.assertEqual(violations({'plain.yml': job(
            "    steps:\n      - name: Read no secrets and no vars\n"
            "        run: echo \"${{ format('secrets {0} vars', github.sha) }} ${{ github.event.secrets }}\"\n")},
            DECLARATION), [])

    def test_variable_outside_a_credential_workflow(self):
        self.assert_one({'plain.yml': job('    steps:\n      - run: echo ${{ vars.PACKAGE_TAGS__AZURE_CLIENT_ID }}\n')},
                        DECLARATION, 'outside a credential environment workflow')

    def test_variable_outside_any_job(self):
        self.assert_one({'plain.yml': 'run-name: ${{ vars.X }}\n' + job('    steps: []\n')},
                        DECLARATION, 'vars.X outside a job')

    def test_variable_not_declared(self):
        self.assert_one({'delivery-package-tags.yml': CALLED.replace('PACKAGE_TAGS__AZURE_CLIENT_ID', 'PACKAGE_TAGS__AZURE_TENANT_ID')},
                        DECLARATION, 'not a variable declared')

    def test_variable_of_another_environment(self):
        self.assert_one({'delivery-package-tags.yml': CALLED.replace('PACKAGE_TAGS__AZURE', 'RELEASE_AUTHORITY__AZURE')},
                        DECLARATION, 'not a variable declared')

    def test_credential_workflow_of_an_undeclared_environment(self):
        found = violations({'delivery-package-tags.yml': CALLED}, {'environments': {}})
        self.assertEqual(len(found), 3, found)

    def test_environment_outside_its_workflow(self):
        self.assert_one({'plain.yml': job('    environment: package-tags\n    steps: []\n')},
                        DECLARATION, 'environment package-tags outside')

    def test_id_token_in_another_job(self):
        self.assert_one({'plain.yml': job('    permissions:\n      id-token: write\n    steps: []\n')},
                        DECLARATION, 'id-token: write')

    def test_id_token_through_write_all(self):
        self.assert_one({'plain.yml': job('    permissions: write-all\n    steps: []\n')},
                        DECLARATION, 'id-token: write')

    def test_id_token_at_the_top_level(self):
        self.assert_one({'plain.yml': PLAIN.replace('permissions: {}', 'permissions:\n  id-token: write', 1)},
                        DECLARATION, 'at the top level')

    def test_caller_of_a_workflow_that_is_not_a_credential_environment(self):
        self.assert_one({'caller.yml': CALLER.replace('delivery-package-tags.yml', 'other.yml')},
                        DECLARATION, 'id-token: write')

    # Span boundaries: a `}}` inside a single-quoted literal does not end the expression.

    def env_step(self, expression):
        return job('    steps:\n      - run: echo "$P"\n        env:\n          P: ' + expression + '\n')

    def test_quoted_delimiter_before_whole_secrets(self):
        self.assert_one({'plain.yml': self.env_step("${{ '}}' && toJSON(secrets) }}")},
                        DECLARATION, 'the whole secrets context')

    def test_quoted_delimiter_before_whole_vars(self):
        self.assert_one({'plain.yml': self.env_step("${{ '}}' && toJSON(vars) }}")},
                        DECLARATION, 'the whole vars context')

    def test_quoted_delimiter_in_a_format_string(self):
        self.assert_one({'plain.yml': self.env_step("${{ format('}}{0}', toJSON(secrets)) }}")},
                        DECLARATION, 'the whole secrets context')

    def test_escaped_quote_next_to_a_quoted_delimiter(self):
        for expression in ("${{ format('it''s }}', toJSON(secrets)) }}",
                           "${{ format('''}}''', toJSON(secrets)) }}",
                           "${{ format('}}''', toJSON(secrets)) }}"):
            with self.subTest(expression):
                self.assert_one({'plain.yml': self.env_step(expression)}, DECLARATION, 'the whole secrets context')

    def test_escaped_quotes_around_a_delimiter_are_one_literal(self):
        self.assertEqual(violations({'plain.yml': self.env_step("${{ 'a''}}''b' }}")}, DECLARATION), [])

    def test_unterminated_quote_still_reads_what_follows(self):
        self.assert_one({'plain.yml': self.env_step("${{ 'open toJSON(secrets) }}")},
                        DECLARATION, 'the whole secrets context')

    def test_context_names_inside_literals_are_not_reads(self):
        for expression in ("${{ format('literal }} ${{ secrets.SYNTHETIC_PLACEHOLDER {0}', github.sha) }}",
                           "${{ 'toJSON(secrets)' }}",
                           "${{ format('{0} secrets vars', 'toJSON(vars)') }}",
                           "${{ 'it''s toJSON(secrets)' }}"):
            with self.subTest(expression):
                self.assertEqual(violations({'plain.yml': self.env_step(expression)}, DECLARATION), [])

    def test_several_spans_on_one_line(self):
        self.assert_one({'plain.yml': self.env_step("${{ github.sha }} ${{ '}}' }} ${{ toJSON(secrets) }}")},
                        DECLARATION, 'the whole secrets context')
        found = violations({'plain.yml': self.env_step("${{ 'x' }}${{ secrets.NPM_TOKEN }} ${{ vars.SOME_NAME }}")},
                           DECLARATION)
        self.assertEqual(len(found), 2, found)
        self.assertIn('secrets.NPM_TOKEN', found[0])
        self.assertIn('vars.SOME_NAME outside a credential environment workflow', found[1])
        self.assertEqual(violations({'plain.yml': self.env_step(
            "${{ github.sha }}-${{ 'secrets }}' }}-${{ github.run_id }}")}, DECLARATION), [])

if __name__ == '__main__':
    unittest.main()
