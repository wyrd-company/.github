// relationships: validates: release-process
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const crypto = require('node:crypto');
const {execFileSync} = require('node:child_process');
const root = path.resolve(__dirname, '../..');
function functions(file, stop, names) {
  const source = fs.readFileSync(path.join(root, 'templates/release/partials', file), 'utf8').split(stop)[0];
  return new Function(source + '\nreturn {' + names + '};')();
}
const {resolveRelease} = functions('resolve.js', '\nawait resolveRelease', 'resolveRelease');
const {releaseNotes, collectAssets, writeReleaseRecord} = functions('release-record.js', '\nconst fs = require', 'releaseNotes, collectAssets, writeReleaseRecord');
const {checkCredentials} = functions('preflight.js', '\ncheckCredentials(', 'checkCredentials');

function resolution(t, records = [{name: 'sample-tool-1.2.3', unit: 'sample-tool', version: '1.2.3', baseline: 'false'}]) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'release-resolve-'));
  t.after(() => fs.rmSync(dir, {recursive: true, force: true}));
  const git = args => execFileSync('git', args, {cwd: dir, encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe']});
  git(['init', '-b', 'main']);
  git(['-c', 'user.name=Example', '-c', 'user.email=example@example.test', 'commit', '--allow-empty', '-m', 'Source']);
  const sha = git(['rev-parse', 'HEAD']).trim();
  git(['remote', 'add', 'origin', dir]);
  for (const record of records) {
    const message = record.message ?? `intentional release record\n\ntag-id: release-unit/${record.unit}/primary\nversion: ${record.version}\n${record.baseline === undefined ? '' : `baseline: ${record.baseline}\n`}`;
    git(['-c', 'user.name=Example', '-c', 'user.email=example@example.test', 'tag', '-a', record.name, '-m', message]);
  }
  const model = {release_units: {
    'sample-tool': {release_record: 'required', tags: {primary: {role: 'primary', template: '{id}-{version}'}}},
    'sample-docs': {release_record: 'none', tags: {primary: {role: 'primary', template: '{id}-{version}'}}},
  }};
  const env = {SOURCE_SHA: sha, DISPATCH_SHA: sha, DISPATCH_REF: 'refs/heads/main', DEFAULT_BRANCH: 'main', CI_WORKFLOW: 'ci.yml', RELEASE_MODEL: JSON.stringify(model)};
  const output = {}, summary = [];
  const runs = [{head_sha: sha, event: 'push', conclusion: 'success'}];
  const calls = [];
  const github = {rest: {actions: {listWorkflowRuns: 'runs'}}, paginate: async (route, args) => {calls.push([route, args]); return runs;}};
  const core = {setOutput: (key, value) => {output[key] = value;}, summary: {addRaw: text => {summary.push(text); return {write: async () => {}};}}};
  return {github, context: {repo: {owner: 'example-org', repo: 'sample-tool'}}, core, env, git, model, output, summary, runs, calls};
}

test('resolve real annotated primary, baseline, record-none, prerelease and unrelated tags', async t => {
  const f = resolution(t, [
    {name: 'sample-tool-1.2.3-beta.1+build.4', unit: 'sample-tool', version: '1.2.3-beta.1+build.4', baseline: 'false'},
    {name: 'sample-docs-9.8.7', unit: 'sample-docs', version: '9.8.7', baseline: 'false'},
    {name: 'arbitrary-baseline', unit: 'sample-tool', version: '8.9.0', baseline: 'true'},
    {name: 'other-annotation', message: 'not intentional'},
    {name: 'workspace-tag', message: 'intentional release record\n\ntag-id: workspace/all\nversion: 1.2.3\nbaseline: false\n'},
  ]);
  f.git(['tag', 'lightweight']);
  const tree = f.git(['rev-parse', 'HEAD^{tree}']).trim();
  f.git(['-c', 'user.name=Example', '-c', 'user.email=example@example.test', 'tag', '-a', 'tree-annotation', tree, '-m', 'not intentional']);
  await resolveRelease(f);
  assert.deepEqual(Object.keys(f.output).sort(), ['record', 'record_units', 'release', 'releases', 'source_sha', 'units']);
  assert.equal(f.output.release, 'true');
  assert.equal(f.output.source_sha, f.env.SOURCE_SHA);
  assert.equal(f.output.record, 'true');
  assert.deepEqual(JSON.parse(f.output.record_units), ['sample-tool']);
  assert.equal(JSON.parse(f.output.releases)['sample-tool'].prerelease, true);
  assert.equal(JSON.parse(f.output.releases)['sample-docs'].prerelease, false);
  assert.match(f.summary[0], /arbitrary-baseline/);
  assert.deepEqual(f.calls[0][1], {...f.context.repo, workflow_id: 'ci.yml', head_sha: f.env.SOURCE_SHA, event: 'push', status: 'success', per_page: 100});
});

test('baseline-only dispatch fails even at a nonzero version', async t => {
  const f = resolution(t, [{name: '8.9.0', unit: 'sample-tool', version: '8.9.0', baseline: 'true'}]);
  await assert.rejects(resolveRelease(f), /No primary release records.*8\.9\.0/);
  assert.deepEqual(f.output, {});
});

test('record-none-only dispatch emits false and an empty record matrix', async t => {
  const f = resolution(t, [{name: 'sample-docs-1.2.3', unit: 'sample-docs', version: '1.2.3', baseline: 'false'}]);
  await resolveRelease(f);
  assert.equal(f.output.record, 'false');
  assert.equal(f.output.record_units, '[]');
});

for (const [label, key, value, error] of [
  ['source-sha spelling', 'SOURCE_SHA', 'A'.repeat(40), /40 lowercase/],
  ['default branch dispatch', 'DISPATCH_REF', 'refs/heads/other', /default branch/],
  ['dispatch source identity', 'DISPATCH_SHA', 'a'.repeat(40), /Dispatch SHA/],
]) test(`resolve guard: ${label}`, async t => {
  const f = resolution(t); f.env[key] = value;
  await assert.rejects(resolveRelease(f), error); assert.deepEqual(f.output, {});
});

test('resolve guard: remote default branch source identity', async t => {
  const f = resolution(t), git = f.git;
  f.git = args => args[0] === 'ls-remote' ? 'a'.repeat(40) + '\trefs/heads/main\n' : git(args);
  await assert.rejects(resolveRelease(f), /Default branch head/);
});
for (const [label, change] of [
  ['no CI run', f => f.runs.length = 0],
  ['wrong CI commit', f => f.runs[0].head_sha = 'a'.repeat(40)],
  ['wrong CI event', f => f.runs[0].event = 'pull_request'],
  ['unsuccessful CI run', f => f.runs[0].conclusion = 'failure'],
]) test(`resolve guard: ${label}`, async t => {
  const f = resolution(t); change(f);
  await assert.rejects(resolveRelease(f), /No successful push CI/);
});

test('resolve guard: primary after-publication is refused', async t => {
  const f = resolution(t); f.model.release_units['sample-tool'].tags.primary.require_phase = 'after-publication';
  f.env.RELEASE_MODEL = JSON.stringify(f.model);
  await assert.rejects(resolveRelease(f), /sample-tool\/primary.*after-publication/);
});
for (const [label, record, error] of [
  ['missing baseline', {name: 'sample-tool-1.2.3'}, /baseline must/],
  ['invalid baseline', {name: 'sample-tool-1.2.3', baseline: 'FALSE'}, /baseline must/],
  ['primary name mismatch', {name: 'different-tag', baseline: 'false'}, /primary tag must/],
  ['invalid version', {name: 'sample-tool-invalid', version: 'invalid', baseline: 'false'}, /invalid version/],
  ['duplicate record field', {name: 'sample-tool-1.2.3', message: 'intentional release record\n\ntag-id: release-unit/sample-tool/primary\nversion: 1.2.3\nbaseline: false\nbaseline: false\n'}, /duplicate baseline/],
]) test(`resolve guard: ${label}`, async t => {
  const f = resolution(t, [{unit: 'sample-tool', version: '1.2.3', ...record}]);
  await assert.rejects(resolveRelease(f), error);
});

test('resolve guard: tag object is fetched even when checkout lacked it', async t => {
  const f = resolution(t), git = f.git; let fetched = false;
  f.git = args => {
    if (args[0] === 'fetch') {assert.deepEqual(args, ['fetch', '--force', 'origin', 'refs/tags/*:refs/tags/*']); fetched = true;}
    if (args[0] === 'for-each-ref') assert.equal(fetched, true);
    return git(args);
  };
  await resolveRelease(f);
});

test('preflight reports missing names and purposes without credential values', () => {
  const credentials = [{name: 'SAMPLE_TOKEN', purpose: 'Publish sample'}, {name: 'SAMPLE_CHANNEL', purpose: 'Select channel'}];
  assert.throws(() => checkCredentials(credentials, {SAMPLE_CHANNEL: 'private-value'}), error => {
    assert.match(error.message, /SAMPLE_TOKEN: Publish sample/);
    assert.doesNotMatch(error.message, /private-value/); return true;
  });
  checkCredentials(credentials, {SAMPLE_TOKEN: 'present', SAMPLE_CHANNEL: 'present'});
  checkCredentials([], {});
});

test('release notes exact heading, nonempty content and next section boundary', () => {
  assert.equal(releaseNotes('# Notes\n## 1.2.3\n\nChanges\n\n## 1.2.2\nOld', '1.2.3'), 'Changes');
  assert.throws(() => releaseNotes('## 1.2.30\nChanges', '1.2.3'), /Missing changelog/);
  assert.throws(() => releaseNotes('## 1.2.3\n\n## 1.2.2\nOld', '1.2.3'), /Empty changelog/);
});

test('release asset collection hashes files, rejects duplicate and reserved names', t => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'release-assets-')); t.after(() => fs.rmSync(dir, {recursive: true, force: true}));
  assert.equal(collectAssets(dir, fs, path, crypto).size, 0);
  fs.mkdirSync(path.join(dir, 'first')); fs.writeFileSync(path.join(dir, 'first', 'sample.tar'), 'sample');
  const files = collectAssets(dir, fs, path, crypto);
  assert.deepEqual([...files.keys()], ['sample.tar', 'SHA256SUMS']);
  assert.equal(files.get('SHA256SUMS').toString(), crypto.createHash('sha256').update('sample').digest('hex') + '  sample.tar\n');
  fs.mkdirSync(path.join(dir, 'second')); fs.writeFileSync(path.join(dir, 'second', 'sample.tar'), 'duplicate');
  assert.throws(() => collectAssets(dir, fs, path, crypto), /Duplicate or reserved/);
  fs.rmSync(path.join(dir, 'second'), {recursive: true}); fs.writeFileSync(path.join(dir, 'SHA256SUMS'), 'reserved');
  assert.throws(() => collectAssets(dir, fs, path, crypto), /Duplicate or reserved/);
});

