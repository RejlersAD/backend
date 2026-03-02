with open('apps/designiq/views.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Add the missing comma
old_text = "'from': line.get('from_line', line.get('from_equipment', ''))\n                    'to':"
new_text = "'from': line.get('from_line', line.get('from_equipment', '')),\n                    'to':"

if old_text in content:
    content = content.replace(old_text, new_text)
    with open('apps/designiq/views.py', 'w', encoding='utf-8') as f:
        f.write(content)
    print("✅ Fixed comma!")
else:
    print("❌ Pattern not found")
    print("Searching for variations...")
    # Try without exact spacing
    lines = content.split('\n')
    for i, line in enumerate(lines):
        if "'from': line.get('from_line'," in line:
            print(f"Line {i+1}: {repr(line[:80])}")
            if i+1 < len(lines):
                print(f"Line {i+2}: {repr(lines[i+1][:80])}")
