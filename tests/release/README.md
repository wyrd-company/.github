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

The Toha build used for the document, part selection and update contract is
commit `8f148040130563ce897e4955dfdef9ae0597eae7`. Build it in an isolated clone
with `cargo build --release`; do not build in the shared composition worktree.
The harness retains `parts: true` and selects parts with `--with`. No marker
adaptation is needed. Production targets remain optional; `prepare` registers
the generic fixture parts. The update suite commits that complete template to
its own Git source repository before resolving a `file://` address.

`test_toha_updates.py` proves the real CLI, record and output boundaries:

- `test_apply_selection_and_revision_update`: apply flags, a new committed
  revision, additions/removals, option order, separate recorded parts/answers,
  emitted jobs/credentials, and an already-current repeat.
- `test_staged_document_answer_and_update_freeze`: a document question answered
  with the committed Release Model through `continue --input`, fresh stage
  flags, repeated staged update flags, frozen admitted documents and a later
  update that admits the current file. The generic projection boolean survives.
- `test_invalid_selection_is_usage_error_before_writes`: apply/stage, fresh and
  update routes reject unknown or contradictory flags with exit 2, the named
  diagnostic and unchanged output/record. A parts answer cannot accompany flags.
- The pin tests prove unchanged/equal/divergent template and operator edits.
  Part removal retains an unrelated operator annotation and pin. A text conflict
  has diff3 markers and retains the exact operator file in index stage 2;
  add/add conflicts retain operator bytes in the working tree.
- The missing commit and unversioned-source tests refuse with exit 1 and name
  `--baseline`. The explicit baseline test uses an empty base and conflicts
  against edited operator output. Missing ownership bytes produce add/add;
  a mismatched reconstructed render uses only the retained recorded blob.

Set `RELEASE_TOHA_RECEIPTS=/absolute/path` to save each proof command's arguments,
exit, stdout and stderr. Run `python3 tests/release/toha_mutations.py --report
/path/to/report.json` for the helper's marker/selection sole-kills. The main
mutation battery continues to cover the reusable part interface. Run the Toha
consumer suites at the same source head with `cargo test --release --test parts
--test applied_record --test document_questions`.

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

## Product build and assembly evidence

`test_product_build.py` uses real Toha renders to validate the assembly key
pattern and existing job schema, all earlier-stage dependencies, optional and
empty stages, selection, local recipe inputs and output transport. Existing
parts keep their interfaces and permissions. `stage-conditions.cjs` evaluates
the emitted expression subset for success, skipped, failure and cancellation
results, with independent cancellation, failure, resolve and preflight guards.
This is a local truth table, not a GitHub scheduler implementation.

`sample-product/` contains two small JavaScript local actions. They write two
target text files and an aggregate of their checksums without a compiler.
`product-build-rendered.cjs` simulates the emitted matrix, per-file upload and
same-run download, then executes the actual emitted release-record entry point
with fake GitHub REST. It checks every attached checksum against the bytes and
the independently produced aggregate. Failed/empty builds create no record;
unreleased units run no recipe. Dependency provisioning is asserted but makes
no network request in the fixture; the SDK and REST transport boundaries are
fake. No hosted upload or publication is claimed.

Set `RELEASE_PRODUCT_RECEIPTS=/absolute/path` to retain the truth table and
artifact/record receipts. Named controls remove assembly build dependencies,
resolve/preflight guards, and separately the generic part's exact permission
and no-credential checks. The full mutation battery is a separate command;
`mutations.inventory()` checks literal site counts without running that battery.

## Mutation sites and reports

`mutations.py` inventories every row before writing source. A row declares one
named killing test per expected text occurrence, in source order. Add a test
entry for each repeated site; never select only the first occurrence. Each site
runs independently and restores the source before the next site. Zero matches,
unexpected match counts, an empty inventory, a surviving site, or an interrupted
run cannot report `PASS`.

