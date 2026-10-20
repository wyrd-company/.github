# relationships: validates: release-process
"""Crates part contracts and publication behavior; no public registry writes."""
import copy
import hashlib
import itertools
import types
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from harness import ROOT, check_parts, load, render
from model import model


def publisher():
    module = types.ModuleType('crates_publisher')
    source = ROOT / 'release/targets/crates-io/publish.py'
    # Always execute current bytes: mutation sites can have equal size/mtime.
    exec(compile(source.read_text(), str(source), 'exec'), module.__dict__)
    return module


def cargo_model():
    value = model()
    value['release_units']['sample-tool']['ecosystems'] = ['cargo']
    value['release_units']['sample-tool']['projections'] = [projection('lib/Cargo.toml'), projection('cli/Cargo.toml')]
    return value


def projection(file, **kw):
    return dict(adapter='cargo', file=file, workspace_file='Cargo.toml', generic=False, mode='committed', **kw)


class CratesPart(unittest.TestCase):
    def test_selected_and_deselected_contracts(self):
        for selected in ([], ['crates-io']):
            with tempfile.TemporaryDirectory() as tmp:
                template, workflow, answers = render(Path(tmp), cargo_model(), selected=selected,
                                                      answers={'crates_io_units': 'sample-tool'})
                parsed = check_parts(template, workflow, selected, answers)
                text = workflow.read_text()
                if selected:
                    job = parsed['jobs']['publish-crates-io']
                    self.assertEqual(job['permissions'], {'contents': 'read'})
                    self.assertIn('release-record', job['needs'])
                    self.assertIn("needs.release-record.result == 'success'", job['if'])
                    step = job['steps'][-1]
                    self.assertEqual(step['env']['CARGO_REGISTRY_TOKEN'], '${{ secrets.CARGO_REGISTRY_TOKEN }}')
                    self.assertEqual(json.loads(step['env']['CRATES_MODEL']), cargo_model())
                    self.assertEqual(step['env']['CRATES_UNITS'], 'sample-tool')
                    self.assertNotIn('${{', step['run'])
                    self.assertIn((ROOT / 'release/targets/crates-io/publish.py').read_text().strip(), step['run'])
                    # Run the exact emitted entry point with no admitted units.
                    env = dict(os.environ, **step['env'], RELEASE_JSON='{}', RELEASE_UNITS='')
                    result = subprocess.run(['bash', '-e', '-c', step['run']], env=env, text=True, capture_output=True)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertIn('CARGO_REGISTRY_TOKEN', parsed['jobs']['preflight']['steps'][0]['env'])
                else:
                    self.assertNotIn('publish-crates-io', parsed['jobs'])
                    self.assertNotIn('CARGO_REGISTRY_TOKEN', text)
                subprocess.run(['actionlint', str(workflow)], check=True)

    def test_model_dollar_data_stays_literal(self):
        value = cargo_model()
        value['release_units']['sample-tool']['sources'] = [{'detector': 'cargo', 'path': '${{ secrets.SAMPLE_TOKEN }}'}]
        with tempfile.TemporaryDirectory() as tmp:
            _, workflow, _ = render(Path(tmp), value, selected=['crates-io'], answers={'crates_io_units': 'sample-tool'})
            data = load(workflow)['jobs']['publish-crates-io']['steps'][-1]['env']['CRATES_MODEL']
            self.assertNotIn('${{', data)
            self.assertEqual(json.loads(data), value)


