# relationships: validates: release-process
"""Exercise the real Toha renderer and Airlock repository config loader."""
import json
import os
import re
from pathlib import Path
import subprocess
import shutil
import tempfile
import unittest

from jsonschema import Draft202012Validator
from ruamel.yaml import YAML

from delivery_environments import violations

ROOT = Path(__file__).resolve().parents[2]
FIELDS = dict(description='Sample utility: "quoted"\nsecond line', topics='cli rust',
              squash=False, rebase=False, merge_commit=True, delete_branch_on_merge=False,
              wiki=True, projects=True, discussions=True)


PER_REPOSITORY = {'.github/airlock.yml', '.github/workflows/airlock-audit.yml', '.github/delivery/airlock-config.json'}
DECLARATIONS = ROOT / 'docs/specifications/assets/delivery-declaration'
SCHEMA = json.loads((DECLARATIONS / 'delivery-declaration-1.schema.json').read_text())
EXAMPLE = json.loads((DECLARATIONS / 'airlock-config.example.json').read_text())
POLICY = '${{ github.repository_owner }}/.github:airlock/policy.yml'
PIN = re.compile(r'^ *(?:- )?uses: (?P<ref>[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:/[^@\s]+)?)@(?P<sha>[0-9a-f]{40}) # \S.*$')
DAILY = re.compile(r'^[0-9]{1,2} [0-9]{1,2} \* \* \*$')


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
        envelope.write_text(json.dumps({'template': str(ROOT / 'templates/airlock-config'), 'answers': answers or {}}))
        return subprocess.run([os.environ['TOHA_BIN'], 'apply', str(ROOT / 'templates/airlock-config'),
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

        for answers, expected_bytes, expected_config in cases:
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
        config = YAML(typ='safe').load((ROOT / 'templates/airlock-config/template.yml').read_text())
        self.assertEqual(config['name'], 'airlock-config')
        self.assertNotIn('hooks', config)
        self.assertNotIn('schemas', config)
        self.assertFalse(any(q.get('type') == 'document' for q in config['interview']))
        questions = {q['id']: q for q in config['interview']}
        for key in FIELDS:
            self.assertEqual(questions[key].get('when'), 'declare_repository', key)
        self.assertEqual(set(questions), {'accepted_generation', 'declare_repository', *FIELDS,
                                          'default_branch'})
        self.assertEqual({key: questions['default_branch'].get(key) for key in ('type', 'default', 'when')},
                         {'type': 'text', 'default': 'main', 'when': None})

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


    # Audit workflows and the delivery declaration.

    def rendered_files(self):
        return {path.relative_to(self.target).as_posix() for path in self.target.rglob('*')
                if path.is_file() and '.git' not in path.relative_to(self.target).parts}

    def rendered_workflows(self):
        return {path.name: path.read_text() for path in sorted((self.target / '.github/workflows').glob('*.yml'))}

    def declaration(self):
        return json.loads((self.target / '.github/delivery/airlock-config.json').read_text())

    def workflow(self, name):
        return YAML(typ='safe').load((self.target / '.github/workflows' / name).read_text())

    def only_step(self, job):
        self.assertEqual(len(job['steps']), 1, job['steps'])
        return job['steps'][0]

    def test_per_repository_files(self):
        self.render()
        self.assertEqual(self.rendered_files(), PER_REPOSITORY)

    def test_per_repository_files_for_every_answer(self):
        self.render(dict(declare_repository=True, **FIELDS))
        self.assertEqual(self.rendered_files(), PER_REPOSITORY)
        self.assertEqual(self.declaration()['environments'], {})

    def test_declaration_validates(self):
        self.render()
        rendered = (self.target / '.github/delivery/airlock-config.json').read_text()
        declaration = json.loads(rendered)
        self.assertEqual(list(Draft202012Validator(SCHEMA).iter_errors(declaration)), [])
        self.assertEqual(declaration, EXAMPLE)
        self.assertEqual(rendered, json.dumps(declaration, indent=2, sort_keys=True, ensure_ascii=False) + '\n')

    def test_audit_workflow(self):
        for branch in ('main', 'release/1.x'):
            with self.subTest(default_branch=branch):
                shutil.rmtree(self.target)
                self.target.mkdir()
                self.render({'default_branch': branch})
                workflow = self.workflow('airlock-audit.yml')
                self.assertEqual(set(workflow['on']), {'schedule', 'workflow_dispatch', 'push'})
                self.assertEqual(len(workflow['on']['schedule']), 1)
                self.assertRegex(workflow['on']['schedule'][0]['cron'], DAILY)
                self.assertIsNone(workflow['on']['workflow_dispatch'])
                self.assertEqual(workflow['on']['push'], {'branches': [branch]})
                self.assertEqual(workflow.get('permissions', 'absent'), {})
                self.assertEqual(set(workflow['jobs']), {'audit'})
                job = workflow['jobs']['audit']
                self.assertEqual(job['permissions'], {'contents': 'read', 'actions': 'read', 'attestations': 'read'})
                self.assertNotIn('environment', job)
                self.assertNotIn('env', job)
                step = self.only_step(job)
                self.assertTrue(step['uses'].startswith('wyrd-company/airlock@'), step)
                self.assertEqual(step['with'], {'mode': 'repository', 'token': '${{ github.token }}', 'policy': POLICY})

    def test_delivery_environments(self):
        self.render()
        self.assertEqual(violations(self.rendered_workflows(), self.declaration()), [])

    def test_actionlint(self):
        self.render()
        # actionlint resolves ./ reusable workflows from the repository root.
        subprocess.run(['git', '-C', str(self.target), 'init', '-q'], check=True)
        result = subprocess.run(['actionlint', *sorted(str(p) for p in (self.target / '.github/workflows').glob('*.yml'))],
                                cwd=self.target, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_action_pins(self):
        self.render()
        pins = set()
        for name, text in self.rendered_workflows().items():
            for line in text.splitlines():
                if re.match(r'^ *(?:- )?uses: ', line) and 'uses: ./' not in line:
                    match = PIN.match(line)
                    self.assertIsNotNone(match, f'{name}: {line}')
                    pins.add(match.group('ref', 'sha'))
        self.assertEqual(len(pins), 1, pins)
        self.assertEqual(next(iter(pins))[0], 'wyrd-company/airlock')

    def test_renovate_reads_the_pins(self):
        config = json.loads((ROOT / 'renovate.json').read_text())
        patterns = [re.compile(pattern.strip('/')) for pattern in config['github-actions']['managerFilePatterns']]
        sources = sorted(path.relative_to(ROOT).as_posix()
                         for path in (ROOT / 'templates/airlock-config/template/.github/workflows').iterdir())
        self.assertEqual(sources, ['templates/airlock-config/template/.github/workflows/airlock-audit.yml'])
        for source in sources:
            with self.subTest(source):
                self.assertTrue(any(pattern.search(source) for pattern in patterns), source)
                for ignored in config.get('ignorePaths', []):
                    literal = re.split(r'[*?{}!()\[\]]', ignored)[0]
                    self.assertFalse(source.startswith(literal) or ignored in source, ignored)

    def test_default_branch_required(self):
        self.assert_refused({'default_branch': ''})

    def test_default_branch_guard(self):
        for value in ('main branch', 'main\non: push', 'main]', '"main"'):
            with self.subTest(value=value):
                self.assert_refused({'default_branch': value})


if __name__ == '__main__':
    unittest.main()
