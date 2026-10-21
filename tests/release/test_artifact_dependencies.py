# relationships: validates: release-process
"""Real emitted npm setup and official Renovate extraction; no hosted transport claim."""
import hashlib
import json
import os
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

    def emitted(self):
        _, workflow, _ = render(self.root, model(), selected=['product-build'], answers=product_answers())
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
            file.write_text('{"lockfileVersion":3,"packages":{}}\n')
            with self.assertRaisesRegex(AssertionError, 'Source and emitted dependency bytes agree: package-lock.json'):
                self.assert_input_agreement(project)
            _, result = self.setup(project, step, 'corrupt-lock')
            self.assertNotEqual(result.returncode, 0, 'Corrupt lock must fail setup')
            self.assertIn('Artifact client locked dependency setup failed', result.stdout + result.stderr)
            self.assertIn('npm error', result.stderr)
            self.assertEqual(file.read_text(), '{"lockfileVersion":3,"packages":{}}\n')
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
        for name in ('renovate.json', 'renovate-config.json'):
            shutil.copyfile(ROOT / name, fixture / name)
        if config is not None:
            (fixture / 'renovate.json').write_text(json.dumps(config))
        files = subprocess.check_output(['git', 'ls-files', '--cached', '--others', '--exclude-standard'],
                                        cwd=ROOT, text=True).splitlines()
        (fixture / 'files.json').write_text(json.dumps(files))
        result = self.command(['node', str(ROOT / 'tests/release/artifact-updater.mjs'), str(fixture)], 'extraction')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return json.loads(result.stdout)

    def assert_extraction(self, result):
        self.assertEqual(result['validation']['repository']['errors'], [], 'Repository configuration passes official validation')
        self.assertEqual(result['validation']['owner']['errors'], [], 'Owner preset passes official validation')
        manifest = (SOURCE / 'package.json').as_posix()
        self.assertEqual(result['selected'], [manifest], 'Official updater extracts the parent SDK source')
        self.assertEqual([e['packageFile'] for e in result['extracted']], [manifest])
        self.assertEqual(result['extracted'][0]['npmLock'], (SOURCE / 'package-lock.json').as_posix(),
                         'Official updater owns the complete source lock')
        dependency, = result['extracted'][0]['dependencies']
        expected_sdk = json.loads((ROOT / SOURCE / 'package.json').read_text())['dependencies']['@actions/artifact']
        self.assertEqual((dependency['depName'], dependency['currentValue']), ('@actions/artifact', expected_sdk))
        self.assertEqual(dependency.get('groupName'), 'release artifact client', 'Parent SDK update rule applies')
        self.assertFalse(dependency['automerge'])
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

    def test_unselected_product_emits_no_dependency_inputs(self):
        _, workflow, _ = render(self.root, model(), selected=[])
        self.assertFalse((workflow.parents[2] / GENERATED).exists())
