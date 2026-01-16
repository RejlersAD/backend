"""
Test script for CRS Multi-Revision Upload with Extraction
Tests the new upload_and_add_revision endpoint
"""

import requests
import json
from pathlib import Path

# Configuration
BASE_URL = "http://localhost:8000/api/v1"
USERNAME = "admin"
PASSWORD = "admin123"  # Update with actual password

def get_auth_token():
    """Get JWT token"""
    response = requests.post(f"{BASE_URL}/users/auth/login/", json={
        "username": USERNAME,
        "password": PASSWORD
    })
    if response.status_code == 200:
        return response.json()['access']
    else:
        print(f"Login failed: {response.text}")
        return None

def create_revision_chain(token):
    """Create a new revision chain"""
    headers = {"Authorization": f"Bearer {token}"}
    data = {
        "document_title": "Test CRS Multi-Revision Document",
        "document_number": "CRS-TEST-001",
        "project_name": "Test Project",
        "description": "Testing multi-revision upload with extraction"
    }
    
    response = requests.post(
        f"{BASE_URL}/crs/revision-chains/",
        headers=headers,
        json=data
    )
    
    if response.status_code == 201:
        chain = response.json()
        print(f"✅ Created revision chain: ID={chain['id']}, Title={chain['document_title']}")
        return chain['id']
    else:
        print(f"❌ Failed to create chain: {response.text}")
        return None

def upload_revision_with_extraction(token, chain_id, pdf_path, revision_label, parent_revision_id=None):
    """
    Upload PDF and automatically process + add as revision
    This tests the NEW endpoint that combines upload + extraction + revision creation
    """
    headers = {"Authorization": f"Bearer {token}"}
    
    # Prepare multipart form data
    files = {
        'file': (Path(pdf_path).name, open(pdf_path, 'rb'), 'application/pdf')
    }
    
    data = {
        'revision_label': revision_label,
        'notes': f'Testing {revision_label} upload with automatic extraction',
        'project_name': 'Test Project',
        'document_number': 'CRS-TEST-001'
    }
    
    if parent_revision_id:
        data['parent_revision_id'] = parent_revision_id
    
    print(f"\n📤 Uploading {revision_label}...")
    print(f"   PDF: {pdf_path}")
    print(f"   Parent Revision: {parent_revision_id or 'None (first revision)'}")
    
    response = requests.post(
        f"{BASE_URL}/crs/revision-chains/{chain_id}/upload_and_add_revision/",
        headers=headers,
        files=files,
        data=data
    )
    
    files['file'][1].close()
    
    if response.status_code == 201:
        result = response.json()
        print(f"✅ Successfully uploaded and processed {revision_label}")
        print(f"   Revision ID: {result['data']['revision']['id']}")
        print(f"   Document ID: {result['data']['document']['id']}")
        print(f"\n   📊 Extraction Summary:")
        summary = result['data']['extraction_summary']
        print(f"      Total Comments: {summary['total_comments']}")
        print(f"      Red Comments: {summary['red_comments']}")
        print(f"      Yellow Boxes: {summary['yellow_boxes']}")
        print(f"      Pages with Comments: {summary['pages_with_comments']}")
        return result['data']['revision']['id']
    else:
        print(f"❌ Failed to upload {revision_label}")
        print(f"   Status: {response.status_code}")
        print(f"   Error: {response.text}")
        return None

def get_chain_details(token, chain_id):
    """Get detailed chain information"""
    headers = {"Authorization": f"Bearer {token}"}
    response = requests.get(
        f"{BASE_URL}/crs/revision-chains/{chain_id}/",
        headers=headers
    )
    
    if response.status_code == 200:
        chain = response.json()
        print(f"\n📋 Chain Summary:")
        print(f"   Title: {chain['document_title']}")
        print(f"   Total Revisions: {chain['total_revisions']}")
        print(f"   Current Revision: {chain['current_revision_number']}")
        
        if 'revisions' in chain:
            print(f"\n   Revisions:")
            for rev in chain['revisions']:
                print(f"      - {rev['revision_label']}: {rev['total_comments']} comments")
        
        return chain
    else:
        print(f"❌ Failed to get chain details: {response.text}")
        return None

