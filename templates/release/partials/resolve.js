// relationships: implements: release-process
// Included once in actions/github-script. Git is the workflow's build tool.
async function resolveRelease({github, context, core, env, git}) {
  const source = env.SOURCE_SHA;
  if (!/^[0-9a-f]{40}$/.test(source)) throw new Error('source-sha must be 40 lowercase hexadecimal characters');
  if (env.DISPATCH_REF !== `refs/heads/${env.DEFAULT_BRANCH}`) throw new Error('Dispatch must use the default branch');
  if (env.DISPATCH_SHA !== source) throw new Error('Dispatch SHA differs from source-sha');
  const head = git(['ls-remote', 'origin', `refs/heads/${env.DEFAULT_BRANCH}`]).trim().split(/\s+/)[0];
  if (head !== source) throw new Error('Default branch head differs from source-sha');
  const runs = await github.paginate(github.rest.actions.listWorkflowRuns, {
    ...context.repo, workflow_id: env.CI_WORKFLOW, head_sha: source,
    event: 'push', status: 'success', per_page: 100,
  });
  if (!runs.some(run => run.head_sha === source && run.event === 'push' && run.conclusion === 'success')) {
    throw new Error('No successful push CI run for source-sha');
  }
  const model = JSON.parse(env.RELEASE_MODEL);
  for (const [unit, value] of Object.entries(model.release_units)) {
    for (const [stream, tag] of Object.entries(value.tags)) {
      if (tag.role === 'primary' && tag.require_phase === 'after-publication') {
        throw new Error(`${unit}/${stream}: primary tag requires after-publication; create it before dispatch`);
      }
    }
  }
  git(['fetch', '--force', 'origin', 'refs/tags/*:refs/tags/*']);
  const tags = git(['for-each-ref', '--format=%(refname:strip=2)', 'refs/tags']).trim().split('\n').filter(Boolean);
  const releases = {};
  const skipped = [];
  for (const name of tags) {
    const ref = `refs/tags/${name}`;
    if (git(['cat-file', '-t', ref]).trim() !== 'tag') continue;
    if (git(['rev-parse', `${ref}^{}`]).trim() !== source) continue;
    const object = git(['cat-file', 'tag', ref]);
    const message = object.slice(object.indexOf('\n\n') + 2);
    if (message.split('\n')[0] !== 'intentional release record') continue;
    const fields = {};
    for (const line of message.split('\n').slice(1)) {
      const field = /^(tag-id|version|baseline): (.*)$/.exec(line);
      if (!field) continue;
      if (Object.hasOwn(fields, field[1])) throw new Error(`${name}: duplicate ${field[1]}`);
      fields[field[1]] = field[2];
    }
    const identity = /^release-unit\/([^/]+)\/([^/]+)$/.exec(fields['tag-id']);
    if (!identity) continue;
    const [, unit, stream] = identity;
    const tag = model.release_units[unit]?.tags[stream];
    if (!tag || tag.role !== 'primary') continue;
    if (fields.baseline === 'true') { skipped.push(name); continue; }
    if (fields.baseline !== 'false') throw new Error(`${name}: baseline must be true or false`);
    const version = fields.version;
    if (!/^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?(\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$/.test(version || '')) {
      throw new Error(`${name}: invalid version`);
    }
    const expected = tag.template.replaceAll('{id}', unit).replaceAll('{version}', version);
    if (name !== expected) throw new Error(`${name}: primary tag must be ${expected}`);
    if (Object.hasOwn(releases, unit)) throw new Error(`${unit}: multiple primary release records`);
    releases[unit] = {tag: name, version, prerelease: version.split('+')[0].includes('-')};
  }
  await core.summary.addRaw(`Skipped baseline tag records: ${skipped.join(', ') || '(none)'}`).write();
  const units = Object.keys(releases);
  if (!units.length) throw new Error(`No primary release records; skipped baseline tag records: ${skipped.join(', ') || '(none)'}`);
  const recordUnits = units.filter(unit => model.release_units[unit].release_record === 'required');
  for (const [key, value] of Object.entries({release: 'true', source_sha: source,
    units: JSON.stringify(units), releases: JSON.stringify(releases),
    record_units: JSON.stringify(recordUnits), record: String(recordUnits.length > 0)})) {
    core.setOutput(key, value);
  }
}
await resolveRelease({github, context, core, env: process.env,
  git: args => require('node:child_process').execFileSync('git', args, {encoding: 'utf8'})});
