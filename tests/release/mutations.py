#!/usr/bin/env python3
# relationships: validates: release-process
"""Sole-kill proof; each mutation must fail its named behavioral test."""
import argparse
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
CASES = []


@dataclass(frozen=True)
class Mutation:
    file: str
    old: str
    new: str
    tests: tuple
    suite: str = 'node'


def mutation(file, old, new, test, suite='node', site_tests=None):
    # One expected test per text occurrence, in source order. Repeated text is
    # never silently reduced to its first occurrence.
    CASES.append(Mutation(file, old, new, tuple(site_tests or [test]), suite))


r = 'release/targets/repo-wyrd-foo/prepare.js'
for old, new, test in [
    ("model.release_units[unit]?.release_record !== 'required'", 'false', 'none in the whole selection refuses before submission'),
    ("!['true', 'false'].includes(env.PACKAGE_DRY_RUN)", 'false', 'dry-run choice is required'),
    ('!selected.includes(unit)', 'false', 'matrix is limited to selected units'),
    ("!env.RELEASE_UNITS.split(' ').includes(unit)", 'false', 'unresolved units do not read assets or submit'),
    ('!release', 'false', 'missing release refuses'),
    ('!stable.test(release.version)', 'false', 'only stable bare tags are supported by publisher'),
    ('release.tag !== release.version', 'false', 'only stable bare tags are supported by publisher'),
    ('record.draft !== false', 'false', 'draft record refuses'),
    ('record.tag_name !== release.tag', 'false', 'different record tag refuses'),
    ('manifests.length !== 1', 'false', 'manifest asset must be present exactly once'),
    ("!/^sha256:[0-9a-f]{64}$/.test(asset.digest || '')", 'false', 'missing manifest digest refuses'),
    ("'sha256:' + crypto.createHash('sha256').update(bytes).digest('hex') !== asset.digest", 'false', 'changed manifest download refuses'),
    ('manifest.tag !== release.tag', 'false', 'manifest tag must match source'),
    ('manifest.version !== release.version', 'false', 'manifest version must match source'),
    ('manifest.source?.commit !== env.RELEASE_SOURCE_SHA', 'false', 'manifest commit must match source'),
    ('manifest.source?.repository !== `${context.repo.owner}/${context.repo.repo}`', 'false', 'manifest repository must match source'),
    ("names.get(item.filename) !== 'sha256:' + item.sha256", 'false', 'package artifact must match release-record digest'),
    ("env.PACKAGE_DRY_RUN === 'true'", 'false', 'dry run never enables submission'),
]: mutation(r, old, new, 'test_repo_wyrd_foo.PackageRuntime.test_' + test.replace(' ', '_').replace('-', '_'), 'python')
mutation(r, 'fs.writeFileSync(file, bytes);', 'fs.writeFileSync(file, JSON.stringify(manifest));',
    'test_repo_wyrd_foo.PackagePart.test_emitted_action_manifest_preserves_original_download_bytes', 'python')
mutation('release/targets/repo-wyrd-foo/publish-repo-wyrd-foo.yml',
    "steps.package.outputs.submit == 'true'", "'true'",
    'test_repo_wyrd_foo.PackagePart.test_selected_and_removed', 'python')
mutation('release/targets/repo-wyrd-foo/publish-repo-wyrd-foo.yml',
    "      PACKAGE_MODEL: {{ release_model | tojson | replace('$', '\\\\u0024') | toyaml }}", "      PACKAGE_MODEL: {{ release_model | tojson | replace('unmatched-dollar', '\\\\u0024') | toyaml }}",
    'test_repo_wyrd_foo.PackagePart.test_model_and_answers_are_literal_data', 'python')

mutation('release/template.yml', "      regex: '^[0-9a-f]{40}$'", "      regex: '.*'",
    'test_repo_wyrd_foo.PackagePart.test_pin_answers_required_without_defaults_and_validated', 'python')
mutation('release/template.yml', "      regex: '^(0|[1-9][0-9]*)\\.(0|[1-9][0-9]*)\\.(0|[1-9][0-9]*)(-((0|[1-9][0-9]*|[0-9A-Za-z-]*[A-Za-z-][0-9A-Za-z-]*)(\\.(0|[1-9][0-9]*|[0-9A-Za-z-]*[A-Za-z-][0-9A-Za-z-]*))*))?(\\+[0-9A-Za-z-]+(\\.[0-9A-Za-z-]+)*)?$'", "      regex: '.*'",
    'test_repo_wyrd_foo.PackagePart.test_pin_answers_required_without_defaults_and_validated', 'python')

mutation('release/template.yml', '  - id: repo_wyrd_foo_units\n    type: text\n    prompt: Release Units submitted to repo.wyrd.foo (space separated)\n    when: "\'repo-wyrd-foo\' in targets"\n    required: true\n    validate:\n      regex: \'^[a-z0-9][a-z0-9._-]*( [a-z0-9][a-z0-9._-]*)*$\'', '  - id: repo_wyrd_foo_units\n    type: text\n    prompt: Release Units submitted to repo.wyrd.foo (space separated)\n    when: "\'repo-wyrd-foo\' in targets"\n    required: true\n    validate:\n      regex: \'.*\'',
    'test_repo_wyrd_foo.PackagePart.test_pin_answers_required_without_defaults_and_validated', 'python')
