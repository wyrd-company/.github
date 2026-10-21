# relationships: validates: release-process
"""Offline admission of captured per-attempt Actions and Release observations."""
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

WORKFLOW = '.github/workflows/release.yml'


def require(condition, message):
    if not condition:
        raise ValueError(message)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def encoded(value):
    return json.dumps(value, sort_keys=True).encode()


def save_new(path, value):
    with Path(path).open('x') as stream:
        json.dump(value, stream, indent=2)
        stream.write('\n')


def now():
    return datetime.now(timezone.utc).isoformat()


def timestamp(value):
    require(isinstance(value, str), 'Missing timestamp')
    result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    require(result.tzinfo is not None, 'Timestamp needs timezone')
    return result


def identity(repo, run_id, case, receipt, evidence):
    require(receipt is not None, 'Missing external receipt')
    require(evidence['status'] == 'COMPLETE', 'Incomplete attempt observation')
    require(receipt['repository'] == repo and str(receipt['run_id']) == str(run_id), 'Wrong repository/run receipt')
    require(re.fullmatch('[0-9a-f]{40}', receipt['source_sha']), 'Incomplete source SHA')
    require(receipt['case_sha256'] == digest(encoded(case)), 'Case differs from source receipt')
    require(evidence['receipt_sha256'] == digest(encoded(receipt)), 'Observation belongs to another receipt')
    require(timestamp(receipt['source_committed_at']) <= timestamp(receipt['created_at']), 'Receipt predates source commit')
    attempt = receipt['attempt']
    require(type(attempt) is int and attempt > 0, 'Explicit positive attempt required')
    prefix = f'actions/runs/{run_id}/attempts/{attempt}'
    require(evidence['run_endpoint'] == prefix and evidence['jobs_endpoint'] == prefix + '/jobs?per_page=100',
            'Historical attempt endpoints differ')
    platform = evidence['repository']
    require(platform['full_name'] == repo and receipt['dispatch_ref'] == 'refs/heads/' + platform['default_branch'],
            'Dispatch did not use the observed default branch')
    run = evidence['run']
    require(run['status'] == 'completed', 'Run has not completed')
    require(run['event'] == 'workflow_dispatch', 'Wrong event')
    require(run['repository']['full_name'] == repo and str(run['id']) == str(run_id), 'Wrong run identity')
    require(run['head_sha'] == receipt['source_sha'], 'Wrong source SHA')
    require(run['head_branch'] == receipt['dispatch_ref'].removeprefix('refs/heads/'), 'Wrong dispatch ref')
    require(run['run_attempt'] == attempt, 'Wrong run attempt')
    require(run['path'] == WORKFLOW and run['workflow_id'] == receipt['workflow_id'], 'Wrong workflow identity')
    require(receipt['workflow_revision'] == receipt['source_sha'], 'Workflow revision differs from source')
    require(evidence['workflow_revision'] == receipt['workflow_revision'], 'Wrong observed workflow revision')
    require(digest(evidence['workflow_bytes'].encode()) == receipt['workflow_sha256'], 'Wrong workflow bytes')
    require(receipt['workflow_ref'] == f'{repo}/{WORKFLOW}@{receipt["dispatch_ref"]}', 'Wrong workflow ref receipt')
    lines = [line.split('LIVE_IDENTITY=', 1)[1] for line in evidence['resolve_logs'].splitlines()
             if 'LIVE_IDENTITY=' in line and not line.split('LIVE_IDENTITY=', 1)[1].startswith("' +")]
    markers = []
    for line in lines:
        try:
            markers.append(json.loads(line))
        except json.JSONDecodeError:
            pass  # A printed script line is not a runtime identity marker.
    expected = dict(repository=repo, sha=receipt['source_sha'], ref=receipt['dispatch_ref'],
                    workflow_ref=receipt['workflow_ref'], workflow_sha=receipt['workflow_revision'],
                    run_id=str(run_id), run_attempt=str(attempt), event_name='workflow_dispatch',
                    source_input=receipt['source_input'])
    require(receipt['source_input'] == receipt['source_sha'], 'Dispatch input differs from source')
    require(markers == [expected], 'Missing or mismatched actual dispatch/workflow/attempt marker')
    ci = evidence['ci']
    require(ci['repository']['full_name'] == repo and ci['head_sha'] == receipt['source_sha'] and
            ci['event'] == 'push' and ci['path'] == '.github/workflows/ci.yml' and
            ci['status'] == 'completed' and ci['conclusion'] == 'success', 'Missing exact-source successful push CI')
    require(timestamp(ci['updated_at']) <= timestamp(run['run_started_at']), 'CI completed after release started')
    return run


