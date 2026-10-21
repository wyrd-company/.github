// relationships: validates: release-process
// Exercise official Renovate selection, npm extraction and package-rule ownership offline.
import fs from 'node:fs';
import path from 'node:path';
import {pathToFileURL} from 'node:url';
const root = path.resolve(process.argv[2]);
const tool = pathToFileURL(path.join(process.env.RENOVATE_PACKAGE_DIR, 'dist/')).href;
const {init} = await import(tool + 'logger/index.js');
await init();
const {GlobalConfig} = await import(tool + 'config/global.js');
const {getConfig} = await import(tool + 'config/defaults.js');
const {validateConfig} = await import(tool + 'config/validation.js');
const {mergeChildConfig} = await import(tool + 'config/utils.js');
const {getFilteredFileList, getMatchingFiles} = await import(tool + 'workers/repository/extract/file-match.js');
const {extractPackageFile} = await import(tool + 'modules/manager/npm/extract/index.js');
const {applyPackageRules} = await import(tool + 'util/package-rules/index.js');
GlobalConfig.set({localDir: root});
const read = file => fs.readFileSync(path.join(root, file), 'utf8');
const repository = JSON.parse(read('renovate.json'));
const owner = JSON.parse(read('renovate-config.json'));
// The local owner preset is explicit. Remote preset resolution, updates and hosted scheduling are not exercised.
const validation = {repository: await validateConfig('repo', repository), owner: await validateConfig('repo', owner)};
const config = mergeChildConfig(mergeChildConfig(getConfig(), owner), repository);
const files = JSON.parse(read('files.json'));
const managerConfig = {...config, ...config.npm, manager: 'npm'};
const selected = getMatchingFiles(managerConfig, files);
const extracted = [];
for (const packageFile of selected) {
  const result = await extractPackageFile(read(packageFile), packageFile, managerConfig);
  if (!result) continue;
  const dependencies = [];
  for (const dep of result.deps) dependencies.push(await applyPackageRules({...config, ...dep, manager: 'npm', packageFile}));
  extracted.push({packageFile, npmLock: result.managerData.npmLock, dependencies});
}
console.log(JSON.stringify({toolVersion: JSON.parse(fs.readFileSync(path.join(process.env.RENOVATE_PACKAGE_DIR, 'package.json'))).version,
  validation, selected, filtered: getFilteredFileList(config, files), extracted, owner, repository}));
