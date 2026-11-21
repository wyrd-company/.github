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
    live_model = model()
    live_model["release_units"]["sample-tool"]["tags"]["primary"]["template"] = "{version}"
    template, workflow, answers = render(output, live_model, samples=2,
        answers={'trusted_publishing': scenario != 'missing-credential', 'trusted_publishing_2': True},
        omit_publish=scenario.startswith('empty-publish'))
    document = load(workflow)
    # Test-only observation; binds actual dispatch input and workflow revision in logs.
    document['jobs']['resolve']['steps'].insert(0, {
        'name': 'Live evidence identity',
        'env': dict({('LIVE_' + key.upper()): '${{ github.' + key + ' }}'
                     for key in ('repository', 'sha', 'ref', 'workflow_ref', 'workflow_sha', 'run_id', 'run_attempt', 'event_name')},
                    LIVE_SOURCE_INPUT='${{ inputs.source-sha }}'),
        'run': "python3 - <<'PYIDENTITY'\nimport json, os\n"
               "keys = ['repository', 'sha', 'ref', 'workflow_ref', 'workflow_sha', 'run_id', 'run_attempt', 'event_name']\n"
               "value = {k: os.environ['LIVE_' + k.upper()] for k in keys}\nvalue['source_input'] = os.environ['LIVE_SOURCE_INPUT']\n"
               "print('LIVE_IDENTITY=' + json.dumps(value, sort_keys=True))\nPYIDENTITY\n",
    })
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
    dump(project / '.intentional/config.yml', {
        '$schema': 'https://intentional.foo/schemas/0.2/config', 'contract': 'contract-1',
        'release-units': {name: {'path': unit['path'], 'release-record': unit['release_record'],
                                'tags': {'primary': {'role': 'primary',
                                    'template': unit['tags']['primary']['template']}}}
                          for name, unit in live_model['release_units'].items()},
    })
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
    case = {'scenario': scenario, 'assets': assets, 'tag': '1.2.3', 'version': '1.2.3',
            'expected_jobs': expected, 'expected_assets': ['sample.tar', 'SHA256SUMS'] if assets else [],
            'expect_success': scenario not in FAIL_RECORD | {'baseline', 'missing-credential', 'empty-publish-failed-build'}}
    (project / '.live-case.json').write_text(json.dumps(case, indent=2) + '\n')
    subprocess.run(['actionlint', str(workflow), str(project / '.github/workflows/ci.yml')], check=True)
    print(project)


def command_receipt(command, payload=None, journal=None):
    result = subprocess.run(command, input=payload, text=True, capture_output=True)
    if journal is not None:
        entry = {'command': command, 'input': payload, 'exit': result.returncode,
                 'stdout': result.stdout, 'stderr': result.stderr}
        # Flush each original request/result before any following mutation.
        with journal.open('a') as stream:
            stream.write(json.dumps(entry) + '\n')
    if result.returncode:
        raise subprocess.CalledProcessError(result.returncode, command, result.stdout, result.stderr)
    return result.stdout


def api(repo, endpoint, method='GET', payload=None, journal=None, raw=False):
    command = ['gh', 'api']
    if method == 'GET' and not raw:
        command.append('--paginate')
    route = f'repos/{repo}/{endpoint}' if endpoint else f'repos/{repo}'
    command.append(endpoint if endpoint.startswith('https://') else route)
    if method != 'GET':
        command += ['--method', method]
    if payload is not None:
        command += ['--input', '-']
    output = command_receipt(command, json.dumps(payload) if payload is not None else None, journal)
    if raw:
        return output
    decoder = json.JSONDecoder()
    text, values = output.strip(), []
    while text:
        value, end = decoder.raw_decode(text)
        values.extend(value if isinstance(value, list) else [value])
        text = text[end:].lstrip()
    return values


def snapshot(repo, case, journal):
    from live_evidence import now
    tag = case['tag']
    records = [r for r in api(repo, 'releases?per_page=100', journal=journal) if r['tag_name'] == tag]
    for record in records:
        record['assets'] = api(repo, f"releases/{record['id']}/assets?per_page=100", journal=journal)
    try:
        release = api(repo, 'releases/tags/' + tag, journal=journal)[0]
        release['assets'] = api(repo, f"releases/{release['id']}/assets?per_page=100", journal=journal)
        lookup = {'status': 200, 'release': release}
    except subprocess.CalledProcessError as error:
        if 'HTTP 404' not in (error.stderr or ''):
            raise
        lookup = {'status': 404}
    return dict(repository=repo, tag=tag, records=records, lookup=lookup, complete=True, captured_at=now())