def jobs_for(run, case, evidence, partial=False):
    jobs = {}
    for page in evidence['jobs_pages']:
        require('total_count' in page, 'Missing job inventory count')
        for job in page['jobs']:
            name = 'release-record' if job['name'].startswith('release-record') else job['name']
            require(name not in jobs, f'Duplicate observed job: {name}')
            require(job['run_id'] == run['id'] and job['head_sha'] == run['head_sha'], 'Wrong job source/run')
            require('run_attempt' in job, 'Missing job attempt')
            require(job['run_attempt'] == run['run_attempt'], 'Wrong job attempt')
            jobs[name] = job
    require(evidence['jobs_pages'] and len(jobs) == evidence['jobs_pages'][0]['total_count'], 'Incomplete job inventory')
    expected = dict(case['expected_jobs'])
    if partial:
        expected = {name: ('failure' if name == 'release-record' else
                    'skipped' if name.startswith(('publish-', 'verify-')) else value) for name, value in expected.items()}
    require(set(jobs) == set(expected), f'Never-observed or unexpected jobs: {set(jobs) ^ set(expected)}')
    for name, job in jobs.items():
        require(job['status'] == 'completed' and job['conclusion'] == expected[name], f'Wrong job conclusion: {name}')
        started_steps = [step for step in job['steps'] if step.get('started_at') and step.get('conclusion') != 'skipped']
        if expected[name] == 'skipped':
            require(not started_steps, f'Skipped job started steps: {name}')
        else:
            start, end = timestamp(job['started_at']), timestamp(job['completed_at'])
            require(timestamp(run['run_started_at']) <= start <= end <= timestamp(run['updated_at']),
                    f'Contradictory job timestamps: {name}')
            require(started_steps, f'Missing job start evidence: {name}')
            for step in started_steps:
                require(start <= timestamp(step['started_at']) <= timestamp(step['completed_at']) <= end,
                        f'Contradictory step timestamps: {name}')
    if case['scenario'] == 'missing-credential':
        for name, job in jobs.items():
            if name.startswith('build-'):
                require(job['conclusion'] == 'skipped' and not job['steps'] and not job.get('runner_id'),
                        f'Build started despite missing credentials: {name}')
    if case['scenario'].startswith('empty-publish') and not partial:
        required = [job for name, job in jobs.items() if name.startswith('build-') or name == 'release-record']
        for name, job in jobs.items():
            if name.startswith('verify-') and job['conclusion'] != 'skipped':
                require(all(other['conclusion'] == 'success' and
                            timestamp(other['completed_at']) <= timestamp(job['started_at'])
                            for other in required if other['conclusion'] != 'skipped'),
                        f'Verification precedes required completion: {name}')
    return jobs


def release_state(snapshot, repo, tag):
    require(snapshot['repository'] == repo and snapshot['tag'] == tag and snapshot['complete'] is True,
            'Incomplete or wrong Release snapshot')
    timestamp(snapshot['captured_at'])
    records = snapshot['records']
    ids = set()
    for record in records:
        require(record['tag_name'] == tag and record['id'] not in ids, 'Wrong/duplicate same-tag inventory')
        ids.add(record['id'])
        for key in ('id', 'updated_at', 'draft', 'prerelease'):
            require(key in record, f'Missing Release metadata: {key}')
        require(timestamp(record['updated_at']) <= timestamp(snapshot['captured_at']), 'Release updated after snapshot')
        assets = record['assets']
        require(len({a['id'] for a in assets}) == len(assets), 'Duplicate asset identity')
        for asset in assets:
            require(all(key in asset for key in ('id', 'name', 'updated_at')), 'Missing asset metadata')
            require(timestamp(asset['updated_at']) <= timestamp(snapshot['captured_at']), 'Asset updated after snapshot')
    lookup = snapshot['lookup']
    if lookup['status'] == 200:
        require(lookup['release'] in records, 'Exact-tag lookup differs from inventory')
    else:
        require(lookup['status'] == 404 and (not records or all(r['draft'] for r in records)),
                'Missing/failed exact-tag lookup')
    return sorted(records, key=lambda r: r['id'])


def seed_state(case, records):
    scenario = case['scenario']
    if scenario in ('skipped-build', 'empty-publish', 'empty-publish-failed-build', 'record-gate',
                    'baseline', 'missing-credential', 'no-release'):
        require(not records, 'Expected clean no-Release seed')
        return
    require(len(records) == (2 if scenario == 'two-releases' else 1), 'Wrong seeded Release count')
    draft = not scenario.startswith('published-')
    names = []
    if scenario in ('expected-draft', 'published-expected', 'published-wrong-prerelease'):
        names = case['expected_assets'][:1] if draft else case['expected_assets']
    elif scenario in ('unexpected-draft', 'published-wrong-assets'):
        names = ['unexpected.bin']
    for record in records:
        require(record['draft'] == draft and record['prerelease'] == (scenario == 'published-wrong-prerelease') and
                sorted(a['name'] for a in record['assets']) == sorted(names), 'Seed state differs from declared case')


