#!/usr/bin/env python3
# relationships: validates: release-process
"""Sole-kill proof; each mutation must fail its named behavioral test."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
CASES = []


def mutation(file, old, new, test, suite='node'):
    CASES.append((file, old, new, test, suite))


r = 'release/partials/resolve.js'
for old, test in [
    ("!/^[0-9a-f]{40}$/.test(source)", 'resolve guard: source-sha spelling'),
    ("env.DISPATCH_REF !== `refs/heads/${env.DEFAULT_BRANCH}`", 'resolve guard: default branch dispatch'),
    ('env.DISPATCH_SHA !== source', 'resolve guard: dispatch source identity'),
    ('head !== source', 'resolve guard: remote default branch source identity'),
    ("!runs.some(run => run.head_sha === source && run.event === 'push' && run.conclusion === 'success')", 'resolve guard: no CI run'),
    ("tag.role === 'primary' && tag.require_phase === 'after-publication'", 'resolve guard: primary after-publication is refused'),
    ("git(['rev-parse', `${ref}^{}`]).trim() !== source", 'resolve ignores primary records at other commits'),
    ("message.split('\\n')[0] !== 'intentional release record'", 'resolve ignores lookalike records without the exact header'),
    ("Object.hasOwn(fields, field[1])", 'resolve guard: duplicate record field'),
    ("fields.baseline === 'true'", 'baseline-only dispatch fails even at a nonzero version'),
    ("fields.baseline !== 'false'", 'resolve guard: missing baseline'),
    ('name !== expected', 'resolve guard: primary name mismatch'),
    ('Object.hasOwn(releases, unit)', 'resolve rejects two primary streams releasing the same unit'),
    ('!units.length', 'baseline-only dispatch fails even at a nonzero version'),
]: mutation(r, old, 'false', test)
mutation(r, "git(['fetch', '--force', 'origin', 'refs/tags/*:refs/tags/*']);", '', 'resolve guard: tag object is fetched even when checkout lacked it')
mutation(r, "model.release_units[unit].release_record === 'required'", 'true', 'record-none-only dispatch emits false and an empty record matrix')
mutation(r, "version.split('+')[0].includes('-')", 'false', 'resolve real annotated primary, baseline, record-none, prerelease and unrelated tags')
for old, new, test in [
    ('run.head_sha === source && ', '', 'resolve guard: wrong CI commit'),
    ("run.event === 'push' && ", '', 'resolve guard: wrong CI event'),
    (" && run.conclusion === 'success'", '', 'resolve guard: unsuccessful CI run'),
]: mutation(r, old, new, test)
mutation(r, 'event: \'push\', status: \'success\'', "event: 'pull_request', status: 'success'", 'resolve real annotated primary, baseline, record-none, prerelease and unrelated tags')

r = 'release/partials/release-record.js'
for old, test in [
    ('start < 0', 'release notes exact heading, nonempty content and next section boundary'),
    ('!notes', 'release notes exact heading, nonempty content and next section boundary'),
    ('!item.isFile()', 'release assets refuse links'),
    ("files.has(item.name) || item.name === 'SHA256SUMS'", 'release asset collection hashes files, rejects duplicate and reserved names'),
    ('!release', 'release record refuses units not resolved in this run'),
    ('releases.length > 1', 'record rejects: two releases, no assets'),
    ('existing.some(item => !files.has(item.name))', 'record rejects: unexpected draft asset, no assets'),
    ('readBack.id !== record.id', 'record read-back guard: draft, prerelease, names, tag and identity, no assets'),
]: mutation(r, old, 'false', test)
for old, test in [
    ('item.draft || ', 'record read-back guard: draft, prerelease, names, tag and identity, no assets'),
    ('item.tag_name !== release.tag || ', 'record read-back guard: draft, prerelease, names, tag and identity, no assets'),
    ('item.prerelease !== release.prerelease || ', 'record rejects: wrong prerelease, no assets'),
    (' || !sameNames(await assets(item))', 'record rejects: wrong published names, no assets'),
]: mutation(r, old, '', test)
mutation(r, "files.set('SHA256SUMS', Buffer.from(sums));", '', 'release asset collection hashes files, rejects duplicate and reserved names')
mutation(r, "await github.rest.repos.deleteReleaseAsset({...repo, asset_id: item.id})", 'Promise.resolve()', 'retry replaces draft assets after a partial upload failure')
mutation(r, 'draft: false,\n      prerelease:', 'draft: true,\n      prerelease:', 'record retry: no release, no assets')
mutation(r, '!release.prerelease && ', '', 'latest-unit prerelease stays non-latest')
mutation(r, "env.RELEASE_UNIT === env.LATEST_UNIT", 'true', 'prereleases and non-latest units never become latest')
mutation(r, "if (record && !record.draft)", 'if (false)', 'record retry: published expected, no assets')
mutation('release/partials/preflight.js', 'if (missing.length)', 'if (false)', 'preflight reports missing names and purposes without credential values')

r = 'release/partials/jobs.yml'
for old, new, test in [
    ('!cancelled() && ', '', 'test_part_graph_all_stages_and_units'),
    ('!failure() && ', '', 'test_part_graph_all_stages_and_units'),
    ("needs.resolve.result == 'success'", 'true', 'test_part_graph_all_stages_and_units'),
    (" && needs.{{ preflight }}.result == 'success'", '', 'test_part_graph_all_stages_and_units'),
    ("ns.build if stage != 'build' else []", '[]', 'test_empty_publish_stage_still_waits_for_build_and_record'),
    ("ns.publish if stage == 'verify' else []", '[]', 'test_part_graph_all_stages_and_units'),
    (" && needs.release-record.result == 'success'", '', 'test_part_graph_all_stages_and_units'),
    ('RELEASE_SOURCE_SHA:', 'SOURCE_SHA:', 'test_part_graph_all_stages_and_units'),
]: mutation(r, old, new, 'test_template.ReleaseTemplate.' + test, 'python')
r = 'release/template/.github/workflows/release.yml'
for old, new, test in [
    (" && needs.resolve.outputs.record == 'true'", '', 'test_part_graph_all_stages_and_units'),
    ("not credential.unless or not credential_answers[credential.unless]", 'true', 'test_trusted_publishing_omits_only_selected_credential'),
    ("credential.name not in group.names", 'true', 'test_duplicate_names_checked_once_per_environment'),
    ("'vars' if credential.kind == 'variable' else 'secrets'", "'secrets'", 'test_trusted_publishing_omits_only_selected_credential'),
    ("ns.record_units %}\n  release-record:", "true %}\n  release-record:", 'test_all_none_omits_record_job'),
    (" | replace('$', '\\\\u0024')", '', 'test_scripts_have_no_github_expressions_and_data_is_single_render'),
    ('cancel-in-progress: false', 'cancel-in-progress: true', 'test_base_dispatch_outputs_and_pins'),
    ('contents: write', 'contents: read', 'test_base_dispatch_outputs_and_pins'),
]: mutation(r, old, new, 'test_template.ReleaseTemplate.' + test, 'python')
mutation('tests/release/harness.py', 'assert job not in jobs_seen,', 'assert True,', 'test_template.ReleaseTemplate.test_duplicate_job_keys_across_parts_fail', 'python')
mutation('tests/release/harness.py', 'assert key not in credentials_seen,', 'assert True,', 'test_template.ReleaseTemplate.test_duplicate_credential_keys_across_parts_fail', 'python')
# Remove the vendored root. The proxy observation, rather than an eventual render
# failure, must be the assertion that kills the attempted hosted fallback.
mutation('release/template.yml', 'schemas: schemas', 'schemas: missing-schemas', 'test_template.VendoredResolution.test_vendored_schema_makes_zero_network_attempts', 'python-network')


# Final internal-schema guards are part of the interface boundary.
r = 'release/schemas/publish-job.schema.json'
schema = json.loads((ROOT / r).read_text())
for old, new in [
    (json.dumps(schema['properties']['if']['allOf'][1]['not']['pattern']), '"never-status"'),
    ('"contents": { "const": "read" }', '"contents": {}'),
    ('"actions": { "const": "read" }', '"actions": {}'),
    ('"additionalProperties": false,\n      "required": ["runs-on", "permissions", "steps"]', '"additionalProperties": true,\n      "required": ["runs-on", "permissions", "steps"]'),
    ('"not": { "pattern": "^(RELEASE_|GITHUB_)" }', '"not": { "pattern": "NEVERMATCH" }'),
    (json.dumps(schema['$defs']['step']['properties']['uses']['pattern']), '".*"'),
    ('"minLength": 1,\n          "not": { "pattern": "\\\\$\\\\{\\\\{" }', '"minLength": 1,\n          "not": { "pattern": "NEVERMATCH" }'),
]: mutation(r, old, new, 'test_template.InternalSchemas.test_schema_guards', 'python')


for old in ['needs.resolve.outputs.source_sha', 'needs.resolve.outputs.releases',
            "join(fromJSON(needs.resolve.outputs.units), ' ')"]:
    mutation('release/partials/jobs.yml', old, 'needs.resolve.outputs.wrong',
             'test_template.ReleaseTemplate.test_part_graph_all_stages_and_units', 'python')
for old in ['inputs.source-sha', 'github.ref', 'github.sha', 'github.event.repository.default_branch']:
    mutation('release/template/.github/workflows/release.yml', old, 'github.wrong',
             'test_template.ReleaseTemplate.test_base_dispatch_outputs_and_pins', 'python')


def run(report):
    results = []
    head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
    report.write_text(json.dumps({'status': 'INCOMPLETE', 'head': head, 'results': []}, indent=2) + '\n')
    for index, (file, old, new, test, suite) in enumerate(CASES, 1):
        target = ROOT / file
        original = target.read_bytes()
        source = original.decode()
        occurrences = source.count(old)
        if not occurrences:
            raise AssertionError(f'Mutation {index}: source not found: {old}')
        mutated = source.replace(old, new, 1)
        if suite == 'python-network':
            # An absent schema directory is a source error before resolution;
            # use an existing empty directory to observe the actual fetch attempt.
            empty = ROOT / 'release/missing-schemas'
            empty.mkdir(exist_ok=False)
        command = (['node', '--test', '--test-name-pattern', '^' + re.escape(test) + '$', 'tests/release/runtime.test.cjs']
                   if suite == 'node' else [sys.executable, '-m', 'unittest', '-v', test])
        env = None
        if suite != 'node':
            import os
            env = dict(os.environ, PYTHONPATH=str(ROOT / 'tests/release'))
        try:
            target.write_text(mutated)
            result = subprocess.run(command, cwd=ROOT, env=env, text=True, capture_output=True)
            output = result.stdout + result.stderr
            if suite == 'node':
                killed = result.returncode != 0 and ('✖ ' + test in output or 'not ok 1 - ' + test in output)
            else:
                killed = result.returncode != 0 and ('FAIL: ' + test.rsplit('.', 1)[1] in output)
            if suite == 'python-network':
                killed = killed and 'Vendored resolution attempted network access' in output
            record = {'index': index, 'file': file, 'old': old, 'new': new, 'applications': 1,
                      'test': test, 'exit_code': result.returncode, 'killed': killed,
                      'original_sha256': hashlib.sha256(original).hexdigest(), 'output': output}
            results.append(record)
            print(f'{index}/{len(CASES)} {"KILLED" if killed else "SURVIVED"} {test}', flush=True)
        finally:
            target.write_bytes(original)
            assert target.read_bytes() == original, 'Mutation restoration failed'
            if suite == 'python-network':
                empty.rmdir()
        report.write_text(json.dumps({'status': 'INCOMPLETE', 'head': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(), 'results': results}, indent=2) + '\n')
    passed = len(results) == len(CASES) and all(r['killed'] for r in results)
    payload = json.loads(report.read_text()); payload['status'] = 'PASS' if passed else 'FAIL'
    report.write_text(json.dumps(payload, indent=2) + '\n')
    if not passed:
        raise SystemExit('Mutation proof FAILED; inspect ' + str(report))
    print(f'PASS {len(results)}/{len(CASES)} sole-kills; report {report}')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    args.report.parent.mkdir(parents=True, exist_ok=True)
    run(args.report)
