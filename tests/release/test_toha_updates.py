# relationships: validates: release-process, template-composition, applied-record
"""Consumer proof against the real Toha CLI, Git revisions and applied record."""
import copy
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from harness import check_parts, load, prepare, render
from model import model

WORKFLOW = '.github/workflows/release.yml'
PARTS = ['sample-target', 'sample-target-2']
OLD_PIN = 'actions/checkout@'  # Locate the parent's actual pinned line, not a fixture pin.


class TohaUpdates(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory()
        self.addCleanup(self.scratch.cleanup)
        self.root = Path(self.scratch.name)
        self.template = self.root / 'release'
        prepare(self.template, samples=2)
        self.assertTrue(next(q for q in load(self.template / 'template.yml')['interview'] if q.get('id') == 'targets')['parts'])
        self.git(self.template, 'init', '-q', '-b', 'main')
        self.first = self.commit(self.template)
        self.address = self.template.as_uri()
        self.target = self.root / 'project'
        (self.target / '.intentional').mkdir(parents=True)
        self.value = model()
        # Exercise the required generic boolean through admission, record and output.
        self.value['release_units']['sample-tool']['projections'] = [{
            'adapter': 'toml', 'file': 'package.toml', 'workspace_file': 'package.toml',
            'mode': 'committed', 'generic': True, 'pointer': 'package.version',
        }]
        self.write_model(self.value)
        self.git(self.target, 'init', '-q', '-b', 'main')
        self.commit(self.target)
        self.env = dict(os.environ)
        for key in ('XDG_CONFIG_HOME', 'XDG_DATA_HOME', 'XDG_CACHE_HOME', 'XDG_STATE_HOME',
                    'APPDATA', 'LOCALAPPDATA', 'PROGRAMDATA'):
            self.env[key] = str(self.root / key)
        self.env.update(TOHA_CONFIG=str(self.root / 'local.yml'),
                        TOHA_USER_CONFIG=str(self.root / 'user.yml'))
        self.env.pop('TOHA_RECORD', None)
        self.sequence = 0

    def git(self, path, *args):
        env = dict(os.environ, GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull,
                   GIT_AUTHOR_NAME='Example User', GIT_AUTHOR_EMAIL='user@example.invalid',
                   GIT_COMMITTER_NAME='Example User', GIT_COMMITTER_EMAIL='user@example.invalid')
        return subprocess.check_output(['git', '-C', str(path), *args], env=env, text=True).strip()

    def commit(self, path):
        self.git(path, 'add', '.')
        self.git(path, 'commit', '-q', '--allow-empty', '-m', 'Example revision')
        return self.git(path, 'rev-parse', 'HEAD')

    def write_model(self, value):
        (self.target / '.intentional/release-model.json').write_text(json.dumps(value) + '\n')

    def cli(self, *args, code=0, status=None):
        self.sequence += 1
        result = subprocess.run([os.environ['TOHA_BIN'], *map(str, args)], env=self.env,
                                cwd=self.root, text=True, capture_output=True)
        receipt = {'command': [str(a) for a in args], 'exit': result.returncode,
                   'stdout': result.stdout, 'stderr': result.stderr}
        if os.environ.get('RELEASE_TOHA_RECEIPTS'):
            directory = Path(os.environ['RELEASE_TOHA_RECEIPTS']) / self._testMethodName
            directory.mkdir(parents=True, exist_ok=True)
            receipt['files'] = {name: (self.target / name).read_text()
                                for name in ('.toha.json', WORKFLOW, '.intentional/release-model.json')
                                if (self.target / name).is_file()}
            (directory / f'{self.sequence:02}.json').write_text(json.dumps(receipt, indent=2) + '\n')
        self.assertEqual(result.returncode, code, receipt)
        if status is not None:
            document = json.JSONDecoder().raw_decode(result.stdout.lstrip())[0]
            self.assertEqual(document['status'], status, receipt)
            return document
        return result

    def run_apply(self, *flags, answers=None, address=None, status='applied', code=0):
        address = address or self.address
        file = self.root / 'answers.json'
        file.write_text(json.dumps({'template': address, 'answers': answers or {}}))
        return self.cli('apply', address, self.target, '--answers', file, *flags,
                        status=status, code=code)

    def record(self):
        record = json.loads((self.target / '.toha.json').read_text())
        self.assertEqual(record['$schema'], 'https://wyrd.foo/toha/schemas/applied-record/1')
        self.assertEqual(len(record['applications']), 1)
        return record['applications'][0]

    def assert_output(self, selected, value=None):
        entry = self.record()
        self.assertEqual(entry['parts'], selected)
        self.assertNotIn('targets', entry['answers'])
        self.assertEqual(entry['answers']['release_model'], value or self.value)
        workflow = self.target / WORKFLOW
        parsed = check_parts(self.template, workflow, selected, entry['answers'])
        env = next(s['env'] for s in parsed['jobs']['resolve']['steps'] if s.get('id') == 'resolve')
        self.assertEqual(json.loads(env['RELEASE_MODEL']), value or self.value)
        for index, part in enumerate(PARTS):
            suffix = '-2' if index else ''
            for stage in ('build', 'publish', 'verify'):
                self.assertEqual(stage + '-sample' + suffix in parsed['jobs'], part in selected)
        # Credential declarations are embedded in the parent's actual preflight script data.
        env = parsed['jobs']['preflight']['steps'][0].get('env', {})
        self.assertEqual(env, {'SAMPLE_TOKEN': '${{ secrets.SAMPLE_TOKEN }}',
                               'SAMPLE_CHANNEL': '${{ vars.SAMPLE_CHANNEL }}'} if selected else {})
        self.assertEqual('preflight-sample' in parsed['jobs'], bool(selected))
        if selected:
            self.assertEqual(parsed['jobs']['preflight-sample']['steps'][0]['env'],
                             {'SAMPLE_ENV_TOKEN': '${{ secrets.SAMPLE_ENV_TOKEN }}'})
        return entry

    def bump(self, transform=lambda s: s):
        path = self.template / 'template' / WORKFLOW
        path.write_text(transform(path.read_text()) + '\n# Template revision\n')
        return self.commit(self.template)

    def initial(self, *flags):
        result = self.run_apply(*flags)
        self.commit(self.target)
        return result

    def test_flag_order_settles_in_option_order(self):
        self.initial('--with', PARTS[1], '--with', PARTS[0])
        self.assert_output(PARTS)

    def test_apply_selection_and_revision_update(self):
        initial = self.initial('--with', PARTS[0], '--without', PARTS[1])
        self.assertEqual(initial['application']['parts_added'], [PARTS[0]])
        entry = self.assert_output([PARTS[0]])
        self.assertEqual(entry['commit'], self.first)
        second = self.bump()
        next_address = self.address + '@' + second
        added = self.run_apply('--update', '--with', PARTS[1], address=next_address,
                               answers={'sample_target_units_2': 'sample-docs', 'trusted_publishing_2': False})
        self.assertEqual(added['application']['parts_added'], [PARTS[1]])
        self.assertEqual(added['application']['prior_revision'], {'commit': self.first})
        self.assertEqual(added['application']['resolved_revision'], {'commit': second})
        self.assertEqual(self.assert_output(PARTS)['commit'], second)
        self.commit(self.target)
        removed = self.run_apply('--update', '--without', PARTS[0], address=next_address)
        self.assertEqual(removed['application']['parts_removed'], [PARTS[0]])
        self.assert_output([PARTS[1]])
        self.commit(self.target)
        before = (self.target / '.toha.json').read_bytes()
        self.run_apply('--update', address=next_address, status='already-current')
        self.assertEqual((self.target / '.toha.json').read_bytes(), before)
        self.assert_output([PARTS[1]])

    def test_staged_document_answer_and_update_freeze(self):
        # Ask the real document question, then answer it with the committed model.
        model_file = self.target / '.intentional/release-model.json'
        saved = model_file.read_bytes()
        model_file.unlink()
        self.cli('stage', self.address, self.target, '--with', PARTS[0], '--without', PARTS[1],
                 '--async', code=4, status='questions')
        model_file.write_bytes(saved)
        self.cli('continue', self.target, '--input', model_file, code=4, status='questions')
        answers = self.root / 'continue.json'
        answers.write_text(json.dumps({'template': self.address, 'answers': {
            'ci_workflow': 'ci.yml', 'sample_target_units': 'sample-tool', 'trusted_publishing': False}}))
        self.cli('continue', self.target, answers)
        self.cli('apply', self.target)
        self.assert_output([PARTS[0]])
        self.commit(self.target)
        for selected, flags in [(PARTS, ['--with', PARTS[1]]), ([PARTS[1]], ['--without', PARTS[0]])]:
            revision = self.bump()
            expected = copy.deepcopy(self.value)
            expected['release_units']['sample-tool']['changelog'] = 'CURRENT.md'
            self.write_model(expected)
            self.commit(self.target)
            staged_address = self.address + '@' + revision
            adding = '--with' in flags
            self.cli('stage', staged_address, self.target, '--update', *flags, '--async',
                     code=4 if adding else 0)
            if adding:
                answers.write_text(json.dumps({'template': staged_address, 'answers': {
                    'sample_target_units_2': 'sample-docs', 'trusted_publishing_2': False}}))
                self.cli('continue', self.target, answers)
            later = copy.deepcopy(expected)
            later['release_units']['sample-tool']['changelog'] = 'LATER.md'
            self.write_model(later)
            self.commit(self.target)
            self.cli('apply', self.target)
            self.assert_output(selected, expected)
            self.assertEqual(json.loads(model_file.read_text()), later)
            self.commit(self.target)
        # A new update admits the current document rather than replaying its old record value.
        self.run_apply('--update', address=self.address + '@' + revision)
        self.assert_output([PARTS[1]], later)
        self.assertEqual(json.loads(model_file.read_text()), later)

    def test_render_helper_uses_real_part_flags(self):
        directory = self.root / 'helper'
        directory.mkdir()
        with patch('harness.subprocess.run', wraps=subprocess.run) as observed:
            template, workflow, answers = render(directory, self.value, samples=1)
        command = observed.call_args.args[0]
        self.assertIn('--with', command)
        self.assertEqual(command[command.index('--with') + 1], PARTS[0])
        supplied = json.loads((directory / 'answers.json').read_text())['answers']
        self.assertNotIn('targets', supplied)
        self.assertEqual(answers.get('targets'), [PARTS[0]])
        self.assertTrue(next(q for q in load(template / 'template.yml')['interview'] if q.get('id') == 'targets')['parts'])
        self.assertIn('build-sample', load(workflow)['jobs'])

    def test_invalid_selection_is_usage_error_before_writes(self):
        for update in (False, True):
            if update:
                self.initial('--with', PARTS[0])
            for mode in ('apply', 'stage'):
                for flags, diagnostic in [
                    (['--with', 'missing'], 'unknown part "missing"'),
                    (['--without', 'missing'], 'unknown part "missing"'),
                    (['--with', PARTS[0], '--without', PARTS[0]],
                     'part "sample-target" is named by both --with and --without'),
                ]:
                    with self.subTest(update=update, mode=mode, flags=flags):
                        before = {p: (self.target / p).read_bytes() if (self.target / p).exists() else None
                                  for p in (WORKFLOW, '.toha.json')}
                        result = self.cli(mode, self.address, self.target, *flags,
                                          *(['--update'] if update else []),
                                          *(['--async'] if mode == 'stage' else ['--answers', self.empty_answers()]), code=2)
                        self.assertIn(diagnostic, result.stderr)
                        self.assertEqual(result.stdout, '')
                        for p, value in before.items():
                            self.assertEqual((self.target / p).read_bytes() if (self.target / p).exists() else None, value)
        result = self.run_apply('--update', '--with', PARTS[1], answers={'targets': []}, code=2, status=None)
        self.assertIn('an answers document cannot answer the parts question with --with or --without', result.stderr)

    def empty_answers(self):
        file = self.root / 'empty.json'
        file.write_text(json.dumps({'template': self.address, 'answers': {}}))
        return file

    def pin_line(self):
        path = self.target / WORKFLOW
        return next(line for line in path.read_text().splitlines() if OLD_PIN in line)

    def operator(self, old, new):
        path = self.target / WORKFLOW
        self.assertIn(old, path.read_text())
        path.write_text(path.read_text().replace(old, new))
        self.commit(self.target)

    def test_unchanged_template_pin_and_part_removal_preserve_operator_edits(self):
        self.initial('--with', PARTS[0])
        old = self.pin_line()
        operator = old.split('@')[0] + '@' + 'a' * 40 + ' # v99.0.0'
        self.operator(old, operator)
        path = self.target / WORKFLOW
        path.write_text(path.read_text() + '\n# Operator annotation\n')
        self.commit(self.target)
        for flags in ([], ['--without', PARTS[0]]):
            revision = self.bump()
            self.run_apply('--update', *flags, address=self.address + '@' + revision)
            self.assertIn(operator, path.read_text())
            self.assertIn('# Operator annotation', path.read_text())
            self.commit(self.target)

    def test_equal_pin_edits_merge(self):
        self.initial()
        old = self.pin_line()
        updated = old.split('@')[0] + '@' + 'a' * 40 + ' # v99.0.0'
        self.operator(old, updated)
        revision = self.bump(lambda s: s.replace(old, updated))
        result = self.run_apply('--update', address=self.address + '@' + revision)
        self.assertEqual(result['merge']['conflicted'], [])
        self.assertIn(updated, (self.target / WORKFLOW).read_text())

    def test_divergent_pin_edits_conflict_without_replacing_operator_bytes(self):
        self.initial()
        old = self.pin_line()
        operator = old.split('@')[0] + '@' + 'a' * 40 + ' # v99.0.0'
        candidate = old.split('@')[0] + '@' + 'b' * 40 + ' # v98.0.0'
        self.operator(old, operator)
        before = (self.target / WORKFLOW).read_bytes()
        revision = self.bump(lambda s: s.replace(old, candidate))
        result = self.run_apply('--update', address=self.address + '@' + revision)
        self.assertEqual(result['merge']['conflicted'], [WORKFLOW])
        self.assertEqual(subprocess.check_output(['git', '-C', str(self.target), 'show', ':2:' + WORKFLOW]), before)
        self.assertIn('<<<<<<< operator', (self.target / WORKFLOW).read_text())
        self.assertIn(operator, (self.target / WORKFLOW).read_text())
        self.assertIn(candidate, self.git(self.target, 'show', ':3:' + WORKFLOW))
        self.assertIn(operator, self.git(self.target, 'show', ':2:' + WORKFLOW))

    def rewrite_record(self, mutate):
        file = self.target / '.toha.json'
        document = json.loads(file.read_text())
        mutate(document['applications'][0])
        file.write_text(json.dumps(document, indent=2) + '\n')
        self.commit(self.target)

    def test_missing_commit_refuses_and_explicit_baseline_conflicts(self):
        self.initial()
        missing = 'f' * 40
        self.rewrite_record(lambda entry: entry.update(commit=missing))
        path = self.target / WORKFLOW
        path.write_text(path.read_text() + '\n# Operator annotation\n')
        self.commit(self.target)
        before = path.read_bytes()
        record = (self.target / '.toha.json').read_bytes()
        result = self.run_apply('--update', code=1, status=None)
        self.assertIn('--baseline', result.stderr)
        self.assertIn(missing, result.stderr)
        self.assertIn(self.address, result.stderr)
        self.assertEqual(result.stdout, '')
        self.assertEqual((self.target / '.toha.json').read_bytes(), record)
        self.assertEqual(path.read_bytes(), before)
        baseline = self.run_apply('--baseline', answers={'ci_workflow': 'ci.yml', 'targets': []})
        self.assertEqual(baseline['merge']['conflicted'], [WORKFLOW])
        self.assertEqual(path.read_bytes(), before)

    def test_missing_blob_conflicts_without_using_mismatched_render(self):
        self.initial()
        missing = 'f' * 40
        self.rewrite_record(lambda entry: entry['paths'][WORKFLOW].update(blob=missing))
        path = self.target / WORKFLOW
        path.write_text(path.read_text() + '\n# Operator annotation\n')
        self.commit(self.target)
        before = path.read_bytes()
        revision = self.bump()
        result = self.run_apply('--update', address=self.address + '@' + revision)
        self.assertEqual(result['merge']['conflicted'], [WORKFLOW])
        self.assertEqual(path.read_bytes(), before)
        stages = self.git(self.target, 'ls-files', '-u', WORKFLOW)
        self.assertNotIn(' 1\t', stages)
        self.assertIn(' 2\t', stages)
        self.assertIn(' 3\t', stages)

    def test_mismatched_render_uses_only_recorded_blob(self):
        self.initial()
        retained = self.git(self.target, 'hash-object', '-w', '.intentional/release-model.json')
        self.rewrite_record(lambda entry: entry['paths'][WORKFLOW].update(blob=retained))
        path = self.target / WORKFLOW
        path.write_text(path.read_text() + '\n# Operator annotation\n')
        self.commit(self.target)
        before = path.read_bytes()
        revision = self.bump()
        result = self.run_apply('--update', address=self.address + '@' + revision)
        self.assertEqual(result['merge']['conflicted'], [WORKFLOW])
        self.assertEqual(self.git(self.target, 'rev-parse', ':1:' + WORKFLOW), retained)
        self.assertEqual(subprocess.check_output(['git', '-C', str(self.target), 'show', ':2:' + WORKFLOW]), before)
        self.assertIn('# Operator annotation', path.read_text())

    def test_unversioned_source_refusal_is_separate(self):
        self.run_apply(address=str(self.template))
        self.commit(self.target)
        before = (self.target / WORKFLOW).read_bytes()
        result = self.run_apply('--update', address=str(self.template), code=1, status=None)
        self.assertIn('--baseline', result.stderr)
        self.assertEqual((self.target / WORKFLOW).read_bytes(), before)