def verify(repo, run_id, case, receipt, evidence, before, seed, previous=None):
    require(seed['status'] == 'COMPLETE', 'Seed refused/partial: case UNVERIFIED')
    require(seed['repository'] == repo and seed['case_sha256'] == digest(encoded(case)), 'Wrong seed receipt')
    require(seed['commands'] and all(all(key in item for key in ('command', 'input', 'exit', 'stdout', 'stderr'))
            for item in seed['commands']), 'Missing original seed requests/results')
    require(all(item['exit'] == 0 or ('--method' not in item['command'] and
                item['command'][-1] == f"repos/{repo}/releases/tags/{case['tag']}" and 'HTTP 404' in item['stderr'])
                for item in seed['commands']), 'Failed seed command: case UNVERIFIED')
    require(not release_state(seed['before'], repo, case['tag']), 'Seed did not start clean')
    require(timestamp(seed['before']['captured_at']) <= timestamp(seed['after']['captured_at']), 'Contradictory seed times')
    seeded = release_state(seed['after'], repo, case['tag'])
    seed_state(case, seeded)
    if previous is None:
        require(seed['after'] == before, 'Before snapshot differs from retained seed state')
    run = identity(repo, run_id, case, receipt, evidence)
    require(run['conclusion'] == ('success' if case['expect_success'] else 'failure'), 'Wrong run conclusion')
    jobs = jobs_for(run, case, evidence)
    old = release_state(before, repo, case['tag'])
    after = evidence['after']
    current = release_state(after, repo, case['tag'])
    require(timestamp(before['captured_at']) <= timestamp(run['run_started_at']) and
            timestamp(after['captured_at']) >= timestamp(run['updated_at']), 'Release snapshot time contradicts attempt')
    if case['expect_success']:
        require(len(current) == 1 and not current[0]['draft'] and current[0]['prerelease'] is False,
                'Expected one published Release')
        require(after['lookup']['status'] == 200, 'Published Release missing exact-tag lookup')
        if old and old[0]['draft']:
            require(current[0]['id'] == old[0]['id'], 'Retry replaced existing Release identity')
        require(sorted(a['name'] for a in current[0]['assets']) == sorted(case['expected_assets']), 'Wrong published assets')
    if case['scenario'].startswith('published-') or not case['expect_success']:
        require(old == current, 'Release/asset metadata changed; no-write not established')
    if previous is not None:
        first_receipt, first = previous['receipt'], previous['evidence']
        require(case['assets'] and case['expect_success'], 'Partial continuation needs an asset success case')
        prior = identity(repo, first_receipt['run_id'], case, first_receipt, first)
        require((str(prior['id']), prior['run_attempt']) != (str(run['id']), run['run_attempt']), 'Continuation reuses first attempt')
        require(prior['conclusion'] == 'failure', 'First attempt did not fail')
        prior_jobs = jobs_for(prior, case, first, partial=True)
        require(any(item['job_id'] == prior_jobs['release-record']['id'] and item['logs'].strip()
                    for item in first['record_logs']), 'Missing first-attempt record logs')
        require(timestamp(seed['after']['captured_at']) <= timestamp(prior['run_started_at']),
                'Seed snapshot follows first attempt')
        partial = release_state(first['after'], repo, case['tag'])
        require(len(partial) == 1 and partial[0]['draft'] and
                0 < len(partial[0]['assets']) < len(case['expected_assets']) and
                {a['name'] for a in partial[0]['assets']} < set(case['expected_assets']), 'Missing partial draft upload')
        require(partial == old and current[0]['id'] == partial[0]['id'], 'Continuation lost existing Release/assets')
        require(timestamp(prior['updated_at']) <= timestamp(first['after']['captured_at']) <=
                timestamp(before['captured_at']), 'Contradictory continuation times')
    return {'status': 'PASS', 'repository': repo, 'source_sha': receipt['source_sha'],
            'run_id': str(run_id), 'attempt': receipt['attempt'], 'scenario': case['scenario'],
            'assets': case['assets'], 'receipt_sha256': digest(encoded(receipt)),
            'jobs': {name: job['conclusion'] for name, job in jobs.items()},
            'continuation': previous is not None}
