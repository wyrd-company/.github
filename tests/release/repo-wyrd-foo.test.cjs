// relationships: validates: release-process
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const crypto = require('node:crypto');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync('release/targets/repo-wyrd-foo/prepare.js', 'utf8');
const digest = bytes => 'sha256:' + crypto.createHash('sha256').update(bytes).digest('hex');
function fixture() {
  const manifest = {tag: '1.2.3', version: '1.2.3', source: {commit: 'a'.repeat(40), repository: 'example-org/sample-tool'},
    artifacts: [{filename: 'sample-tool.deb', sha256: 'b'.repeat(64)}]};
  return {env: {PACKAGE_MODEL: JSON.stringify({release_units: {'sample-tool': {release_record: 'required'}}}),
    PACKAGE_UNITS: 'sample-tool', PACKAGE_UNIT: 'sample-tool', PACKAGE_MANIFEST_ASSET: 'release-manifest.json',
    PACKAGE_DRY_RUN: 'false', RELEASE_UNITS: 'sample-tool', RELEASE_JSON: JSON.stringify({'sample-tool': {version: '1.2.3', tag: '1.2.3'}}),
    RELEASE_SOURCE_SHA: 'a'.repeat(40), RUNNER_TEMP: '/tmp'}, manifest, outputs: {}, writes: [], reads: [], info: [],
    record: {id: 42, tag_name: '1.2.3', draft: false}};
}
async function run(f, emitted=source) {
  const bytes = Buffer.from(JSON.stringify(f.manifest));
  const assets = f.assets || [{id: 7, name: 'release-manifest.json', digest: digest(bytes)},
    {id: 8, name: 'sample-tool.deb', digest: 'sha256:' + 'b'.repeat(64)}];
  const github = {rest: {repos: {
    getReleaseByTag: async args => {f.reads.push(args); return {data: f.record};},
    listReleaseAssets: Symbol('assets'),
    getReleaseAsset: async args => {f.reads.push(args); return {data: f.download || bytes};},
  }}, paginate: async (method, args) => {assert.equal(method, github.rest.repos.listReleaseAssets); f.reads.push(args); return assets;}};
  const core = {setOutput: (key, value) => {f.outputs[key] = value;}, info: value => f.info.push(value)};
  const localFs = {writeFileSync: (...args) => f.writes.push(args)};
  const sandbox = {github, context: {repo: {owner: 'example-org', repo: 'sample-tool'}}, core, process: {env: f.env}, Buffer,
    require: name => ({'node:fs': localFs, 'node:path': path, 'node:crypto': crypto}[name])};
  await vm.runInNewContext('(async () => {' + emitted + '\n})()', sandbox);
  return f;
}
async function refuses(change, message) {
  const f = fixture(); change(f);
  await assert.rejects(run(f), message);
  assert.equal(f.outputs.submit, 'false');
  assert.equal(f.writes.length, 0);
}
test('packages: released record supplies manifest, names and digests', async () => {
  const f = await run(fixture());
  assert.equal(f.outputs.submit, 'true');
  assert.equal(f.outputs.tag, '1.2.3'); assert.equal(f.outputs.version, '1.2.3');
  assert.equal(f.outputs.manifest, '/tmp/package-selection-sample-tool.json');
  assert.equal(f.writes[0][1].toString(), JSON.stringify(f.manifest));
  assert.equal(JSON.parse(f.outputs.assets)[1].digest, 'sha256:' + 'b'.repeat(64));
  assert.equal(f.reads[0].tag, '1.2.3'); assert.equal(f.reads[1].release_id, 42);
  assert.equal(f.reads[1].per_page, 100); assert.equal(f.reads[2].asset_id, 7);
});
test('packages: none in the whole selection refuses before submission', async () => {
  await refuses(f => {
    f.env.PACKAGE_UNITS = 'sample-tool sample-docs';
    f.env.PACKAGE_MODEL = JSON.stringify({release_units: {'sample-tool': {release_record: 'required'}, 'sample-docs': {release_record: 'none'}}});
  }, /requires release_record/);
});
test('packages: unknown selection refuses', async () => {
  await refuses(f => {f.env.PACKAGE_UNITS += ' absent';}, /requires release_record/);
});
test('packages: dry-run choice is required', async () => {
  await refuses(f => {delete f.env.PACKAGE_DRY_RUN;}, /dry-run choice/);
});
test('packages: matrix is limited to selected units', async () => {
  await refuses(f => {f.env.PACKAGE_UNIT = 'sample-docs';}, /outside the selection/);
});
test('packages: unresolved units do not read assets or submit', async () => {
  const f = fixture(); f.env.RELEASE_UNITS = 'sample-docs';
  await run(f); assert.equal(f.outputs.submit, 'false'); assert.equal(f.reads.length, 0); assert.equal(f.writes.length, 0);
});
test('packages: missing release refuses', async () => {
  await refuses(f => {f.env.RELEASE_JSON = '{}';}, /release record is missing/);
});
test('packages: only stable bare tags are supported by publisher', async () => {
  for (const [version, tag] of [['1.2.3', 'sample-tool-1.2.3'], ['1.2.3-beta', '1.2.3-beta'], ['v1.2.3', 'v1.2.3']]) {
    await refuses(f => {f.env.RELEASE_JSON = JSON.stringify({'sample-tool': {version, tag}});}, /stable bare SemVer/);
  }
});
test('packages: draft record refuses', async () => {await refuses(f => {f.record.draft = true;}, /Published package/);});
test('packages: different record tag refuses', async () => {await refuses(f => {f.record.tag_name = '1.2.4';}, /Published package/);});
test('packages: manifest asset must be present exactly once', async () => {
  for (const count of [0, 2]) await refuses(f => {f.assets = Array(count).fill({id: 7, name: 'release-manifest.json'});}, /Exactly one/);
});
test('packages: missing manifest digest refuses', async () => {
  await refuses(f => {f.assets = [{id: 7, name: 'release-manifest.json'}];}, /needs a SHA256/);
});
test('packages: changed manifest download refuses', async () => {
  await refuses(f => {f.download = Buffer.from('{}');}, /differs from the release-record digest/);
});
for (const field of ['tag', 'version', 'commit', 'repository']) test('packages: manifest ' + field + ' must match source', async () => {
  await refuses(f => {if (field in f.manifest) f.manifest[field] = 'different'; else f.manifest.source[field] = 'different';}, /identity differs/);
});
test('packages: package artifact must match release-record digest', async () => {
  await refuses(f => {f.manifest.artifacts[0].sha256 = 'c'.repeat(64);}, /artifact differs/);
});
test('packages: dry run never enables submission', async () => {
  const f = fixture(); f.env.PACKAGE_DRY_RUN = 'true'; await run(f);
  assert.equal(f.outputs.submit, 'false'); assert.equal(f.info.length, 1); assert.equal(f.writes.length, 1);
});
// The Python contract test supplies the exact rendered entry point here.
if (process.env.PACKAGE_EMITTED_SCRIPT) test('packages: emitted dry run', async () => {
  const f = fixture(); f.env.PACKAGE_DRY_RUN = 'true';
  await run(f, fs.readFileSync(process.env.PACKAGE_EMITTED_SCRIPT, 'utf8'));
  assert.equal(f.outputs.submit, 'false'); assert.equal(f.writes.length, 1); assert.equal(f.info.length, 1);
});
