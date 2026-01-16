# 🧪 CRS Multi-Revision Testing Guide

## ✅ Container Status

**Backend:** ✓ Running on http://localhost:8000  
**Redis:** ✓ Running (healthy)  
**PostgreSQL:** ✓ Running (healthy)  

---

## 🔑 Step 1: Get Authentication Token

### Using PowerShell:
```powershell
$loginBody = @{
    username = "admin"
    password = "your_password_here"
} | ConvertTo-Json

$response = Invoke-RestMethod -Uri "http://localhost:8000/api/v1/users/auth/login/" `
    -Method POST `
    -Body $loginBody `
    -ContentType "application/json"

$token = $response.access
Write-Host "Token: $token"
```

### Using cURL:
```bash
curl -X POST http://localhost:8000/api/v1/users/auth/login/ \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"your_password"}'
```

**Save the token** from the response - you'll need it for all other requests.

---

## 📝 Step 2: Create a Revision Chain

### PowerShell:
```powershell
$headers = @{
    Authorization = "Bearer $token"
    "Content-Type" = "application/json"
}

$chainBody = @{
    document_title = "Test Building Design"
    document_number = "TEST-CRS-001"
    project_name = "Test Project"
    description = "Testing multi-revision upload"
} | ConvertTo-Json

$chain = Invoke-RestMethod -Uri "http://localhost:8000/api/v1/crs/revision-chains/" `
    -Method POST `
    -Headers $headers `
    -Body $chainBody

$chainId = $chain.id
Write-Host "Chain ID: $chainId"
```

### cURL:
```bash
curl -X POST http://localhost:8000/api/v1/crs/revision-chains/ \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "document_title": "Test Building Design",
    "document_number": "TEST-CRS-001",
    "project_name": "Test Project"
  }'
```

**Expected Response:**
```json
{
  "id": 1,
  "document_title": "Test Building Design",
  "document_number": "TEST-CRS-001",
  "total_revisions": 0,
  "status": "active"
}
```

---

## 📤 Step 3: Upload First Revision (Rev 0)

### PowerShell:
```powershell
# Path to your CRS PDF with red/yellow comments
$pdfPath = "C:\path\to\your\rev0.pdf"

$headers = @{
    Authorization = "Bearer $token"
}

# Create multipart form data
$form = @{
    file = Get-Item -Path $pdfPath
    revision_label = "Rev 0"
    notes = "First submission"
}

$rev0 = Invoke-RestMethod -Uri "http://localhost:8000/api/v1/crs/revision-chains/$chainId/upload_and_add_revision/" `
    -Method POST `
    -Headers $headers `
    -Form $form

Write-Host "✓ Rev 0 Uploaded"
Write-Host "  Revision ID: $($rev0.data.revision.id)"
Write-Host "  Total Comments: $($rev0.data.extraction_summary.total_comments)"
Write-Host "  Red Comments: $($rev0.data.extraction_summary.red_comments)"
Write-Host "  Yellow Boxes: $($rev0.data.extraction_summary.yellow_boxes)"

$rev0Id = $rev0.data.revision.id
```

### cURL:
```bash
curl -X POST "http://localhost:8000/api/v1/crs/revision-chains/1/upload_and_add_revision/" \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -F "file=@/path/to/rev0.pdf" \
  -F "revision_label=Rev 0" \
  -F "notes=First submission"
```

**Expected Response:**
```json
{
  "success": true,
  "message": "Revision Rev 0 uploaded and processed successfully",
  "data": {
    "revision": {
      "id": 101,
      "revision_number": 1,
      "revision_label": "Rev 0",
      "total_comments": 45
    },
    "extraction_summary": {
      "total_comments": 45,
      "red_comments": 30,
      "yellow_boxes": 15,
      "pages_with_comments": 12
    }
  }
}
```

---

## 📤 Step 4: Upload Second Revision (Rev 1)

### PowerShell:
```powershell
$pdfPath = "C:\path\to\your\rev1.pdf"

$form = @{
    file = Get-Item -Path $pdfPath
    revision_label = "Rev 1"
    parent_revision_id = $rev0Id.ToString()
    notes = "Second submission - addressing comments"
}

$rev1 = Invoke-RestMethod -Uri "http://localhost:8000/api/v1/crs/revision-chains/$chainId/upload_and_add_revision/" `
    -Method POST `
    -Headers $headers `
    -Form $form

Write-Host "✓ Rev 1 Uploaded"
Write-Host "  Total Comments: $($rev1.data.extraction_summary.total_comments)"
Write-Host "  Comments linked to Rev 0"

$rev1Id = $rev1.data.revision.id
```

### cURL:
```bash
curl -X POST "http://localhost:8000/api/v1/crs/revision-chains/1/upload_and_add_revision/" \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -F "file=@/path/to/rev1.pdf" \
  -F "revision_label=Rev 1" \
  -F "parent_revision_id=101" \
  -F "notes=Second submission"
```

---

## 📤 Step 5: Upload More Revisions

Repeat Step 4 for Rev 2, Rev 3, etc. Just change:
- `revision_label` to "Rev 2", "Rev 3", etc.
- `parent_revision_id` to the previous revision's ID
- PDF file path

**You can upload unlimited revisions to the same chain!**

---

## 📊 Step 6: View Chain Summary

### PowerShell:
```powershell
$chain = Invoke-RestMethod -Uri "http://localhost:8000/api/v1/crs/revision-chains/$chainId/" `
    -Method GET `
    -Headers @{ Authorization = "Bearer $token" }

Write-Host "Chain Summary:"
Write-Host "  Total Revisions: $($chain.total_revisions)"
Write-Host "  Current Revision: $($chain.current_revision_number)"
Write-Host ""
Write-Host "Revisions:"
foreach ($rev in $chain.revisions) {
    Write-Host "  - $($rev.revision_label): $($rev.total_comments) comments"
}
```

