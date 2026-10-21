<!-- relationships: implements: release-process -->

# Publication targets

Publication parts live here. The repository test fixture in
`tests/release/sample-target/` demonstrates the five internal interfaces.
Run `task test` after adding a part; the harness tests each part alone and all
parts together, including credential labels and job ids.

## Product build and assembly

Select `--with product-build` and set `product_build_units` to the Release Unit
ids separated by spaces. Supply `.github/release-builds.json` or answer the
`product_build_matrix` document question. Target names belong to the product;
runner values accept a GitHub runner label or an array of self-hosted labels.
For example:

```json
{
  "targets": [
    {"target": "linux-amd64", "runner": "ubuntu-latest"},
    {"target": "linux-arm64", "runner": "ubuntu-24.04-arm"},
    {"target": "darwin-arm64", "runner": "macos-latest"},
    {"target": "windows-amd64", "runner": "windows-latest"}
  ]
}
```

`product_build_recipe` defaults to `./.github/actions/release-build`. The local
action receives `unit`, `target`, and `output-directory` inputs. It inherits
`RELEASE_SOURCE_SHA`, `RELEASE_UNITS` and `RELEASE_JSON` from the parent. Each
released unit/target pair runs at the resolved source commit. Entries for
unreleased units skip recipes and artifact transport. The product action owns
compiler commands, tool pins, archives, packages and product checks.

Enable `product_build_assembly` to call `product_assembly_recipe` (default
`./.github/actions/release-assemble`) after every build job succeeds or skips.
`product_assembly_runner` defaults to `ubuntu-latest`. The action receives
`unit`, `input-directory` and `output-directory`. The input directory contains
this run's `release-asset+<unit>+*` artifacts in per-artifact subdirectories;
no token, repository or run-id input is used to download them. Assembly can
produce a formula, package-selection manifest or other aggregate release file.

Both recipes write regular files directly in their output directory. Empty
output fails (`if-no-files-found: error`). Each file is uploaded as a separate
`release-asset+<unit>+<file>` artifact with the official `@actions/artifact`
JavaScript client. The template installs that dependency under `runner.temp`
with npm lifecycle scripts disabled; runners need npm for this setup. Artifact
names must be distinct across targets and assembly. A duplicate fails upload;
files cannot replace an earlier artifact. The parent owns artifact names,
upload/download, job ordering and conditions. Both jobs request only
`contents: read`, persist no checkout credential, declare no credentials and
receive no user secrets.

The parent release-record job attaches build and assembly files and their
`SHA256SUMS`. Homebrew receives the product-written `<unit>.rb` through the
existing release-asset convention; repo.wyrd.foo receives its package assets
and product-written package-selection manifest the same way. A failed build or
assembly stops all later stages. Skipped work for an unreleased unit and empty
stages preserve ordering. The local fixture proves transport and record bytes;
hosted scheduling cases require separate live evidence.

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
publication remains behind the parent's resolved Intentional tag record and
successful release record. Run `intentional apply` only with the user's release
approval.

Packaging uses `npm pack --ignore-scripts`: commit ready-to-pack files, including
launcher code and binary payloads. Build/prepare scripts do not run. An injected
projection stamps the resolved version in a temporary copy; committed and none
projections must already match. Publish uses that exact packed archive and
checks npm's SHA512 integrity, name and version. A retry accepts identical bytes
and refuses conflicting bytes. A concurrent matching publication is accepted.
If npm has not exposed a successful write yet, the job fails and can be rerun
on the same source commit; there is no polling timer. Prereleases use `next`;
stable versions omit an explicit tag so npm's default backward-move check can
apply. npm refuses a stable version lower than the current `latest`; the publish
job fails, and reruns fail the same way. A maintenance-line release needs
`publishConfig.tag` in its manifest. Explicit npm configuration can override
the default tag.

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
Linux amd64 and arm64 are built on an amd64 runner without emulation. Compile
in `FROM --platform=$BUILDPLATFORM` stages, using `TARGETOS` and `TARGETARCH`
for the compiler target. The final stage copies the target binary and has no
`RUN` instructions. Target-platform `RUN` instructions need emulation, which
this job does not configure. Pin build inputs and keep builds reproducible.
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
without GHCR lookup, login or push. BuildKit and base-image registry pulls may
occur. The job uses automatic `GITHUB_TOKEN`,
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
A successful first publication can log expected `level=fatal` diagnostics with
`manifest unknown` from missing-tag lookups before and after the digest copy.
These lookup diagnostics alone do not establish workflow failure.

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
a pinned Go compiler runs in a native `BUILDPLATFORM` stage and cross-compiles
both target executables, Skopeo preserves the digest through a loopback-only
registry, both ELF machine types are read back from the images, and the scratch
entry point runs on amd64. It needs Docker and Buildx; it pulls standard tool
images but writes no public registry. The receipt starts incomplete and passes
only after every assertion and cleanup succeeds.
`GhcrCompilationProof.test_native_build_stage_contract` and
`test_binary_machine_refuses_mislabeled_output` guard the compilation proof.
The authorized GitHub test repository and an explicitly approved disposable
private package destination are required for authenticated first-publication,
matching-retry and conflicting-byte proof. Local registry results do not prove
GHCR's answer to `GITHUB_TOKEN` for a never-published package.
`GhcrPublisher.test_ghcr_unmeasured_denials_fail_closed` covers unrecognized
denials as refusal fixtures; it is not a captured authenticated response.
Captured authenticated GHCR response evidence:
`test_captured_authenticated_ghcr_first_lookup`,
`test_captured_authenticated_ghcr_matching_retry`,
`test_captured_authenticated_ghcr_conflict`,
`test_captured_authenticated_ghcr_unauthorized_near_miss` and
`test_captured_authenticated_ghcr_denied_near_miss`.
Failed tools print captured stdout and stderr, with the raw token redacted.
`test_build_failure_diagnostics`, `test_builder_failure_diagnostics`,
`test_login_failure_diagnostics`, `test_local_inspect_failure_diagnostics`,
`test_remote_inspect_failure_diagnostics`, `test_copy_failure_diagnostics`,
`test_tag_copy_failure_diagnostics` and `test_cleanup_failure_diagnostics`
check the diagnostic and failure outcome.

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
`test_version`, `test_urls`, `test_downgrade`, `test_ordering`, `test_dry`,
`test_race`, and `test_readback`.
These named tests exercise the emitted entry point and separate source/tap
API boundaries without remote writes. Live GitHub workflow proof remains with
the parent's authorized test repository work.

