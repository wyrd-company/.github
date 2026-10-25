# relationships: validates: release-process
"""Parent-only final writer: real Toha renders and disposable, file-only Git remotes."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import harness
from harness import ROOT, check_major_tag, check_parts, load, render
from model import model
from test_product_verification import verification_answers

BASE = 'ee7a1be1d08177d1fa0e2420bc26292369bab336'


def enabled_answers():
    return dict(major_tag_update=True, major_tag_unit='sample-tool')


class MajorTag(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory()
        self.addCleanup(self.scratch.cleanup)
        self.root = Path(self.scratch.name)

    def workflow(self, selected=None, answers=None, value=None, **options):
        selected = [] if selected is None else selected
        template, workflow, settled = render(self.root, value or model(), selected=selected,
                                             answers=dict(enabled_answers(), **(answers or {})), **options)
        return template, workflow, settled, check_parts(template, workflow, selected, settled)

    def save(self, name, value):
        if os.environ.get('RELEASE_MAJOR_TAG_RECEIPTS'):
            where = Path(os.environ['RELEASE_MAJOR_TAG_RECEIPTS']); where.mkdir(parents=True, exist_ok=True)
            (where / (name + '.json')).write_text(json.dumps(value, indent=2) + '\n')

    def test_optional_interview_and_required_record_selection(self):
        questions = {q['id']: q for q in load(ROOT / 'templates/release/template.yml')['interview']}
        enable = questions['major_tag_update']; unit = questions['major_tag_unit']
        self.assertEqual(enable['type'], 'confirm'); self.assertFalse(enable['default'])
        self.assertNotIn('when', enable, 'major-tag enable requires no part')
        self.assertEqual(unit['type'], 'select'); self.assertEqual(unit['when'], 'major_tag_update')
        self.assertTrue(unit['required'])
        self.assertEqual(unit['options'], questions['latest_unit']['options'])
        for choice in ['sample-docs', 'unknown', "sample-tool') || true || ('", '${{ secrets.EXAMPLE_TOKEN }}']:
            with self.subTest(choice=choice), tempfile.TemporaryDirectory() as tmp:
                with self.assertRaisesRegex(AssertionError, 'Toha exit 4'):
                    render(Path(tmp), model(), selected=[], answers=dict(enabled_answers(), major_tag_unit=choice))
                self.assertFalse((Path(tmp) / 'project/.github').exists())
        value = model(); value['release_units']['sample-tool.v2_test'] = value['release_units'].pop('sample-tool')
        _, _, _, parsed = self.workflow(value=value, answers={'major_tag_unit': 'sample-tool.v2_test'})
        self.assertIn("'sample-tool.v2_test'", parsed['jobs']['update-major-tag']['if'])
        self.assertEqual(parsed['jobs']['update-major-tag']['env']['MAJOR_TAG_UNIT'], 'sample-tool.v2_test')

    def test_default_off_and_deselected(self):
        for answers in ({}, {'major_tag_update': False, 'major_tag_unit': 'sample-tool'}):
            with self.subTest(answers=answers), tempfile.TemporaryDirectory() as tmp:
                _, workflow, _ = render(Path(tmp), model(), selected=[], answers=answers)
                self.assertNotIn('update-major-tag', load(workflow)['jobs'])

    def test_parent_surface_and_fixed_code(self):
        _, workflow, _, parsed = self.workflow()
        job = parsed['jobs']['update-major-tag']
        self.assertEqual(job['permissions'], {'contents': 'write'})
        self.assertEqual(job['needs'], ['resolve', 'preflight', 'release-record'])
        self.assertEqual(job['steps'][0]['with'], {'ref': '${{ needs.resolve.outputs.source_sha }}', 'fetch-depth': 0})
        self.assertEqual(job['steps'][1]['with']['script'], (ROOT / 'templates/release/partials/major-tag.js').read_text())
        self.assertNotIn('sample-tool', job['steps'][1]['with']['script'])
        result = subprocess.run(['actionlint', str(workflow)], text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_needs_every_earlier_job(self):
        answers = dict(verification_answers(True), sample_target_units='sample-tool',
                       sample_target_units_2='sample-docs', trusted_publishing=False, trusted_publishing_2=False,
                       npm_units='sample-tool', npm_dry_run=True, npm_trusted_publishing=True)
        _, _, _, parsed = self.workflow(['product-build', 'npm', 'sample-target', 'sample-target-2'], answers,
                                       samples=2, assembly_samples=2)
        self.assertEqual(parsed['jobs']['update-major-tag']['needs'], [
            'resolve', 'preflight', 'preflight-sample', 'build-product', 'build-sample', 'build-sample-2',
            'assemble-product', 'assemble-sample', 'assemble-sample-2', 'release-record', 'publish-npm',
            'publish-sample', 'publish-sample-2', 'verify-npm-clients', 'verify-product', 'verify-sample',
            'verify-sample-2'], 'major tag must need every earlier job including all verify jobs')

    def test_named_unit_verification_truth_table(self):
        answers = dict(verification_answers(True), sample_target_units='sample-tool',
                       sample_target_units_2='sample-docs', trusted_publishing=False, trusted_publishing_2=False)
        _, _, _, parsed = self.workflow(['product-build', 'sample-target', 'sample-target-2'], answers,
                                       samples=2, assembly_samples=2)
        self.truth_table(parsed, ['verify-product', 'verify-sample'], 'truth-table')
        self.assertIn("needs.verify-product.result == 'success'", parsed['jobs']['update-major-tag']['if'],
                      'multi-unit verification must succeed as a whole')

    def truth_table(self, parsed, required, name):
        path = self.root / 'workflow.json'; path.write_text(json.dumps(parsed))
        result = subprocess.run(['node', str(ROOT / 'tests/release/major-tag-conditions.cjs'), str(path), json.dumps(required)],
                                text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.save(name, json.loads(result.stdout))

    def test_empty_named_unit_verify_stage(self):
        _, _, _, parsed = self.workflow(['sample-target'], {'sample_target_units': 'sample-docs', 'trusted_publishing': False},
                                       samples=1, omit_build=True, omit_publish=True)
        self.truth_table(parsed, [], 'empty-named-verification')

    def test_only_new_job_adds_write_permission(self):
        parts = sorted(load(ROOT / 'templates/release/template.yml')['data']['release_targets'])
        answers = verification_answers(True)
        for part in parts:
            answers.update(json.loads((ROOT / 'tests/release/part-answers' / (part + '.json')).read_text()))
        workflows = []
        for enabled in (False, True):
            with tempfile.TemporaryDirectory() as tmp:
                _, path, _ = render(Path(tmp), model(), selected=parts,
                                     answers=dict(answers, major_tag_update=enabled, major_tag_unit='sample-tool'))
                parsed = load(path); check_major_tag(parsed) if enabled else None
                raw = path.read_text()
                if enabled: parsed['jobs'].pop('update-major-tag')
                workflows.append((parsed, raw))
        self.assertEqual(workflows[0][0], workflows[1][0], 'every earlier job and workflow permission unchanged')
        self.assertTrue(workflows[1][1].startswith(workflows[0][1]),
                        'enabled workflow retains the entire earlier workflow byte prefix including pin comments')

    def test_rejects_workflow_write(self):
        _, _, _, parsed = self.workflow(); parsed['permissions'] = {'contents': 'write'}
        with self.assertRaisesRegex(AssertionError, 'Major tag keeps workflow permissions empty'): check_major_tag(parsed)

    def test_rejects_other_job_write(self):
        _, _, _, parsed = self.workflow(); parsed['jobs']['preflight']['permissions'] = {'contents': 'write'}
        with self.assertRaisesRegex(AssertionError, 'Major tag adds no other contents writer'): check_major_tag(parsed)

    def test_rejects_credential(self):
        _, _, _, parsed = self.workflow(); parsed['jobs']['update-major-tag']['env']['EXAMPLE_TOKEN'] = 'example'
        with self.assertRaisesRegex(AssertionError, 'Major tag declares no credential'): check_major_tag(parsed)

    def test_rejects_secret(self):
        _, _, _, parsed = self.workflow(); parsed['jobs']['update-major-tag']['env']['MAJOR_TAG_UNIT'] = '${{ secrets.EXAMPLE_TOKEN }}'
        with self.assertRaisesRegex(AssertionError, 'Major tag receives no user secret'): check_major_tag(parsed)

    def test_rejects_environment(self):
        _, _, _, parsed = self.workflow(); parsed['jobs']['update-major-tag']['environment'] = 'example'
        with self.assertRaisesRegex(AssertionError, 'Major tag declares no environment'): check_major_tag(parsed)

    def test_local_tag_semantics(self):
        _, _, _, parsed = self.workflow()
        path = self.root / 'workflow.json'; path.write_text(json.dumps(parsed))
        fixture = self.root / 'git-fixture'; fixture.mkdir()
        # A namespace may additionally deny networking; Git itself permits only the file protocol.
        command = ['node', str(ROOT / 'tests/release/major-tag-rendered.cjs'), str(path),
                   str(ROOT / 'tests/release/sample-major-tag/old-job.sh')]
        result = subprocess.run(command, cwd=fixture, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        receipt = json.loads(result.stdout)
        self.assertTrue(receipt['rows'][-1]['olderRerunMovesBackward'])
        self.save('local-tag-fixtures', dict(receipt, command=command, exit=result.returncode))

    def test_default_off_outputs_equal_base_bytes(self):
        baseline = self.root / 'base'
        paths = subprocess.check_output(['git', 'ls-tree', '-r', '--name-only', BASE, '--', 'release'],
                                        cwd=ROOT, text=True).splitlines()
        for name in paths:
            dest = baseline / 'templates' / name; dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(subprocess.check_output(['git', 'show', BASE + ':' + name], cwd=ROOT))
        parts = sorted(load(ROOT / 'templates/release/template.yml')['data']['release_targets'])
        selections = [[part] for part in parts] + [[part, 'product-build'] for part in parts if part != 'product-build'] + [parts]
        receipts = []
        for selection in selections:
            for assembly in (False, True):
                for verify in (False, True):
                    answers = verification_answers(assembly)
                    for part in selection:
                        answers.update(json.loads((ROOT / 'tests/release/part-answers' / (part + '.json')).read_text()))
                    answers.update(product_build_assembly=assembly, product_verification=verify)
                    outputs = []
                    for label, source, enabled in [('base', baseline, None), ('absent', ROOT, None), ('false', ROOT, False)]:
                        where = self.root / (str(len(receipts)) + '-' + label); where.mkdir()
                        supplied = dict(answers)
                        if enabled is False: supplied['major_tag_update'] = False
                        with patch.object(harness, 'ROOT', source):
                            _, path, _ = render(where, model(), selected=selection, answers=supplied)
                        project = where / 'project'
                        outputs.append({str(p.relative_to(project)): p.read_bytes()
                                        for p in (project / '.github').rglob('*') if p.is_file()})
                    self.assertEqual(outputs[0], outputs[1], f'absent major-tag identity: {selection}, assembly={assembly}, verify={verify}')
                    self.assertEqual(outputs[0], outputs[2], f'false major-tag identity: {selection}, assembly={assembly}, verify={verify}')
                    receipts.append({'parts': selection, 'assembly': assembly, 'verify': verify, 'base': BASE,
                                     'sha256': [{key: hashlib.sha256(value).hexdigest() for key, value in output.items()} for output in outputs]})
        self.assertEqual(len(receipts), 48)
        self.save('default-off-byte-identity', receipts)
