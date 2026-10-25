// relationships: validates: release-process
const {test} = require('node:test');
const assert = require('node:assert/strict');
const {homebrewRelease,formulaVersion,formulaDiff,compareVersions} = require('../../templates/release/targets/homebrew-tap/homebrew.js');
const {fixture,formula} = require('./homebrew-fixture.cjs');

test('homebrew fresh update and identical retry',async()=>{
  const f=fixture();await homebrewRelease(f.options());
  assert.deepEqual(f.stored,f.bytes);assert.equal(f.writes.length,1);assert.equal(f.writes[0].sha,'observed-blob');
  assert.equal(f.calls.filter(c=>c[0]==='read').length,2);
  await homebrewRelease(f.options());assert.equal(f.writes.length,1);
});
test('homebrew new formula omits the existing blob SHA',async()=>{
  const f=fixture();f.stored=null;await homebrewRelease(f.options());
  assert.equal(f.writes.length,1);assert.equal('sha' in f.writes[0],false);assert.deepEqual(f.stored,f.bytes);
});
test('homebrew same version conflicting bytes refuse before write',async()=>{
  const f=fixture();f.stored=Buffer.from(formula().toString().replace('class SampleTool','class OtherTool'));
  await assert.rejects(homebrewRelease(f.options()),/different formula bytes/);assert.equal(f.writes.length,0);
});
test('homebrew entire selection requires release records before IO',async()=>{
  for(const policy of ['none',null]) {
    const f=fixture();const model=JSON.parse(f.env.HOMEBREW_MODEL);
    if(policy) model.release_units['sample-docs']={release_record:policy};
    f.env.HOMEBREW_MODEL=JSON.stringify(model);f.env.HOMEBREW_UNITS+=' sample-docs';
    await assert.rejects(homebrewRelease(f.options()),/release record/);assert.equal(f.calls.length,0);
  }
});
test('homebrew only released selected units read assets',async()=>{
  const f=fixture();f.env.RELEASE_UNITS='sample-docs';await homebrewRelease(f.options());
  assert.equal(f.calls.filter(c=>c[0]==='release').length,0);assert.equal(f.writes.length,0);
});
test('homebrew resolved release evidence is required',async()=>{
  for(const value of [undefined,{}, {version:'v1.2.3',tag:'v1.2.3'}, {version:'1.2.3'}]) {
    const f=fixture();f.env.RELEASE_JSON=JSON.stringify({'sample-tool':value});
    await assert.rejects(homebrewRelease(f.options()),/Missing resolved/);assert.equal(f.calls.length,0);
  }
});
test('homebrew dry run displays patch and makes zero writes',async()=>{
  const f=fixture();f.env.HOMEBREW_DRY_RUN='true';const prior=Buffer.from(f.stored);
  await homebrewRelease(f.options());assert.equal(f.writes.length,0);assert.deepEqual(f.stored,prior);
  const patch=f.logs.join('\n');assert.match(patch,/--- a\/Formula\/sample-tool.rb/);assert.match(patch,/\+\+\+ b\/Formula\/sample-tool.rb/);
  assert.match(patch,/-  version "1.2.2"/);assert.match(patch,/\+  version "1.2.3"/);assert.match(patch,/Dry run/);
});
test('homebrew dry run choice must be explicit',async()=>{
  for(const value of [undefined,'','False']) {const f=fixture();f.env.HOMEBREW_DRY_RUN=value;
    await assert.rejects(homebrewRelease(f.options()),/dry-run choice/);assert.equal(f.calls.length,0);}
});
test('homebrew destination is a separate named repository',async()=>{
  for(const value of ['https://example.invalid/tap','example-org/sample-tool','EXAMPLE-ORG/SAMPLE-TOOL']) {
    const f=fixture();f.env.HOMEBREW_TAP_REPOSITORY=value;
    await assert.rejects(homebrewRelease(f.options()),/tap repository|another repository/);assert.equal(f.calls.length,0);
  }
});
test('homebrew tap must have a default branch',async()=>{
  const f=fixture();f.branch='';await assert.rejects(homebrewRelease(f.options()),/default branch/);assert.equal(f.writes.length,0);
});
test('homebrew source release must match resolved state',async()=>{
  for(const [key,value] of [['draft',true],['tag_name','9.0.0'],['prerelease',true]]) {
    const f=fixture();f.record[key]=value;await assert.rejects(homebrewRelease(f.options()),/Source release differs/);assert.equal(f.writes.length,0);
  }
});
test('homebrew exact formula and checksum assets are required',async()=>{
  for(const assets of [[],[{id:10,name:'sample-tool.rb'}],
    [{id:10,name:'sample-tool.rb'},{id:10,name:'sample-tool.rb'},{id:11,name:'SHA256SUMS'}]]) {
    const f=fixture();f.assets=assets;await assert.rejects(homebrewRelease(f.options()),/Expected one release asset/);assert.equal(f.writes.length,0);
  }
});
test('homebrew formula checksum binds independent downloaded bytes',async()=>{
  for(const sums of ['', 'a'.repeat(64)+'  sample-tool.rb\n', 'bad  sample-tool.rb\nbad  sample-tool.rb\n',
    (require('node:crypto').createHash('sha256').update(formula()).digest('hex')+'  sample-tool.rb\n').repeat(2)]) {
    const f=fixture();f.sums=sums;await assert.rejects(homebrewRelease(f.options()),/checksum differs/);assert.equal(f.writes.length,0);
  }
});
test('homebrew formula has one explicit bare SemVer stanza',async()=>{
  for(const text of ['', "version '1.2.3'\n", 'version "v1.2.3"\n','version "1.02.3"\n','version "1.2.3-01"\n',
    'version "1.2.3"\nversion "1.2.3"\n',
    'version "1.2.3"\nversion \'9.0.0\'\n']) assert.throws(()=>formulaVersion(Buffer.from(text)),/one explicit bare SemVer/);
  assert.equal(formulaVersion(formula('1.2.3-beta.2+build.1')),'1.2.3-beta.2+build.1');
});
test('homebrew formula version binds resolved version',async()=>{
  const f=fixture();f.bytes=formula('9.0.0','1.2.3');await assert.rejects(homebrewRelease(f.options()),/version differs/);assert.equal(f.writes.length,0);
});
test('homebrew formula ends with newline',async()=>{
  const f=fixture();f.bytes=f.bytes.subarray(0,-1);await assert.rejects(homebrewRelease(f.options()),/newline/);assert.equal(f.writes.length,0);
});
test('homebrew every download URL binds source repository and tag',async()=>{
  for(const bytes of [formula('1.2.3','9.0.0'),Buffer.from(formula().toString().replace('  url ','  other ')),
    Buffer.from(formula().toString().replace('https:', 'http:')),
    Buffer.from(formula().toString()+'  url other_value\n'),
    Buffer.from(formula().toString()+'  url "https://example.invalid/file"\n')]) {
    const f=fixture();f.bytes=bytes;await assert.rejects(homebrewRelease(f.options()),/download URL differs/);assert.equal(f.writes.length,0);
  }
});
test('homebrew tap read errors fail closed',async()=>{
  for(const status of [401,403,429,500]) {const f=fixture();f.readError=status;
    await assert.rejects(homebrewRelease(f.options()),/Read failed/);assert.equal(f.writes.length,0);}
});
test('homebrew tap content must be a file with observable bytes',async()=>{
  for(const changed of [{type:'dir'},{encoding:'none'},{sha:''},{content:null}]) {
    const f=fixture();f.contentOverride={type:'file',encoding:'base64',sha:'observed-blob',content:f.stored.toString('base64'),...changed};
    await assert.rejects(homebrewRelease(f.options()),/observable file/);assert.equal(f.writes.length,0);
  }
});
test('homebrew matching concurrent commit succeeds without retrying write',async()=>{
  const f=fixture();f.race=()=>{f.stored=f.bytes;throw new Error('Concurrent commit');};
  await homebrewRelease(f.options());assert.equal(f.writes.length,1);
});
test('homebrew conflicting concurrent commit preserves failure',async()=>{
  const f=fixture();f.race=()=>{throw new Error('Concurrent conflict');};
  await assert.rejects(homebrewRelease(f.options()),/Concurrent conflict/);assert.equal(f.writes.length,1);
});
test('homebrew write must be observed with identical bytes',async()=>{
  for(const stored of [null,formula('1.2.2')]) {const f=fixture();f.stored=stored;f.noStore=true;
    await assert.rejects(homebrewRelease(f.options()),/not observable/);assert.equal(f.writes.length,1);}
});
test('homebrew diff describes exact full-file bytes',()=>{
  assert.equal(formulaDiff('Formula/sample.rb',Buffer.from('one\n'),Buffer.from('two\n')),
    '--- a/Formula/sample.rb\n+++ b/Formula/sample.rb\n@@ -1,1 +1,1 @@\n-one\n+two');
  assert.match(formulaDiff('Formula/sample.rb',Buffer.from('old'),Buffer.from('new\n')), /-old\n\\ No newline at end of file\n\+new/);
});

