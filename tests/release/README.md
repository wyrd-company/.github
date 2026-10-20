<!-- relationships: validates: release-process -->

# Release template validation

Use Python 3, Node.js, Git, `task`, `ryl`, `actionlint`, and the Python packages
in `requirements.txt`. Build Toha from the recorded composition commit and make
that binary available through `TOHA_BIN`. `INTENTIONAL_DIR` names a checkout
that contains the schema's recorded source commit.

```sh
python3 -m venv .venv
.venv/bin/pip install -r tests/release/requirements.txt
export PATH="$PWD/.venv/bin:$PATH"
export TOHA_BIN=/path/to/toha
export INTENTIONAL_DIR=/path/to/intentional
task ci
python3 tests/release/mutations.py --report /tmp/release-mutations.json
```

The Toha build used for the document and rendering contract is commit
`82e39d485f20729d38aa9b550b445edf58c61708`. The harness removes `parts: true`
from test copies for that build and supplies the selection through the ordinary
multiselect answer. It does not prove `--with`, `--without`, recorded `parts`,
or update reconciliation. Those require the parts/update implementation and
its adoption tests. Production `template.yml` keeps `parts: true`.

The Python suite renders with Toha and parses YAML with comments and duplicate
key detection. It checks every registered part separately and all selected
parts together, then validates the jobs and credential declarations. It also
executes the emitted `github-script` entry points with real Git repositories and
a fake GitHub REST boundary, with and without release assets. The Node suite
exercises the runtime guards and retry states. The proxy test observes zero
network attempts during vendored-schema resolution; removing the vendored root
must fail that observation. These tests do not measure GitHub job scheduling.

`schema-provenance.json` beside the template records the vendored model source.
The copy must be byte-identical to the named source path. Refresh from the first
Intentional release tag that names line 1 and update the provenance and test
source together. Keep the source commit available in `INTENTIONAL_DIR`.

## Add a publication part

Use `sample-target/` as a fixture for the interface. Add the target directory to
`release/targets/`, load `target.yml` under `data.release_targets` with `!include`,
and add its literal name to `targets.options`. Add its units answer and other
questions after `targets`, each with a `when` condition on that selection.
Target metadata contains `units` (the units answer id), stage lists `build`,
`publish`, and `verify`, and the `credentials` map.

Every credential `unless` selector names a boolean answer. Add that answer to
the `credential_answers` computed map so the parent can resolve the selector.
Names and declarations are data; never put credential values in answers.
Add `tests/release/part-answers/<target>.json` with explicit units and boolean
selector answers for the per-part and combined interface tests. The common
model uses the generic units `sample-tool` and `sample-docs`.

`partials/parts.yml` renders once for each `stage`, in parent stage order. Add
literal includes inside the target and stage conditions, capture the job body
and optional environment partial, then call the parent's macro:

```jinja
{% if 'sample-target' in targets and stage == 'publish' %}
{% set body %}
{% include 'targets/sample-target/publish-sample.yml' %}
{% endset %}
{% set extra_env %}
{% include 'targets/sample-target/publish-sample.env.yml' %}
{% endset %}
{{ ns.part_job('publish-sample', sample_target_units,
               'publish', body, extra_env) }}
{% endif %}
```

The macro's optional last argument is the extra bare `if` expression. It can
narrow the parent gate. The harness extracts it from the rendered condition and
validates it against the part-job schema, including case-insensitive status
function refusal. It validates the body after removing parent-owned fields;
a duplicate parent field fails YAML parsing. External `uses` references retain
their version comments. A model or declaration serialized into script data
escapes dollar signs as JSON Unicode escapes so GitHub cannot evaluate data as
an expression. Scripts use environment values.

A build that provides assets uploads workflow artifacts named
`release-asset+<unit>+<name>`. Files in those artifacts become release assets by
basename. Duplicate basenames, links, and a supplied `SHA256SUMS` fail; the
parent produces `SHA256SUMS`. A unit with no matching artifacts expects no
release assets. The parent creates a draft, uploads, publishes, and reads back
by tag. On retry it replaces expected draft assets, rejects unexpected draft
assets, and accepts a matching published release unchanged.

## Live proof procedure