def main():
    """
    Main test flow:
    1. Login and get token
    2. Create revision chain
    3. Upload Rev 0 PDF (first revision)
    4. Upload Rev 1 PDF (linked to Rev 0)
    5. Upload Rev 2 PDF (linked to Rev 1)
    6. Get chain details to verify
    """
    
    print("=" * 70)
    print("CRS MULTI-REVISION UPLOAD WITH EXTRACTION TEST")
    print("=" * 70)
    
    # Get authentication token
    print("\n🔐 Authenticating...")
    token = get_auth_token()
    if not token:
        print("❌ Authentication failed. Please check credentials.")
        return
    print("✅ Authentication successful")
    
    # Create revision chain
    print("\n📝 Creating revision chain...")
    chain_id = create_revision_chain(token)
    if not chain_id:
        print("❌ Failed to create chain")
        return
    
    # NOTE: You need to provide actual PDF files for testing
    # These paths should point to real CRS PDFs with red/yellow comments
    
    print("\n" + "=" * 70)
    print("UPLOAD TEST REVISIONS")
    print("=" * 70)
    
    # Example 1: Upload first revision (Rev 0)
    print("\n[1/3] First Revision Upload")
    pdf_path_rev0 = "path/to/your/rev0.pdf"  # UPDATE THIS
    print(f"⚠️  Please update pdf_path_rev0 to point to your Rev 0 PDF")
    print(f"   Current path: {pdf_path_rev0}")
    
    # Uncomment when you have real PDF files:
    # rev0_id = upload_revision_with_extraction(token, chain_id, pdf_path_rev0, "Rev 0")
    # if not rev0_id:
    #     return
    
    # Example 2: Upload second revision (Rev 1) linked to Rev 0
    # print("\n[2/3] Second Revision Upload (linked to Rev 0)")
    # pdf_path_rev1 = "path/to/your/rev1.pdf"  # UPDATE THIS
    # rev1_id = upload_revision_with_extraction(token, chain_id, pdf_path_rev1, "Rev 1", parent_revision_id=rev0_id)
    # if not rev1_id:
    #     return
    
    # Example 3: Upload third revision (Rev 2) linked to Rev 1
    # print("\n[3/3] Third Revision Upload (linked to Rev 1)")
    # pdf_path_rev2 = "path/to/your/rev2.pdf"  # UPDATE THIS
    # rev2_id = upload_revision_with_extraction(token, chain_id, pdf_path_rev2, "Rev 2", parent_revision_id=rev1_id)
    
    # Get final chain details
    print("\n" + "=" * 70)
    print("FINAL RESULTS")
    print("=" * 70)
    # get_chain_details(token, chain_id)
    
    print("\n" + "=" * 70)
    print("TEST INSTRUCTIONS:")
    print("=" * 70)
    print("""
To run the full test:

1. Update the PDF paths in this script:
   - pdf_path_rev0: Path to your first revision PDF
   - pdf_path_rev1: Path to your second revision PDF  
   - pdf_path_rev2: Path to your third revision PDF

2. Ensure PDFs contain red text or yellow box comments

3. Uncomment the upload function calls

4. Run: python test_crs_multi_revision_upload.py

The endpoint will:
✅ Extract comments using CRS color detection logic
✅ Filter out technical drawing elements
✅ Clean comment text
✅ Create document with comments
✅ Add to revision chain
✅ Auto-link comments between revisions
✅ Calculate AI metrics
    """)
    
    print("\n📌 API Endpoint Details:")
    print(f"   URL: POST {BASE_URL}/crs/revision-chains/{{chain_id}}/upload_and_add_revision/")
    print(f"   Method: POST (multipart/form-data)")
    print(f"   Required: file (PDF), revision_label")
    print(f"   Optional: parent_revision_id, notes, project_name, document_number")

if __name__ == "__main__":
    main()
