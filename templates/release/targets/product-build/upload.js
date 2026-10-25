// relationships: implements: release-process
const fs = require('node:fs');
const path = require('node:path');
const {pathToFileURL} = require('node:url');
const directory = process.env.ASSET_DIRECTORY;
const files = fs.existsSync(directory) ? fs.readdirSync(directory, {withFileTypes: true}) : [];
if (!files.length) throw new Error('if-no-files-found: error: recipe output directory is empty');
const {DefaultArtifactClient} = await import(pathToFileURL(path.join(
  process.env.ARTIFACT_CLIENT_DIRECTORY, 'node_modules/@actions/artifact/lib/artifact.js')).href);
const client = new DefaultArtifactClient();
for (const file of files) {
  if (!file.isFile()) throw new Error('Recipe output directory must contain regular files');
  await client.uploadArtifact(`release-asset+${process.env.ASSET_UNIT}+${file.name}`,
    [path.join(directory, file.name)], directory);
}
