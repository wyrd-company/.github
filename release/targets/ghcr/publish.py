# relationships: implements: release-process
"""Build a source-bound OCI archive, compare, then publish immutable digests."""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import uuid


def command(args, **kwargs):
    return subprocess.run(args, check=True, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, **kwargs).stdout


def publish(env, run=command, root=Path.cwd()):
    tool = run

    def run(args, **kwargs):
        try:
            return tool(args, **kwargs)
        except subprocess.CalledProcessError as error:
            # Keep tool diagnostics even when lookup/retry translates the error.
            # Credentials travel through stdin, never argv or diagnostic output.
            for output, stream in ((error.stdout, sys.stdout), (error.stderr, sys.stderr)):
                text = (output or b'').decode(errors='replace')
                token = env.get('GHCR_TOKEN', '')
                if token:
                    text = text.replace(token, '[REDACTED]')
                print(text, end='', file=stream, flush=True)
            raise

    model = json.loads(env['GHCR_MODEL'])
    releases = json.loads(env['RELEASE_JSON'])
    released = env['RELEASE_UNITS'].split()
    selected = env['GHCR_UNITS'].split()
    if env.get('GHCR_DRY_RUN') not in ('true', 'false'):
        raise ValueError('Missing GHCR dry-run choice')
    dry = env['GHCR_DRY_RUN'] == 'true'
    images = []
    for unit_id in selected:
        unit = model['release_units'].get(unit_id)
        if not unit or unit['release_record'] != 'required':
            raise ValueError('GHCR requires a release record: ' + unit_id)
        context = root / unit['path']
        if not (context / 'Dockerfile').is_file():
            raise ValueError('Missing GHCR Dockerfile: ' + unit_id)
        if unit_id not in released:
            continue
        version = releases[unit_id]['version']
        # OCI tags cannot represent SemVer build metadata (+); never silently
        # map two distinct versions onto the same mutable tag.
        if not re.fullmatch(r'(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(?:-[0-9A-Za-z.-]+)?', version) or len(version) > 128:
            raise ValueError('Version cannot be represented as a bare OCI tag: ' + version)
        images.append((unit_id, context, version))
    repository = env['GITHUB_REPOSITORY'].lower()
    source = env['RELEASE_SOURCE_SHA']
    if run(['git', 'rev-parse', 'HEAD'], cwd=root).decode().strip() != source:
        raise ValueError('GHCR source checkout mismatch')
    epoch = run(['git', 'show', '-s', '--format=%ct', source], cwd=root).decode().strip()
    with tempfile.TemporaryDirectory(prefix='release-ghcr-') as scratch:
        auth = str(Path(scratch) / 'auth.json')
        builder = 'release-ghcr-' + uuid.uuid4().hex
        run(['docker', 'buildx', 'create', '--name', builder, '--driver', 'docker-container'], cwd=root)
        try:
            for unit_id, context, version in images:
                archive = str(Path(scratch) / (unit_id + '.tar'))
                image = 'ghcr.io/' + repository + '/' + unit_id
                run(['docker', 'buildx', 'build', '--builder', builder,
                     '--platform', 'linux/amd64,linux/arm64', '--provenance=false', '--sbom=false',
                     '--build-arg', 'SOURCE_DATE_EPOCH=' + epoch,
                     '--label', 'org.opencontainers.image.source=https://github.com/' + env['GITHUB_REPOSITORY'],
                     '--label', 'org.opencontainers.image.revision=' + source,
                     '--label', 'org.opencontainers.image.version=' + version,
                     '--output', 'type=oci,dest=' + archive + ',rewrite-timestamp=true', str(context)], cwd=root)
                local = 'oci-archive:' + archive
                raw = run(['skopeo', 'inspect', '--raw', local])
                digest = 'sha256:' + hashlib.sha256(raw).hexdigest()
                if dry:
                    print('Dry run built ' + image + ':' + version + ' ' + digest)
                    continue
                run(['skopeo', 'login', '--authfile', auth, '--username', env['GITHUB_ACTOR'],
                     '--password-stdin', 'ghcr.io'], input=env['GHCR_TOKEN'].encode())
                tag = 'docker://' + image + ':' + version

                def lookup(reference, missing=False):
                    try:
                        remote = run(['skopeo', 'inspect', '--authfile', auth, '--raw', reference])
                    except subprocess.CalledProcessError as error:
                        # Only the registry's explicit missing-manifest response
                        # permits a write. Authentication/transport failures stop.
                        if missing and b'manifest unknown' in (error.stderr or b'').lower():
                            return False
                        raise RuntimeError('GHCR lookup failed') from None
                    if hashlib.sha256(remote).hexdigest() != digest.removeprefix('sha256:'):
                        raise ValueError('GHCR version exists with different image bytes')
                    return True

                if lookup(tag, missing=True):
                    continue
                immutable = 'docker://' + image + '@' + digest
                # Never tag an image before its immutable digest is observable.
                run(['skopeo', 'copy', '--authfile', auth, '--all', '--preserve-digests', local, immutable])
                lookup(immutable)
                # Recheck the tag after the digest upload; a concurrent matching
                # publisher or a retry after partial failure can finish the work.
                if lookup(tag, missing=True):
                    continue
                try:
                    run(['skopeo', 'copy', '--authfile', auth, '--all', '--preserve-digests', immutable, tag])
                except subprocess.CalledProcessError:
                    if not lookup(tag, missing=True):
                        raise RuntimeError('GHCR tag publication failed') from None
                if not lookup(tag, missing=True):
                    raise RuntimeError('GHCR publication not observable; rerun this source commit')
        finally:
            run(['docker', 'buildx', 'rm', builder])


if __name__ == '__main__':
    publish(os.environ)