test('homebrew downgrade refuses stable and prerelease precedence',async()=>{
  for(const [current, next] of [['1.2.4','1.2.3'], ['1.2.3','1.2.3-beta'],
    ['1.2.3-beta.10','1.2.3-beta.2'], ['1.2.3-beta.x','1.2.3-beta.10']]) {
    const f=fixture();f.stored=formula(current);f.bytes=formula(next);
    f.record.tag_name=next;f.record.prerelease=next.includes('-');
    f.env.RELEASE_JSON=JSON.stringify({'sample-tool':{version:next,tag:next,prerelease:f.record.prerelease}});
    await assert.rejects(homebrewRelease(f.options()),/downgrade/);assert.equal(f.writes.length,0);
  }
});
test('homebrew SemVer ordering agrees with npm reference',()=>{
  const path=require('node:path');
  const root=require('node:child_process').execFileSync('npm',['root','--global'],{encoding:'utf8'}).trim();
  const semver=require(path.join(root,'npm/node_modules/semver'));
  const versions=['0.0.0','0.0.1','1.0.0-alpha','1.0.0-alpha.1','1.0.0-alpha.beta',
    '1.0.0-beta','1.0.0-beta.2','1.0.0-beta.11','1.0.0-rc.1','1.0.0','1.0.0+build.1',
    '1.0.1','1.2.3-1','1.2.3-0a','1.2.3-1.0','1.2.3-1.a','1.2.3-2','1.2.3-a','1.2.3-a.0',
    '1.2.3-a.b','1.2.3-a-b','1.2.3-z','1.2.3','1.2.3+build.2','2.0.0','10.0.0'];
  for(const a of versions) for(const b of versions) assert.equal(compareVersions(a,b),Math.sign(semver.compare(a,b)),a+' vs '+b);
  assert.equal(compareVersions('99999999999999999999.0.0','99999999999999999998.0.0'),1);
  assert.equal(compareVersions('1.0.0-99999999999999999999','1.0.0-99999999999999999998'),1);
});

test('homebrew validates later unit assets before the first tap write',async()=>{
  const f=fixture();const model=JSON.parse(f.env.HOMEBREW_MODEL);
  model.release_units['sample-docs']={release_record:'required'};
  f.env.HOMEBREW_MODEL=JSON.stringify(model);f.env.HOMEBREW_UNITS+=' sample-docs';f.env.RELEASE_UNITS+=' sample-docs';
  const releases=JSON.parse(f.env.RELEASE_JSON);releases['sample-docs']={...releases['sample-tool']};
  f.env.RELEASE_JSON=JSON.stringify(releases);
  await assert.rejects(homebrewRelease(f.options()),/Expected one release asset: sample-docs.rb/);
  assert.equal(f.writes.length,0);
});
