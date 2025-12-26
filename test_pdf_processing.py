"""
Test PDF Processing for PFD Upload
Quick test to verify PDF files can be processed
"""
import os
import sys
import django
from io import BytesIO

# Setup Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from apps.pfd_converter.services import PFDToPIDConverter
from django.core.files.uploadedfile import InMemoryUploadedFile

def test_pdf_processing():
    """Test that PDF files can be converted to images"""
    print("\n" + "="*60)
    print("TEST: PDF Processing for PFD Upload")
    print("="*60)
    
    try:
        # Create a simple test PDF in memory
        from reportlab.pdfgen import canvas
        from reportlab.lib.pagesizes import letter
        
        buffer = BytesIO()
        c = canvas.Canvas(buffer, pagesize=letter)
        c.drawString(100, 750, "Test PFD Document")
        c.drawString(100, 700, "Equipment: Vessel V-101")
        c.drawString(100, 650, "Pump P-101")
        c.showPage()
        c.save()
        
        # Reset buffer
        buffer.seek(0)
        
        # Create an InMemoryUploadedFile to simulate Django file upload
        test_pdf = InMemoryUploadedFile(
            file=buffer,
            field_name='file',
            name='test_pfd.pdf',
            content_type='application/pdf',
            size=buffer.getbuffer().nbytes,
            charset=None
        )
        
        print("\n✅ Created test PDF file")
        print(f"   File name: {test_pdf.name}")
        print(f"   Content type: {test_pdf.content_type}")
        print(f"   Size: {test_pdf.size} bytes")
        
        # Initialize converter
        converter = PFDToPIDConverter()
        print("\n✅ Initialized PFDToPIDConverter")
        
        # Test PDF to image conversion
        print("\n🔄 Testing PDF to image conversion...")
        image_data = converter._prepare_image(test_pdf)
        
        if image_data:
            print(f"✅ Successfully converted PDF to base64 image")
            print(f"   Base64 length: {len(image_data)} characters")
            print(f"   Base64 preview: {image_data[:50]}...")
            return True
        else:
            print("❌ Failed: No image data returned")
            return False
            
    except Exception as e:
        print(f"\n❌ Test Failed: {str(e)}")
        import traceback
        traceback.print_exc()
        return False

def test_image_processing():
    """Test that regular image files still work"""
    print("\n" + "="*60)
    print("TEST: Image Processing (PNG/JPEG)")
    print("="*60)
    
    try:
        from PIL import Image
        
        # Create a test image
        img = Image.new('RGB', (800, 600), color='white')
        buffer = BytesIO()
        img.save(buffer, format='PNG')
        buffer.seek(0)
        
        # Create an InMemoryUploadedFile
        test_image = InMemoryUploadedFile(
            file=buffer,
            field_name='file',
            name='test_pfd.png',
            content_type='image/png',
            size=buffer.getbuffer().nbytes,
            charset=None
        )
        
        print("\n✅ Created test PNG image")
        print(f"   File name: {test_image.name}")
        print(f"   Content type: {test_image.content_type}")
        print(f"   Size: {test_image.size} bytes")
        
        # Initialize converter
        converter = PFDToPIDConverter()
        
        # Test image processing
        print("\n🔄 Testing image processing...")
        image_data = converter._prepare_image(test_image)
        
        if image_data:
            print(f"✅ Successfully processed image")
            print(f"   Base64 length: {len(image_data)} characters")
            return True
        else:
            print("❌ Failed: No image data returned")
            return False
            
    except Exception as e:
        print(f"\n❌ Test Failed: {str(e)}")
        import traceback
        traceback.print_exc()
        return False

def main():
    print("\n" + "🔬" + " "*58 + "🔬")
    print("  PFD FILE PROCESSING TEST SUITE")
    print("🔬" + " "*58 + "🔬")
    
    results = {
        "PDF Processing": test_pdf_processing(),
        "Image Processing": test_image_processing(),
    }
    
    print("\n" + "="*60)
    print("SUMMARY")
    print("="*60)
    
    for test_name, result in results.items():
        status = "✅ PASS" if result else "❌ FAIL"
        print(f"{status} - {test_name}")
    
    passed = sum(results.values())
    total = len(results)
    print(f"\nTotal: {passed}/{total} tests passed")
    
    if passed == total:
        print("\n🎉 All tests passed! PDF and image processing working correctly.")
        print("\n📝 You can now upload PFD files (PDF, JPG, PNG) at:")
        print("   http://localhost:3000/pfd/upload")
    else:
        print("\n⚠️  Some tests failed. Please check the errors above.")

if __name__ == "__main__":
    main()
