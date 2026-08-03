import sys, os
sys.path.insert(0, "/app")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.local")
import django
django.setup()
from apps.pid_analysis.services import PIDAnalysisService

svc = PIDAnalysisService()
images = svc._pdf_to_base64_images("/tmp/test_pid.pdf")
svc._extract_text_from_pdf("/tmp/test_pid.pdf")
svc._parse_extracted_data()

# Capture what messages are actually sent
captured = {}
original_create = svc.client.chat.completions.create

def spy(**kwargs):
    msgs = kwargs.get('messages', [])
    if msgs:
        sys_msg = msgs[0]
        user_msg = msgs[1] if len(msgs) > 1 else None
        
        captured['sys_len'] = len(sys_msg.get('content', ''))
        
        if user_msg:
            content = user_msg.get('content', [])
            if isinstance(content, list):
                for item in content:
                    if isinstance(item, dict) and item.get('type') == 'text':
                        user_text = item['text']
                        captured['user_text_len'] = len(user_text)
                        captured['user_text_start'] = user_text[:300]
                        captured['user_text_end'] = user_text[-200:]
                        # Check for non-ASCII in user message
                        non_ascii = [(i, hex(ord(c)), c if ord(c) < 65536 else '?') 
                                     for i, c in enumerate(user_text) if ord(c) > 127]
                        captured['user_non_ascii'] = non_ascii[:10]
                        break
    return original_create(**kwargs)

svc.client.chat.completions.create = spy

try:
    svc._vision_analysis_pass(images, "")
except:
    pass

print(f"System prompt length: {captured.get('sys_len', 'N/A')}")
print(f"User message text length: {captured.get('user_text_len', 'N/A')}")
print(f"\nUser message START:")
print(repr(captured.get('user_text_start', 'N/A')))
print(f"\nUser message END:")
print(repr(captured.get('user_text_end', 'N/A')))
print(f"\nNon-ASCII in user message: {captured.get('user_non_ascii', 'N/A')}")
