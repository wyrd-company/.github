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
the declaration block. `test_rendered_config_has_exactly_one_final_newline`
checks exact bytes, parsed values and YAML lint for the default and declared
branches. It covers valid single- and multi-topic lists and accepted-generation
variants. Independently authored expected bytes and complete parsed configurations
form the oracle; this regression needs no Git history. The topic array
uses comma-space formatting. Focused mutation rows cover the declaration guard,
EOF whitespace and topic-array separator formatting. The required/guard tests
reject empty and malformed answers before output. `test_all_optional_answers`
checks exact scalar types, boolean values, topic splitting and quoted multiline
text. `test_contract`
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

The audit workflows and the declaration have their own tests.
`test_per_repository_files` and `test_central_audit_renders_central_files`
compare the rendered file set for each `central_audit` answer.
`test_declaration_validates` validates both declarations against the
specification's schema and compares the central one with
`airlock-config.example.json`. `test_audit_workflow` and
`test_central_workflows` check triggers, permissions, environment and action
inputs. `test_delivery_environments` runs `delivery_environments.py`, the
check that `delivery-environments` states in its validation criteria, on each
render. The check reads every `${{ }}` span and every `if:` value, and refuses
a read of the whole `secrets` or `vars` context, such as `toJSON(secrets)`, a
computed index, and `secrets: inherit`, besides named reads;
`test_delivery_environments.py` proves it refuses each breach it names, and
mutation rows add each such read to the rendered workflows. `test_actionlint` lints the rendered workflows, `test_action_pins`
requires every external action to be pinned by commit with a comment, and
`test_renovate_reads_the_pins` checks that `renovate.json` reads the template's
workflow sources, whose conditional names do not end in `.yml`.
`test_applied_record_holds_central_audit` reads `answers.central_audit` from
`.toha.json`. These tests need `actionlint` on `PATH`.
