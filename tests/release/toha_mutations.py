#!/usr/bin/env python3
# relationships: validates: release-process, template-composition
"""Sole-kill the real part path using the shared fail-closed mutation runner."""
import argparse
from pathlib import Path

import mutations

mutations.CASES.clear()
TEST = 'test_toha_updates.TohaUpdates.test_render_helper_uses_real_part_flags'
mutations.mutation('tests/release/harness.py',
    "part_question = next(q for q in config['interview'] if q.get('id') == 'targets')",
    "part_question = next(q for q in config['interview'] if q.get('id') == 'targets'); part_question['parts'] = False",
    TEST, 'python')
mutations.mutation('tests/release/harness.py', "command.extend(['--with', part])", 'pass', TEST, 'python')
mutations.mutation('tests/release/harness.py', "supplied = dict(ci_workflow='ci.yml')",
    "supplied = dict(targets=selected, ci_workflow='ci.yml')", TEST, 'python')

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    args.report.parent.mkdir(parents=True, exist_ok=True)
    mutations.run(args.report)
