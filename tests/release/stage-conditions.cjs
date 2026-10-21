// relationships: validates: release-process
// Evaluate this template's expression subset; hosted scheduling remains a live gate.
const assert = require('node:assert/strict');
function ancestors(jobs, name) {
  return [...new Set((jobs[name].needs || []).flatMap(prior => [prior, ...ancestors(jobs, prior)]))];
}
function condition(jobs, name, results = {}, units = ['sample-tool', 'sample-docs'], overrides = {}) {
  const prior = ancestors(jobs, name);
  const needs = Object.fromEntries(prior.map(id => [id, {result: results[id] || 'success'}]));
  if (needs.resolve) needs.resolve.outputs = {units: JSON.stringify(units), record: String(units.includes('sample-tool'))};
  const expression = jobs[name].if;
  if (!expression) return prior.every(id => needs[id].result === 'success');
  return Boolean(new Function('needs', 'cancelled', 'failure', 'contains', 'fromJSON',
    'return (' + expression.replace(/needs\.([a-z0-9-]+)/g, (_, id) => `needs[${JSON.stringify(id)}]`) + ');')(
      needs, () => overrides.cancelled ?? prior.some(id => needs[id].result === 'cancelled'),
      () => overrides.failure ?? prior.some(id => needs[id].result === 'failure'),
      (array, value) => array.includes(value), JSON.parse));
}
function truthTable(jobs, names = Object.keys(jobs)) {
  const rows = [];
  for (const name of names) {
    if (name === 'resolve') continue;
    const isPreflight = name.startsWith('preflight');
    const unit = name.endsWith('-2') ? 'sample-docs' : 'sample-tool';
    for (const prior of jobs[name].needs) {
      for (const result of ['success', 'skipped', 'failure', 'cancelled']) {
        const requiredRecord = prior === 'release-record' && unit === 'sample-tool';
        const expected = result === 'success' || (result === 'skipped' && !isPreflight &&
          prior !== 'resolve' && !prior.startsWith('preflight') && !requiredRecord);
        const runs = condition(jobs, name, {[prior]: result});
        assert.equal(runs, expected, `${name}: ${prior}=${result} must ${expected ? 'admit' : 'stop'} job`);
        rows.push({job: name, prior, priorResult: result, runs});
      }
    }
    if (!isPreflight) {
      assert.equal(condition(jobs, name, {}, [], {}), false, `${name}: no released unit must stop job`);
      rows.push({job: name, prior: 'units', priorResult: 'empty', runs: false});
      for (const guard of ['cancelled', 'failure']) {
        assert.equal(condition(jobs, name, {}, undefined, {[guard]: true}), false,
          `${name}: ${guard} independently must stop job`);
        rows.push({job: name, prior: guard, priorResult: 'true', runs: false});
      }
    }
  }
  return rows;
}
module.exports = {ancestors, condition, truthTable};
if (require.main === module) {
  const jobs = JSON.parse(require('node:fs').readFileSync(process.argv[2], 'utf8')).jobs;
  console.log(JSON.stringify(truthTable(jobs, process.argv[3] ? [process.argv[3]] : undefined)));
}
