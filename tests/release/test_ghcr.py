# relationships: validates: release-process
"""GHCR transport boundaries and actual parent renders; no public publication."""
import copy
from contextlib import redirect_stderr, redirect_stdout
import io
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import tarfile
import unittest
from unittest.mock import patch

from harness import ROOT, render, check_parts, load
from model import model

proof_spec = importlib.util.spec_from_file_location('ghcr_oci_proof', ROOT / 'tests/release/ghcr_oci_proof.py')
ghcr_oci_proof = importlib.util.module_from_spec(proof_spec)
exec(compile(Path(proof_spec.origin).read_text(), proof_spec.origin, 'exec'), ghcr_oci_proof.__dict__)

spec = importlib.util.spec_from_file_location('ghcr', ROOT / 'release/targets/ghcr/publish.py')
ghcr = importlib.util.module_from_spec(spec)
exec(compile(Path(spec.origin).read_text(), spec.origin, 'exec'), ghcr.__dict__)
RAW = b'{"schemaVersion":2,"mediaType":"application/vnd.oci.image.index.v1+json"}'
DIGEST = 'sha256:' + hashlib.sha256(RAW).hexdigest()

# Captured authenticated GHCR diagnostic grammar; destination only is normalized.
AUTHENTICATED_MISSING_MANIFEST = (
    b'time="2026-10-21T03:30:43Z" level=fatal msg="Error parsing image name \\"docker://ghcr.io/example-org/sample-tool/sample-tool:1.2.3\\": reading manifest 1.2.3 in ghcr.io/example-org/sample-tool/sample-tool: manifest unknown"\n',
    b'time="2026-10-21T03:49:24Z" level=fatal msg="Error parsing image name \\"docker://ghcr.io/example-org/sample-tool/sample-tool:1.2.3\\": reading manifest 1.2.3 in ghcr.io/example-org/sample-tool/sample-tool: manifest unknown"\n',
)


class Transport:
    def __init__(self, tag=None, error=None, fail_tag=False, invisible=False, digest_raw=RAW, missing_error=b'manifest unknown'):
        self.calls = []
        self.tag = tag
        self.error = error
        self.fail_tag = fail_tag
        self.invisible = invisible
        self.digest_raw = digest_raw
        self.pushed = False
        self.missing_error = missing_error

    def __call__(self, args, **kwargs):
        self.calls.append((args, kwargs))
        if args[:2] == ['git', 'rev-parse']:
            return b'a' * 40
        if args[:2] == ['git', 'show']:
            return b'1700000000\n'
        if args[:2] == ['skopeo', 'inspect']:
            ref = args[-1]
            if ref.startswith('oci-archive:'):
                return RAW
            if self.error:
                raise subprocess.CalledProcessError(1, args, stderr=self.error)
            if '@' in ref and self.pushed:
                return self.digest_raw
            if self.tag is None:
                raise subprocess.CalledProcessError(1, args, stderr=self.missing_error)
            return self.tag
        if args[:2] == ['skopeo', 'copy']:
            if '@' in args[-1]:
                self.pushed = True
            else:
                if not self.invisible:
                    self.tag = RAW
                if self.fail_tag:
                    raise subprocess.CalledProcessError(1, args)
        return b''

    def writes(self):
        return [a for a, _ in self.calls if a[:2] == ['skopeo', 'copy']]


