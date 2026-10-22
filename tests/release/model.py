# relationships: validates: release-process
import copy


def model():
    unit = {
        'path': '.', 'changelog': 'CHANGELOG.md', 'disposition': 'managed',
        'release_record': 'required', 'depends_on': [], 'ecosystems': [],
        'projections': [], 'sources': [],
        'tags': {'primary': {'role': 'primary', 'template': '{id}-{version}', 'tag_after': []}},
    }
    return {
        '$schema': 'https://intentional.foo/schemas/release-model/1',
        'paths': {'config': '.intentional/config.yml', 'intents': '.intentional/intents',
                  'model': '.intentional/release-model.json'},
        'release_units': {'sample-tool': unit, 'sample-docs': dict(copy.deepcopy(unit), release_record='none')},
        'workspace_tags': {}, 'release_groups': {'fixed': [], 'linked': []},
    }
