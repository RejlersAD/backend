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

tags_str = chr(10).join('  - ' + t for t in sorted(svc.instrument_tags)[:10])
lines_str = chr(10).join('  - ' + ln for ln in sorted(svc.line_numbers)[:15])

# Build the NEW user message
new_user_msg = f"""Please perform a complete, systematic P&ID Quality Control review on this drawing.

This review should follow the standard for an IFC-stage QC check at an EPC oil and gas company.

--- OCR-CONFIRMED ELEMENTS ON THIS DRAWING ---
INSTRUMENT TAGS ({len(svc.instrument_tags)} total):
{tags_str}

LINE NUMBERS ({len(svc.line_numbers)} total):
{lines_str}

For EVERY instrument tag above, verify and report any gap:
  A. CONTROLLERS (HIC, FIC, LIC, TIC, PIC): Is there a matched control valve (FCV/LCV/TCV)?
  B. TRANSMITTERS (TT, FT, PT, LT, AT): Is signal type shown?
  C. ON-OFF VALVES (XV, SDV, BDV, MOV): Is fail-safe FC/FO/FL annotated?
  D. INDICATORS (TI, PI, FI, LI, PG): Is measurement type clear?

Return JSON with issues array."""

print(f"New user message length: {len(new_user_msg)}")

# Test A: simple system prompt + new user message
simple_sys = """You are a senior P&ID QC engineer. Analyze drawings for engineering issues using ISA-5.1 standards. Return only JSON."""

print("\n=== TEST A: Simple system + detailed user ===")
r = svc.client.chat.completions.create(
    model="gpt-4o",
    messages=[
        {"role": "system", "content": simple_sys},
        {"role": "user", "content": [
            {"type": "text", "text": new_user_msg},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64," + images[0], "detail": "high"}}
        ]}
    ],
    max_tokens=1000, temperature=0.3, timeout=90
)
c = r.choices[0]
content = c.message.content or c.message.refusal or ""
print(f"finish_reason: {c.finish_reason}")
print(f"content[:200]: {repr(content[:200])}")
