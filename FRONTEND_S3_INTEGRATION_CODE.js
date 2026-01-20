/**
 * Frontend Code Snippets for S3 Excel Download Integration
 * 
 * Add these to your frontend components:
 * - CRSMultiRevisionSmart.jsx
 * - CRSChainDetail.jsx
 * - CRSDocuments.jsx
 */

// ============================================
// 1. ADD TO CRSMultiRevisionSmart.jsx
// ============================================

/**
 * Handler for downloading Excel from S3 or fallback to on-the-fly generation
 * Add this function inside your component
 */
const handleDownloadFromS3 = async (revisionIndex) => {
  try {
    const revision = revisions[revisionIndex];
    
    // Check if S3 presigned URL exists
    if (revision.excel_download_url) {
      console.log(`Downloading from S3: ${revision.revision_label}`);
      
      // Download from S3 using presigned URL
      const link = document.createElement('a');
      link.href = revision.excel_download_url;
      link.download = `CRS_${chainData.chain_id}_${revision.revision_label}_${new Date().toISOString().split('T')[0]}.xls`;
      link.target = '_blank'; // Open in new tab as backup
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
      
      // Show success message
      alert(`Downloaded ${revision.revision_label} from S3`);
    } else {
      // Fallback: Generate on-the-fly (existing logic)
      console.warn('No S3 URL available, generating excel on-the-fly');
      handleDownloadSingleRevision(revisionIndex);
    }
  } catch (error) {
    console.error('Error downloading from S3:', error);
    // Fallback to existing on-the-fly generation
    alert('S3 download failed, generating excel...');
    handleDownloadSingleRevision(revisionIndex);
  }
};

/**
 * Updated table cell with S3-aware download button
 * Replace the existing download IconButton in your revisions table
 */
// In your revisions summary table (around line 450-500):
<TableCell align="center">
  <Tooltip title={revision.excel_download_url ? "Download from S3" : "Generate Excel"}>
    <IconButton
      onClick={() => handleDownloadFromS3(index)}
      color={revision.excel_download_url ? "primary" : "default"}
      size="small"
    >
      {revision.excel_download_url ? <CloudDownloadIcon /> : <DownloadIcon />}
    </IconButton>
  </Tooltip>
  
  {/* Optional: Show S3 status badge */}
  {revision.excel_s3_url && (
    <Chip
      label="S3"
      size="small"
      color="success"
      sx={{ ml: 0.5, height: 16, fontSize: 9 }}
    />
  )}
</TableCell>


// ============================================
// 2. ADD TO CRSChainDetail.jsx
// ============================================

/**
 * Updated revision history table with S3 download links
 */
<TableCell align="center">
  {revision.excel_download_url ? (
    <Button
      variant="contained"
      size="small"
      color="primary"
      startIcon={<CloudDownloadIcon />}
      onClick={() => window.open(revision.excel_download_url, '_blank')}
      sx={{ textTransform: 'none' }}
    >
      Download Excel
    </Button>
  ) : (
    <Tooltip title="Excel not yet generated">
      <span>
        <Button
          variant="outlined"
          size="small"
          startIcon={<DownloadIcon />}
          disabled
          sx={{ textTransform: 'none' }}
        >
          Not Available
        </Button>
      </span>
    </Tooltip>
  )}
  
  {/* Optional: Show generation timestamp */}
  {revision.excel_generated_at && (
    <Typography variant="caption" display="block" color="textSecondary">
      Generated: {new Date(revision.excel_generated_at).toLocaleDateString()}
    </Typography>
  )}
</TableCell>


// ============================================
// 3. ADD TO CRSDocuments.jsx (Regular Upload)
// ============================================

/**
 * Add S3 download button to document list actions
 */
<TableCell align="center">
  <Box sx={{ display: 'flex', gap: 1, justifyContent: 'center' }}>
    {/* Existing action buttons */}
    
    {/* S3 Excel Download Button */}
    {document.excel_download_url && (
      <Tooltip title="Download Excel (S3)">
        <IconButton
          size="small"
          color="primary"
          onClick={() => window.open(document.excel_download_url, '_blank')}
        >
          <CloudDownloadIcon />
        </IconButton>
      </Tooltip>
    )}
    
    {/* Existing buttons */}
  </Box>
</TableCell>


// ============================================
// 4. IMPORTS TO ADD (at top of files)
// ============================================

import CloudDownloadIcon from '@mui/icons-material/CloudDownload';
import DownloadIcon from '@mui/icons-material/Download';
import { Chip, Tooltip, Button, Typography, Box } from '@mui/material';


// ============================================
// 5. OPTIONAL: Add S3 Status Indicator
// ============================================

/**
 * Helper component to show S3 availability status
 * Can be used in any component
 */
const S3StatusBadge = ({ revision }) => {
  if (!revision.excel_s3_url) return null;
  
  const isExpired = revision.excel_generated_at && 
    new Date() - new Date(revision.excel_generated_at) > 24 * 60 * 60 * 1000; // 24 hours
  
  return (
    <Tooltip title={isExpired ? "Excel may be outdated" : "Excel available in S3"}>
      <Chip
        icon={<CloudDownloadIcon sx={{ fontSize: 12 }} />}
        label="S3 Ready"
        size="small"
        color={isExpired ? "warning" : "success"}
        variant="outlined"
        sx={{ height: 20, fontSize: 10 }}
      />
    </Tooltip>
  );
};

