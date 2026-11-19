#!/usr/bin/env python3
# relationships: validates: release-process
"""Named, one-site mutation rows for interview and rendering guards."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

from ruamel.yaml import YAML

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / 'templates/airlock-config/template.yml'
TEMPLATE = ROOT / 'templates/airlock-config/template/.github/airlock.yml'
SOURCE = ROOT / 'templates/airlock-config/template/.github'
DECLARATION = SOURCE / 'delivery/airlock-config.json'
AUDIT = SOURCE / 'workflows/airlock-audit.yml'
CENTRAL = SOURCE / 'workflows/{% if central_audit %}airlock-central.yml{% endif %}'
SAFE = SOURCE / 'workflows/{% if central_audit %}delivery-airlock-safe.yml{% endif %}'


STEP = '      - name: Audit this repository\n'


def probe(expression):
    """Give the audit step an environment variable that reads the expression."""
    return STEP, STEP + '        env:\n          SYNTHETIC_PROBE: {% raw %}${{ ' + expression + ' }}{% endraw %}\n'


def run(report):
    receipt = {'status': 'INCOMPLETE', 'head': subprocess.check_output(
        ['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(), 'rows': []}
    report.write_text(json.dumps(receipt, indent=2))
    baseline = subprocess.run([sys.executable, '-m', 'unittest', 'discover', '-s',
        'tests/airlock-config', '-v'], cwd=ROOT, text=True, capture_output=True)
    receipt['baseline'] = dict(exit=baseline.returncode, output=baseline.stdout + baseline.stderr)
    report.write_text(json.dumps(receipt, indent=2))
    if baseline.returncode:
        return False
    original = {path: path.read_bytes() for path in (CONFIG, TEMPLATE, DECLARATION, AUDIT, CENTRAL, SAFE)}
    config = YAML(typ='safe').load(original[CONFIG].decode())
    rows = []
    for question in config['interview']:
        key = question['id']
        for guard in ('required', 'validate', 'when'):
            if guard in question:
                test = ('test_contract' if guard == 'when' else
                        {'accepted_generation': 'test_generation', 'description': 'test_description',
                         'topics': 'test_topics', 'default_branch': 'test_default_branch'}[key] + ('_required' if guard == 'required' else '_guard'))
                rows.append((key + '-' + guard, CONFIG, (key, guard), test))
    rows += [('declarations-opt-in', TEMPLATE, ('{% if declare_repository -%}', '{% if true -%}'),
              'test_declarations_are_opt_in'),
             ('rendered-final-newline', TEMPLATE, ('{% endif -%}', '{% endif %}'),
              'test_rendered_config_has_exactly_one_final_newline'),
             ('rendered-topic-comma-spacing', TEMPLATE,
              ('''topics: ["{{ topics | split(' ') | join('", "') }}"]''',
               '''topics: {{ topics | split(' ') | tojson }}'''),
              'test_rendered_config_has_exactly_one_final_newline'),
             ('description-quoted', TEMPLATE, ('description | tojson', 'description'), 'test_all_optional_answers'),
             ('central-workflow-opt-in', CENTRAL, ('rename', 'airlock-central.yml'), 'test_central_audit_is_opt_in'),
             ('central-environment-workflow-opt-in', SAFE, ('rename', 'delivery-airlock-safe.yml'),
              'test_central_audit_is_opt_in'),
             ('declaration-opt-in', DECLARATION, ('{%- if central_audit %}', '{%- if true %}'),
              'test_central_audit_is_opt_in'),
             ('audit-push-branch', AUDIT, ('[{{ default_branch | tojson }}]', '["main"]'), 'test_audit_workflow'),
             ('audit-top-permissions', AUDIT, ('permissions: {}\n', ''), 'test_audit_workflow'),
             ('audit-read-only', AUDIT, ('attestations: read', 'attestations: write'), 'test_audit_workflow'),
             ('audit-secret', AUDIT, ('github.token', 'secrets.AIRLOCK_TOKEN'), 'test_delivery_environments'),
             ('central-no-deployment', SAFE, ('      deployment: false\n', ''), 'test_central_workflows'),
             ('central-variable-names', SAFE, ('vars.AIRLOCK_SAFE__AZURE_CLIENT_ID', 'vars.AZURE_CLIENT_ID'),
              'test_delivery_environments'),
             # Whole-context reads and inherited secrets, each refused by the delivery-environments check.
             ('audit-whole-secrets', AUDIT, probe('toJSON(secrets)'), 'test_delivery_environments'),
             ('audit-bare-secrets', AUDIT, probe('secrets'), 'test_delivery_environments'),
             ('audit-computed-secret-index', AUDIT, probe('secrets[github.actor]'), 'test_delivery_environments'),
             ('audit-whole-vars', AUDIT, probe('toJSON(vars)'), 'test_delivery_environments'),
             ('audit-secrets-in-condition', AUDIT, ('    runs-on: ubuntu-latest\n',
              "    runs-on: ubuntu-latest\n    if: contains(toJSON(secrets), 'x')\n"), 'test_delivery_environments'),
             ('audit-top-level-secrets', AUDIT, ('permissions: {}\njobs:',
              'permissions: {}\nenv:\n  SYNTHETIC_PROBE: {% raw %}${{ toJSON(secrets) }}{% endraw %}\njobs:'),
              'test_delivery_environments'),
             ('central-inherit-secrets', CENTRAL, ('uses: ./.github/workflows/delivery-airlock-safe.yml',
              'uses: ./.github/workflows/delivery-airlock-safe.yml\n    secrets: inherit'), 'test_delivery_environments'),
             ('central-whole-vars', SAFE, ('vars.AIRLOCK_SAFE__AZURE_CLIENT_ID', 'toJSON(vars)'),
              'test_delivery_environments'),
             # A `}}` inside a quoted literal must not end the expression before the read.
             ('audit-quoted-delimiter-secrets', AUDIT, probe("'}}' && toJSON(secrets)"), 'test_delivery_environments'),
             ('audit-quoted-delimiter-vars', AUDIT, probe("'}}' && toJSON(vars)"), 'test_delivery_environments'),
             ('audit-format-delimiter-secrets', AUDIT, probe("format('}}{0}', toJSON(secrets))"),
              'test_delivery_environments'),
             ('audit-escaped-quote-delimiter-secrets', AUDIT, probe("format('it''s }}', toJSON(secrets))"),
              'test_delivery_environments'),
             ('central-quoted-delimiter-vars', SAFE, ('vars.AIRLOCK_SAFE__AZURE_CLIENT_ID', "'}}' && toJSON(vars)"),
              'test_delivery_environments'),
             ('central-caller-id-token', CENTRAL, ('uses: ./.github/workflows/delivery-airlock-safe.yml',
              'runs-on: ubuntu-latest\n    steps: [{run: "true"}]'), 'test_delivery_environments')]
    try:
        for name, path, change, test in rows:
            if path == CONFIG:
                altered = YAML(typ='safe').load(original[CONFIG].decode())
                question = next(q for q in altered['interview'] if q['id'] == change[0])
                del question[change[1]]
                with path.open('w') as stream:
                    YAML().dump(altered, stream)
            elif change[0] == 'rename':
                path.rename(path.with_name(change[1]))
            else:
                source = original[path].decode()
                assert source.count(change[0]) == 1, name
                path.write_text(source.replace(*change))
            moved = path.with_name(change[1]) if change[0] == 'rename' else path
            mutated = moved.read_bytes() if change[0] != 'rename' else ('rename:' + change[1]).encode()
            assert mutated != original[path], name
            result = subprocess.run([sys.executable, '-m', 'unittest',
                'test_template.AirlockTemplate.' + test, '-v'], cwd=ROOT / 'tests/airlock-config',
                text=True, capture_output=True)
            if moved != path:
                moved.unlink()
            path.write_bytes(original[path])
            output = result.stdout + result.stderr
            row = dict(name=name, file=str(path.relative_to(ROOT)), test=test, exit=result.returncode,
                       killed=result.returncode != 0 and 'FAIL: ' + test in output,
                       original_sha256=hashlib.sha256(original[path]).hexdigest(),
                       mutated_sha256=hashlib.sha256(mutated).hexdigest(),
                       restored_sha256=hashlib.sha256(path.read_bytes()).hexdigest(), output=output)
            receipt['rows'].append(row)
            report.write_text(json.dumps(receipt, indent=2))
            print(name, 'KILLED' if row['killed'] else 'SURVIVED', flush=True)
        receipt['final_head'] = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
        if (len(receipt['rows']) == len(rows) and rows and receipt['head'] == receipt['final_head']
                and all(row['killed'] and row['restored_sha256'] == row['original_sha256']
                        for row in receipt['rows'])):
            receipt['status'] = 'PASS'
    finally:
        for path, content in original.items():
            for stray in (path.with_name('airlock-central.yml'), path.with_name('delivery-airlock-safe.yml')):
                if stray.parent == AUDIT.parent and stray.exists():
                    stray.unlink()
            path.write_bytes(content)
        report.write_text(json.dumps(receipt, indent=2))
    return receipt['status'] == 'PASS'


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--report', type=Path, required=True)
    sys.exit(0 if run(parser.parse_args().report) else 1)
