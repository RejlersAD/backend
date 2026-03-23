"""Diagnostic script - tests what causes OpenAI to refuse the P&ID analysis"""
import os, sys
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
sys.path.insert(0, '/app')
import django; django.setup()
from openai import OpenAI
import fitz, base64, io
from PIL import Image

client = OpenAI(api_key=os.getenv('OPENAI_API_KEY'), timeout=90)

# Load image
doc = fitz.open('/tmp/test_pid.pdf')
page = doc.load_page(0)
mat = fitz.Matrix(150/72, 150/72)
pix = page.get_pixmap(matrix=mat)
img = Image.frombytes('RGB', [pix.width, pix.height], pix.samples)
buf = io.BytesIO()
img.save(buf, format='PNG')
b64 = base64.b64encode(buf.getvalue()).decode('utf-8')
doc.close()
print(f'Image size: {pix.width}x{pix.height}px, base64 len={len(b64)}')

# Extract the actual system prompt from services.py
src = open('/app/apps/pid_analysis/services.py', encoding='utf-8').read()
start = src.find('system_prompt = """') + len('system_prompt = """')
end = src.find('\n            if reference_context:', start)
system_prompt = src[start:end].strip().rstrip('"').rstrip()
print(f'System prompt length: {len(system_prompt)} chars')

# Check for any non-printable or problematic chars
bad_chars = [(i, hex(ord(c)), c) for i, c in enumerate(system_prompt) if ord(c) > 127 and ord(c) not in (0x2019, 0x2018, 0x201c, 0x201d, 0x2014, 0x2013, 0x2022, 0x2713, 0x2192, 0x1f50d, 0x1f6a8, 0xfffd)]
print(f'Potentially problematic non-ASCII chars (excluding common ones): {len(bad_chars)}')
if bad_chars[:5]:
    for pos, code, ch in bad_chars[:5]:
        print(f'  pos={pos} code={code} context={repr(system_prompt[max(0,pos-10):pos+10])}')

# Check for replacement char
repl_count = system_prompt.count('\ufffd')
print(f'Replacement chars remaining in prompt: {repl_count}')

# TEST 1: Minimal system prompt + image
print('\n--- TEST 1: Minimal system prompt + image ---')
resp1 = client.chat.completions.create(
    model='gpt-4o',
    messages=[
        {'role': 'system', 'content': 'You are a P&ID QA engineer. Analyze P&ID drawings and return JSON with issues found.'},
        {'role': 'user', 'content': [
            {'type': 'text', 'text': 'Analyze this P&ID and return JSON: {"issues": [{"pid_reference": "...", "issue_observed": "..."}]}'},
            {'type': 'image_url', 'image_url': {'url': f'data:image/png;base64,{b64}', 'detail': 'high'}}
        ]}
    ],
    max_tokens=2000, timeout=60
)
print(f'TEST 1 response: {resp1.choices[0].message.content[:300]}')
print(f'TEST 1 tokens: {resp1.usage.total_tokens}')

# TEST 2: Full system prompt + image (first 5000 chars of prompt)
print('\n--- TEST 2: Partial system prompt (5000 chars) + image ---')
resp2 = client.chat.completions.create(
    model='gpt-4o',
    messages=[
        {'role': 'system', 'content': system_prompt[:5000]},
        {'role': 'user', 'content': [
            {'type': 'text', 'text': 'Analyze this P&ID drawing and return JSON with issues found.'},
            {'type': 'image_url', 'image_url': {'url': f'data:image/png;base64,{b64}', 'detail': 'high'}}
        ]}
    ],
    max_tokens=2000, timeout=60
)
print(f'TEST 2 response: {resp2.choices[0].message.content[:300]}')
print(f'TEST 2 tokens: {resp2.usage.total_tokens}')
