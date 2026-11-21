// relationships: validates: release-process
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {condition} = require('./stage-conditions.cjs');
const jobs = JSON.parse(fs.readFileSync(process.argv[2])).jobs;
const requiredVerify = JSON.parse(process.argv[3]);
const rows = [];
for (const prior of jobs['update-major-tag'].needs) {
  for (const result of ['success', 'failure', 'cancelled', 'skipped']) {
    const mustSucceed = ['resolve', 'release-record', ...requiredVerify].includes(prior) || prior.startsWith('preflight');
    const expected = result === 'success' || result === 'skipped' && !mustSucceed;
    const runs = condition(jobs, 'update-major-tag', {[prior]: result});
    assert.equal(runs, expected, `major-tag ${prior}=${result} must ${expected ? 'admit' : 'stop'}`);
    rows.push({prior, result, runs});
  }
}
for (const units of [[], ['sample-docs'], ['sample-tool'], ['sample-tool', 'sample-docs']]) {
  const runs = condition(jobs, 'update-major-tag', {}, units);
  assert.equal(runs, units.includes('sample-tool'), 'major-tag requires the named released unit');
  rows.push({units, runs});
}
for (const guard of ['cancelled', 'failure']) {
  assert.equal(condition(jobs, 'update-major-tag', {}, undefined, {[guard]: true}), false,
    `major-tag ${guard} independently stops`);
  rows.push({guard, runs: false});
}
// Set both status functions false so each explicit result guard is the sole refusal.
for (const prior of ['resolve', ...jobs['update-major-tag'].needs.filter(x => x.startsWith('preflight')),
  'release-record', ...requiredVerify]) {
  assert.equal(condition(jobs, 'update-major-tag', {[prior]: 'skipped'}, undefined,
    {cancelled: false, failure: false}), false, `major-tag explicit ${prior} success independently required`);
  rows.push({independent: prior, result: 'skipped', runs: false});
}
console.log(JSON.stringify(rows));
