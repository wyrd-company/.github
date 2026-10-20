// relationships: implements: release-process
// Kept in the workflow, so a source checkout need not contain template helpers.
function npmCommand(platform, executable, envPath = process.env.PATH) {
  const path = require('node:path');
  const fs = require('node:fs');
  // github-script's own Node executable is separate from setup-node's npm.
  // Windows cannot execute npm.cmd through execFileSync without a shell.
  if (platform !== 'win32') return ['npm'];
  const cli = envPath.split(';').map(directory => path.join(directory, 'node_modules/npm/bin/npm-cli.js'))
    .find(file => fs.existsSync(file));
  if (!cli) throw new Error('npm CLI is missing from the setup-node PATH');
  return [executable, cli];
}
async function npmRelease({env, verify, run, request = fetch, cwd = process.cwd()}) {
  const fs = require('node:fs');
  const path = require('node:path');
  const os = require('node:os');
  const crypto = require('node:crypto');
  const {execFileSync} = require('node:child_process');
  const command = npmCommand(process.platform, process.execPath);
  run ||= (args, options) => execFileSync(command[0], [...command.slice(1), ...args],
    {encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'], ...options});
  const model = JSON.parse(env.NPM_MODEL);
  const releases = JSON.parse(env.RELEASE_JSON);
  const released = new Set(env.RELEASE_UNITS.split(' '));
  const selected = env.NPM_UNITS.split(' ');
  const dry = env.NPM_DRY_RUN === 'true';
  const packages = [];
  // Validate the complete selection before any pack, install, or registry call.
  for (const id of selected) {
    const unit = model.release_units[id];
    if (!unit || unit.release_record !== 'required') throw new Error('npm requires a release record: ' + id);
    const projections = unit.projections.filter(p => p.adapter === 'npm' && p.generic === false);
    if (!projections.length) throw new Error('No npm package projection: ' + id);
    if (!released.has(id)) continue;
    const release = releases[id];
    if (!release || !release.version) throw new Error('Missing resolved npm release: ' + id);
    for (const projection of projections) {
      const file = path.resolve(cwd, projection.file);
      const manifest = JSON.parse(fs.readFileSync(file, 'utf8'));
      if (!manifest.name || manifest.private === true) throw new Error('npm package must be named and publishable: ' + id);
      if (projection.native_identity && manifest.name !== projection.native_identity) throw new Error('npm package identity mismatch: ' + id);
      if (projection.mode !== 'injected' && manifest.version !== release.version) throw new Error('npm package version mismatch: ' + id);
      if (packages.some(p => p.name === manifest.name)) throw new Error('Duplicate npm package: ' + manifest.name);
      packages.push({name: manifest.name, version: release.version, prerelease: release.prerelease,
        directory: path.dirname(file), injected: projection.mode === 'injected'});
    }
  }
  if (env.NPM_DRY_RUN !== 'true' && env.NPM_DRY_RUN !== 'false') throw new Error('Missing npm dry-run choice');
  const registry = 'https://registry.npmjs.org';
  for (const pkg of packages) {
    const scratch = fs.mkdtempSync(path.join(os.tmpdir(), 'release-npm-'));
    try {
      if (verify) {
        if (dry) continue;
        run(['install', '--prefix', scratch, '--no-package-lock', '--no-audit', '--no-fund',
          '--registry=' + registry, pkg.name + '@' + pkg.version], {cwd: scratch});
        const installed = JSON.parse(fs.readFileSync(path.join(scratch, 'node_modules', pkg.name, 'package.json'), 'utf8'));
        if (installed.name !== pkg.name || installed.version !== pkg.version) throw new Error('npm client installed a different package');
        continue;
      }
      let source = pkg.directory;
      if (pkg.injected) {
        source = path.join(scratch, 'source');
        fs.cpSync(pkg.directory, source, {recursive: true, filter: file => path.basename(file) !== '.git'});
        const manifestPath = path.join(source, 'package.json');
        const manifest = JSON.parse(fs.readFileSync(manifestPath, 'utf8'));
        manifest.version = pkg.version;
        fs.writeFileSync(manifestPath, JSON.stringify(manifest, null, 2) + '\n');
      }
      const packed = JSON.parse(run(['pack', source, '--json', '--ignore-scripts', '--pack-destination', scratch], {cwd}));
      if (packed.length !== 1 || packed[0].name !== pkg.name || packed[0].version !== pkg.version) throw new Error('npm pack identity mismatch');
      const archive = path.join(scratch, path.basename(packed[0].filename));
      const integrity = 'sha512-' + crypto.createHash('sha512').update(fs.readFileSync(archive)).digest('base64');
      const args = ['publish', archive, '--registry=' + registry, '--access=public', '--provenance', '--ignore-scripts'];
      // Leave stable tags implicit so npm can refuse backward latest moves.
      if (pkg.prerelease) args.push('--tag=next');
      if (dry) {
        run([...args, '--dry-run'], {cwd: scratch});
        continue;
      }
      const url = registry + '/' + encodeURIComponent(pkg.name) + '/' + encodeURIComponent(pkg.version);
      async function lookup() {
        const response = await request(url);
        if (response.status === 404) return false;
        if (response.status !== 200) throw new Error('npm registry lookup failed: HTTP ' + response.status);
        const remote = await response.json();
        if (remote.name !== pkg.name || remote.version !== pkg.version || remote.dist?.integrity !== integrity) {
          throw new Error('npm version exists with different package bytes: ' + pkg.name + '@' + pkg.version);
        }
        return true;
      }
      if (await lookup()) continue;
      // A concurrent publisher can win between lookup and publish. Accept only
      // the same immutable bytes; otherwise preserve the publication failure.
      try { run(args, {cwd: scratch}); }
      catch (error) { if (await lookup()) continue; throw error; }
      if (!await lookup()) throw new Error('npm publication is not observable yet; rerun this source commit');
    } finally {
      fs.rmSync(scratch, {recursive: true, force: true});
    }
  }
}
if (typeof module !== 'undefined') module.exports = {npmRelease, npmCommand};