Use an authorized, disposable GitHub test repository with Actions enabled and
the default branch `main`. Do not create one as part of local tests. Use dummy
values, never registry credentials. Configure repository variable
`SAMPLE_CHANNEL=sample`, environment `sample`, and environment secret
`SAMPLE_ENV_TOKEN=present`. Set repository secret `SAMPLE_TOKEN=present` except
for the `missing-credential` case. The fixture jobs publish no registry package.

Use a clean repository/tag for each case, or have the authorized operator remove
its old releases and tag before reusing it. The preparer refuses to overwrite
files and the seeder refuses to replace a release. Keep all receipts outside
the test repository so the source commit stays fixed.

1. Prepare a case. Run from this repository with `TOHA_BIN` set:

   ```sh
   python3 tests/release/live.py prepare \
     --output /tmp/release-case --scenario skipped-build --assets
   ```

2. Copy `/tmp/release-case/project/` into the test checkout, including hidden
   files. Commit `.intentional/release-model.json`, the workflows, changelog,
   and `.live-case.json`, then push `main`. No publication target is installed.

3. Create the annotated tag on that source commit, then push it. Use `true`
   for the baseline case and `false` for every other case:

   ```sh
   git tag -a sample-tool-1.2.3 -F - <<RECORD
   intentional release record

   contract: contract-1
   generator: intentional 0.3.0
   plan-digest: sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
   tag-id: release-unit/sample-tool/primary
   version: 1.2.3
   baseline: false
   target: $(git rev-parse HEAD)
   RECORD
   git push origin refs/tags/sample-tool-1.2.3
   ```

4. Wait for successful push CI on the exact source SHA. From the template
   checkout, seed the case when it needs an existing release:

   ```sh
   python3 tests/release/live.py seed --repo example-org/sample-tool \
     --case /path/to/test-checkout/.live-case.json
   ```

   For `published-expected`, capture `gh api
   repos/example-org/sample-tool/releases/tags/sample-tool-1.2.3` before and
   after the run. Compare release id, `updated_at`, prerelease flag, and asset
   ids/names to prove no write occurred. An `expected-draft` with no build
   assets is an empty draft: its expected-name set is empty. Record that fact
   instead of inventing an expected asset.

5. Dispatch `release.yml` on `main`, passing its current source SHA:

   ```sh
   gh workflow run release.yml --repo example-org/sample-tool --ref main \
     -f source-sha="$(git -C /path/to/test-checkout rev-parse HEAD)"
   ```

6. Once the run is complete, use its numeric run id:

   ```sh
   python3 tests/release/live.py check --repo example-org/sample-tool \
     --case /path/to/test-checkout/.live-case.json --run-id 123
   ```

   The checker requires the exact workflow and every expected job conclusion,
   and reads every successful release back by tag. It prints `PASS` only after
   all checks finish. Save the output, workflow file, run URL, source SHA, and
   job timestamps. For the empty publish stage, compare verify start times with
   every build and release-record completion time. For missing credentials,
   confirm no build starts. For every failed record, confirm publish and verify
   are skipped. A two-release seed refused by GitHub is not a measured run;
   record the raw API refusal and leave that case unverified.

Run `skipped-build`, `empty-publish`, `empty-publish-failed-build`, `record-gate`,
`baseline`, and `missing-credential`. Run each of the following both with and
without `--assets`: `no-release`, `empty-draft`, `expected-draft`,
`unexpected-draft`, `published-expected`, `published-wrong-prerelease`,
`published-wrong-assets`, and `two-releases`. The preparer has local tests for
all 28 case/asset combinations; live outcomes require actual run receipts.

Also rerun a case after a partial draft upload. With the operator's read-only
Airlock credentials configured, run the live organization policy at the source
revision and save the complete output:

```sh
airlock audit example-org/sample-tool --ref SOURCE_SHA --format json \
  > /tmp/release-airlock.json
```

Inspect `REPO-REL-04`, `REPO-REL-05`, `REPO-REL-06`, and `REPO-CI-04` in that
output. An unobserved or routed result is not a pass.
Toha update cases for preserved/equal/conflicting Renovate lines and
unavailable/mismatched merge bases are proved with the parts/update dependency.
