// relationships: implements: release-process
// Included in the workflow; the source repository need not carry this helper.
const HOMEBREW_SEMVER = /^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-((?:0|[1-9]\d*|\d*[A-Za-z-][0-9A-Za-z-]*)(?:\.(?:0|[1-9]\d*|\d*[A-Za-z-][0-9A-Za-z-]*))*))?(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$/;
function formulaVersion(bytes) {
  const versions = [...bytes.toString('utf8').matchAll(/^\s*version "([^"\r\n]+)"\s*$/gm)];
  if ([...bytes.toString('utf8').matchAll(/^\s*version\b/gm)].length !== 1 || versions.length !== 1 || !HOMEBREW_SEMVER.test(versions[0][1])) {
    throw new Error('Formula needs one explicit bare SemVer version stanza');
  }
  return versions[0][1];
}
// Inputs are validated by formulaVersion or the resolved release check.
// Build metadata does not affect SemVer precedence.
function compareVersions(left, right) {
  const parts = version => version.split('+')[0].split(/-(.*)/s);
  const [leftCore, leftPre] = parts(left), [rightCore, rightPre] = parts(right);
  const a = leftCore.split('.').map(BigInt), b = rightCore.split('.').map(BigInt);
  for (let i = 0; i < 3; i++) {
    if (a[i] !== b[i]) return a[i] > b[i] ? 1 : -1;
  }
  if (leftPre === undefined || rightPre === undefined) {
    return leftPre === rightPre ? 0 : leftPre === undefined ? 1 : -1;
  }
  const x = leftPre.split('.'), y = rightPre.split('.');
  for (let i = 0; i < Math.max(x.length, y.length); i++) {
    if (i === x.length || i === y.length) return i === x.length ? -1 : 1;
    if (x[i] === y[i]) continue;
    const xn = /^\d+$/.test(x[i]), yn = /^\d+$/.test(y[i]);
    if (xn && yn) return BigInt(x[i]) > BigInt(y[i]) ? 1 : -1;
    if (xn !== yn) return xn ? -1 : 1;
    return x[i] > y[i] ? 1 : -1;
  }
  return 0;
}
function formulaDiff(path, before, after) {
  const lines = value => {
    const text = value.toString('utf8');
    if (!text) return [];
    return text.endsWith('\n') ? text.slice(0, -1).split('\n') : text.split('\n');
  };
  const oldLines = lines(before), newLines = lines(after);
  // A full-file unified hunk preserves the exact proposed change, including
  // deletions, without invoking a CLI or evaluating Ruby from a release asset.
  return [`--- a/${path}`, `+++ b/${path}`,
    `@@ -${oldLines.length ? 1 : 0},${oldLines.length} +1,${newLines.length} @@`,
    ...oldLines.map(line => '-' + line),
    ...(before.length && !before.toString('utf8').endsWith('\n') ? ['\\ No newline at end of file'] : []),
    ...newLines.map(line => '+' + line)].join('\n');
}
async function homebrewRelease({source, tap, repo, env, log = console.log}) {
  const crypto = require('node:crypto');
  const model = JSON.parse(env.HOMEBREW_MODEL);
  const releases = JSON.parse(env.RELEASE_JSON);
  const selected = env.HOMEBREW_UNITS.split(' ');
  const released = new Set(env.RELEASE_UNITS.split(' '));
  const plans = [];
  // Validate the entire selection before reads or writes, including units
  // selected for this part which are not released by this dispatch.
  for (const id of selected) {
    if (!model.release_units[id] || model.release_units[id].release_record !== 'required') {
      throw new Error('Homebrew requires a release record: ' + id);
    }
    if (!released.has(id)) continue;
    const release = releases[id];
    if (!release || !HOMEBREW_SEMVER.test(release.version) || !release.tag) {
      throw new Error('Missing resolved Homebrew release: ' + id);
    }
    plans.push({id, release});
  }
  if (env.HOMEBREW_DRY_RUN !== 'true' && env.HOMEBREW_DRY_RUN !== 'false') throw new Error('Missing Homebrew dry-run choice');
  const dry = env.HOMEBREW_DRY_RUN === 'true';
  if (!/^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/.test(env.HOMEBREW_TAP_REPOSITORY)) throw new Error('Invalid tap repository');
  const [owner, name] = env.HOMEBREW_TAP_REPOSITORY.split('/');
  if ((owner + '/' + name).toLowerCase() === (repo.owner + '/' + repo.repo).toLowerCase()) throw new Error('Tap must be another repository');
  const target = {owner, repo: name};
  const branch = (await tap.rest.repos.get(target)).data.default_branch;
  if (!branch) throw new Error('Tap has no default branch');
  async function current(path) {
    let data;
    try { data = (await tap.rest.repos.getContent({...target, path, ref: branch})).data; }
    catch (error) { if (error.status === 404) return null; throw error; }
    if (data.type !== 'file' || data.encoding !== 'base64' || !data.sha || typeof data.content !== 'string') {
      throw new Error('Tap formula is not an observable file: ' + path);
    }
    return {sha: data.sha, bytes: Buffer.from(data.content, 'base64')};
  }
  async function asset(assets, name) {
    const matches = assets.filter(item => item.name === name);
    if (matches.length !== 1) throw new Error('Expected one release asset: ' + name);
    const response = await source.rest.repos.getReleaseAsset({...repo, asset_id: matches[0].id,
      headers: {accept: 'application/octet-stream'}});
    return Buffer.from(response.data);
  }
  // Prepare and verify every released unit before the first tap commit.
  for (const plan of plans) {
    const {id, release} = plan;
    const record = (await source.rest.repos.getReleaseByTag({...repo, tag: release.tag})).data;
    if (record.draft || record.tag_name !== release.tag || record.prerelease !== release.prerelease) throw new Error('Source release differs from resolved release: ' + id);
    const assets = await source.paginate(source.rest.repos.listReleaseAssets, {...repo, release_id: record.id, per_page: 100});
    const bytes = await asset(assets, id + '.rb');
    const sums = (await asset(assets, 'SHA256SUMS')).toString('utf8').split('\n');
    const expected = sums.filter(line => line.endsWith('  ' + id + '.rb'));
    const digest = crypto.createHash('sha256').update(bytes).digest('hex');
    if (expected.length !== 1 || expected[0] !== digest + '  ' + id + '.rb') throw new Error('Formula checksum differs: ' + id);
    if (formulaVersion(bytes) !== release.version) throw new Error('Formula version differs from resolved release: ' + id);
    if (!bytes.length || !bytes.toString('utf8').endsWith('\n')) throw new Error('Formula must end in a newline: ' + id);
    const urls = [...bytes.toString('utf8').matchAll(/^\s*url "([^"\r\n]+)"/gm)];
    const prefix = 'https://github.com/' + repo.owner + '/' + repo.repo + '/releases/download/' + release.tag + '/';
    if (urls.length !== [...bytes.toString('utf8').matchAll(/^\s*url\b/gm)].length || !urls.length || urls.some(match => !match[1].startsWith(prefix))) throw new Error('Formula download URL differs from resolved tag: ' + id);
    const path = 'Formula/' + id + '.rb';
    const prior = await current(path);
    if (prior && compareVersions(formulaVersion(prior.bytes), release.version) > 0) throw new Error('Refusing Homebrew downgrade: ' + id);
    if (prior && formulaVersion(prior.bytes) === release.version && !prior.bytes.equals(bytes)) throw new Error('Homebrew version exists with different formula bytes: ' + id);
    Object.assign(plan, {path, bytes, prior});
  }
  for (const {id, release, path, bytes, prior} of plans) {
    if (prior?.bytes.equals(bytes)) { log('Homebrew formula already matches: ' + path); continue; }
    log(formulaDiff(path, prior?.bytes || Buffer.alloc(0), bytes));
    if (dry) { log('Dry run: no tap commit for ' + path); continue; }
    const parameters = {...target, branch, path, message: id + ' ' + release.version,
      content: bytes.toString('base64'), ...(prior ? {sha: prior.sha} : {})};
    try { await tap.rest.repos.createOrUpdateFileContents(parameters); }
    catch (error) {
      // A concurrent identical commit is safe; any other response preserves
      // the original failure. Do not retry a write against a moved blob SHA.
      const observed = await current(path);
      if (observed?.bytes.equals(bytes)) continue;
      throw error;
    }
    const observed = await current(path);
    if (!observed?.bytes.equals(bytes)) throw new Error('Homebrew commit is not observable; rerun this source commit');
  }
}
if (typeof module !== 'undefined') module.exports = {homebrewRelease, formulaVersion, formulaDiff, compareVersions};
