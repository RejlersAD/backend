"""
Cleans all garbled emoji/question-mark patterns from the system_prompt and user message
in apps/pid_analysis/services.py, replacing them with clean professional equivalents.
"""
import re

FILE = 'apps/pid_analysis/services.py'
content = open(FILE, encoding='utf-8').read()
original_len = len(content)

# 1. Remove long sequences of question marks (separator lines like ????????????????...)
#    Replace with clean dashes
content = re.sub(r'\?{8,}', '---', content)

# 2. Replace heading-style ?? XX: ... ?? patterns (e.g. ?? RULE 1: foo ??)
#    "?? TEXT ??" at start-of-line → "[TEXT]"
content = re.sub(r'^\s*\?\?\s*([A-Z][^\n]*?)\s*\?\?\s*$', r'[\1]', content, flags=re.MULTILINE)

# 3. Replace remaining "?? TEXT" line prefixes (e.g. "?? STEP 1: ...")
content = re.sub(r'^(\s*)\?\?\s+', r'\1', content, flags=re.MULTILINE)

# 4. Replace single "? " bullets (e.g. "   ? ONLY use...") with "   - "
content = re.sub(r'^(\s+)\?\s+', r'\1- ', content, flags=re.MULTILINE)

# 5. Replace "? GOOD:" / "? BAD:" patterns
content = re.sub(r'\?\s*(GOOD|BAD|CORRECT|WRONG|FORBIDDEN|OK|AVOID|FOCUS):', r'\1:', content)

# 6. Replace remaining standalone "???" patterns not already fixed
content = re.sub(r'\?{2,}', '--', content)

# 7. Clean up any leftover replacement chars just in case
content = content.replace('\ufffd', '-')

# Write back
open(FILE, 'w', encoding='utf-8').write(content)
print(f'Done. Original: {original_len} chars. New: {len(content)} chars. Delta: {len(content)-original_len}')

# Verify Python syntax
import ast
ast.parse(content)
print('Python syntax: OK')

# Count remaining ? issues
remaining_q8 = len(re.findall(r'\?{8,}', content))
remaining_qq = len(re.findall(r'\?\?', content))
print(f'Remaining ????+ separators: {remaining_q8}')
print(f'Remaining ?? prefixes: {remaining_qq}')
