<!-- relationships: implements: release-process -->

# Publication targets

Publication parts live here. The repository test fixture in
`tests/release/sample-target/` demonstrates the four internal interfaces.
Run `task test` after adding a part; the harness tests each part alone and all
parts together, including credential labels and job ids.

## crates.io

Select `--with crates-io`, then answer `crates_io_units` with the Release Unit
ids separated by spaces. Each selected released unit must have a required
release record and at least one active, non-generic Cargo projection. Each
projection's `file` identifies one crate manifest, including each crate of a
multi-crate unit. Cargo metadata supplies native package names and normal/build
path dependency order across all selected released units. Development edges do
not constrain publication order.

Configure repository or organization secret `CARGO_REGISTRY_TOKEN` for the
crates.io account that owns the crates. The parent preflight checks its presence.
This part uses the token path, including first publication; configuring trusted
publishing is an adoption concern. The job requests only `contents: read` and
runs after the parent's release-record gate. No dry-run path invokes it.

A retry packages the same source with Cargo and compares its SHA256 with the
crates.io version API checksum, including an accepted submission still waiting
for the sparse index. Matching bytes succeed; different bytes fail. A new
submission is confirmed with the same checksum. A Cargo index-wait failure
stops dependent crates; rerun after the registry has indexed the submission.
Failed Cargo publication emits its captured stdout and stderr. Failed version
lookups report the numeric HTTP status without the server reason or response body.
The part adds no polling or timeout mechanic. Cargo's own indexing wait applies.

Evidence: `CratesPart.test_selected_and_deselected_contracts`,
`CratesPublisher.test_multi_crate_unit_dependency_order`,
`CratesPublisher.test_matching_published_or_submitted_retry`,
`CratesPublisher.test_different_published_artifact_refused`,
`CratesPublisher.test_index_wait_failure_stops_dependents_then_retry_adopts_submission`,
and `CratesPublisher.test_real_cargo_fresh_checkouts_reproduce_package_checksum`.
Live workflow proof requires the authorized test repository tracked by the
parent's live-proof work. No local test publishes to a public registry.

## npm

Select `--with npm`. `npm_units` lists the Release Units served, separated by
spaces. Each selected unit must require a release record and contain an npm
projection (`adapter: npm`, `generic: false`); the projection's `file` locates
its `package.json`, including launchers for non-Node binaries. A unit without a
release record is refused before packaging or registry calls. Generic JSON
projections are not npm packages. Package names follow the manifest and, when
present, the projection's `native_identity`.

