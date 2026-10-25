# relationships: validates: release-process
import copy
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import harness

import jsonschema
from harness import ROOT, check_parts, dump, load, prepare, render
from model import model


class ReleaseTemplate(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory()
        self.addCleanup(self.scratch.cleanup)
        self.root = Path(self.scratch.name)

    def sample(self, count=1, **options):
        template, workflow, answers = render(self.root, model(), samples=count, **options)
        for number in range(count):
            suffix = '_' + str(number + 1) if number else ''
            answers.setdefault('sample_target_units' + suffix, 'sample-tool' if number == 0 else 'sample-docs')
            answers.setdefault('trusted_publishing' + suffix, False)
        selected = options.get('selected', ['sample-target' if i == 0 else f'sample-target-{i + 1}' for i in range(count)])
        return template, workflow, answers, check_parts(template, workflow, selected, answers)

    def test_document_contract_and_no_hooks(self):
        config = load(ROOT / 'templates/release/template.yml')
        self.assertEqual(config['schemas'], 'schemas')
        self.assertEqual(config['interview'][0], dict(id='release_model', type='document', prompt='Intentional Release Model',
            schema='https://intentional.foo/schemas/release-model/1', input='.intentional/release-model.json'))
        self.assertEqual(config['interview'][1]['parts'], True)
        self.assertEqual(config['interview'][1]['type'], 'multiselect')
        self.assertNotIn('hooks', config)
        self.assertFalse((ROOT / '.intentional').exists())

    def test_vendored_schema_exact_source(self):
        source = os.environ['INTENTIONAL_DIR']
        provenance = json.loads((ROOT / 'templates/release/schema-provenance.json').read_text())['release-model-1.schema.yml']
        self.assertEqual(provenance['path'], 'docs/specifications/release-model.schema.yml')
        expected = subprocess.check_output(['git', '-C', source, 'show', provenance['commit'] + ':' + provenance['path']])
        self.assertEqual((ROOT / 'templates/release/schemas/release-model-1.schema.yml').read_bytes(), expected)
        jsonschema.validate(model(), load(ROOT / 'templates/release/schemas/release-model-1.schema.yml'))

    def test_generic_projection_survives_document_admission_and_render(self):
        value = model()
        value['release_units']['sample-tool']['projections'] = [{
            'adapter': 'toml', 'file': 'package.toml', 'workspace_file': 'package.toml',
            'mode': 'committed', 'generic': True, 'pointer': 'package.version',
        }]
        _, workflow, _ = render(self.root, value)
        env = next(s['env'] for s in load(workflow)['jobs']['resolve']['steps'] if s.get('id') == 'resolve')
        self.assertEqual(json.loads(env['RELEASE_MODEL']), value)

    def test_base_dispatch_outputs_and_pins(self):
        _, workflow, _ = render(self.root, model())
        parsed = check_parts(self.root / 'release', workflow, [], {})
        self.assertEqual(set(parsed['on']), {'workflow_dispatch'})
        self.assertEqual(set(parsed['on']['workflow_dispatch']['inputs']), {'source-sha'})
        self.assertEqual(parsed['permissions'], {})
        self.assertEqual(parsed['concurrency'], {'group': 'release', 'cancel-in-progress': False})
        self.assertEqual(set(parsed['jobs']['resolve']['outputs']), {'release', 'source_sha', 'units', 'releases', 'record_units', 'record'})
        for key, value in parsed['jobs']['resolve']['outputs'].items():
            self.assertEqual(value, '${{ steps.resolve.outputs.' + key + ' }}')
        step = next(s for s in parsed['jobs']['resolve']['steps'] if s.get('id') == 'resolve')
        for key, value in {'SOURCE_SHA': '${{ inputs.source-sha }}', 'DISPATCH_REF': '${{ github.ref }}',
                           'DISPATCH_SHA': '${{ github.sha }}',
                           'DEFAULT_BRANCH': '${{ github.event.repository.default_branch }}'}.items():
            self.assertEqual(step['env'][key], value)
        self.assertEqual(parsed['jobs']['resolve']['permissions'], {'contents': 'read', 'actions': 'read'})
        self.assertEqual(parsed['jobs']['release-record']['permissions'], {'contents': 'write'})
        self.assertEqual(parsed['jobs']['release-record']['strategy']['matrix']['unit'], '${{ fromJSON(needs.resolve.outputs.record_units) }}')
        subprocess.run(['actionlint', str(workflow)], check=True)

    def test_all_none_omits_record_job(self):
        value = model()
        value['release_units']['sample-tool']['release_record'] = 'none'
        _, workflow, _ = render(self.root, value, samples=1)
        parsed = load(workflow)
        self.assertNotIn('release-record', parsed['jobs'])
        self.assertNotIn('release-record', parsed['jobs']['publish-sample']['if'])
        subprocess.run(['actionlint', str(workflow)], check=True)

    def test_latest_question_only_multiple_required(self):
        value = model()
        value['release_units']['sample-docs']['release_record'] = 'required'
        _, workflow, _ = render(self.root, value, answers={'latest_unit': 'sample-docs'})
        self.assertEqual(load(workflow)['jobs']['release-record']['env']['LATEST_UNIT'], 'sample-docs')

    def test_part_graph_all_stages_and_units(self):
        _, workflow, _, parsed = self.sample(2)
        jobs = parsed['jobs']
        self.assertEqual(list(jobs), ['resolve', 'preflight', 'preflight-sample', 'build-sample', 'build-sample-2',
                                     'release-record', 'publish-sample', 'publish-sample-2', 'verify-sample', 'verify-sample-2'])
        preflight = ['resolve', 'preflight', 'preflight-sample']
        for name in ('build-sample', 'build-sample-2'):
            self.assertEqual(jobs[name]['needs'], preflight)
        self.assertEqual(jobs['release-record']['needs'], preflight + ['build-sample', 'build-sample-2'])
        for name in ('publish-sample', 'publish-sample-2'):
            self.assertEqual(jobs[name]['needs'], preflight + ['build-sample', 'build-sample-2', 'release-record'])
        for name in ('verify-sample', 'verify-sample-2'):
            self.assertEqual(jobs[name]['needs'], preflight + ['build-sample', 'build-sample-2', 'release-record', 'publish-sample', 'publish-sample-2'])
        for name in list(jobs)[3:]:
            condition = jobs[name]['if']
            self.assertIn("!cancelled() && !failure() && needs.resolve.result == 'success'", condition)
            for job in preflight[1:]:
                self.assertIn(f"needs.{job}.result == 'success'", condition)
        self.assertIn("needs.release-record.result == 'success'", jobs['publish-sample']['if'])
        self.assertNotIn("needs.release-record.result", jobs['publish-sample-2']['if'])
        self.assertIn("contains(fromJSON(needs.resolve.outputs.units), 'sample-docs')", jobs['build-sample-2']['if'])
        self.assertIn("needs.resolve.outputs.record == 'true'", jobs['release-record']['if'])
        self.assertEqual(jobs['publish-sample']['env']['SAMPLE_SETTING'], 'literal-value')
        subprocess.run(['actionlint', str(workflow)], check=True)

    def test_empty_publish_stage_still_waits_for_build_and_record(self):
        _, _, _, parsed = self.sample(2, omit_publish=True)
        for name in ('verify-sample', 'verify-sample-2'):
            self.assertEqual(parsed['jobs'][name]['needs'], ['resolve', 'preflight', 'preflight-sample', 'build-sample', 'build-sample-2', 'release-record'])

    def test_selection_drops_unselected_jobs_and_credentials(self):
        _, _, _, parsed = self.sample(1, selected=[])
        self.assertEqual(list(parsed['jobs']), ['resolve', 'preflight', 'release-record'])

    def test_trusted_publishing_omits_only_selected_credential(self):
        _, _, _, parsed = self.sample(answers={'trusted_publishing': True})
        step = parsed['jobs']['preflight']['steps'][0]
        self.assertNotIn('SAMPLE_TOKEN', step['env'])
        self.assertEqual(step['env']['SAMPLE_CHANNEL'], '${{ vars.SAMPLE_CHANNEL }}')
        self.assertEqual(parsed['jobs']['preflight-sample']['environment'], 'sample')
        self.assertEqual(parsed['jobs']['preflight-sample']['steps'][0]['env']['SAMPLE_ENV_TOKEN'], '${{ secrets.SAMPLE_ENV_TOKEN }}')

    def test_duplicate_names_checked_once_per_environment(self):
        from ruamel.yaml.constructor import DuplicateKeyError
        try:
            _, _, _, parsed = self.sample(2)
        except DuplicateKeyError:
            self.fail('Credential dedup generated duplicate env keys')
        script = parsed['jobs']['preflight']['steps'][0]['with']['script']
        self.assertEqual(script.count('"name":"SAMPLE_TOKEN"'), 1)

    def test_emitted_runtime_entry_points_execute(self):
        _, workflow, _, parsed = self.sample()
        document = self.root / 'workflow.json'
        document.write_text(json.dumps(parsed))
        for assets in (False, True):
            project = self.root / ('assets' if assets else 'no-assets')
            project.mkdir()
            run = subprocess.run(['node', str(ROOT / 'tests/release/rendered.cjs'), str(document), 'assets' if assets else 'none'],
                                 cwd=project, text=True, capture_output=True)
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
            evidence = json.loads(run.stdout)
            self.assertEqual({key: evidence[key] for key in ('resolved', 'preflight', 'record', 'assets')},
                             dict(resolved=True, preflight=True, record=True, assets=assets))
            self.assertEqual(evidence['events'][-1], 'read')

    def test_extra_condition_and_reserved_environment_are_validated(self):
        template, workflow, answers, parsed = self.sample()
        parsed['jobs']['publish-sample']['if'] += ' && (AlWaYs())'
        dump(workflow, parsed)
        with self.assertRaises(jsonschema.ValidationError):
            check_parts(template, workflow, ['sample-target'], answers)
        parsed['jobs']['publish-sample']['if'] = parsed['jobs']['publish-sample']['if'].replace('AlWaYs()', 'true')
        parsed['jobs']['publish-sample']['env']['GITHUB_TOKEN'] = 'forbidden'
        dump(workflow, parsed)
        with self.assertRaises(jsonschema.ValidationError):
            check_parts(template, workflow, ['sample-target'], answers)

    def test_duplicate_job_keys_across_parts_fail(self):
        template, workflow, answers, _ = self.sample(2)
        path = template / 'targets/sample-target-2/target.yml'
        data = load(path)
        data['build'] = ['build-sample']
        dump(path, data)
        with self.assertRaisesRegex(AssertionError, 'Duplicate job id'):
            check_parts(template, workflow, ['sample-target', 'sample-target-2'], answers)

    def test_duplicate_credential_keys_across_parts_fail(self):
        template, workflow, answers, _ = self.sample(2)
        path = template / 'targets/sample-target-2/target.yml'
        data = load(path)
        data['credentials']['sample-token'] = data['credentials'].pop('sample-token-2')
        dump(path, data)
        with self.assertRaisesRegex(AssertionError, 'Duplicate credential key'):
            check_parts(template, workflow, ['sample-target', 'sample-target-2'], answers)

    def test_each_repository_part_and_all_parts(self):
        # Real parts are registered by their parent interview, never discovered at runtime.
        config = load(ROOT / 'templates/release/template.yml')
        registered = set(config['data']['release_targets'])
        available = {p.parent.name for p in (ROOT / 'templates/release/targets').glob('*/target.yml')}
        self.assertEqual(registered, available)
        options = next(q['options'] for q in config['interview'] if q.get('id') == 'targets')
        self.assertEqual(set(options), registered)
        for selection in [[part] for part in sorted(registered)] + [sorted(registered)]:
            with tempfile.TemporaryDirectory() as tmp:
                answers = {}
                for part in selection:
                    fixture = ROOT / 'tests/release/part-answers' / (part + '.json')
                    self.assertTrue(fixture.exists(), f'Add explicit interface-test answers: {fixture}')
                    answers.update(json.loads(fixture.read_text()))
                template, workflow, answers = render(Path(tmp), model(), selected=selection, answers=answers)
                check_parts(template, workflow, selection, answers)

    def literal_data_workflow(self):
        value = model()
        value['release_units']['sample-tool']['changelog'] = '${{ secrets.SAMPLE_TOKEN }}-{{ untouched }}.md'
        purpose = "Publish ${{ secrets.SAMPLE_TOKEN }} and {{ untouched }}"
        original_prepare = prepare

        def inject(template, *args, **kwargs):
            config = original_prepare(template, *args, **kwargs)
            path = template / 'targets/sample-target/target.yml'
            data = load(path)
            for credential in data['credentials'].values():
                credential['purpose'] = purpose
            dump(path, data)
            return config

        with patch.object(harness, 'prepare', inject):
            _, workflow, _ = render(self.root, value, samples=1)
        return load(workflow), value, purpose

    def test_resolve_model_data_stays_literal(self):
        parsed, value, _ = self.literal_data_workflow()
        env = next(s['env'] for s in parsed['jobs']['resolve']['steps'] if s.get('id') == 'resolve')
        self.assertNotIn('${{', env['RELEASE_MODEL'], 'Resolve model data must stay literal')
        self.assertEqual(json.loads(env['RELEASE_MODEL']), value)
        self.assertEqual(env['SOURCE_SHA'], '${{ inputs.source-sha }}')

    def test_preflight_credential_data_stays_literal(self):
        parsed, _, purpose = self.literal_data_workflow()
        for name in ('preflight', 'preflight-sample'):
            step = parsed['jobs'][name]['steps'][0]
            script = step['with']['script']
            self.assertNotIn('${{', script, 'Preflight declaration data must stay literal')
            literal = re.search(r'checkCredentials\((\[.*\]), process.env\);', script).group(1)
            declarations = json.loads(literal)
            self.assertTrue(declarations)
            self.assertTrue(all(c['purpose'] == purpose for c in declarations))
            self.assertEqual(step['env']['SAMPLE_ENV_TOKEN' if name.endswith('-sample') else 'SAMPLE_TOKEN'],
                             '${{ secrets.SAMPLE_ENV_TOKEN }}' if name.endswith('-sample') else '${{ secrets.SAMPLE_TOKEN }}')

    def test_record_job_model_data_stays_literal(self):
        parsed, value, _ = self.literal_data_workflow()
        env = parsed['jobs']['release-record']['env']
        self.assertNotIn('${{', env['RELEASE_MODEL'], 'Release-record job model data must stay literal')
        self.assertEqual(json.loads(env['RELEASE_MODEL']), value)
        self.assertEqual(env['RELEASE_UNIT'], '${{ matrix.unit }}')
        self.assertEqual(env['RELEASE_JSON'], '${{ needs.resolve.outputs.releases }}')

    def test_scripts_have_no_github_expressions_and_data_is_single_render(self):
        parsed, _, _ = self.literal_data_workflow()
        for job in parsed['jobs'].values():
            self.assertNotIn('${{', job.get('if', ''))
            if 'RELEASE_MODEL' in job.get('env', {}):
                self.assertNotIn('${{', job['env']['RELEASE_MODEL'])
            for step in job['steps']:
                for field in ('run', 'if'):
                    self.assertNotIn('${{', step.get(field, ''))
                self.assertNotIn('${{', step.get('with', {}).get('script', ''))
                if 'RELEASE_MODEL' in step.get('env', {}):
                    self.assertNotIn('${{', step['env']['RELEASE_MODEL'])

    def rendered_condition(self, expression):
        original_prepare = prepare

        def inject(template, *args, **kwargs):
            config = original_prepare(template, *args, **kwargs)
            path = template / 'partials/parts.yml'
            path.write_text(path.read_text().replace("'publish', body, extra_env)",
                "'publish', body, extra_env, " + json.dumps(expression) + ')'))
            return config

        with patch.object(harness, 'prepare', inject):
            template, workflow, answers = render(self.root, model(), samples=1)
        answers.update(sample_target_units='sample-tool', trusted_publishing=False)
        return template, workflow, answers

    def test_unbalanced_condition_rejected_at_rendered_boundary(self):
        template, workflow, answers = self.rendered_condition('false) || (true')
        self.assertIn(' && (false) || (true)', load(workflow)['jobs']['publish-sample']['if'])
        with self.assertRaisesRegex(ValueError, 'Unbalanced part condition parentheses'):
            check_parts(template, workflow, ['sample-target'], answers)

    def test_condition_validation_command_rejects_unbalanced_part(self):
        template, workflow, answers = self.rendered_condition('false) || (true')
        path = self.root / 'validation-answers.json'
        path.write_text(json.dumps({'answers': answers}))
        result = subprocess.run([os.sys.executable, str(ROOT / 'tests/release/harness.py'),
            '--template', str(template), '--workflow', str(workflow), '--answers', str(path)],
            text=True, capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Unbalanced part condition parentheses', result.stderr)

    def test_valid_nested_and_quoted_conditions_render(self):
        expressions = ["(true && (false || true))", "contains('it''s (ready)', '(')",
                       "contains('a)(', ')')", r"contains('a\)(', '(')", "'(' == '('", "true"]
        for expression in expressions:
            with self.subTest(expression=expression), tempfile.TemporaryDirectory() as tmp:
                self.root = Path(tmp)
                template, workflow, answers = self.rendered_condition(expression)
                parsed = check_parts(template, workflow, ['sample-target'], answers)
                self.assertTrue(parsed['jobs']['publish-sample']['if'].endswith(' && (' + expression + ')'))
                result = subprocess.run(['actionlint', str(workflow)], text=True, capture_output=True)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                path = self.root / 'validation-answers.json'
                path.write_text(json.dumps({'answers': answers}))
                result = subprocess.run([os.sys.executable, str(ROOT / 'tests/release/harness.py'),
                    '--template', str(template), '--workflow', str(workflow), '--answers', str(path)],
                    text=True, capture_output=True)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_primary_after_publication_renders_first_failure_step(self):
        value = model()
        value['release_units']['sample-tool']['tags']['primary']['require_phase'] = 'after-publication'
        _, workflow, _ = render(self.root, value)
        first = load(workflow)['jobs']['resolve']['steps'][0]
        self.assertIn('sample-tool/primary', first['env']['PHASE_ERRORS'])
        self.assertIn('exit 1', first['run'])

    def test_invalid_model_writes_nothing(self):
        value = model()
        value['$schema'] = 'https://schemas.example.test/wrong/1'
        with self.assertRaisesRegex(AssertionError, 'Toha exit 1'):
            render(self.root, value)
        self.assertFalse((self.root / 'project/.github').exists())


class InternalSchemas(unittest.TestCase):
    def test_examples(self):
        schema = json.loads((ROOT / 'templates/release/schemas/publish-job.schema.json').read_text())
        examples = ROOT / 'docs/examples/release-process'
        jsonschema.validate(json.loads((examples / 'publish-job.valid.json').read_text()), schema)
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.validate(json.loads((examples / 'publish-job.invalid.json').read_text()), schema)
        jsonschema.validate(json.loads((examples / 'credential.valid.json').read_text()),
                            json.loads((ROOT / 'templates/release/schemas/credential.schema.json').read_text()))

    def assert_expression_guard(self, field):
        valid = json.loads((ROOT / 'docs/examples/release-process/publish-job.valid.json').read_text())
        schema = json.loads((ROOT / 'templates/release/schemas/publish-job.schema.json').read_text())
        valid['job']['steps'][-1][field] = '${{ secrets.SAMPLE_TOKEN }}'
        self.assertTrue(list(jsonschema.Draft202012Validator(schema).iter_errors(valid)), field + ' must reject expressions')

    def test_step_if_expression_guard(self):
        self.assert_expression_guard('if')

    def test_run_expression_guard(self):
        self.assert_expression_guard('run')

    def test_schema_guards(self):
        valid = json.loads((ROOT / 'docs/examples/release-process/publish-job.valid.json').read_text())
        schema = json.loads((ROOT / 'templates/release/schemas/publish-job.schema.json').read_text())
        variants = []
        for expression in ['always()', 'AlWaYs ()', 'SUCCESS()', 'failure()', 'Cancelled()', '${{ true }}']:
            v = copy.deepcopy(valid); v['if'] = expression; variants.append(v)
        for key in ['needs', 'if', 'uses', 'secrets', 'continue-on-error', 'concurrency']:
            v = copy.deepcopy(valid); v['job'][key] = 'value'; variants.append(v)
        for env in ['RELEASE_JSON', 'GITHUB_TOKEN']:
            v = copy.deepcopy(valid); v['job']['env'][env] = 'value'; variants.append(v)
        for scope, value in [('contents', 'write'), ('actions', 'write'), ('checks', 'write')]:
            v = copy.deepcopy(valid); v['job']['permissions'] = {scope: value}; variants.append(v)
        for reference in ['actions/checkout@v7', 'actions/checkout@' + 'a' * 40]:
            v = copy.deepcopy(valid); v['job']['steps'][0]['uses'] = reference; variants.append(v)
        for field in ['run', 'if']:
            v = copy.deepcopy(valid); v['job']['steps'][-1][field] = '${{ secrets.SAMPLE_TOKEN }}'; variants.append(v)
        for v in variants:
            with self.subTest(value=v):
                self.assertTrue(list(jsonschema.Draft202012Validator(schema).iter_errors(v)))


class VendoredResolution(unittest.TestCase):
    def test_vendored_schema_makes_zero_network_attempts(self):
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        import threading
        calls = []
        class Proxy(BaseHTTPRequestHandler):
            def do_CONNECT(self):
                calls.append(self.path)
                self.send_error(502)
            def log_message(self, *args):
                pass
        server = ThreadingHTTPServer(('127.0.0.1', 0), Proxy)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        result = None
        try:
            with tempfile.TemporaryDirectory() as tmp:
                directory = Path(tmp)
                template = directory / 'release'
                prepare(template)
                target = directory / 'project'
                (target / '.intentional').mkdir(parents=True)
                (target / '.intentional/release-model.json').write_text(json.dumps(model()))
                answers = directory / 'answers.json'
                answers.write_text(json.dumps({'template': str(template), 'answers': {'targets': [], 'ci_workflow': 'ci.yml'}}))
                env = dict(os.environ, HTTPS_PROXY=f'http://127.0.0.1:{server.server_port}', NO_PROXY='', no_proxy='')
                result = subprocess.run([env['TOHA_BIN'], 'apply', str(template), str(target), '--answers', str(answers)], env=env, capture_output=True, text=True)
        finally:
            server.shutdown(); server.server_close(); thread.join()
        self.assertEqual(calls, [], 'Vendored resolution attempted network access')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
