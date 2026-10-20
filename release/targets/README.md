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
The part adds no polling or timeout mechanic. Cargo's own indexing wait applies.

Evidence: `CratesPart.test_selected_and_deselected_contracts`,
`CratesPublisher.test_multi_crate_unit_dependency_order`,
`CratesPublisher.test_matching_published_or_submitted_retry`,
`CratesPublisher.test_different_published_artifact_refused`,
`CratesPublisher.test_index_wait_failure_stops_dependents_then_retry_adopts_submission`,
and `CratesPublisher.test_real_cargo_metadata_and_reproducible_package`.
Live workflow proof requires the authorized test repository tracked by the
parent's live-proof work. No local test publishes to a public registry.
