# relationships: validates: release-process
"""Actual Toha output and release/tap API-boundary tests; no remote writes."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from harness import ROOT, check_parts, load, render
from model import model


class HomebrewRuntime(unittest.TestCase):
    def node(self, name):
        result = subprocess.run(['node', '--test', '--test-name-pattern', '^' + name + '$',
                                 'tests/release/homebrew.test.cjs'], cwd=ROOT, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('pass 1', result.stdout)


RUNTIME_TESTS = {
    'planning': 'homebrew validates later unit assets before the first tap write',
    'downgrade': 'homebrew downgrade refuses stable and prerelease precedence',
    'ordering': 'homebrew SemVer ordering agrees with npm reference',
    'retry': 'homebrew fresh update and identical retry',
    'create': 'homebrew new formula omits the existing blob SHA',
    'conflict': 'homebrew same version conflicting bytes refuse before write',
    'record': 'homebrew entire selection requires release records before IO',
    'selection': 'homebrew only released selected units read assets',
    'release': 'homebrew resolved release evidence is required',
    'dry': 'homebrew dry run displays patch and makes zero writes',
    'dry_choice': 'homebrew dry run choice must be explicit',
    'destination': 'homebrew destination is a separate named repository',
    'branch': 'homebrew tap must have a default branch',
    'source': 'homebrew source release must match resolved state',
    'assets': 'homebrew exact formula and checksum assets are required',
    'checksum': 'homebrew formula checksum binds independent downloaded bytes',
    'semver': 'homebrew formula has one explicit bare SemVer stanza',
    'version': 'homebrew formula version binds resolved version',
    'newline': 'homebrew formula ends with newline',
    'urls': 'homebrew every download URL binds source repository and tag',
    'read_errors': 'homebrew tap read errors fail closed',
    'content': 'homebrew tap content must be a file with observable bytes',
    'race': 'homebrew matching concurrent commit succeeds without retrying write',
    'race_conflict': 'homebrew conflicting concurrent commit preserves failure',
    'readback': 'homebrew write must be observed with identical bytes',
    'diff': 'homebrew diff describes exact full-file bytes',
}
for key, name in RUNTIME_TESTS.items():
    def method(self, name=name):
        self.node(name)
    setattr(HomebrewRuntime, 'test_' + key, method)


@unittest.skipUnless(os.environ.get('TOHA_BIN'), 'TOHA_BIN is required')
class HomebrewPart(unittest.TestCase):
    def workflow(self, selected=True, literal=False):
        value = model()
        if literal:
            value['release_units']['sample-tool']['changelog'] = '${{ secrets.SAMPLE_TOKEN }}.md'
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        answers = dict(homebrew_tap_units='sample-tool', homebrew_tap_repository='example-org/homebrew-tools',
                       homebrew_tap_dry_run=True)
        parts = ['homebrew-tap'] if selected else []
        template, workflow, answers = render(Path(tmp.name), value, selected=parts, answers=answers)
        return workflow, check_parts(template, workflow, parts, answers), value

    def test_selected_contract_and_emitted_dry_run(self):
        workflow, parsed, value = self.workflow()
        job = parsed['jobs']['publish-homebrew-tap']
        self.assertEqual(job['permissions'], {'contents': 'read'})
        self.assertIn('release-record', job['needs'])
        self.assertIn("needs.release-record.result == 'success'", job['if'])
        self.assertEqual(job['env']['HOMEBREW_TAP_REPOSITORY'], 'example-org/homebrew-tools')
        self.assertEqual(job['env']['HOMEBREW_UNITS'], 'sample-tool')
        self.assertEqual(job['env']['HOMEBREW_DRY_RUN'], 'true')
        self.assertEqual(json.loads(job['env']['HOMEBREW_MODEL']), value)
        self.assertEqual(job['steps'][0]['env']['HOMEBREW_TAP_TOKEN'], '${{ secrets.HOMEBREW_TAP_TOKEN }}')
        self.assertIn('HOMEBREW_TAP_TOKEN', parsed['jobs']['preflight']['steps'][0]['env'])
        self.assertEqual(set(parsed['on']), {'workflow_dispatch'})
        document = workflow.with_suffix('.json')
        document.write_text(json.dumps(parsed))
        result = subprocess.run(['node', 'tests/release/homebrew-rendered.cjs', str(document)], cwd=ROOT,
                                text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('PASS emitted Homebrew', result.stdout)
        subprocess.run(['actionlint', str(workflow)], check=True)

    def test_deselected_has_no_job_or_credential(self):
        workflow, parsed, _ = self.workflow(selected=False)
        self.assertNotIn('publish-homebrew-tap', parsed['jobs'])
        self.assertNotIn('HOMEBREW_TAP_TOKEN', workflow.read_text())

    def test_model_data_stays_literal(self):
        _, parsed, value = self.workflow(literal=True)
        data = parsed['jobs']['publish-homebrew-tap']['env']['HOMEBREW_MODEL']
        self.assertNotIn('${{', data)
        self.assertEqual(json.loads(data), value)
