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

    def test_live_check_fails_closed_for_incomplete_run(self):
        output = io.StringIO()
        with patch('live.api', return_value=[{'status': 'in_progress'}]), contextlib.redirect_stdout(output):
            with self.assertRaisesRegex(AssertionError, 'not completed'):
                live.check('example-org/sample-tool', '1', {})
        self.assertNotIn('PASS', output.getvalue())

    def test_live_check_fails_closed_for_wrong_workflow(self):
        output = io.StringIO()
        run = {'status': 'completed', 'event': 'workflow_dispatch', 'path': '.github/workflows/other.yml'}
        with patch('live.api', return_value=[run]), contextlib.redirect_stdout(output):
            with self.assertRaisesRegex(AssertionError, 'Wrong workflow'):
                live.check('example-org/sample-tool', '1', {})
        self.assertNotIn('PASS', output.getvalue())

    def test_live_check_fails_closed_for_missing_jobs(self):
        output = io.StringIO()
        responses = [[{'status': 'completed', 'event': 'workflow_dispatch', 'path': '.github/workflows/release.yml', 'conclusion': 'failure'}], [{'jobs': []}]]
        with patch('live.api', side_effect=responses), contextlib.redirect_stdout(output):
            with self.assertRaises(AssertionError):
                live.check('example-org/sample-tool', '1', {'expect_success': False, 'expected_jobs': {'resolve': 'failure'}})
        self.assertNotIn('PASS', output.getvalue())

    def test_interrupted_mutation_report_cannot_keep_a_previous_pass(self):
        import mutations
        with tempfile.TemporaryDirectory() as tmp:
            report = Path(tmp) / 'mutations.json'
            report.write_text('{"status":"PASS"}')
            case = ('release/template.yml', 'absent-mutation-source', '', 'unused', 'node')
            with patch('mutations.CASES', [case]):
                with self.assertRaisesRegex(AssertionError, 'source not found'):
                    mutations.run(report)
            import json
            self.assertEqual(json.loads(report.read_text())['status'], 'INCOMPLETE')
