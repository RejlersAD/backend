# Frontend S3 Integration - Quick Update Guide

## ✅ Backend Status: COMPLETE
- S3 enabled with new AWS credentials ✅
- Database migration applied ✅
- Auto-upload configured ✅

## 📝 Frontend Update Instructions

### File to Edit: `airflow_frontend/src/pages/CRSMultiRevisionSmart.jsx`

---

## Step 1: Add Import (at top of file, around line 1-10)

Find the existing imports and add:

```jsx
import CloudDownloadIcon from '@mui/icons-material/CloudDownload';
```

---

## Step 2: Add Download Handler (around line 200-300, with other handlers)

Add this new function **before** the return statement:

```jsx
  // S3 Excel Download Handler
  const handleDownloadFromS3 = async (revisionIndex) => {
    try {
      const revision = revisions[revisionIndex];
      
      // Check if S3 presigned URL exists
      if (revision.excel_download_url) {
        console.log(`✅ Downloading from S3: ${revision.revision_label}`);
        
        // Download from S3 using presigned URL
        const link = document.createElement('a');
        link.href = revision.excel_download_url;
        link.download = `CRS_${chainData.chain_id}_${revision.revision_label}_${new Date().toISOString().split('T')[0]}.xls`;
        link.target = '_blank';
        document.body.appendChild(link);
        link.click();
        document.body.removeChild(link);
        
        alert(`✅ Downloaded ${revision.revision_label} from S3`);
      } else {
        // Fallback: Generate on-the-fly (existing logic)
        console.warn('⚠️ No S3 URL available, generating excel on-the-fly');
        handleDownloadSingleRevision(revisionIndex);
      }
    } catch (error) {
      console.error('❌ Error downloading from S3:', error);
      // Fallback to existing on-the-fly generation
      alert('S3 download failed, generating excel...');
      handleDownloadSingleRevision(revisionIndex);
    }
  };
```

---

## Step 3: Update Download Button (around line 450-500, in revisions table)

Find this code in your revisions summary table:

```jsx
<IconButton
  onClick={() => handleDownloadSingleRevision(index)}
  color="primary"
  size="small"
  title="Download Excel"
>
  <DownloadIcon />
</IconButton>
```

**Replace with:**

```jsx
<Tooltip title={revision.excel_download_url ? "Download from S3 (instant)" : "Generate Excel"}>
  <IconButton
    onClick={() => handleDownloadFromS3(index)}
    color={revision.excel_download_url ? "success" : "primary"}
    size="small"
  >
    {revision.excel_download_url ? <CloudDownloadIcon /> : <DownloadIcon />}
  </IconButton>
</Tooltip>
```

---

## Step 4: OPTIONAL - Add S3 Status Badge

If you want to show which revisions have S3 files ready, add this next to the revision label:

```jsx
{revision.excel_s3_url && (
  <Chip
    label="S3 Ready"
    size="small"
    color="success"
    sx={{ ml: 1, height: 20, fontSize: 10 }}
  />
)}
```

---

## 🧪 Testing

1. **Upload a new CRS revision** via the multi-revision workflow
2. **Check browser console** - should see: `✅ Downloaded from S3: Rev X`
3. **Click download button** - should download instantly (no generation delay)
4. **Open downloaded file** - should open correctly in Excel

---

## 🔍 Verification

Check API response for new fields:
```json
{
  "revision_label": "Rev 1",
  "excel_s3_url": "s3://user-management-rejlers/crs/revisions/...",
  "excel_generated_at": "2026-01-16T11:30:00Z",
  "excel_download_url": "https://user-management-rejlers.s3.me-central-1.amazonaws.com/..."
}
```

---

## 📂 Complete Code Example

Here's what the download section should look like after update:

```jsx
// Handler function (add with other handlers)
const handleDownloadFromS3 = async (revisionIndex) => {
  try {
    const revision = revisions[revisionIndex];
    
    if (revision.excel_download_url) {
      console.log(`✅ Downloading from S3: ${revision.revision_label}`);
      const link = document.createElement('a');
      link.href = revision.excel_download_url;
      link.download = `CRS_${chainData.chain_id}_${revision.revision_label}_${new Date().toISOString().split('T')[0]}.xls`;
      link.target = '_blank';
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
      alert(`✅ Downloaded ${revision.revision_label} from S3`);
    } else {
      console.warn('⚠️ No S3 URL, generating on-the-fly');
      handleDownloadSingleRevision(revisionIndex);
    }
  } catch (error) {
    console.error('❌ S3 download error:', error);
    alert('S3 download failed, generating excel...');
    handleDownloadSingleRevision(revisionIndex);
  }
};

// In your table (around line 450-500)
<TableCell align="center">
  <Tooltip title={revision.excel_download_url ? "Download from S3" : "Generate Excel"}>
    <IconButton
      onClick={() => handleDownloadFromS3(index)}
      color={revision.excel_download_url ? "success" : "primary"}
      size="small"
    >
      {revision.excel_download_url ? <CloudDownloadIcon /> : <DownloadIcon />}
    </IconButton>
  </Tooltip>
</TableCell>
```

---

## ✅ Checklist

- [ ] Add `CloudDownloadIcon` import
- [ ] Add `handleDownloadFromS3` handler function
- [ ] Update download button to use new handler
- [ ] Test with new revision upload
- [ ] Verify S3 download works
- [ ] Check fallback works when S3 disabled

---

## 🎉 Benefits

- **Instant Downloads:** No generation delay
- **Consistent Files:** Same file every time
- **Reduced Load:** No frontend processing
- **Fallback Ready:** Automatically uses old method if S3 fails
- **Secure:** Presigned URLs expire after 1 hour

---

## 🆘 Need Help?

Check the complete code examples in:
- `FRONTEND_S3_INTEGRATION_CODE.js` (in RAD_AI folder)
- `S3_EXCEL_IMPLEMENTATION_SUMMARY.md` (complete guide)
