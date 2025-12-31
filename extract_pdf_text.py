import PyPDF2
import re
import json
import sys

# Set UTF-8 encoding for output
if sys.stdout.encoding != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')

pdf_path = r"C:\Users\Mohammed.Agra\OneDrive - Rejlers AB\Desktop\AIFlow\Documents\Theme and Design\RejlersBrandGuidelines2024_FINAL.pdf"

try:
    with open(pdf_path, 'rb') as file:
        pdf_reader = PyPDF2.PdfReader(file)
        
        print(f"Total pages: {len(pdf_reader.pages)}\n")
        print("=" * 80)
        
        all_text = ""
        for page_num in range(len(pdf_reader.pages)):
            page = pdf_reader.pages[page_num]
            text = page.extract_text()
            # Clean up text encoding
            text = text.encode('utf-8', errors='replace').decode('utf-8')
            all_text += f"\n\n--- PAGE {page_num + 1} ---\n\n{text}"
        
        # Print all text
        print(all_text)
        
        # Extract potential color codes
        print("\n\n" + "=" * 80)
        print("POTENTIAL COLOR CODES FOUND:")
        print("=" * 80)
        
        # Find hex colors
        hex_colors = re.findall(r'#[0-9A-Fa-f]{6}', all_text)
        if hex_colors:
            print("Hex colors:", set(hex_colors))
        
        # Find RGB values
        rgb_colors = re.findall(r'RGB[:\s]+(\d{1,3})[,\s]+(\d{1,3})[,\s]+(\d{1,3})', all_text, re.IGNORECASE)
        if rgb_colors:
            print("RGB colors:", set(rgb_colors))
        
        # Find CMYK values
        cmyk_colors = re.findall(r'CMYK[:\s]+(\d{1,3})[,\s]+(\d{1,3})[,\s]+(\d{1,3})[,\s]+(\d{1,3})', all_text, re.IGNORECASE)
        if cmyk_colors:
            print("CMYK colors:", set(cmyk_colors))
        
        # Find Pantone colors
        pantone = re.findall(r'Pantone[:\s]+[\w\s-]+', all_text, re.IGNORECASE)
        if pantone:
            print("Pantone colors:", set(pantone))

except Exception as e:
    print(f"Error: {e}")
    import traceback
    traceback.print_exc()
