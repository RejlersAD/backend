"""Verify frozen local artifact hashes before a disposable-target handoff.

This does not grant authority, check live release freshness or execute SP3D.
Use released downloads from the guarded API, not draft workbook exports.
"""
import argparse
import hashlib
import json
from pathlib import Path


def verify_package(release, spec, cat):
    if release.get('state') != 'frozen' or not release.get('id') or release.get('invalidated'):
        raise ValueError('A frozen release response with its release ID is required.')
    manifest = release.get('manifest') or {}
    if not manifest.get('input_sha256') or not manifest.get('reference_set_hash'):
        raise ValueError('Source/reference release identity is missing.')
    result = {'release_id': release['id'], 'state': 'local_hashes_verified',
              'live_freshness': 'must_be_checked_by_released_download_API',
              'target_import': 'not_executed'}
    for kind, path in [('spec', spec), ('cat', cat)]:
        with Path(path).open('rb') as file:
            actual = hashlib.file_digest(file, 'sha256').hexdigest()
        if actual != manifest.get(f'{kind}_sha256'):
            raise ValueError(f'{kind.upper()} workbook does not match its frozen manifest.')
        result[f'{kind}_sha256'] = actual
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--release', type=Path, required=True)
    parser.add_argument('--spec', type=Path, required=True)
    parser.add_argument('--cat', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(verify_package(json.loads(args.release.read_text(encoding='utf-8')), args.spec, args.cat), indent=2))


if __name__ == '__main__':
    main()
