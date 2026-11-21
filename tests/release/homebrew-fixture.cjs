// relationships: validates: release-process
const assert = require('node:assert/strict');
const crypto = require('node:crypto');
function formula(version = '1.2.3', tag = version) {
  return Buffer.from(`class SampleTool < Formula\n  version "${version}"\n  url "https://github.com/example-org/sample-tool/releases/download/${tag}/sample.tar.gz"\n  sha256 "${'a'.repeat(64)}"\nend\n`);
}
function fixture() {
  const calls = [], logs = [];
  const f = {bytes:formula(), stored:formula('1.2.2'), writes:[], calls, logs,
    record:{id:5, draft:false, tag_name:'1.2.3', prerelease:false},
    assets:[{id:10,name:'sample-tool.rb'}, {id:11,name:'SHA256SUMS'}],
    env:{HOMEBREW_MODEL:JSON.stringify({release_units:{'sample-tool':{release_record:'required'}}}),
      HOMEBREW_UNITS:'sample-tool', RELEASE_UNITS:'sample-tool',
      RELEASE_JSON:JSON.stringify({'sample-tool':{version:'1.2.3',tag:'1.2.3',prerelease:false}}),
      HOMEBREW_TAP_REPOSITORY:'example-org/homebrew-tools',HOMEBREW_DRY_RUN:'false'}};
  const sourceRepo = args => {assert.equal(args.owner, 'example-org');assert.equal(args.repo,'sample-tool');};
  const tapRepo = args => {assert.equal(args.owner,'example-org');assert.equal(args.repo,'homebrew-tools');};
  f.source = {rest:{repos:{
    getReleaseByTag:async args => {sourceRepo(args);calls.push(['release',args]);return {data:f.record};},
    listReleaseAssets:async () => {throw new Error('Use pagination');},
    getReleaseAsset:async args => {
      sourceRepo(args);calls.push(['asset',args]);assert.equal(args.headers.accept,'application/octet-stream');
      const digest=crypto.createHash('sha256').update(f.bytes).digest('hex');
      return {data:args.asset_id===10 ? f.bytes : Buffer.from(f.sums ?? `${digest}  sample-tool.rb\n`)};
    },
  }}, paginate:async (method,args) => {sourceRepo(args);calls.push(['assets',args]);assert.equal(args.release_id,5);return f.assets;}};
  f.tap = {rest:{repos:{
    get:async args => {tapRepo(args);calls.push(['tap',args]);return {data:{default_branch:f.branch ?? 'main'}};},
    getContent:async args => {
      tapRepo(args);calls.push(['read',args]);assert.equal(args.path,'Formula/sample-tool.rb');assert.equal(args.ref,'main');
      if(f.readError) throw Object.assign(new Error('Read failed'),{status:f.readError});
      if(!f.stored) throw Object.assign(new Error('Missing'),{status:404});
      return {data:f.contentOverride ?? {type:'file',encoding:'base64',content:f.stored.toString('base64'),sha:'observed-blob'}};
    },
    createOrUpdateFileContents:async args => {
      tapRepo(args);f.writes.push(args);assert.equal(args.branch,'main');
      assert.equal(args.path,'Formula/sample-tool.rb');assert.equal(args.message,'sample-tool 1.2.3');
      if(f.race) return f.race(args);
      if(!f.noStore) f.stored = Buffer.from(args.content,'base64');
      return {data:{}};
    },
  }}};
  f.options = () => ({source:f.source,tap:f.tap,repo:{owner:'example-org',repo:'sample-tool'},env:f.env,log:s=>logs.push(s)});
  return f;
}
module.exports={fixture,formula};
