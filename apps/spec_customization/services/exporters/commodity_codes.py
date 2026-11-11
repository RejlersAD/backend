"""Project-generated review identifiers, separate from literal PMS evidence."""
import hashlib
import json
import re


def assign_generated_codes(rows):
    groups = {}
    prepared = []
    for row in rows:
        base = re.sub(r'[^A-Z0-9]', '', row.pop('_generation_base', '').upper())
        extra = row.pop('_generation_attributes', {})
        identity = json.dumps({**{k: v for k, v in row.items() if not k.startswith('_')}, **extra}, sort_keys=True, default=str)
        prepared.append((row, base, identity))
        if not row.get('CommodityCode'):
            groups.setdefault(base, set()).add(identity)
    reserved = {row['CommodityCode'] for row, _, _ in prepared if row.get('CommodityCode')}
    seen = {code: None for code in reserved}
    for row, base, identity in prepared:
        if row.get('CommodityCode'):
            continue
        if 18 <= len(base) <= 20 and len(groups[base]) == 1 and base not in reserved:
            code = base
        else:
            prefix = base[:12]
            code = prefix + hashlib.sha256(identity.encode()).hexdigest().upper()[:20 - len(prefix)]
        if code in seen and seen[code] != identity:
            raise ValueError('Generated commodity identifier collision requires review.')
        seen[code] = identity
        row['CommodityCode'] = code
        row['GeneratedCommodityCode'] = code
    return rows
