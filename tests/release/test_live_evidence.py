# relationships: validates: release-process
import copy
import contextlib
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import live
from live_evidence import digest, encoded, save_new, WORKFLOW

REPO = 'example-org/sample-tool'
SHA = 'a' * 40


def clock(minute):
    return f'2026-01-01T00:{minute:02d}:00Z'


def snapshot(records, minute):
    return dict(repository=REPO, tag='1.2.3', complete=True, captured_at=clock(minute), records=copy.deepcopy(records),
                lookup={'status': 200, 'release': copy.deepcopy(records[0])} if records and not records[0]['draft'] else {'status': 404})


def record(assets=True, draft=False):
    return dict(id=20, tag_name='1.2.3', updated_at=clock(2), draft=draft, prerelease=False,
                assets=[dict(id=30 + i, name=name, updated_at=clock(2))
                        for i, name in enumerate(['sample.tar', 'SHA256SUMS'] if assets else [])])


def fixture(scenario='published-expected', assets=True, attempt=2):
    names = ['resolve', 'preflight', 'build-sample', 'build-sample-2', 'release-record', 'verify-sample', 'verify-sample-2']
    expected = {name: 'skipped' if name.endswith('-2') else 'success' for name in names}
    if scenario == 'missing-credential':
        expected = {name: 'success' if name == 'resolve' else 'failure' if name == 'preflight' else 'skipped' for name in names}
    case = dict(scenario=scenario, assets=assets, tag='1.2.3', version='1.2.3', expected_jobs=expected,
                expected_assets=['sample.tar', 'SHA256SUMS'] if assets else [], expect_success=scenario != 'missing-credential')
    receipt = dict(repository=REPO, source_sha=SHA, source_input=SHA, dispatch_ref='refs/heads/main', run_id='42',
                   attempt=attempt, workflow_id=7, workflow_revision=SHA, workflow_ref=f'{REPO}/{WORKFLOW}@refs/heads/main',
                   workflow_sha256=digest(b'workflow bytes'), case_sha256=digest(encoded(case)), source_committed_at=clock(1), created_at=clock(3))
    run = dict(id=42, repository={'full_name': REPO}, head_sha=SHA, head_branch='main', run_attempt=attempt, workflow_id=7,
               event='workflow_dispatch', path=WORKFLOW, status='completed', conclusion='success' if case['expect_success'] else 'failure',
               run_started_at=clock(10), updated_at=clock(30))
    marker = dict(repository=REPO, sha=SHA, ref='refs/heads/main', workflow_ref=receipt['workflow_ref'], workflow_sha=SHA,
                  run_id='42', run_attempt=str(attempt), event_name='workflow_dispatch', source_input=SHA)
    jobs = []
    for i, (name, state) in enumerate(expected.items()):
        minute = 10 + i * 2
        jobs.append(dict(id=100+i, name=name, run_id=42, head_sha=SHA, run_attempt=attempt, status='completed', conclusion=state,
                         started_at=clock(minute), completed_at=clock(minute+1), runner_id=0 if state == 'skipped' else 5,
                         steps=[] if state == 'skipped' else [dict(name='Run', started_at=clock(minute),
                               completed_at=clock(minute+1), conclusion=state)]))
    existing = [record(assets)] if scenario.startswith('published-') else []
    after = existing if scenario == 'missing-credential' else [record(assets)]
    evidence = dict(status='COMPLETE', receipt_sha256=digest(encoded(receipt)), run_endpoint=f'actions/runs/42/attempts/{attempt}',
                    jobs_endpoint=f'actions/runs/42/attempts/{attempt}/jobs?per_page=100', run=run,
                    repository={'full_name': REPO, 'default_branch':'main'},
                    workflow_revision=SHA, workflow_bytes='workflow bytes', resolve_logs='LIVE_IDENTITY='+json.dumps(marker),
                    jobs_pages=[dict(total_count=len(jobs), jobs=jobs)], record_logs=[{'job_id':104, 'logs':'upload failure'}],
                    ci=dict(repository={'full_name': REPO}, head_sha=SHA, event='push', path='.github/workflows/ci.yml',
                            status='completed', conclusion='success', updated_at=clock(5)), after=snapshot(after, 31))
    seed = dict(status='COMPLETE', repository=REPO, case_sha256=digest(encoded(case)),
                before=snapshot([], 1), after=snapshot(existing, 9),
                commands=[dict(command=['gh','api',f'repos/{REPO}/releases?per_page=100'],
                               input=None,exit=0,stdout='[]',stderr='')])
    return case, receipt, evidence, seed