The JSON report starts as `INCOMPLETE`. Its inventory and results identify the
row and occurrence (`row:occurrence`), file, line, character offset, expected and
observed match counts, expected and actual application count, named test, exit
code, output, and original, mutated, and restored SHA256 values. Each application
must change exactly one site. A named assertion failure counts as a kill; a test
loader error does not. The final report requires every inventoried site to be
killed and restored at the same Git head. `test_mutations.MutationRunner` covers
repeated sites, unexpected counts, interruption, restoration, and surviving sites.

The dollar-escape row has three tests: `test_resolve_model_data_stays_literal`,
`test_preflight_credential_data_stays_literal`, and
`test_record_job_model_data_stays_literal`. They render GitHub-expression text
inside model and credential-purpose data and check its literal value while
parent-owned expressions remain. The schema row uses separate
`test_step_if_expression_guard` and `test_run_expression_guard` tests.

## Add a publication part

Use `sample-target/` as a fixture for the interface. Add the target directory to
`release/targets/`, load `target.yml` under `data.release_targets` with `!include`,
and add its literal name to `targets.options`. Add its units answer and other
questions after `targets`, each with a `when` condition on that selection.
Target metadata contains `units` (the units answer id), stage lists `build`,
optional `assemble`, `publish`, and `verify`, and the `credentials` map.

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
checks balanced parentheses outside GitHub expression strings, then validates
it against the part-job schema, including case-insensitive status function
refusal. Strings use single quotes; a doubled single quote escapes a single
quote. Backslashes are literal. Parentheses inside strings do not affect
grouping. Nested groups are accepted; an early closing parenthesis, a missing
closing parenthesis, or an unterminated string fails validation. This is a
grouping check, not a complete GitHub expression parser.
`test_unbalanced_condition_rejected_at_rendered_boundary` rejects
`false) || (true`; `test_valid_nested_and_quoted_conditions_render` checks valid
nested and quoted conditions with actionlint. `test_conditions.PartConditions`
covers individual grouping and string errors.

Validate a rendered workflow with settled answers (including `targets`, units,
and credential selectors) through the same boundary:

```sh
python3 tests/release/harness.py --template /path/to/release \
  --workflow /path/to/project/.github/workflows/release.yml \
  --answers /path/to/settled-answers.json
```

The answers file contains `{"answers": {"targets": ["sample-target"], ...}}`.
Invalid grouping returns nonzero with `Unbalanced part condition parentheses`;
an unterminated string reports `Unterminated part condition string`.
`test_condition_validation_command_rejects_unbalanced_part` proves the command
refusal at the rendered boundary. The harness validates the body after removing
parent-owned fields; a duplicate parent field fails YAML parsing. External
`uses` references retain their version comments. A model or declaration
serialized into script data escapes dollar signs as JSON Unicode escapes so
GitHub cannot evaluate data as an expression. Scripts use environment values.

A build that provides assets uploads workflow artifacts named
`release-asset+<unit>+<name>`. Files in those artifacts become release assets by
basename. Duplicate basenames, links, and a supplied `SHA256SUMS` fail; the
parent produces `SHA256SUMS`. A unit with no matching artifacts expects no
release assets. The parent creates a draft, uploads, publishes, and reads back
by tag. On retry it replaces expected draft assets, rejects unexpected draft
assets, and accepts a matching published release unchanged.

## Live proof procedure

All live commands below require root admission for the named disposable test
repository, dummy setup, owned resources and exact source. Offline preparation
and checking do not grant permission to seed, push, dispatch, rerun, audit or
change settings. Preserve failed commands and partial state. Frozen private
container proof is historical evidence; it supplies no generic live result.

Use Actions on default branch `main`. Configure repository variable
`SAMPLE_CHANNEL=sample`, environment `sample`, and environment secret
`SAMPLE_ENV_TOKEN=present` with dummy values. Leave `SAMPLE_TOKEN` absent in
all effective scopes. Normal prepared cases omit its declaration through
`trusted_publishing`; `missing-credential` declares the absent token. The
fixture has no registry publication. A push, including a tag push, runs CI;
it does not trigger a release. Release uses explicit default-branch dispatch.

Use a clean repository/tag per case. Reuse requires the admitted operator to
inventory and remove only that case's owned resources. Never delete foreign
refs, Releases, assets or failed observations. `prepare` requires empty output;
`seed` refuses an existing same-tag Release. Evidence directories, receipts and
report paths must be new. Keep them outside the fixture source repository.