mutation('release/template.yml', '  - id: repo_wyrd_foo_manifest_asset\n    type: text\n    prompt: Package selection manifest release asset name\n    when: "\'repo-wyrd-foo\' in targets"\n    required: true\n    default: release-manifest.json\n    validate:\n      regex: \'^[A-Za-z0-9][A-Za-z0-9._+-]*$\'', '  - id: repo_wyrd_foo_manifest_asset\n    type: text\n    prompt: Package selection manifest release asset name\n    when: "\'repo-wyrd-foo\' in targets"\n    required: true\n    default: release-manifest.json\n    validate:\n      regex: \'.*\'',
    'test_repo_wyrd_foo.PackagePart.test_pin_answers_required_without_defaults_and_validated', 'python')

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
]:
    mutation(r, old, 'false', test, site_tests=([test, 'record retry: no release, no assets'] if old == '!release' else None))
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
]:
    mutation(r, old, new, 'test_template.ReleaseTemplate.' + test, 'python',
             site_tests=['test_template.ReleaseTemplate.' + name for name in (
                 'test_resolve_model_data_stays_literal',
                 'test_preflight_credential_data_stays_literal',
                 'test_record_job_model_data_stays_literal')] if 'replace' in old else None)
mutation('tests/release/harness.py', 'assert job not in jobs_seen,', 'assert True,', 'test_template.ReleaseTemplate.test_duplicate_job_keys_across_parts_fail', 'python')
mutation('tests/release/harness.py', 'assert key not in credentials_seen,', 'assert True,', 'test_template.ReleaseTemplate.test_duplicate_credential_keys_across_parts_fail', 'python')
# Remove the vendored root. The proxy observation, rather than an eventual render
# failure, must be the assertion that kills the attempted hosted fallback.
mutation('release/template.yml', 'schemas: schemas', 'schemas: missing-schemas', 'test_template.VendoredResolution.test_vendored_schema_makes_zero_network_attempts', 'python-network')


# The vendored ecosystem rule must independently cover every known adapter.
for adapter in ('npm', 'cargo', 'pub', 'python', 'msbuild', 'go'):
    mutation('release/schemas/release-model-1.schema.yml',
             '- const: ' + adapter + '\n', '- const: unknown-ecosystem\n',
             'test_release_model_schema.ReleaseModelSchema.test_known_ecosystem_adapters_require_generic_false', 'python')
mutation('release/schemas/release-model-1.schema.yml',
         'generic:\n              const: false', 'generic:\n              const: true',
         'test_release_model_schema.ReleaseModelSchema.test_known_ecosystem_adapters_require_generic_false', 'python')


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
]:
    mutation(r, old, new, 'test_template.InternalSchemas.test_schema_guards', 'python',
             site_tests=['test_template.InternalSchemas.test_step_if_expression_guard',
                         'test_template.InternalSchemas.test_run_expression_guard'] if 'minLength' in old else None)


for old in ['needs.resolve.outputs.source_sha', 'needs.resolve.outputs.releases',
            "join(fromJSON(needs.resolve.outputs.units), ' ')"]:
    mutation('release/partials/jobs.yml', old, 'needs.resolve.outputs.wrong',
             'test_template.ReleaseTemplate.test_part_graph_all_stages_and_units', 'python')
for old in ['inputs.source-sha', 'github.ref', 'github.sha', 'github.event.repository.default_branch']:
    mutation('release/template/.github/workflows/release.yml', old, 'github.wrong',
             'test_template.ReleaseTemplate.test_base_dispatch_outputs_and_pins', 'python')


# Prove the harness's containment check and each refusal independently.
for old, new, test in [
    ("check_condition(entry['if'])", 'pass', 'test_template.ReleaseTemplate.test_unbalanced_condition_rejected_at_rendered_boundary'),
    ('if depth < 0:', 'if False:', 'test_conditions.PartConditions.test_early_close_cannot_escape_parent_group'),
    ('if depth != 0:', 'if False:', 'test_conditions.PartConditions.test_missing_close_is_rejected'),
    ('if quoted:', 'if False:', 'test_conditions.PartConditions.test_unterminated_string_is_rejected'),
    ('elif not quoted:', 'else:', 'test_conditions.PartConditions.test_nested_and_github_quoted_parentheses_are_literal'),
]:
    mutation('tests/release/harness.py', old, new, test, 'python')

# Split self-mutation strings so the catalog itself is not a match site.
for old, new, test in [
    ('assert len(positions)' + ' == len(case.tests),', 'assert True,', 'test_zero_and_unexpected_match_counts_fail_before_running'),
    ("assert sites, " + "'Mutation inventory is empty'", 'pass', 'test_empty_inventory_cannot_pass'),
    ("status=" + "'INCOMPLETE'", "status='PASS'", 'test_interrupted_test_leaves_incomplete_report_and_restores_source'),
    ("all(r['killed']" + " and r['restored']", "any(r['killed'] and r['restored']", 'test_surviving_second_site_fails_the_battery'),
    ('target.write_' + 'bytes(original)', 'pass', 'test_each_repeated_site_is_applied_killed_and_restored'),
    ('assert case.old and ' + 'case.old != case.new,', 'assert True,', 'test_empty_or_unchanged_mutation_is_refused'),
    ('assert applied != source ' + 'and applied == mutated,', 'assert True,', 'test_failed_application_cannot_be_a_kill'),
    ('head == final_head ' + 'and ', '', 'test_changed_head_cannot_pass'),
    ("assert hashlib.sha256(original).hexdigest() == " + "site['original_sha256'],", 'assert True,', 'test_changed_source_cannot_use_stale_inventory'),
]:
    mutation('tests/release/mutations.py', old, new, 'test_mutations.MutationRunner.' + test, 'python')


