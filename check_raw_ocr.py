"""Quick check of raw OCR text to see actual line format"""
from apps.designiq.pid_ocr_extractor_v2 import PIDLineExtractorV2
import fitz
from PIL import Image
import io

extractor = PIDLineExtractorV2()

pdf_path = '/app/pfd_documents/2025/12/30/P16093-16-01-08-1689_PID1.pdf'
doc = fitz.open(pdf_path)
page = doc[0]

# Convert to image
pix = page.get_pixmap(matrix=fitz.Matrix(2.5, 2.5))
img = Image.open(io.BytesIO(pix.tobytes("png")))
img = img.convert('L')

# Get OCR text
ocr_results = extractor.extract_all_text_from_image(img)

print('\n' + '='*70)
print('RAW OCR TEXT ANALYSIS')
print('='*70)

for engine, text in ocr_results.items():
    print(f'\n{engine.upper()} OUTPUT (first 2000 chars):')
    print('-'*70)
    print(text[:2000])
    print('-'*70)

doc.close()