// Usage in table:
<TableCell>
  {revision.revision_label}
  <S3StatusBadge revision={revision} />
</TableCell>


// ============================================
// 6. OPTIONAL: Bulk Download Handler
// ============================================

/**
 * Download all revisions from S3 at once
 * Add this to CRSMultiRevisionSmart.jsx
 */
const handleBulkDownloadFromS3 = async () => {
  const s3Revisions = revisions.filter(r => r.excel_download_url);
  
  if (s3Revisions.length === 0) {
    alert('No S3 excels available. Using fallback generation...');
    handleDownloadExcel(); // existing combined download
    return;
  }
  
  // Download each revision sequentially with delay
  for (let i = 0; i < s3Revisions.length; i++) {
    const rev = s3Revisions[i];
    
    setTimeout(() => {
      const link = document.createElement('a');
      link.href = rev.excel_download_url;
      link.download = `CRS_${chainData.chain_id}_${rev.revision_label}.xls`;
      link.click();
    }, i * 1000); // 1 second delay between downloads
  }
  
  alert(`Downloading ${s3Revisions.length} revisions from S3...`);
};


// ============================================
// 7. ERROR HANDLING & FALLBACK EXAMPLE
// ============================================

/**
 * Robust download handler with multiple fallback strategies
 */
const handleRobustDownload = async (revision, revisionIndex) => {
  try {
    // Strategy 1: Try S3 presigned URL
    if (revision.excel_download_url) {
      console.log('Attempting S3 download...');
      const response = await fetch(revision.excel_download_url, { method: 'HEAD' });
      
      if (response.ok) {
        window.open(revision.excel_download_url, '_blank');
        return;
      }
    }
    
    // Strategy 2: Request new presigned URL from API
    if (revision.excel_s3_url) {
      console.log('Requesting new presigned URL...');
      const response = await api.get(`/api/v1/crs/revisions/${revision.id}/download_url/`);
      
      if (response.data.download_url) {
        window.open(response.data.download_url, '_blank');
        return;
      }
    }
    
    // Strategy 3: Fallback to on-the-fly generation
    console.log('Falling back to on-the-fly generation...');
    handleDownloadSingleRevision(revisionIndex);
    
  } catch (error) {
    console.error('All download strategies failed:', error);
    alert('Download failed. Please try again or contact support.');
  }
};


// ============================================
// 8. USAGE EXAMPLE: Complete Component Update
// ============================================

/**
 * Example of updated CRSMultiRevisionSmart.jsx with S3 integration
 */

// Add near other handlers (around line 200-300)
const handleDownloadFromS3 = async (revisionIndex) => {
  try {
    const revision = revisions[revisionIndex];
    
    if (revision.excel_download_url) {
      // Download from S3
      const link = document.createElement('a');
      link.href = revision.excel_download_url;
      link.download = `CRS_${chainData.chain_id}_${revision.revision_label}_${new Date().toISOString().split('T')[0]}.xls`;
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
      console.log(`Downloaded from S3: ${revision.revision_label}`);
    } else {
      // Fallback
      console.warn('No S3 URL, generating on-the-fly');
      handleDownloadSingleRevision(revisionIndex);
    }
  } catch (error) {
    console.error('S3 download error:', error);
    handleDownloadSingleRevision(revisionIndex);
  }
};

// Update table cell (around line 450-500)
<TableRow key={index}>
  {/* ... other cells ... */}
  
  <TableCell align="center">
    <IconButton
      onClick={() => handleDownloadFromS3(index)}
      color="primary"
      size="small"
      title={revision.excel_download_url ? "Download from S3" : "Generate Excel"}
    >
      <DownloadIcon />
    </IconButton>
  </TableCell>
</TableRow>


// ============================================
// 9. BACKEND API ENDPOINT (Optional Enhancement)
// ============================================

/**
 * If you want to add a dedicated endpoint for getting fresh presigned URLs
 * Add this to apps/crs/revision_views.py in CRSRevisionViewSet:
 * 
 * @action(detail=True, methods=['get'])
 * def download_url(self, request, pk=None):
 *     revision = self.get_object()
 *     from .s3_excel_generator import CRSS3ExcelGenerator
 *     
 *     generator = CRSS3ExcelGenerator()
 *     download_url = generator.get_download_url(revision, expiration=3600)
 *     
 *     if download_url:
 *         return Response({
 *             'success': True,
 *             'download_url': download_url,
 *             'expires_in': 3600,
 *             'revision_label': revision.revision_label
 *         })
 *     else:
 *         return Response({
 *             'success': False,
 *             'error': 'Excel not available in S3'
 *         }, status=status.HTTP_404_NOT_FOUND)
 * 
 * Frontend usage:
 * const response = await api.get(`/api/v1/crs/revisions/${revision.id}/download_url/`);
 * window.open(response.data.download_url, '_blank');
 */