# crates.io publication guards; each site has its own named behavioral kill.
r = 'release/targets/crates-io/publish.py'
mutation(r, 'if unit not in releases:', 'if False:', 'test_crates_io.CratesPublisher.test_only_selected_released_units', 'python')
mutation(r, "value['release_record'] != 'required'", 'False', 'test_crates_io.CratesPublisher.test_record_none_refused_before_writes', 'python')
mutation(r, 'if not manifests:', 'if False:', 'test_crates_io.CratesPublisher.test_missing_projection_refused', 'python')
mutation(r, "p['adapter'] == 'cargo'", 'True', 'test_crates_io.CratesPublisher.test_projection_adapter_generic_and_mode_selection', 'python')
mutation(r, "not p['generic']", 'True', 'test_crates_io.CratesPublisher.test_projection_adapter_generic_and_mode_selection', 'python')
mutation(r, "p['mode'] != 'none'", 'True', 'test_crates_io.CratesPublisher.test_projection_adapter_generic_and_mode_selection', 'python')
mutation(r, 'len(matches) != 1', 'False', 'test_crates_io.CratesPublisher.test_manifest_identity_refused', 'python')
mutation(r, "package['version'] != releases[unit]['version']", 'False', 'test_crates_io.CratesPublisher.test_version_mismatch_refused', 'python')
mutation(r, "package.get('publish') is not None and 'crates-io' not in package['publish']", 'False', 'test_crates_io.CratesPublisher.test_private_package_refused', 'python')
mutation(r, 'if name in packages:', 'if False:', 'test_crates_io.CratesPublisher.test_duplicate_projection_refused', 'python')
mutation(r, "d.get('kind') != 'dev'", 'True', 'test_crates_io.CratesPublisher.test_multi_crate_unit_dependency_order', 'python')
mutation(r, "d['name'] in pending", 'False', 'test_crates_io.CratesPublisher.test_multi_crate_unit_dependency_order', 'python')
mutation(r, 'error.code == 404', 'True', 'test_crates_io.CratesPublisher.test_api_not_found_and_errors_fail_closed', 'python')
mutation(r, "value['crate'] != name or value['num'] != version", 'False', 'test_crates_io.CratesPublisher.test_api_accepted_submission_checksum_and_identity', 'python')
mutation(r, 'not isinstance(result, str) or len(result) != 64', 'False', 'test_crates_io.CratesPublisher.test_api_accepted_submission_checksum_and_identity', 'python')
mutation(r, 'if observed is not None:', 'if False:', 'test_crates_io.CratesPublisher.test_matching_published_or_submitted_retry', 'python')
mutation(r, 'if observed != expected:', 'if False:', 'test_crates_io.CratesPublisher.test_different_published_artifact_refused', 'python', site_tests=['test_crates_io.CratesPublisher.test_different_published_artifact_refused', 'test_crates_io.CratesPublisher.test_unconfirmed_or_conflicting_submission_refused'])
mutation(r, 'if result.returncode:\n        raise RuntimeError', 'if False:\n        raise RuntimeError', 'test_crates_io.CratesPublisher.test_index_wait_failure_stops_dependents_then_retry_adopts_submission', 'python')
mutation(r, "& set(os.environ['RELEASE_UNITS'].split())", '', 'test_crates_io.CratesPublisher.test_only_selected_released_units', 'python')
mutation(r, "raise ValueError('Cargo publication dependency cycle')", 'return ordered', 'test_crates_io.CratesPublisher.test_dependency_cycle_refused', 'python')
mutation(r, "d.get('path') and ", '', 'test_crates_io.CratesPublisher.test_external_dependency_does_not_constrain_selected_crate', 'python')
mutation(r, "'--registry', 'crates-io', ", '', 'test_crates_io.CratesPublisher.test_publish_and_confirm', 'python',
         site_tests=['test_crates_io.CratesPublisher.test_matching_published_or_submitted_retry',
                     'test_crates_io.CratesPublisher.test_publish_and_confirm'])
mutation(r, "value['crate'] != name or ", '', 'test_crates_io.CratesPublisher.test_api_accepted_submission_checksum_and_identity', 'python')
mutation(r, " or value['num'] != version", '', 'test_crates_io.CratesPublisher.test_api_accepted_submission_checksum_and_identity', 'python')
mutation(r, "not isinstance(result, str) or ", '', 'test_crates_io.CratesPublisher.test_api_accepted_submission_checksum_and_identity', 'python')
mutation(r, " or len(result) != 64", '', 'test_crates_io.CratesPublisher.test_api_accepted_submission_checksum_and_identity', 'python')
mutation(r, "Path(p['manifest_path']).resolve() == Path(manifest).resolve()", 'True', 'test_crates_io.CratesPublisher.test_manifest_path_filter', 'python')
mutation(r, "package.get('publish') is not None and ", '', 'test_crates_io.CratesPublisher.test_registry_allow_list', 'python')
mutation(r, " and 'crates-io' not in package['publish']", '', 'test_crates_io.CratesPublisher.test_registry_allow_list', 'python')
mutation('release/targets/crates-io/publish-crates-io.yml', r" | replace('$', '\\u0024')", '', 'test_crates_io.CratesPart.test_model_dollar_data_stays_literal', 'python')

# Diagnostics and fresh-checkout retry agreements.
mutation(r, 'if result.returncode:\n        sys.stdout', 'if False:\n        sys.stdout',
         'test_crates_io.CratesPart.test_emitted_publish_failure_logs_both_cargo_streams', 'python')