class Evidence(unittest.TestCase):
    def check(self, values, previous=None):
        return live.check(REPO, '42', *values, previous=previous, before=values[2].get('before'))

    def rejected(self, values, diagnostic):
        with self.assertRaisesRegex(ValueError, diagnostic):
            self.check(values)

    def test_valid_per_attempt_evidence(self):
        result = self.check(fixture())
        self.assertEqual(result['status'], 'PASS')
        self.assertEqual(result['attempt'], 2)
        self.assertFalse(result['continuation'])

    def test_missing_external_receipt(self):
        case, _, evidence, seed = fixture()
        self.rejected((case, None, evidence, seed), 'Missing external receipt')

    def test_wrong_source_independent_of_workflow(self):
        values = fixture(); values[2]['run']['head_sha'] = 'b'*40
        self.rejected(values, 'Wrong source SHA')

    def test_wrong_workflow_id(self):
        values = fixture(); values[2]['run']['workflow_id'] = 99
        self.rejected(values, 'Wrong workflow identity')

    def test_wrong_workflow_revision(self):
        values = fixture(); values[2]['workflow_revision'] = 'b'*40
        self.rejected(values, 'Wrong observed workflow revision')

    def test_wrong_workflow_bytes(self):
        values = fixture(); values[2]['workflow_bytes'] += 'changed'
        self.rejected(values, 'Wrong workflow bytes')

    def test_wrong_attempt_at_actual_run_boundary(self):
        values = fixture(); values[2]['run']['run_attempt'] = 1
        self.rejected(values, 'Wrong run attempt')

    def test_historical_attempt_endpoint_cannot_supply_current_jobs(self):
        values = fixture(); values[2]['jobs_endpoint'] = 'actions/runs/42/jobs?per_page=100'
        self.rejected(values, 'Historical attempt endpoints differ')

    def test_job_missing_run_attempt_fails_closed(self):
        values = fixture(); del values[2]['jobs_pages'][0]['jobs'][0]['run_attempt']
        self.rejected(values, '^Missing job attempt$')

    def test_historical_job_cannot_mix_with_current_attempt(self):
        values = fixture(); values[2]['jobs_pages'][0]['jobs'][0]['run_attempt'] = 1
        self.rejected(values, '^Wrong job attempt$')

    def test_actual_dispatch_input_marker_required(self):
        values = fixture(); values[2]['resolve_logs'] = 'script printed but no runtime marker'
        self.rejected(values, 'Missing or mismatched actual dispatch')
        values = fixture(); values[2]['resolve_logs'] = values[2]['resolve_logs'].replace('"source_input": "'+SHA, '"source_input": "'+'b'*40)
        self.rejected(values, 'Missing or mismatched actual dispatch')

    def test_incomplete_run_and_ci(self):
        values = fixture(); values[2]['run']['status'] = 'in_progress'
        self.rejected(values, 'Run has not completed')
        values = fixture(); values[2]['ci']['head_sha'] = 'b'*40
        self.rejected(values, 'Missing exact-source successful push CI')

    def test_never_observed_build_is_not_skipped(self):
        values = fixture('missing-credential'); values[2]['jobs_pages'][0]['jobs'].pop(2)
        values[2]['jobs_pages'][0]['total_count'] -= 1
        self.rejected(values, 'Never-observed or unexpected jobs')

    def test_missing_credentials_cannot_have_build_start(self):
        values = fixture('missing-credential')
        self.assertEqual(self.check(values)['status'], 'PASS')
        values[2]['jobs_pages'][0]['jobs'][2]['runner_id'] = 5
        self.rejected(values, 'Build started despite missing credentials')

    def test_skipped_build_steps_contradict_no_start(self):
        values = fixture('missing-credential')
        values[2]['jobs_pages'][0]['jobs'][2]['steps'] = [dict(started_at=clock(15), conclusion='success')]
        self.rejected(values, 'Skipped job started steps')

    def test_job_and_step_time_contradictions(self):
        values = fixture(); values[2]['jobs_pages'][0]['jobs'][2]['completed_at'] = clock(1)
        self.rejected(values, 'Contradictory job timestamps')
        values = fixture(); values[2]['jobs_pages'][0]['jobs'][2]['steps'][0]['started_at'] = clock(1)
        self.rejected(values, 'Contradictory step timestamps')

    def test_missing_start_is_incomplete(self):
        values = fixture(); values[2]['jobs_pages'][0]['jobs'][2]['steps'] = []
        self.rejected(values, 'Missing job start evidence')

    def test_empty_publish_verification_requires_all_completions(self):
        values = fixture('empty-publish')
        self.assertEqual(self.check(values)['status'], 'PASS')
        verify = values[2]['jobs_pages'][0]['jobs'][5]
        verify.update(started_at=clock(16), completed_at=clock(17), steps=[dict(started_at=clock(16),completed_at=clock(17),conclusion='success')])
        self.rejected(values, 'Verification precedes required completion')

    def test_published_release_and_asset_metadata_drift(self):
        for mutate in (lambda r: r.update(updated_at=clock(25)),
                       lambda r: r.update(id=99),
                       lambda r: r['assets'][0].update(id=99),
                       lambda r: r['assets'][0].update(updated_at=clock(25))):
            with self.subTest(mutation=mutate):
                values = fixture(); after = values[2]['after']
                mutate(after['records'][0]); after['lookup']['release'] = copy.deepcopy(after['records'][0])
                self.rejected(values, 'metadata changed')

    def test_incomplete_inventory_and_wrong_exact_tag(self):
        values = fixture(); values[2]['after']['complete'] = False
        self.rejected(values, 'Incomplete or wrong Release snapshot')
        values = fixture(); values[2]['after']['lookup']['release']['id'] = 99
        self.rejected(values, 'Exact-tag lookup differs')

    def test_release_snapshot_time_contradiction(self):
        values = fixture(); values[3]['after']['captured_at'] = clock(11)
        self.rejected(values, 'snapshot time contradicts')

    def test_seed_refusal_and_partial_remain_unverified(self):
        for state in ('REFUSED', 'INCOMPLETE'):
            values = fixture(); values[3]['status'] = state
            self.rejected(values, 'Seed refused/partial')

    def continuation(self):
        values = fixture('no-release')
        case, receipt, evidence, seed = values
        partial = record(); partial['draft'] = True; partial['assets'] = partial['assets'][:1]
        seed['after']['captured_at'] = clock(5)
        evidence['before'] = snapshot([partial], 9)
        first_receipt = copy.deepcopy(receipt); first_receipt['run_id'] = '41'; first_receipt['attempt'] = 1
        first = copy.deepcopy(evidence)
        first.update(receipt_sha256=digest(encoded(first_receipt)), run_endpoint='actions/runs/41/attempts/1',
                     jobs_endpoint='actions/runs/41/attempts/1/jobs?per_page=100', after=snapshot([partial], 8))
        first['run'].update(id=41,run_attempt=1,conclusion='failure',run_started_at=clock(6),updated_at=clock(7))
        marker = json.loads(first['resolve_logs'].split('=',1)[1]); marker.update(run_id='41',run_attempt='1')
        first['resolve_logs'] = 'LIVE_IDENTITY='+json.dumps(marker)
        for job in first['jobs_pages'][0]['jobs']:
            job.update(run_id=41,run_attempt=1,started_at=clock(6),completed_at=clock(7))
            if job['name'] == 'release-record': job['conclusion'] = 'failure'
            if job['name'].startswith('verify-'): job['conclusion'] = 'skipped'
            job['steps'] = [] if job['conclusion'] == 'skipped' else [dict(started_at=clock(6),completed_at=clock(7),conclusion=job['conclusion'])]
        return values, dict(receipt=first_receipt,evidence=first)

    def test_partial_upload_continuation_completes_existing_release(self):
        values, previous = self.continuation()
        self.assertTrue(self.check(values,previous)['continuation'])

    def test_partial_continuation_missing_failed_and_mismatched(self):
        errors = {'missing': '^Missing first-attempt record logs$',
                  'failure': '^First attempt did not fail$',
                  'release': '^Continuation lost existing Release/assets$',
                  'source': '^Wrong source SHA$'}
        for field, error in errors.items():
            values, previous = self.continuation()
            if field == 'missing': previous['evidence']['record_logs'] = []
            if field == 'failure': previous['evidence']['run']['conclusion'] = 'success'
            # Preserve the draft ID so only the retained-partial equality can reject.
            if field == 'release': values[2]['before']['records'][0]['assets'] = []
            if field == 'source': previous['evidence']['run']['head_sha'] = 'b'*40
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, error):
                self.check(values,previous)

    def test_report_failure_nonzero_no_pass_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); values = fixture()
            paths = []
            for name, value in zip(('case','receipt','evidence','seed'),values):
                path = root / (name+'.json'); save_new(path,value); paths.append(path)
            paths[1].unlink()
            argv = ['check','--repo',REPO,'--run-id','42','--case',str(paths[0]),'--receipt',str(paths[1]),
                    '--evidence',str(paths[2]),'--seed',str(paths[3]),'--report',str(root/'report.json')]
            output = io.StringIO()
            with contextlib.redirect_stdout(output): self.assertEqual(live.main(argv),1)
            self.assertNotIn('PASS',output.getvalue())
            self.assertEqual(json.loads((root/'report.json').read_text())['status'],'UNVERIFIED')
            prior = (root/'report.json').read_bytes()
            with self.assertRaises(FileExistsError): live.main(argv)
            self.assertEqual((root/'report.json').read_bytes(),prior)


