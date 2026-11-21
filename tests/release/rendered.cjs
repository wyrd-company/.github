// relationships: validates: release-process
// Execute the emitted github-script bodies, including their entry points.
const fs = require('node:fs');
const path = require('node:path');
const {execFileSync} = require('node:child_process');
const assert = require('node:assert/strict');
const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;
async function main() {
  const workflow = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
  const withAssets = process.argv[3] === 'assets';
  const git = args => execFileSync('git', args, {encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe']});
  git(['init', '-b', 'main']);
  git(['-c', 'user.name=Example', '-c', 'user.email=example@example.test', 'commit', '--allow-empty', '-m', 'Source']);
  git(['remote', 'add', 'origin', process.cwd()]);
  const sha = git(['rev-parse', 'HEAD']).trim();
  git(['-c', 'user.name=Example', '-c', 'user.email=example@example.test', 'tag', '-a', 'sample-tool-1.2.3', '-m', 'intentional release record\n\ntag-id: release-unit/sample-tool/primary\nversion: 1.2.3\nbaseline: false\n']);
  const resolve = workflow.jobs.resolve.steps.find(step => step.id === 'resolve');
  const context = {repo: {owner: 'example-org', repo: 'sample-tool'}};
  const outputs = {};
  const core = {setOutput: (key, value) => outputs[key] = value, summary: {addRaw: () => ({write: async () => {}})}};
  const execute = (script, github, env) => new AsyncFunction('github', 'context', 'core', 'require', 'process', script)(github, context, core, require, {env});
  await execute(resolve.with.script, {rest: {actions: {listWorkflowRuns: 'runs'}}, paginate: async () => [{head_sha: sha, event: 'push', conclusion: 'success'}]},
    {...resolve.env, SOURCE_SHA: sha, DISPATCH_SHA: sha, DISPATCH_REF: 'refs/heads/main', DEFAULT_BRANCH: 'main'});
  assert.equal(outputs.source_sha, sha);
  assert.deepEqual(JSON.parse(outputs.units), ['sample-tool']);
  for (const [name, job] of Object.entries(workflow.jobs)) {
    if (name !== 'preflight' && !name.startsWith('preflight-')) continue;
    const step = job.steps[0], env = {};
    for (const key of Object.keys(step.env || {})) env[key] = 'present';
    await execute(step.with.script, {}, env);
    if (Object.keys(env).length) {
      const key = Object.keys(env)[0]; delete env[key];
      await assert.rejects(execute(step.with.script, {}, env), new RegExp('Missing credentials:.*\\n' + key));
    }
  }
  fs.writeFileSync('CHANGELOG.md', '# Changes\n\n## 1.2.3\n\nSample changes.\n');
  const dir = path.join(process.cwd(), 'assets');
  if (withAssets) {
    fs.mkdirSync(path.join(dir, 'release-asset+sample-tool+sample'), {recursive: true});
    fs.writeFileSync(path.join(dir, 'release-asset+sample-tool+sample/sample.tar'), 'sample');
  }
  let record;
  const uploaded = [], events = [];
  const github = {rest: {repos: {
    listReleases: 'releases', listReleaseAssets: 'assets',
    createRelease: async params => {events.push('create'); record = {...params, id: 1}; return {data: record};},
    uploadReleaseAsset: async params => {events.push('upload'); uploaded.push({name: params.name, bytes: params.data});},
    updateRelease: async params => {events.push('publish'); record = {...record, ...params}; return {data: record};},
    getReleaseByTag: async params => {events.push('read'); assert.equal(params.tag, record.tag_name); return {data: record};},
  }}, paginate: async route => route === 'releases' ? [] : uploaded};
  const job = workflow.jobs['release-record'];
  const step = job.steps.find(item => item.name === 'Publish and read back the release record');
  await execute(step.with.script, github, {...job.env, RELEASE_SOURCE_SHA: sha, RELEASE_UNITS: 'sample-tool',
    RELEASE_JSON: outputs.releases, RELEASE_UNIT: 'sample-tool', ASSET_DIRECTORY: dir});
  assert.equal(record.draft, false); assert.equal(record.name, 'sample-tool-1.2.3');
  assert.equal(record.body, 'Sample changes.'); assert.equal(record.make_latest, 'true');
  assert.equal(events.at(-1), 'read');
  assert.deepEqual(uploaded.map(a => a.name).sort(), withAssets ? ['SHA256SUMS', 'sample.tar'] : []);
  console.log(JSON.stringify({resolved: true, preflight: true, record: true, assets: withAssets, events}));
}
main().catch(error => {console.error(error); process.exitCode = 1;});
