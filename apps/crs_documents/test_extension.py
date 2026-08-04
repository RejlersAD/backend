"""
Quick Test Script for CRS Document Intelligence Extension
Verifies PDF extraction and template population
"""

import sys
import os

# Add backend to path
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from io import BytesIO


def test_extraction():
    """Test PDF comment extraction"""
    print("=" * 60)
    print("TEST 1: PDF Comment Extraction")
    print("=" * 60)
    
    try:
        from apps.crs_documents.helpers.comment_extractor import (
            extract_reviewer_comments,
            get_comment_statistics
        )
        
        # Load test PDF
        pdf_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
            'test', 'crs', 'ConsolidatedComments_00523-0000-RPT-PRU-0004RA.pdf'
        )
        
        if not os.path.exists(pdf_path):
            print(f"❌ Test PDF not found: {pdf_path}")
            print("   Please ensure test file exists")
            return False
        
        print(f"📄 Loading PDF: {os.path.basename(pdf_path)}")
        
        with open(pdf_path, 'rb') as f:
            pdf_buffer = BytesIO(f.read())
        
        # Extract comments
        print("🔍 Extracting comments...")
        comments = extract_reviewer_comments(pdf_buffer)
        
        print(f"\n✅ Extracted {len(comments)} comments")
        
        # Show statistics
        stats = get_comment_statistics(comments)
        print("\n📊 Statistics:")
        print(f"  - Total: {stats['total']}")
        print(f"  - By Type: {stats['by_type']}")
        print(f"  - By Discipline: {stats['by_discipline']}")
        print(f"  - Pages with comments: {stats['pages_with_comments']}")
        
        # Show sample comments
        print("\n📝 Sample Comments (first 3):")
        for i, comment in enumerate(comments[:3], 1):
            print(f"\n  {i}. {comment.reviewer_name}")
            print(f"     Type: {comment.comment_type}")
            print(f"     Discipline: {comment.discipline}")
            print(f"     Page: {comment.page_number or 'N/A'}")
            print(f"     Text: {comment.comment_text[:100]}...")
        
        return True
    
    except Exception as e:
        print(f"❌ Error: {str(e)}")
        import traceback
        traceback.print_exc()
        return False


def test_template_population():
    """Test CRS template population"""
    print("\n" + "=" * 60)
    print("TEST 2: CRS Template Population")
    print("=" * 60)
    
    try:
        from apps.crs_documents.helpers.comment_extractor import extract_reviewer_comments
        from apps.crs_documents.helpers.template_populator import (
            populate_crs_template,
            validate_template
        )
        
        # Load test PDF
        pdf_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
            'test', 'crs', 'ConsolidatedComments_00523-0000-RPT-PRU-0004RA.pdf'
        )
        
        # Load template
        template_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
            'test', 'crs', 'CRS template.xlsx'
        )
        
        if not os.path.exists(template_path):
            print(f"❌ Template not found: {template_path}")
            return False
        
        print(f"📋 Loading template: {os.path.basename(template_path)}")
        
        with open(template_path, 'rb') as f:
            template_buffer = BytesIO(f.read())
        
        # Validate template
        print("✓ Validating template...")
        validation = validate_template(BytesIO(template_buffer.getvalue()))
        
        if not validation['valid']:
            print(f"❌ Template validation failed: {validation.get('error')}")
            return False
        
        print(f"✅ Template is valid:")
        print(f"  - Sheet: {validation['sheet_name']}")
        print(f"  - Rows: {validation['row_count']}")
        print(f"  - Columns: {validation['column_count']}")
        
        # Extract comments
        print("\n🔍 Extracting comments from PDF...")
        with open(pdf_path, 'rb') as f:
            pdf_buffer = BytesIO(f.read())
        
        comments = extract_reviewer_comments(pdf_buffer)
        print(f"✅ Extracted {len(comments)} comments")
        
        # Populate template
        print("\n📝 Populating template...")
        metadata = {
            'project_name': 'Test Project',
            'document_number': '00523-0000-RPT-PRU-0004RA',
            'revision': 'R1',
            'contractor': 'Test Contractor'
        }
        
        populated = populate_crs_template(
            BytesIO(template_buffer.getvalue()),
            comments,
            metadata
        )
        
        # Save output
        output_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
            'test', 'crs'
        )
        output_path = os.path.join(output_dir, 'CRS_Populated_TEST.xlsx')
        
        with open(output_path, 'wb') as f:
            f.write(populated.getvalue())
        
        print(f"\n✅ Template populated successfully!")
        print(f"📁 Output saved to: {output_path}")
        print(f"   Open this file in Excel to verify the results")
        
        return True
    
    except Exception as e:
        print(f"❌ Error: {str(e)}")
        import traceback
        traceback.print_exc()
        return False


def test_api_imports():
    """Test that API can import the helpers"""
    print("\n" + "=" * 60)
    print("TEST 3: API Integration Check")
    print("=" * 60)
    
    try:
        print("🔧 Testing helper imports...")
        
        from apps.crs_documents.helpers import (
            extract_reviewer_comments,
            classify_comment,
            populate_crs_template,
            generate_populated_crs
        )
        
        print("✅ All helper functions imported successfully")
        print("✅ API endpoints should be ready to use")
        
        return True
    
    except Exception as e:
        print(f"❌ Error: {str(e)}")
        return False


if __name__ == '__main__':
    print("\n🚀 CRS Document Intelligence Extension - Test Suite")
    print("=" * 60)
    
    results = []
    
    # Run tests
    results.append(("Comment Extraction", test_extraction()))
    results.append(("Template Population", test_template_population()))
    results.append(("API Integration", test_api_imports()))
    
    # Summary
    print("\n" + "=" * 60)
    print("TEST SUMMARY")
    print("=" * 60)
    
    for test_name, passed in results:
        status = "✅ PASS" if passed else "❌ FAIL"
        print(f"{status} - {test_name}")
    
    all_passed = all(result[1] for result in results)
    
    if all_passed:
        print("\n🎉 All tests passed! Extension is ready for use.")
        print("\n📡 Available API Endpoints:")
        print("  1. POST /api/v1/crs-documents/{id}/process-pdf-comments/")
        print("  2. POST /api/v1/crs-documents/{id}/extract-comments-only/")
        print("  3. POST /api/v1/crs-documents/validate-template/")
    else:
        print("\n⚠️  Some tests failed. Please review errors above.")
    
    print("=" * 60)