def seed(repo, case, output):
    from live_evidence import digest, encoded, save_new
    output.mkdir(exist_ok=False, parents=True)
    journal = output / 'commands.jsonl'
    result = dict(status='INCOMPLETE', repository=repo, case_sha256=digest(encoded(case)))
    try:
        result['before'] = snapshot(repo, case, journal)
        if result['before']['records']:
            raise ValueError('Use a clean case repository/tag; seed refuses existing releases')
        scenario, tag = case['scenario'], case['tag']
        if scenario not in ('no-release', 'skipped-build', 'empty-publish', 'empty-publish-failed-build',
                            'record-gate', 'baseline', 'missing-credential'):
            draft = not scenario.startswith('published-')
            created = api(repo, 'releases', 'POST', {'tag_name': tag, 'name': tag, 'body': 'Seeded sample',
                          'draft': True, 'make_latest': 'false'}, journal)[0]
            names = []
            if scenario in ('expected-draft', 'published-expected', 'published-wrong-prerelease'):
                names = case['expected_assets'][:1] if draft else case['expected_assets']
            elif scenario in ('unexpected-draft', 'published-wrong-assets'):
                names = ['unexpected.bin']
            for name in names:
                url = created['upload_url'].split('{')[0] + '?name=' + name
                command_receipt(['gh', 'api', url, '--method', 'POST', '--header',
                                 'Content-Type: application/octet-stream', '--input', '-'], 'sample\n', journal)
            if not draft:
                api(repo, f"releases/{created['id']}", 'PATCH', {'draft': False,
                    'prerelease': scenario == 'published-wrong-prerelease', 'make_latest': 'false'}, journal)
            if scenario == 'two-releases':
                api(repo, 'releases', 'POST', {'tag_name': tag, 'name': tag, 'body': 'Second seeded sample',
                    'draft': True, 'make_latest': 'false'}, journal)
        result['status'] = 'COMPLETE'
    except (ValueError, subprocess.CalledProcessError) as error:
        result['error'] = str(error)
    finally:
        try:
            result['after'] = snapshot(repo, case, journal)
        except (KeyError, ValueError, subprocess.CalledProcessError) as error:
            result['status'] = 'INCOMPLETE'
            result['readback_error'] = str(error)
        result['commands'] = [json.loads(line) for line in journal.read_text().splitlines()] if journal.exists() else []
        save_new(output / 'seed.json', result)
    if result['status'] != 'COMPLETE':
        raise ValueError('Seed refused/partial: UNVERIFIED; retain seed.json and commands.jsonl')
    return result


def bind(source, repo, run_id, attempt, workflow_id, dispatch_ref, output):
    from live_evidence import WORKFLOW, digest, encoded, now, require, save_new
    def git(*args):
        return subprocess.check_output(['git', '-C', str(source), *args])
    require(not git('status', '--porcelain').strip(), 'Fixture source must be committed and clean')
    sha = git('rev-parse', 'HEAD').decode().strip()
    workflow = git('show', f'{sha}:{WORKFLOW}')
    case = json.loads(git('show', f'{sha}:.live-case.json'))
    require(attempt > 0 and workflow_id > 0 and str(run_id).isdigit(), 'Numeric run/workflow IDs and positive attempt required')
    require(dispatch_ref.startswith('refs/heads/'), 'Dispatch requires a branch ref')
    receipt = dict(repository=repo, source_sha=sha, source_input=sha, dispatch_ref=dispatch_ref,
        run_id=str(run_id), attempt=attempt, workflow_id=workflow_id, workflow_revision=sha,
        workflow_ref=f'{repo}/{WORKFLOW}@{dispatch_ref}', workflow_sha256=digest(workflow),
        case_sha256=digest(encoded(case)), source_committed_at=git('show', '-s', '--format=%cI', sha).decode().strip(),
        created_at=now())
    save_new(output, receipt)
    return receipt


