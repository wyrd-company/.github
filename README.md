# Wyrd Company

## Renovate

This repository defines the independent Renovate preset for the wyrd-company owner.
Repositories under this owner can extend it with:

```json
{
  "extends": ["github>wyrd-company/.github:renovate-config"]
}
```

Renovate keeps supported managers enabled by auto-discovery. It opens separate
major update pull requests, limits open and hourly pull requests, and does not
merge updates automatically. Lock files are maintained weekly. Renovate waits
until an npm package version is one day old before it creates a branch or pull
request for it, so that pnpm can install it. Lock file maintenance is not held.

## Release workflow template

`gh:wyrd-company/.github#templates/release` renders `.github/workflows/release.yml` from
`.intentional/release-model.json`. Use a Toha build with document questions and
part selection. Create and commit the model with `intentional model`, then run:

```sh
toha apply gh:wyrd-company/.github#templates/release .
```

Choose the CI workflow file and, when more than one unit requires a GitHub
Release, the unit marked latest. Optional publication targets add build,
publish, verify, and credential declarations to the parent workflow. Available
parts are `crates-io`, `npm`, `ghcr`, `homebrew-tap`, and `repo-wyrd-foo`;
see the [part guide](templates/release/targets/README.md) for answers, credentials and
qualification requirements.

Push the source commit and Intentional tags, wait for successful push CI on that
commit, then dispatch `release.yml` on the default branch with `source-sha` set
to that commit. Baseline tag records do not release a unit. The workflow checks
all selected targets' credential names before builds start, creates or completes
one GitHub Release per required unit, then publishes and verifies.

Update the workflow with:

```sh
toha apply gh:wyrd-company/.github#templates/release . --update
```

The applied record and normal Toha update merge preserve operator changes,
including Renovate's action pins. Resolve reported conflicts before applying.

See [the release-process design](docs/technical-designs/release-process.yml) and
[the template test guide](tests/release/README.md) for part interfaces, local
validation, and live workflow proof cases.

## Airlock repository configuration template

Apply `gh:wyrd-company/.github#templates/airlock-config` beside the `release` template:

```sh
toha apply gh:wyrd-company/.github#templates/airlock-config .
```

The default file declares accepted generation `0.6.0`. Enter a generation at
least as recent as the organization's accepted generation. Opt in to declare
description, topics, merge settings and features. No waiver is preselected.
Add reasoned `exceptions.<rule>` requests by hand; `severity: none` requests
a waiver. Requests apply only where the organization's `allow_override`
authority permits. Classification and organization-only parameters, including
template identities, are not repository declarations. This template has no
hooks and is not a part of the release composition.

Every repository that applies the template also gets:

- `.github/workflows/airlock-audit.yml`, which audits the repository once a
  day, when a person dispatches it, and on each push to `default_branch`
  (default `main`). Its one job holds only `contents: read`, `actions: read`
  and `attestations: read`, enters no environment, reads no secret, and runs
  the pinned Airlock action in repository mode with the run's
  `GITHUB_TOKEN`.
- `.github/delivery/airlock-config.json`, the Airlock configuration
  declaration, which declares no environment.

Answer `central_audit` true only in the organization's audit repository,
`organization-audit`. Every other repository keeps the default, false. In the
audit repository the template also renders:

- `.github/workflows/airlock-central.yml`, which runs once a day and when a
  person dispatches it. Its one job calls `delivery-airlock-safe.yml` with
  only `id-token: write`.
- `.github/workflows/delivery-airlock-safe.yml`, the workflow of the
  `airlock-safe` credential environment. Its one job enters `airlock-safe`
  without creating a deployment and runs the Airlock action in central mode,
  signed in as Airlock Safe from the six `AIRLOCK_SAFE__` variables.

The declaration then lists `airlock-safe` with its key and its six variables.
Both workflows read the organization policy from `airlock/policy.yml` in the
owner's `.github` repository. The template owns the Airlock action pin, and
this repository's Renovate updates it. See
[the delivery controls design](docs/technical-designs/delivery-controls.yml),
`audit-runs`.

See [the configuration test guide](tests/airlock-config/README.md).
