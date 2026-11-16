# relationships: validates: delivery-apps
"""The App authorization schema accepts its example and refuses each invalid document for its named reason."""
import json
from pathlib import Path
import unittest

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[2]
ASSETS = ROOT / 'docs/specifications/assets/delivery-apps'
SCHEMA = json.loads((ASSETS / 'delivery-apps-1.schema.json').read_text())
INVALID = {
    'custodial-app-serving-two-repositories': (['apps', 'example-service-release', 'repositories'], 'maxItems'),
    'release-authority-without-a-tier': (['apps', 'example-intentional-standard'], 'required'),
}


def leaves(document):
    found, stack = set(), list(Draft202012Validator(SCHEMA).iter_errors(document))
    while stack:
        error = stack.pop()
        found.add((tuple(error.absolute_path), error.validator))
        stack.extend(error.context or [])
    return found


class DeliveryApps(unittest.TestCase):
    def test_schema_is_valid_2020_12(self):
        Draft202012Validator.check_schema(SCHEMA)

    def test_example_validates(self):
        self.assertEqual(leaves(json.loads((ASSETS / 'apps.example.json').read_text())), set())

    def test_each_invalid_document_fails_for_its_named_reason(self):
        self.assertEqual({p.stem for p in (ASSETS / 'invalid').glob('*.json')}, set(INVALID))
        for name, (path, keyword) in INVALID.items():
            with self.subTest(name):
                self.assertIn((tuple(path), keyword), leaves(json.loads((ASSETS / 'invalid' / f'{name}.json').read_text())))


if __name__ == '__main__':
    unittest.main()