1. Prepare locally with the selected `TOHA_BIN`:

   ```sh
   python3 tests/release/live.py prepare \
     --output /tmp/release-case --scenario skipped-build --assets
   ```

   This renders two generic parts, adds a fixture-only dispatch identity log
   step, and writes the model, minimal genuine Intentional config, workflows,
   changelog and `.live-case.json`. The LIVE primary tag template is `{version}`
   and its case/seed/readback tag is `1.2.3`. Shared fixtures and production
   model-driven tag capability retain their supported forms. Config uses
   `https://intentional.foo/schemas/0.2/config`, `contract-1`, and the same units,
   tag templates and Release record declarations as the model. It chooses no
   Airlock policy and proves no audit capability. The config schema input is
   Intentional commit `acbb793427b8de35ca84c9a2e9f371b71738b230`, path
   `docs/specifications/release-workspace.schema.yml`.

2. After admission, install `/tmp/release-case/project/`, including hidden
   files, into the test checkout. Commit all fixture inputs and push `main`.
   Save the source commit, template source, actual renderer source/binary hash,
   Intentional schema source and rendered workflow bytes with the evidence.
   An old renderer receipt does not qualify a new render.

3. Create an annotated Intentional record on that exact fixture source. Use
   `baseline: true` only for `baseline`; use `false` otherwise:

   ```sh
   git tag -a 1.2.3 -F - <<RECORD
   intentional release record

   contract: contract-1
   generator: intentional 0.3.0
   plan-digest: sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
   tag-id: release-unit/sample-tool/primary
   version: 1.2.3
   baseline: false
   target: $(git rev-parse HEAD)
   RECORD
   git push origin refs/tags/1.2.3
   ```

   This is a fixture record, not a generated release plan or public release.
   Retain the real source/CI prerequisites; do not invent CI success. Wait for
   successful push CI on the exact full source SHA and save its numeric run ID.

4. Seed and retain the before/after state, original requests and results:

   ```sh
   python3 tests/release/live.py seed --repo example-org/sample-tool \
     --case /path/to/test-checkout/.live-case.json --output /tmp/case-seed
   ```

   Every case needs a seed receipt, including cases that require no Release.
   `seed.json` and `commands.jsonl` preserve exact duplicate-Release API refusal,
   failed uploads and partial state. A refused or impossible two-Release seed
   stays UNVERIFIED; do not dispatch it as a passing case. An `expected-draft`
   without assets is an empty draft. Snapshot includes complete same-tag
   inventory, exact-tag lookup, Release ID/`updated_at`/draft/prerelease and
   asset IDs/names/`updated_at`. A lookup error other than 404 is incomplete.

5. Dispatch only after admission and successful source CI:

   ```sh
   gh workflow run release.yml --repo example-org/sample-tool --ref main \
     -f source-sha="$(git -C /path/to/test-checkout rev-parse HEAD)"
   ```

   Preserve the dispatch request/result, actual workflow ID, run ID and explicit
   attempt. `gh workflow run` acceptance alone is not a measured run. After
   obtaining those actual IDs, bind an external receipt from the clean committed
   fixture checkout (numeric values below are examples):

   ```sh
   python3 tests/release/live.py bind --source /path/to/test-checkout \
     --repo example-org/sample-tool --run-id 123 --attempt 1 --workflow-id 456 \
     --dispatch-ref refs/heads/main --output /tmp/case-receipt.json
   ```

   Bind reads committed case and workflow bytes, records full source/workflow
   revision, workflow ID/ref/hash, dispatch ref/input, run ID/attempt and source
   commit/receipt times. It creates the receipt after the source commit; it
   never embeds a commit's own SHA into that commit. Keep source and evidence
   fixed. Each rerun/attempt needs a new receipt and output directory.