mutation(r, 'sys.stdout.buffer.write(result.stdout)', 'pass',
         'test_crates_io.CratesPart.test_emitted_publish_failure_logs_both_cargo_streams', 'python')
mutation(r, 'sys.stderr.buffer.write(result.stderr)', 'pass',
         'test_crates_io.CratesPart.test_emitted_publish_failure_logs_both_cargo_streams', 'python')
mutation(r, '(HTTP {error.code})', '(HTTP unknown)',
         'test_crates_io.CratesPublisher.test_lookup_authentication_status_without_private_text', 'python')
mutation(r, '(HTTP {error.code})', '(HTTP {error})',
         'test_crates_io.CratesPublisher.test_lookup_rate_limit_status_without_private_text', 'python')
mutation(r, '(HTTP {error.code})', '(HTTP 429)',
         'test_crates_io.CratesPublisher.test_lookup_server_status_without_private_text', 'python')
mutation(r, ') from None', ') from error',
         'test_crates_io.CratesPublisher.test_lookup_authentication_status_without_private_text', 'python')
# Corrupt only the independently packaged second archive so that comparison
# must observe bytes from both fresh checkouts.
mutation('tests/release/test_crates_io.py',
         "digests.append(hashlib.sha256(archive.read_bytes()).hexdigest())",
         "digests.append(hashlib.sha256(archive.read_bytes() + (b'changed' if index == 1 else b'')).hexdigest())",
         'test_crates_io.CratesPublisher.test_real_cargo_fresh_checkouts_reproduce_package_checksum', 'python')

# npm publication guards execute through named tests with real npm pack.
r = 'release/targets/npm/npm.js'
for old, new, test in [
    ("platform !== 'win32'", 'false', 'windows'),
    ("file => fs.existsSync(file)", 'file => true', 'windows'),
    ("if (!cli)", 'if (false)', 'windows'),
    ("'--ignore-scripts'", "'--foreground-scripts'", 'retry'),
    ("'--provenance'", "'--no-provenance'", 'retry'),
    ("fs.rmSync(scratch, {recursive: true, force: true});", 'void scratch;', 'retry'),
    ("!unit || unit.release_record !== 'required'", 'false', 'record'),
    ("p.adapter === 'npm' && p.generic === false", 'true', 'selection'),
    ("!projections.length", 'false', 'projection'),
    ("!released.has(id)", 'false', 'selection'),
    ("!release || !release.version", 'false', 'release'),
    ("!manifest.name || manifest.private === true", 'false', 'manifest'),
    ("projection.native_identity && manifest.name !== projection.native_identity", 'false', 'manifest'),
    ("projection.mode !== 'injected' && manifest.version !== release.version", 'false', 'manifest'),
    ("packages.some(p => p.name === manifest.name)", 'false', 'duplicate'),
    ("env.NPM_DRY_RUN !== 'true' && env.NPM_DRY_RUN !== 'false'", 'false', 'dry_choice'),
    ("if (dry) continue;", 'if (false) continue;', 'dry'),
    ("installed.name !== pkg.name || installed.version !== pkg.version", 'false', 'client'),
    ("manifest.version = pkg.version;", "manifest.version = '0.0.0';", 'injected'),
    ("packed.length !== 1 || packed[0].name !== pkg.name || packed[0].version !== pkg.version", 'false', 'pack'),
    ("if (pkg.prerelease) args.push('--tag=next');", "args.push(pkg.prerelease ? '--tag=next' : '--tag=latest');", 'stable_tag'),
    ("if (pkg.prerelease) args.push('--tag=next');", "void pkg.prerelease;", 'prerelease_tag'),
    ("if (dry) {", 'if (false) {', 'dry'),
    ("[...args, '--dry-run']", '[...args]', 'dry'),
    ("response.status === 404", 'false', 'retry'),
    ("response.status !== 200", 'false', 'registry'),
    ("remote.name !== pkg.name || remote.version !== pkg.version || remote.dist?.integrity !== integrity", 'false', 'registry'),
    ("remote.dist?.integrity !== integrity", 'false', 'conflict'),
    ("if (await lookup()) continue;", 'if (false) continue;', 'retry'),
    ("if (await lookup()) continue; throw error;", 'if (false) continue; throw error;', 'race'),
    ("if (!await lookup())", 'if (false)', 'readback'),
]:
    mutation(r, old, new, 'test_npm.NpmRuntime.test_' + test, 'python',
             site_tests=([
                 'test_npm.NpmRuntime.test_retry', 'test_npm.NpmRuntime.test_retry']
                 if old == "'--ignore-scripts'" else [
                 'test_npm.NpmRuntime.test_retry', 'test_npm.NpmRuntime.test_race']
                 if old == 'if (await lookup()) continue;' else None))
for name, test in [('publish-npm', 'publish'), ('verify-npm-clients', 'verify')]:
    mutation('release/targets/npm/' + name + '.env.yml',
             " | replace('$', '\\\\u0024')", '',
             'test_npm.NpmPart.test_' + test + '_model_data_stays_literal', 'python')


