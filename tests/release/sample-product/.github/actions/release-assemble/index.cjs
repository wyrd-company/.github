// relationships: validates: release-process
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const checksums = {};
function read(directory) {
  for (const file of fs.readdirSync(directory, {withFileTypes: true})) {
    const name = path.join(directory, file.name);
    if (file.isDirectory()) read(name);
    else checksums[file.name] = crypto.createHash('sha256').update(fs.readFileSync(name)).digest('hex');
  }
}
read(process.env['INPUT_INPUT-DIRECTORY']);
const directory = process.env['INPUT_OUTPUT-DIRECTORY'];
fs.mkdirSync(directory, {recursive: true});
fs.writeFileSync(path.join(directory, 'aggregate.json'), JSON.stringify(checksums) + '\n');
