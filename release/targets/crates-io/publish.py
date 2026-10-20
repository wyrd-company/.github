# relationships: implements: release-process
"""Inline Cargo publisher. Only the Cargo publish command writes to crates.io."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen


def cargo(*args):
    return subprocess.check_output(['cargo', *args], text=True)


def plan(model, units, releases):
    """Resolve every admitted crate and edge before the first registry write."""
    packages = {}
    for unit in units:
        if unit not in releases:
            continue
        value = model['release_units'][unit]
        if value['release_record'] != 'required':
            raise ValueError('crates.io publication requires a release record')
        manifests = [p['file'] for p in value['projections']
                     if p['adapter'] == 'cargo' and not p['generic'] and p['mode'] != 'none']
        if not manifests:
            raise ValueError('Selected released unit has no Cargo projection')
        for manifest in manifests:
            metadata = json.loads(cargo('metadata', '--no-deps', '--format-version', '1',
                                        '--manifest-path', manifest))
            matches = [p for p in metadata['packages'] if Path(p['manifest_path']).resolve() == Path(manifest).resolve()]
            if len(matches) != 1:
                raise ValueError('Cargo projection must identify one package manifest')
            package = matches[0]
            if package['version'] != releases[unit]['version']:
                raise ValueError('Cargo package version differs from released unit version')
            if package.get('publish') is not None and 'crates-io' not in package['publish']:
                raise ValueError('Cargo package does not permit crates.io publication')
            name = package['name']
            if name in packages:
                raise ValueError('Cargo package belongs to more than one selected projection')
            packages[name] = package
    # Normal and build dependencies, including renamed/optional/target-specific
    # path dependencies, use Cargo's native identity (not the dependency alias).
    ordered, pending = [], dict(packages)
    while pending:
        ready = [name for name, p in pending.items() if not any(
            d.get('path') and d.get('kind') != 'dev' and d['name'] in pending
            for d in p['dependencies'])]
        if not ready:
            raise ValueError('Cargo publication dependency cycle')
        for name in sorted(ready):
            ordered.append(pending.pop(name))
    return ordered


def checksum(name, version):
    # Read-only API: sees accepted submissions even before sparse-index visibility.
    url = 'https://crates.io/api/v1/crates/' + quote(name, safe='') + '/' + quote(version, safe='')
    try:
        with urlopen(Request(url, headers={'User-Agent': 'release-template-crates-io'})) as response:
            value = json.load(response)['version']
    except HTTPError as error:
        if error.code == 404:
            return None
        raise RuntimeError('crates.io version lookup failed') from None
    if value['crate'] != name or value['num'] != version:
        raise ValueError('crates.io returned a different crate version')
    result = value['checksum']
    if not isinstance(result, str) or len(result) != 64:
        raise ValueError('crates.io returned no package checksum')
    return result


def ensure(package):
    name, version = package['name'], package['version']
    manifest = package['manifest_path']
    # Explicit registry prevents repository configuration redirecting publication.
    cargo('package', '--locked', '--registry', 'crates-io', '--manifest-path', manifest)
    metadata = json.loads(cargo('metadata', '--no-deps', '--format-version', '1', '--manifest-path', manifest))
    archive = Path(metadata['target_directory']) / 'package' / (name + '-' + version + '.crate')
    expected = hashlib.sha256(archive.read_bytes()).hexdigest()
    observed = checksum(name, version)
    if observed is not None:
        if observed != expected:
            raise ValueError('Published crate has different package bytes')
        print(name + ' ' + version + ' already submitted with matching bytes')
        return
    # A concurrent submission or Cargo's own index-wait failure can leave a
    # successful upload. Inspect its checksum before deciding this run failed.
    result = subprocess.run(['cargo', 'publish', '--locked', '--registry', 'crates-io', '--manifest-path', manifest],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    observed = checksum(name, version)
    if observed != expected:
        raise ValueError('Crate submission not confirmed with matching bytes')
    print(name + ' ' + version + ' submitted with matching bytes')
    # Cargo waits for dependency visibility. If it failed, stop this run before
    # dependent crates; a later retry can adopt the confirmed submission.
    if result.returncode:
        raise RuntimeError('Cargo did not finish registry indexing; retry the release')


def main():
    releases = json.loads(os.environ['RELEASE_JSON'])
    units = set(os.environ['CRATES_UNITS'].split()) & set(os.environ['RELEASE_UNITS'].split())
    packages = plan(json.loads(os.environ['CRATES_MODEL']), sorted(units), releases)
    for package in packages:
        ensure(package)


if __name__ == '__main__':
    main()