# GHCR publication: each guard is killed by a named transport-boundary test.
r = 'release/targets/ghcr/publish.py'
for old, new, test in [
    ("env.get('GHCR_DRY_RUN') not in ('true', 'false')", 'False', 'missing_dry_choice_before_any_tool'),
    ("env['GHCR_DRY_RUN'] == 'true'", 'False', 'dry_run_no_login_or_registry'),
    ("not unit or unit['release_record'] != 'required'", 'not unit', 'record_none_before_any_tool'),
    ("not (context / 'Dockerfile').is_file()", 'False', 'missing_dockerfile_before_any_tool'),
    ("unit_id not in released", 'False', 'selected_and_released_units_only'),
    ("if not re.fullmatch(", "if False and not re.fullmatch(", 'unsupported_oci_tag_before_any_tool'),
    ("len(version) > 128", 'False', 'unsupported_oci_tag_before_any_tool'),
    ("run(['git', 'rev-parse', 'HEAD'], cwd=root).decode().strip() != source", 'False', 'source_checkout_mismatch'),
    ("context = root / unit['path']", "context = root", 'released_unit_context_and_prerelease_version'),
    ("if dry:", 'if False:', 'dry_run_no_login_or_registry'),
    ("'--password-stdin'", "'--password'", 'fresh_and_matching_retry'),
    ("'org.opencontainers.image.revision=' + source", "'org.opencontainers.image.revision=missing'", 'fresh_and_matching_retry'),
    ("'org.opencontainers.image.version=' + version", "'org.opencontainers.image.version=missing'", 'fresh_and_matching_retry'),
    ("'SOURCE_DATE_EPOCH=' + epoch", "'SOURCE_DATE_EPOCH=0'", 'fresh_and_matching_retry'),
    ("',rewrite-timestamp=true'", "''", 'fresh_and_matching_retry'),
    ("'--provenance=false'", "'--provenance=true'", 'fresh_and_matching_retry'),
    ("'--sbom=false'", "'--sbom=true'", 'fresh_and_matching_retry'),
    ("'--platform', 'linux/amd64,linux/arm64'", "'--platform', 'linux/amd64'", 'fresh_and_matching_retry'),
    ("'org.opencontainers.image.source=https://github.com/' + env['GITHUB_REPOSITORY']", "'org.opencontainers.image.source=missing'", 'fresh_and_matching_retry'),
    ("missing and b'manifest unknown' in (error.stderr or b'').lower()", 'missing', 'registry_errors_fail_closed'),
    ("hashlib.sha256(remote).hexdigest() != digest.removeprefix('sha256:')", 'False', 'conflict_before_push'),
    ("                lookup(immutable)\n", '', 'digest_readback_before_tag'),
    ("                if lookup(tag, missing=True):\n                    continue\n", '                if False:\n                    continue\n', 'fresh_and_matching_retry'),
    ("if not lookup(tag, missing=True):", 'if False:', 'concurrent_tag_write_readback'),
    ("'--all', '--preserve-digests'", "'--all'", 'fresh_and_matching_retry'),
    ("run(['docker', 'buildx', 'rm', builder])", 'pass', 'fresh_and_matching_retry'),
]:
    mutation(r, old, new, 'test_ghcr.GhcrPublisher.test_' + test, 'python',
             site_tests=(['test_ghcr.GhcrPublisher.test_fresh_and_matching_retry',
                          'test_ghcr.GhcrPublisher.test_concurrent_conflicting_tag_after_digest']
                         if old.startswith('                if lookup(tag') else
                         ['test_ghcr.GhcrPublisher.test_partial_digest_retry',
                          'test_ghcr.GhcrPublisher.test_concurrent_tag_write_readback']
                         if old == 'if not lookup(tag, missing=True):' else
                         ['test_ghcr.GhcrPublisher.test_' + test] * (2 if old in (
                 "                if lookup(tag, missing=True):\n                    continue\n",
                 "if not lookup(tag, missing=True):", "'--all', '--preserve-digests'") else 1)))
mutation('release/targets/ghcr/publish-ghcr.env.yml',
         " | replace('$', '\\\\u0024')", '',
         'test_ghcr.GhcrPart.test_model_data_stays_literal', 'python')



