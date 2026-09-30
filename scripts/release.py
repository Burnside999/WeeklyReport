"""Sync packaging metadata from app/release.json; --check is used in CI."""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.release import MANIFEST  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    version = MANIFEST['clients']['windows']['version']
    mismatches = []
    for name in ('package.json', 'package-lock.json'):
        path = ROOT / 'desktop' / name
        data = json.loads(path.read_text(encoding='utf-8'))
        targets = [data] + ([data['packages']['']] if name == 'package-lock.json' else [])
        if any(target['version'] != version for target in targets):
            mismatches.append(str(path.relative_to(ROOT)))
        for target in targets:
            target['version'] = version
        if not args.check:
            path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    if args.check and mismatches:
        sys.exit('Version mismatch: ' + ', '.join(mismatches) + '. Run python scripts/release.py')
    print(f"weeklyreport {MANIFEST['application']['version']} / Windows {version}: manifest OK")


if __name__ == '__main__':
    main()
