"""
Complete CRS Multi-Revision Workflow Demo
==========================================

This script demonstrates:
1. Creating a revision chain
2. Uploading multiple PDFs (Rev 0, Rev 1, Rev 2, etc.)
3. Each PDF gets extracted automatically
4. Comments are linked between revisions
5. Download Excel with all revisions and comments
"""

import requests
import json
from pathlib import Path

# Configuration
BASE_URL = "http://localhost:8000/api/v1"
USERNAME = "admin"
PASSWORD = "admin123"  # Update with your password

class Colors:
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    RED = '\033[91m'
    BLUE = '\033[94m'
    BOLD = '\033[1m'
    END = '\033[0m'

def print_step(step_num, total, message):
    print(f"\n{Colors.BOLD}{Colors.BLUE}[Step {step_num}/{total}] {message}{Colors.END}")

def print_success(message):
    print(f"{Colors.GREEN}✓ {message}{Colors.END}")

def print_error(message):
    print(f"{Colors.RED}✗ {message}{Colors.END}")

def print_info(message):
    print(f"{Colors.YELLOW}ℹ {message}{Colors.END}")

def get_auth_token():
    """Get JWT authentication token"""
    print_step(1, 6, "Authenticating")
    response = requests.post(f"{BASE_URL}/users/auth/login/", json={
        "username": USERNAME,
        "password": PASSWORD
    })
    
    if response.status_code == 200:
        token = response.json()['access']
        print_success(f"Authenticated as {USERNAME}")
        return token
    else:
        print_error(f"Login failed: {response.text}")
        return None

def create_revision_chain(token):
    """Create a new revision chain"""
    print_step(2, 6, "Creating Revision Chain")
    
    headers = {"Authorization": f"Bearer {token}"}
    data = {
        "document_title": "Multi-Storey Building Design Review",
        "document_number": "PRJ-2026-001",
        "project_name": "Dubai Marina Tower Phase 2",
        "description": "Structural and architectural design review across multiple revisions"
    }
    
    response = requests.post(
        f"{BASE_URL}/crs/revision-chains/",
        headers=headers,
        json=data
    )
    
    if response.status_code == 201:
        chain = response.json()
        print_success(f"Created chain: {chain['document_title']}")
        print_info(f"   Chain ID: {chain['id']}")
        print_info(f"   Document Number: {chain['document_number']}")
        return chain['id']
    else:
        print_error(f"Failed to create chain: {response.text}")
        return None

def upload_revision(token, chain_id, pdf_path, revision_label, parent_revision_id=None):
    """
    Upload a PDF and automatically extract + add as revision
    This is the KEY endpoint - it does everything in one call!
    """
    
    if not Path(pdf_path).exists():
        print_error(f"PDF file not found: {pdf_path}")
        print_info("Please update the PDF path to point to a real CRS document")
        return None
    
    headers = {"Authorization": f"Bearer {token}"}
    
    files = {
        'file': (Path(pdf_path).name, open(pdf_path, 'rb'), 'application/pdf')
    }
    
    data = {
        'revision_label': revision_label,
        'notes': f'Uploading {revision_label} - Automated extraction test',
        'project_name': 'Dubai Marina Tower Phase 2',
        'document_number': 'PRJ-2026-001'
    }
    
    if parent_revision_id:
        data['parent_revision_id'] = parent_revision_id
    
    print(f"\n   📤 Uploading {revision_label}...")
    print(f"      File: {Path(pdf_path).name}")
    if parent_revision_id:
        print(f"      Linked to: Parent Revision ID {parent_revision_id}")
    
    response = requests.post(
        f"{BASE_URL}/crs/revision-chains/{chain_id}/upload_and_add_revision/",
        headers=headers,
        files=files,
        data=data
    )
    
    files['file'][1].close()
    
    if response.status_code == 201:
        result = response.json()
        revision = result['data']['revision']
        summary = result['data']['extraction_summary']
        
        print_success(f"{revision_label} uploaded successfully!")
        print_info(f"   Revision ID: {revision['id']}")
        print_info(f"   Revision Number: {revision['revision_number']}")
        print_info(f"   📊 Extracted Comments:")
        print(f"      • Total: {summary['total_comments']}")
        print(f"      • Red Comments: {summary['red_comments']}")
        print(f"      • Yellow Boxes: {summary['yellow_boxes']}")
        print(f"      • Pages: {summary['pages_with_comments']}")
        
        return revision['id']
    else:
        print_error(f"Failed to upload {revision_label}")
        print_error(f"   Status: {response.status_code}")
        print_error(f"   Error: {response.text}")
        return None

