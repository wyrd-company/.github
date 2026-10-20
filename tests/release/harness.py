# relationships: validates: release-process
"""Real Toha renders, comment-preserving interface checks, and test-only parts."""
import copy
import json
import os
from pathlib import Path
import re
import shutil
import subprocess

import jsonschema
from ruamel.yaml import YAML
from ruamel.yaml.comments import TaggedScalar

ROOT = Path(__file__).resolve().parents[2]
YAML_RT = YAML()
PIN = re.compile(r'^(?:\./\S+|[\w.-]+/[\w./-]+@[0-9a-f]{40} # v?\d+(?:\.\d+){0,2}(?:[-+][\w.-]+)?)$')


def load(path):
    return YAML_RT.load(Path(path).read_text())


def dump(path, value):
    with Path(path).open('w') as stream:
        YAML_RT.dump(value, stream)


def prepare(template, samples=1, omit_publish=False):
    shutil.copytree(ROOT / 'release', template)
    config = load(template / 'template.yml')
    part_question = next(q for q in config['interview'] if q.get('id') == 'targets')
    existing_switches = next(q['computed'] for q in config['interview'] if q.get('id') == 'credential_answers')
    config['interview'] = [q for q in config['interview'] if q.get('id') != 'credential_answers']
    switches = {}
    for number in range(samples):
        target = 'sample-target' if number == 0 else f'sample-target-{number + 1}'
        suffix = '' if number == 0 else f'-{number + 1}'
        directory = template / 'targets' / target
        shutil.copytree(ROOT / 'tests/release/sample-target', directory)
        data = load(directory / 'target.yml')
        for stage in ('build', 'publish', 'verify'):
            data[stage] = [job + suffix for job in data[stage]]
        if omit_publish:
            data['publish'] = []
        data['units'] = 'sample_target_units' + ('_' + str(number + 1) if number else '')
        switch = 'trusted_publishing' + ('_' + str(number + 1) if number else '')
        data['credentials'] = {key + suffix: dict(value) for key, value in data['credentials'].items()}
        for credential in data['credentials'].values():
            if 'unless' in credential:
                credential['unless'] = switch
        for file in list(directory.glob('*.yml')):
            if file.name != 'target.yml':
                file.rename(file.with_name(file.stem + suffix + file.suffix))
        dump(directory / 'target.yml', data)
        part_question['options'].append(target)
        config['data']['release_targets'][target] = TaggedScalar(f'targets/{target}/target.yml', tag='!include')
        config['interview'].extend([
            {'id': data['units'], 'type': 'text', 'prompt': 'Sample units', 'required': True,
             'when': f"'{target}' in targets", 'default': 'sample-tool' if number == 0 else 'sample-docs',
             'validate': {'regex': '^[a-z0-9][a-z0-9._-]*( [a-z0-9][a-z0-9._-]*)*$'}},
            {'id': switch, 'type': 'confirm', 'prompt': 'Use trusted publishing?', 'default': False,
             'when': f"'{target}' in targets"},
        ])
        switches[switch] = switch
        with (template / 'partials/parts.yml').open('a') as stream:
            stream.write("\n{% if '" + target + "' in targets %}\n")
            for stage in ('build', 'publish', 'verify'):
                stream.write("{% if stage == '" + stage + "' %}\n")
                for job in data[stage]:
                    stream.write("{% set body %}{% include 'targets/" + target + '/' + job + ".yml' %}{% endset %}\n")
                    env_file = directory / (job + '.env.yml')
                    if env_file.exists():
                        stream.write("{% set extra_env %}{% include 'targets/" + target + '/' + job + ".env.yml' %}{% endset %}\n")
                    stream.write("{{ ns.part_job('" + job + "', " + data['units'] + ", '" + stage + "', body" + (', extra_env' if env_file.exists() else '') + ') }}\n')
                stream.write('{% endif %}\n')
            stream.write('{% endif %}\n')
    expression = 'dict(' + existing_switches + ''.join(', ' + key + '=' + value for key, value in switches.items()) + ')'
    config['interview'].append({'id': 'credential_answers', 'computed': expression})
    dump(template / 'template.yml', config)
    return config


def render(root, model, samples=0, selected=None, answers=None, omit_publish=False):
    template = root / 'release'
    prepare(template, samples, omit_publish)
    target = root / 'project'
    (target / '.intentional').mkdir(parents=True)
    (target / '.intentional/release-model.json').write_text(json.dumps(model))
    selected = selected if selected is not None else ['sample-target' if i == 0 else f'sample-target-{i + 1}' for i in range(samples)]
    supplied = dict(ci_workflow='ci.yml')
    supplied.update(answers or {})
    document = root / 'answers.json'
    document.write_text(json.dumps({'template': str(template), 'answers': supplied}))
    command = [os.environ['TOHA_BIN'], 'apply', str(template), str(target), '--answers', str(document)]
    for part in selected:
        command.extend(['--with', part])
    # The empty selection uses the actual parts question's empty default.
    result = subprocess.run(command, text=True, capture_output=True)
    if result.returncode:
        raise AssertionError(f'Toha exit {result.returncode}: {result.stdout}\n{result.stderr}')
    assert json.loads(result.stdout)['status'] == 'applied', result.stdout
    workflow = target / '.github/workflows/release.yml'
    return template, workflow, supplied


