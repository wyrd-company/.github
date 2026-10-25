// relationships: validates: release-process
const fs = require('node:fs');
const env = process.env;
const receipt = Object.fromEntries(['unit', 'matrix-entry', 'tag', 'version', 'prerelease']
  .map(key => [key, env['INPUT_' + key.toUpperCase()]]));
fs.appendFileSync(env.EXAMPLE_RECEIPT, JSON.stringify(receipt) + '\n');
if (env.EXAMPLE_FAIL_VERIFICATION === 'true') throw new Error('Example verification failed');
