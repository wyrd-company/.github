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
import sys
import tempfile
import traceback
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from harness import ROOT, check_parts, load, render
from model import model


def publisher():
    module = types.ModuleType('crates_publisher')
    source = ROOT / 'templates/release/targets/crates-io/publish.py'
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
                    self.assertIn((ROOT / 'templates/release/targets/crates-io/publish.py').read_text().strip(), step['run'])
                    # Run the exact emitted entry point with no admitted units.
                    env = dict(os.environ, **step['env'], RELEASE_JSON='{}', RELEASE_UNITS='')
                    result = subprocess.run(['bash', '-e', '-c', step['run']], env=env, text=True, capture_output=True)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertIn('CARGO_REGISTRY_TOKEN', parsed['jobs']['preflight']['steps'][0]['env'])
                else:
                    self.assertNotIn('publish-crates-io', parsed['jobs'])
                    self.assertNotIn('CARGO_REGISTRY_TOKEN', text)
                subprocess.run(['actionlint', str(workflow)], check=True)

    def test_emitted_publish_failure_logs_both_cargo_streams(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            value = cargo_model()
            value['release_units']['sample-tool']['projections'] = [projection('Cargo.toml')]
            _, workflow, _ = render(root, value, selected=['crates-io'], answers={'crates_io_units': 'sample-tool'})
            step = load(workflow)['jobs']['publish-crates-io']['steps'][-1]
            archive = root / 'target/package/sample-core-1.2.3.crate'
            archive.parent.mkdir(parents=True)
            archive.write_bytes(b'fixture archive')
            metadata = dict(packages=[dict(name='sample-core', version='1.2.3',
                manifest_path=str(root / 'Cargo.toml'), dependencies=[], publish=None)],
                target_directory=str(root / 'target'))
            binary = root / 'bin'
            binary.mkdir()
            cargo = binary / 'cargo'
            cargo.write_text('#!' + sys.executable + '\n' +
                'import sys\n' +
                'if sys.argv[1] == "metadata": print(' + repr(json.dumps(metadata)) + ')\n' +
                'if sys.argv[1] == "publish":\n'
                '    print("cargo-stdout-marker", flush=True)\n'
                '    print("cargo-stderr-marker", file=sys.stderr, flush=True)\n'
                '    sys.exit(101)\n')
            cargo.chmod(0o755)
            # Stub only the HTTP transport in the emitted Python process.
            # Even an unexpected lookup cannot reach the public registry.
            (root / 'sitecustomize.py').write_text(
                'import io, json, os\n'
                'from urllib import request, error\n'
                'calls = 0\n'
                'def lookup(*args, **kwargs):\n'
                '    global calls\n'
                '    calls += 1\n'
                '    code = 404 if calls == 1 else int(os.environ["LOOKUP_STATUS"])\n'
                '    if code != 200:\n'
                '        raise error.HTTPError("https://crates.io", code, "private reason", {}, io.BytesIO(b"private body"))\n'
                '    return io.BytesIO(json.dumps({"version": {"crate": "sample-core", "num": "1.2.3", "checksum": '
                + repr(hashlib.sha256(archive.read_bytes()).hexdigest()) + '}}).encode())\n'
                'request.urlopen = lookup\n')
            env = dict(os.environ, **step['env'])
            env.update(PATH=str(binary) + os.pathsep + os.environ['PATH'], PYTHONPATH=str(root),
                       RELEASE_JSON=json.dumps({'sample-tool': {'version': '1.2.3'}}), RELEASE_UNITS='sample-tool')
            for status, message in [(404, 'Crate submission not confirmed with matching bytes'),
                                    (500, 'crates.io version lookup failed (HTTP 500)'),
                                    (200, 'Cargo did not finish registry indexing; retry the release')]:
                with self.subTest(status=status):
                    result = subprocess.run(['bash', '-e', '-c', step['run']], cwd=root,
                                            env=dict(env, LOOKUP_STATUS=str(status)), text=True, capture_output=True)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn('cargo-stdout-marker', result.stdout)
                    self.assertIn('cargo-stderr-marker', result.stderr)
                    self.assertIn(message, result.stderr)
                    self.assertNotIn('private reason', result.stderr)
                    self.assertNotIn('private body', result.stderr)

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
                with patch.object(self.pub.subprocess, 'run', return_value=subprocess.CompletedProcess([], returncode, stdout=b'', stderr=b'')) as publish:
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
                    with self.assertRaisesRegex(RuntimeError, rf'^crates.io version lookup failed \(HTTP {code}\)$'):
                        self.pub.checksum('sample-core', '1.2.3')

    def assert_lookup_status(self, code):
        error = HTTPError('https://crates.io', code, 'private server reason', {}, io.BytesIO(b'private body'))
        with patch.object(self.pub, 'urlopen', side_effect=error):
            with self.assertRaises(RuntimeError) as raised:
                self.pub.checksum('sample-core', '1.2.3')
        self.assertEqual(str(raised.exception), f'crates.io version lookup failed (HTTP {code})')
        formatted = ''.join(traceback.format_exception(raised.exception))
        self.assertNotIn('private server reason', formatted)
        self.assertNotIn('private body', formatted)

    def test_lookup_authentication_status_without_private_text(self):
        self.assert_lookup_status(401)
        self.assert_lookup_status(403)

    def test_lookup_rate_limit_status_without_private_text(self):
        self.assert_lookup_status(429)

    def test_lookup_server_status_without_private_text(self):
        self.assert_lookup_status(500)

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

    def test_real_cargo_fresh_checkouts_reproduce_package_checksum(self):
        # Commit once; each runner gets its own checkout and Cargo target tree.
        source = self.root / 'source'
        (source / 'src').mkdir(parents=True)
        (source / 'src/lib.rs').write_text('pub fn value() -> u8 { 7 }\n')
        (source / 'Cargo.toml').write_text('[package]\nname="sample-core"\nversion="1.2.3"\nedition="2021"\n')
        (source / '.gitignore').write_text('/target/\n')
        subprocess.run(['cargo', 'generate-lockfile', '--offline'], cwd=source, check=True, capture_output=True)
        def git(cwd, *args):
            return subprocess.check_output(['git', '-C', str(cwd), *args], text=True).strip()
        git(source, 'init', '-q')
        git(source, 'add', '.')
        git(source, '-c', 'user.name=Example', '-c', 'user.email=example@example.invalid', 'commit', '-qm', 'Fixture')
        commit = git(source, 'rev-parse', 'HEAD')
        checkouts = [self.root / 'runner-one', self.root / 'runner-two']
        digests, targets = [], []
        for index, checkout in enumerate(checkouts):
            git(self.root, 'clone', '--no-local', str(source), str(checkout))
            self.assertEqual(git(checkout, 'rev-parse', 'HEAD'), commit)
            if index == 1:
                for path in (checkout / 'Cargo.toml', checkout / 'Cargo.lock', checkout / 'src/lib.rs'):
                    os.utime(path, (978307200, 978307200))
            env = dict(os.environ, CARGO_TARGET_DIR=str(checkout / 'target'))
            metadata = json.loads(subprocess.check_output(
                ['cargo', 'metadata', '--offline', '--no-deps', '--format-version', '1'], cwd=checkout, env=env))
            targets.append(Path(metadata['target_directory']))
            self.value['release_units']['sample-tool']['projections'] = [projection(str(checkout / 'Cargo.toml'))]
            with patch.dict(os.environ, env):
                packages = self.pub.plan(self.value, ['sample-tool'], self.releases)
            self.assertEqual([p['name'] for p in packages], ['sample-core'])
            subprocess.run(['cargo', 'package', '--locked', '--offline'], cwd=checkout, env=env,
                           check=True, capture_output=True)
            archive = targets[-1] / 'package/sample-core-1.2.3.crate'
            digests.append(hashlib.sha256(archive.read_bytes()).hexdigest())
            self.assertEqual(git(checkout, 'status', '--porcelain'), '')
        self.assertNotEqual(checkouts[0], checkouts[1])
        self.assertNotEqual(targets[0], targets[1])
        self.assertNotEqual((checkouts[0] / 'src/lib.rs').stat().st_mtime,
                            (checkouts[1] / 'src/lib.rs').stat().st_mtime)
        self.assertEqual(digests[0], digests[1])