class GhcrPublisher(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / 'Dockerfile').write_text('FROM scratch\n')
        self.value = model()
        self.env = dict(GHCR_MODEL=json.dumps(self.value), GHCR_UNITS='sample-tool',
                        GHCR_DRY_RUN='false', RELEASE_UNITS='sample-tool',
                        RELEASE_JSON=json.dumps({'sample-tool': {'version': '1.2.3'}}),
                        RELEASE_SOURCE_SHA='a' * 40, GITHUB_REPOSITORY='example-org/sample-tool',
                        GITHUB_ACTOR='example-actor', GHCR_TOKEN='synthetic-token')

    def publish(self, transport, env=None):
        ghcr.publish(env or self.env, transport, self.root)

    def assert_tool_failure_logged(self, prefix, remote=False, tag=False, writes=0):
        transport = Transport()
        stdout, stderr = io.StringIO(), io.StringIO()

        def execute(args, **kwargs):
            fails = args[:len(prefix)] == prefix
            if tag and '@' in args[-1]:
                fails = False
            if remote and args[-1].startswith('oci-archive:'):
                fails = False
            if fails:
                raise subprocess.CalledProcessError(23, args,
                    output=b'original tool stdout synthetic-token\n',
                    stderr=b'original tool diagnostic synthetic-token\n')
            return subprocess.CompletedProcess(args, 0, stdout=transport(args, **kwargs))

        # Exercise the actual command() adapter as well as publication error paths.
        with patch.object(ghcr.subprocess, 'run', side_effect=execute):
            with redirect_stdout(stdout), redirect_stderr(stderr):
                with self.assertRaises((subprocess.CalledProcessError, RuntimeError)):
                    ghcr.publish(self.env, root=self.root)
        self.assertIn('original tool stdout', stdout.getvalue())
        self.assertIn('original tool diagnostic', stderr.getvalue())
        self.assertNotIn('synthetic-token', stdout.getvalue() + stderr.getvalue())
        self.assertEqual(len(transport.writes()), writes)

    def test_build_failure_diagnostics(self):
        self.assert_tool_failure_logged(['docker', 'buildx', 'build'])

    def test_builder_failure_diagnostics(self):
        self.assert_tool_failure_logged(['docker', 'buildx', 'create'])

    def test_login_failure_diagnostics(self):
        self.assert_tool_failure_logged(['skopeo', 'login'])

    def test_local_inspect_failure_diagnostics(self):
        self.assert_tool_failure_logged(['skopeo', 'inspect'])

    def test_remote_inspect_failure_diagnostics(self):
        self.assert_tool_failure_logged(['skopeo', 'inspect'], remote=True)

    def test_copy_failure_diagnostics(self):
        self.assert_tool_failure_logged(['skopeo', 'copy'])

    def test_tag_copy_failure_diagnostics(self):
        self.assert_tool_failure_logged(['skopeo', 'copy'], tag=True, writes=1)

    def test_cleanup_failure_diagnostics(self):
        self.assert_tool_failure_logged(['docker', 'buildx', 'rm'], writes=2)

    def test_ghcr_unmeasured_denials_fail_closed(self):
        # These are refusal fixtures, not receipts of an authenticated GHCR run.
        for error in [b'403 Forbidden', b'404 Not Found', b'401 Unauthorized',
                      b'denied: requested access to the resource is denied',
                      b'502 Bad Gateway', b'TLS handshake timeout', b'unknown response']:
            with self.subTest(error=error):
                transport = Transport(error=error)
                with self.assertRaisesRegex(RuntimeError, 'lookup failed'):
                    self.publish(transport)
                self.assertEqual(transport.writes(), [])

    def test_captured_authenticated_ghcr_first_lookup(self):
        for diagnostic in AUTHENTICATED_MISSING_MANIFEST:
            with self.subTest(diagnostic=diagnostic):
                transport = Transport(missing_error=diagnostic)
                try:
                    self.publish(transport)
                except RuntimeError as error:
                    self.fail('Captured authenticated missing-manifest response refused: ' + str(error))
                writes = transport.writes()
                self.assertEqual(len(writes), 2)
                self.assertEqual(writes[0][-1], 'docker://ghcr.io/example-org/sample-tool/sample-tool@' + DIGEST)
                self.assertEqual(writes[1][-2], writes[0][-1])
                self.assertEqual(writes[1][-1], 'docker://ghcr.io/example-org/sample-tool/sample-tool:1.2.3')

    def test_captured_authenticated_ghcr_matching_retry(self):
        for diagnostic in AUTHENTICATED_MISSING_MANIFEST:
            with self.subTest(diagnostic=diagnostic):
                transport = Transport(tag=RAW, missing_error=diagnostic)
                self.publish(transport)
                self.assertEqual(transport.writes(), [])

    def test_captured_authenticated_ghcr_conflict(self):
        for diagnostic in AUTHENTICATED_MISSING_MANIFEST:
            with self.subTest(diagnostic=diagnostic):
                transport = Transport(tag=b'different image bytes', missing_error=diagnostic)
                with self.assertRaisesRegex(ValueError, 'different image bytes'):
                    self.publish(transport)
                self.assertEqual(transport.writes(), [])

    def assert_captured_denial_refused(self, replacement):
        for diagnostic in AUTHENTICATED_MISSING_MANIFEST:
            with self.subTest(diagnostic=diagnostic):
                error = diagnostic.replace(b'manifest unknown', replacement)
                transport = Transport(error=error)
                with self.assertRaisesRegex(RuntimeError, 'lookup failed'):
                    self.publish(transport)
                self.assertEqual(transport.writes(), [])

    def test_captured_authenticated_ghcr_unauthorized_near_miss(self):
        self.assert_captured_denial_refused(b'unauthorized: authentication required')

    def test_captured_authenticated_ghcr_denied_near_miss(self):
        self.assert_captured_denial_refused(b'denied: requested access to the resource is denied')

    def test_fresh_and_matching_retry(self):
        transport = Transport()
        self.publish(transport)
        writes = transport.writes()
        self.assertEqual(len(writes), 2)
        self.assertEqual(writes[0][-1], 'docker://ghcr.io/example-org/sample-tool/sample-tool@' + DIGEST)
        self.assertEqual(writes[1][-2], writes[0][-1])
        self.assertEqual(writes[1][-1], 'docker://ghcr.io/example-org/sample-tool/sample-tool:1.2.3')
        for args in writes:
            self.assertIn('--all', args)
            self.assertIn('--preserve-digests', args)
        self.assertEqual(transport.calls[-1][0][:3], ['docker', 'buildx', 'rm'])
        build = next(a for a, _ in transport.calls if a[:3] == ['docker', 'buildx', 'build'])
        self.assertIn('SOURCE_DATE_EPOCH=1700000000', build)
        self.assertIn('linux/amd64,linux/arm64', build)
        self.assertIn('org.opencontainers.image.source=https://github.com/example-org/sample-tool', build)
        self.assertIn('rewrite-timestamp=true', build[-2])
        self.assertIn('org.opencontainers.image.revision=' + 'a' * 40, build)
        self.assertIn('org.opencontainers.image.version=1.2.3', build)
        self.assertIn('--provenance=false', build)
        self.assertIn('--sbom=false', build)
        retry = Transport(tag=RAW)
        self.publish(retry)
        self.assertEqual(retry.writes(), [])
        self.assertTrue(any(a[:2] == ['skopeo', 'inspect'] for a, _ in retry.calls))
        login = next((a, k) for a, k in transport.calls if a[:2] == ['skopeo', 'login'])
        self.assertIn('--password-stdin', login[0])
        self.assertNotIn('synthetic-token', ' '.join(login[0]))
        self.assertEqual(login[1]['input'], b'synthetic-token')

    def test_released_unit_context_and_prerelease_version(self):
        (self.root / 'nested').mkdir()
        (self.root / 'nested/Dockerfile').write_text('FROM scratch\n')
        self.value['release_units']['sample-tool']['path'] = 'nested'
        transport = Transport()
        self.publish(transport, dict(self.env, GHCR_MODEL=json.dumps(self.value),
            RELEASE_JSON=json.dumps({'sample-tool': {'version': '1.2.3-rc.1'}})))
        build = next(a for a, _ in transport.calls if a[:3] == ['docker', 'buildx', 'build'])
        self.assertEqual(build[-1], str(self.root / 'nested'))
        self.assertEqual(transport.writes()[-1][-1], 'docker://ghcr.io/example-org/sample-tool/sample-tool:1.2.3-rc.1')

    def test_conflict_before_push(self):
        transport = Transport(tag=b'different')
        with self.assertRaisesRegex(ValueError, 'different image bytes'):
            self.publish(transport)
        self.assertEqual(transport.writes(), [])
        self.assertEqual(transport.calls[-1][0][:3], ['docker', 'buildx', 'rm'])

    def test_registry_errors_fail_closed(self):
        for error in [b'unauthorized', b'connection refused', b'HTTP 500', b'not found']:
            transport = Transport(error=error)
            with self.assertRaisesRegex(RuntimeError, 'lookup failed'):
                self.publish(transport)
            self.assertEqual(transport.writes(), [])

    def test_digest_readback_before_tag(self):
        transport = Transport(digest_raw=b'wrong digest')
        with self.assertRaisesRegex(ValueError, 'different image bytes'):
            self.publish(transport)
        self.assertEqual(len(transport.writes()), 1)

    def test_concurrent_conflicting_tag_after_digest(self):
        transport = Transport()
        original = transport.__call__

        def racing(args, **kwargs):
            result = original(args, **kwargs)
            if args[:2] == ['skopeo', 'copy'] and '@' in args[-1]:
                transport.tag = b'concurrent different bytes'
            return result

        with self.assertRaisesRegex(ValueError, 'different image bytes'):
            self.publish(racing)
        self.assertEqual(len(transport.writes()), 1)

    def test_partial_digest_retry(self):
        transport = Transport(fail_tag=True, invisible=True)
        with self.assertRaisesRegex(RuntimeError, 'tag publication failed'):
            self.publish(transport)
        self.assertTrue(transport.pushed)
        transport.fail_tag = transport.invisible = False
        transport.calls.clear()
        self.publish(transport)
        self.assertEqual(len(transport.writes()), 2)
        self.assertEqual(transport.tag, RAW)

    def test_concurrent_tag_write_readback(self):
        self.publish(Transport(fail_tag=True))
        transport = Transport(invisible=True)
        with self.assertRaisesRegex(RuntimeError, 'not observable'):
            self.publish(transport)

    def test_record_none_before_any_tool(self):
        for unit in [None, dict(self.value['release_units']['sample-tool'], release_record='none')]:
            value = copy.deepcopy(self.value)
            if unit is None:
                del value['release_units']['sample-tool']
            else:
                value['release_units']['sample-tool'] = unit
            transport = Transport()
            with self.assertRaisesRegex(ValueError, 'requires a release record'):
                self.publish(transport, dict(self.env, GHCR_MODEL=json.dumps(value)))
            self.assertEqual(transport.calls, [])

    def test_missing_dockerfile_before_any_tool(self):
        (self.root / 'Dockerfile').unlink()
        transport = Transport()
        with self.assertRaisesRegex(ValueError, 'Missing GHCR Dockerfile'):
            self.publish(transport)
        self.assertEqual(transport.calls, [])

    def test_dry_run_no_login_or_registry(self):
        transport = Transport()
        self.publish(transport, dict(self.env, GHCR_DRY_RUN='true'))
        self.assertTrue(any(a[:3] == ['docker', 'buildx', 'build'] for a, _ in transport.calls))
        self.assertFalse(any(a[:2] == ['skopeo', 'login'] for a, _ in transport.calls))
        self.assertEqual(transport.writes(), [])
        self.assertFalse(any(a[-1].startswith('docker://') for a, _ in transport.calls))

    def test_missing_dry_choice_before_any_tool(self):
        transport = Transport()
        with self.assertRaisesRegex(ValueError, 'dry-run choice'):
            self.publish(transport, dict(self.env, GHCR_DRY_RUN=''))
        self.assertEqual(transport.calls, [])

    def test_source_checkout_mismatch(self):
        transport = Transport()
        with self.assertRaisesRegex(ValueError, 'checkout mismatch'):
            self.publish(transport, dict(self.env, RELEASE_SOURCE_SHA='b' * 40))
        self.assertFalse(any(a[0] == 'docker' for a, _ in transport.calls))

    def test_unsupported_oci_tag_before_any_tool(self):
        for version in ['v1.2.3', '1.2.3+build', '1.2', '1.2.3-' + 'x' * 130]:
            transport = Transport()
            with self.assertRaisesRegex(ValueError, 'bare OCI tag'):
                self.publish(transport, dict(self.env, RELEASE_JSON=json.dumps({'sample-tool': {'version': version}})))
            self.assertEqual(transport.calls, [])

    def test_selected_and_released_units_only(self):
        self.value['release_units']['sample-docs']['release_record'] = 'required'
        self.value['release_units']['sample-docs']['path'] = 'docs'
        (self.root / 'docs').mkdir()
        (self.root / 'docs/Dockerfile').write_text('FROM scratch\n')
        transport = Transport()
        self.publish(transport, dict(self.env, GHCR_MODEL=json.dumps(self.value), GHCR_UNITS='sample-tool sample-docs',
            RELEASE_JSON=json.dumps({'sample-tool': {'version': '1.2.3'}, 'sample-docs': {'version': '1.2.3'}})))
        self.assertEqual(len(transport.writes()), 2)
        build = [a for a, _ in transport.calls if a[:3] == ['docker', 'buildx', 'build']]
        self.assertEqual(len(build), 1)
        self.assertEqual(build[0][-1], str(self.root))


