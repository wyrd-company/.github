# relationships: validates: release-process
"""Exercise mutation reporting with real Python tests in a disposable Git repo."""
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import mutations


class MutationRunner(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory()
        self.addCleanup(self.scratch.cleanup)
        self.root = Path(self.scratch.name)
        subprocess.run(['git', 'init', '-q', str(self.root)], check=True)
        subprocess.run(['git', '-C', str(self.root), '-c', 'user.name=Example',
            '-c', 'user.email=example@example.test', 'commit', '-q', '--allow-empty', '-m', 'fixture'], check=True)
        self.target = self.root / 'guards.py'
        self.original = b'first = True\nsecond = True\n'
        self.target.write_bytes(self.original)
        tests = self.root / 'tests/release'
        tests.mkdir(parents=True)
        (tests / 'test_probe.py').write_text('''import runpy
import unittest

class Probe(unittest.TestCase):
    def test_first(self):
        self.assertTrue(runpy.run_path('guards.py')['first'])
    def test_second(self):
        self.assertTrue(runpy.run_path('guards.py')['second'])
''')
        self.case = mutations.Mutation('guards.py', 'True', 'False',
            ('test_probe.Probe.test_first', 'test_probe.Probe.test_second'), 'python')
        self.report = self.root / 'report.json'

    def run_cases(self, cases):
        mutations.run(self.report, root=self.root, cases=cases)
        return json.loads(self.report.read_text())

    def test_each_repeated_site_is_applied_killed_and_restored(self):
        report = self.run_cases([self.case])
        self.assertEqual(report['status'], 'PASS')
        self.assertEqual([r['site'] for r in report['results']], ['1:1', '1:2'])
        self.assertEqual([r['line'] for r in report['results']], [1, 2])
        for result in report['results']:
            self.assertEqual(result['observed_matches'], result['expected_matches'])
            self.assertEqual(result['applications'], result['expected_applications'])
            self.assertTrue(result['killed'])
            self.assertNotEqual(result['exit_code'], 0)
            self.assertTrue(result['restored'])
            self.assertEqual(result['original_sha256'], result['restored_sha256'])
            self.assertNotEqual(result['original_sha256'], result['mutated_sha256'])
        self.assertEqual(self.target.read_bytes(), self.original)

    def test_zero_and_unexpected_match_counts_fail_before_running(self):
        for old, tests in [('absent', ('test_probe.Probe.test_first',)),
                           ('True', ('test_probe.Probe.test_first',))]:
            with self.subTest(old=old):
                case = mutations.Mutation('guards.py', old, 'False', tests, 'python')
                with self.assertRaisesRegex(AssertionError, 'expected .* matches, observed'):
                    self.run_cases([case])
                self.assertEqual(json.loads(self.report.read_text())['status'], 'INCOMPLETE')
                self.assertEqual(self.target.read_bytes(), self.original)

    def test_empty_inventory_cannot_pass(self):
        with self.assertRaisesRegex(AssertionError, 'inventory is empty'):
            self.run_cases([])
        self.assertEqual(json.loads(self.report.read_text())['status'], 'INCOMPLETE')

    def test_surviving_second_site_fails_the_battery(self):
        case = mutations.Mutation('guards.py', 'True', 'False',
            ('test_probe.Probe.test_first', 'test_probe.Probe.test_first'), 'python')
        with self.assertRaisesRegex(SystemExit, 'Mutation proof FAILED'):
            self.run_cases([case])
        report = json.loads(self.report.read_text())
        self.assertEqual(report['status'], 'FAIL')
        self.assertEqual([r['killed'] for r in report['results']], [True, False])
        self.assertEqual(self.target.read_bytes(), self.original)

    def test_interrupted_test_leaves_incomplete_report_and_restores_source(self):
        real_run = subprocess.run

        def interrupt(command, **kwargs):
            if command[0] == 'git':
                return real_run(command, **kwargs)
            raise KeyboardInterrupt

        with patch.object(mutations.subprocess, 'run', side_effect=interrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.run_cases([self.case])
        report = json.loads(self.report.read_text())
        self.assertEqual(report['status'], 'INCOMPLETE')
        self.assertEqual(len(report['results']), 1)
        self.assertTrue(report['results'][0]['restored'])
        self.assertEqual(self.target.read_bytes(), self.original)

    def test_empty_or_unchanged_mutation_is_refused(self):
        for old, new in [('', 'False'), ('True', 'True')]:
            with self.subTest(old=old):
                case = mutations.Mutation('guards.py', old, new, self.case.tests, 'python')
                with self.assertRaisesRegex(AssertionError, 'empty or unchanged mutation'):
                    self.run_cases([case])
                self.assertEqual(json.loads(self.report.read_text())['status'], 'INCOMPLETE')

    def test_failed_application_cannot_be_a_kill(self):
        real_write = Path.write_text

        def skip_target(path, value, **kwargs):
            if path != self.target:
                return real_write(path, value, **kwargs)

        with patch.object(Path, 'write_text', skip_target):
            with self.assertRaises((AssertionError, SystemExit)) as raised:
                self.run_cases([self.case])
            self.assertIsInstance(raised.exception, AssertionError)
            self.assertIn('Mutation application failed', str(raised.exception))
        report = json.loads(self.report.read_text())
        self.assertEqual(report['status'], 'INCOMPLETE')
        self.assertEqual(report['results'][0]['applications'], 0)
        self.assertFalse(report['results'][0]['killed'])
        self.assertTrue(report['results'][0]['restored'])

    def test_changed_head_cannot_pass(self):
        real_run = subprocess.run
        moved = False

        def advance(command, **kwargs):
            nonlocal moved
            result = real_run(command, **kwargs)
            if '-m' in command and not moved:
                moved = True
                real_run(['git', '-C', str(self.root), '-c', 'user.name=Example',
                    '-c', 'user.email=example@example.test', 'commit', '-q', '--allow-empty', '-m', 'advance'], check=True)
            return result

        with patch.object(mutations.subprocess, 'run', advance):
            with self.assertRaisesRegex(SystemExit, 'Mutation proof FAILED'):
                self.run_cases([self.case])
        report = json.loads(self.report.read_text())
        self.assertEqual(report['status'], 'FAIL')
        self.assertNotEqual(report['head'], report['final_head'])

    def test_changed_source_cannot_use_stale_inventory(self):
        real_inventory = mutations.inventory

        def change(*args):
            sites = real_inventory(*args)
            self.target.write_bytes(b'first = False\nsecond = True\n')
            return sites

        with patch.object(mutations, 'inventory', change):
            with self.assertRaises((AssertionError, SystemExit)) as raised:
                self.run_cases([self.case])
            self.assertIsInstance(raised.exception, AssertionError)
            self.assertIn('Source changed since inventory', str(raised.exception))
        self.assertEqual(json.loads(self.report.read_text())['status'], 'INCOMPLETE')

    def test_runner_error_is_not_a_named_assertion_kill(self):
        case = mutations.Mutation('guards.py', 'True', 'False',
            ('missing.Probe.test_first', 'missing.Probe.test_second'), 'python')
        with self.assertRaises(SystemExit):
            self.run_cases([case])
        self.assertTrue(all(not r['killed'] for r in json.loads(self.report.read_text())['results']))