class CommandBoundary(unittest.TestCase):
    def test_bind_after_commit_keeps_source_fixed_and_receipt_immutable(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / 'source'; source.mkdir()
            def git(*args):
                return subprocess.check_output(['git','-C',str(source),*args],stderr=subprocess.STDOUT).decode().strip()
            git('init','-q'); git('config','user.name','Example Agent'); git('config','user.email','agent@example.invalid')
            workflow = source / WORKFLOW; workflow.parent.mkdir(parents=True); workflow.write_text('workflow bytes')
            (source/'.live-case.json').write_text(json.dumps(fixture()[0])); git('add','.'); git('commit','-qm','fixture')
            head = git('rev-parse','HEAD'); receipt = Path(tmp)/'receipt.json'
            result = live.bind(source,REPO,'42',2,7,'refs/heads/main',receipt)
            self.assertEqual(result['source_sha'],head)
            self.assertEqual(git('rev-parse','HEAD'),head)
            self.assertEqual(git('status','--porcelain'),'')
            self.assertEqual(result['workflow_sha256'],digest(workflow.read_bytes()))
            with self.assertRaises(FileExistsError): live.bind(source,REPO,'42',2,7,'refs/heads/main',receipt)
            workflow.write_text('uncommitted')
            with self.assertRaisesRegex(ValueError,'committed and clean'):
                live.bind(source,REPO,'42',2,7,'refs/heads/main',Path(tmp)/'other.json')

    def seed_boundary(self, scenario, fail):
        case = fixture(scenario)[0]; created = record(); created['draft'] = True; created['assets'] = []
        created['upload_url'] = 'https://uploads.example.invalid/assets{?name}'
        state = {'created':False, 'assets':[]}
        def run(command,input=None,**kwargs):
            endpoint = next(arg for arg in command if arg.startswith(('repos/','https://')))
            method = command[command.index('--method')+1] if '--method' in command else 'GET'
            status, result, stderr = 0, [], ''
            if method == 'POST' and endpoint.endswith('/releases'):
                if state['created']:
                    status,result,stderr = 1,{'message':'Validation Failed','errors':[{'code':'already_exists'}]},'HTTP 422 duplicate Release refusal'
                else: state['created'] = True; result = created
            elif method == 'POST' and endpoint.startswith('https://'):
                if fail == 'upload' and state['assets']:
                    status,result,stderr = 1,{'message':'upload refused'},'HTTP 503 upload failed'
                else:
                    state['assets'].append(dict(id=30,name=endpoint.split('name=')[1],updated_at=clock(2)))
                    result = state['assets'][-1]
            elif '/releases/tags/' in endpoint:
                status,result,stderr = 1,{'message':'Not Found'},'HTTP 404 Not Found'
            elif endpoint.endswith('/assets?per_page=100'): result = state['assets']
            elif endpoint.endswith('/releases?per_page=100'): result = [created] if state['created'] else []
            else: raise AssertionError(f'Unexpected command {command}')
            return subprocess.CompletedProcess(command,status,json.dumps(result),stderr)
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp)/'seed'
            with patch('live.subprocess.run',side_effect=run):
                with self.assertRaisesRegex(ValueError,'UNVERIFIED'):
                    live.seed(REPO,case,output)
            saved = json.loads((output/'seed.json').read_text())
            self.assertEqual(saved['status'],'INCOMPLETE')
            commands = [json.loads(line) for line in (output/'commands.jsonl').read_text().splitlines()]
            self.assertEqual(saved['commands'],commands)
            self.assertEqual(saved['before']['records'],[])
            self.assertEqual(saved['after']['records'][0]['id'],20)
            self.assertTrue(saved['after']['records'][0]['draft'])
            return saved, commands

    def test_duplicate_release_api_refusal_preserves_exact_request_result_and_partial_state(self):
        saved, commands = self.seed_boundary('two-releases','duplicate')
        failed = [entry for entry in commands if entry['exit'] and '422' in entry['stderr']]
        self.assertEqual(len(failed),1)
        self.assertEqual(json.loads(failed[0]['input'])['body'],'Second seeded sample')
        self.assertEqual(json.loads(failed[0]['stdout'])['errors'][0]['code'],'already_exists')
        self.assertEqual(len(saved['after']['records']),1)

    def test_failed_upload_retains_first_asset_and_original_seed_failure(self):
        saved, commands = self.seed_boundary('published-expected','upload')
        self.assertEqual([a['name'] for a in saved['after']['records'][0]['assets']],['sample.tar'])
        self.assertEqual(sum('503' in entry['stderr'] for entry in commands),1)

    def test_observe_uses_attempt_endpoints_and_retains_raw_logs_bytes(self):
        import base64
        case,receipt,evidence,_ = fixture()
        endpoints = []
        def run(command,**kwargs):
            endpoint = command[-1]; endpoints.append(endpoint)
            responses = {
                evidence['run_endpoint']: evidence['run'], evidence['jobs_endpoint']: evidence['jobs_pages'][0],
                'actions/runs/9': evidence['ci'],
                'contents/'+WORKFLOW+'?ref='+SHA: {'content':base64.b64encode(b'workflow bytes').decode()},
                'actions/jobs/100/logs': evidence['resolve_logs'],
                'actions/jobs/104/logs': 'upload failed',
                'releases?per_page=100': evidence['after']['records'],
                'releases/20/assets?per_page=100': evidence['after']['records'][0]['assets'],
                'releases/tags/1.2.3': evidence['after']['records'][0],
            }
            responses = {f'repos/{REPO}/{path}': value for path, value in responses.items()}
            responses[f'repos/{REPO}'] = evidence['repository']
            self.assertIn(endpoint, responses, 'Noncanonical API route')
            value = responses[endpoint]
            return subprocess.CompletedProcess(command,0,value if endpoint.endswith('/logs') else json.dumps(value),'')
        with tempfile.TemporaryDirectory() as tmp,patch('live.subprocess.run',side_effect=run):
            output = Path(tmp)/'observe'; result = live.observe(receipt,case,'9',output)
            self.assertEqual(result['status'],'COMPLETE')
            self.assertEqual(result['workflow_bytes'],'workflow bytes')
            self.assertEqual(result['resolve_logs'],evidence['resolve_logs'])
            self.assertEqual(endpoints[:2],[f'repos/{REPO}/' + evidence['run_endpoint'],
                                            f'repos/{REPO}/' + evidence['jobs_endpoint']])
            self.assertIn(f'repos/{REPO}', endpoints)
            self.assertNotIn(f'repos/{REPO}/', endpoints)
            self.assertTrue((output/'commands.jsonl').is_file())
            self.assertFalse(any('/runs/42/jobs' in endpoint for endpoint in endpoints))

    def test_observe_failure_retains_incomplete_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp)/'observe'
            with patch('live.subprocess.run',return_value=subprocess.CompletedProcess(['gh'],1,'raw failure','HTTP 403')):
                with self.assertRaisesRegex(ValueError,'UNVERIFIED'): live.observe(fixture()[1],fixture()[0],'9',output)
            saved = json.loads((output/'evidence.json').read_text())
            self.assertEqual(saved['status'],'INCOMPLETE')
            self.assertIn('raw failure',(output/'commands.jsonl').read_text())

    def test_offline_cli_pass_needs_no_command_or_network(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); values = fixture(); paths = []
            for name,value in zip(('case','receipt','evidence','seed'),values):
                path = root/(name+'.json'); save_new(path,value); paths.append(path)
            argv = ['check','--repo',REPO,'--run-id','42','--case',str(paths[0]),'--receipt',str(paths[1]),
                    '--evidence',str(paths[2]),'--seed',str(paths[3]),'--report',str(root/'report.json')]
            with patch('live.subprocess.run',side_effect=AssertionError('offline checker invoked a command')), \
                 contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(live.main(argv),0)
            self.assertEqual(json.loads((root/'report.json').read_text())['status'],'PASS')

    def test_receipt_cannot_admit_nondefault_dispatch(self):
        values = fixture(); values[2]['repository']['default_branch'] = 'other'
        with self.assertRaisesRegex(ValueError,'observed default branch'):
            live.check(REPO,'42',*values)

    def test_retained_seed_declaration_and_future_metadata_are_checked(self):
        values = fixture(); values[3]['after']['records'][0]['prerelease'] = True
        values[3]['after']['lookup']['release'] = copy.deepcopy(values[3]['after']['records'][0])
        with self.assertRaisesRegex(ValueError,'Seed state differs'):
            live.check(REPO,'42',*values)
        values = fixture(); values[2]['after']['records'][0]['updated_at'] = clock(40)
        with self.assertRaisesRegex(ValueError,'Release updated after snapshot'):
            live.check(REPO,'42',*values)

    def test_missing_seed_commands_or_preserved_failure_cannot_pass(self):
        values = fixture(); values[3]['commands'] = []
        with self.assertRaisesRegex(ValueError,'Missing original seed'):
            live.check(REPO,'42',*values)
        values = fixture(); values[3]['commands'][0].update(exit=1,stderr='HTTP 422 refused')
        with self.assertRaisesRegex(ValueError,'Failed seed command'):
            live.check(REPO,'42',*values)

    def test_interrupted_report_stays_incomplete(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); values = fixture(); paths = []
            for name,value in zip(('case','receipt','evidence','seed'),values):
                path = root/(name+'.json'); save_new(path,value); paths.append(path)
            argv = ['check','--repo',REPO,'--run-id','42','--case',str(paths[0]),'--receipt',str(paths[1]),
                    '--evidence',str(paths[2]),'--seed',str(paths[3]),'--report',str(root/'report.json')]
            def interrupted(path,value):
                if path.name.endswith('.complete'): raise OSError('interrupted before terminal report')
                save_new(path,value)
            with patch('live_evidence.save_new',side_effect=interrupted), self.assertRaisesRegex(OSError,'interrupted'):
                live.main(argv)
            self.assertEqual(json.loads((root/'report.json').read_text())['status'],'INCOMPLETE')

    def test_seed_after_readback_keyerror_preserves_original_upload_failure(self):
        case = fixture('published-expected')[0]
        created = dict(record(draft=True), upload_url='https://uploads.example.invalid/assets{?name}')
        responses = [
            (0, [], ''),
            (1, {'message': 'Not Found'}, 'HTTP 404 Not Found'),
            (0, created, ''),
            (0, {'id': 30, 'name': 'sample.tar'}, ''),
            (1, {'message': 'upload failed'}, 'HTTP 503 upload failed'),
            (0, [{'id': 20}], ''),  # Malformed terminal readback: missing tag_name.
        ]
        def run(command, **kwargs):
            status, value, stderr = responses.pop(0)
            return subprocess.CompletedProcess(command, status, json.dumps(value), stderr)
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp)/'seed'
            with patch('live.subprocess.run', side_effect=run):
                with self.assertRaisesRegex(ValueError, '^Seed refused/partial: UNVERIFIED;'):
                    live.seed(REPO, case, output)
            saved = json.loads((output/'seed.json').read_text())
            commands = [json.loads(line) for line in (output/'commands.jsonl').read_text().splitlines()]
            self.assertFalse(responses)
            self.assertEqual(saved['status'], 'INCOMPLETE')
            self.assertEqual(saved['commands'], commands)
            self.assertEqual(saved['readback_error'], "'tag_name'")
            self.assertIn('non-zero exit status 1', saved['error'])
            self.assertEqual(saved['before']['records'], [])
            self.assertNotIn('after', saved)
            self.assertEqual(commands[3]['exit'], 0)
            self.assertEqual(json.loads(commands[3]['stdout'])['name'], 'sample.tar')
            self.assertEqual(commands[4]['input'], 'sample\n')
            self.assertEqual(commands[4]['stderr'], 'HTTP 503 upload failed')
            self.assertEqual(json.loads(commands[4]['stdout']), {'message': 'upload failed'})
            self.assertEqual(json.loads(commands[5]['stdout']), [{'id': 20}])