function recordFixture(withAssets, initial = [], prerelease = false) {
  const files = withAssets ? new Map([['sample.tar', Buffer.from('sample')], ['SHA256SUMS', Buffer.from('hash')]]) : new Map();
  const records = structuredClone(initial), calls = [];
  const context = {repo: {owner: 'example-org', repo: 'sample-tool'}};
  const env = {RELEASE_JSON: JSON.stringify({'sample-tool': {tag: '1.2.3', version: '1.2.3', prerelease}}), RELEASE_UNIT: 'sample-tool', LATEST_UNIT: 'sample-tool'};
  const response = item => ({data: structuredClone(item)});
  const repos = {};
  for (const name of ['listReleases', 'listReleaseAssets']) repos[name] = name;
  repos.createRelease = async params => {calls.push(['create', params]); const item = {...params, id: 10, assets: []}; records.push(item); return response(item);};
  repos.updateRelease = async params => {calls.push(['update', params]); Object.assign(records.find(r => r.id === params.release_id), params); return response(records.find(r => r.id === params.release_id));};
  repos.deleteReleaseAsset = async params => {calls.push(['delete', params]); for (const r of records) r.assets = r.assets.filter(a => a.id !== params.asset_id);};
  repos.uploadReleaseAsset = async params => {calls.push(['upload', params]); records.find(r => r.id === params.release_id).assets.push({id: 100 + calls.length, name: params.name});};
  repos.getReleaseByTag = async params => {calls.push(['read', params]); const r = records.find(r => r.tag_name === params.tag && !r.draft); if (!r) throw new Error('not found'); return response(r);};
  const github = {rest: {repos}, paginate: async (route, params) => route === 'listReleases' ? structuredClone(records) : structuredClone(records.find(r => r.id === params.release_id).assets)};
  return {github, context, env, files, notes: 'Sample changes', records, calls};
}
function release(draft, names = [], prerelease = false, id = 1) {
  return {id, tag_name: '1.2.3', name: 'old title', body: 'old notes', draft, prerelease, assets: names.map((name, i) => ({name, id: id * 100 + i}))};
}
for (const withAssets of [false, true]) {
  const suffix = withAssets ? 'with assets' : 'no assets';
  const expected = withAssets ? ['sample.tar', 'SHA256SUMS'] : [];
  for (const [scenario, initial] of [
    ['no release', []], ['empty draft', [release(true)]],
    ['draft with expected subset', [release(true, expected.slice(0, 1))]],
    ['published expected', [release(false, expected)]],
  ]) test(`record retry: ${scenario}, ${suffix}`, async () => {
    const f = recordFixture(withAssets, initial);
    await writeReleaseRecord(f);
    assert.equal(f.records.length, 1); assert.equal(f.records[0].draft, false);
    assert.deepEqual(f.records[0].assets.map(a => a.name).sort(), expected.slice().sort());
    assert.equal(f.calls.at(-1)[0], 'read');
    if (scenario === 'published expected') assert.deepEqual(f.calls.map(c => c[0]), ['read']);
    else {
      const publish = f.calls.find(c => c[0] === 'update' && c[1].draft === false);
      assert.equal(publish[1].make_latest, 'true');
      assert.equal(f.records[0].name, '1.2.3'); assert.equal(f.records[0].body, 'Sample changes');
      if (scenario.includes('draft')) assert.ok(f.calls.some(c => c[0] === 'update' && c[1].body === 'Sample changes'));
    }
  });
  for (const [scenario, initial, error] of [
    ['unexpected draft asset', [release(true, ['unexpected.bin'])], /unexpected assets/],
    ['wrong prerelease', [release(false, expected, true)], /differs from expected/],
    ['wrong published names', [release(false, ['unexpected.bin'])], /differs from expected/],
    ['two releases', [release(true), release(false, expected, false, 2)], /more than one release/],
  ]) test(`record rejects: ${scenario}, ${suffix}`, async () => {
    const f = recordFixture(withAssets, initial);
    await assert.rejects(writeReleaseRecord(f), error); assert.deepEqual(f.calls, []);
  });
  test(`record read-back guard: draft, prerelease, names, tag and identity, ${suffix}`, async () => {
    for (const alter of [r => r.draft = true, r => r.prerelease = true, (r, f) => f.records[0].assets = [{name: 'unexpected.bin'}], r => r.tag_name = 'other', r => r.id = 88]) {
      const f = recordFixture(withAssets);
      const read = f.github.rest.repos.getReleaseByTag;
      f.github.rest.repos.getReleaseByTag = async params => {const response = await read(params); alter(response.data, f); return response;};
      await assert.rejects(writeReleaseRecord(f), /differs from expected|different release/);
    }
  });
}

