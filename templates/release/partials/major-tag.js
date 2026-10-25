// relationships: implements: release-process
const {execFileSync} = require('node:child_process');
const release = JSON.parse(process.env.RELEASE_JSON)[process.env.MAJOR_TAG_UNIT];
if (!release) throw new Error('The selected major-tag unit has no resolved release');
const {version, tag} = release;
if (!/^[0-9]+\.[0-9]+\.[0-9]+$/.test(version)) {
  console.log(`Skipping major tag update for non-plain version ${version}.`);
  return;
}
const major = version.split('.')[0];
if (BigInt(major) === 0n) {
  console.log(`Skipping major tag update for pre-1.0 release ${tag}.`);
  return;
}
const git = args => execFileSync('git', args, {stdio: 'pipe'});
git(['-c', 'user.name=github-actions[bot]', '-c', 'user.email=github-actions[bot]@users.noreply.github.com',
  'tag', '-fa', major, process.env.RELEASE_SOURCE_SHA, '-m', `Update ${major} to ${tag}`]);
git(['push', 'origin', `refs/tags/${major}:refs/tags/${major}`, '--force']);
console.log(`Updated ${major} to ${tag} at ${process.env.RELEASE_SOURCE_SHA}.`);
