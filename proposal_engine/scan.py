"""Scan a tender/bid folder or ZIP into a classified inventory."""
from __future__ import annotations

import hashlib
import re
import shutil
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

MAX_ZIP_ENTRIES = 10000
MAX_ZIP_TOTAL = 4 * 1024 ** 3


def open_source(path, workdir=None):
    """Return a directory for `path`; ZIPs are extracted safely into a fresh temp directory."""
    path = Path(path)
    if path.is_dir():
        return path, None
    if not zipfile.is_zipfile(path):
        raise ValueError(f'{path} is neither a directory nor a ZIP archive')
    target = Path(tempfile.mkdtemp(prefix='proposal_src_', dir=workdir))
    with zipfile.ZipFile(path) as archive:
        entries = archive.infolist()
        if len(entries) > MAX_ZIP_ENTRIES or sum(e.file_size for e in entries) > MAX_ZIP_TOTAL:
            raise ValueError(f'{path.name}: archive exceeds entry/size limits')
        for entry in entries:
            parts = PurePosixPath(entry.filename.replace('\\', '/')).parts
            if entry.filename.startswith('/') or '..' in parts or re.match(r'^[A-Za-z]:', entry.filename):
                raise ValueError(f'{path.name}: unsafe archive path {entry.filename!r}')
        archive.extractall(target)
    return target, target


def _sha256(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _norm(text):
    return re.sub(r'[^a-z0-9]', '', text.casefold())


def _core_name(stem):
    """Name without leading sequence numbers, used for re-saved duplicates (`1 Name` vs `Name`)."""
    return _norm(re.sub(r'^\s*\d+[\s._-]+', '', stem))


def _short_name_prefix(stem):
    """`EXECUT~1.DOC` → `execut` (Windows 8.3 alias of a longer file name)."""
    match = re.match(r'^([A-Za-z0-9&]{1,6})~\d', stem)
    return _norm(match.group(1)) if match else None


def scan(roots, config, quote_ref=None):
    """Inventory every file under each root, classify it, and detect duplicates and anomalies."""
    files, folders = [], []
    for root in roots:
        # A root is a path (its folder name prefixes relative paths) or (path, prefix);
        # extracted ZIPs use prefix '' because archive entries carry their own top folder.
        root, prefix = (Path(root[0]), root[1]) if isinstance(root, tuple) else (Path(root), Path(root).name)
        for item in sorted(root.rglob('*')):
            rel = '/'.join(part for part in (prefix, item.relative_to(root).as_posix()) if part)
            if item.is_dir():
                folders.append({'rel': rel, 'empty': not any(item.iterdir())})
                continue
            files.append({
                'path': str(item), 'rel': rel, 'name': item.name, 'stem': item.stem,
                'ext': item.suffix.lower().lstrip('.'), 'size': item.stat().st_size,
                'sha256': _sha256(item), 'folder': PurePosixPath(rel).parent.as_posix(),
                'components': [], 'reference_only': None, 'duplicate_of': None,
                'twin_of': None, 'variant': False, 'foreign_refs': [],
            })
    supported = set(config['supported_formats'])
    for row in files:
        row['supported'] = row['ext'] in supported
        for rule in config.get('reference_only') or []:
            if re.search(rule['match'], row['rel'], re.I):
                row['reference_only'] = rule['reason']
                break
        if row['reference_only'] is None:
            row['components'] = [key for key, rule in config['components'].items()
                                 if re.search(rule['match'], row['rel'], re.I)]
        row['variant'] = any(re.search(marker, row['stem'].strip(), re.I) for marker in config.get('variant_markers') or [])
        if quote_ref:
            target = re.sub(r'\D', '', quote_ref)
            row['foreign_refs'] = sorted({
                f'Q-{value}' for value in re.findall(r'\bQ-?(\d{5,6})\b', row['rel'], re.I) if value != target
            })
    _mark_duplicates(files)
    return {'files': files, 'folders': folders}


def _mark_duplicates(files):
    by_hash = {}
    for row in files:
        first = by_hash.setdefault(row['sha256'], row)
        if first is not row:
            row['duplicate_of'] = first['rel']
            row['duplicate_kind'] = 'identical content'
    by_folder = {}
    for row in files:
        by_folder.setdefault(row['folder'], []).append(row)
    for siblings in by_folder.values():
        # Same name re-saved with/without a sequence prefix, in the same format.
        seen = {}
        for row in sorted(siblings, key=lambda r: (not re.match(r'^\d', r['stem']), r['name'])):
            key = (_core_name(row['stem']), row['ext'])
            if key in seen and row['duplicate_of'] is None:
                row['duplicate_of'] = seen[key]['rel']
                row['duplicate_kind'] = 'same document saved twice'
            seen.setdefault(key, row)
        # Format twins: same stem in another format, or a Windows 8.3 alias of a longer name.
        for row in siblings:
            if row['twin_of']:
                continue
            prefix = _short_name_prefix(row['stem'])
            for other in sorted(siblings, key=lambda o: o['variant']):
                if other is row or other['ext'] == row['ext']:
                    continue
                if prefix:
                    if _norm(other['stem']).startswith(prefix) and not _short_name_prefix(other['stem']):
                        row['twin_of'] = other['rel']
                        break
                elif _norm(other['stem']).rstrip('-') == _norm(row['stem']).rstrip('-') and row['ext'] == 'pdf':
                    row['twin_of'] = other['rel']
                    break