test('prereleases and non-latest units never become latest', async () => {
  for (const prerelease of [false, true]) {
    const f = recordFixture(false, [], prerelease); f.env.LATEST_UNIT = 'sample-docs';
    await writeReleaseRecord(f);
    const publish = f.calls.find(c => c[0] === 'update' && c[1].draft === false);
    assert.equal(publish[1].prerelease, prerelease); assert.equal(publish[1].make_latest, 'false');
  }
});

test('retry replaces draft assets after a partial upload failure', async () => {
  const f = recordFixture(true);
  const upload = f.github.rest.repos.uploadReleaseAsset;
  let fail = true;
  f.github.rest.repos.uploadReleaseAsset = async params => {
    if (fail && params.name === 'SHA256SUMS') throw new Error('upload interrupted');
    return upload(params);
  };
  await assert.rejects(writeReleaseRecord(f), /upload interrupted/);
  assert.equal(f.records[0].draft, true); assert.equal(f.records[0].assets.length, 1);
  fail = false; await writeReleaseRecord(f);
  assert.equal(f.records.length, 1); assert.equal(f.records[0].draft, false);
  assert.equal(f.records[0].assets.length, 2); assert.ok(f.calls.some(c => c[0] === 'delete'));
});

test('resolve ignores primary records at other commits', async t => {
  const f = resolution(t);
  const tree = f.git(['rev-parse', 'HEAD^{tree}']).trim();
  const other = execFileSync('git', ['-c', 'user.name=Example', '-c', 'user.email=example@example.test', 'commit-tree', tree, '-m', 'Other'], {cwd: f.git(['rev-parse', '--show-toplevel']).trim(), encoding: 'utf8'}).trim();
  f.git(['-c', 'user.name=Example', '-c', 'user.email=example@example.test', 'tag', '-a', 'sample-docs-4.5.6', other, '-m', 'intentional release record\n\ntag-id: release-unit/sample-docs/primary\nversion: 4.5.6\nbaseline: false\n']);
  await resolveRelease(f);
  assert.deepEqual(JSON.parse(f.output.units), ['sample-tool']);
});

