// relationships: implements: release-process
function checkCredentials(declarations, env) {
  const missing = declarations.filter(c => !env[c.name]);
  if (missing.length) {
    throw new Error('Missing credentials:\n' + missing.map(c => `${c.name}: ${c.purpose}`).join('\n'));
  }
}
