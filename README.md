# Wyrd Company


## Renovate

This repository defines the independent Renovate preset for the wyrd-company owner.
Repositories under this owner can extend it with:

```json
{
  "extends": ["github>wyrd-company/.github:renovate-config"]
}
```

Renovate keeps supported managers enabled by auto-discovery. It opens separate major update pull requests, limits open and hourly pull requests, and does not merge updates automatically. Lock files are maintained weekly.