# Homebrew: one named assertion boundary for each independently mutable guard.
h = 'release/targets/homebrew-tap/homebrew.js'
for old, new, test in [
    ("if (a[i] !== b[i])", "if (false)", 'ordering'),
    ("if (leftPre === undefined || rightPre === undefined)", "if (false)", 'ordering'),
    ("if (i === x.length || i === y.length)", "if (false)", 'ordering'),
    ("if (xn && yn)", "if (false)", 'ordering'),
    ("a[i] > b[i] ? 1 : -1", "a[i] > b[i] ? -1 : 1", 'ordering'),
    ("leftPre === rightPre ? 0 : leftPre === undefined ? 1 : -1", "0", 'ordering'),
    ("i === x.length ? -1 : 1", "i === x.length ? 1 : -1", 'ordering'),
    ("if (x[i] === y[i]) continue;", "if (false) continue;", 'ordering'),
    ("BigInt(x[i]) > BigInt(y[i]) ? 1 : -1", "BigInt(x[i]) > BigInt(y[i]) ? -1 : 1", 'ordering'),
    ("if (xn !== yn)", "if (false)", 'ordering'),
    ("x[i] > y[i] ? 1 : -1", "x[i] > y[i] ? -1 : 1", 'ordering'),
    ("for (const plan of plans)", "for (const plan of plans.slice(0, 1))", 'planning'),
    ("[...bytes.toString('utf8').matchAll(/^\\s*version\\b/gm)].length !== 1", "false", 'semver'),
    ("urls.length !== [...bytes.toString('utf8').matchAll(/^\\s*url\\b/gm)].length", "false", 'urls'),
    ("versions.length !== 1", "false", 'semver'),
    ("!HOMEBREW_SEMVER.test(versions[0][1])", "false", 'semver'),
    ("!model.release_units[id]", "false", 'record'),
    ("model.release_units[id].release_record !== 'required'", "false", 'record'),
    ("if (!released.has(id)) continue;", "if (false) continue;", 'selection'),
    ("!release ||", "false ||", 'release'),
    ("!HOMEBREW_SEMVER.test(release.version)", "false", 'release'),
    ("!release.tag", "false", 'release'),
    ("env.HOMEBREW_DRY_RUN !== 'true' && env.HOMEBREW_DRY_RUN !== 'false'", "false", 'dry_choice'),
    ("!/^[A-Za-z0-9_.-]+\\/[A-Za-z0-9_.-]+$/.test(env.HOMEBREW_TAP_REPOSITORY)", "false", 'destination'),
    ("(owner + '/' + name).toLowerCase() === (repo.owner + '/' + repo.repo).toLowerCase()", "false", 'destination'),
    ("if (!branch)", "if (false)", 'branch'),
    ("if (error.status === 404) return null;", "if (true) return null;", 'read_errors'),
    ("data.type !== 'file'", "false", 'content'),
    ("data.encoding !== 'base64'", "false", 'content'),
    ("!data.sha", "false", 'content'),
    ("typeof data.content !== 'string'", "false", 'content'),
    ("matches.length !== 1", "false", 'assets'),
    ("record.draft", "false", 'source'),
    ("record.tag_name !== release.tag", "false", 'source'),
    ("record.prerelease !== release.prerelease", "false", 'source'),
    ("expected.length !== 1", "false", 'checksum'),
    ("expected[0] !== digest + '  ' + id + '.rb'", "false", 'checksum'),
    ("formulaVersion(bytes) !== release.version", "false", 'version'),
    ("!bytes.length || !bytes.toString('utf8').endsWith('\\n')", "false", 'newline'),
    ("!urls.length", "false", 'urls'),
    ("urls.some(match => !match[1].startsWith(prefix))", "false", 'urls'),
    ("prior && formulaVersion(prior.bytes) === release.version && !prior.bytes.equals(bytes)", "false", 'conflict'),
    ("prior && compareVersions(formulaVersion(prior.bytes), release.version) > 0", "false", 'downgrade'),
    ("if (prior?.bytes.equals(bytes))", "if (false)", 'retry'),
    ("if (dry)", "if (false)", 'dry'),
    ("...(prior ? {sha: prior.sha} : {})", "...{}", 'retry'),
    ("if (observed?.bytes.equals(bytes)) continue;", "if (false) continue;", 'race'),
    ("if (!observed?.bytes.equals(bytes))", "if (false)", 'readback'),
]:
    mutation(h, old, new, 'test_homebrew_tap.HomebrewRuntime.test_' + test, 'python')
mutation('release/targets/homebrew-tap/publish-homebrew-tap.env.yml',
         " | replace('$', '\\\\u0024')", '',
         'test_homebrew_tap.HomebrewPart.test_model_data_stays_literal', 'python')

# Each invocation must retain the diagnostic wrapper, including retry translation.
for old, tests in [
    ("run(['docker', 'buildx', 'create'", ['builder_failure_diagnostics']),
    ("run(['docker', 'buildx', 'build'", ['build_failure_diagnostics']),
    ("run(['docker', 'buildx', 'rm'", ['cleanup_failure_diagnostics']),
    ("run(['skopeo', 'login'", ['login_failure_diagnostics']),
    ("run(['skopeo', 'inspect', '--raw'", ['local_inspect_failure_diagnostics']),
    ("run(['skopeo', 'inspect', '--authfile'", ['remote_inspect_failure_diagnostics']),
    ("run(['skopeo', 'copy'", ['copy_failure_diagnostics', 'tag_copy_failure_diagnostics']),
]:
    mutation('release/targets/ghcr/publish.py', old, old.replace('run(', 'tool(', 1),
             '', 'python', site_tests=['test_ghcr.GhcrPublisher.test_' + test for test in tests])

# GHCR robustness: captured output, credential redaction and real-build agreements.
mutation('release/targets/ghcr/publish.py',
         "(error.stdout, sys.stdout)", "(b'', sys.stdout)",
         'test_ghcr.GhcrPublisher.test_build_failure_diagnostics', 'python')
mutation('release/targets/ghcr/publish.py',
         "(error.stderr, sys.stderr)", "(b'', sys.stderr)",
         'test_ghcr.GhcrPublisher.test_remote_inspect_failure_diagnostics', 'python')
mutation('release/targets/ghcr/publish.py',
         "text = text.replace(token, '[REDACTED]')", 'pass',
         'test_ghcr.GhcrPublisher.test_login_failure_diagnostics', 'python')
mutation('release/targets/ghcr/publish.py',
         "missing and b'manifest unknown' in (error.stderr or b'').lower()", 'missing',
         'test_ghcr.GhcrPublisher.test_ghcr_unmeasured_denials_fail_closed', 'python')
# The real wrapped diagnostic must be accepted, while same-shape denials fail closed.
mutation('release/targets/ghcr/publish.py',
         "missing and b'manifest unknown' in (error.stderr or b'').lower()",
         "missing and (error.stderr or b'').lower() == b'manifest unknown'",
         'test_ghcr.GhcrPublisher.test_captured_authenticated_ghcr_first_lookup', 'python')
for test in ('unauthorized', 'denied'):
    mutation('release/targets/ghcr/publish.py',
             "missing and b'manifest unknown' in (error.stderr or b'').lower()", 'missing',
             'test_ghcr.GhcrPublisher.test_captured_authenticated_ghcr_' + test + '_near_miss', 'python')
