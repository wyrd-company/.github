# relationships: validates: release-process
"""Read-only product recipe: real renders and local emitted-job execution."""
import copy
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

import jsonschema
from harness import ROOT, check_parts, dump, load, render
from model import model
from test_product_build import product_answers


def verification_answers(assembly=False):
    return dict(product_answers(assembly), product_verification=True,
                product_verification_recipe='./.github/actions/release-verify',
                product_verification_matrix={'runners': ['ubuntu-latest', ['self-hosted', 'example-runner']]})


class ProductVerification(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory()
        self.addCleanup(self.scratch.cleanup)
        self.root = Path(self.scratch.name)

    def workflow(self, selected=None, answers=None, validate=True, **options):
        selected = ['product-build'] if selected is None else selected
        template, workflow, settled = render(self.root, model(), selected=selected,
                                             answers=verification_answers() if answers is None else answers, **options)
        parsed = check_parts(template, workflow, selected, settled) if validate else load(workflow)
        return template, workflow, settled, parsed

    def test_optional_interview_and_schema(self):
        questions = {q['id']: q for q in load(ROOT / 'release/template.yml')['interview']}
        enable = questions['product_verification']
        self.assertEqual(enable['type'], 'confirm')
        self.assertFalse(enable['default'])
        self.assertEqual(enable['when'], "'product-build' in targets")
        recipe = questions['product_verification_recipe']
        self.assertEqual(recipe['default'], './.github/actions/release-verify')
        self.assertEqual(recipe['when'], "'product-build' in targets and product_verification")
        matrix = questions['product_verification_matrix']
        self.assertEqual(matrix['when'], recipe['when'])
        self.assertEqual(matrix['input'], '.github/release-verification.json')
        schema = json.loads((ROOT / 'release/schemas/product-verification-matrix.schema.json').read_text())
        self.assertEqual(matrix['schema'], schema['$id'])
        build = json.loads((ROOT / 'release/schemas/product-build-matrix.schema.json').read_text())
        self.assertEqual(schema['$defs'], build['$defs'])
        self.assertEqual(schema['properties']['runners']['items'],
                         build['properties']['targets']['items']['properties']['runner'])
        for runner in ('ubuntu-latest', ['self-hosted', 'example-runner']):
            jsonschema.validate({'runners': [runner]}, schema)

    def test_default_recipe_and_document_input(self):
        directory = self.root / 'project/.github'
        directory.mkdir(parents=True)
        document = {'runners': ['windows-latest']}
        (directory / 'release-verification.json').write_text(json.dumps(document))
        answers = dict(product_answers(False), product_verification=True)
        _, _, _, parsed = self.workflow(answers=answers)
        job = parsed['jobs']['verify-product']
        self.assertEqual(job['steps'][1]['uses'], './.github/actions/release-verify')
        self.assertEqual(job['strategy']['matrix']['include'], [
            {'unit': unit, 'runner': 'windows-latest'} for unit in ['sample-tool', 'sample-docs']])

    def test_read_only_recipe_contract(self):
        _, workflow, _, parsed = self.workflow()
        job = parsed['jobs']['verify-product']
        self.assertEqual(job['permissions'], {'contents': 'read'})
        self.assertNotIn('environment', job)
        self.assertNotIn('secrets.', json.dumps(job))
        self.assertNotIn('continue-on-error', job)
        self.assertNotIn('timeout-minutes', job)
        self.assertEqual(job['runs-on'], '${{ matrix.runner }}')
        self.assertEqual(job['strategy']['fail-fast'], False)
        self.assertEqual(job['strategy']['matrix']['include'], [
            {'unit': unit, 'runner': runner}
            for unit in ['sample-tool', 'sample-docs']
            for runner in ['ubuntu-latest', ['self-hosted', 'example-runner']]])
        self.assertEqual(job['steps'][0]['with'], {'ref': '${{ needs.resolve.outputs.source_sha }}',
                                                  'persist-credentials': False})
        recipe = job['steps'][1]
        self.assertEqual(recipe['uses'], './.github/actions/release-verify')
        self.assertEqual(recipe['with']['unit'], '${{ matrix.unit }}')
        self.assertEqual(recipe['with']['matrix-entry'], '${{ toJSON(matrix) }}')
        self.assertTrue(recipe['if'].startswith('contains(fromJSON(needs.resolve.outputs.units), matrix.unit)'))
        for step in job['steps']:
            self.assertNotIn('${{', step.get('run', ''))
        result = subprocess.run(['actionlint', str(workflow)], text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_enabled_needs_every_earlier_job(self):
        answers = dict(verification_answers(True), npm_units='sample-tool', npm_dry_run=True, npm_trusted_publishing=True)
        _, _, _, parsed = self.workflow(['product-build', 'npm'], answers, samples=2, assembly_samples=2)
        self.assertEqual(parsed['jobs']['verify-product']['needs'],
                         ['resolve', 'preflight', 'build-product', 'assemble-product',
                          'release-record', 'publish-npm'])
        self.assertEqual(parsed['jobs']['verify-npm-clients']['needs'], parsed['jobs']['verify-product']['needs'])

    def test_all_publish_dependencies(self):
        answers = dict(verification_answers(True), npm_units='sample-tool', npm_dry_run=True, npm_trusted_publishing=True,
                       sample_target_units='sample-tool', sample_target_units_2='sample-docs',
                       trusted_publishing=False, trusted_publishing_2=False)
        _, _, _, parsed = self.workflow(['product-build', 'npm', 'sample-target', 'sample-target-2'],
                                        answers, samples=2, assembly_samples=2)
        self.assertEqual(parsed['jobs']['verify-product']['needs'], [
            'resolve', 'preflight', 'preflight-sample', 'build-product', 'build-sample', 'build-sample-2',
            'assemble-product', 'assemble-sample', 'assemble-sample-2', 'release-record',
            'publish-npm', 'publish-sample', 'publish-sample-2'], 'verify-product must need every publish job')

    def test_enabled_preserves_npm_verification(self):
        jobs = []
        for enabled in (False, True):
            with tempfile.TemporaryDirectory() as tmp:
                answers = dict(verification_answers(True), product_verification=enabled,
                               npm_units='sample-tool', npm_dry_run=True, npm_trusted_publishing=True)
                _, workflow, _ = render(Path(tmp), model(), selected=['product-build', 'npm'], answers=answers)
                jobs.append(load(workflow)['jobs']['verify-npm-clients'])
        self.assertEqual(jobs[0], jobs[1], 'npm verification content and graph must remain unchanged')

    def test_default_off_false_and_deselected(self):
        for enabled, selected in ((None, ['product-build']), (False, ['product-build']), (True, [])):
            with self.subTest(enabled=enabled, selected=selected), tempfile.TemporaryDirectory() as tmp:
                answers = product_answers(False)
                if enabled is not None:
                    answers.update(verification_answers(), product_verification=enabled)
                _, workflow, _ = render(Path(tmp), model(), selected=selected, answers=answers)
                self.assertNotIn('verify-product', load(workflow)['jobs'])

    def test_verification_without_assembly_publication_or_record_stage(self):
        value = model()
        for unit in value['release_units'].values():
            unit['release_record'] = 'none'
        template, workflow, answers = render(self.root, value, selected=['product-build'],
                                             answers=verification_answers())
        parsed = check_parts(template, workflow, ['product-build'], answers)
        self.assertEqual(parsed['jobs']['verify-product']['needs'], ['resolve', 'preflight', 'build-product'])
        self.assertNotIn('assemble-product', parsed['jobs'])
        self.assertNotIn('release-record', parsed['jobs'])
        self.assertNotIn('release-record', parsed['jobs']['verify-product']['steps'][1]['if'])

    def test_stage_truth_table_and_required_record(self):
        answers = dict(verification_answers(True), product_build_units='sample-tool',
                       sample_target_units='sample-tool', sample_target_units_2='sample-docs',
                       trusted_publishing=False, trusted_publishing_2=False)
        _, _, _, parsed = self.workflow(['product-build', 'sample-target', 'sample-target-2'],
                                        answers, validate=False, samples=2, assembly_samples=2)
        path = self.root / 'workflow.json'
        path.write_text(json.dumps(parsed))
        result = subprocess.run(['node', str(ROOT / 'tests/release/stage-conditions.cjs'), str(path), 'verify-product'],
                     capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        rows = json.loads(result.stdout)
        self.assertTrue(any(row['prior'] == 'release-record' and row['priorResult'] == 'skipped'
                            and not row['runs'] for row in rows), 'required unit must wait for release-record success')
        self.save('truth-table', rows)

    def test_empty_build_assembly_publish_stages(self):
        # Exercise the same parent macro with a selected verify recipe but no earlier part jobs.
        import harness
        from unittest.mock import patch
        original = harness.prepare
        def without_build(template, *args, **kwargs):
            config = original(template, *args, **kwargs)
            target = template / 'targets/product-build/target.yml'
            data = load(target); data['build'] = []; dump(target, data)
            partial = template / 'partials/parts.yml'
            text = partial.read_text().replace("{% if stage == 'build' %}\n{% set body %}{% include 'targets/product-build/build-product.jinja' %}",
                                                "{% if false %}\n{% set body %}{% include 'targets/product-build/build-product.jinja' %}")
            partial.write_text(text)
            return config
        with patch.object(harness, 'prepare', without_build):
            _, _, _, parsed = self.workflow(answers=dict(verification_answers(), product_build_units='sample-tool'))
        self.assertEqual(parsed['jobs']['verify-product']['needs'], ['resolve', 'preflight', 'release-record'])
        self.assertNotIn('build-product', parsed['jobs'])
        path = self.root / 'workflow.json'; path.write_text(json.dumps(parsed))
        result = subprocess.run(['node', '-e',
            "const fs=require('node:fs'); const {condition,truthTable}=require(process.argv[1]); "
            "const jobs=JSON.parse(fs.readFileSync(process.argv[2])).jobs; "
            "const runs=condition(jobs,'verify-product',{},['sample-tool']); "
            "require('node:assert/strict').equal(runs,true); "
            "console.log(JSON.stringify({rows:truthTable(jobs,['verify-product']), "
            "empty:['build','assemble','publish'].map(stage=>({stage,result:'empty',runs}))}));",
            str(ROOT / 'tests/release/stage-conditions.cjs'), str(path)], text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.save('empty-stages', json.loads(result.stdout))

    def test_rejects_extra_permission(self):
        template, workflow, answers, parsed = self.workflow()
        parsed['jobs']['verify-product']['permissions']['id-token'] = 'write'
        dump(workflow, parsed)
        with self.assertRaisesRegex(AssertionError, 'Product recipes request contents: read only'):
            check_parts(template, workflow, ['product-build'], answers)

    def test_rejects_declared_credential(self):
        template, workflow, answers, _ = self.workflow()
        path = template / 'targets/product-build/target.yml'
        data = load(path); data['credentials']['example-token'] = {'name': 'EXAMPLE_TOKEN', 'purpose': 'Example check'}
        dump(path, data)
        with self.assertRaisesRegex(AssertionError, 'Product recipes declare no credentials'):
            check_parts(template, workflow, ['product-build'], answers)

    def test_rejects_user_secret_and_protected_environment(self):
        template, workflow, answers, parsed = self.workflow()
        for field in ('secret', 'environment'):
            with self.subTest(field=field):
                changed = copy.deepcopy(parsed)
                if field == 'secret':
                    changed['jobs']['verify-product']['steps'][1]['with']['example'] = '${{ secrets.EXAMPLE_TOKEN }}'
                    diagnostic = 'Product verification receives no user secrets'
                else:
                    changed['jobs']['verify-product']['environment'] = 'example'
                    diagnostic = 'Product verification declares no protected environment'
                dump(workflow, changed)
                with self.assertRaisesRegex(AssertionError, diagnostic):
                    check_parts(template, workflow, ['product-build'], answers)

    def test_invalid_recipe_and_matrix_before_output(self):
        variants = [('product_verification_recipe', './.github/../outside'),
                    ('product_verification_recipe', './action${{ secrets.EXAMPLE_TOKEN }}'),
                    ('product_verification_recipe', './recipe;echo'),
                    ('product_verification_matrix', {'runners': []}),
                    ('product_verification_matrix', {'runners': ['${{ secrets.EXAMPLE_TOKEN }}']}),
                    ('product_verification_matrix', {'runners': [['self-hosted', '${{ github.token }}']]}),
                    ('product_verification_matrix', {'runners': ['ubuntu-latest'], 'other': True})]
        for key, value in variants:
            with self.subTest(key=key, value=value), tempfile.TemporaryDirectory() as tmp:
                where = Path(tmp)
                answers = dict(verification_answers(), **{key: value})
                with self.assertRaisesRegex(AssertionError, 'Toha exit ' + ('1' if key.endswith('matrix') else '4')):
                    render(where, model(), selected=['product-build'], answers=answers)
                self.assertFalse((where / 'project/.github').exists())

    def execute(self, scenario):
        _, _, _, parsed = self.workflow()
        path = self.root / 'workflow.json'
        path.write_text(json.dumps(parsed))
        directory = self.root / scenario
        directory.mkdir()
        result = subprocess.run(['node', str(ROOT / 'tests/release/product-verification-rendered.cjs'), str(path), scenario],
                                cwd=directory, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        receipt = json.loads(result.stdout)
        self.save(scenario, receipt)
        return receipt

    def save(self, name, receipt):
        if os.environ.get('RELEASE_VERIFICATION_RECEIPTS'):
            where = Path(os.environ['RELEASE_VERIFICATION_RECEIPTS']); where.mkdir(parents=True, exist_ok=True)
            (where / (name + '.json')).write_text(json.dumps(receipt, indent=2) + '\n')

    def test_recipe_receives_resolved_identity(self):
        receipt = self.execute('success')
        self.assertEqual(receipt['recipeCalls'], 4)
        self.assertNotEqual(receipt['ref'], receipt['observed'][0]['tag'])
        self.assertNotEqual(receipt['ref'], receipt['observed'][0]['version'])

    def test_recipe_failure_fails_job(self):
        self.assertTrue(self.execute('recipe-failure')['jobFailed'])

    def test_unreleased_unit_skips_recipe(self):
        self.assertEqual(self.execute('unreleased-unit')['recipeCalls'], 2)

    def test_mixed_units_required_record_step_gate(self):
        self.assertEqual(self.execute('skipped-record')['recipeCalls'], 2)

    def test_disabled_outputs_equal_base_bytes(self):
        import hashlib
        import harness
        from unittest.mock import patch
        base = os.environ.get('RELEASE_VERIFICATION_BASE', '91a89be6299ec5e9ce6a417d50fe4940b439cc0e')
        baseline = self.root / 'base'
        paths = subprocess.check_output(['git', 'ls-tree', '-r', '--name-only', base, '--', 'release'],
                                        cwd=ROOT, text=True).splitlines()
        for name in paths:
            destination = baseline / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(subprocess.check_output(['git', 'show', base + ':' + name], cwd=ROOT))
        parts = sorted(load(ROOT / 'release/template.yml')['data']['release_targets'])
        selections = [[part] for part in parts] + [
            [part, 'product-build'] for part in parts if part != 'product-build'] + [parts]
        receipts = []
        for selection in selections:
            for assembly in (False, True):
                answers = {}
                for part in selection:
                    answers.update(json.loads((ROOT / 'tests/release/part-answers' / (part + '.json')).read_text()))
                answers['product_build_assembly'] = assembly
                outputs = []
                for label, source in (('base', baseline), ('final', ROOT)):
                    where = self.root / (str(len(receipts)) + '-' + label); where.mkdir()
                    with patch.object(harness, 'ROOT', source):
                        _, workflow, _ = harness.render(where, model(), selected=selection, answers=answers)
                    # Compare the workflow and every generated SDK input.
                    project = where / 'project'
                    outputs.append({str(path.relative_to(project)): path.read_bytes()
                                    for path in (project / '.github').rglob('*') if path.is_file()})
                self.assertEqual(outputs[0], outputs[1], f'disabled outputs: {selection}, assembly={assembly}')
                receipts.append({'parts': selection, 'assembly': assembly, 'base': base,
                                 'base_sha256': {name: hashlib.sha256(data).hexdigest() for name, data in outputs[0].items()},
                                 'final_sha256': {name: hashlib.sha256(data).hexdigest() for name, data in outputs[1].items()}})
        self.assertEqual(len(receipts), 24)
        self.save('disabled-byte-comparison', receipts)
