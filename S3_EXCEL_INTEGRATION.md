# CRS S3 Excel Integration Guide

## Backend Changes Completed ✅

### 1. Database Model Updates
- **File:** `apps/crs/revision_models.py`
- **Changes:**
  - Added `excel_s3_url` field to CRSRevision model
  - Added `excel_generated_at` timestamp field
  
### 2. S3 Excel Generator Utility
- **File:** `apps/crs/s3_excel_generator.py` (NEW)
- **Features:**
  - Generates Excel HTML for individual revisions
  - Uploads to S3 with encryption (AES256)
  - Generates presigned URLs for secure downloads
  - Handles S3 disabled scenarios gracefully
  
### 3. Auto-Upload on Revision Creation
- **File:** `apps/crs/revision_views.py`
- **Changes:**
  - Imports CRSS3ExcelGenerator
  - Automatically generates and uploads excel after successful revision upload
  - Non-blocking - continues if S3 upload fails
  
### 4. API Response Updates
- **File:** `apps/crs/revision_serializers.py`
- **Changes:**
  - Added `excel_s3_url` field to CRSRevisionSerializer
  - Added `excel_generated_at` field
  - Added `excel_download_url` computed field (presigned URL)
  
### 5. Database Migration
- **File:** `apps/crs/migrations/0002_add_s3_excel_fields.py`
- **Run:** `python manage.py migrate crs`

---

## Frontend Integration Required 🔧

### Update 1: CRSMultiRevisionSmart.jsx

Replace the existing download button with S3-aware download:

```jsx
// In the revisions summary table (around line 450-500)
// Replace the existing download button

{/* Excel Download Button */}
<TableCell align="center">
  <IconButton
    onClick={() => handleDownloadFromS3(index)}
    color="primary"
    size="small"
    title="Download Excel"
  >
    <DownloadIcon />
  </IconButton>
</TableCell>

// Add this new handler function
const handleDownloadFromS3 = async (revisionIndex) => {
  try {
    const revision = revisions[revisionIndex];
    
    // Check if S3 URL exists
    if (revision.excel_download_url) {
      // Download from S3 using presigned URL
      const link = document.createElement('a');
      link.href = revision.excel_download_url;
      link.download = `CRS_${chainData.chain_id}_${revision.revision_label}_${new Date().toISOString().split('T')[0]}.xls`;
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
      
      console.log(`Downloaded from S3: ${revision.revision_label}`);
    } else {
      // Fallback: Generate on-the-fly (existing logic)
      console.warn('No S3 URL, generating excel on-the-fly');
      handleDownloadSingleRevision(revisionIndex);
    }
  } catch (error) {
    console.error('Error downloading from S3:', error);
    // Fallback to existing logic
    handleDownloadSingleRevision(revisionIndex);
  }
};
```

### Update 2: CRSChainDetail.jsx

Similarly update the download buttons in the chain detail page:

```jsx
// In the revision history table
<TableCell align="center">
  {revision.excel_download_url ? (
    <Button
      variant="outlined"
      size="small"
      startIcon={<DownloadIcon />}
      onClick={() => window.open(revision.excel_download_url, '_blank')}
    >
      Download
    </Button>
  ) : (
    <Button
      variant="outlined"
      size="small"
      startIcon={<DownloadIcon />}
      onClick={() => handleGenerateExcel(revision)}
      disabled
    >
      Not Available
    </Button>
  )}
</TableCell>
```

### Update 3: CRSDocuments.jsx (Regular CRS Upload)

Update the document list to show S3 download links:

```jsx
// In the documents table actions column
{document.excel_download_url && (
  <Tooltip title="Download Excel">
    <IconButton
      size="small"
      color="primary"
      onClick={() => window.open(document.excel_download_url, '_blank')}
    >
      <DownloadIcon />
    </IconButton>
  </Tooltip>
)}
```

---

## Environment Variables Required

Add to backend `.env` or Railway environment:

```env
# S3 Configuration
USE_S3=True
S3_READY=True
AWS_ACCESS_KEY_ID=your_aws_access_key
AWS_SECRET_ACCESS_KEY=your_aws_secret_key
AWS_STORAGE_BUCKET_NAME=your-bucket-name
AWS_S3_REGION_NAME=us-east-1  # or your region
```

---

## Testing Checklist

### Backend Testing
- [ ] Run migration: `python manage.py migrate crs`
- [ ] Upload a new CRS document via `/api/v1/crs/revision-chains/{id}/upload_and_add_revision/`
- [ ] Check Docker logs for S3 upload confirmation
- [ ] Verify `excel_s3_url` field is populated in response
- [ ] Test presigned URL generation in serializer

### Frontend Testing
- [ ] Upload a new revision in CRS Multi-Revision workflow
- [ ] Click download button - should download from S3
- [ ] Verify downloaded file opens correctly in Excel
- [ ] Test fallback behavior when S3 disabled (USE_S3=False)
- [ ] Check browser console for S3 download logs

### S3 Bucket Structure
```
your-bucket-name/
  crs/
    revisions/
      CHAIN-001/
        Rev 1/
          CRS_CHAIN-001_Rev 1_20260116_143000.xls
        Rev 2/
          CRS_CHAIN-001_Rev 2_20260116_150000.xls
```

---

## Benefits

1. **Performance:** Pre-generated excels load instantly
2. **Scalability:** Offload file generation from frontend
3. **Consistency:** Same excel format across uploads
4. **Reliability:** Fallback to on-the-fly generation if S3 fails
5. **Security:** Presigned URLs expire after 1 hour
6. **Storage:** Centralized storage in S3 bucket

---

## Rollback Plan

If issues occur:

1. Set `USE_S3=False` in environment
2. Frontend will automatically fall back to existing download logic
3. No data loss - existing downloads still work
4. Can re-enable S3 after fixing issues

---

## Next Steps

1. ✅ Backend code completed
2. ⏳ Run database migration
3. ⏳ Update frontend components
4. ⏳ Configure S3 credentials
5. ⏳ Test end-to-end workflow
6. ⏳ Deploy to staging/production