for old, new in [
    ('`FROM --platform=$BUILDPLATFORM` stages', '`FROM` stages'),
    ('without GHCR lookup, login or push', 'without GHCR push'),
    ('BuildKit and base-image registry pulls may', 'BuildKit and base-image registry pulls never'),
]:
    mutation('release/targets/README.md', old, new,
             'test_ghcr.GhcrCompilationProof.test_documented_build_and_dry_run_contract', 'python')
for old, new, test in [
    ('FROM --platform=$BUILDPLATFORM ', 'FROM ', 'native_build_stage_contract'),
    ('GOOS=$TARGETOS GOARCH=$TARGETARCH go build', 'GOOS=linux GOARCH=amd64 go build', 'native_build_stage_contract'),
    ('COPY --from=build /out/sample-tool /sample-tool', 'COPY main.go /sample-tool', 'native_build_stage_contract'),
    ("binary[:6] == b'\\x7fELF\\x02\\x01'", 'True', 'binary_machine_refuses_mislabeled_output'),
    ("machine == {'amd64': 62, 'arm64': 183}[arch]", 'True', 'binary_machine_refuses_mislabeled_output'),
]:
    mutation('tests/release/ghcr_oci_proof.py', old, new,
             'test_ghcr.GhcrCompilationProof.test_' + test, 'python')

mutation('tests/release/ghcr_oci_proof.py',
         "member.name.lstrip('./') == 'sample-tool'", 'True',
         'test_ghcr.GhcrCompilationProof.test_compiled_binary_readback_and_absence', 'python')
mutation('tests/release/ghcr_oci_proof.py',
         "raise AssertionError('Compiled executable absent from image')", "return b''",
         'test_ghcr.GhcrCompilationProof.test_compiled_binary_readback_and_absence', 'python')



# Optional product verification: named, independently falsifiable boundaries.
mutation('release/partials/jobs.yml',
         "(ns.publish if stage == 'verify' else [])", '[]',
         'test_product_verification.ProductVerification.test_all_publish_dependencies', 'python')
mutation('release/partials/jobs.yml',
         " && needs.release-record.result == 'success')", ")",
         'test_product_verification.ProductVerification.test_stage_truth_table_and_required_record', 'python')
mutation('release/targets/product-build/verify-product.jinja',
         " && (!contains(fromJSON(needs.resolve.outputs.record_units), matrix.unit) || needs.release-record.result == 'success')", '',
         'test_product_verification.ProductVerification.test_mixed_units_required_record_step_gate', 'python')
mutation('tests/release/harness.py',
         "assert body['permissions'] == {'contents': 'read'}, 'Product recipes request contents: read only'", 'pass',
         'test_product_verification.ProductVerification.test_rejects_extra_permission', 'python')
mutation('tests/release/harness.py',
         "assert data['credentials'] == {}, 'Product recipes declare no credentials'", 'pass',
         'test_product_verification.ProductVerification.test_rejects_declared_credential', 'python')
mutation('tests/release/harness.py',
         "assert 'secrets.' not in json.dumps(body), 'Product verification receives no user secrets'", 'pass',
         'test_product_verification.ProductVerification.test_rejects_user_secret_and_protected_environment', 'python')
mutation('tests/release/harness.py',
         "assert 'environment' not in body, 'Product verification declares no protected environment'", 'pass',
         'test_product_verification.ProductVerification.test_rejects_user_secret_and_protected_environment', 'python')
for member in ('tag', 'version'):
    mutation('release/targets/product-build/verify-product.jinja',
             'fromJSON(env.RELEASE_JSON)[matrix.unit].' + member, 'github.ref_name',
             'test_product_verification.ProductVerification.test_recipe_receives_resolved_identity', 'python')

# Optional parent-owned final major tag: seven obligations, split at independent assertions.
MAJOR_TAG_CASE_START = len(CASES)
mutation('release/partials/major-tag.yml', ' + ns.verify) | tojson', ') | tojson',
         'test_major_tag.MajorTag.test_needs_every_earlier_job', 'python')
mutation('release/partials/major-tag.yml', " && needs.{{ verify }}.result == 'success'", '',
         'test_major_tag.MajorTag.test_named_unit_verification_truth_table', 'python')
for old, test in [
    ("assert parsed['permissions'] == {}, 'Major tag keeps workflow permissions empty'", 'test_rejects_workflow_write'),
    ("assert body.get('permissions', {}).get('contents') != 'write', 'Major tag adds no other contents writer'", 'test_rejects_other_job_write'),
    ("assert set(job['env']) == {'RELEASE_SOURCE_SHA', 'RELEASE_JSON', 'MAJOR_TAG_UNIT'}, 'Major tag declares no credential'", 'test_rejects_credential'),
    ("assert 'secrets.' not in json.dumps(job), 'Major tag receives no user secret'", 'test_rejects_secret'),
]:
    mutation('tests/release/harness.py', old, 'pass', 'test_major_tag.MajorTag.' + test, 'python')
# The environment boundary has both an explicit refusal and a fixed-key check.
# Admit that one property through both layers for a realistic semantic revert.
mutation('tests/release/harness.py',
         "assert 'environment' not in job, 'Major tag declares no environment'\n    assert 'secrets.' not in json.dumps(job), 'Major tag receives no user secret'\n    assert set(job) == {'needs', 'if', 'runs-on', 'permissions', 'env', 'steps'}, 'Major tag has a fixed parent job surface'",
         "assert 'secrets.' not in json.dumps(job), 'Major tag receives no user secret'\n    assert set(job) - {'environment'} == {'needs', 'if', 'runs-on', 'permissions', 'env', 'steps'}, 'Major tag has a fixed parent job surface'",
         'test_major_tag.MajorTag.test_rejects_environment', 'python')
