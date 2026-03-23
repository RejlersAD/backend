import re
content = open('apps/pid_analysis/services.py', encoding='utf-8').read()

start = content.find('f"""?? COMPREHENSIVE P&ID')
end = content.find('Return ONLY valid JSON. NO other text."""', start) + len('Return ONLY valid JSON. NO other text."""')
user_text = content[start:end]
print('User msg length:', len(user_text))

q_marks = len(re.findall(r'\?{8,}', user_text))
double_q = len(re.findall(r'\?\?', user_text))
single_q_bullet = len(re.findall(r'^\s*\?[ \t]', user_text, re.MULTILINE))
print('Lines with ????+ separators:', q_marks)
print('Occurrences of ?? prefix:', double_q)
print('Lines with single-? bullet:', single_q_bullet)
print()
print('First 800 chars:')
print(repr(user_text[:800]))
