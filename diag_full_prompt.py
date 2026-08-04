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

# Test the FULL system prompt (both string literals concatenated as Python does)
# We'll build it by actually calling __init__ style logic
# Instead, let's trigger the method and intercept right before the API call

import unittest.mock as mock

captured_messages = []
original_create = svc.client.chat.completions.create

def capture_and_call(**kwargs):
    msgs = kwargs.get('messages', [])
    captured_messages.extend(msgs)
    return original_create(**kwargs)

svc.client.chat.completions.create = capture_and_call

print("Running _vision_analysis_pass to capture messages...")
try:
    result = svc._vision_analysis_pass(images, "")
except:
    pass

if captured_messages:
    sys_msg = captured_messages[0]
    user_msg = captured_messages[1] if len(captured_messages) > 1 else None
    
    sys_content = sys_msg.get('content', '')
    print(f"System prompt total length: {len(sys_content)}")
    
    # Find any ? bullet characters
    import re
    q_bullets = [(i, repr(sys_content[max(0,i-20):i+20])) for i, c in enumerate(sys_content) if c == '?' and i > 0 and sys_content[i-1] == ' ']
    print(f"Inline ? bullets (space before ?): {len(q_bullets)}")
    for pos, ctx in q_bullets[:5]:
        print(f"  pos={pos}: {ctx}")
    
    # Check for non-ASCII
    non_ascii = [(i, hex(ord(c))) for i, c in enumerate(sys_content) if ord(c) > 127]
    print(f"Non-ASCII chars in combined system prompt: {len(non_ascii)}")
    
    # Show quality standards section
    qs_idx = sys_content.find('**QUALITY STANDARDS:**')
    if qs_idx >= 0:
        print(f"\nQUALITY STANDARDS section (first 800 chars):")
        print(repr(sys_content[qs_idx:qs_idx+800]))
    
    # Now test JUST the system_prompt with a neutral user message  
    svc.client.chat.completions.create = original_create
    print("\nTesting with full combined system_prompt + neutral user message...")
    resp = svc.client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": sys_content},
            {"role": "user", "content": [
                {"type": "text", "text": "Analyze this drawing. Return JSON."},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64," + images[0], "detail": "high"}}
            ]}
        ],
        max_tokens=200, temperature=0.3, timeout=90
    )
    c = resp.choices[0]
    content = c.message.content or c.message.refusal or ""
    print(f"finish_reason: {c.finish_reason}")
    print(f"content: {repr(content[:150])}")
