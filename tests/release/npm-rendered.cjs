// relationships: validates: release-process
// Drive the emitted entry points; never execute a registry write.
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const child = require('node:child_process');
const AsyncFunction = Object.getPrototypeOf(async function() {}).constructor;
const workflow = JSON.parse(fs.readFileSync(process.argv[2]));
const cwd = process.argv[3];
fs.mkdirSync(path.join(cwd, 'npm'));
fs.writeFileSync(path.join(cwd, 'npm/package.json'), JSON.stringify({name:'sample-tool',version:'1.2.3',files:['index.js']}));
fs.writeFileSync(path.join(cwd, 'npm/index.js'), 'module.exports=42;\n');
const calls = [];
const wrappedRequire = name => name === 'node:child_process' ? {execFileSync:(command,args,opts)=>{
  calls.push(args);
  if(args[0]==='pack') return child.execFileSync(command,args,opts);
  assert.equal(args[0],'publish');assert.ok(args.includes('--dry-run'));
  return '';
}} : require(name);
async function main() {
  for(const name of ['publish-npm','verify-npm-clients']) {
    const job=workflow.jobs[name];
    const step=job.steps.find(s=>s.with?.script);
    await new AsyncFunction('require','process','fetch',step.with.script)(wrappedRequire,
      {platform:process.platform,cwd:()=>cwd,env:{...job.env,RELEASE_UNITS:'sample-tool',
        RELEASE_JSON:JSON.stringify({'sample-tool':{version:'1.2.3',prerelease:false,tag:'1.2.3'}})}},
      ()=>{throw new Error('Dry run attempted a registry lookup');});
  }
  assert.deepEqual(calls.map(c=>c[0]),['pack','publish']);
  console.log('PASS emitted npm dry-run publish and verify entry points');
}
main().catch(e=>{console.error(e);process.exitCode=1;});
