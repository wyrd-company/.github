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
