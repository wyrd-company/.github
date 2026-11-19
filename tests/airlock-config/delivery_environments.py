# relationships: validates: delivery-environments
"""The delivery-environments check of rendered workflows, from that specification's validation criteria.

`violations(workflows, declaration)` takes the rendered workflow files, keyed by
file name, and the rendered delivery declaration, and returns one line for each
breach; an empty list passes. A breach is:

- a read of the `secrets` context other than `secrets.GITHUB_TOKEN`,
  including a read of the whole context, such as `toJSON(secrets)`, or an index
  that is not a string literal;
- `secrets: inherit` on a job that calls a reusable workflow;
- a read of the whole `vars` context, anywhere;
- a `vars.` reference outside a job of a credential environment's workflow
  file, `delivery-<E>.yml` for an environment `<E>` that the declaration lists;
- a `vars.` name in such a job other than `<ENVIRONMENT>__<NAME>` for a name
  the declaration lists for `<E>`;
- an `environment:` outside the workflow file of the environment it names;
- `id-token: write` anywhere but in a job of a credential environment's
  workflow file, a job that calls one, and a job named `attest`.

Expressions are every `${{ }}` span in any string, and every `if:` value,
which GitHub reads as an expression without `${{ }}`. Context names and
property names are compared without case, as GitHub compares them.
"""
import re

from ruamel.yaml import YAML

SPAN = re.compile(r'\$\{\{(.*?)\}\}', re.DOTALL)
LITERAL = re.compile(r"'(?:[^']|'')*'")
IDENTIFIER = re.compile(r'[A-Za-z_][A-Za-z0-9_-]*')
PROPERTY = re.compile(r'\s*\.\s*([A-Za-z_][A-Za-z0-9_-]*)')
INDEX = re.compile(r"\s*\[\s*'((?:[^']|'')*)'\s*\]")
WHOLE = '*'
WORKFLOW = re.compile(r'^delivery-([a-z][a-z0-9]*(?:-[a-z0-9]+)*)\.ya?ml$')
CALL = re.compile(r'^\./\.github/workflows/(delivery-[a-z][a-z0-9-]*\.ya?ml)$')


def expressions(value, key=None):
    if isinstance(value, dict):
        for name, item in value.items():
            yield from expressions(item, name)
    elif isinstance(value, list):
        for item in value:
            yield from expressions(item)
    elif isinstance(value, str):
        spans = SPAN.findall(value)
        yield from spans
        if key == 'if' and not spans:
            yield value


def context_reads(expression):
    """Yield (context, name) for each read of `secrets` or `vars`; name is WHOLE for the whole context."""
    position = 0
    while position < len(expression):
        literal = LITERAL.match(expression, position)
        if literal:
            position = literal.end()
            continue
        word = IDENTIFIER.match(expression, position)
        if not word:
            position += 1
            continue
        position = word.end()
        before = expression[:word.start()].rstrip()
        context = word.group().lower()
        if context not in ('secrets', 'vars') or before.endswith('.'):
            continue
        named = PROPERTY.match(expression, position) or INDEX.match(expression, position)
        if named:
            position = named.end()
            yield context, named.group(1).replace("''", "'").upper()
        else:
            yield context, WHOLE


def references(value):
    for expression in expressions(value):
        yield from context_reads(expression)


def describe(kind, ref):
    return f'the whole {kind} context' if ref == WHOLE else f'{kind}.{ref}'


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
            if kind == 'secrets' and ref != 'GITHUB_TOKEN' or kind == 'vars':
                found.append(f'{name}: {describe(kind, ref)} outside a job')
        if grants_id_token(workflow.get('permissions')):
            found.append(f'{name}: id-token: write at the top level')
        for job_id, job in jobs.items():
            where = f'{name} job {job_id}'
            if isinstance(job.get('secrets'), str):
                found.append(f'{where}: secrets: {job["secrets"]} passes every secret to the called workflow')
            for kind, ref in references(job):
                if kind == 'secrets' and ref != 'GITHUB_TOKEN':
                    found.append(f'{where}: {describe(kind, ref)}')
                elif kind == 'vars' and ref == WHOLE:
                    found.append(f'{where}: the whole vars context')
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
