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

# Build the exact system prompt from services.py
# We'll truncate it to find where the problem starts
src = open("/app/apps/pid_analysis/services.py").read()

# Extract the system_prompt string from the source
import re
# Find the start and end of system_prompt
start_marker = '"""-- STRICT ENGINEERING MODE - ZERO HALLUCINATION POLICY --'
end_marker = '**REQUIRED VERIFICATION CHECKLIST - CHECK EVERY ITEM:**'

start_idx = src.find(start_marker)
end_idx = src.find(end_marker)

print(f"System prompt starts at: {start_idx}")
print(f"Checklist section at: {end_idx}")

# Get system prompt up to 50% of the way through
half_idx = start_idx + (end_idx - start_idx) // 2
print(f"Half point: {half_idx}")

# Test with FIRST HALF of system prompt
sys_prompt_first_half = src[start_idx:half_idx]
# Remove the triple-quote start
sys_prompt_first_half = sys_prompt_first_half.replace('"""', '', 1)

print(f"Testing with first {len(sys_prompt_first_half)} chars of system prompt...")
print(f"Last 100 chars of first half: {repr(sys_prompt_first_half[-100:])}")

response = svc.client.chat.completions.create(
    model="gpt-4o",
    messages=[
        {"role": "system", "content": sys_prompt_first_half},
        {"role": "user", "content": [
            {"type": "text", "text": "Please analyze this P&ID drawing. Return JSON with issues array."},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64," + images[0], "detail": "high"}}
        ]}
    ],
    max_tokens=500, temperature=0.3, timeout=120
)
choice = response.choices[0]
print(f"finish_reason: {choice.finish_reason}")
content = choice.message.content or choice.message.refusal or ""
print(f"content[:200]: {repr(content[:200])}")
