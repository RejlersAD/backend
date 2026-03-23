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
print(f"Line numbers (first 10): {sorted(svc.line_numbers)[:10]}")

tags_str = ", ".join(sorted(svc.instrument_tags)[:10])
msg = "Please perform a P&ID quality check on this drawing. OCR confirmed tags: " + tags_str + ". Return JSON with issues array."

print(f"Message: {msg[:100]}")
print("Calling API...")
response = svc.client.chat.completions.create(
    model="gpt-4o",
    messages=[{
        "role": "user",
        "content": [
            {"type": "text", "text": msg},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64," + images[0], "detail": "high"}}
        ]
    }],
    max_tokens=500,
    temperature=0.3,
    timeout=120
)
choice = response.choices[0]
print("finish_reason:", choice.finish_reason)
content = choice.message.content or ""
print("content_len:", len(content))
print("content_start:", repr(content[:300]))