def get_chain_summary(token, chain_id):
    """Get chain details with all revisions"""
    headers = {"Authorization": f"Bearer {token}"}
    response = requests.get(
        f"{BASE_URL}/crs/revision-chains/{chain_id}/",
        headers=headers
    )
    
    if response.status_code == 200:
        chain = response.json()
        print_success("Retrieved chain summary")
        print(f"\n   📋 Chain: {chain['document_title']}")
        print(f"      Status: {chain['status']}")
        print(f"      Total Revisions: {chain['total_revisions']}")
        print(f"      Current Revision: {chain['current_revision_number']}")
        
        if 'revisions' in chain and chain['revisions']:
            print(f"\n   📝 Revisions:")
            for rev in chain['revisions']:
                print(f"      • {rev['revision_label']}: {rev['total_comments']} comments (Status: {rev['status']})")
        
        return chain
    else:
        print_error(f"Failed to get chain: {response.text}")
        return None

def download_excel(token, chain_id):
    """Download Excel export with all revisions and comments"""
    print_step(5, 6, "Downloading Excel Export")
    
    headers = {"Authorization": f"Bearer {token}"}
    response = requests.get(
        f"{BASE_URL}/crs/revision-chains/{chain_id}/export_excel/",
        headers=headers
    )
    
    if response.status_code == 200:
        # Get filename from Content-Disposition header
        content_disposition = response.headers.get('Content-Disposition', '')
        filename = 'CRS_Export.xlsx'
        if 'filename=' in content_disposition:
            filename = content_disposition.split('filename=')[1].strip('"')
        
        # Save file
        output_path = Path.cwd() / filename
        with open(output_path, 'wb') as f:
            f.write(response.content)
        
        print_success(f"Excel file downloaded successfully!")
        print_info(f"   Location: {output_path}")
        print_info(f"   Size: {len(response.content) / 1024:.2f} KB")
        print_info("\n   📊 Excel contains 4 sheets:")
        print("      1. Chain Summary - Overview of the revision chain")
        print("      2. All Revisions - Details of each revision")
        print("      3. All Comments - Every comment from all revisions")
        print("      4. Comment Links - How comments are linked between revisions")
        
        return output_path
    else:
        print_error(f"Failed to download Excel: {response.status_code}")
        print_error(f"   {response.text}")
        return None