`npm_trusted_publishing` defaults to true. Configure npm's trusted publisher
for `.github/workflows/release.yml` on GitHub-hosted runners. A package must
already exist before its trusted publisher can be configured; first publication
needs the operator's granular token. With trusted publishing off, declare the
repository secret `NPM_TOKEN`. Preflight checks its presence. No token is
requested or emitted with trusted publishing on. Both paths request
`id-token: write` and publish with provenance. The current npm client is
installed on Node 24 for OIDC support. Registry evidence:
[trusted publishing](https://docs.npmjs.com/trusted-publishers/).

`npm_dry_run` defaults to false. Set it to true for local preparation or a live
workflow proof without public publication. It adds npm's `--dry-run`, makes no
registry writes, and skips client installation. npm itself can read registry
metadata, authenticate, and refuse an already-published version. Actual
publication remains behind the parent's resolved Intentional tag record and successful release
record. Run `intentional apply` only with the user's release approval.

Packaging uses `npm pack --ignore-scripts`: commit ready-to-pack files, including
launcher code and binary payloads. Build/prepare scripts do not run. An injected
projection stamps the resolved version in a temporary copy; committed and none
projections must already match. Publish uses that exact packed archive and
checks npm's SHA512 integrity, name and version. A retry accepts identical bytes
and refuses conflicting bytes. A concurrent matching publication is accepted.
If npm has not exposed a successful write yet, the job fails and can be rerun
on the same source commit; there is no polling timer. Prereleases use `next`;
stable versions omit an explicit tag so npm's default backward-move check can
apply. Explicit npm configuration, including a manifest's `publishConfig.tag`,
can override that default.

Clean clients install the exact package version on Linux amd64/arm64, macOS
arm64 and Windows amd64, then check the installed manifest identity. Install
scripts run, so binary launchers can fetch their release assets. Product-specific
CLI checks belong to adoption. Named local tests use real npm packs and a fake
registry boundary; they make no public publication. Live GitHub run receipts
remain pending the authorized test repository.

## GHCR

Select `--with ghcr`; `ghcr_units` lists Release Units separated by spaces.
Each selected unit must require a release record. Its `path` holds the build
context and `Dockerfile`. Use scratch or a similar minimal runnable final image;
the Dockerfile supplies the executable, entry point and dependencies. Both
Linux amd64 and arm64 are built. Pin build inputs and keep builds reproducible.
The parent validates unit identities at render time. Runtime refuses a missing
Dockerfile before any builder, login or registry call.

Images use `ghcr.io/<repository>/<unit-id>:<version>`, with the repository name
lowercased and a bare SemVer version. OCI tags cannot contain `+`, so versions
with SemVer build metadata fail before any tool runs. The source checkout must
match the resolved source commit. The build receives its commit time as
`SOURCE_DATE_EPOCH`, rewrites layer timestamps, disables generated attestations,
and labels source, revision and version. The Dockerfile must also produce stable
bytes; a changed base image or nondeterministic build causes a retry conflict.

`ghcr_dry_run` defaults to false. Set it to true to build local OCI archives
without registry lookup, login or push. The job uses automatic `GITHUB_TOKEN`,
with `packages: write` only on `publish-ghcr`; no operator secret is declared.
Publication stays behind the parent's successful release-record gate. Nothing
in the tests publishes to a public registry.

The job installs Skopeo and uses Docker Buildx with an isolated container
builder. It compares the existing version's exact manifest digest, accepts a
matching retry and refuses different bytes before any push. Only an explicit
`manifest unknown` lookup response permits publication; authentication and
transport errors fail closed. It copies the OCI archive to its digest reference
with all architectures and preserved digests, reads that digest back, checks
for a concurrent tag, then sets and reads back the version tag. A failed tag
write can be retried from the same source commit. There is no polling timer.
Registry tags are mutable; the parent serializes this workflow's releases.
External writers must not move a release tag concurrently.

GHCR creates new packages as private. The source label links the package before
its first publication for repository access inheritance. For a public repository,
the operator makes the package public in GitHub's UI at adoption; the workflow
does not change visibility. For a private repository, keep the package private.
Confirm inherited repository access at adoption, including organization settings
that disable inheritance. See the registry evidence and
[GitHub's access and visibility documentation](https://docs.github.com/en/packages/learn-github-packages/configuring-a-packages-access-control-and-visibility).

Evidence: `GhcrPart.test_selected_and_deselected_contracts`,
`GhcrPart.test_emitted_entrypoint`, `GhcrPublisher.test_fresh_and_matching_retry`,
`test_conflict_before_push`, `test_partial_digest_retry`,
`test_digest_readback_before_tag`, `test_registry_errors_fail_closed`,
`test_missing_dockerfile_before_any_tool`, and `test_dry_run_no_login_or_registry`.
These named tests use a transport boundary and make no public publication.
`python3 tests/release/ghcr_oci_proof.py --scratch /absolute/new/directory
--report /absolute/receipt.json` runs the opt-in real-tool proof: two fresh
snapshots with different file times produce the same multi-platform OCI digest,
Skopeo preserves it through a loopback-only registry, and the scratch entry
point runs on amd64. It needs Docker, Buildx and Go; it pulls standard tool
images but writes no public registry. The receipt starts incomplete and passes
only after every assertion and cleanup succeeds.
The authorized GitHub test repository is still required for a live workflow
run and GHCR adoption proof.

## Homebrew tap

Select `--with homebrew-tap`. Answer `homebrew_tap_units` with space-separated
Release Unit ids, and `homebrew_tap_repository` with the tap's `owner/repository`.
The tap must be another repository. Each selected unit must require a release
record. The part reads only the selected units released by the dispatch and
runs after the parent's successful release record.

The adopting repository's build uploads `<unit>.rb` as a release asset using
the parent's `release-asset+<unit>+<name>` artifact convention. The part adds
no generator command or hooks. Prepare a complete formula with one explicit
`version "<SemVer>"` line (no `v`), literal double-quoted `url` lines pointing
at that unit's source repository and resolved release tag, and a final newline.
The parent includes the formula in `SHA256SUMS`. The part downloads both assets
through the source repository API and checks the formula checksum and version
before any tap commit. Formula syntax, archive checksums, installation, and
product CLI checks belong to adoption.

Declare the repository or organization secret `HOMEBREW_TAP_TOKEN` as a
pre-minted GitHub App installation token scoped to the answered tap repository,
with **Contents: write** there. Supply a fresh token for the run; this part does
not mint or refresh it. Preflight checks presence only. Token scope and tap
branch rules must permit direct Contents API commits. The source repository's
`GITHUB_TOKEN` has only `contents: read`; it reads the release assets. The tap
token is used only for tap API operations. There is no publisher OIDC flow.

`homebrew_tap_dry_run` defaults to false. With true, the part validates the same
assets and prints a full formula diff against the tap, with zero write calls.
It still needs both read credentials. With false, it commits
`Formula/<unit>.rb` to the tap's default branch through the Contents API,
using the observed blob SHA for an existing formula. It reads the committed
bytes back. Identical retries succeed without a write; a current formula with
the same explicit version and different bytes fails. A lower version is refused
by SemVer precedence, including prerelease identifiers; build metadata does not
affect precedence. Existing tap formulas
must have an explicit version stanza too. An API race succeeds only when the
observed bytes already match; other failures remain errors and can be retried
on the same source commit. Each unit is one commit; a retry preserves units
that already match. No polling or new timeout is added.

Evidence: `HomebrewPart.test_selected_contract_and_emitted_dry_run`,
`HomebrewPart.test_deselected_has_no_job_or_credential`,
`HomebrewRuntime.test_record`, `test_retry`, `test_conflict`, `test_checksum`,
`test_version`, `test_urls`, `test_downgrade`, `test_ordering`, `test_dry`, `test_race`, and `test_readback`.
These named tests exercise the emitted entry point and separate source/tap
API boundaries without remote writes. Live GitHub workflow proof remains with
the parent's authorized test repository work.
