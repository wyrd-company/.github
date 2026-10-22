# relationships: validates: release-process
import contextlib
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from harness import load
import live


class LiveProof(unittest.TestCase):
    def test_every_live_case_prepares_and_passes_actionlint(self):
        for scenario in live.SCENARIOS:
            for assets in (False, True):
                with self.subTest(scenario=scenario, assets=assets), tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stdout(io.StringIO()):
                    live.prepare(Path(tmp), scenario, assets)
                    workflow = load(Path(tmp) / 'project/.github/workflows/release.yml')
                    self.assertIn('release-record', workflow['jobs'])
                    if scenario.startswith('empty-publish'):
                        self.assertFalse(any(job.startswith('publish-') for job in workflow['jobs']))
                    self.assertEqual(sum('upload-artifact' in step.get('uses', '') for step in workflow['jobs']['build-sample']['steps']), int(assets))

    def test_interrupted_mutation_report_cannot_keep_a_previous_pass(self):
        import mutations
        with tempfile.TemporaryDirectory() as tmp:
            report = Path(tmp) / 'mutations.json'
            report.write_text('{"status":"PASS"}')
            case = mutations.Mutation('release/template.yml', 'absent-mutation-source', '', ('unused',), 'node')
            with patch('mutations.CASES', [case]):
                with self.assertRaisesRegex(AssertionError, 'expected 1 matches, observed 0'):
                    mutations.run(report)
            import json
            self.assertEqual(json.loads(report.read_text())['status'], 'INCOMPLETE')

    def test_live_bare_tags_config_baseline_and_credential_declarations(self):
        import json
        import os
        import subprocess
        import jsonschema
        from harness import ROOT, YAML_RT
        provenance = json.loads((ROOT / 'release/schema-provenance.json').read_text())['release-model-1.schema.yml']
        schema = YAML_RT.load(subprocess.check_output(['git', '-C', os.environ['INTENTIONAL_DIR'], 'show',
            provenance['commit'] + ':docs/specifications/release-workspace.schema.yml'], text=True))
        for scenario in ('baseline', 'missing-credential', 'no-release'):
            with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stdout(io.StringIO()):
                live.prepare(Path(tmp), scenario, False)
                project = Path(tmp) / 'project'
                config = load(project / '.intentional/config.yml')
                jsonschema.validate(config, schema)
                document = json.loads((project / '.intentional/release-model.json').read_text())
                case = json.loads((project / '.live-case.json').read_text())
                self.assertEqual(case['tag'], '1.2.3')
                self.assertEqual(config['release-units']['sample-tool']['tags']['primary']['template'], '{version}')
                self.assertEqual(document['release_units']['sample-tool']['tags']['primary']['template'], '{version}')
                self.assertEqual(config['release-units']['sample-docs']['release-record'], 'none')
                self.assertEqual(case['expected_jobs']['resolve'], 'failure' if scenario == 'baseline' else 'success')
                workflow = load(project / '.github/workflows/release.yml')
                preflight = workflow['jobs']['preflight']['steps'][0]
                self.assertEqual('SAMPLE_TOKEN' in preflight.get('env', {}), scenario == 'missing-credential')
                self.assertIn('SAMPLE_CHANNEL', preflight['env'])
                self.assertIn('SAMPLE_ENV_TOKEN', workflow['jobs']['preflight-sample']['steps'][0]['env'])
                identity = workflow['jobs']['resolve']['steps'][0]
                self.assertNotIn('toJSON(github)', identity['run'] + str(identity['env']))
                self.assertIn('LIVE_SOURCE_INPUT', identity['env'])
                values = {key: 'fixture-' + key for key in identity['env']}
                result = subprocess.run(['bash', '-e', '-c', identity['run']],
                    env=dict(os.environ, **values), text=True, capture_output=True, check=True)
                marker = json.loads(result.stdout.split('LIVE_IDENTITY=', 1)[1])
                self.assertEqual(marker['source_input'], values['LIVE_SOURCE_INPUT'])
                self.assertEqual(marker['workflow_sha'], values['LIVE_WORKFLOW_SHA'])
                self.assertEqual(marker['run_attempt'], values['LIVE_RUN_ATTEMPT'])

    def test_live_guide_agrees_with_evidence_commands_and_bare_tag(self):
        import json
        from harness import ROOT
        text = (ROOT / 'tests/release/README.md').read_text().split('## Live proof procedure')[1]
        self.assertNotIn('sample-tool-1.2.3', text)
        self.assertIn('Leave `SAMPLE_TOKEN` absent', text)
        self.assertIn('repository variable', text)
        self.assertIn('environment secret', text)
        for argument in ('--receipt', '--evidence', '--seed', '--report', '--previous', '--before'):
            self.assertIn(argument, text)
        for scenario in live.SCENARIOS:
            self.assertIn('`' + scenario + '`', text)
        self.assertEqual(len(live.SCENARIOS) * 2, 28)
        self.assertIn('22 required live cases', text)
        self.assertEqual(json.loads((ROOT / 'release/schema-provenance.json').read_text())['release-model-1.schema.yml']['commit'],
                         'acbb793427b8de35ca84c9a2e9f371b71738b230')
