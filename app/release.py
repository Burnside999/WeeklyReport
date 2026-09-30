"""Release manifest shared by the server and packaging tools."""
import json
import re
from pathlib import Path

MANIFEST_PATH = Path(__file__).with_name('release.json')
VERSION_PATTERN = r'(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?'


def load_manifest(path=MANIFEST_PATH):
    data = json.loads(Path(path).read_text(encoding='utf-8'))
    if data['schema_version'] != 1 or data['client_protocol'] != 1:
        raise ValueError('Unsupported release manifest schema or client protocol')
    versions = [data['application']['version']]
    if data['application']['name'] != 'weeklyreport':
        raise ValueError('Unexpected application name')
    for client in data['clients'].values():
        if client['kind'] == 'native':
            versions.append(client['version'])
        elif client['kind'] != 'web':
            raise ValueError('Unsupported client kind')
    if any(not isinstance(value, str) or not re.fullmatch(VERSION_PATTERN, value) for value in versions):
        raise ValueError('Release versions must use semantic versioning')
    return data


MANIFEST = load_manifest()
APPLICATION = MANIFEST['application']
