<!-- relationships: validates: release-process -->

# Airlock configuration template tests

Use the Python dependencies in `../release/requirements.txt`. Set `TOHA_BIN` to
Toha built from `8f148040` and `AIRLOCK_BIN` to Airlock built from `fb99337bd650fdf769c6e1d40eaa6d581e827268`.
Build each in its own scratch clone with `cargo build --locked -j2` (Airlock's
package is `airlock-cli`). Remove owned clones and build output after testing.

```sh
python3 -m unittest discover -s tests/airlock-config -v
python3 tests/airlock-config/mutations.py --report /tmp/airlock-config-mutations.json
task ci
```

`test_defaults` and `test_all_optional_answers` render through Toha, commit the
result in a generic fixture repository, then run the actual Airlock local audit
with a separate minimal organization policy. `test_generation_variants` also
covers a later accepted generation and a prerelease with build metadata. Local
platform observations can be incomplete; parsing is proven by the resulting
repository report. `test_unknown_repository_key_rejected` checks that an unknown
key fails the same loader. No hosted Airlock schema exists at this input, so
there is no document question or vendored schema.

`test_declarations_are_opt_in` proves supplied declaration answers cannot enable
the declaration block. The required/guard tests reject empty and malformed
answers before output. `test_all_optional_answers` checks exact scalar types,
boolean values, topic splitting and quoted multiline text. `test_contract`
checks the interview inventory, conditional questions and absence of hooks or
document/schema declarations. Mutation rows remove each validation constraint
and conditional question, bypass the opt-in block, or drop description escaping.
The receipt records each named test, exit, output and source restoration hashes;
it starts incomplete and passes only when every row fails its named test.

Content follows the approved minimal interview: no preselected exceptions or
generic rule-parameter question. Operators add reasoned exceptions by hand;
Airlock applies them under organization override authority. Classification is
not a repository input. The template is adopted beside `release`, not through
its part selection.