### cURL:
```bash
curl -X GET "http://localhost:8000/api/v1/crs/revision-chains/1/" \
  -H "Authorization: Bearer YOUR_TOKEN"
```

---

## 📥 Step 7: Download Excel Export

### PowerShell:
```powershell
$outputFile = "CRS_Export.xlsx"

Invoke-WebRequest -Uri "http://localhost:8000/api/v1/crs/revision-chains/$chainId/export_excel/" `
    -Method GET `
    -Headers @{ Authorization = "Bearer $token" } `
    -OutFile $outputFile

Write-Host "✓ Excel downloaded: $outputFile"
Write-Host ""
Write-Host "Excel contains 4 sheets:"
Write-Host "  1. Chain Summary - Overview and metrics"
Write-Host "  2. All Revisions - Details of each revision"
Write-Host "  3. All Comments - Every comment from all revisions"
Write-Host "  4. Comment Links - Comment evolution tracking"
```

### cURL:
```bash
curl -X GET "http://localhost:8000/api/v1/crs/revision-chains/1/export_excel/" \
  -H "Authorization: Bearer YOUR_TOKEN" \
  --output CRS_Export.xlsx
```

**The Excel file will contain:**
- ✅ Chain Summary (overview + metrics)
- ✅ All Revisions (each revision's details)
- ✅ All Comments (every comment from ALL revisions)
- ✅ Comment Links (how comments evolved between revisions)

---

## 🧪 Complete PowerShell Test Script

Save this as `test_crs.ps1`:

```powershell
# Configuration
$baseUrl = "http://localhost:8000/api/v1"
$username = "admin"
$password = "your_password"  # UPDATE THIS

Write-Host "=== CRS Multi-Revision Test ===" -ForegroundColor Cyan

# 1. Login
Write-Host "`n[1/5] Authenticating..." -ForegroundColor Yellow
$loginBody = @{ username = $username; password = $password } | ConvertTo-Json
$response = Invoke-RestMethod -Uri "$baseUrl/users/auth/login/" -Method POST -Body $loginBody -ContentType "application/json"
$token = $response.access
Write-Host "✓ Authenticated" -ForegroundColor Green

$headers = @{ Authorization = "Bearer $token" }

# 2. Create Chain
Write-Host "`n[2/5] Creating revision chain..." -ForegroundColor Yellow
$chainBody = @{
    document_title = "Test Building"
    document_number = "TEST-001"
    project_name = "Test Project"
} | ConvertTo-Json

$chain = Invoke-RestMethod -Uri "$baseUrl/crs/revision-chains/" -Method POST -Headers $headers -Body $chainBody -ContentType "application/json"
Write-Host "✓ Chain created: ID = $($chain.id)" -ForegroundColor Green

# 3. Upload Rev 0
Write-Host "`n[3/5] Upload Rev 0 PDF..." -ForegroundColor Yellow
Write-Host "⚠️  Update PDF path in script!" -ForegroundColor Red
# $pdfPath = "C:\path\to\rev0.pdf"
# $form = @{ file = Get-Item $pdfPath; revision_label = "Rev 0" }
# $rev0 = Invoke-RestMethod -Uri "$baseUrl/crs/revision-chains/$($chain.id)/upload_and_add_revision/" -Method POST -Headers $headers -Form $form
# Write-Host "✓ Rev 0: $($rev0.data.extraction_summary.total_comments) comments" -ForegroundColor Green

# 4. Upload Rev 1
Write-Host "`n[4/5] Upload Rev 1 PDF..." -ForegroundColor Yellow
Write-Host "⚠️  Update PDF path in script!" -ForegroundColor Red

# 5. Download Excel
Write-Host "`n[5/5] Downloading Excel..." -ForegroundColor Yellow
Invoke-WebRequest -Uri "$baseUrl/crs/revision-chains/$($chain.id)/export_excel/" -Method GET -Headers $headers -OutFile "CRS_Export.xlsx"
Write-Host "✓ Excel downloaded: CRS_Export.xlsx" -ForegroundColor Green

Write-Host "`n=== Test Complete ===" -ForegroundColor Cyan
```

Run with: `.\test_crs.ps1`

---

## 📝 Notes

**PDF Requirements:**
- Must contain red text or yellow box annotations
- Text must be extractable (not scanned images)
- Recommended max size: 50MB

**Extraction Details:**
- Red comments: RGB thresholds R>0.7, G<0.4, B<0.4
- Yellow boxes: RGB thresholds R>0.8, G>0.8, B<0.5
- Technical elements filtered automatically

**Chain Management:**
- One chain per document series
- Unlimited revisions per chain
- Each revision maintains its own comments
- Comments are automatically linked between revisions

---

## ✅ Endpoints Ready to Test

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/users/auth/login/` | POST | Get JWT token |
| `/crs/revision-chains/` | POST | Create chain |
| `/crs/revision-chains/{id}/upload_and_add_revision/` | POST | Upload PDF + Extract |
| `/crs/revision-chains/{id}/` | GET | View chain |
| `/crs/revision-chains/{id}/export_excel/` | GET | Download Excel |

All endpoints are **live and ready** at: http://localhost:8000

---

**Status:** ✅ Container Updated and Running  
**Port:** 8000  
**Ready to Test:** YES - Just add your CRS PDFs!
