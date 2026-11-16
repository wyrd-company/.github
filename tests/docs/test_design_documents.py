# relationships: validates: delivery-skeleton
"""Design documents parse, carry the ontology type of their folder, hold prose list items, and link to documents that exist."""
from pathlib import Path
import unittest

from ruamel.yaml import YAML

ROOT = Path(__file__).resolve().parents[2]
DOCS = ROOT / 'docs'
TYPES = {
    'assumptions': 'assumption',
    'concepts': 'concept',
    'decisions': 'decision-record',
    'specifications': 'specification',
    'technical-designs': 'technical-design',
}
# Documents that other repositories, or pull requests not yet merged here, hold.
EXTERNAL = {
    'release-model': 'wyrd-company/intentional docs/technical-designs/release-model.yml',
    'release-process-conformance': 'wyrd-company/airlock docs/technical-designs/release-process-conformance.yml',
    'template-composition': 'wyrd-company/toha docs/technical-designs/template-composition.yml',
}


def documents():
    for folder, kind in TYPES.items():
        for path in sorted((DOCS / folder).glob('*.yml')):
            yield path, kind


def strings_only(value, where):
    if isinstance(value, dict):
        for key, item in value.items():
            yield from strings_only(item, f'{where}.{key}')
    elif isinstance(value, list):
        for index, item in enumerate(value):
            if isinstance(item, (dict, list)) and not where.endswith('relationships'):
                yield f'{where}[{index}] is a {type(item).__name__}, not prose'
            yield from strings_only(item, f'{where}[{index}]')


class DesignDocuments(unittest.TestCase):
    def test_each_document_has_its_folder_type_and_prose_items(self):
        for path, kind in documents():
            with self.subTest(path.relative_to(ROOT).as_posix()):
                data = YAML(typ='safe').load(path.read_text())
                self.assertEqual(data['$schema'], f'https://refinery.systems/ontology/{kind}')
                self.assertEqual(list(strings_only({k: v for k, v in data.items() if k != 'relationships'}, path.stem)), [])

    def test_relationships_resolve(self):
        slugs = {path.stem for path, _ in documents()}
        for path, _ in documents():
            data = YAML(typ='safe').load(path.read_text())
            for relation, targets in (data.get('relationships') or {}).items():
                for target in targets:
                    with self.subTest(f'{path.stem} {relation} {target}'):
                        self.assertTrue(target in slugs or target in EXTERNAL, f'{target} names no document')


if __name__ == '__main__':
    unittest.main()
