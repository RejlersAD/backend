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

# Rebuild system prompt manually
import re
# Build a minimal system prompt excerpt and send with minimal user message
sys_prompt_start = """You are a senior P&ID QA/QC engineer with strict validation discipline.

Your role is to systematically analyze P&ID drawings for engineering issues.
Apply ISA-5.1 standards. Only report issues visible on the provided drawing.
"""

tags_str = ", ".join(sorted(svc.instrument_tags)[:10])

response = svc.client.chat.completions.create(
    model="gpt-4o",
    messages=[
        {"role": "system", "content": sys_prompt_start},
        {"role": "user", "content": [
            {"type": "text", "text": f"Please analyze this P&ID drawing for quality issues.\n\nOCR-confirmed instrument tags: {tags_str}\n\nReturn JSON with issues array listing any engineering problems found."},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64," + images[0], "detail": "high"}}
        ]}
    ],
    max_tokens=2000,
    temperature=0.3,
    timeout=120
)
choice = response.choices[0]
print(f"finish_reason: {choice.finish_reason}")
print(f"content is None: {choice.message.content is None}")
print(f"content len: {len(choice.message.content or '')}")
print(f"refusal: {choice.message.refusal}")
print(f"content[:300]: {repr((choice.message.content or '')[:300])}")
