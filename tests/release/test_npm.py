# relationships: validates: release-process
"""npm part boundaries: actual Toha output and actual npm pack bytes."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from harness import ROOT, render, load, check_parts
from model import model


class NpmRuntime(unittest.TestCase):
    def node(self, name):
        result = subprocess.run(['node', '--test', '--test-name-pattern', '^' + name + '$',
                                 'tests/release/npm.test.cjs'], cwd=ROOT, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('pass 1', result.stdout)


RUNTIME_TESTS = {
    'windows': 'npm Windows client invokes the Node CLI without a command shell',
    'retry': 'npm fresh publication and retry use identical packed bytes',
    'conflict': 'npm conflicting published bytes fail before publication',
    'registry': 'npm registry identity and lookup failures fail closed',
    'record': 'npm missing release record refuses before packing or registry calls',
    'selection': 'npm units and ecosystem projections select non-node launchers',
    'projection': 'npm missing or generic-only projections refuse',
    'manifest': 'npm manifest guards refuse before writes',
    'injected': 'npm injected projection stamps a temporary copy and preserves source',
    'none': 'npm none projection validates the source version',
    'dry': 'npm dry run skips part registry lookup and client installation and uses dry-run',
    'dry_choice': 'npm missing dry run choice refuses before packing',
    'duplicate': 'npm duplicate package projections refuse before packing',
    'release': 'npm absent release version refuses before packing',
    'pack': 'npm pack identity is checked before publication',
    'prerelease_tag': 'npm prerelease publication explicitly uses next',
    'stable_tag': 'npm stable publication omits explicit dist-tag',
    'race': 'npm race accepts matching bytes and rejects conflict',
    'readback': 'npm publication must be read back and missing publication preserves failure',
    'client': 'npm clean clients install and validate exact package identity',
}
for key, name in RUNTIME_TESTS.items():
    def method(self, name=name):
        self.node(name)
    setattr(NpmRuntime, 'test_' + key, method)


@unittest.skipUnless(os.environ.get('TOHA_BIN'), 'TOHA_BIN is required')
class NpmPart(unittest.TestCase):
    def workflow(self, trusted=True, selected=True, literal=False, packages=False):
        value = model()
        if packages:
            value['release_units']['sample-tool']['projections'] = [{
                'adapter': 'npm', 'file': 'npm/package.json', 'workspace_file': 'npm/package.json',
                'mode': 'committed', 'generic': False, 'native_identity': 'sample-tool'}]
        if literal:
            value['release_units']['sample-tool']['changelog'] = '${{ secrets.SAMPLE_TOKEN }}.md'
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        answers = {'npm_units': 'sample-tool', 'npm_trusted_publishing': trusted, 'npm_dry_run': True}
        template, workflow, settled = render(Path(tmp.name), value, selected=['npm'] if selected else [],
                                             answers=answers if selected else {})
        parsed = check_parts(template, workflow, ['npm'] if selected else [], settled)
        result = subprocess.run(['actionlint', str(workflow)], text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return parsed, workflow.read_text()

    def test_selected_and_removed(self):
        parsed, _ = self.workflow()
        for name in ['publish-npm', 'verify-npm-clients']:
            job = parsed['jobs'][name]
            self.assertIn("needs.release-record.result == 'success'", job['if'])
            self.assertIn('release-record', job['needs'])
            self.assertEqual(job['env']['NPM_DRY_RUN'], 'true')
        self.assertIn('publish-npm', parsed['jobs']['verify-npm-clients']['needs'])
        self.assertEqual(parsed['jobs']['verify-npm-clients']['strategy']['matrix']['os'],
                         ['ubuntu-latest', 'ubuntu-24.04-arm', 'macos-15', 'windows-latest'])
        removed, text = self.workflow(selected=False)
        self.assertNotIn('publish-npm', removed['jobs'])
        self.assertNotIn('verify-npm-clients', removed['jobs'])
        self.assertNotIn('NPM_TOKEN', text)
        self.assertNotIn('NPM_MODEL', text)

    def test_trusted_and_token_credentials(self):
        trusted, text = self.workflow()
        self.assertNotIn('NPM_TOKEN', text)
        self.assertEqual(trusted['jobs']['publish-npm']['permissions']['id-token'], 'write')
        token, text = self.workflow(trusted=False)
        self.assertEqual(token['jobs']['preflight']['steps'][0]['env']['NPM_TOKEN'], '${{ secrets.NPM_TOKEN }}')
        publish = next(s for s in token['jobs']['publish-npm']['steps'] if s.get('name') == 'Publish or compare exact package bytes')
        self.assertEqual(publish['env']['NODE_AUTH_TOKEN'], '${{ secrets.NPM_TOKEN }}')
        self.assertNotIn('NPM_TOKEN', json.dumps(token['jobs']['verify-npm-clients']))

    def test_publish_model_data_stays_literal(self):
        parsed, _ = self.workflow(literal=True)
        value = parsed['jobs']['publish-npm']['env']['NPM_MODEL']
        self.assertNotIn('${{', value)
        self.assertEqual(json.loads(value)['release_units']['sample-tool']['changelog'], '${{ secrets.SAMPLE_TOKEN }}.md')

    def test_verify_model_data_stays_literal(self):
        parsed, _ = self.workflow(literal=True)
        value = parsed['jobs']['verify-npm-clients']['env']['NPM_MODEL']
        self.assertNotIn('${{', value)
        self.assertEqual(json.loads(value)['release_units']['sample-tool']['changelog'], '${{ secrets.SAMPLE_TOKEN }}.md')

    def test_emitted_entry_points_dry_run(self):
        parsed, _ = self.workflow(packages=True)
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / 'workflow.json'
            output.write_text(json.dumps(parsed))
            result = subprocess.run(['node', str(ROOT / 'tests/release/npm-rendered.cjs'),
                                     str(output), tmp], text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn('PASS emitted npm dry-run', result.stdout)