test('resolve rejects two primary streams releasing the same unit', async t => {
  const f = resolution(t);
  f.model.release_units['sample-tool'].tags.mirror = {role: 'primary', template: 'mirror-{version}'};
  f.env.RELEASE_MODEL = JSON.stringify(f.model);
  f.git(['-c', 'user.name=Example', '-c', 'user.email=example@example.test', 'tag', '-a', 'mirror-1.2.3', '-m', 'intentional release record\n\ntag-id: release-unit/sample-tool/mirror\nversion: 1.2.3\nbaseline: false\n']);
  await assert.rejects(resolveRelease(f), /multiple primary release records/);
});

test('resolve ignores lookalike records without the exact header', async t => {
  const f = resolution(t, [
    {name: 'sample-tool-1.2.3', unit: 'sample-tool', version: '1.2.3', baseline: 'false'},
    {name: 'sample-docs-4.5.6', message: 'different header\n\ntag-id: release-unit/sample-docs/primary\nversion: 4.5.6\nbaseline: false\n'},
  ]);
  await resolveRelease(f); assert.deepEqual(JSON.parse(f.output.units), ['sample-tool']);
});

test('latest-unit prerelease stays non-latest', async () => {
  const f = recordFixture(false, [], true);
  await writeReleaseRecord(f);
  assert.equal(f.calls.find(c => c[0] === 'update' && c[1].draft === false)[1].make_latest, 'false');
});

test('release record refuses units not resolved in this run', async () => {
  const f = recordFixture(false); f.env.RELEASE_UNIT = 'sample-docs';
  await assert.rejects(writeReleaseRecord(f), /not in RELEASE_JSON/);
  assert.deepEqual(f.calls, []);
});

test('release assets refuse links', t => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'release-links-')); t.after(() => fs.rmSync(dir, {recursive: true, force: true}));
  fs.writeFileSync(path.join(dir, 'sample.tar'), 'sample');
  fs.symlinkSync(path.join(dir, 'sample.tar'), path.join(dir, 'linked.tar'));
  assert.throws(() => collectAssets(dir, fs, path, crypto), /Non-file release asset/);
});
