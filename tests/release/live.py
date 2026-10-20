#!/usr/bin/env python3
# relationships: validates: release-process
"""Prepare generic live proof cases; never create repositories or dispatch runs."""
import argparse
import json
from pathlib import Path
import subprocess
import sys

from harness import dump, load, render
from model import model

SCENARIOS = ['skipped-build', 'empty-publish', 'empty-publish-failed-build', 'record-gate',
             'baseline', 'missing-credential', 'no-release', 'empty-draft', 'expected-draft',
             'unexpected-draft', 'published-expected', 'published-wrong-prerelease',
             'published-wrong-assets', 'two-releases']
FAIL_RECORD = {'record-gate', 'unexpected-draft', 'published-wrong-prerelease', 'published-wrong-assets', 'two-releases'}


def prepare(output, scenario, assets):
    if output.exists() and any(output.iterdir()):
        raise SystemExit('Output must be an empty directory')
    output.mkdir(parents=True, exist_ok=True)
    template, workflow, answers = render(output, model(), samples=2,
        answers={'trusted_publishing': scenario != 'missing-credential', 'trusted_publishing_2': True},
        omit_publish=scenario.startswith('empty-publish'))
    document = load(workflow)
    # Test-only build contributions: no registry writes.
    build = document['jobs']['build-sample']
    if assets:
        build['steps'].extend([
            {'run': "mkdir -p assets\nprintf 'sample\\n' > assets/sample.tar"},
            {'uses': 'actions/upload-artifact@bbbca2ddaa5d8feaa63e36b76fdaad77386f024f',
             'with': {'name': 'release-asset+sample-tool+sample', 'path': 'assets/sample.tar'}},
        ])
    if scenario == 'empty-publish-failed-build':
        build['steps'].append({'run': 'exit 1'})
    for name, job in document['jobs'].items():
        if name.startswith(('build-', 'publish-', 'verify-')):
            job['name'] = name
    dump(workflow, document)
    # Preserve the mandatory version comment on the additional fixture action.
    if assets:
        text = workflow.read_text().replace('actions/upload-artifact@bbbca2ddaa5d8feaa63e36b76fdaad77386f024f\n',
            'actions/upload-artifact@bbbca2ddaa5d8feaa63e36b76fdaad77386f024f # v7.0.0\n')
        workflow.write_text(text)
    project = workflow.parents[2]
    (project / 'CHANGELOG.md').write_text('# Changelog\n\n## 1.2.3\n\n' + ('' if scenario == 'record-gate' else 'Sample changes.\n'))
    (project / '.github/workflows/ci.yml').write_text(
        'name: CI\non: [push]\npermissions: {}\njobs:\n  check:\n    runs-on: ubuntu-latest\n    permissions: {}\n    steps:\n      - run: echo sample-ci-complete\n')
    expected = {}
    for name in document['jobs']:
        state = 'success'
        if scenario == 'baseline':
            state = 'failure' if name == 'resolve' else 'skipped'
        elif scenario == 'missing-credential':
            state = 'failure' if name == 'preflight' else ('success' if name in ('resolve', 'preflight-sample') else 'skipped')
        elif name.endswith('-2'):
            state = 'skipped'
        elif scenario == 'empty-publish-failed-build':
            state = 'failure' if name == 'build-sample' else ('skipped' if name == 'release-record' or name.startswith('verify-') else 'success')
        elif scenario in FAIL_RECORD:
            state = 'failure' if name == 'release-record' else ('skipped' if name.startswith(('publish-', 'verify-')) else 'success')
        expected[name] = state
    case = {'scenario': scenario, 'assets': assets, 'tag': 'sample-tool-1.2.3', 'version': '1.2.3',
            'expected_jobs': expected, 'expected_assets': ['sample.tar', 'SHA256SUMS'] if assets else [],
            'expect_success': scenario not in FAIL_RECORD | {'baseline', 'missing-credential', 'empty-publish-failed-build'}}
    (project / '.live-case.json').write_text(json.dumps(case, indent=2) + '\n')
    subprocess.run(['actionlint', str(workflow), str(project / '.github/workflows/ci.yml')], check=True)
    print(project)