class GhcrCompilationProof(unittest.TestCase):
    def test_native_build_stage_contract(self):
        build, final = ghcr_oci_proof.DOCKERFILE.split('FROM scratch\n')
        self.assertIn('FROM --platform=$BUILDPLATFORM ', build)
        self.assertIn('ARG TARGETOS', build)
        self.assertIn('ARG TARGETARCH', build)
        self.assertIn('GOOS=$TARGETOS GOARCH=$TARGETARCH go build', build)
        self.assertNotIn('RUN ', final)
        self.assertIn('COPY --from=build /out/sample-tool /sample-tool', final)

    def test_compiled_binary_readback_and_absence(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive_path = Path(tmp) / 'image.tar'
            def write_image(include_binary):
                def add(archive, name, value):
                    member = tarfile.TarInfo(name)
                    member.size = len(value)
                    archive.addfile(member, io.BytesIO(value))
                layer = io.BytesIO()
                with tarfile.open(fileobj=layer, mode='w') as archive:
                    add(archive, 'unrelated', b'other')
                    if include_binary:
                        add(archive, './sample-tool', b'compiled output')
                with tarfile.open(archive_path, 'w') as archive:
                    add(archive, 'manifest.json', b'[{"Layers":["layer.tar"]}]')
                    add(archive, 'layer.tar', layer.getvalue())
            write_image(True)
            self.assertEqual(ghcr_oci_proof.archive_binary(archive_path), b'compiled output')
            write_image(False)
            with self.assertRaisesRegex(AssertionError, 'executable absent'):
                ghcr_oci_proof.archive_binary(archive_path)

    def test_binary_machine_refuses_mislabeled_output(self):
        for arch, machine in [('amd64', 62), ('arm64', 183)]:
            binary = b'\x7fELF\x02\x01' + bytes(12) + machine.to_bytes(2, 'little')
            self.assertEqual(ghcr_oci_proof.verify_binary(binary, arch), machine)
            other = 'arm64' if arch == 'amd64' else 'amd64'
            with self.assertRaises(AssertionError):
                ghcr_oci_proof.verify_binary(binary, other)
        with self.assertRaises(AssertionError):
            ghcr_oci_proof.verify_binary(bytes(18) + (183).to_bytes(2, 'little'), 'arm64')


@unittest.skipUnless(os.environ.get('TOHA_BIN'), 'TOHA_BIN is required')
class GhcrPart(unittest.TestCase):
    def workflow(self, selected=True, value=None):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        template, workflow, answers = render(Path(tmp.name), value or model(), selected=['ghcr'] if selected else [],
            answers={'ghcr_units': 'sample-tool', 'ghcr_dry_run': True} if selected else {})
        parsed = check_parts(template, workflow, ['ghcr'] if selected else [], answers)
        subprocess.run(['actionlint', str(workflow)], check=True, capture_output=True)
        return parsed, workflow

    def test_selected_and_deselected_contracts(self):
        parsed, _ = self.workflow()
        job = parsed['jobs']['publish-ghcr']
        self.assertEqual(job['permissions'], {'contents': 'read', 'packages': 'write'})
        self.assertIn('release-record', job['needs'])
        self.assertIn("needs.release-record.result == 'success'", job['if'])
        self.assertEqual(job['env']['GHCR_DRY_RUN'], 'true')
        self.assertEqual(set(parsed['on']), {'workflow_dispatch'})
        for name, other in parsed['jobs'].items():
            if name != 'publish-ghcr':
                self.assertNotEqual(other.get('permissions', {}).get('packages'), 'write')
        removed, workflow = self.workflow(False)
        self.assertNotIn('publish-ghcr', removed['jobs'])
        self.assertNotIn('GHCR_', workflow.read_text())
        self.assertNotIn('secrets.GITHUB_TOKEN', workflow.read_text())

    def test_unknown_unit_refused_at_render(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(AssertionError, 'Toha exit 1'):
                render(Path(tmp), model(), selected=['ghcr'],
                       answers={'ghcr_units': 'unknown-unit', 'ghcr_dry_run': True})
            self.assertFalse((Path(tmp) / 'project/.github/workflows/release.yml').exists())

    def test_model_data_stays_literal(self):
        value = model()
        value['release_units']['sample-tool']['changelog'] = '${{ secrets.SAMPLE_TOKEN }}.md'
        parsed, _ = self.workflow(value=value)
        encoded = parsed['jobs']['publish-ghcr']['env']['GHCR_MODEL']
        self.assertNotIn('${{', encoded)
        self.assertEqual(json.loads(encoded), value)

    def test_emitted_entrypoint(self):
        parsed, workflow = self.workflow()
        job = parsed['jobs']['publish-ghcr']
        step = job['steps'][-1]
        self.assertEqual(step['env'], {'GHCR_TOKEN': '${{ secrets.GITHUB_TOKEN }}'})
        script = step['run'].split("python3 <<'PY'\n", 1)[1].rsplit('\nPY', 1)[0]
        namespace = {'__name__': 'rendered'}
        exec(compile(script, str(workflow), 'exec'), namespace)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'Dockerfile').write_text('FROM scratch\n')
            env = dict(job['env'], RELEASE_JSON=json.dumps({'sample-tool': {'version': '1.2.3'}}),
                       RELEASE_UNITS='sample-tool', RELEASE_SOURCE_SHA='a' * 40,
                       GITHUB_REPOSITORY='example-org/sample-tool')
            transport = Transport()
            namespace['publish'](env, transport, root)
            self.assertEqual(transport.writes(), [])
            self.assertTrue(any(a[:3] == ['docker', 'buildx', 'build'] for a, _ in transport.calls))
