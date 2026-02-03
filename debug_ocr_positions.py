"""
Debug script to see what EasyOCR is actually detecting
"""
import os
import sys
import django
import cv2
import numpy as np
from pdf2image import convert_from_path

# Set up Django environment
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from apps.designiq.pid_ocr_extractor_v2 import PIDLineExtractorV2
import json

def debug_ocr_detection():
    print("=" * 80)
    print("DEBUG: OCR DETECTION AND LINE NUMBER MATCHING")
    print("=" * 80)
    
    # Initialize extractor
    print("\n1️⃣  Initializing extractor...")
    extractor = PIDLineExtractorV2()
    
    # First extract line items
    pdf_path = "/app/test_pid.pdf"
    print(f"\n2️⃣  Extracting line items from {pdf_path}...")
    line_items = extractor.extract_from_pdf(pdf_path)
    print(f"   ✅ Found {len(line_items)} line items via regex")
    print(f"   Sample line numbers:")
    for i, item in enumerate(line_items[:5], 1):
        print(f"      {i}. {item['line_number']}")
    
    # Now convert PDF to image and run OCR
    print(f"\n3️⃣  Converting PDF to image...")
    images = convert_from_path(pdf_path, dpi=200)
    img = images[0]  # First page
    img_array = np.array(img)
    print(f"   ✅ Image size: {img_array.shape}")
    
    # Run EasyOCR
    print(f"\n4️⃣  Running EasyOCR detection...")
    easyocr_result = extractor.easyocr_reader.readtext(img_array, detail=1)
    print(f"   ✅ EasyOCR detected {len(easyocr_result)} text regions")
    
    # Filter for line-number-like patterns
    print(f"\n5️⃣  Filtering for line-number-like text patterns...")
    line_like_patterns = []
    for detection in easyocr_result:
        bbox, text, conf = detection
        text_upper = text.upper().strip()
        
        # Check if text contains typical line number characters
        if any(char in text_upper for char in ['-', '"']):
            line_like_patterns.append({
                'text': text_upper,
                'confidence': conf,
                'bbox': bbox
            })
    
    print(f"   ✅ Found {len(line_like_patterns)} line-number-like patterns")
    print(f"\n   Top 20 candidates:")
    for i, pattern in enumerate(line_like_patterns[:20], 1):
        print(f"      {i:2d}. '{pattern['text']}' (conf: {pattern['confidence']:.2f})")
    
    # Try matching
    print(f"\n6️⃣  Attempting to match line numbers with OCR detections...")
    matched = 0
    unmatched = []
    
    for line_item in line_items:
        line_number = line_item['line_number'].upper().strip()
        found = False
        
        for detection in easyocr_result:
            bbox, text, conf = detection
            text_upper = text.upper().strip()
            
            # Try different matching strategies
            if (line_number in text_upper or 
                text_upper in line_number or
                text_upper.replace(' ', '') in line_number.replace('-', '').replace('"', '')):
                matched += 1
                found = True
                print(f"   ✅ MATCH: '{line_number}' found in OCR text '{text_upper}'")
                break
        
        if not found:
            unmatched.append(line_number)
    
    print(f"\n" + "=" * 80)
    print(f"MATCHING SUMMARY")
    print(f"=" * 80)
    print(f"✅ Matched: {matched}/{len(line_items)} ({matched/len(line_items)*100:.1f}%)")
    print(f"❌ Unmatched: {len(unmatched)}/{len(line_items)} ({len(unmatched)/len(line_items)*100:.1f}%)")
    
    if unmatched:
        print(f"\nUnmatched line numbers (first 10):")
        for ln in unmatched[:10]:
            print(f"   - {ln}")

if __name__ == "__main__":
    debug_ocr_detection()
