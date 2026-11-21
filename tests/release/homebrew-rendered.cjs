// relationships: validates: release-process
const fs = require('node:fs');
const assert = require('node:assert/strict');
const {fixture} = require('./homebrew-fixture.cjs');
const AsyncFunction = Object.getPrototypeOf(async function() {}).constructor;
async function main() {
  const workflow=JSON.parse(fs.readFileSync(process.argv[2]));
  const job=workflow.jobs['publish-homebrew-tap'];
  const step=job.steps[0];const f=fixture();
  const token = 'example-runtime-value';
  class Client {constructor(options) {assert.equal(options.auth,token);return f.tap;}}
  f.source.constructor=Client;
  const env={...job.env,...step.env,HOMEBREW_TAP_TOKEN:token,RELEASE_UNITS:'sample-tool',RELEASE_JSON:f.env.RELEASE_JSON};
  await new AsyncFunction('require','process','github','context','core',step.with.script)(require,{env},f.source,
    {repo:f.options().repo},{info:s=>f.logs.push(s)});
  assert.equal(f.writes.length,0);assert.match(f.logs.join('\n'),/\+  version "1.2.3"/);
  console.log('PASS emitted Homebrew dry-run diff and zero tap writes');
}
main().catch(e=>{console.error(e);process.exitCode=1;});
