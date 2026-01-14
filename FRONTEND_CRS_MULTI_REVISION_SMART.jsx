/**
 * CRS Multi-Revision Workflow - SMART VERSION
 * With Preview Table & HTML-to-Excel Export
 */

import React, { useState } from 'react';
import { 
  Box, Button, Card, CardContent, TextField, Typography, Grid, Alert,
  Paper, Stepper, Step, StepLabel, CircularProgress, Divider,
  List, ListItem, ListItemText, Chip, Table, TableBody, TableCell,
  TableContainer, TableHead, TableRow, IconButton, Tooltip
} from '@mui/material';
import {
  CloudUpload as UploadIcon, CheckCircle as CheckIcon,
  Download as DownloadIcon, Visibility as PreviewIcon,
  TableChart as TableIcon
} from '@mui/icons-material';

// Smart configuration
const CONFIG = {
  API_BASE_URL: import.meta.env.VITE_API_URL || '/api/v1',
  TOKEN_KEYS: ['radai_access_token', 'access_token', 'access'],
  MAX_FILE_SIZE_MB: 50,
  ACCEPTED_FILE_TYPE: '.pdf'
};

const CRSMultiRevisionSmart = () => {
  // Step tracking
  const [currentStep, setCurrentStep] = useState(0);
  
  // Chain details
  const [chainForm, setChainForm] = useState({
    chain_id: '', document_title: '', document_number: '',
    project_name: '', contractor_name: '', department: '',
    notes: '', max_allowed_revisions: 10  // Flexible: user can set max, finish early
  });
  
  // Created chain and revisions
  const [createdChain, setCreatedChain] = useState(null);
  const [currentRevisionNumber, setCurrentRevisionNumber] = useState(1);
  const [uploadedRevisions, setUploadedRevisions] = useState([]);
  const [allComments, setAllComments] = useState([]); // For preview table
  
  // UI states
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [success, setSuccess] = useState(null);
  const [showPreview, setShowPreview] = useState(false);

  // Smart auth helper
  const getAuthHeaders = () => {
    for (const key of CONFIG.TOKEN_KEYS) {
      const token = localStorage.getItem(key);
      if (token) return { 'Authorization': `Bearer ${token}` };
    }
    console.warn('[CRS] No auth token found');
    return {};
  };

  // Create Chain
  const handleCreateChain = async (e) => {
    e.preventDefault();
    setLoading(true);
    setError(null);

    try {
      const response = await fetch(`${CONFIG.API_BASE_URL}/crs/revision-chains/`, {
        method: 'POST',
        headers: { ...getAuthHeaders(), 'Content-Type': 'application/json' },
        body: JSON.stringify(chainForm)
      });

      if (!response.ok) {
        const errorData = await response.json().catch(() => ({}));
        throw new Error(errorData.detail || `Failed: ${response.status}`);
      }

      const data = await response.json();
      setCreatedChain(data);
      setSuccess(`Chain "${data.chain_id}" created successfully!`);
      setCurrentStep(1);
      setError(null);
    } catch (err) {
      setError(err.message || 'Failed to create chain');
      console.error('[CRS] Create chain error:', err);
    } finally {
      setLoading(false);
    }
  };

  // Upload Revision with smart file validation
  const handleUploadRevision = async (e) => {
    const file = e.target.files[0];
    if (!file) return;

    // Smart validation
    if (!file.name.toLowerCase().endsWith('.pdf')) {
      setError('Only PDF files are accepted');
      return;
    }

    const fileSizeMB = file.size / (1024 * 1024);
    if (fileSizeMB > CONFIG.MAX_FILE_SIZE_MB) {
      setError(`File too large (${fileSizeMB.toFixed(1)} MB). Max: ${CONFIG.MAX_FILE_SIZE_MB} MB`);
      return;
    }

    setLoading(true);
    setError(null);

    try {
      const formData = new FormData();
      formData.append('file', file);  // Backend expects 'file', not 'pdf_file'
      formData.append('revision_label', `Rev ${currentRevisionNumber}`);
      formData.append('notes', `Revision ${currentRevisionNumber} upload`);

      const response = await fetch(
        `${CONFIG.API_BASE_URL}/crs/revision-chains/${createdChain.id}/upload_and_add_revision/`,
        {
          method: 'POST',
          headers: getAuthHeaders(),
          body: formData
        }
      );

      if (!response.ok) {
        const errorData = await response.json().catch(() => ({}));
        throw new Error(errorData.error || `Upload failed: ${response.status}`);
      }

      const data = await response.json();
      
      // Smart comment extraction from response
      let extractedComments = [];
      
      // Try multiple paths to find comments in the response
      if (data.data?.comments) {
        extractedComments = data.data.comments;
      } else if (data.comments) {
        extractedComments = data.comments;
      } else if (data.data?.document_details?.comments) {
        extractedComments = data.data.document_details.comments;
      }
      
      // Store revision with extracted comments
      const revisionData = {
        number: currentRevisionNumber,
        label: `Rev ${currentRevisionNumber}`,
        fileName: file.name,
        data: data.data,
        uploadedAt: new Date().toISOString(),
        comments: extractedComments, // Store comments directly
        totalComments: data.data?.extraction_summary?.total_comments || extractedComments.length || 0
      };

      setUploadedRevisions(prev => [...prev, revisionData]);
      
      // Add comments to global comments list
      if (extractedComments.length > 0) {
        setAllComments(prev => [...prev, ...extractedComments.map(c => ({
          ...c,
          revision: revisionData.label,
          revisionNumber: currentRevisionNumber
        }))]);
      }

      setSuccess(`Revision ${currentRevisionNumber} uploaded successfully! (${revisionData.totalComments} comments found)`);
      setCurrentRevisionNumber(prev => prev + 1);
      setError(null);

      // Auto-show preview after first upload
      if (currentRevisionNumber === 1) {
        setShowPreview(true);
      }
    } catch (err) {
      setError(err.message || 'Upload failed');
      console.error('[CRS] Upload error:', err);
    } finally {
      setLoading(false);
      e.target.value = ''; // Reset file input
    }
  };

  // Smart HTML-to-Excel export (like CRS management)
  const handleDownloadExcel = async () => {
    try {
      setLoading(true);
      
      // Fetch all chain data
      const response = await fetch(
        `${CONFIG.API_BASE_URL}/crs/revision-chains/${createdChain.id}/`,
        { headers: getAuthHeaders() }
      );

      if (!response.ok) throw new Error('Failed to fetch chain data');
      
      const chainData = await response.json();

      // Build smart HTML table
      const html = generateSmartHTML(chainData, uploadedRevisions);

      // Convert to Excel-compatible format
      const blob = new Blob([html], { 
        type: 'application/vnd.ms-excel;charset=utf-8' 
      });
      
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = `CRS_${chainData.chain_id}_${new Date().toISOString().split('T')[0]}.xls`;
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
      URL.revokeObjectURL(url);

      setSuccess('Excel file downloaded successfully!');
    } catch (err) {
      setError(`Download failed: ${err.message}`);
      console.error('[CRS] Excel download error:', err);
    } finally {
      setLoading(false);
    }
  };

  // Generate smart HTML for Excel export
  const generateSmartHTML = (chainData, revisions) => {
    return `
      <html xmlns:o="urn:schemas-microsoft-com:office:office" xmlns:x="urn:schemas-microsoft-com:office:excel">
      <head>
        <meta charset="UTF-8">
        <style>
          table { border-collapse: collapse; width: 100%; }
          th, td { border: 1px solid black; padding: 8px; text-align: left; }
          th { background-color: #4CAF50; color: white; font-weight: bold; }
          .section-header { background-color: #2196F3; color: white; font-weight: bold; }
        </style>
      </head>
      <body>
        <h2>CRS Multi-Revision Report</h2>
        
        <!-- Chain Information -->
        <table>
          <tr class="section-header"><th colspan="2">Chain Information</th></tr>
          <tr><td><b>Chain ID</b></td><td>${chainData.chain_id || 'N/A'}</td></tr>
          <tr><td><b>Document Title</b></td><td>${chainData.document_title || 'N/A'}</td></tr>
          <tr><td><b>Document Number</b></td><td>${chainData.document_number || 'N/A'}</td></tr>
          <tr><td><b>Project Name</b></td><td>${chainData.project_name || 'N/A'}</td></tr>
          <tr><td><b>Contractor</b></td><td>${chainData.contractor_name || 'N/A'}</td></tr>
          <tr><td><b>Department</b></td><td>${chainData.department || 'N/A'}</td></tr>
          <tr><td><b>Total Revisions</b></td><td>${revisions.length}</td></tr>
        </table>
        <br/>
        
        <!-- Revisions Summary -->
        <table>
          <tr class="section-header"><th colspan="5">Revisions Summary</th></tr>
          <tr>
            <th>Revision</th>
            <th>File Name</th>
            <th>Total Comments</th>
            <th>Uploaded At</th>
            <th>Status</th>
          </tr>
          ${revisions.map(rev => `
            <tr>
              <td>${rev.label}</td>
              <td>${rev.fileName}</td>
              <td>${rev.data?.extraction_summary?.total_comments || 0}</td>
              <td>${new Date(rev.uploadedAt).toLocaleString()}</td>
              <td>${rev.data?.revision?.status || 'N/A'}</td>
            </tr>
          `).join('')}
        </table>
        <br/>
        
        <!-- All Comments -->
        <table>
          <tr class="section-header"><th colspan="7">All Comments</th></tr>
          <tr>
            <th>Page</th>
            <th>Reviewer</th>
            <th>Comment</th>
            <th>Type</th>
            <th>Discipline</th>
            <th>Drawing Ref</th>
            <th>Status</th>
          </tr>
          ${revisions.map(rev => {
            // Smart comment extraction
            const comments = rev.comments || rev.data?.comments || rev.data?.document_details?.recent_activities || [];
            return comments.length > 0 ? comments.map(comment => `
              <tr>
                <td>${comment.page_number || 'N/A'}</td>
                <td>${comment.reviewer_name || 'Not Provided'}</td>
                <td>${comment.comment_text || comment.text || 'N/A'}</td>
                <td>${comment.comment_type || comment.type || 'General'}</td>
                <td>${comment.discipline || 'Not Provided'}</td>
                <td>${comment.drawing_ref || 'N/A'}</td>
                <td>${comment.status || 'Open'}</td>
              </tr>
            `).join('') : `<tr><td colspan="7">No comments found for ${rev.label}</td></tr>`;
          }).join('')}
        </table>
      </body>
      </html>
    `;
  };

  // Smart preview table component
  const PreviewTable = () => {
    if (!showPreview || uploadedRevisions.length === 0) return null;

    return (
      <Card sx={{ mt: 3, mb: 3 }}>
        <CardContent>
          <Box sx={{ display: 'flex', alignItems: 'center', mb: 2 }}>
            <TableIcon sx={{ mr: 1, color: 'primary.main' }} />
            <Typography variant="h6">Preview: All Revisions & Comments</Typography>
            <Box sx={{ flexGrow: 1 }} />
            <Button
              variant="contained"
              color="success"
              startIcon={<DownloadIcon />}
              onClick={handleDownloadExcel}
              disabled={loading}
            >
              Download Excel
            </Button>
          </Box>

          <Divider sx={{ mb: 2 }} />

          {/* Chain Info */}
          <Typography variant="subtitle1" gutterBottom sx={{ fontWeight: 'bold' }}>
            Chain Information
          </Typography>
          <TableContainer component={Paper} sx={{ mb: 3, maxHeight: 200 }}>
            <Table size="small">
              <TableBody>
                <TableRow>
                  <TableCell sx={{ fontWeight: 'bold', width: '30%' }}>Chain ID</TableCell>
                  <TableCell>{createdChain?.chain_id}</TableCell>
                </TableRow>
                <TableRow>
                  <TableCell sx={{ fontWeight: 'bold' }}>Document Title</TableCell>
                  <TableCell>{createdChain?.document_title}</TableCell>
                </TableRow>
                <TableRow>
                  <TableCell sx={{ fontWeight: 'bold' }}>Project</TableCell>
                  <TableCell>{createdChain?.project_name}</TableCell>
                </TableRow>
                <TableRow>
                  <TableCell sx={{ fontWeight: 'bold' }}>Total Revisions</TableCell>
                  <TableCell>{uploadedRevisions.length}</TableCell>
                </TableRow>
              </TableBody>
            </Table>
          </TableContainer>

          {/* Revisions Summary */}
          <Typography variant="subtitle1" gutterBottom sx={{ fontWeight: 'bold' }}>
            Revisions Summary
          </Typography>
          <TableContainer component={Paper} sx={{ mb: 3, maxHeight: 300 }}>
            <Table size="small" stickyHeader>
              <TableHead>
                <TableRow>
                  <TableCell sx={{ fontWeight: 'bold', bgcolor: '#2196F3', color: 'white' }}>Revision</TableCell>
                  <TableCell sx={{ fontWeight: 'bold', bgcolor: '#2196F3', color: 'white' }}>File Name</TableCell>
                  <TableCell sx={{ fontWeight: 'bold', bgcolor: '#2196F3', color: 'white' }}>Comments</TableCell>
                  <TableCell sx={{ fontWeight: 'bold', bgcolor: '#2196F3', color: 'white' }}>Status</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {uploadedRevisions.map((rev, idx) => (
                  <TableRow key={idx}>
                    <TableCell>{rev.label}</TableCell>
                    <TableCell>{rev.fileName}</TableCell>
                    <TableCell>
                      <Chip 
                        label={rev.data?.extraction_summary?.total_comments || 0}
                        color="primary"
                        size="small"
                      />
                    </TableCell>
                    <TableCell>
                      <Chip 
                        label={rev.data?.revision?.status || 'N/A'}
                        color="success"
                        size="small"
                      />
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </TableContainer>

          {/* Comments Detail (if available) */}
          <Typography variant="subtitle1" gutterBottom sx={{ fontWeight: 'bold' }}>
            All Comments Across Revisions
          </Typography>
          <TableContainer component={Paper} sx={{ maxHeight: 400 }}>
            <Table size="small" stickyHeader>
              <TableHead>
                <TableRow>
                  <TableCell sx={{ fontWeight: 'bold', bgcolor: '#4CAF50', color: 'white' }}>Page</TableCell>
                  <TableCell sx={{ fontWeight: 'bold', bgcolor: '#4CAF50', color: 'white' }}>Reviewer</TableCell>
                  <TableCell sx={{ fontWeight: 'bold', bgcolor: '#4CAF50', color: 'white', minWidth: 300 }}>Comment</TableCell>
                  <TableCell sx={{ fontWeight: 'bold', bgcolor: '#4CAF50', color: 'white' }}>Type</TableCell>
                  <TableCell sx={{ fontWeight: 'bold', bgcolor: '#4CAF50', color: 'white' }}>Discipline</TableCell>
                  <TableCell sx={{ fontWeight: 'bold', bgcolor: '#4CAF50', color: 'white' }}>Drawing Ref</TableCell>
                  <TableCell sx={{ fontWeight: 'bold', bgcolor: '#4CAF50', color: 'white' }}>Status</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {uploadedRevisions.map((rev) => {
                  // Smart comment extraction: try multiple paths
                  const comments = rev.comments || rev.data?.comments || rev.data?.document_details?.recent_activities || [];
                  
                  return comments.length > 0 ? (
                    comments.map((comment, idx) => (
                      <TableRow key={`${rev.number}-${idx}`}>
                        <TableCell>{comment.page_number || 'N/A'}</TableCell>
                        <TableCell>{comment.reviewer_name || 'Not Provided'}</TableCell>
                        <TableCell sx={{ wordBreak: 'break-word' }}>{comment.comment_text || comment.text || 'N/A'}</TableCell>
                        <TableCell>
                          <Chip 
                            label={comment.comment_type || comment.type || 'General'}
                            size="small"
                            color={comment.comment_type?.includes('RED') || comment.type?.includes('RED') ? 'error' : 'warning'}
                          />
                        </TableCell>
                        <TableCell>{comment.discipline || 'Not Provided'}</TableCell>
                        <TableCell>{comment.drawing_ref || 'N/A'}</TableCell>
                        <TableCell>
                          <Chip 
                            label={comment.status || 'Open'}
                            size="small"
                            color="default"
                          />
                        </TableCell>
                      </TableRow>
                    ))
                  ) : (
                    <TableRow key={`${rev.number}-empty`}>
                      <TableCell colSpan={7} align="center" sx={{ fontStyle: 'italic', color: 'text.secondary' }}>
                        No comments found for {rev.label}
                      </TableCell>
                    </TableRow>
                  );
                })}
                {uploadedRevisions.length === 0 && (
                  <TableRow>
                    <TableCell colSpan={7} align="center" sx={{ fontStyle: 'italic', color: 'text.secondary' }}>
                      No revisions uploaded yet
                    </TableCell>
                  </TableRow>
                )}
              </TableBody>
            </Table>
          </TableContainer>
        </CardContent>
      </Card>
    );
  };

  return (
    <Box sx={{ p: 3 }}>
      <Typography variant="h4" gutterBottom>
        CRS Multi-Revision Workflow (Smart Version)
      </Typography>

      {/* Stepper */}
      <Stepper activeStep={currentStep} sx={{ mb: 4 }}>
        <Step><StepLabel>Create Chain</StepLabel></Step>
        <Step><StepLabel>Upload Revisions</StepLabel></Step>
        <Step><StepLabel>Complete</StepLabel></Step>
      </Stepper>

      {/* Error/Success Messages */}
      {error && <Alert severity="error" sx={{ mb: 2 }} onClose={() => setError(null)}>{error}</Alert>}
      {success && <Alert severity="success" sx={{ mb: 2 }} onClose={() => setSuccess(null)}>{success}</Alert>}

      {/* STEP 0: Create Chain */}
      {currentStep === 0 && (
        <Card>
          <CardContent>
            <Typography variant="h6" gutterBottom>Step 1: Create Revision Chain</Typography>
            <form onSubmit={handleCreateChain}>
              <Grid container spacing={2}>
                <Grid item xs={12} md={6}>
                  <TextField
                    fullWidth
                    label="Chain ID *"
                    value={chainForm.chain_id}
                    onChange={(e) => setChainForm({...chainForm, chain_id: e.target.value})}
                    required
                  />
                </Grid>
                <Grid item xs={12} md={6}>
                  <TextField
                    fullWidth
                    label="Document Title *"
                    value={chainForm.document_title}
                    onChange={(e) => setChainForm({...chainForm, document_title: e.target.value})}
                    required
                  />
                </Grid>
                <Grid item xs={12} md={6}>
                  <TextField
                    fullWidth
                    label="Document Number *"
                    value={chainForm.document_number}
                    onChange={(e) => setChainForm({...chainForm, document_number: e.target.value})}
                    required
                  />
                </Grid>
                <Grid item xs={12} md={6}>
                  <TextField
                    fullWidth
                    label="Project Name *"
                    value={chainForm.project_name}
                    onChange={(e) => setChainForm({...chainForm, project_name: e.target.value})}
                    required
                  />
                </Grid>
                <Grid item xs={12} md={6}>
                  <TextField
                    fullWidth
                    label="Contractor Name"
                    value={chainForm.contractor_name}
                    onChange={(e) => setChainForm({...chainForm, contractor_name: e.target.value})}
                  />
                </Grid>
                <Grid item xs={12} md={6}>
                  <TextField
                    fullWidth
                    label="Department"
                    value={chainForm.department}
                    onChange={(e) => setChainForm({...chainForm, department: e.target.value})}
                  />
                </Grid>
                <Grid item xs={12} md={6}>
                  <TextField
                    fullWidth
                    type="number"
                    label="Maximum Revisions *"
                    value={chainForm.max_allowed_revisions}
                    onChange={(e) => setChainForm({...chainForm, max_allowed_revisions: parseInt(e.target.value) || 10})}
                    inputProps={{ min: 1, max: 50 }}
                    helperText="You can finish early, this is just the maximum"
                    required
                  />
                </Grid>
                <Grid item xs={12}>
                  <TextField
                    fullWidth
                    multiline
                    rows={2}
                    label="Notes"
                    value={chainForm.notes}
                    onChange={(e) => setChainForm({...chainForm, notes: e.target.value})}
                  />
                </Grid>
                <Grid item xs={12}>
                  <Button
                    type="submit"
                    variant="contained"
                    color="primary"
                    size="large"
                    disabled={loading}
                    fullWidth
                  >
                    {loading ? <CircularProgress size={24} /> : 'Create Chain'}
                  </Button>
                </Grid>
              </Grid>
            </form>
          </CardContent>
        </Card>
      )}

      {/* STEP 1: Upload Revisions */}
      {currentStep === 1 && (
        <Box>
          <Card sx={{ mb: 3 }}>
            <CardContent>
              <Typography variant="h6" gutterBottom>
                Chain: {createdChain?.chain_id} - Upload Revisions
              </Typography>
              
              <Box sx={{ display: 'flex', gap: 2, flexWrap: 'wrap', mb: 3 }}>
                {Array.from({ length: createdChain?.max_allowed_revisions || 5 }).map((_, idx) => (
                  <Button
                    key={idx}
                    variant={idx < currentRevisionNumber - 1 ? "outlined" : idx === currentRevisionNumber - 1 ? "contained" : "text"}
                    color={idx < currentRevisionNumber - 1 ? "success" : "primary"}
                    startIcon={idx < currentRevisionNumber - 1 ? <CheckIcon /> : <UploadIcon />}
                    component="label"
                    disabled={idx !== currentRevisionNumber - 1 || loading}
                    sx={{ minWidth: 150 }}
                  >
                    Rev {idx + 1} {idx < currentRevisionNumber - 1 ? "✓" : ""}
                    <input
                      type="file"
                      accept={CONFIG.ACCEPTED_FILE_TYPE}
                      hidden
                      onChange={handleUploadRevision}
                      disabled={idx !== currentRevisionNumber - 1}
                    />
                  </Button>
                ))}
              </Box>

              {loading && (
                <Box sx={{ display: 'flex', alignItems: 'center', gap: 2 }}>
                  <CircularProgress size={24} />
                  <Typography>Processing PDF and extracting comments...</Typography>
                </Box>
              )}

              {uploadedRevisions.length > 0 && (
                <Box sx={{ mt: 3 }}>
                  <Typography variant="subtitle1" gutterBottom>Uploaded Revisions:</Typography>
                  <List>
                    {uploadedRevisions.map((rev, idx) => (
                      <ListItem key={idx}>
                        <ListItemText
                          primary={`${rev.label}: ${rev.fileName}`}
                          secondary={`${rev.data?.extraction_summary?.total_comments || 0} comments extracted`}
                        />
                        <Chip label={rev.data?.revision?.status || 'Submitted'} color="success" />
                      </ListItem>
                    ))}
                  </List>
                </Box>
              )}

              <Divider sx={{ my: 3 }} />

              <Box sx={{ display: 'flex', gap: 2, flexWrap: 'wrap' }}>
                <Button
                  variant="outlined"
                  onClick={() => setShowPreview(!showPreview)}
                  startIcon={<PreviewIcon />}
                  disabled={uploadedRevisions.length === 0}
                >
                  {showPreview ? 'Hide' : 'Show'} Preview
                </Button>
                <Button
                  variant="contained"
                  color="success"
                  startIcon={<DownloadIcon />}
                  onClick={handleDownloadExcel}
                  disabled={uploadedRevisions.length === 0 || loading}
                >
                  Download Excel
                </Button>
                <Box sx={{ flexGrow: 1 }} />
                <Button
                  variant="outlined"
                  color="warning"
                  startIcon={<CheckIcon />}
                  onClick={() => setCurrentStep(2)}
                  disabled={uploadedRevisions.length === 0}
                >
                  Finish Early ({uploadedRevisions.length} of {createdChain?.max_allowed_revisions})
                </Button>
                <Button
                  variant="contained"
                  onClick={() => setCurrentStep(2)}
                  disabled={uploadedRevisions.length === 0}
                >
                  Complete All Revisions
                </Button>
              </Box>
            </CardContent>
          </Card>

          {/* SMART PREVIEW TABLE */}
          <PreviewTable />
        </Box>
      )}

      {/* STEP 2: Complete */}
      {currentStep === 2 && (
        <Card>
          <CardContent>
            <Box sx={{ textAlign: 'center', py: 4 }}>
              <CheckIcon sx={{ fontSize: 80, color: 'success.main', mb: 2 }} />
              <Typography variant="h5" gutterBottom>
                Multi-Revision Workflow Complete!
              </Typography>
              <Typography variant="body1" color="text.secondary" gutterBottom>
                Chain: {createdChain?.chain_id} | Revisions: {uploadedRevisions.length}
              </Typography>
              <Box sx={{ mt: 4, display: 'flex', gap: 2, justifyContent: 'center' }}>
                <Button
                  variant="contained"
                  color="success"
                  startIcon={<DownloadIcon />}
                  onClick={handleDownloadExcel}
                  disabled={loading}
                >
                  Download Excel Report
                </Button>
                <Button
                  variant="outlined"
                  onClick={() => {
                    setCurrentStep(0);
                    setCreatedChain(null);
                    setUploadedRevisions([]);
                    setCurrentRevisionNumber(1);
                    setShowPreview(false);
                  }}
                >
                  Start New Chain
                </Button>
              </Box>
            </Box>
          </CardContent>
        </Card>
      )}
    </Box>
  );
};

export default CRSMultiRevisionSmart;
