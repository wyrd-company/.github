// relationships: implements: release-process
async function preparePackages({github, context, core, env, fs, crypto, path}) {
  core.setOutput('submit', 'false');
  const model = JSON.parse(env.PACKAGE_MODEL);
  const selected = env.PACKAGE_UNITS.split(' ');
  // Validate the whole selection in every matrix leg before any submission.
  for (const unit of selected) {
    if (model.release_units[unit]?.release_record !== 'required') {
      throw new Error('Package submission requires release_record: required for every selected unit');
    }
  }
  if (!['true', 'false'].includes(env.PACKAGE_DRY_RUN)) throw new Error('Package dry-run choice is required');
  const unit = env.PACKAGE_UNIT;
  if (!selected.includes(unit)) throw new Error('Package matrix unit is outside the selection');
  if (!env.RELEASE_UNITS.split(' ').includes(unit)) return;
  const release = JSON.parse(env.RELEASE_JSON)[unit];
  if (!release) throw new Error('Package release record is missing');
  const stable = /^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$/;
  if (!stable.test(release.version) || release.tag !== release.version) {
    throw new Error('Package publisher requires a stable bare SemVer tag equal to the version');
  }
  const {data: record} = await github.rest.repos.getReleaseByTag({...context.repo, tag: release.tag});
  if (record.draft !== false || record.tag_name !== release.tag) throw new Error('Published package release record is required');
  const assets = await github.paginate(github.rest.repos.listReleaseAssets, {...context.repo, release_id: record.id, per_page: 100});
  const manifests = assets.filter(asset => asset.name === env.PACKAGE_MANIFEST_ASSET);
  if (manifests.length !== 1) throw new Error('Exactly one package selection manifest asset is required');
  const asset = manifests[0];
  if (!/^sha256:[0-9a-f]{64}$/.test(asset.digest || '')) throw new Error('Package manifest asset needs a SHA256 digest');
  const response = await github.rest.repos.getReleaseAsset({...context.repo, asset_id: asset.id,
    headers: {accept: 'application/octet-stream'}});
  const bytes = Buffer.from(response.data);
  if ('sha256:' + crypto.createHash('sha256').update(bytes).digest('hex') !== asset.digest) {
    throw new Error('Package manifest download differs from the release-record digest');
  }
  const manifest = JSON.parse(bytes.toString('utf8'));
  if (manifest.tag !== release.tag || manifest.version !== release.version ||
      manifest.source?.commit !== env.RELEASE_SOURCE_SHA ||
      manifest.source?.repository !== `${context.repo.owner}/${context.repo.repo}`) {
    throw new Error('Package manifest identity differs from the released source');
  }
  const names = new Map(assets.map(item => [item.name, item.digest]));
  for (const item of manifest.artifacts) {
    if (names.get(item.filename) !== 'sha256:' + item.sha256) {
      throw new Error('Package artifact differs from the release record');
    }
  }
  const file = path.join(env.RUNNER_TEMP, `package-selection-${unit}.json`);
  fs.writeFileSync(file, bytes);
  core.setOutput('manifest', file);
  core.setOutput('tag', release.tag);
  core.setOutput('version', release.version);
  core.setOutput('assets', JSON.stringify(assets.map(({name, digest}) => ({name, digest}))));
  if (env.PACKAGE_DRY_RUN === 'true') {
    core.info('Dry run: validated package selection; no submission or App token requested');
    return;
  }
  core.setOutput('submit', 'true');
}
await preparePackages({github, context, core, env: process.env, fs: require('node:fs'),
  crypto: require('node:crypto'), path: require('node:path')});