def api(repo, endpoint, method='GET', payload=None):
    command = ['gh', 'api', '--paginate', f'repos/{repo}/{endpoint}']
    if method != 'GET':
        command += ['--method', method]
    if payload is not None:
        command += ['--input', '-']
    result = subprocess.run(command, input=json.dumps(payload) if payload is not None else None,
                            text=True, capture_output=True, check=True)
    # Only GET lists paginate; gh emits one JSON array per page.
    decoder = json.JSONDecoder()
    text, values = result.stdout.strip(), []
    while text:
        value, end = decoder.raw_decode(text)
        values.extend(value if isinstance(value, list) else [value])
        text = text[end:].lstrip()
    return values


def seed(repo, case):
    scenario, tag = case['scenario'], case['tag']
    if scenario in ('no-release', 'skipped-build', 'empty-publish', 'empty-publish-failed-build', 'record-gate', 'baseline', 'missing-credential'):
        return
    draft = scenario not in ('published-expected', 'published-wrong-prerelease', 'published-wrong-assets')
    existing = [r for r in api(repo, 'releases?per_page=100') if r['tag_name'] == tag]
    if existing:
        raise SystemExit('Use a clean case repository/tag; seed refuses existing releases')
    created = api(repo, 'releases', 'POST', {'tag_name': tag, 'name': tag, 'body': 'Seeded sample', 'draft': True, 'make_latest': 'false'})[0]
    names = []
    if scenario in ('expected-draft', 'published-expected', 'published-wrong-prerelease'):
        names = case['expected_assets'][:1] if draft else case['expected_assets']
    elif scenario in ('unexpected-draft', 'published-wrong-assets'):
        names = ['unexpected.bin']
    for name in names:
        # GitHub's uploads host is a separate REST endpoint; gh accepts its URL.
        url = created['upload_url'].split('{')[0] + '?name=' + name
        subprocess.run(['gh', 'api', url, '--method', 'POST', '--header', 'Content-Type: application/octet-stream', '--input', '-'],
                       input='sample\n', text=True, capture_output=True, check=True)
    if not draft:
        api(repo, f"releases/{created['id']}", 'PATCH', {'draft': False, 'prerelease': scenario == 'published-wrong-prerelease', 'make_latest': 'false'})
    if scenario == 'two-releases':
        api(repo, 'releases', 'POST', {'tag_name': tag, 'name': tag, 'body': 'Second seeded sample', 'draft': True, 'make_latest': 'false'})


def check(repo, run_id, case):
    run = api(repo, f'actions/runs/{run_id}')[0]
    assert run['status'] == 'completed', 'Run has not completed'
    assert run['event'] == 'workflow_dispatch' and run['path'] == '.github/workflows/release.yml', 'Wrong workflow run'
    expected_conclusion = 'success' if case['expect_success'] else 'failure'
    assert run['conclusion'] == expected_conclusion, run
    response = api(repo, f'actions/runs/{run_id}/jobs?per_page=100')
    jobs = [job for page in response for job in page['jobs']]
    observed = {}
    for job in jobs:
        name = job['name']
        if name.startswith('release-record'):
            name = 'release-record'
        assert name not in observed, f'Duplicate observed job: {name}'
        observed[name] = job['conclusion']
    assert observed == case['expected_jobs'], (observed, case['expected_jobs'])
    if case['expect_success']:
        records = [r for r in api(repo, 'releases?per_page=100') if r['tag_name'] == case['tag']]
        assert len(records) == 1 and not records[0]['draft'], records
        release = api(repo, 'releases/tags/' + case['tag'])[0]
        assert release['id'] == records[0]['id'] and release['prerelease'] is False, release
        names = [a['name'] for a in api(repo, f"releases/{release['id']}/assets?per_page=100")]
        assert sorted(names) == sorted(case['expected_assets']), names
    print(json.dumps({'status': 'PASS', 'scenario': case['scenario'], 'assets': case['assets'], 'run_url': run['html_url'], 'jobs': observed}, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('prepare'); p.add_argument('--output', type=Path, required=True); p.add_argument('--scenario', choices=SCENARIOS, required=True); p.add_argument('--assets', action='store_true')
    for command in ('seed', 'check'):
        p = sub.add_parser(command); p.add_argument('--repo', required=True); p.add_argument('--case', type=Path, required=True)
        if command == 'check': p.add_argument('--run-id', required=True)
    args = parser.parse_args()
    if args.command == 'prepare': prepare(args.output.resolve(), args.scenario, args.assets)
    elif args.command == 'seed': seed(args.repo, json.loads(args.case.read_text()))
    else: check(args.repo, args.run_id, json.loads(args.case.read_text()))
