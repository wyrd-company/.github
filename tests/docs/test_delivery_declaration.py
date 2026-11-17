# relationships: validates: delivery-declaration
"""The delivery declaration schema accepts its examples and refuses each invalid document for its named reason."""
import json
from pathlib import Path
import unittest

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[2]
ASSETS = ROOT / 'docs/specifications/assets/delivery-declaration'
SCHEMA = json.loads((ASSETS / 'delivery-declaration-1.schema.json').read_text())

# Each invalid document breaks one rule; the error is reported at this location with this keyword.
INVALID = {
    'assurance-control-with-a-ceremony-setting': (['controls', 'rehearsal', 'setting'], 'enum'),
    'deploy-declares-a-tier': ([], 'not'),
    'deploy-with-an-empty-chain': (['chain'], 'minItems'),
    'release-without-release-authority': ([], 'required'),
    'secret-name-in-staging-form': (['environments', 'publish-homebrew-tap', 'secrets', 0, 'name'], 'not'),
    'variable-name-starts-with-github': (['environments', 'release-authority', 'variables', 6], 'not'),
    'approval-environment-with-a-variable': (['environments', 'release-approval', 'variables'], 'maxItems'),
    'secret-with-env-and-fields': (['environments', 'publish-homebrew-tap', 'secrets', 0], 'oneOf'),
    'environment-with-secrets-and-keys': (['environments', 'publish-homebrew-tap'], 'not'),
    'airlock-config-declares-a-tier': ([], 'not'),
    # Names the retired airlock-audit environment on purpose: the only environment allowed is airlock-safe.
    'airlock-config-with-another-environment': (['environments'], 'const'),
    'key-without-installation-variable': (['environments', 'release-authority', 'keys', 0], 'required'),
    'prepare-output-outside-the-repository': (['prepare_outputs', 0], 'pattern'),
    'prepare-output-matching-every-path': (['prepare_outputs', 0], 'not'),
}


def errors(document):
    return sorted(Draft202012Validator(SCHEMA).iter_errors(document), key=lambda e: list(e.absolute_path))


def leaves(error_list):
    found = set()
    for error in error_list:
        stack = [error]
        while stack:
            current = stack.pop()
            if current.context:
                stack.extend(current.context)
            else:
                found.add((tuple(current.absolute_path), current.validator))
    return found


class DeliveryDeclaration(unittest.TestCase):
    def test_schema_is_valid_2020_12(self):
        Draft202012Validator.check_schema(SCHEMA)

    def test_examples_validate(self):
        for name in ('release.example.json', 'deploy.example.json', 'airlock-config.example.json'):
            with self.subTest(name):
                self.assertEqual(errors(json.loads((ASSETS / name).read_text())), [])

    def test_each_invalid_document_fails_for_its_named_reason(self):
        documents = {p.stem for p in (ASSETS / 'invalid').glob('*.json')}
        self.assertEqual(documents, set(INVALID), 'Every invalid document has a stated reason')
        for name, (path, keyword) in INVALID.items():
            with self.subTest(name):
                found = errors(json.loads((ASSETS / 'invalid' / f'{name}.json').read_text()))
                self.assertTrue(found, 'The document is refused')
                self.assertIn((tuple(path), keyword), leaves(found) | {(tuple(e.absolute_path), e.validator) for e in found})


if __name__ == '__main__':
    unittest.main()
