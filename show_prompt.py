import re
content = open('apps/pid_analysis/services.py', encoding='utf-8').read()

# Find user message text start
idx = content.find('COMPREHENSIVE P')
start = max(content.rfind('\n', 0, idx), content.rfind('"', 0, idx)) + 1
print('User text starts at char:', start)
print(repr(content[start:start+800]))
