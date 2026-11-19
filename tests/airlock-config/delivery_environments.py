# relationships: validates: delivery-environments
"""The delivery-environments check of rendered workflows, from that specification's validation criteria.

`violations(workflows, declaration)` takes the rendered workflow files, keyed by
file name, and the rendered delivery declaration, and returns one line for each
breach; an empty list passes. A breach is:

- a `secrets.` reference other than `secrets.GITHUB_TOKEN`;
- a `vars.` reference outside a job of a credential environment's workflow
  file, `delivery-<E>.yml` for an environment `<E>` that the declaration lists;
- a `vars.` name in such a job other than `<ENVIRONMENT>__<NAME>` for a name
  the declaration lists for `<E>`;
- an `environment:` outside the workflow file of the environment it names;
- `id-token: write` anywhere but in a job of a credential environment's
  workflow file, a job that calls one, and a job named `attest`.

Every string value is searched, not only `${{ }}` spans, because a job
condition is a bare expression.
"""
import re

from ruamel.yaml import YAML

REFERENCE = re.compile(r'\b(secrets|vars)\s*(?:\.\s*([A-Za-z_][A-Za-z0-9_-]*)|(\[))')
WORKFLOW = re.compile(r'^delivery-([a-z][a-z0-9]*(?:-[a-z0-9]+)*)\.ya?ml$')
CALL = re.compile(r'^\./\.github/workflows/(delivery-[a-z][a-z0-9-]*\.ya?ml)$')


def strings(value):
    if isinstance(value, dict):
        for item in value.values():
            yield from strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from strings(item)
    elif isinstance(value, str):
        yield value


def references(value):
    for text in strings(value):
        for match in REFERENCE.finditer(text):
            yield match.group(1), match.group(2) or '[' + text[match.end():].split(']')[0] + ']'


def grants_id_token(permissions):
    if permissions == 'write-all':
        return True
    return isinstance(permissions, dict) and permissions.get('id-token') == 'write'


def environment_name(job):
    environment = job.get('environment')
    if isinstance(environment, dict):
        return environment.get('name')
    return environment


def prefix(environment):
    return environment.upper().replace('-', '_') + '__'


def violations(workflows, declaration):
    environments = declaration.get('environments') or {}
    found = []
    for name, text in sorted(workflows.items()):
        workflow = YAML(typ='safe').load(text)
        match = WORKFLOW.match(name)
        own = match.group(1) if match and match.group(1) in environments else None
        jobs = workflow.get('jobs') or {}
        outside = {key: value for key, value in workflow.items() if key != 'jobs'}
        for kind, ref in references(outside):
            if kind == 'secrets' and ref != 'GITHUB_TOKEN':
                found.append(f'{name}: secrets.{ref} outside a job')
            if kind == 'vars':
                found.append(f'{name}: vars.{ref} outside a job')
        if grants_id_token(workflow.get('permissions')):
            found.append(f'{name}: id-token: write at the top level')
        for job_id, job in jobs.items():
            where = f'{name} job {job_id}'
            for kind, ref in references(job):
                if kind == 'secrets' and ref != 'GITHUB_TOKEN':
                    found.append(f'{where}: secrets.{ref}')
                elif kind == 'vars':
                    if own is None:
                        found.append(f'{where}: vars.{ref} outside a credential environment workflow')
                    elif not (ref.startswith(prefix(own))
                              and ref[len(prefix(own)):] in environments[own].get('variables', [])):
                        found.append(f'{where}: vars.{ref} is not a variable declared for {own}')
            entered = environment_name(job)
            if entered is not None and entered != own:
                found.append(f'{where}: environment {entered} outside delivery-{entered}.yml')
            if grants_id_token(job.get('permissions')):
                called = CALL.match(job.get('uses') or '')
                calls_credential = bool(called and WORKFLOW.match(called.group(1))
                                        and WORKFLOW.match(called.group(1)).group(1) in environments)
                if not (own or calls_credential or job_id == 'attest'):
                    found.append(f'{where}: id-token: write')
    return found
