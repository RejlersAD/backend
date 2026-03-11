"""
Remove all Unicode emojis from Python files
This fixes Windows encoding issues with print statements
"""
import os
import re

# Emoji regex pattern - matches common emoji ranges
EMOJI_PATTERN = re.compile(
    "["
    "\U0001F600-\U0001F64F"  # emoticons
    "\U0001F300-\U0001F5FF"  # symbols & pictographs
    "\U0001F680-\U0001F6FF"  # transport & map symbols
    "\U0001F1E0-\U0001F1FF"  # flags (iOS)
    "\U00002702-\U000027B0"
    "\U000024C2-\U0001F251"
    "\U0001F900-\U0001F9FF"  # Supplemental Symbols and Pictographs
    "\U0001FA70-\U0001FAFF"  # Symbols and Pictographs Extended-A
    "]+",
    flags=re.UNICODE
)

# Specific emoji replacements
REPLACEMENTS = {
    '🚀': '[OK]',
    '✅': '[SUCCESS]',
    '❌': '[ERROR]',
    '⚠️': '[WARNING]',
    '🔧': '[CONFIG]',
    '🗄️': '[DATABASE]',
    '🌍': '[ENV]',
    '🏭': '[PRODUCTION]',
    '📊': '[STATS]',
    '🔍': '[SEARCH]',
    '📋': '[INFO]',
    '💡': '[TIP]',
    '🚂': '[RAILWAY]',
    '📡': '[NETWORK]',
    '⏱️': '[TIME]',
}

def remove_emojis_from_file(filepath):
    """Remove emojis from a single file"""
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()
        
        original_content = content
        
        # Replace specific emojis first
        for emoji, replacement in REPLACEMENTS.items():
            if emoji in content:
                content = content.replace(emoji, replacement)
                print(f"   Replaced {emoji} with {replacement} in {filepath}")
        
        # Remove any remaining emojis
        content = EMOJI_PATTERN.sub('[EMOJI]', content)
        
        if content != original_content:
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(content)
            return True
        return False
    except Exception as e:
        print(f"   Error processing {filepath}: {e}")
        return False

def find_and_fix_emojis(directory):
    """Find all Python files and remove emojis"""
    fixed_count = 0
    
    for root, dirs, files in os.walk(directory):
        # Skip virtualenv and node_modules
        if 'venv' in root or 'node_modules' in root or '__pycache__' in root:
            continue
            
        for file in files:
            if file.endswith('.py'):
                filepath = os.path.join(root, file)
                if remove_emojis_from_file(filepath):
                    fixed_count += 1
                    print(f"[FIXED] {filepath}")
    
    return fixed_count

if __name__ == '__main__':
    print("Removing emojis from Python files...")
    fixed = find_and_fix_emojis('.')
    print(f"\nFixed {fixed} files")
