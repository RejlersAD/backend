#!/usr/bin/env python3
import re

# Read the file
with open('apps/pid_analysis/services.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Count all triple quotes
triple_quote_count = content.count('"""')
print(f'Total triple quotes: {triple_quote_count}')
print(f'Should be even: {triple_quote_count % 2 == 0}')

# Find each occurrence
for i, match in enumerate(re.finditer('"""', content)):
    line_num = content[:match.start()].count('\n') + 1
    context_start = max(0, match.start() - 30)
    context_end = min(len(content), match.end() + 30)
    context = content[context_start:context_end].replace('\n', ' ')
    print(f'{i+1}. Line {line_num}: {context}')