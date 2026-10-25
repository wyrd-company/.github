# relationships: validates: release-process
"""Package submission interface; synthetic pins never name a published Action."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from harness import ROOT, render, load, check_parts
from model import model

ANSWERS = json.loads((ROOT / 'tests/release/part-answers/repo-wyrd-foo.json').read_text())


class PackagePart(unittest.TestCase):
    def workflow(self, selected=True, value=None, answers=None):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        settled = dict(ANSWERS, **(answers or {}))
        template, workflow, settled = render(Path(tmp.name), value or model(),
            selected=['repo-wyrd-foo'] if selected else [], answers=settled if selected else {})
        parsed = check_parts(template, workflow, ['repo-wyrd-foo'] if selected else [], settled)
        result = subprocess.run(['actionlint', str(workflow)], text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return parsed, workflow.read_text()

    def test_selected_and_removed(self):
        parsed, text = self.workflow()
        job = parsed['jobs']['publish-repo-wyrd-foo']
        self.assertEqual(job['permissions'], {'contents': 'read'})
        self.assertIn('release-record', job['needs'])
        self.assertIn("needs.release-record.result == 'success'", job['if'])
        self.assertEqual(job['strategy']['matrix'], {'unit': ['sample-tool']})
        step = job['steps'][-1]
        self.assertEqual(step['uses'], 'wyrd-company/repo.wyrd.foo/.github/actions/submit-release@' + 'a' * 40)
        self.assertIn('# 1.2.3', text)
        self.assertNotIn('PUBLISHED_MAIN_SHA', text)
        self.assertNotIn('RELEASE_TAG', text)
        self.assertEqual(step['if'], "steps.package.outputs.submit == 'true'")
        self.assertEqual(step['with']['release-assets'], '${{ steps.package.outputs.assets }}')
        self.assertEqual(step['with']['tag'], '${{ steps.package.outputs.tag }}')
        self.assertEqual(step['with']['version'], '${{ steps.package.outputs.version }}')
        for name, field in [('REPO_WYRD_FOO_PUBLISHER_APP_ID', 'app-client-id'),
                            ('REPO_WYRD_FOO_PUBLISHER_PRIVATE_KEY', 'app-private-key')]:
            expression = '${{ secrets.' + name + ' }}'
            self.assertEqual(parsed['jobs']['preflight']['steps'][0]['env'][name], expression)
            self.assertEqual(step['with'][field], expression)
        self.assertEqual(set(parsed['jobs']['preflight']['steps'][0]['env']),
            {'REPO_WYRD_FOO_PUBLISHER_APP_ID', 'REPO_WYRD_FOO_PUBLISHER_PRIVATE_KEY'})
        self.assertEqual(set(parsed['on']), {'workflow_dispatch'})
        removed, text = self.workflow(selected=False)
        self.assertNotIn('publish-repo-wyrd-foo', removed['jobs'])
        self.assertNotIn('REPO_WYRD_FOO', text)
        self.assertNotIn('submit-release@', text)

    def test_pin_answers_required_without_defaults_and_validated(self):
        config = load(ROOT / 'templates/release/template.yml')
        for field in ['repo_wyrd_foo_action_sha', 'repo_wyrd_foo_action_tag']:
            question = next(q for q in config['interview'] if q.get('id') == field)
            self.assertTrue(question['required'])
            self.assertNotIn('default', question)
            self.assertEqual(question['when'], "'repo-wyrd-foo' in targets")
        for field, invalid in [('repo_wyrd_foo_action_sha', 'main'),
                               ('repo_wyrd_foo_action_sha', 'a' * 39),
                               ('repo_wyrd_foo_action_tag', 'v1.2.3'),
                               ('repo_wyrd_foo_action_tag', '01.2.3'),
                               ('repo_wyrd_foo_action_tag', '1.2.3-01'),
                               ('repo_wyrd_foo_manifest_asset', '${{ secrets.SAMPLE_TOKEN }}'),
                               ('repo_wyrd_foo_units', "sample-tool' || true")]:
            with self.subTest(field=field, invalid=invalid):
                with tempfile.TemporaryDirectory() as tmp:
                    with self.assertRaisesRegex(AssertionError, 'Toha exit 4:') as rejected:
                        render(Path(tmp), model(), selected=['repo-wyrd-foo'], answers=dict(ANSWERS, **{field: invalid}))
                    envelope, _ = json.JSONDecoder().raw_decode(str(rejected.exception).split(': ', 1)[1])
                    self.assertEqual(set(envelope['errors']), {field})
                    self.assertFalse((Path(tmp) / 'project/.github/workflows/release.yml').exists())

    def test_model_and_answers_are_literal_data(self):
        value = model()
        value['release_units']['sample-tool']['changelog'] = '${{ secrets.SAMPLE_TOKEN }}.md'
        parsed, _ = self.workflow(value=value)
        env = parsed['jobs']['publish-repo-wyrd-foo']['steps'][0]['env']
        self.assertNotIn('${{', env['PACKAGE_MODEL'])
        self.assertEqual(json.loads(env['PACKAGE_MODEL']), value)
        self.assertEqual(env['PACKAGE_DRY_RUN'], 'true')
        self.assertEqual(env['PACKAGE_UNIT'], '${{ matrix.unit }}')

    def test_emitted_entry_point_dry_run(self):
        parsed, _ = self.workflow()
        script = parsed['jobs']['publish-repo-wyrd-foo']['steps'][0]['with']['script']
        with tempfile.TemporaryDirectory() as tmp:
            emitted = Path(tmp) / 'prepare.js'
            emitted.write_text(script)
            result = subprocess.run(['node', '--test', '--test-name-pattern', '^packages: emitted dry run$',
                'tests/release/repo-wyrd-foo.test.cjs'], cwd=ROOT,
                env=dict(os.environ, PACKAGE_EMITTED_SCRIPT=str(emitted)), text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn('pass 1', result.stdout)

    def test_emitted_action_manifest_preserves_original_download_bytes(self):
        parsed, _ = self.workflow(answers={'repo_wyrd_foo_dry_run': False})
        prepare, submit = parsed['jobs']['publish-repo-wyrd-foo']['steps']
        self.assertEqual(prepare['id'], 'package')
        self.assertEqual(submit['with']['manifest'], '${{ steps.package.outputs.manifest }}')
        with tempfile.TemporaryDirectory() as tmp:
            emitted = Path(tmp) / 'prepare.js'
            emitted.write_text(prepare['with']['script'])
            result = subprocess.run(['node', '--test', '--test-name-pattern',
                '^packages: rendered Action manifest preserves original download bytes$',
                'tests/release/repo-wyrd-foo.test.cjs'], cwd=ROOT,
                env=dict(os.environ, PACKAGE_EMITTED_SCRIPT=str(emitted),
                         PACKAGE_EMITTED_MANIFEST_INPUT=submit['with']['manifest']),
                text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn('pass 1', result.stdout)


class PackageRuntime(unittest.TestCase):
    def node(self, name):
        result = subprocess.run(['node', '--test', '--test-name-pattern', '^packages: ' + name + '$',
            'tests/release/repo-wyrd-foo.test.cjs'], cwd=ROOT, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('pass 1', result.stdout)


for name in [
    'released record supplies manifest, names and digests',
    'runtime manifest preserves original download bytes',
    'none in the whole selection refuses before submission', 'unknown selection refuses',
    'dry-run choice is required', 'matrix is limited to selected units',
    'unresolved units do not read assets or submit', 'missing release refuses',
    'only stable bare tags are supported by publisher', 'draft record refuses',
    'different record tag refuses', 'manifest asset must be present exactly once',
    'missing manifest digest refuses', 'changed manifest download refuses',
    'manifest tag must match source', 'manifest version must match source',
    'manifest commit must match source', 'manifest repository must match source',
    'package artifact must match release-record digest', 'dry run never enables submission',
]:
    def method(self, name=name):
        self.node(name)
    setattr(PackageRuntime, 'test_' + name.replace(' ', '_').replace('-', '_'), method)
