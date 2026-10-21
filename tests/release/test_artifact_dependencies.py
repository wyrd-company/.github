# relationships: validates: release-process
"""Real emitted npm setup and official Renovate extraction; no hosted transport claim."""
import hashlib
import json
import os
import re
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from harness import ROOT, load, render
from model import model
from test_product_build import product_answers

SOURCE = Path('release/targets/product-build/artifact-client')
GENERATED = Path('.github/release-artifact-client')
TOOL = Path('tests/release/tools/renovate')


class ArtifactDependencies(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory()
        self.addCleanup(self.scratch.cleanup)
        self.root = Path(self.scratch.name)
        self.sequence = 0

    def command(self, command, label, cwd=None, env=None):
        result = subprocess.run(command, cwd=cwd or self.root, env=env, text=True, capture_output=True)
        receipt = os.environ.get('RELEASE_DEPENDENCY_RECEIPTS')
        if receipt:
            destination = Path(receipt) / self._testMethodName
            destination.mkdir(parents=True, exist_ok=True)
            self.sequence += 1
            (destination / f'{self.sequence:02}-{label}.json').write_text(json.dumps({
                'command': command, 'cwd': str(cwd or self.root), 'exit': result.returncode,
                'stdout': result.stdout, 'stderr': result.stderr,
                'source_head': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
                'cache': os.environ.get('RELEASE_ARTIFACT_NPM_CACHE'),
                'renovate': os.environ.get('RENOVATE_PACKAGE_DIR')}, indent=2) + '\n')
        return result

    def emitted(self, root=None):
        _, workflow, _ = render(root or self.root, model(), selected=['product-build'], answers=product_answers())
        parsed = load(workflow)
        steps = [next(s for s in parsed['jobs'][job]['steps'] if s.get('name') == 'Install official artifact client')
                 for job in ('build-product', 'assemble-product')]
        self.assertEqual(steps[0], steps[1], 'Build and assembly consume the same locked setup')
        return workflow.parents[2], steps[0]

    def assert_input_agreement(self, project):
        for name in ('package.json', 'package-lock.json'):
            self.assertEqual((ROOT / SOURCE / name).read_bytes(), (project / GENERATED / name).read_bytes(),
                             'Source and emitted dependency bytes agree: ' + name)

    def setup(self, project, step, label):
        runner = self.root / label
        runner.mkdir()
        expand = lambda value: value.replace('${{ github.workspace }}', str(project)).replace('${{ runner.temp }}', str(runner))
        self.assertEqual(expand(step['working-directory']), str(runner))
        emitted_env = {key: expand(value) for key, value in step['env'].items()}
        self.assertEqual(emitted_env['ARTIFACT_CLIENT_INPUTS'], str(project / GENERATED),
                         'Emitted setup consumes generated inputs from the source checkout')
        self.assertEqual(emitted_env['ARTIFACT_CLIENT_DIRECTORY'], str(runner / 'release-artifact-client'),
                         'Emitted setup installs outside product sources')
        env = dict(os.environ, **emitted_env,
                   npm_config_cache=os.environ['RELEASE_ARTIFACT_NPM_CACHE'], npm_config_offline='true')
        result = self.command(['bash', '-c', step['run']], label, cwd=runner, env=env)
        return runner / 'release-artifact-client', result

    def test_source_emitted_installed_graph_agreement(self):
        project, step = self.emitted()
        self.assert_input_agreement(project)
        directory, result = self.setup(project, step, 'official-sdk-offline')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        for name in ('package.json', 'package-lock.json'):
            self.assertEqual((ROOT / SOURCE / name).read_bytes(), (directory / name).read_bytes())
        lock = json.loads((ROOT / SOURCE / 'package-lock.json').read_text())
        expected = {str(directory / name): data['version'] for name, data in lock['packages'].items() if name}
        observed = self.command(['npm', 'ls', '--all', '--parseable', '--prefix', str(directory)], 'installed-graph')
        self.assertEqual(observed.returncode, 0, observed.stdout + observed.stderr)
        actual = {name: json.loads((Path(name) / 'package.json').read_text())['version']
                  for name in observed.stdout.splitlines() if name != str(directory)}
        self.assertEqual(expected, actual, 'Installed graph agrees with committed complete lock')
        imported = self.command(['node', '--input-type=module', '-e',
                                 'const m=await import(process.argv[1]); if(typeof m.DefaultArtifactClient!=="function")process.exit(1)',
                                 (directory / 'node_modules/@actions/artifact/lib/artifact.js').as_uri()], 'official-esm')
        self.assertEqual(imported.returncode, 0, imported.stdout + imported.stderr)

    def test_corrupt_emitted_lock_fails_agreement_and_setup(self):
        project, step = self.emitted()
        self.assert_input_agreement(project)
        file = project / GENERATED / 'package-lock.json'
        original = file.read_bytes()
        try:
            corrupt = json.loads(original)
            corrupt['packages']['node_modules/@actions/artifact']['version'] = '0.0.0'
            corrupt_bytes = (json.dumps(corrupt, indent=2) + '\n').encode()
            file.write_bytes(corrupt_bytes)
            with self.assertRaisesRegex(AssertionError, 'Source and emitted dependency bytes agree: package-lock.json'):
                self.assert_input_agreement(project)
            _, result = self.setup(project, step, 'corrupt-lock')
            self.assertNotEqual(result.returncode, 0, 'Corrupt lock must fail setup')
            self.assertIn('Artifact client locked dependency setup failed', result.stdout + result.stderr)
            self.assertIn('npm error code EUSAGE', result.stderr, 'Locked consistency refusal must be observed')
            self.assertIn("Invalid: lock file's @actions/artifact@0.0.0", result.stderr)
            self.assertNotIn('ENOTCACHED', result.stderr, 'Missing metadata cannot prove locked consistency')
            self.assertEqual(file.read_bytes(), corrupt_bytes)
            # The real alternate command must install successfully with exactly this cache/input.
            alternate = dict(step, run=step['run'].replace('npm ci --prefix', 'npm install --prefix', 1))
            installed, falsifier = self.setup(project, alternate, 'unlocked-falsifier')
            self.assertEqual(falsifier.returncode, 0, 'Unlocked alternate must falsify the guard: ' + falsifier.stdout + falsifier.stderr)
            expected = json.loads(original)['packages']['node_modules/@actions/artifact']['version']
            actual = json.loads((installed / 'node_modules/@actions/artifact/package.json').read_text())['version']
            self.assertEqual(actual, expected)
            self.assertEqual(file.read_bytes(), corrupt_bytes)
        finally:
            file.write_bytes(original)
        self.assertEqual(hashlib.sha256(file.read_bytes()).digest(), hashlib.sha256(original).digest())
        self.assert_input_agreement(project)
        _, restored = self.setup(project, step, 'restored-lock')
        self.assertEqual(restored.returncode, 0, restored.stdout + restored.stderr)

    def test_missing_emitted_lock_fails_without_unlocked_fallback(self):
        project, step = self.emitted()
        file = project / GENERATED / 'package-lock.json'
        original = file.read_bytes()
        try:
            file.unlink()
            directory, result = self.setup(project, step, 'missing-lock')
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('Artifact client dependency inputs are missing', result.stdout + result.stderr)
            self.assertFalse((directory / 'node_modules').exists())
            self.assertNotIn('npm error', result.stderr, 'Missing inputs fail before npm starts')
        finally:
            file.write_bytes(original)
        self.assert_input_agreement(project)

    def extraction(self, config=None):
        fixture = self.root / 'extraction'
        fixture.mkdir(exist_ok=True)
        shutil.copytree(ROOT / SOURCE, fixture / SOURCE, dirs_exist_ok=True)
        shutil.copytree(ROOT / TOOL, fixture / TOOL, dirs_exist_ok=True)
        for name in ('renovate.json', 'renovate-config.json'):
            shutil.copyfile(ROOT / name, fixture / name)
        if config is not None:
            (fixture / 'renovate.json').write_text(json.dumps(config))
        files = subprocess.check_output(['git', 'ls-files', '--cached', '--others', '--exclude-standard'],
                                        cwd=ROOT, text=True).splitlines()
        (fixture / 'files.json').write_text(json.dumps(files))
        return self.extract_fixture(fixture)

    def extract_fixture(self, fixture, label='extraction'):
        result = self.command(['node', str(ROOT / 'tests/release/artifact-updater.mjs'), str(fixture)], label)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return json.loads(result.stdout)

    def assert_extraction(self, result):
        self.assertEqual(result['validation']['repository']['errors'], [], 'Repository configuration passes official validation')
        self.assertEqual(result['validation']['owner']['errors'], [], 'Owner preset passes official validation')
        manifest = (SOURCE / 'package.json').as_posix()
        self.assertEqual(result['selected'], [manifest, (TOOL / 'package.json').as_posix()], 'Official updater extracts the parent SDK source')
        self.assertEqual([e['packageFile'] for e in result['extracted']], result['selected'])
        self.assertEqual(result['extracted'][0]['npmLock'], (SOURCE / 'package-lock.json').as_posix(),
                         'Official updater owns the complete source lock')
        dependency, = result['extracted'][0]['dependencies']
        expected_sdk = json.loads((ROOT / SOURCE / 'package.json').read_text())['dependencies']['@actions/artifact']
        self.assertEqual((dependency['depName'], dependency['currentValue']), ('@actions/artifact', expected_sdk))
        self.assertEqual(dependency.get('groupName'), 'release artifact client', 'Parent SDK update rule applies')
        self.assertFalse(dependency['automerge'])
        tool = result['extracted'][1]
        self.assertEqual(tool['npmLock'], (TOOL / 'package-lock.json').as_posix())
        self.assertEqual(tool['dependencies'][0]['depName'], 'renovate')
        self.assertIn(':maintainLockFilesWeekly', result['owner']['extends'])
        self.assertEqual(result['repository']['extends'], ['github>wyrd-company/.github:renovate-config'])
        self.assertEqual(sorted(f for f in result['filtered'] if f.startswith('release/')),
                         sorted([(SOURCE / name).as_posix() for name in ('package.json', 'package-lock.json')]),
                         'Other release inputs retain their updater exclusions')

    def test_official_updater_extracts_source_and_lock(self):
        self.assert_extraction(self.extraction())

    def test_misdirected_official_extraction_fails_named_assertion(self):
        self.assert_extraction(self.extraction())
        config = json.loads((ROOT / 'renovate.json').read_text())
        config['ignorePaths'] = ['release/**', 'tests/release/sample-target/**']
        result = self.extraction(config)
        with self.assertRaisesRegex(AssertionError, 'Official updater extracts the parent SDK source'):
            self.assert_extraction(result)
        self.assert_extraction(self.extraction())

    def adopter(self, explicit=False):
        project, _ = self.emitted(self.root / ('adopter-explicit' if explicit else 'adopter-inherited'))
        (project / 'package.json').write_text(json.dumps({'private': True, 'dependencies': {'is-number': '7.0.0'}}))
        excluded = ['tests', 'examples/demo', 'vendor/lib']
        if explicit:
            excluded.append('example-ignore')
        for name in excluded:
            directory = project / name
            directory.mkdir(parents=True)
            (directory / 'package.json').write_text(json.dumps({'dependencies': {'is-odd': '3.0.1'}}))
        existing = {'extends': ['github>wyrd-company/.github:renovate-config'], 'labels': ['dependencies']}
        if explicit:
            existing['ignorePaths'] = ['**/tests/**', '**/examples/**', '**/vendor/**', 'example-ignore/**']
        (project / 'renovate.json').write_text(json.dumps(existing))
        shutil.copyfile(ROOT / 'renovate-config.json', project / 'renovate-config.json')
        files = sorted(p.relative_to(project).as_posix() for p in project.rglob('*') if p.is_file())
        (project / 'files.json').write_text(json.dumps(files))
        guide = (ROOT / 'release/targets/README.md').read_text().split('## Product build and assembly\n', 1)[1].split('\n## ', 1)[0]
        fragments = [json.loads(block) for block in re.findall(r'```json\n(.*?)\n```', guide, re.S)]
        addition, = [fragment for fragment in fragments if 'ignorePaths' in fragment]
        configured = dict(existing, ignorePaths=(existing['ignorePaths'] + [addition['ignorePaths'][-1]]
                                                if explicit else addition['ignorePaths']))
        return project, existing, configured

    def assert_adopter_preservation(self, result, baseline):
        generated = (GENERATED / '**').as_posix()
        self.assertEqual([entry for entry in result['resolved']['ignorePaths'] if entry != generated],
                         baseline['resolved']['ignorePaths'],
                         'Effective inherited or explicit adopter exclusions remain intact')
        for file in ('tests/package.json', 'examples/demo/package.json', 'vendor/lib/package.json'):
            self.assertNotIn(file, result['filtered'], 'Previously excluded adopter inputs remain excluded')
        before = dict(baseline['resolved'])
        after = dict(result['resolved'])
        del before['ignorePaths'], after['ignorePaths']
        self.assertEqual(after, before, 'Existing effective action and update policy remains intact')
        self.assertIn('config:recommended', result['visitedPresets']['merged'])
        self.assertIn(':ignoreModulesAndTests', result['visitedPresets']['merged'])
        self.assertEqual(sorted(set(baseline['filtered']) - set(result['filtered'])),
                         sorted([(GENERATED / name).as_posix() for name in ('package.json', 'package-lock.json')]),
                         'Only the generated SDK manifest and lock leave the updater surface')
        self.assertEqual(set(result['filtered']) - set(baseline['filtered']), set())

    def assert_adopter_ownership(self, result):
        self.assertEqual(result['validation']['repository']['errors'], [])
        self.assertNotIn((GENERATED / 'package.json').as_posix(), result['selected'],
                         'Generated SDK graph is excluded from adopter automatic updates')
        self.assertEqual(result['selected'], ['package.json'], 'Unrelated adopter npm input remains extracted')
        ordinary, = result['extracted']
        dependency, = ordinary['dependencies']
        self.assertEqual((dependency['depName'], dependency['currentValue']), ('is-number', '7.0.0'))
        self.assertIn('.github/workflows/release.yml', result['filtered'], 'Adopter external-action pins remain included')
        self.assertTrue(result['resolved']['lockFileMaintenance']['enabled'])
        self.assertFalse(dependency['automerge'])

    def test_rendered_adopter_excludes_only_generated_sdk(self):
        for explicit in (False, True):
            with self.subTest(explicit=explicit):
                project, existing, configured = self.adopter(explicit)
                workflow = project / '.github/workflows/release.yml'
                workflow_bytes = workflow.read_bytes()
                baseline = self.extract_fixture(project, 'unexcluded-adopter')
                (project / 'renovate.json').write_text(json.dumps(configured))
                result = self.extract_fixture(project, 'configured-adopter')
                self.assertNotIn((GENERATED / 'package.json').as_posix(), result['selected'],
                                 'Generated SDK graph is excluded from adopter automatic updates')
                self.assert_adopter_preservation(result, baseline)
                self.assert_adopter_ownership(result)
                self.assertEqual(result['repository'], configured)
                for key, value in existing.items():
                    if key != 'ignorePaths':
                        self.assertEqual(result['repository'][key], value, 'Existing adopter configuration preserved')
                self.assertEqual(workflow.read_bytes(), workflow_bytes)
                shutil.rmtree(project)
        self.assert_extraction(self.extraction())

    def test_replacing_inherited_exclusions_fails_preservation(self):
        project, _, configured = self.adopter()
        file = project / 'renovate.json'
        baseline = self.extract_fixture(project, 'inherited-baseline')
        restored = (json.dumps(configured) + '\n').encode()
        file.write_bytes(restored)
        self.assert_adopter_preservation(self.extract_fixture(project, 'preserved-adopter'), baseline)
        try:
            changed = dict(configured, ignorePaths=[(GENERATED / '**').as_posix()])
            file.write_text(json.dumps(changed))
            result = self.extract_fixture(project, 'replaced-inherited-exclusions')
            self.assertTrue({'tests/package.json', 'examples/demo/package.json', 'vendor/lib/package.json'}
                            <= set(result['selected']))
            with self.assertRaisesRegex(AssertionError, 'Effective inherited or explicit adopter exclusions remain intact'):
                self.assert_adopter_preservation(result, baseline)
        finally:
            file.write_bytes(restored)
        self.assertEqual(file.read_bytes(), restored)
        result = self.extract_fixture(project, 'restored-preservation')
        self.assert_adopter_preservation(result, baseline)
        self.assert_adopter_ownership(result)

    def test_missing_or_misdirected_adopter_exclusion_fails_ownership(self):
        project, _, configured = self.adopter()
        baseline = self.extract_fixture(project, 'unexcluded-adopter')
        generated, = [entry for entry in baseline['extracted']
                      if entry['packageFile'] == (GENERATED / 'package.json').as_posix()]
        self.assertEqual(generated['npmLock'], (GENERATED / 'package-lock.json').as_posix())
        sdk, = generated['dependencies']
        self.assertEqual(sdk['depName'], '@actions/artifact')
        file = project / 'renovate.json'
        restored = (json.dumps(configured) + '\n').encode()
        file.write_bytes(restored)
        self.assert_adopter_ownership(self.extract_fixture(project, 'configured-adopter'))
        try:
            for exclusion in ([], ['.github/absent-client/**']):
                with self.subTest(exclusion=exclusion):
                    changed = dict(configured, ignorePaths=configured['ignorePaths'][:-1] + exclusion)
                    file.write_text(json.dumps(changed))
                    result = self.extract_fixture(project, 'misdirected-adopter')
                    with self.assertRaisesRegex(AssertionError, 'Generated SDK graph is excluded from adopter automatic updates'):
                        self.assert_adopter_ownership(result)
        finally:
            file.write_bytes(restored)
        self.assertEqual(file.read_bytes(), restored)
        result = self.extract_fixture(project, 'restored-adopter')
        self.assert_adopter_ownership(result)
        self.assert_adopter_preservation(result, baseline)

    def test_unselected_product_emits_no_dependency_inputs(self):
        _, workflow, _ = render(self.root, model(), selected=[])
        self.assertFalse((workflow.parents[2] / GENERATED).exists())
