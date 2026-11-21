// relationships: implements: release-process
function releaseNotes(text, version) {
  const lines = text.split(/\r?\n/);
  const start = lines.indexOf(`## ${version}`);
  if (start < 0) throw new Error(`Missing changelog section: ## ${version}`);
  const end = lines.findIndex((line, index) => index > start && line.startsWith('## '));
  const notes = lines.slice(start + 1, end < 0 ? undefined : end).join('\n').trim();
  if (!notes) throw new Error(`Empty changelog section: ## ${version}`);
  return notes;
}
function collectAssets(directory, fs, path, crypto) {
  const files = new Map();
  function visit(root) {
    if (!fs.existsSync(root)) return;
    for (const item of fs.readdirSync(root, {withFileTypes: true})) {
      const file = path.join(root, item.name);
      if (item.isDirectory()) visit(file);
      else {
        if (!item.isFile()) throw new Error(`Non-file release asset: ${file}`);
        if (files.has(item.name) || item.name === 'SHA256SUMS') throw new Error(`Duplicate or reserved asset name: ${item.name}`);
        files.set(item.name, fs.readFileSync(file));
      }
    }
  }
  visit(directory);
  if (files.size) {
    const sums = [...files].sort(([a], [b]) => a.localeCompare(b)).map(([name, data]) =>
      `${crypto.createHash('sha256').update(data).digest('hex')}  ${name}\n`).join('');
    files.set('SHA256SUMS', Buffer.from(sums));
  }
  return files;
}
async function writeReleaseRecord({github, context, env, files, notes}) {
  const repo = context.repo;
  const release = JSON.parse(env.RELEASE_JSON)[env.RELEASE_UNIT];
  if (!release) throw new Error('Release Unit is not in RELEASE_JSON');
  const expected = [...files.keys()].sort();
  const releases = (await github.paginate(github.rest.repos.listReleases, {...repo, per_page: 100}))
    .filter(item => item.tag_name === release.tag);
  if (releases.length > 1) throw new Error(`${release.tag}: more than one release on the tag`);
  let record = releases[0];
  async function assets(item) {
    return github.paginate(github.rest.repos.listReleaseAssets, {...repo, release_id: item.id, per_page: 100});
  }
  function sameNames(items) {
    return JSON.stringify(items.map(item => item.name).sort()) === JSON.stringify(expected);
  }
  async function verifyPublished(item) {
    if (item.draft || item.tag_name !== release.tag || item.prerelease !== release.prerelease || !sameNames(await assets(item))) {
      throw new Error(`${release.tag}: published release ${item.id} differs from expected state`);
    }
  }
  if (record && !record.draft) {
    await verifyPublished(record);
  } else {
    const existing = record ? await assets(record) : [];
    if (existing.some(item => !files.has(item.name))) throw new Error(`${release.tag}: draft ${record.id} has unexpected assets`);
    const parameters = {...repo, name: release.tag, body: notes};
    if (record) {
      await github.rest.repos.updateRelease({...parameters, release_id: record.id});
      for (const item of existing) await github.rest.repos.deleteReleaseAsset({...repo, asset_id: item.id});
    } else {
      record = (await github.rest.repos.createRelease({...parameters, tag_name: release.tag,
        draft: true, prerelease: release.prerelease, make_latest: 'false'})).data;
    }
    for (const [name, data] of files) {
      await github.rest.repos.uploadReleaseAsset({...repo, release_id: record.id, name, data,
        headers: {'content-type': 'application/octet-stream', 'content-length': data.length}});
    }
    await github.rest.repos.updateRelease({...repo, release_id: record.id, draft: false,
      prerelease: release.prerelease, make_latest: String(!release.prerelease && env.RELEASE_UNIT === env.LATEST_UNIT)});
  }
  // Always read back by tag, including unchanged published releases.
  const readBack = (await github.rest.repos.getReleaseByTag({...repo, tag: release.tag})).data;
  if (readBack.id !== record.id) throw new Error(`${release.tag}: read-back returned a different release`);
  await verifyPublished(readBack);
}
const fs = require('node:fs');
const env = process.env;
const release = JSON.parse(env.RELEASE_JSON)[env.RELEASE_UNIT];
const model = JSON.parse(env.RELEASE_MODEL);
const notes = releaseNotes(fs.readFileSync(model.release_units[env.RELEASE_UNIT].changelog, 'utf8'), release.version);
const files = collectAssets(env.ASSET_DIRECTORY, fs, require('node:path'), require('node:crypto'));
await writeReleaseRecord({github, context, env, files, notes});