mutation('release/partials/major-tag.js', 'const {version, tag} = release;',
         'const {tag} = release; const version = process.env.GITHUB_REF_NAME;',
         'test_major_tag.MajorTag.test_local_tag_semantics', 'python')
mutation('release/partials/major-tag.js', 'if (BigInt(major) === 0n)', 'if (false)',
         'test_major_tag.MajorTag.test_local_tag_semantics', 'python')
mutation('release/partials/major-tag.js', 'if (!/^[0-9]+\\.[0-9]+\\.[0-9]+$/.test(version))', 'if (false)',
         'test_major_tag.MajorTag.test_local_tag_semantics', 'python')
MAJOR_TAG_CASE_END = len(CASES)


def inventory(root=ROOT, cases=None):
    """Freeze every expected site before any source write or test invocation."""
    sites = []
    for index, case in enumerate(CASES if cases is None else cases, 1):
        original = (root / case.file).read_bytes()
        source = original.decode()
        positions = [match.start() for match in re.finditer(re.escape(case.old), source)]
        assert case.old and case.old != case.new, f'Mutation {index}: empty or unchanged mutation'
        assert len(positions) == len(case.tests), (
            f'Mutation {index}: expected {len(case.tests)} matches, observed {len(positions)}: {case.file}')
        for occurrence, (offset, test) in enumerate(zip(positions, case.tests), 1):
            sites.append(dict(index=index, site=f'{index}:{occurrence}', file=case.file,
                occurrence=occurrence, line=source.count('\n', 0, offset) + 1, offset=offset,
                expected_matches=len(case.tests), observed_matches=len(positions),
                expected_applications=1, old=case.old, new=case.new, test=test, suite=case.suite,
                original_sha256=hashlib.sha256(original).hexdigest()))
    assert sites, 'Mutation inventory is empty'
    return sites


def run(report, root=ROOT, cases=None):
    results = []
    head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, text=True).strip()
    payload = dict(status='INCOMPLETE', head=head, inventory=[], results=results)

    def save():
        report.write_text(json.dumps(payload, indent=2) + '\n')

    save()
    sites = inventory(root, cases)
    payload['inventory'] = sites
    save()
    for site in sites:
        target = root / site['file']
        original = target.read_bytes()
        assert hashlib.sha256(original).hexdigest() == site['original_sha256'], 'Source changed since inventory'
        source = original.decode()
        offset = site['offset']
        old, new, test, suite = (site[key] for key in ('old', 'new', 'test', 'suite'))
        mutated = source[:offset] + new + source[offset + len(old):]
        record = dict(site, applications=0, killed=False, restored=False, exit_code=None, output='')
        results.append(record)
        save()
        empty = None
        command = (['node', '--test', '--test-name-pattern', '^' + re.escape(test) + '$', 'tests/release/runtime.test.cjs']
                   if suite == 'node' else [sys.executable, '-m', 'unittest', '-v', test])
        env = dict(os.environ, PYTHONPATH=str(root / 'tests/release'))
        try:
            if suite == 'python-network':
                empty = root / 'release/missing-schemas'
                empty.mkdir(exist_ok=False)
            target.write_text(mutated)
            # Verify the exact single splice, including the untouched prefix and
            # suffix, rather than assuming replace() applied the intended edit.
            applied = target.read_text()
            assert applied != source and applied == mutated, 'Mutation application failed'
            record['applications'] = 1
            record['mutated_sha256'] = hashlib.sha256(target.read_bytes()).hexdigest()
            save()
            result = subprocess.run(command, cwd=root, env=env, text=True, capture_output=True)
            output = result.stdout + result.stderr
            if suite == 'node':
                killed = result.returncode != 0 and ('✖ ' + test in output or 'not ok 1 - ' + test in output)
            else:
                killed = result.returncode != 0 and ('FAIL: ' + test.rsplit('.', 1)[1] in output)
            if suite == 'python-network':
                killed = killed and 'Vendored resolution attempted network access' in output
            record.update(exit_code=result.returncode, killed=killed, output=output)
            print(f"{site['site']} ({site['file']}:{site['line']}) {'KILLED' if killed else 'SURVIVED'} {test}", flush=True)
        finally:
            target.write_bytes(original)
            record['restored'] = target.read_bytes() == original
            record['restored_sha256'] = hashlib.sha256(target.read_bytes()).hexdigest()
            save()
            assert record['restored'], 'Mutation restoration failed'
            if empty is not None and empty.exists():
                empty.rmdir()
    final_head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, text=True).strip()
    passed = (head == final_head and len(results) == len(sites)
              and {r['site'] for r in results} == {s['site'] for s in sites}
              and all(r['killed'] and r['restored'] and r['applications'] == r['expected_applications'] for r in results))
    payload.update(status='PASS' if passed else 'FAIL', final_head=final_head)
    save()
    if not passed:
        raise SystemExit('Mutation proof FAILED; inspect ' + str(report))
    print(f'PASS {len(results)}/{len(sites)} sole-kills across {len(CASES if cases is None else cases)} rows; report {report}')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    args.report.parent.mkdir(parents=True, exist_ok=True)
    run(args.report)
