# relationships: validates: release-process
"""Real renders and local artifact/record simulation; no hosted scheduler claim."""
import copy
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

import harness
from harness import ROOT, check_parts, dump, load, render
from model import model


def product_answers(assembly=True):
    return dict(product_build_units='sample-tool sample-docs',
                product_build_matrix={'targets': [{'target': 'first', 'runner': 'ubuntu-latest'},
                                      {'target': 'second', 'runner': ['self-hosted', 'example-runner']}]},
                product_build_recipe='./.github/actions/release-build',
                product_build_assembly=assembly,
                product_assembly_recipe='./.github/actions/release-assemble',
                product_assembly_runner='ubuntu-latest')


class ProductBuild(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory()
        self.addCleanup(self.scratch.cleanup)
        self.root = Path(self.scratch.name)

    def product(self, assembly=True):
        template, workflow, answers = render(self.root, model(), selected=['product-build'],
                                            answers=product_answers(assembly))
        parsed = check_parts(template, workflow, ['product-build'], answers)
        return template, workflow, answers, parsed

    def test_product_recipe_and_artifact_contract(self):
        _, workflow, _, parsed = self.product()
        jobs = parsed['jobs']
        build, assembly = jobs['build-product'], jobs['assemble-product']
        entries = build['strategy']['matrix']['include']
        self.assertEqual(len(entries), 4)
        self.assertEqual(entries[-1], {'unit': 'sample-docs', 'target': 'second',
                                      'runner': ['self-hosted', 'example-runner']})
        self.assertEqual(assembly['strategy']['matrix']['unit'], ['sample-tool', 'sample-docs'])
        for job in (build, assembly):
            self.assertEqual(job['permissions'], {'contents': 'read'})
            self.assertEqual(set(job['env']), {'RELEASE_SOURCE_SHA', 'RELEASE_UNITS', 'RELEASE_JSON'})
            checkout = job['steps'][0]
            self.assertEqual(checkout['with'], {'ref': '${{ needs.resolve.outputs.source_sha }}',
                                                'persist-credentials': False})
            recipe = next(step for step in job['steps'] if step.get('name') == 'Run repository recipe')
            self.assertTrue(recipe['uses'].startswith('./.github/actions/release-'))
            self.assertEqual(recipe['if'], 'contains(fromJSON(needs.resolve.outputs.units), matrix.unit)')
            for step in job['steps']:
                self.assertNotIn('${{', step.get('run', ''))
                self.assertNotIn('${{', step.get('with', {}).get('script', ''))
        download = next(step for step in assembly['steps'] if 'download-artifact@' in step.get('uses', ''))
        self.assertEqual(download['with']['pattern'], 'release-asset+${{ matrix.unit }}+*')
        self.assertNotIn('github-token', download['with'])
        self.assertNotIn('run-id', download['with'])
        result = subprocess.run(['actionlint', str(workflow)], text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_build_without_optional_assembly(self):
        _, _, _, parsed = self.product(False)
        jobs = parsed['jobs']
        self.assertNotIn('assemble-product', jobs)
        self.assertEqual(jobs['release-record']['needs'], ['resolve', 'preflight', 'build-product'])

    def test_product_rejects_extra_permission(self):
        template, workflow, answers, parsed = self.product()
        # actions:read is valid for other part jobs, but this recipe part promises contents:read only.
        parsed['jobs']['assemble-product']['permissions']['actions'] = 'read'
        dump(workflow, parsed)
        with self.assertRaisesRegex(AssertionError, 'Product recipes request contents: read only'):
            check_parts(template, workflow, ['product-build'], answers)

    def test_product_rejects_declared_credential(self):
        template, workflow, answers, _ = self.product()
        path = template / 'targets/product-build/target.yml'
        data = load(path)
        data['credentials']['sample-token'] = {'name': 'SAMPLE_TOKEN', 'purpose': 'Example artifact'}
        dump(path, data)
        with self.assertRaisesRegex(AssertionError, 'Product recipes declare no credentials'):
            check_parts(template, workflow, ['product-build'], answers)

    def test_invalid_recipe_and_matrix_answers_fail_before_output(self):
        variants = [('product_build_recipe', './.github/../outside'),
                    ('product_build_recipe', './action${{ secrets.SAMPLE_TOKEN }}'),
                    ('product_build_recipe', './recipe;echo'),
                    ('product_build_matrix', [{'target': 'first', 'runner': '${{ secrets.SAMPLE_TOKEN }}'}]),
                    ('product_build_matrix', [{'target': 'first;echo', 'runner': 'ubuntu-latest'}]),
                    ('product_build_matrix', [])]
        for key, value in variants:
            with self.subTest(key=key, value=value), tempfile.TemporaryDirectory() as tmp:
                where = Path(tmp)
                answers = dict(product_answers(), **{key: {'targets': value} if key == 'product_build_matrix' else value})
                with self.assertRaisesRegex(AssertionError, 'Toha exit ' + ('1' if key == 'product_build_matrix' else '4')):
                    render(where, model(), selected=['product-build'], answers=answers)
                self.assertFalse((where / 'project/.github').exists())

    def test_two_target_assets_aggregate_and_emitted_record(self):
        _, _, _, parsed = self.product()
        document = self.root / 'workflow.json'
        document.write_text(json.dumps(parsed))
        results = []
        for scenario in ('success', 'failed-build', 'empty-output', 'unreleased-unit'):
            where = self.root / scenario
            where.mkdir()
            run = subprocess.run(['node', str(ROOT / 'tests/release/product-build-rendered.cjs'),
                                  str(document), scenario], cwd=where, text=True, capture_output=True)
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
            result = json.loads(run.stdout)
            self.assertEqual(result['scenario'], scenario)
            results.append(result)
        self.assertEqual(results[0]['assets'], ['SHA256SUMS', 'aggregate.json', 'first.txt', 'second.txt'])
        self.assertTrue(results[0]['aggregateMatchesRecord'])
        self.assertEqual(results[1]['recordCalls'], 0)
        self.assertEqual(results[2]['recordCalls'], 0)
        self.assertEqual(results[3]['recipeCalls'], 0)
        if os.environ.get('RELEASE_PRODUCT_RECEIPTS'):
            path = Path(os.environ['RELEASE_PRODUCT_RECEIPTS'])
            path.mkdir(parents=True, exist_ok=True)
            (path / 'artifact-record.json').write_text(json.dumps(results, indent=2) + '\n')


class AssemblyGraph(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory()
        self.addCleanup(self.scratch.cleanup)
        self.root = Path(self.scratch.name)

    def graph(self, validate=True, **options):
        template, workflow, answers = render(self.root, model(), samples=2, assembly_samples=2, **options)
        answers.update(sample_target_units='sample-tool', sample_target_units_2='sample-docs',
                       trusted_publishing=False, trusted_publishing_2=False)
        selected = options.get('selected', ['sample-target', 'sample-target-2'])
        return check_parts(template, workflow, selected, answers) if validate else load(workflow)

    def test_assembly_waits_for_every_build(self):
        parsed = self.graph()
        jobs = parsed['jobs']
        stages = [['resolve'], ['preflight', 'preflight-sample'], ['build-sample', 'build-sample-2'],
                  ['assemble-sample', 'assemble-sample-2'], ['release-record'],
                  ['publish-sample', 'publish-sample-2'], ['verify-sample', 'verify-sample-2']]
        earlier = []
        for stage in stages:
            for name in stage:
                expected = ['resolve'] if name.startswith('preflight') else earlier
                self.assertEqual(jobs[name].get('needs', []), expected,
                                 f'{name} must wait for every earlier stage')
            earlier += stage
        self.assertNotIn('release-record', jobs['assemble-sample']['if'])

    def test_empty_stages_and_selection(self):
        for options in ({'omit_build': True}, {'omit_publish': True}, {'selected': []},
                        {'selected': ['sample-target']}):
            with self.subTest(options=options), tempfile.TemporaryDirectory() as tmp:
                self.root = Path(tmp)
                jobs = self.graph(**options)['jobs']
                if options.get('omit_build'):
                    self.assertEqual(jobs['assemble-sample']['needs'], ['resolve', 'preflight', 'preflight-sample'])
                if options.get('omit_publish'):
                    self.assertEqual(jobs['verify-sample']['needs'], ['resolve', 'preflight', 'preflight-sample',
                                     'build-sample', 'build-sample-2', 'assemble-sample', 'assemble-sample-2', 'release-record'])
                if options.get('selected') == []:
                    self.assertEqual(list(jobs), ['resolve', 'preflight', 'release-record'])
                self.assertTrue(all(set(job.get('needs', [])) <= set(jobs) for job in jobs.values()))

    def table(self, job=None):
        parsed = self.graph(validate=False)
        document = self.root / 'workflow.json'
        document.write_text(json.dumps(parsed))
        run = subprocess.run(['node', str(ROOT / 'tests/release/stage-conditions.cjs'), str(document)] + ([job] if job else []),
                             text=True, capture_output=True)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        result = json.loads(run.stdout)
        if os.environ.get('RELEASE_PRODUCT_RECEIPTS'):
            path = Path(os.environ['RELEASE_PRODUCT_RECEIPTS'])
            path.mkdir(parents=True, exist_ok=True)
            (path / ('stage-truth-table-' + job + '.json' if job else 'stage-truth-table.json')).write_text(json.dumps(result, indent=2) + '\n')
        return result

    def test_stage_truth_table(self):
        result = self.table()
        self.assertGreater(len(result), 50)
        self.assertTrue(any(row['priorResult'] == 'skipped' and row['runs'] for row in result))

    def test_assembly_resolve_result_conjunct(self):
        rows = self.table('assemble-sample')
        self.assertTrue(any(row['job'] == 'assemble-sample' and row['prior'] == 'resolve'
                            and row['priorResult'] == 'skipped' and not row['runs'] for row in rows))

    def test_assembly_preflight_result_conjunct(self):
        rows = self.table('assemble-sample')
        for prior in ('preflight', 'preflight-sample'):
            self.assertTrue(any(row['job'] == 'assemble-sample' and row['prior'] == prior
                                and row['priorResult'] == 'skipped' and not row['runs'] for row in rows))
