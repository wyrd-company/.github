// relationships: validates: release-process
const fs = require('node:fs');
const path = require('node:path');
const env = process.env;
if (env.EXAMPLE_FAIL_BUILD === 'true') throw new Error('Example build failed');
const directory = env['INPUT_OUTPUT-DIRECTORY'];
fs.mkdirSync(directory, {recursive: true});
if (env.EXAMPLE_EMPTY_OUTPUT !== 'true') fs.writeFileSync(path.join(directory, env.INPUT_TARGET + '.txt'),
  `Example output for ${env.INPUT_UNIT}/${env.INPUT_TARGET}\n`);
