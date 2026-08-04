import sys, os
sys.path.insert(0, "/app")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.local")
import django
django.setup()
from apps.pid_analysis.services import PIDAnalysisService

svc = PIDAnalysisService()
images = svc._pdf_to_base64_images("/tmp/test_pid.pdf")

src = open("/app/apps/pid_analysis/services.py").read()
start_marker = '"""-- STRICT ENGINEERING MODE - ZERO HALLUCINATION POLICY --'
end_marker = '**REQUIRED VERIFICATION CHECKLIST - CHECK EVERY ITEM:**'

start_idx = src.find(start_marker)
end_idx = src.find(end_marker)

sys_prompt_full = src[start_idx:end_idx]
sys_prompt_full = sys_prompt_full.replace('"""', '', 1)

total_len = len(sys_prompt_full)
print(f"Full system prompt length: {total_len}")

# Test the second quarter (50% to 75%)
# First half: 0-50% = OK
# Now test 50%-75% and 75%-100% separately
quarter_1 = sys_prompt_full[:total_len//4]
quarter_2 = sys_prompt_full[total_len//4:total_len//2]
quarter_3 = sys_prompt_full[total_len//2:3*total_len//4]
quarter_4 = sys_prompt_full[3*total_len//4:]

print(f"Q3 (50-75%) starts: {repr(sys_prompt_full[total_len//2:total_len//2+100])}")
print(f"Q3 (50-75%) ends: {repr(sys_prompt_full[3*total_len//4-100:3*total_len//4])}")
print(f"Q4 (75-100%) starts: {repr(sys_prompt_full[3*total_len//4:3*total_len//4+100])}")

# Test first half + Q3 (prompts from 0-75%)
test_prompt = sys_prompt_full[:3*total_len//4]
print(f"\nTesting 0-75% ({len(test_prompt)} chars)...")
response = svc.client.chat.completions.create(
    model="gpt-4o",
    messages=[
        {"role": "system", "content": test_prompt},
        {"role": "user", "content": [
            {"type": "text", "text": "Please analyze this P&ID. Return JSON with issues."},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64," + images[0], "detail": "high"}}
        ]}
    ],
    max_tokens=200, temperature=0.3, timeout=60
)
c = response.choices[0]
content = c.message.content or c.message.refusal or ""
print(f"finish_reason: {c.finish_reason}")
print(f"content: {repr(content[:100])}")
