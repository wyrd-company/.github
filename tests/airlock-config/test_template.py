# relationships: validates: release-process
"""Exercise the real Toha renderer and Airlock repository config loader."""
import json
import os
from pathlib import Path
import subprocess
import shutil
import tempfile
import unittest

from ruamel.yaml import YAML

ROOT = Path(__file__).resolve().parents[2]
BASE_HEAD = 'b22497203f2619b5d3ebcc6cb13d04ad334f6958'
FIELDS = dict(description='Sample utility: "quoted"\nsecond line', topics='cli rust',
              squash=False, rebase=False, merge_commit=True, delete_branch_on_merge=False,
              wiki=True, projects=True, discussions=True)


def expected_declared_bytes(version, topics):
    topic_list = ', '.join(f'"{topic}"' for topic in topics)
    return (b'# relationships:\n#   references: release-process\n'
            b'# Repository requests are honored only where organization authority permits.\n'
            b'# Add exceptions.<rule> with reason; severity: none requests a waiver.\n'
            b'# Classification and organization-only rule parameters do not belong here.\n'
            + f'version: "{version}"\n'.encode()
            + b'description: "Sample utility: \\"quoted\\"\\nsecond line"\n'
            + f'topics: [{topic_list}]\n'.encode()
            + b'merge:\n  squash: false\n  rebase: false\n  merge_commit: true\n'
            b'  delete_branch_on_merge: false\nfeatures:\n  wiki: true\n'
            b'  projects: true\n  discussions: true\n')


def expected_declared_config(version, topics):
    return {'version': version, 'description': FIELDS['description'], 'topics': topics,
            'merge': {'squash': False, 'rebase': False, 'merge_commit': True,
                      'delete_branch_on_merge': False},
            'features': {'wiki': True, 'projects': True, 'discussions': True}}


