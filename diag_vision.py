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

print(f"Instrument tags: {sorted(svc.instrument_tags)}")

# Call _vision_analysis_pass directly, monkeypatching to print finish_reason
original_create = svc.client.chat.completions.create

def patched_create(**kwargs):
    resp = original_create(**kwargs)
    if resp and resp.choices:
        choice = resp.choices[0]
        print(f"[DIAG] finish_reason: {choice.finish_reason}")
        print(f"[DIAG] content is None: {choice.message.content is None}")
        print(f"[DIAG] content len: {len(choice.message.content or '')}")
        if choice.message.content:
            print(f"[DIAG] content[:200]: {repr(choice.message.content[:200])}")
        else:
            print(f"[DIAG] refusal: {choice.message.refusal}")
    return resp

svc.client.chat.completions.create = patched_create

print("Calling _vision_analysis_pass...")
result = svc._vision_analysis_pass(images, "")
print(f"Result issues: {result.get('total_issues', 0)}")
