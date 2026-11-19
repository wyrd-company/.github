# relationships: validates: delivery-environments
"""The delivery-environments check refuses each breach it names, so a passing render proves something."""
import unittest

from delivery_environments import violations

DECLARATION = {'environments': {'airlock-safe': {'variables': ['AZURE_CLIENT_ID']}}}
CALLER = '''
permissions: {}
jobs:
  audit:
    permissions:
      id-token: write
    uses: ./.github/workflows/delivery-airlock-safe.yml
'''
CALLED = '''
on:
  workflow_call:
permissions: {}
jobs:
  audit:
    runs-on: ubuntu-latest
    environment:
      name: airlock-safe
      deployment: false
    permissions:
      id-token: write
    steps:
      - uses: example/action@0000000000000000000000000000000000000000 # 1.0.0
        with:
          client: ${{ vars.AIRLOCK_SAFE__AZURE_CLIENT_ID }}
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
        self.assertEqual(violations({'caller.yml': CALLER, 'delivery-airlock-safe.yml': CALLED, 'plain.yml': PLAIN},
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
                        DECLARATION, "secrets.['NPM_TOKEN']")

    def test_variable_outside_a_credential_workflow(self):
        self.assert_one({'plain.yml': job('    steps:\n      - run: echo ${{ vars.AIRLOCK_SAFE__AZURE_CLIENT_ID }}\n')},
                        DECLARATION, 'outside a credential environment workflow')

    def test_variable_outside_any_job(self):
        self.assert_one({'plain.yml': 'run-name: ${{ vars.X }}\n' + job('    steps: []\n')},
                        DECLARATION, 'vars.X outside a job')

    def test_variable_not_declared(self):
        self.assert_one({'delivery-airlock-safe.yml': CALLED.replace('AIRLOCK_SAFE__AZURE_CLIENT_ID', 'AIRLOCK_SAFE__AZURE_TENANT_ID')},
                        DECLARATION, 'not a variable declared')

    def test_variable_of_another_environment(self):
        self.assert_one({'delivery-airlock-safe.yml': CALLED.replace('AIRLOCK_SAFE__AZURE', 'RELEASE_AUTHORITY__AZURE')},
                        DECLARATION, 'not a variable declared')

    def test_credential_workflow_of_an_undeclared_environment(self):
        found = violations({'delivery-airlock-safe.yml': CALLED}, {'environments': {}})
        self.assertEqual(len(found), 3, found)

    def test_environment_outside_its_workflow(self):
        self.assert_one({'plain.yml': job('    environment: airlock-safe\n    steps: []\n')},
                        DECLARATION, 'environment airlock-safe outside')

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
        self.assert_one({'caller.yml': CALLER.replace('delivery-airlock-safe.yml', 'other.yml')},
                        DECLARATION, 'id-token: write')


if __name__ == '__main__':
    unittest.main()