6. After the admitted attempt completes, collect its original observations:

   ```sh
   python3 tests/release/live.py observe \
     --case /path/to/test-checkout/.live-case.json \
     --receipt /tmp/case-receipt.json \
     --ci-run-id 789 --output /tmp/case-attempt-1
   ```

   `observe` uses `/actions/runs/ID/attempts/ATTEMPT` and that attempt's jobs
   endpoint, retains paginated jobs and raw logs, fetches workflow bytes at the
   observed run head, captures exact-source push CI and reads Release state.
   It never dispatches or writes Releases. Failed observation stays INCOMPLETE
   with original command exit/stdout/stderr. Do not substitute latest jobs or
   overwrite an earlier attempt. Historical evidence stays separately named.

7. Check entirely offline:

   ```sh
   python3 tests/release/live.py check --repo example-org/sample-tool \
     --case /path/to/test-checkout/.live-case.json --run-id 123 \
     --receipt /tmp/case-receipt.json \
     --evidence /tmp/case-attempt-1/evidence.json \
     --seed /tmp/case-seed/seed.json --report /tmp/case-report-1.json
   ```

   The checker requires matching event/source/ref/workflow ID/revision/bytes,
   dispatch input and explicit attempt. The fixture log marker reports only
   those identity fields; it never logs the whole GitHub context or credentials.
   Every expected job must be observed with its distinct conclusion. Completed
   jobs need coherent job/step times and start evidence. A skipped missing-token
   build needs no steps and no assigned runner; an absent build cannot prove
   it never started. For empty publication, verification starts after every
   required build and Release record finishes. Published matching cases prove
   no-write through unchanged actual Release and asset metadata, including IDs
   and timestamps. Missing/contradictory evidence fails nonzero without PASS.
   A fresh report starts INCOMPLETE before inputs load; failure is UNVERIFIED.
   Existing reports are never overwritten.

The required live matrix is six graph/gate cases (`skipped-build`,
`empty-publish`, `empty-publish-failed-build`, `record-gate`, `baseline`,
`missing-credential`) plus eight retry states with and without assets:
`no-release`, `empty-draft`, `expected-draft`, `unexpected-draft`,
`published-expected`, `published-wrong-prerelease`, `published-wrong-assets`,
`two-releases`. This is 22 required live cases, plus partial continuation.
The preparer tests cover all 28 scenario/asset combinations locally. Prepared
combinations and synthetic checker tests are not measured live results.

### Partial draft upload continuation

Under separate execution admission, retain the first failed workflow attempt,
its bound receipt, record-job failure logs and terminal Release snapshot. The
snapshot must contain one draft with a nonempty proper subset of expected
assets. Do not create a second Release or overwrite the first attempt. No
partial failure is induced by this harness; absence of a real failed attempt
leaves this live case UNVERIFIED.

Before the admitted continuation, collect a separate snapshot:

```sh
python3 tests/release/live.py snapshot --repo example-org/sample-tool \
  --case /path/to/test-checkout/.live-case.json --output /tmp/continuation-before
```

Retain the original seed receipt unchanged. Bind and observe the continuation
with its own full source/run/attempt identity. Create `/tmp/first-attempt.json`
outside source with two objects: `receipt` (first receipt JSON) and `evidence`
(first `evidence.json`). Check the continuation using its ordinary check
arguments plus `--before /tmp/continuation-before/snapshot.json` and
`--previous /tmp/first-attempt.json`. The checker requires the retained partial
state to agree with continuation's before state and requires publication of the
same Release ID with the full expected asset set. Missing, failed, mismatched
or reused continuation evidence fails; no real continuation remains UNVERIFIED.

### Audit evidence

Airlock executable provenance, suitable read-only profile, intended generation
and authoritative organization policy remain separate unresolved inputs.
Do not select or generate a policy from this fixture. After root admits those
inputs and the live audit, preserve complete output at the exact source:

```sh
airlock audit example-org/sample-tool --ref SOURCE_SHA --format json \
  > /tmp/release-airlock.json
```

Inspect `REPO-REL-04`, `REPO-REL-05`, `REPO-REL-06` and `REPO-CI-04` as actual
observations. Unbuilt, manual, routed, unobserved or unavailable checks remain
UNKNOWN/UNVERIFIED; they are not PASS. The harness supplies no audit outcome.
Toha update tests are local and need no live repository.
