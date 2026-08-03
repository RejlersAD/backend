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

# Capture the full user message
captured = {}
original_create = svc.client.chat.completions.create

def spy(**kwargs):
    msgs = kwargs.get('messages', [])
    if msgs and len(msgs) > 1:
        content = msgs[1].get('content', [])
        if isinstance(content, list):
            for item in content:
                if isinstance(item, dict) and item.get('type') == 'text':
                    captured['user_text'] = item['text']
                    break
    raise Exception("SPY_DONE")

svc.client.chat.completions.create = spy

try:
    svc._vision_analysis_pass(images, "")
except Exception as e:
    if "SPY_DONE" not in str(e):
        print(f"Error: {e}")

user_text = captured.get('user_text', 'NOT CAPTURED')
print(f"=== FULL USER MESSAGE ({len(user_text)} chars) ===")
print(user_text)
print("=== END ===")
