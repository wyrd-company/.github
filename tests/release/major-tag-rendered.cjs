// relationships: validates: release-process
// Execute only the emitted writer, with its parsed environment, in local Git fixtures.
const fs = require('node:fs');
const path = require('node:path');
const {execFileSync} = require('node:child_process');
const assert = require('node:assert/strict');
const job = JSON.parse(fs.readFileSync(process.argv[2])).jobs['update-major-tag'];
const root = process.cwd();
const remote = path.join(root, 'remote.git');
const work = path.join(root, 'work');
const env = Object.fromEntries(Object.entries(process.env).filter(([key]) => !key.startsWith('GIT_')));
Object.assign(env, {GIT_ALLOW_PROTOCOL: 'file', GIT_CONFIG_NOSYSTEM: '1', GIT_CONFIG_GLOBAL: '/dev/null',
  GIT_AUTHOR_NAME: 'Example Author', GIT_AUTHOR_EMAIL: 'author@example.invalid',
  GIT_COMMITTER_NAME: 'Example Committer', GIT_COMMITTER_EMAIL: 'committer@example.invalid'});
const git = (args, cwd = work) => execFileSync('git', args, {cwd, env, encoding: 'utf8', stdio: ['pipe', 'pipe', 'pipe']}).trim();
fs.mkdirSync(work);
git(['init', '--bare', remote], root);
git(['init']);
git(['remote', 'add', 'origin', remote]);
const commits = [];
for (let i = 0; i < 3; i++) {
  fs.writeFileSync(path.join(work, 'example.txt'), String(i));
  git(['add', 'example.txt']); git(['commit', '-m', `Example revision ${i}`]);
  commits.push(git(['rev-parse', 'HEAD']));
}
const script = job.steps[1].with.script;
const execute = (version, source, tag = `sample-tool-${version}`) => {
  const releases = {'sample-tool': {version, tag, prerelease: version.includes('-')},
    'sample-docs': {version: '9.8.7', tag: 'other-9.8.7', prerelease: false}};
  const runtime = {};
  for (const [key, value] of Object.entries(job.env)) {
    runtime[key] = value === '${{ needs.resolve.outputs.source_sha }}' ? source :
      value === '${{ needs.resolve.outputs.releases }}' ? JSON.stringify(releases) : value;
  }
  const output = [];
  const runEnv = {...Object.fromEntries(Object.entries(env).filter(([key]) => !/^GIT_(AUTHOR|COMMITTER)_/.test(key))), ...runtime, GITHUB_REF: 'refs/tags/7.6.5', GITHUB_REF_NAME: '7.6.5',
    GITHUB_SHA: commits[2]};
  const runner = path.join(root, 'runner.cjs');
  fs.writeFileSync(runner, `new Function('require', ${JSON.stringify(script)})(require);`);
  output.push(execFileSync(process.execPath, [runner], {cwd: work, env: runEnv, encoding: 'utf8'}));
  return {version, tag, source, output};
};
const refs = () => git(['show-ref', '--tags'], remote);
const tagState = major => ({commit: git(['rev-parse', `refs/tags/${major}^{}`], remote),
  type: git(['cat-file', '-t', `refs/tags/${major}`], remote),
  message: git(['for-each-ref', '--format=%(contents)', `refs/tags/${major}`], remote),
  tagger: git(['for-each-ref', '--format=%(taggername) %(taggeremail)', `refs/tags/${major}`], remote)});
const rows = [];
const checkTag = (source, tag) => {
  assert.equal(git(['for-each-ref', '--format=%(refname)', 'refs/tags'], remote), 'refs/tags/1', 'major version comes from selected RELEASE_JSON');
  const state = tagState('1');
  assert.equal(state.commit, source, 'major tag uses resolved source');
  assert.equal(state.type, 'tag', 'major tag is annotated');
  assert.equal(state.message, `Update 1 to ${tag}`, 'major tag uses selected RELEASE_JSON tag');
  assert.equal(state.tagger, 'github-actions[bot] <github-actions[bot]@users.noreply.github.com>');
  assert.equal(git(['for-each-ref', '--format=%(refname)', 'refs/tags'], remote), 'refs/tags/1', 'push only bare numeric major tag');
  return state;
};
rows.push({...execute('1.0.0', commits[0]), state: checkTag(commits[0], 'sample-tool-1.0.0')});
rows.push({...execute('1.4.0', commits[0]), state: checkTag(commits[0], 'sample-tool-1.4.0')});
rows.push({...execute('1.5.0', commits[1]), state: checkTag(commits[1], 'sample-tool-1.5.0')});
for (const version of ['0.9.0', '1.2.0-rc.1', '1.2.3+b']) {
  const before = refs(); const row = execute(version, commits[2]);
  assert.equal(refs(), before, version === '0.9.0' ? 'major-zero skip leaves all remote refs unchanged' :
    'plain-version check leaves all remote refs unchanged');
  assert.match(row.output.join(''), /Skipping major tag update/);
  rows.push({...row, unchanged: true});
}
rows.push({...execute('1.5.0', commits[1]), state: checkTag(commits[1], 'sample-tool-1.5.0'), identicalRerun: true});
rows.push({...execute('1.4.0', commits[0]), state: checkTag(commits[0], 'sample-tool-1.4.0'), olderRerunMovesBackward: true});
// Measure the retained old job's force-update behavior against the same local bare remote.
const oldScript = fs.readFileSync(process.argv[3], 'utf8');
const oldRows = [];
for (const [tag, source] of [['1.5.0', commits[1]], ['1.4.0', commits[0]]]) {
  git(['checkout', '--detach', source]);
  execFileSync('bash', ['-e', '-c', oldScript], {cwd: work, env: {...Object.fromEntries(Object.entries(env).filter(([key]) => !/^GIT_(AUTHOR|COMMITTER)_/.test(key))), GITHUB_REF: `refs/tags/${tag}`}, stdio: 'pipe'});
  oldRows.push({tag, source, state: tagState('1')});
}
assert.equal(oldRows[1].state.commit, commits[0], 'old job retry force-moves backward');
console.log(JSON.stringify({network: 'Git file protocol only', remote, commits, rows, oldRows, cleanup: 'caller TemporaryDirectory'}, null, 2));
