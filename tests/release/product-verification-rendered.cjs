// relationships: validates: release-process
// Local runner for emitted step expressions and the real example local action.
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const {execFileSync} = require('node:child_process');
const {condition} = require('./stage-conditions.cjs');
function expression(text, scope) {
  return new Function(...Object.keys(scope), 'return (' + text
    .replace(/needs\.([a-z0-9-]+)/g, (_, id) => `needs[${JSON.stringify(id)}]`) + ');')(...Object.values(scope));
}
function expand(value, scope) {
  if (typeof value !== 'string') return value;
  return value.replace(/\$\{\{\s*(.*?)\s*\}\}/g, (_, text) => String(expression(text, scope)));
}
function main() {
  const jobs = JSON.parse(fs.readFileSync(process.argv[2], 'utf8')).jobs;
  const scenario = process.argv[3], job = jobs['verify-product'];
  const unit = 'sample-tool', sha = 'a'.repeat(40), ref = 'dispatch-9.9.9';
  const releases = {
    [unit]: {tag: 'sample-tool-1.2.3-preview.4', version: '1.2.3-preview.4', prerelease: true},
    'sample-docs': {tag: 'sample-docs-4.5.6', version: '4.5.6', prerelease: false}
  };
  const units = scenario === 'unreleased-unit' ? ['sample-docs'] : Object.keys(releases);
  const results = {'release-record': scenario === 'skipped-record' ? 'skipped' : 'success'};
  const needs = Object.fromEntries(job.needs.map(name => [name, {result: results[name] || 'success'}]));
  needs.resolve.outputs = {source_sha: sha, units: JSON.stringify(units),
    record_units: JSON.stringify(units.includes(unit) ? [unit] : []), releases: JSON.stringify(releases)};
  const directory = process.cwd(), receipt = path.join(directory, 'verification.jsonl');
  fs.cpSync(path.join(__dirname, 'sample-product'), directory, {recursive: true});
  let recipeCalls = 0, jobFailed = false;
  const observed = [];
  for (const matrix of job.strategy.matrix.include) {
    const scope = {needs, matrix, github: {ref_name: ref}, fromJSON: JSON.parse, toJSON: JSON.stringify,
      join: (values, separator) => values.join(separator), contains: (values, item) => values.includes(item)};
    const env = Object.fromEntries(Object.entries(job.env).map(([key, value]) => [key, expand(value, scope)]));
    scope.env = env;
    if (!condition(jobs, 'verify-product', results, units)) continue;
    for (const step of job.steps) {
      if (step.if && !expression(step.if, scope)) continue;
      if (step.uses.startsWith('actions/checkout@')) {
        assert.equal(expand(step.with.ref, scope), sha);
        assert.equal(step.with['persist-credentials'], false);
      } else {
        assert.ok(step.uses.startsWith('./'));
        const action = path.resolve(directory, step.uses);
        assert.ok(action.startsWith(directory + path.sep));
        recipeCalls++;
        const actionEnv = {...process.env, ...env, EXAMPLE_RECEIPT: receipt,
          EXAMPLE_FAIL_VERIFICATION: String(scenario === 'recipe-failure')};
        for (const [key, value] of Object.entries(step.with)) actionEnv['INPUT_' + key.toUpperCase()] = expand(value, scope);
        try {execFileSync(process.execPath, [path.join(action, 'index.cjs')], {env: actionEnv, stdio: 'pipe'});}
        catch (error) {
          assert.equal(scenario, 'recipe-failure');
          assert.match(String(error.stderr), /Example verification failed/);
          jobFailed = true;
        }
        const actual = JSON.parse(fs.readFileSync(receipt, 'utf8').trim().split('\n').at(-1));
        assert.equal(actual.unit, matrix.unit);
        assert.deepEqual(JSON.parse(actual['matrix-entry']), matrix);
        assert.equal(actual.tag, releases[matrix.unit].tag, 'recipe must receive resolved tag');
        assert.equal(actual.version, releases[matrix.unit].version, 'recipe must receive resolved version');
        assert.equal(actual.prerelease, JSON.stringify(releases[matrix.unit].prerelease), 'recipe must receive resolved prerelease');
        observed.push(actual);
      }
    }
  }
  assert.equal(jobFailed, scenario === 'recipe-failure', 'recipe failure must fail the verification job');
  const selected = job.strategy.matrix.include.filter(entry => units.includes(entry.unit) &&
    !(scenario === 'skipped-record' && entry.unit === unit));
  assert.equal(recipeCalls, selected.length);
  if (['unreleased-unit', 'skipped-record'].includes(scenario))
    assert.ok(observed.every(row => row.unit === 'sample-docs'), 'unreleased or record-blocked unit runs no recipe');
  return {scenario, recipeCalls, jobFailed, ref, releases, observed};
}
try {console.log(JSON.stringify(main()));} catch (error) {console.error(error); process.exitCode = 1;}
