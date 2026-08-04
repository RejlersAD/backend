"""Extract all API fields and parameters from the SmartProject PDF."""
import zlib
import re
from pathlib import Path

PDF = Path(r"C:\Users\Mohammed.Agra\OneDrive - Rejlers AB\Desktop\AIFlow\Documents\wrench\SmartProject API - Rejlers R0.pdf")

pdf_bytes = PDF.read_bytes()
all_chunks = []
for m in re.finditer(rb"stream\r?\n(.*?)\r?\nendstream", pdf_bytes, re.DOTALL):
    try:
        dec = zlib.decompress(m.group(1))
        txt = dec.decode("utf-8", errors="ignore")
        if any(k in txt for k in ["SERVER_ID", "TOKEN", "OTP", "IS_PASSWORD", "SearchObject", "GenerateOTP", "AccessControl", "Logout", "CopyDocument"]):
            all_chunks.append(txt)
    except Exception:
        pass

combined = " ".join(all_chunks)

# Extract quoted ALL-CAPS field names like ("SERVER_ID")
quoted = re.findall(r'\("([A-Z][A-Z0-9_]{2,30})"\)', combined)
unique_fields = sorted(set(quoted))

print("=" * 60)
print("ALL API FIELD NAMES FOUND IN PDF")
print("=" * 60)
for f in unique_fields:
    print(" ", f)

# Also look for endpoint paths
endpoints = re.findall(r"((?:api|Document)[A-Z/][A-Za-z/]+)", combined)
unique_ep = sorted(set(endpoints))
print()
print("=" * 60)
print("ENDPOINT PATHS")
print("=" * 60)
for e in unique_ep:
    print(" ", e)

# Show context around IS_PASSWORD_ENCRYPTED
print()
print("=" * 60)
print("IS_PASSWORD_ENCRYPTED context")
print("=" * 60)
idx = combined.find("IS_PASSWORD_ENCRYPTED")
if idx >= 0:
    print(combined[max(0, idx-200):idx+400])
