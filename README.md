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
merge updates automatically. Lock files are maintained weekly.

## Release workflow template

`gh:wyrd-company/.github#release` renders `.github/workflows/release.yml` from
`.intentional/release-model.json`. Use a Toha build with document questions and
part selection. Create and commit the model with `intentional model`, then run:

```sh
toha apply gh:wyrd-company/.github#release .
```

Choose the CI workflow file and, when more than one unit requires a GitHub
Release, the unit marked latest. Optional publication targets add build,
publish, verify, and credential declarations to the parent workflow. Available
parts are `crates-io`, `npm`, `ghcr`, and `homebrew-tap`; see the
[part guide](release/targets/README.md) for answers and credentials.

Push the source commit and Intentional tags, wait for successful push CI on that
commit, then dispatch `release.yml` on the default branch with `source-sha` set
to that commit. Baseline tag records do not release a unit. The workflow checks
all selected targets' credential names before builds start, creates or completes
one GitHub Release per required unit, then publishes and verifies.

Update the workflow with:

```sh
toha apply gh:wyrd-company/.github#release . --update
```

The applied record and normal Toha update merge preserve operator changes,
including Renovate's action pins. Resolve reported conflicts before applying.

See [the release-process design](docs/technical-designs/release-process.yml) and
[the template test guide](tests/release/README.md) for part interfaces, local
validation, and live workflow proof cases.