def observe(receipt, case, ci_run_id, output):
    import base64
    from live_evidence import digest, encoded, save_new
    output.mkdir(exist_ok=False, parents=True)
    journal = output / 'commands.jsonl'
    repo = receipt['repository']
    prefix = f"actions/runs/{receipt['run_id']}/attempts/{receipt['attempt']}"
    result = dict(status='INCOMPLETE', receipt_sha256=digest(encoded(receipt)),
                  run_endpoint=prefix, jobs_endpoint=prefix + '/jobs?per_page=100')
    try:
        result['run'] = api(repo, prefix, journal=journal)[0]
        result['jobs_pages'] = api(repo, result['jobs_endpoint'], journal=journal)
        result['ci'] = api(repo, f'actions/runs/{ci_run_id}', journal=journal)[0]
        result['repository'] = api(repo, '', journal=journal)[0]
        # Fetch actual observed revision, never substitute receipt bytes.
        revision = result['run']['head_sha']
        content = api(repo, f'contents/.github/workflows/release.yml?ref={revision}', journal=journal)[0]
        result['workflow_revision'] = revision
        result['workflow_bytes'] = base64.b64decode(content['content']).decode()
        result['resolve_logs'] = ''
        result['record_logs'] = []
        for page in result['jobs_pages']:
            for job in page['jobs']:
                if job['name'] == 'resolve' or job['name'].startswith('release-record'):
                    logs = api(repo, f"actions/jobs/{job['id']}/logs", journal=journal, raw=True)
                    if job['name'] == 'resolve':
                        result['resolve_logs'] = logs
                    else:
                        result['record_logs'].append({'job_id': job['id'], 'logs': logs})
        result['after'] = snapshot(repo, case, journal)
        result['status'] = 'COMPLETE'
    except (KeyError, ValueError, subprocess.CalledProcessError) as error:
        result['error'] = str(error)
    finally:
        save_new(output / 'evidence.json', result)
    if result['status'] != 'COMPLETE':
        raise ValueError('Observation incomplete: UNVERIFIED; retain original commands')
    return result


def check(repo, run_id, case, receipt=None, evidence=None, seed_receipt=None, previous=None, before=None):
    from live_evidence import require, verify
    require(receipt is not None, 'Missing external receipt')
    require(evidence is not None and evidence['status'] == 'COMPLETE', 'Incomplete observation')
    require(seed_receipt is not None, 'Missing seed/before receipt')
    return verify(repo, run_id, case, receipt, evidence, before or seed_receipt['after'], seed_receipt, previous)


def main(argv=None):
    from live_evidence import save_new
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('prepare')
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--scenario', choices=SCENARIOS, required=True)
    p.add_argument('--assets', action='store_true')
    p = sub.add_parser('bind')
    p.add_argument('--source', type=Path, required=True)
    p.add_argument('--repo', required=True)
    p.add_argument('--run-id', required=True)
    p.add_argument('--attempt', type=int, required=True)
    p.add_argument('--workflow-id', type=int, required=True)
    p.add_argument('--dispatch-ref', required=True)
    p.add_argument('--output', type=Path, required=True)
    for command in ('seed', 'snapshot', 'observe', 'check'):
        p = sub.add_parser(command)
        p.add_argument('--case', type=Path, required=True)
        if command != 'observe':
            p.add_argument('--repo', required=True)
        if command in ('observe', 'check'):
            p.add_argument('--receipt', type=Path, required=True)
        if command == 'check':
            p.add_argument('--run-id', required=True)
            p.add_argument('--evidence', type=Path, required=True)
            p.add_argument('--seed', type=Path, required=True)
            p.add_argument('--previous', type=Path, help='JSON with first-attempt receipt and evidence')
            p.add_argument('--before', type=Path, help='Separate pre-continuation snapshot; original seed stays fixed')
            p.add_argument('--report', type=Path, required=True)
        else:
            p.add_argument('--output', type=Path, required=True)
        if command == 'observe':
            p.add_argument('--ci-run-id', required=True)
    args = parser.parse_args(argv)
    if args.command == 'prepare':
        prepare(args.output, args.scenario, args.assets)
        return 0
    if args.command == 'bind':
        bind(args.source, args.repo, args.run_id, args.attempt, args.workflow_id, args.dispatch_ref, args.output)
        return 0
    def read(path):
        return json.loads(path.read_text())
    if args.command == 'snapshot':
        args.output.mkdir(exist_ok=False, parents=True)
        save_new(args.output / 'snapshot.json', snapshot(args.repo, read(args.case), args.output / 'commands.jsonl'))
        return 0
    if args.command == 'seed':
        seed(args.repo, read(args.case), args.output)
        return 0
    if args.command == 'observe':
        observe(read(args.receipt), read(args.case), args.ci_run_id, args.output)
        return 0
    # Reserve a fresh report before loading evidence; a crash leaves INCOMPLETE.
    save_new(args.report, {'status': 'INCOMPLETE'})
    try:
        result = check(args.repo, args.run_id, read(args.case), read(args.receipt), read(args.evidence),
                       read(args.seed), read(args.previous) if args.previous else None, read(args.before) if args.before else None)
    except (KeyError, TypeError, ValueError, OSError) as error:
        result = {'status': 'UNVERIFIED', 'error': str(error)}
    completed = args.report.with_name(args.report.name + '.complete')
    save_new(completed, result)
    completed.replace(args.report)
    print(json.dumps(result, indent=2))
    return 0 if result['status'] == 'PASS' else 1


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (KeyError, ValueError, OSError, subprocess.CalledProcessError) as error:
        print(f'UNVERIFIED: {error}', file=sys.stderr)
        sys.exit(1)
