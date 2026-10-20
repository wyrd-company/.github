// relationships: validates: release-process
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const crypto = require('node:crypto');
const {execFileSync} = require('node:child_process');
const {npmRelease, npmCommand} = require('../../release/targets/npm/npm.js');

function fixture(t, {mode = 'committed', version = '1.2.3', privatePackage = false} = {}) {
  const cwd = fs.mkdtempSync(path.join(os.tmpdir(), 'npm-part-test-'));
  t.after(() => fs.rmSync(cwd, {recursive:true, force:true}));
  fs.mkdirSync(path.join(cwd, 'npm'));
  fs.writeFileSync(path.join(cwd, 'npm/package.json'), JSON.stringify({name:'@example-org/sample-tool', version, private:privatePackage,
    files:['launcher.cjs'], bin:{'sample-tool':'launcher.cjs'}, scripts:{prepack:'exit 79', prepublishOnly:'exit 79'}}));
  fs.writeFileSync(path.join(cwd, 'npm/launcher.cjs'), '#!/usr/bin/env node\nconsole.log("sample");\n');
  const projection = {adapter:'npm', generic:false, mode, file:'npm/package.json', native_identity:'@example-org/sample-tool'};
  const unit = {release_record:'required', ecosystems:['cargo','npm'], projections:[projection]};
  const env = {NPM_MODEL:JSON.stringify({release_units:{'sample-tool':unit}}), NPM_UNITS:'sample-tool',
    NPM_DRY_RUN:'false', RELEASE_UNITS:'sample-tool', RELEASE_JSON:JSON.stringify({'sample-tool':{version:'1.2.3', tag:'1.2.3', prerelease:false}})};
  const calls = [], requests = [];
  let bytes, published = false, remoteOverride;
  const run = (args, options) => {
    calls.push(args);
    if (args[0] === 'pack') {
      const output = execFileSync('npm', args, {...options, encoding:'utf8', env:{...process.env, npm_config_userconfig:'/dev/null'}});
      bytes = fs.readFileSync(path.join(args.at(-1), JSON.parse(output)[0].filename));
      return output;
    }
    if (args[0] === 'publish') {
      assert.deepEqual(fs.readFileSync(args[1]), bytes);
      assert.ok(args.includes('--ignore-scripts'));
      assert.ok(args.includes('--provenance'));
      published = !args.includes('--dry-run');
      return '';
    }
    throw new Error('Unexpected npm command: ' + args[0]);
  };
  const remote = () => ({name:'@example-org/sample-tool', version:'1.2.3', dist:{integrity:'sha512-'+crypto.createHash('sha512').update(bytes).digest('base64')}});
  const request = async url => {
    requests.push(url);
    return remoteOverride ? remoteOverride() : {status:published?200:404, json:async()=>remote()};
  };
  return {cwd, env, unit, projection, calls, requests, run, request, remote,
    setRemote:fn=>remoteOverride=fn, reset:()=>calls.splice(0), options:()=>({cwd,env,run,request,verify:false})};
}

