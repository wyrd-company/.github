#!/usr/bin/env python3
# relationships: validates: release-process
"""Opt-in real OCI proof: Docker, Buildx, Go; no public registry writes.

Run with --scratch /absolute/empty/directory --report /absolute/receipt.json.
Pulls standard BuildKit, Skopeo and registry images, builds two fresh source
snapshots and copies both architectures through a loopback-only registry.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import uuid


def proof(scratch, report):
    scratch.mkdir(parents=True, exist_ok=False)
    result = {'status': 'INCOMPLETE', 'commands': []}
    report.parent.mkdir(parents=True, exist_ok=True)

    def save():
        report.write_text(json.dumps(result, indent=2) + '\n')

    save()
    config = scratch / 'docker-config'
    config.mkdir()
    env = dict(os.environ, DOCKER_CONFIG=str(config))

    def run(args, **options):
        completed = subprocess.run(args, env=options.pop('env', env), stdout=subprocess.PIPE, stderr=subprocess.PIPE, **options)
        result['commands'].append(dict(args=args, exit=completed.returncode,
                                       stdout=completed.stdout.decode(), stderr=completed.stderr.decode()))
        save()
        completed.check_returncode()
        return completed.stdout

    def skopeo(*args):
        return run(['docker', 'run', '--rm', '--network', 'host', '-v', str(scratch) + ':' + str(scratch),
                    'quay.io/skopeo/stable', *args])

    identity = 'ghcr-proof-' + uuid.uuid4().hex
    builder = identity + '-builder'
    registry = identity + '-registry'
    builder_created = registry_created = image_loaded = False
    try:
        first = scratch / 'first'
        first.mkdir()
        (first / 'main.go').write_text('package main\nimport "fmt"\nfunc main() { fmt.Println("sample-tool") }\n')
        for arch in ('amd64', 'arm64'):
            run(['go', 'build', '-trimpath', '-ldflags=-s -w -buildid=', '-o', 'sample-' + arch, 'main.go'],
                cwd=first, env=dict(env, CGO_ENABLED='0', GOOS='linux', GOARCH=arch))
        (first / 'Dockerfile').write_text('FROM scratch\nARG TARGETARCH\nCOPY sample-${TARGETARCH} /sample-tool\nENTRYPOINT ["/sample-tool"]\n')
        second = scratch / 'second'
        shutil.copytree(first, second)
        for file in second.iterdir():
            os.utime(file, (1800000000, 1800000000))
        run(['docker', 'buildx', 'create', '--name', builder, '--driver', 'docker-container'])
        builder_created = True
        raw = []
        for name, context in [('first', first), ('second', second)]:
            archive = str(scratch / (name + '.tar'))
            run(['docker', 'buildx', 'build', '--builder', builder, '--no-cache',
                 '--platform', 'linux/amd64,linux/arm64', '--provenance=false', '--sbom=false',
                 '--build-arg', 'SOURCE_DATE_EPOCH=1700000000',
                 '--output', 'type=oci,dest=' + archive + ',rewrite-timestamp=true', str(context)])
            raw.append(skopeo('inspect', '--raw', 'oci-archive:' + archive))
        assert raw[0] == raw[1], 'Fresh source snapshots produced different OCI manifests'
        platforms = {(d['platform']['os'], d['platform']['architecture']) for d in json.loads(raw[0])['manifests']}
        assert platforms == {('linux', 'amd64'), ('linux', 'arm64')}, platforms
        digest = 'sha256:' + hashlib.sha256(raw[0]).hexdigest()
        result['digest'] = digest
        run(['docker', 'run', '-d', '--name', registry, '-p', '127.0.0.1::5000', 'registry:2'])
        registry_created = True
        address = run(['docker', 'port', registry, '5000/tcp']).decode().strip()
        image = 'docker://' + address + '/sample-tool'
        immutable, tag = image + '@' + digest, image + ':1.2.3'
        skopeo('copy', '--dest-tls-verify=false', '--all', '--preserve-digests',
               'oci-archive:' + str(scratch / 'first.tar'), immutable)
        assert skopeo('inspect', '--tls-verify=false', '--raw', immutable) == raw[0]
        skopeo('copy', '--src-tls-verify=false', '--dest-tls-verify=false', '--all', '--preserve-digests', immutable, tag)
        assert skopeo('inspect', '--tls-verify=false', '--raw', tag) == raw[0]
        # A second fresh archive is a matching retry, independent of tar metadata.
        assert hashlib.sha256(skopeo('inspect', '--raw', 'oci-archive:' + str(scratch / 'second.tar'))).hexdigest() == digest[7:]
        # Load the host architecture and exercise the scratch image's entry point.
        skopeo('copy', '--src-tls-verify=false', '--override-arch=amd64', tag,
               'docker-archive:' + str(scratch / 'runnable.tar') + ':sample-tool:proof')
        run(['docker', 'load', '--input', str(scratch / 'runnable.tar')])
        image_loaded = True
        assert run(['docker', 'run', '--rm', 'sample-tool:proof']).strip() == b'sample-tool'
    finally:
        if registry_created:
            run(['docker', 'rm', '-f', registry])
        if builder_created:
            run(['docker', 'buildx', 'rm', builder])
        if image_loaded:
            run(['docker', 'image', 'rm', 'sample-tool:proof'])
        save()
    result['status'] = 'PASS'
    save()
    print('PASS real OCI snapshot, digest, version tag, platforms and runnable scratch image: ' + result['digest'])


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scratch', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    proof(args.scratch.resolve(), args.report.resolve())
