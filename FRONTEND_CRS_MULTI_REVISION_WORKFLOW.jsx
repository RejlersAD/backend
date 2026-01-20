/**
 * CRS Multi-Revision Workflow
 * Create chain → Upload Rev 1 → Upload Rev 2 → Upload Rev 3...
 */

import React, { useState } from 'react';
import { 
  Box, 
  Button, 
  Card, 
  CardContent, 
  TextField, 
  Typography, 
  Grid, 
  Alert,
  Paper,
  Stepper,
  Step,
  StepLabel,
  CircularProgress,
  Divider,
  List,
  ListItem,
  ListItemText,
  Chip
} from '@mui/material';
import {
  CloudUpload as UploadIcon,
  CheckCircle as CheckIcon,
  Download as DownloadIcon,
  Add as AddIcon,
  NavigateNext as NextIcon
} from '@mui/icons-material';

// Soft-coded configuration
const CONFIG = {
  API_BASE_URL: import.meta.env.VITE_API_URL || '/api/v1',
  TOKEN_KEYS: ['radai_access_token', 'access_token', 'access'],
  MAX_REVISIONS: 10,
  MIN_REVISIONS: 1,
  ACCEPTED_FILE_TYPE: '.pdf'
};

const CRSMultiRevisionWorkflow = () => {
  // Step tracking
  const [currentStep, setCurrentStep] = useState(0); // 0=create chain, 1=upload revisions, 2=complete
  
  // Chain details form
  const [chainForm, setChainForm] = useState({
    chain_id: '',
    document_title: '',
    document_number: '',
    project_name: '',
    contractor_name: '',
    department: '',
    notes: '',
    max_allowed_revisions: 5
  });
  
  // Created chain and revisions
  const [createdChain, setCreatedChain] = useState(null);
  const [currentRevisionNumber, setCurrentRevisionNumber] = useState(1);
  const [uploadedRevisions, setUploadedRevisions] = useState([]);
  const [lastRevisionId, setLastRevisionId] = useState(null);
  
  // UI states
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [success, setSuccess] = useState(null);

  // Get auth token
  const getAuthHeaders = () => {
    let token = null;
    for (const key of CONFIG.TOKEN_KEYS) {
      token = localStorage.getItem(key);
      if (token) break;
    }
    if (!token) {
      console.warn('[CRS] No token found');
      return {};
    }
    return { 'Authorization': `Bearer ${token}` };
  };

  // STEP 1: Create Chain
  const handleCreateChain = async (e) => {
    e.preventDefault();
    setLoading(true);
    setError(null);

    try {
      const response = await fetch(`${CONFIG.API_BASE_URL}/crs/revision-chains/`, {
        method: 'POST',
        headers: {
          ...getAuthHeaders(),
          'Content-Type': 'application/json'
        },
        body: JSON.stringify(chainForm)
      });

      if (response.ok) {
        const data = await response.json();
        console.log('[CRS] Chain created - Full response:', JSON.stringify(data, null, 2));
        console.log('[CRS] Chain ID field:', data.id);
        console.log('[CRS] Chain PK field:', data.pk);
        console.log('[CRS] All keys:', Object.keys(data));
        setCreatedChain(data);
        setCurrentStep(1);
        setSuccess(`Chain created: ${data.chain_id || data.id}`);
      } else {
        const errData = await response.json().catch(() => ({}));
        console.error('[CRS] Chain creation failed:', response.status, errData);
        setError(`Failed to create chain: ${errData.detail || errData.chain_id?.[0] || response.statusText}`);
      }
    } catch (err) {
      setError(`Error: ${err.message}`);
    } finally {
      setLoading(false);
    }
  };

  // STEP 2: Upload Revision
  const handleUploadRevision = async (file) => {
    if (!file) return;
    
    setLoading(true);
    setError(null);

    try {
      const formData = new FormData();
      formData.append('file', file);
      formData.append('revision_label', `Rev ${currentRevisionNumber}`);
      
      if (lastRevisionId) {
        formData.append('parent_revision_id', lastRevisionId);
      }

      console.log('[CRS] Uploading to chain:', createdChain.id);
      console.log('[CRS] URL:', `${CONFIG.API_BASE_URL}/crs/revision-chains/${createdChain.id}/upload_and_add_revision/`);
      
      const response = await fetch(
        `${CONFIG.API_BASE_URL}/crs/revision-chains/${createdChain.id}/upload_and_add_revision/`,
        {
          method: 'POST',
          headers: getAuthHeaders(),
          body: formData
        }
      );

      if (response.ok) {
        const result = await response.json();
        console.log('[CRS] Upload success - Full response:', JSON.stringify(result, null, 2));
        const data = result.data || result; // Handle both response structures
        console.log('[CRS] Extraction summary:', data.extraction_summary);
        const newRevision = {
          number: currentRevisionNumber,
          label: `Rev ${currentRevisionNumber}`,
          fileName: file.name,
          comments: data.extraction_summary?.total_comments || 0,
          redComments: data.extraction_summary?.red_comments || 0,
          yellowComments: data.extraction_summary?.yellow_comments || 0,
          data: data
        };
        
        setUploadedRevisions([...uploadedRevisions, newRevision]);
        setLastRevisionId(data.revision?.id || null);
        setSuccess(`Rev ${currentRevisionNumber} uploaded! ${newRevision.comments} comments extracted.`);
        
        // Move to next revision or complete
        if (currentRevisionNumber >= chainForm.max_allowed_revisions) {
          setCurrentStep(2);
        } else {
          setCurrentRevisionNumber(currentRevisionNumber + 1);
        }
      } else {
        const errData = await response.json().catch(() => ({}));
        console.error('[CRS] Upload failed:', response.status, errData);
        const errorMsg = errData.details || errData.error || errData.detail || response.statusText;
        setError(`Failed to upload revision: ${errorMsg}`);
      }
    } catch (err) {
      setError(`Error: ${err.message}`);
    } finally {
      setLoading(false);
    }
  };

  // Download Excel
  const handleDownloadExcel = async () => {
    if (!createdChain) {
      setError('No chain created yet');
      return;
    }
    
    console.log('[CRS] Downloading Excel for chain:', createdChain.id);
    setLoading(true);
    setError(null);
    
    try {
      // Use simple HTML-to-Excel export endpoint
      const url = `${CONFIG.API_BASE_URL}/crs/revision-chains/${createdChain.id}/export_simple/`;
      console.log('[CRS] Excel download URL:', url);
      
      const response = await fetch(url, { 
        headers: getAuthHeaders(),
        method: 'GET'
      });
      
      console.log('[CRS] Excel response status:', response.status);
      
      if (response.ok) {
        const blob = await response.blob();
        console.log('[CRS] Excel blob size:', blob.size);
        
        const downloadUrl = window.URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = downloadUrl;
        a.download = `CRS_${createdChain.chain_id}_Export.xls`;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        window.URL.revokeObjectURL(downloadUrl);
        
        setSuccess('Excel downloaded successfully!');
      } else {
        const errorText = await response.text();
        console.error('[CRS] Excel download failed:', errorText);
        setError(`Failed to download Excel: ${response.statusText}`);
      }
    } catch (err) {
      console.error('[CRS] Excel download error:', err);
      setError('Failed to download Excel: ' + err.message);
    } finally {
      setLoading(false);
    }
  };

  // Reset and start over
  const handleReset = () => {
    setCurrentStep(0);
    setCreatedChain(null);
    setCurrentRevisionNumber(1);
    setUploadedRevisions([]);
    setLastRevisionId(null);
    setError(null);
    setSuccess(null);
    setChainForm({
      chain_id: '',
      document_title: '',
      document_number: '',
      project_name: '',
      contractor_name: '',
      department: '',
      notes: '',
      max_allowed_revisions: 5
    });
  };

  return (
    <Box sx={{ p: 3, maxWidth: 1200, mx: 'auto' }}>
      {/* Header */}
      <Typography variant="h4" gutterBottom fontWeight="bold">
        📄 CRS Multi-Revision Workflow
      </Typography>
      <Typography variant="body2" color="text.secondary" sx={{ mb: 3 }}>
        Create a new revision chain and upload CRS sheets one by one
      </Typography>

      {/* Stepper */}
      <Stepper activeStep={currentStep} sx={{ mb: 4 }}>
        <Step>
          <StepLabel>Create Chain</StepLabel>
        </Step>
        <Step>
          <StepLabel>Upload Revisions</StepLabel>
        </Step>
        <Step>
          <StepLabel>Complete</StepLabel>
        </Step>
      </Stepper>

      {/* Alerts */}
      {error && <Alert severity="error" sx={{ mb: 2 }} onClose={() => setError(null)}>{error}</Alert>}
      {success && <Alert severity="success" sx={{ mb: 2 }} onClose={() => setSuccess(null)}>{success}</Alert>}

      {/* STEP 0: Create Chain Form */}
      {currentStep === 0 && (
        <Paper sx={{ p: 4 }}>
          <Typography variant="h5" gutterBottom>
            Step 1: Create New Revision Chain
          </Typography>
          <Typography variant="body2" color="text.secondary" sx={{ mb: 3 }}>
            Enter project details and specify how many revisions you want to upload
          </Typography>

          <form onSubmit={handleCreateChain}>
            <Grid container spacing={3}>
              <Grid item xs={12} md={6}>
                <TextField
                  label="Chain ID *"
                  fullWidth
                  required
                  value={chainForm.chain_id}
                  onChange={(e) => setChainForm({...chainForm, chain_id: e.target.value})}
                  placeholder="e.g., CHAIN-001"
                  helperText="Unique identifier for this chain"
                />
              </Grid>
              <Grid item xs={12} md={6}>
                <TextField
                  label="Document Title *"
                  fullWidth
                  required
                  value={chainForm.document_title}
                  onChange={(e) => setChainForm({...chainForm, document_title: e.target.value})}
                  placeholder="e.g., Main Building Structural Drawings"
                />
              </Grid>
              <Grid item xs={12} md={6}>
                <TextField
                  label="Document Number *"
                  fullWidth
                  required
                  value={chainForm.document_number}
                  onChange={(e) => setChainForm({...chainForm, document_number: e.target.value})}
                  placeholder="e.g., STR-001"
                />
              </Grid>
              <Grid item xs={12} md={6}>
                <TextField
                  label="Project Name *"
                  fullWidth
                  required
                  value={chainForm.project_name}
                  onChange={(e) => setChainForm({...chainForm, project_name: e.target.value})}
                  placeholder="e.g., Downtown Tower Project"
                />
              </Grid>
              <Grid item xs={12} md={6}>
                <TextField
                  label="Contractor Name"
                  fullWidth
                  value={chainForm.contractor_name}
                  onChange={(e) => setChainForm({...chainForm, contractor_name: e.target.value})}
                  placeholder="e.g., ABC Construction"
                />
              </Grid>
              <Grid item xs={12} md={6}>
                <TextField
                  label="Department"
                  fullWidth
                  value={chainForm.department}
                  onChange={(e) => setChainForm({...chainForm, department: e.target.value})}
                  placeholder="e.g., Engineering"
                />
              </Grid>
              <Grid item xs={12} md={6}>
                <TextField
                  label="Number of Revisions *"
                  type="number"
                  fullWidth
                  required
                  value={chainForm.max_allowed_revisions}
                  onChange={(e) => setChainForm({
                    ...chainForm, 
                    max_allowed_revisions: Math.min(CONFIG.MAX_REVISIONS, Math.max(CONFIG.MIN_REVISIONS, parseInt(e.target.value) || 1))
                  })}
                  InputProps={{
                    inputProps: { 
                      min: CONFIG.MIN_REVISIONS, 
                      max: CONFIG.MAX_REVISIONS 
                    }
                  }}
                  helperText={`How many CRS sheets will you upload? (${CONFIG.MIN_REVISIONS}-${CONFIG.MAX_REVISIONS})`}
                />
              </Grid>
              <Grid item xs={12}>
                <TextField
                  label="Notes"
                  fullWidth
                  multiline
                  rows={3}
                  value={chainForm.notes}
                  onChange={(e) => setChainForm({...chainForm, notes: e.target.value})}
                  placeholder="Optional notes about this document chain"
                />
              </Grid>
            </Grid>

            <Box sx={{ mt: 4, display: 'flex', justifyContent: 'flex-end' }}>
              <Button
                type="submit"
                variant="contained"
                size="large"
                disabled={loading}
                startIcon={loading ? <CircularProgress size={20} /> : <AddIcon />}
                sx={{ minWidth: 200 }}
              >
                {loading ? 'Creating...' : 'Create Chain & Start Upload'}
              </Button>
            </Box>
          </form>
        </Paper>
      )}

      {/* STEP 1: Upload Revisions */}
      {currentStep === 1 && createdChain && (
        <Box>
          {/* Chain Info */}
          <Paper sx={{ p: 3, mb: 3, bgcolor: 'primary.light' }}>
            <Typography variant="h6" gutterBottom>
              📋 Chain: {createdChain.document_title}
            </Typography>
            <Grid container spacing={2}>
              <Grid item>
                <Chip label={`Doc #: ${createdChain.document_number}`} />
              </Grid>
              <Grid item>
                <Chip label={`Project: ${createdChain.project_name}`} />
              </Grid>
              <Grid item>
                <Chip label={`Total Revisions: ${chainForm.max_allowed_revisions}`} color="primary" />
              </Grid>
            </Grid>
          </Paper>

          {/* Upload Current Revision */}
          <Paper sx={{ p: 4, mb: 3 }}>
            <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', mb: 3 }}>
              <Typography variant="h5">
                Upload Revision {currentRevisionNumber} of {chainForm.max_allowed_revisions}
              </Typography>
              <Chip 
                label={`Rev ${currentRevisionNumber}`} 
                color="primary" 
                size="large"
                sx={{ fontSize: '1.1rem', py: 3 }}
              />
            </Box>

            <Box sx={{ textAlign: 'center', py: 4 }}>
              <Button
                variant="contained"
                component="label"
                size="large"
                disabled={loading}
                startIcon={loading ? <CircularProgress size={24} /> : <UploadIcon />}
                sx={{ 
                  py: 3, 
                  px: 6, 
                  fontSize: '1.2rem',
                  minWidth: 300
                }}
              >
                {loading ? 'Uploading...' : `📤 Upload CRS Sheet ${currentRevisionNumber}`}
                <input
                  type="file"
                  hidden
                  accept={CONFIG.ACCEPTED_FILE_TYPE}
                  onChange={(e) => {
                    if (e.target.files[0]) {
                      handleUploadRevision(e.target.files[0]);
                    }
                  }}
                  disabled={loading}
                />
              </Button>
              <Typography variant="body2" color="text.secondary" sx={{ mt: 2 }}>
                PDF files only • Comments will be automatically extracted
              </Typography>
            </Box>
          </Paper>

          {/* Uploaded Revisions List */}
          {uploadedRevisions.length > 0 && (
            <Paper sx={{ p: 3 }}>
              <Typography variant="h6" gutterBottom>
                ✅ Uploaded Revisions ({uploadedRevisions.length})
              </Typography>
              <List>
                {uploadedRevisions.map((rev, idx) => (
                  <React.Fragment key={idx}>
                    <ListItem>
                      <CheckIcon color="success" sx={{ mr: 2, fontSize: 32 }} />
                      <ListItemText
                        primary={
                          <Typography variant="h6">
                            {rev.label}: {rev.fileName}
                          </Typography>
                        }
                        secondary={
                          <Box sx={{ mt: 1 }}>
                            <Chip label={`${rev.comments} Total Comments`} size="small" sx={{ mr: 1 }} />
                            <Chip label={`${rev.redComments} Red`} size="small" color="error" sx={{ mr: 1 }} />
                            <Chip label={`${rev.yellowComments} Yellow`} size="small" color="warning" />
                          </Box>
                        }
                      />
                    </ListItem>
                    {idx < uploadedRevisions.length - 1 && <Divider />}
                  </React.Fragment>
                ))}
              </List>
              
              {/* Download Excel Button - Available after first upload */}
              <Box sx={{ mt: 3, pt: 3, borderTop: '1px solid #ddd', textAlign: 'center' }}>
                <Button
                  variant="contained"
                  color="success"
                  size="large"
                  onClick={handleDownloadExcel}
                  startIcon={<DownloadIcon />}
                  sx={{ minWidth: 250, py: 1.5 }}
                >
                  📥 Download Excel Report
                </Button>
                <Typography variant="caption" display="block" color="text.secondary" sx={{ mt: 1 }}>
                  Contains all uploaded revisions and extracted comments
                </Typography>
              </Box>
            </Paper>
          )}
        </Box>
      )}

      {/* STEP 2: Complete */}
      {currentStep === 2 && (
        <Paper sx={{ p: 4, textAlign: 'center' }}>
          <CheckIcon sx={{ fontSize: 80, color: 'success.main', mb: 2 }} />
          <Typography variant="h4" gutterBottom>
            🎉 All Revisions Uploaded!
          </Typography>
          <Typography variant="body1" color="text.secondary" sx={{ mb: 4 }}>
            Successfully uploaded {uploadedRevisions.length} revisions for {createdChain?.document_title}
          </Typography>

          <Grid container spacing={2} sx={{ mb: 4 }}>
            {uploadedRevisions.map((rev, idx) => (
              <Grid item xs={12} md={6} key={idx}>
                <Card variant="outlined">
                  <CardContent>
                    <Typography variant="h6">{rev.label}</Typography>
                    <Typography variant="body2" color="text.secondary" noWrap>
                      {rev.fileName}
                    </Typography>
                    <Box sx={{ mt: 1 }}>
                      <Chip label={`${rev.comments} Comments`} size="small" color="primary" />
                    </Box>
                  </CardContent>
                </Card>
              </Grid>
            ))}
          </Grid>

          <Box sx={{ display: 'flex', gap: 2, justifyContent: 'center' }}>
            <Button
              variant="contained"
              size="large"
              onClick={handleDownloadExcel}
              startIcon={<DownloadIcon />}
              sx={{ minWidth: 200 }}
            >
              Download Excel Report
            </Button>
            <Button
              variant="outlined"
              size="large"
              onClick={handleReset}
              sx={{ minWidth: 200 }}
            >
              Create Another Chain
            </Button>
          </Box>
        </Paper>
      )}
    </Box>
  );
};

export default CRSMultiRevisionWorkflow;