class AirlockTemplate(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory()
        self.addCleanup(self.scratch.cleanup)
        self.root = Path(self.scratch.name)
        self.target = self.root / 'project'
        self.target.mkdir()

    def apply(self, answers=None):
        envelope = self.root / 'answers.json'
        envelope.write_text(json.dumps({'template': str(ROOT / 'airlock-config'), 'answers': answers or {}}))
        return subprocess.run([os.environ['TOHA_BIN'], 'apply', str(ROOT / 'airlock-config'),
                               str(self.target), '--answers', str(envelope)], text=True, capture_output=True)

    def render(self, answers=None):
        result = self.apply(answers)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(json.loads(result.stdout)['status'], 'applied')
        self.file = self.target / '.github/airlock.yml'
        try:
            return YAML(typ='safe').load(self.file.read_text())
        except Exception as error:
            self.fail(f'Rendered YAML is invalid: {error}')

    def audit(self):
        # Airlock reads requests from committed HEAD, declarations from the tree.
        for command in (['init', '-b', 'main'], ['config', 'user.name', 'Example Agent'],
                        ['config', 'user.email', 'agent@example.invalid'],
                        ['remote', 'add', 'origin', 'https://github.com/example-org/sample-tool.git'],
                        ['add', '.'], ['commit', '-m', 'fixture']):
            subprocess.run(['git', '-C', str(self.target), *command], check=True, capture_output=True)
        policy = self.root / 'organization.yml'
        policy.write_text('version: 0.6.0\n')
        result = subprocess.run([os.environ['AIRLOCK_BIN'], 'audit', '--working-tree', str(self.target),
                                 '--policy', str(policy), '--format', 'json'], text=True, capture_output=True)
        return result

    def assert_loaded(self):
        result = self.audit()
        # Local audits contain unrelated incomplete platform observations. A report
        # with the intended repository proves the loader reached the audit boundary.
        report = json.loads(result.stdout)
        self.assertEqual(report['repository']['full_name'], 'example-org/sample-tool', result.stdout)
        self.assertEqual(report['run_errors'], [], result.stdout)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertNotIn('unknown policy key', result.stderr)
        return report

    def test_defaults(self):
        self.assertEqual(self.render(), {'version': '0.6.0'})
        self.assert_loaded()

    def test_all_optional_answers(self):
        result = self.render(dict(declare_repository=True, **FIELDS))
        self.assertEqual(result, dict(version='0.6.0', description=FIELDS['description'], topics=['cli', 'rust'],
            merge={key: FIELDS[key] for key in ('squash', 'rebase', 'merge_commit', 'delete_branch_on_merge')},
            features={key: FIELDS[key] for key in ('wiki', 'projects', 'discussions')}))
        self.assert_loaded()

    def test_declarations_are_opt_in(self):
        self.assertEqual(self.render(dict(declare_repository=False, **FIELDS)), {'version': '0.6.0'})

    def test_rendered_config_has_exactly_one_final_newline(self):
        historical_source = subprocess.check_output(
            ['git', 'show', f'{BASE_HEAD}:airlock-config/template/.github/airlock.yml'],
            cwd=ROOT, text=True)
        historical_template = self.root / 'historical-airlock-config'
        shutil.copytree(ROOT / 'airlock-config', historical_template)
        historical_file = historical_template / 'template/.github/airlock.yml'
        historical_file.write_text(historical_source)

        header = (b'# relationships:\n#   references: release-process\n'
                  b'# Repository requests are honored only where organization authority permits.\n'
                  b'# Add exceptions.<rule> with reason; severity: none requests a waiver.\n'
                  b'# Classification and organization-only rule parameters do not belong here.\n')
        cases = [({}, header + b'version: "0.6.0"\n', {'version': '0.6.0'})]
        for version in ('0.6.0', '1.2.3', '0.7.0-rc.1+sample.2'):
            cases.append((dict(declare_repository=True, accepted_generation=version, **FIELDS),
                          expected_declared_bytes(version, ['cli', 'rust']),
                          expected_declared_config(version, ['cli', 'rust'])))
        single_topic_answers = dict(declare_repository=True, **dict(FIELDS, topics='utility'))
        cases.append((single_topic_answers, expected_declared_bytes('0.6.0', ['utility']),
                      expected_declared_config('0.6.0', ['utility'])))
        for version in ('1.2.3', '0.7.0-rc.1+sample.2'):
            cases.append(({'accepted_generation': version},
                          header + f'version: "{version}"\n'.encode(), {'version': version}))

        for index, (answers, expected_bytes, expected_config) in enumerate(cases):
            with self.subTest(answers=answers):
                shutil.rmtree(self.target)
                self.target.mkdir()
                current = self.apply(answers)
                self.assertEqual(current.returncode, 0, current.stdout + current.stderr)
                self.file = self.target / '.github/airlock.yml'
                rendered = self.file.read_bytes()
                parsed = YAML(typ='safe').load(rendered.decode())
                self.assertEqual(parsed, expected_config)
                self.assertTrue(rendered.endswith(b'\n') and not rendered.endswith(b'\n\n'), rendered)
                self.assertEqual(rendered, expected_bytes)

                whitespace = subprocess.run(
                    ['git', 'diff', '--no-index', '--check', '/dev/null', str(self.file)],
                    text=True, capture_output=True)
                self.assertEqual(whitespace.returncode, 1, whitespace.stdout + whitespace.stderr)
                self.assertEqual(whitespace.stdout + whitespace.stderr, '')

                historical_answers = self.root / 'historical-answers.json'
                historical_answers.write_text(json.dumps(
                    {'template': str(historical_template), 'answers': answers}))
                historical_target = self.root / f'historical-project-{index}'
                historical_target.mkdir()
                historical = subprocess.run(
                    [os.environ['TOHA_BIN'], 'apply', str(historical_template), str(historical_target),
                     '--answers', str(historical_answers)], text=True, capture_output=True)
                self.assertEqual(historical.returncode, 0, historical.stdout + historical.stderr)
                old_file = historical_target / '.github/airlock.yml'
                self.assertEqual(YAML(typ='safe').load(old_file.read_text()), expected_config)
                lint = subprocess.run(
                    ['ryl', 'check', '-c', '.ryl.yml', '--stdin-filename', '.github/airlock.yml', '-'],
                    cwd=ROOT, input=rendered.decode(), text=True, capture_output=True)
                self.assertEqual(lint.returncode, 0, lint.stdout + lint.stderr)

    def test_unknown_repository_key_rejected(self):
        self.render()
        with self.file.open('a') as stream:
            stream.write('classification: tool\n')
        result = self.audit()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('unknown policy key `classification`', result.stdout + result.stderr)

    def test_generation_variants(self):
        for version in ('1.2.3', '0.7.0-rc.1+sample.2'):
            with self.subTest(version=version):
                self.assertEqual(self.render({'accepted_generation': version})['version'], version)
                self.assert_loaded()
                # Separate repositories for each committed input.
                shutil.rmtree(self.target)
                self.target.mkdir()

    def test_contract(self):
        config = YAML(typ='safe').load((ROOT / 'airlock-config/template.yml').read_text())
        self.assertEqual(config['name'], 'airlock-config')
        self.assertNotIn('hooks', config)
        self.assertNotIn('schemas', config)
        self.assertFalse(any(q.get('type') == 'document' for q in config['interview']))
        for question in config['interview'][2:]:
            self.assertEqual(question.get('when'), 'declare_repository', question['id'])
        self.assertEqual({q['id'] for q in config['interview']},
                         {'accepted_generation', 'declare_repository', *FIELDS})

    def assert_refused(self, answers):
        result = self.apply(answers)
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertFalse((self.target / '.github/airlock.yml').exists())

    def test_generation_guard(self):
        for value in ('v0.6.0', '0.6', '01.2.3', '1.2.3-01', '1.2.3\nclassification: tool'):
            with self.subTest(value=value):
                self.assert_refused({'accepted_generation': value})

    def test_generation_required(self):
        self.assert_refused({'accepted_generation': ''})

    def test_description_required(self):
        self.assert_refused(dict(declare_repository=True, **dict(FIELDS, description='')))

    def test_description_guard(self):
        self.assert_refused(dict(declare_repository=True, **dict(FIELDS, description='   ')))

    def test_topics_required(self):
        self.assert_refused(dict(declare_repository=True, **dict(FIELDS, topics='')))

    def test_topics_guard(self):
        self.assert_refused(dict(declare_repository=True, **dict(FIELDS, topics='cli\nclassification: tool')))


if __name__ == '__main__':
    unittest.main()
