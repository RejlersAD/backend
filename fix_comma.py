with open('apps/designiq/views.py', 'rb') as f:
    content = f.read()

# Add missing comma
content = content.replace(
    b"'from': line.get('from_line', line.get('from_equipment', ''))\n                    'to':",
    b"'from': line.get('from_line', line.get('from_equipment', '')),\n                    'to':"
)

with open('apps/designiq/views.py', 'wb') as f:
    f.write(content)

print("✅ Fixed comma!")