class CratesPublisher(unittest.TestCase):
    def setUp(self):
        self.pub = publisher()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.old = Path.cwd()
        os.chdir(self.root)
        self.addCleanup(os.chdir, self.old)
        self.releases = {'sample-tool': {'version': '1.2.3', 'tag': '1.2.3', 'prerelease': False}}
        self.value = cargo_model()
        self.lib = self.package('sample-core', 'lib/Cargo.toml')
        self.cli = self.package('sample-cli', 'cli/Cargo.toml', [{'name': 'sample-core', 'rename': 'alias',
                                                              'path': str(self.root / 'lib'), 'kind': 'build'}])
        self.metadata = {'packages': [self.cli, self.lib], 'target_directory': str(self.root / 'target')}

    def package(self, name, manifest, dependencies=None):
        return dict(name=name, version='1.2.3', manifest_path=str(self.root / manifest),
                    dependencies=dependencies or [], publish=None)

    def plan(self, value=None, units=None, releases=None):
        with patch.object(self.pub, 'cargo', return_value=json.dumps(self.metadata)):
            return self.pub.plan(value or self.value, units or ['sample-tool'], self.releases if releases is None else releases)

    def test_multi_crate_unit_dependency_order(self):
        self.assertEqual([p['name'] for p in self.plan()], ['sample-core', 'sample-cli'])
        # A development edge back to the CLI is not a publication cycle.
        self.lib['dependencies'] = [{'name': 'sample-cli', 'kind': 'dev', 'path': str(self.root / 'cli')}]
        try:
            self.assertEqual([p['name'] for p in self.plan()], ['sample-core', 'sample-cli'])
        except ValueError as error:
            self.fail('Development edge constrained publication: ' + str(error))

    def test_only_selected_released_units(self):
        try:
            self.assertEqual(self.plan(releases={}), [])
        except KeyError as error:
            self.fail('Unreleased unit reached planning: ' + str(error))
        with patch.dict(os.environ, {'CRATES_MODEL': json.dumps(self.value), 'CRATES_UNITS': 'sample-tool',
                                    'RELEASE_UNITS': 'sample-docs', 'RELEASE_JSON': json.dumps(self.releases)}):
            with patch.object(self.pub, 'plan', return_value=[]) as plan:
                self.pub.main()
                self.assertEqual(plan.call_args.args[1], [])

    def test_cross_unit_dependencies_and_prepublication_validation(self):
        second = copy.deepcopy(self.value['release_units']['sample-tool'])
        second['projections'] = [projection('cli/Cargo.toml')]
        self.value['release_units']['sample-tool']['projections'] = [projection('lib/Cargo.toml')]
        self.value['release_units']['other-tool'] = second
        self.releases['other-tool'] = dict(self.releases['sample-tool'])
        self.assertEqual([p['name'] for p in self.plan(units=['other-tool', 'sample-tool'])],
                         ['sample-core', 'sample-cli'])
        second['release_record'] = 'none'
        with patch.dict(os.environ, {'CRATES_MODEL': json.dumps(self.value), 'CRATES_UNITS': 'sample-tool other-tool',
                                    'RELEASE_UNITS': 'sample-tool other-tool', 'RELEASE_JSON': json.dumps(self.releases)}):
            with patch.object(self.pub, 'cargo', return_value=json.dumps(self.metadata)):
                with patch.object(self.pub, 'ensure') as ensure:
                    with self.assertRaisesRegex(ValueError, 'release record'):
                        self.pub.main()
                    ensure.assert_not_called()

    def test_external_dependency_does_not_constrain_selected_crate(self):
        # A registry dependency with a matching native name is a different source.
        self.lib['dependencies'] = [{'name': 'sample-cli', 'kind': None, 'path': None}]
        try:
            self.assertEqual([p['name'] for p in self.plan()], ['sample-core', 'sample-cli'])
        except ValueError as error:
            self.fail('Registry edge introduced a local cycle: ' + str(error))

    def test_record_none_refused_before_writes(self):
        self.value['release_units']['sample-tool']['release_record'] = 'none'
        with patch.object(self.pub, 'ensure') as ensure:
            with self.assertRaisesRegex(ValueError, 'release record'):
                self.plan()
            ensure.assert_not_called()

    def test_missing_projection_refused(self):
        self.value['release_units']['sample-tool']['projections'] = []
        with self.assertRaisesRegex(ValueError, 'no Cargo projection'):
            self.plan()

    def test_projection_adapter_generic_and_mode_selection(self):
        for field, value in [('adapter', 'toml'), ('generic', True), ('mode', 'none')]:
            with self.subTest(field=field):
                bad = copy.deepcopy(self.value)
                for p in bad['release_units']['sample-tool']['projections']:
                    p[field] = value
                with self.assertRaisesRegex(ValueError, 'no Cargo projection'):
                    self.plan(value=bad)

    def test_manifest_path_filter(self):
        try:
            self.assertEqual([p['name'] for p in self.plan()], ['sample-core', 'sample-cli'])
        except ValueError as error:
            self.fail('Cargo manifest identity selection failed: ' + str(error))

    def test_registry_allow_list(self):
        self.cli['publish'] = ['crates-io']
        try:
            self.assertEqual([p['name'] for p in self.plan()], ['sample-core', 'sample-cli'])
        except (ValueError, TypeError) as error:
            self.fail('crates.io allowed package was refused: ' + str(error))

    def test_manifest_identity_refused(self):
        self.metadata['packages'].append(copy.deepcopy(self.lib))
        with self.assertRaisesRegex(ValueError, 'one package manifest'):
            self.plan()

    def test_version_mismatch_refused(self):
        self.cli['version'] = '9.0.0'
        with self.assertRaisesRegex(ValueError, 'version differs'):
            self.plan()

    def test_private_package_refused(self):
        self.cli['publish'] = []
        with self.assertRaisesRegex(ValueError, 'does not permit'):
            self.plan()

    def test_duplicate_projection_refused(self):
        self.value['release_units']['sample-tool']['projections'].append(projection('lib/Cargo.toml'))
        with self.assertRaisesRegex(ValueError, 'more than one'):
            self.plan()

    def test_dependency_cycle_refused(self):
        self.lib['dependencies'] = [{'name': 'sample-cli', 'kind': None, 'path': str(self.root / 'cli')}]
        with self.assertRaisesRegex(ValueError, 'cycle'):
            self.plan()

    def archive(self):
        archive = self.root / 'target/package/sample-core-1.2.3.crate'
        archive.parent.mkdir(parents=True)
        archive.write_bytes(b'packaged source')
        return hashlib.sha256(archive.read_bytes()).hexdigest()

    def ensure(self, observed, returncode=0, allow_publish=True):
        with patch.object(self.pub, 'cargo', return_value=json.dumps(self.metadata)) as cargo:
            with patch.object(self.pub, 'checksum', side_effect=itertools.chain(observed, itertools.repeat(observed[-1]))):
                with patch.object(self.pub.subprocess, 'run', return_value=subprocess.CompletedProcess([], returncode)) as publish:
                    if not allow_publish:
                        publish.side_effect = lambda *a, **kw: self.fail('Existing conflicting artifact reached upload')
                    self.pub.ensure(self.lib)
                    return cargo, publish

    def test_matching_published_or_submitted_retry(self):
        expected = self.archive()
        cargo, publish = self.ensure([expected])
        publish.assert_not_called()
        self.assertEqual(cargo.call_args_list[0].args,
                         ('package', '--locked', '--registry', 'crates-io', '--manifest-path', self.lib['manifest_path']))

    def test_different_published_artifact_refused(self):
        self.archive()
        with self.assertRaisesRegex(ValueError, 'different package bytes'):
            self.ensure(['f' * 64], allow_publish=False)

    def test_publish_and_confirm(self):
        expected = self.archive()
        _, publish = self.ensure([None, expected])
        self.assertEqual(publish.call_args.args[0], ['cargo', 'publish', '--locked', '--registry', 'crates-io',
                                                   '--manifest-path', self.lib['manifest_path']])

    def test_unconfirmed_or_conflicting_submission_refused(self):
        self.archive()
        for observed in (None, 'f' * 64):
            with self.subTest(observed=observed), self.assertRaisesRegex(ValueError, 'not confirmed'):
                self.ensure([None, observed])

    def test_index_wait_failure_stops_dependents_then_retry_adopts_submission(self):
        expected = self.archive()
        with self.assertRaisesRegex(RuntimeError, 'retry the release'):
            self.ensure([None, expected], returncode=101)
        self.ensure([expected])[1].assert_not_called()

    def test_api_accepted_submission_checksum_and_identity(self):
        response = {'version': {'crate': 'sample-core', 'num': '1.2.3', 'checksum': 'a' * 64}}
        with patch.object(self.pub, 'urlopen', return_value=io.BytesIO(json.dumps(response).encode())) as opener:
            self.assertEqual(self.pub.checksum('sample-core', '1.2.3'), 'a' * 64)
            request = opener.call_args.args[0]
            self.assertEqual(request.full_url, 'https://crates.io/api/v1/crates/sample-core/1.2.3')
            self.assertFalse(request.has_header('Authorization'))
        for key, value in [('crate', 'other-crate'), ('num', '9.0.0'), ('checksum', None), ('checksum', 'short')]:
            changed = copy.deepcopy(response)
            changed['version'][key] = value
            with patch.object(self.pub, 'urlopen', return_value=io.BytesIO(json.dumps(changed).encode())):
                try:
                    self.pub.checksum('sample-core', '1.2.3')
                except Exception as error:
                    self.assertIsInstance(error, ValueError)
                else:
                    self.fail('Invalid API evidence was accepted')

    def test_api_not_found_and_errors_fail_closed(self):
        for code in (404, 401, 429, 500):
            with patch.object(self.pub, 'urlopen', side_effect=HTTPError('https://crates.io', code, 'private server text', {}, None)):
                if code == 404:
                    self.assertIsNone(self.pub.checksum('sample-core', '1.2.3'))
                else:
                    with self.assertRaisesRegex(RuntimeError, '^crates.io version lookup failed$'):
                        self.pub.checksum('sample-core', '1.2.3')

    def test_real_cargo_multi_crate_workspace_order(self):
        (self.root / 'Cargo.toml').write_text('[workspace]\nmembers=["lib","cli"]\nresolver="2"\n')
        for directory, name, dependency in [('lib', 'sample-core', ''), ('cli', 'sample-cli',
                '[build-dependencies]\nalias={package="sample-core",path="../lib",version="1.2.3"}\n')]:
            source = self.root / directory / 'src'
            source.mkdir(parents=True)
            (source / 'lib.rs').write_text('pub fn value() -> u8 { 7 }\n')
            (source.parent / 'Cargo.toml').write_text('[package]\nname="' + name +
                '"\nversion="1.2.3"\nedition="2021"\n' + dependency)
        self.value['release_units']['sample-tool']['projections'].reverse()
        self.assertEqual([p['name'] for p in self.pub.plan(self.value, ['sample-tool'], self.releases)],
                         ['sample-core', 'sample-cli'])

    def test_real_cargo_metadata_and_reproducible_package(self):
        # Real Cargo proves adapter paths/native names and checksum stability.
        (self.root / 'src').mkdir()
        (self.root / 'src/lib.rs').write_text('pub fn value() -> u8 { 7 }\n')
        (self.root / 'Cargo.toml').write_text('[package]\nname="sample-core"\nversion="1.2.3"\nedition="2021"\n')
        self.pub.cargo('generate-lockfile', '--offline')
        self.value['release_units']['sample-tool']['projections'] = [projection('Cargo.toml')]
        packages = self.pub.plan(self.value, ['sample-tool'], self.releases)
        self.assertEqual([p['name'] for p in packages], ['sample-core'])
        self.pub.cargo('package', '--locked', '--offline')
        first = (self.root / 'target/package/sample-core-1.2.3.crate').read_bytes()
        self.pub.cargo('package', '--locked', '--offline')
        self.assertEqual((self.root / 'target/package/sample-core-1.2.3.crate').read_bytes(), first)