## repo.wyrd.foo

Select `--with repo-wyrd-foo`. `repo_wyrd_foo_units` lists the Release Units
submitted, separated by spaces. Each selected unit must require a release
record. The publisher supports stable bare SemVer tags equal to the version;
prerelease and prefixed product tags are refused before submission. Package
builds belong to the product's build part. They must upload the Linux package
assets and a package selection manifest as release assets using
the parent's `release-asset+<unit>+<name>` artifact convention.

`repo_wyrd_foo_manifest_asset` names the manifest asset in each unit's GitHub
Release (default `release-manifest.json`). It follows repo.wyrd.foo's
`schemas/release-manifest.schema.json` for the pinned Action. The current Action
requires APT, RPM and AUR selection plus six artifacts. The agreed AUR removal
from repo.wyrd.foo is separate work; qualify the manifest contract against the
Action release selected at adoption. This part does not generate AUR metadata.
The part downloads the manifest through the release API, verifies its SHA256
against the release-record digest, checks its source and tag, and passes the
release-record asset names and digests to the submission Action. The Action
validates the complete manifest and rechecks the published assets before any
inbox write. The separate publisher owns signing and consumer
observation. Submission success does not establish consumer visibility.

Supply `repo_wyrd_foo_action_sha` and `repo_wyrd_foo_action_tag` explicitly.
Neither has a default. The SHA must be 40 lowercase hexadecimal characters; the
tag must be bare SemVer. Form validation does not qualify a release identity.
At adoption/UAT, after an approved repo.wyrd.foo release exists, use its real tag
and full peeled commit reachable from remote main. Match the separate consumer
example's pin. Run from that repo.wyrd.foo checkout:

```sh
task submission:qualify PUBLICATION_CALLER=/path/to/rendered/release.yml
```

The gate checks the rendered workflow's one submission Action reference, tag,
main ancestry and runtime agreement. Synthetic test pins validate forms only;
they are not published Action identities. No placeholder appears in rendered
workflows. No release or public tag is authorized by rendering or qualification.

Configure repository or organization secrets `REPO_WYRD_FOO_PUBLISHER_APP_ID`
and `REPO_WYRD_FOO_PUBLISHER_PRIVATE_KEY`. Preflight checks both by name and
purpose. The publish job requests only `contents: read`; the Action mints a
Contents-write token scoped to repo.wyrd.foo. Operators must limit the App
installation to that repository and configure the inbox/main branch rules as
described in repo.wyrd.foo's submission guide. No publisher signing or AUR
credential enters the product workflow.

`repo_wyrd_foo_dry_run` defaults to false. Set it to true to validate the manifest
and release assets without invoking the submission Action or minting an App
token. Preflight still checks the declared credential names. Every matrix leg
validates the entire selected unit set before enabling submission. Unreleased
units skip submission. An identical immutable submission succeeds on retry;
a different manifest for the same product/version is refused by the Action.
The Action re-dispatches the same inbox commit after an interrupted publisher.

Local evidence: `PackagePart` validates selected/deselected jobs and credentials,
the operator pin answers and the emitted dry-run entry point. `PackageRuntime`
executes record, digest, source and dry-run guards with a fake GitHub transport.
The submission Action's named retry/conflict tests provide its retry evidence;
live workflow and pin qualification receipts belong to adoption/UAT.