def check_condition(expression):
    """Check grouping using GitHub's single-quoted, doubled-quote strings.

    A backslash is literal in GitHub expressions; it does not escape a quote.
    This checks containment, not the full GitHub expression grammar.
    """
    depth = 0
    quoted = False
    index = 0
    while index < len(expression):
        char = expression[index]
        if char == "'":
            if quoted and index + 1 < len(expression) and expression[index + 1] == "'":
                index += 2
                continue
            quoted = not quoted
        elif not quoted:
            if char == '(':
                depth += 1
            elif char == ')':
                depth -= 1
                if depth < 0:
                    raise ValueError('Unbalanced part condition parentheses: unexpected closing parenthesis')
        index += 1
    if quoted:
        raise ValueError('Unterminated part condition string')
    if depth != 0:
        raise ValueError('Unbalanced part condition parentheses: missing closing parenthesis')


def check_parts(template, workflow, selected, answers):
    """Reject duplicates before parsing workflow YAML; YAML also rejects duplicate keys."""
    jobs_seen, credentials_seen = set(), set()
    metadata = {}
    for target in selected:
        data = load(template / 'targets' / target / 'target.yml')
        metadata[target] = data
        for stage in ('build', 'publish', 'verify'):
            for job in data[stage]:
                assert re.fullmatch(stage + r'-[a-z0-9]+(-[a-z0-9]+)*', job), job
                assert job not in jobs_seen, f'Duplicate job id: {job}'
                jobs_seen.add(job)
        for key, credential in data['credentials'].items():
            assert re.fullmatch(r'[a-z0-9]+(-[a-z0-9]+)*', key), key
            assert key not in credentials_seen, f'Duplicate credential key: {key}'
            credentials_seen.add(key)
            credential = dict(credential)
            unless = credential.pop('unless', None)
            if unless:
                assert unless in answers and isinstance(answers[unless], bool), f'Invalid unless selector: {unless}'
            jsonschema.validate(credential, json.loads((template / 'schemas/credential.schema.json').read_text()))
    parsed = load(workflow)
    for job in parsed['jobs'].values():
        for step in job.get('steps', []):
            if 'uses' in step:
                comment = step.ca.items.get('uses', [None, None, None])[2]
                reference = step['uses'] + (' ' + comment.value.strip() if comment else '')
                assert PIN.fullmatch(reference), reference
    for target, data in metadata.items():
        units = answers[data['units']]
        assert all(unit in json.loads(next(s['env']['RELEASE_MODEL'] for s in parsed['jobs']['resolve']['steps'] if s.get('id') == 'resolve'))['release_units'] for unit in units.split(' ')), 'Unknown unit'
        for stage in ('build', 'publish', 'verify'):
            for name in data[stage]:
                body = copy.deepcopy(parsed['jobs'][name])
                condition = body.pop('if')
                body.pop('needs')
                clauses = []
                release_model = json.loads(next(s['env']['RELEASE_MODEL'] for s in parsed['jobs']['resolve']['steps'] if s.get('id') == 'resolve'))
                for unit in units.split(' '):
                    clause = f"contains(fromJSON(needs.resolve.outputs.units), '{unit}')"
                    if stage != 'build' and release_model['release_units'][unit]['release_record'] == 'required':
                        clause = f"({clause} && needs.release-record.result == 'success')"
                    clauses.append(clause)
                gate = "!cancelled() && !failure() && needs.resolve.result == 'success'"
                for preflight in parsed['jobs']:
                    if preflight == 'preflight' or preflight.startswith('preflight-'):
                        gate += f" && needs.{preflight}.result == 'success'"
                parent_condition = gate + ' && (' + ' || '.join(clauses) + ')'
                assert condition.startswith(parent_condition), 'Part condition replaced parent gate'
                extra = condition[len(parent_condition):]
                if extra:
                    assert extra.startswith(' && (') and extra.endswith(')'), 'Malformed extra condition'

                expected_env = {
                    'RELEASE_SOURCE_SHA': '${{ needs.resolve.outputs.source_sha }}',
                    'RELEASE_UNITS': "${{ join(fromJSON(needs.resolve.outputs.units), ' ') }}",
                    'RELEASE_JSON': '${{ needs.resolve.outputs.releases }}',
                }
                for key, value in expected_env.items():
                    assert body['env'].get(key) == value, f'Invalid parent environment: {key}'
                    body['env'].pop(key)
                if not body['env']:
                    body.pop('env')
                for step in body['steps']:
                    if 'uses' in step and not step['uses'].startswith('./'):
                        comment = step.ca.items['uses'][2]
                        step['uses'] += ' ' + comment.value.strip()
                entry = {'units': units, 'job': body}
                if extra:
                    entry['if'] = extra[5:-1]
                    check_condition(entry['if'])
                jsonschema.validate(entry, json.loads((template / 'schemas/publish-job.schema.json').read_text()))
    return parsed


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='Validate rendered release part interfaces')
    parser.add_argument('--template', type=Path, required=True)
    parser.add_argument('--workflow', type=Path, required=True)
    parser.add_argument('--answers', type=Path, required=True, help='JSON document with settled answers, including targets')
    args = parser.parse_args()
    answers = json.loads(args.answers.read_text())['answers']
    try:
        check_parts(args.template, args.workflow, answers['targets'], answers)
    except (AssertionError, ValueError, jsonschema.ValidationError) as error:
        parser.exit(1, str(error) + '\n')
    print('PASS: rendered part interfaces')
