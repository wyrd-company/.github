# relationships: validates: release-process, release-model
"""Consumer coverage for the authoritative Release Model projection contract."""
import json
from pathlib import Path
import tempfile
import unittest

import jsonschema
from harness import ROOT, load, render
from model import model


class ReleaseModelSchema(unittest.TestCase):
    def projection_model(self, adapter, generic):
        value = model()
        # Match Intentional's known_ecosystem_adapters_require_generic_false:
        # pointer present, native_identity absent, classification the sole error.
        value['release_units']['sample-tool']['projections'] = [{
            'adapter': adapter, 'file': 'package.json', 'workspace_file': 'package.json',
            'mode': 'committed', 'generic': generic, 'pointer': 'package.version',
        }]
        return value

    def test_known_ecosystem_adapters_require_generic_false(self):
        validator = jsonschema.Draft202012Validator(
            load(ROOT / 'release/schemas/release-model-1.schema.yml'))
        for adapter in ('npm', 'cargo', 'pub', 'python', 'msbuild', 'go'):
            with self.subTest(adapter=adapter):
                value = self.projection_model(adapter, True)
                errors = list(validator.iter_errors(value))
                self.assertEqual([(list(e.path), e.validator) for e in errors], [
                    (['release_units', 'sample-tool', 'projections', 0, 'generic'], 'const')])
                projection = value['release_units']['sample-tool']['projections'][0]
                projection['generic'] = False
                self.assertEqual(list(validator.iter_errors(value)), [])
                del projection['pointer']
                self.assertEqual(list(validator.iter_errors(value)), [])

    def test_generic_format_projections_remain_valid(self):
        schema = load(ROOT / 'release/schemas/release-model-1.schema.yml')
        for adapter in ('json', 'toml', 'yaml'):
            with self.subTest(adapter=adapter):
                jsonschema.validate(self.projection_model(adapter, True), schema)

    def test_ecosystem_classification_at_document_admission(self):
        for adapter in ('npm', 'cargo', 'pub', 'python', 'msbuild', 'go'):
            with self.subTest(adapter=adapter), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                with self.assertRaisesRegex(AssertionError, 'Toha exit 1'):
                    render(root, self.projection_model(adapter, True))
                self.assertFalse((root / 'project/.github').exists())
                self.assertFalse((root / 'project/.toha.json').exists())
            with self.subTest(adapter=adapter, generic=False), tempfile.TemporaryDirectory() as tmp:
                value = self.projection_model(adapter, False)
                _, workflow, _ = render(Path(tmp), value)
                step = next(s for s in load(workflow)['jobs']['resolve']['steps'] if s.get('id') == 'resolve')
                self.assertEqual(json.loads(step['env']['RELEASE_MODEL']), value)