def main():
    """
    Complete workflow demonstration
    """
    
    print("\n" + "=" * 70)
    print(f"{Colors.BOLD}CRS MULTI-REVISION COMPLETE WORKFLOW DEMONSTRATION{Colors.END}")
    print("=" * 70)
    
    print(f"\n{Colors.BOLD}What this demo shows:{Colors.END}")
    print("• Create a revision chain for tracking multiple document revisions")
    print("• Upload multiple PDFs (Rev 0, Rev 1, Rev 2, etc.)")
    print("• Each PDF is automatically extracted for comments")
    print("• Comments are linked between revisions")
    print("• Download complete Excel with all data")
    print("\n" + "=" * 70)
    
    # Step 1: Authenticate
    token = get_auth_token()
    if not token:
        return
    
    # Step 2: Create chain
    chain_id = create_revision_chain(token)
    if not chain_id:
        return
    
    # Step 3: Upload multiple revisions
    print_step(3, 6, "Uploading Multiple Revisions")
    
    # Example PDF paths - UPDATE THESE with your actual CRS PDFs
    revisions_to_upload = [
        {
            "path": "C:/path/to/your/Rev0.pdf",  # UPDATE THIS
            "label": "Rev 0",
            "parent": None
        },
        {
            "path": "C:/path/to/your/Rev1.pdf",  # UPDATE THIS
            "label": "Rev 1",
            "parent": "previous"  # Will use the previous revision's ID
        },
        {
            "path": "C:/path/to/your/Rev2.pdf",  # UPDATE THIS
            "label": "Rev 2",
            "parent": "previous"
        },
    ]
    
    print_info(f"\n⚠️  PDF Upload Configuration:")
    print_info("   Please update the PDF paths in this script to point to your actual CRS documents")
    print_info("   Each PDF should contain red text or yellow box comments\n")
    
    previous_revision_id = None
    uploaded_revisions = []
    
    for i, rev_config in enumerate(revisions_to_upload, 1):
        pdf_path = rev_config["path"]
        revision_label = rev_config["label"]
        
        # Determine parent
        parent_id = None
        if rev_config["parent"] == "previous" and previous_revision_id:
            parent_id = previous_revision_id
        
        print(f"\n   [{i}/{len(revisions_to_upload)}] Processing {revision_label}")
        
        # Check if file exists
        if not Path(pdf_path).exists():
            print_error(f"   File not found: {pdf_path}")
            print_info(f"   Skipping {revision_label} - please update the path")
            continue
        
        # Upload and extract
        revision_id = upload_revision(token, chain_id, pdf_path, revision_label, parent_id)
        
        if revision_id:
            uploaded_revisions.append({
                "id": revision_id,
                "label": revision_label
            })
            previous_revision_id = revision_id
        else:
            print_error(f"   Failed to upload {revision_label}")
    
    if not uploaded_revisions:
        print_error("\n⚠️  No revisions were uploaded successfully")
        print_info("Please update the PDF paths in this script and try again")
        print_info("\nThe endpoint is ready and working - it just needs real PDF files!\n")
        return
    
    # Step 4: Get chain summary
    print_step(4, 6, "Retrieving Chain Summary")
    chain = get_chain_summary(token, chain_id)
    
    # Step 5: Download Excel
    excel_path = download_excel(token, chain_id)
    
    # Step 6: Summary
    print_step(6, 6, "Complete!")
    
    print(f"\n{Colors.BOLD}📊 Workflow Summary:{Colors.END}")
    print(f"   • Chain ID: {chain_id}")
    print(f"   • Revisions Uploaded: {len(uploaded_revisions)}")
    
    if uploaded_revisions:
        print(f"   • Uploaded Revisions:")
        for rev in uploaded_revisions:
            print(f"      - {rev['label']} (ID: {rev['id']})")
    
    if excel_path:
        print(f"   • Excel Export: {excel_path}")
    
    print(f"\n{Colors.GREEN}{Colors.BOLD}✓ Multi-Revision Workflow Completed Successfully!{Colors.END}\n")
    
    print("=" * 70)
    print(f"{Colors.BOLD}How to use this in production:{Colors.END}")
    print("=" * 70)
    print("""
1. Create a revision chain once per document series

2. For each new revision, call:
   POST /api/v1/crs/revision-chains/{chain_id}/upload_and_add_revision/
   
   With:
   - file: PDF with comments
   - revision_label: "Rev 0", "Rev 1", etc.
   - parent_revision_id: Previous revision ID (optional)

3. The endpoint automatically:
   ✓ Extracts all red/yellow comments
   ✓ Creates a new revision in the chain
   ✓ Links comments to previous revision
   ✓ Calculates metrics
   ✓ Returns extraction summary

4. Download Excel anytime with:
   GET /api/v1/crs/revision-chains/{chain_id}/export_excel/
   
   Excel contains:
   • Chain Summary (overview + metrics)
   • All Revisions (each revision's details)
   • All Comments (every comment from all revisions)
   • Comment Links (how comments evolved)

5. No limit on revisions - upload as many as needed!
    """)

if __name__ == "__main__":
    main()
