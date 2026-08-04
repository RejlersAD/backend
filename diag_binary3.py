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

src = open("/app/apps/pid_analysis/services.py").read()

# Find where the system_prompt variable is assigned and ends (just before 'messages = [')
# Let's find the actual triple-quote end of system_prompt
start_marker = '"""-- STRICT ENGINEERING MODE - ZERO HALLUCINATION POLICY --'

# Find the SECOND occurrence of triple-quote after the start (that's the end)
start_idx = src.find(start_marker)
print(f"System prompt starts at: {start_idx}")

# Find the closing triple-quote (the string value ends at the 2nd triple-quote after start)
remaining = src[start_idx + 3:]  # skip the opening """
close_idx = remaining.find('"""')
print(f"System prompt ends at offset: {close_idx} from start")
sys_prompt = remaining[:close_idx]
print(f"Full system prompt length: {len(sys_prompt)}")
print(f"Last 200 chars: {repr(sys_prompt[-200:])}")

# Now check for non-ASCII chars in system_prompt
bad = [(i, hex(ord(c)), c) for i, c in enumerate(sys_prompt) if ord(c) > 127]
print(f"\nNon-ASCII chars in system_prompt: {len(bad)}")
for pos, code, ch in bad[:10]:
    ctx = sys_prompt[max(0,pos-30):pos+30]
    print(f"  pos={pos} {code} [{repr(ch)}] context: {repr(ctx)}")

# Test last quarter of system_prompt only
q4_start = 3*len(sys_prompt)//4
q4 = sys_prompt[q4_start:]
print(f"\nQ4 (75-100%) length: {len(q4)}")
print(f"Q4 starts: {repr(q4[:100])}")

print("\nTesting Q4 only as system prompt...")
response = svc.client.chat.completions.create(
    model="gpt-4o",
    messages=[
        {"role": "system", "content": q4},
        {"role": "user", "content": [
            {"type": "text", "text": "Analyze this P&ID drawing. Return JSON with issues."},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64," + images[0], "detail": "high"}}
        ]}
    ],
    max_tokens=200, temperature=0.3, timeout=60
)
c = response.choices[0]
content = c.message.content or c.message.refusal or ""
print(f"finish_reason: {c.finish_reason}")
print(f"content: {repr(content[:150])}")