test('npm fresh publication and retry use identical packed bytes', async t => {
  const f=fixture(t); await npmRelease(f.options());
  assert.equal(f.calls.filter(c=>c[0]==='publish').length,1);
  assert.equal(f.requests.length,2);
  assert.equal(fs.existsSync(f.calls[0].at(-1)),false);
  assert.match(f.requests[0], /%40example-org%2Fsample-tool\/1.2.3$/);
  f.reset(); await npmRelease(f.options());
  assert.equal(f.calls.filter(c=>c[0]==='publish').length,0);
});
test('npm conflicting published bytes fail before publication', async t => {
  const f=fixture(t); f.setRemote(()=>({status:200,json:async()=>({...f.remote(),dist:{integrity:'sha512-other'}})}));
  await assert.rejects(npmRelease(f.options()), /different package bytes/);
  assert.equal(f.calls.filter(c=>c[0]==='publish').length,0);
});
test('npm registry identity and lookup failures fail closed', async t => {
  const f=fixture(t);
  for (const status of [401,403,429,500]) {
    f.setRemote(()=>({status})); await assert.rejects(npmRelease(f.options()), /lookup failed/);
  }
  for(const key of ['name','version']) {
    f.setRemote(()=>({status:200,json:async()=>({...f.remote(),[key]:'different'})}));
    await assert.rejects(npmRelease(f.options()), /different package bytes/);
  }
  assert.equal(f.calls.filter(c=>c[0]==='publish').length,0);
});
test('npm missing release record refuses before packing or registry calls', async t => {
  const f=fixture(t);f.unit.release_record='none';f.env.NPM_MODEL=JSON.stringify({release_units:{'sample-tool':f.unit}});
  await assert.rejects(npmRelease(f.options()), /requires a release record/);
  assert.equal(f.calls.length,0);assert.equal(f.requests.length,0);
});
test('npm units and ecosystem projections select non-node launchers', async t => {
  const f=fixture(t);f.unit.projections.push({...f.projection,adapter:'json',generic:true});
  f.env.NPM_MODEL=JSON.stringify({release_units:{'sample-tool':f.unit}});
  await npmRelease(f.options());assert.equal(f.calls.filter(c=>c[0]==='pack').length,1);
  f.reset();f.env.RELEASE_UNITS='sample-docs';await npmRelease(f.options());assert.equal(f.calls.length,0);
});
test('npm missing or generic-only projections refuse', async t => {
  const f=fixture(t);f.unit.projections=[{...f.projection,generic:true}];
  f.env.NPM_MODEL=JSON.stringify({release_units:{'sample-tool':f.unit}});
  await assert.rejects(npmRelease(f.options()), /No npm package projection/);assert.equal(f.calls.length,0);
});
test('npm manifest guards refuse before writes', async t => {
  for(const [field,value,error] of [['name','wrong',/identity mismatch/],['version','0.0.0',/version mismatch/],['private',true,/publishable/]]) {
    const f=fixture(t), file=path.join(f.cwd,'npm/package.json'), manifest=JSON.parse(fs.readFileSync(file));
    manifest[field]=value;fs.writeFileSync(file,JSON.stringify(manifest));
    await assert.rejects(npmRelease(f.options()),error);assert.equal(f.calls.length,0);
  }
});
test('npm injected projection stamps a temporary copy and preserves source', async t => {
  const f=fixture(t,{mode:'injected',version:'0.0.0'});
  await npmRelease(f.options());
  assert.equal(JSON.parse(fs.readFileSync(path.join(f.cwd,'npm/package.json'))).version,'0.0.0');
  assert.notEqual(f.calls[0][1],path.join(f.cwd,'npm'));
});
test('npm none projection validates the source version', async t => {
  const f=fixture(t,{mode:'none',version:'0.0.0'});
  await assert.rejects(npmRelease(f.options()),/version mismatch/);assert.equal(f.calls.length,0);
});
test('npm dry run never contacts registry and explicitly uses dry-run', async t => {
  const f=fixture(t);f.env.NPM_DRY_RUN='true';await npmRelease(f.options());
  assert.ok(f.calls.find(c=>c[0]==='publish').includes('--dry-run'));assert.equal(f.requests.length,0);
  f.reset();await npmRelease({...f.options(),verify:true});assert.equal(f.calls.length,0);
});
test('npm missing dry run choice refuses before packing', async t => {
  const f=fixture(t);delete f.env.NPM_DRY_RUN;
  await assert.rejects(npmRelease(f.options()),/dry-run choice/);assert.equal(f.calls.length,0);
});
test('npm duplicate package projections refuse before packing', async t => {
  const f=fixture(t);f.unit.projections.push(f.projection);f.env.NPM_MODEL=JSON.stringify({release_units:{'sample-tool':f.unit}});
  await assert.rejects(npmRelease(f.options()),/Duplicate npm package/);assert.equal(f.calls.length,0);
});
test('npm absent release version refuses before packing', async t => {
  const f=fixture(t);f.env.RELEASE_JSON='{}';
  await assert.rejects(npmRelease(f.options()),/Missing resolved npm release/);assert.equal(f.calls.length,0);
});
test('npm pack identity is checked before publication', async t => {
  const f=fixture(t), run=(args,opts)=>args[0]==='pack'?'[{"name":"other","version":"1.2.3"}]':f.run(args,opts);
  await assert.rejects(npmRelease({...f.options(),run}),/pack identity mismatch/);assert.equal(f.requests.length,0);
});
test('npm prereleases use next and stable releases use latest', async t => {
  const f=fixture(t);f.env.RELEASE_JSON=JSON.stringify({'sample-tool':{version:'1.2.3',prerelease:true}});
  await npmRelease(f.options());assert.ok(f.calls.find(c=>c[0]==='publish').includes('--tag=next'));
  const stable=fixture(t);await npmRelease(stable.options());assert.ok(stable.calls.find(c=>c[0]==='publish').includes('--tag=latest'));
});
test('npm race accepts matching bytes and rejects conflict', async t => {
  for(const match of [true,false]) {
    const f=fixture(t);let lookup=0;f.setRemote(()=>++lookup===1?{status:404}:{status:200,json:async()=>match?f.remote():{}});
    const run=(args,opts)=>{if(args[0]==='publish')throw new Error('raced');return f.run(args,opts);};
    if(match)await npmRelease({...f.options(),run});
    else await assert.rejects(npmRelease({...f.options(),run}),/different package bytes/);
  }
});
test('npm publication must be read back and missing publication preserves failure', async t => {
  const f=fixture(t);f.setRemote(()=>({status:404}));
  await assert.rejects(npmRelease(f.options()),/not observable/);
  const run=(args,opts)=>{if(args[0]==='publish')throw new Error('publish failed');return f.run(args,opts);};
  await assert.rejects(npmRelease({...f.options(),run}),/publish failed/);
});
test('npm clean clients install and validate exact package identity', async t => {
  for(const wrong of [false,true]) {
    const f=fixture(t);const run=(args,opts)=>{
      assert.equal(args[0],'install');assert.ok(args.includes('@example-org/sample-tool@1.2.3'));
      const dest=path.join(opts.cwd,'node_modules/@example-org/sample-tool');fs.mkdirSync(dest,{recursive:true});
      fs.writeFileSync(path.join(dest,'package.json'),JSON.stringify({name:'@example-org/sample-tool',version:wrong?'0.0.0':'1.2.3'}));
    };
    if(wrong)await assert.rejects(npmRelease({...f.options(),verify:true,run}),/different package/);
    else await npmRelease({...f.options(),verify:true,run});
  }
});

test('npm Windows client invokes the Node CLI without a command shell', t => {
  const directory=fs.mkdtempSync(path.join(os.tmpdir(),'npm-windows-test-'));
  t.after(()=>fs.rmSync(directory,{recursive:true,force:true}));
  const executable=path.join(directory,'action-runtime/node.exe');
  const setup=path.join(directory,'setup-node');
  const cli=path.join(setup,'node_modules/npm/bin/npm-cli.js');
  fs.mkdirSync(path.dirname(cli),{recursive:true});fs.writeFileSync(cli,'');
  const missing=path.join(directory,'missing');
  assert.deepEqual(npmCommand('win32',executable,missing+';'+setup),[executable,cli]);
  assert.deepEqual(npmCommand('linux',executable,''),['npm']);
  assert.throws(()=>npmCommand('win32',executable,missing),/npm CLI is missing/);
});
