// relationships: validates: release-process
// Exercise emitted upload scripts and release-record entry point with fake transports.
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const {execFileSync} = require('node:child_process');
const {condition} = require('./stage-conditions.cjs');
const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;
async function main() {
  const workflow = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
  const scenario = process.argv[3], jobs = workflow.jobs;
  const sha = 'a'.repeat(40), unit = 'sample-tool';
  const directory = process.cwd(), temp = path.join(directory, 'temp');
  fs.cpSync(path.join(__dirname, 'sample-product'), directory, {recursive: true});
  fs.writeFileSync(path.join(directory, 'CHANGELOG.md'), '# Changes\n\n## 1.2.3\n\nExample release.\n');
  const sdkDirectory = path.join(temp, 'release-artifact-client');
  const moduleDirectory = path.join(sdkDirectory, 'node_modules/@actions/artifact/lib');
  fs.mkdirSync(moduleDirectory, {recursive: true});
  fs.writeFileSync(path.join(moduleDirectory, 'package.json'), '{"type":"module"}');
  fs.writeFileSync(path.join(moduleDirectory, 'artifact.js'),
    'export const DefaultArtifactClient = globalThis.exampleArtifactClient;\n');
  const artifacts = new Map(), uploaded = [], events = [], results = {};
  let recordCalls = 0, recipeCalls = 0, record;
  globalThis.exampleArtifactClient = class {
    async uploadArtifact(name, files, rootDirectory, options) {
      assert.equal(options, undefined, 'No cross-run credential options');
      assert.equal(files.length, 1);
      assert.equal(path.dirname(files[0]), rootDirectory);
      assert.equal(name, `release-asset+${unit}+${path.basename(files[0])}`);
      assert.equal(artifacts.has(name), false, 'Per-file artifact names cannot overwrite');
      artifacts.set(name, {name: path.basename(files[0]), bytes: fs.readFileSync(files[0])});
      events.push({upload: name});
      return {id: artifacts.size};
    }
  };
  const env = {RELEASE_SOURCE_SHA: sha, RELEASE_UNITS: unit,
    RELEASE_JSON: JSON.stringify({[unit]: {tag: 'sample-tool-1.2.3', version: '1.2.3', prerelease: false}})};
  function expand(value, matrix) {
    if (typeof value !== 'string') return value;
    return value.replace(/\$\{\{\s*(runner.temp|matrix.unit|matrix.target|needs.resolve.outputs.source_sha)\s*\}\}/g,
      (_, key) => ({'runner.temp': temp, 'matrix.unit': matrix.unit, 'matrix.target': matrix.target,
                    'needs.resolve.outputs.source_sha': sha})[key]);
  }
  function download(step, matrix) {
    assert.equal(step.with['github-token'], undefined);
    assert.equal(step.with['run-id'], undefined);
    assert.equal(step.with.repository, undefined);
    const pattern = expand(step.with.pattern, matrix);
    assert.equal(pattern, `release-asset+${unit}+*`);
    const destination = expand(step.with.path, matrix);
    for (const [name, file] of artifacts) {
      if (!name.startsWith(pattern.slice(0, -1))) continue;
      const output = path.join(destination, name);
      fs.mkdirSync(output, {recursive: true});
      fs.writeFileSync(path.join(output, file.name), file.bytes);
    }
    events.push({download: pattern, destination});
  }
  const execute = (script, stepEnv, github = {}) => new AsyncFunction('require', 'process', 'github', 'context', 'core', script)(
    require, {env: {...env, ...stepEnv}}, github, {repo: {owner: 'example-org', repo: 'sample-tool'}}, {});
  async function runRecipeJob(name, matrix) {
    const job = jobs[name];
    for (const step of job.steps) {
      if (step.if && matrix.unit !== unit) continue;
      if (step.uses?.startsWith('actions/checkout@')) {
        assert.equal(expand(step.with.ref, matrix), sha);
        assert.equal(step.with['persist-credentials'], false);
      } else if (step.uses?.includes('download-artifact@')) {
        download(step, matrix);
      } else if (step.uses?.startsWith('./')) {
        recipeCalls++;
        const actionDirectory = path.resolve(directory, step.uses);
        assert.ok(actionDirectory.startsWith(directory + path.sep));
        const recipeEnv = {...process.env, ...env};
        for (const [key, value] of Object.entries(step.with)) recipeEnv['INPUT_' + key.toUpperCase()] = expand(value, matrix);
        recipeEnv.EXAMPLE_FAIL_BUILD = String(scenario === 'failed-build' && matrix.target === 'second');
        recipeEnv.EXAMPLE_EMPTY_OUTPUT = String(scenario === 'empty-output');
        execFileSync(process.execPath, [path.join(actionDirectory, 'index.cjs')], {env: recipeEnv, stdio: 'pipe'});
      } else if (step.name === 'Install official artifact client') {
        // Dependency installation is outside product sources. No network in this fixture.
        assert.match(step.run, /npm ci --prefix "\$ARTIFACT_CLIENT_DIRECTORY" --ignore-scripts/);
        assert.equal(expand(step['working-directory'], matrix), temp);
      } else if (step.uses?.includes('github-script@')) {
        const stepEnv = Object.fromEntries(Object.entries(step.env).map(([key, value]) => [key, expand(value, matrix)]));
        await execute(step.with.script, stepEnv);
      } else assert.fail('Unrecognized emitted recipe step');
    }
  }
  const build = jobs['build-product'];
  if (scenario === 'unreleased-unit') {
    await runRecipeJob('build-product', {unit: 'sample-docs', target: 'first'});
    assert.equal(recipeCalls, 0); assert.equal(artifacts.size, 0);
    return {scenario, recipeCalls, recordCalls};
  }
  let failed = false;
  for (const matrix of build.strategy.matrix.include.filter(entry => entry.unit === unit)) {
    try {await runRecipeJob('build-product', matrix);} catch (error) {
      if (scenario === 'failed-build') assert.match(String(error.stderr), /Example build failed/);
      else if (scenario === 'empty-output') assert.match(error.message, /if-no-files-found: error/);
      else throw error;
      failed = true;
    }
  }
  results['build-product'] = failed ? 'failure' : 'success';
  const assemblyRuns = condition(jobs, 'assemble-product', results, [unit]);
  if (assemblyRuns) await runRecipeJob('assemble-product', {unit});
  results['assemble-product'] = assemblyRuns ? 'success' : 'skipped';
  const recordRuns = condition(jobs, 'release-record', results, [unit]);
  if (failed) {
    assert.equal(assemblyRuns, false, 'Failed build must stop assembly');
    assert.equal(recordRuns, false, 'Failed build must stop record');
  }
  if (recordRuns) {
    const job = jobs['release-record'];
    const downloadStep = job.steps.find(step => step.uses?.includes('download-artifact@'));
    download(downloadStep, {unit});
    const step = job.steps.find(step => step.name === 'Publish and read back the release record');
    const stepEnv = {...job.env, ...step.env, ...env, RELEASE_UNIT: unit};
    stepEnv.ASSET_DIRECTORY = expand(step.env.ASSET_DIRECTORY, {unit});
    const github = {rest: {repos: {
      listReleases: 'releases', listReleaseAssets: 'assets',
      createRelease: async params => {recordCalls++; record = {...params, id: 1}; return {data: record};},
      uploadReleaseAsset: async params => {uploaded.push({name: params.name, bytes: params.data});},
      updateRelease: async params => {record = {...record, ...params}; return {data: record};},
      getReleaseByTag: async () => {events.push({record: 'read-back'}); return {data: record};},
    }}, paginate: async route => route === 'releases' ? [] : uploaded};
    await execute(step.with.script, stepEnv, github);
  }
  if (failed) return {scenario, recordCalls, events};
  assert.equal(recordCalls, 1); assert.equal(record.draft, false);
  assert.deepEqual(uploaded.map(file => file.name).sort(), ['SHA256SUMS', 'aggregate.json', 'first.txt', 'second.txt']);
  const files = Object.fromEntries(uploaded.map(file => [file.name, file.bytes]));
  const sums = Object.fromEntries(files.SHA256SUMS.toString().trim().split('\n').map(line => {
    const [digest, name] = line.split('  '); return [name, digest];
  }));
  for (const [name, bytes] of Object.entries(files)) if (name !== 'SHA256SUMS')
    assert.equal(sums[name], crypto.createHash('sha256').update(bytes).digest('hex'), name + ' record checksum');
  const aggregate = JSON.parse(files['aggregate.json']);
  assert.deepEqual(Object.keys(aggregate).sort(), ['first.txt', 'second.txt']);
  for (const [name, digest] of Object.entries(aggregate)) assert.equal(digest, sums[name], name + ' aggregate/record agreement');
  assert.deepEqual(events.at(-1), {record: 'read-back'});
  return {scenario, recordCalls, recipeCalls, assets: Object.keys(files).sort(), aggregateMatchesRecord: true, events};
}
main().then(receipt => console.log(JSON.stringify(receipt))).catch(error => {console.error(error); process.exitCode = 1;});
