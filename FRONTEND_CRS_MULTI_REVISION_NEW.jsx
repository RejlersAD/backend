/**
 * CRS Multi-Revision Management - Redesigned
 * Clean, intuitive interface for managing document revisions
 */

import React, { useState, useEffect } from 'react';
import { 
  Box, 
  Button, 
  Card, 
  CardContent, 
  TextField, 
  Typography, 
  Grid, 
  Dialog, 
  DialogTitle, 
  DialogContent, 
  DialogActions,
  Paper,
  Chip,
  IconButton,
  Alert,
  CircularProgress,
  Stepper,
  Step,
  StepLabel,
  List,
  ListItem,
  ListItemText,
  ListItemIcon,
  Divider,
  Tooltip,
  LinearProgress,
  Stack
} from '@mui/material';
import {
  Add as AddIcon,
  UploadFile as UploadIcon,
  Download as DownloadIcon,
  CheckCircle as CheckIcon,
  Error as ErrorIcon,
  CloudUpload as CloudUploadIcon,
  Description as DescriptionIcon,
  Timeline as TimelineIcon,
  Close as CloseIcon,
  InsertDriveFile as FileIcon
} from '@mui/icons-material';

// Soft-coded configuration
const CONFIG = {
  API_BASE_URL: import.meta.env.VITE_API_URL || '/api/v1', // Use Vite's env variable
  TOKEN_KEYS: ['radai_access_token', 'access_token', 'access'], // Try multiple token storage keys
  MAX_REVISIONS: 10,
  DEFAULT_REVISIONS: 5,
  ACCEPTED_FILE_TYPE: '.pdf',
  REVISION_PREFIX: 'Rev' // Can be customized
};

const CRSMultiRevision = () => {
  // State management
  const [chains, setChains] = useState([]);
  const [loading, setLoading] = useState(true);
  const [currentStep, setCurrentStep] = useState(0); // 0: Select chain, 1: Upload files, 2: Review & Submit
  
  // Form states
  const [selectedChain, setSelectedChain] = useState(null);
  const [revisionFiles, setRevisionFiles] = useState([]);
  const [revisionCount, setRevisionCount] = useState(CONFIG.DEFAULT_REVISIONS);
  const [metadata, setMetadata] = useState({
    project_name: '',
    document_number: '',
    contractor: '',
    department: '',
    notes: ''
  });

  // UI states
  const [showCreateDialog, setShowCreateDialog] = useState(false);
  const [uploadProgress, setUploadProgress] = useState(null);
  const [uploadResults, setUploadResults] = useState(null);
  const [error, setError] = useState(null);
  const [processing, setProcessing] = useState(false);

  // New chain form
  const [chainForm, setChainForm] = useState({
    document_title: '',
    document_number: '',
    project_name: '',
    description: ''
  });

  useEffect(() => {
    loadChains();
  }, []);

  // Smart token authentication - tries multiple storage keys
  const getAuthHeaders = () => {
    let token = null;
    for (const key of CONFIG.TOKEN_KEYS) {
      token = localStorage.getItem(key);
      if (token) {
        console.log(`[CRS Multi-Revision] Using token from: ${key}`);
        break;
      }
    }
    
    if (!token) {
      console.warn('[CRS Multi-Revision] No authentication token found in:', CONFIG.TOKEN_KEYS);
      return {};
    }
    
    return {
      'Authorization': `Bearer ${token}`
    };
  };

  const loadChains = async () => {
    try {
      setLoading(true);
      const headers = getAuthHeaders();
      
      if (!headers.Authorization) {
        setError('Authentication required. Please log in.');
        setLoading(false);
        return;
      }
      
      console.log('[CRS Multi-Revision] Loading chains from:', `${CONFIG.API_BASE_URL}/crs/revision-chains/`);
      const response = await fetch(`${CONFIG.API_BASE_URL}/crs/revision-chains/`, {
        headers: headers
      });
      
      if (response.ok) {
        const data = await response.json();
        console.log('[CRS Multi-Revision] Loaded chains:', data);
        setChains(Array.isArray(data) ? data : data.results || []);
      } else {
        const errorData = await response.json().catch(() => ({}));
        console.error('[CRS Multi-Revision] Failed to load chains:', response.status, errorData);
        setError(`Failed to load chains (${response.status}). Please check your authentication.`);
      }
    } catch (error) {
      console.error('[CRS Multi-Revision] Error loading chains:', error);
      setError('Failed to load revision chains');
    } finally {
      setLoading(false);
    }
  };

  const handleCreateChain = async (e) => {
    e.preventDefault();
    setProcessing(true);
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
        setChains([data, ...chains]);
        setShowCreateDialog(false);
        setChainForm({
          document_title: '',
          document_number: '',
          project_name: '',
          description: ''
        });
        setError(null);
      } else {
        const error = await response.json();
        setError(error.detail || 'Failed to create chain');
      }
    } catch (error) {
      console.error('Error creating chain:', error);
      setError('Failed to create chain');
    } finally {
      setProcessing(false);
    }
  };

  const handleSelectChain = (chain) => {
    setSelectedChain(chain);
    setMetadata({
      project_name: chain.project_name || '',
      document_number: chain.document_number || '',
      contractor: '',
      department: '',
      notes: ''
    });
    setCurrentStep(1);
    setError(null);
  };

  const handleFileSelect = (index, file) => {
    const newFiles = [...revisionFiles];
    newFiles[index] = file;
    setRevisionFiles(newFiles);
  };

  const handleRevisionCountChange = (count) => {
    const newCount = Math.min(Math.max(1, count), CONFIG.MAX_REVISIONS);
    setRevisionCount(newCount);
    
    // Adjust files array
    const newFiles = [...revisionFiles];
    while (newFiles.length < newCount) newFiles.push(null);
    while (newFiles.length > newCount) newFiles.pop();
    setRevisionFiles(newFiles);
  };

  const validateFiles = () => {
    const missingCount = revisionFiles.filter(f => !f).length;
    if (missingCount > 0) {
      setError(`Please upload all ${revisionCount} PDF files. ${missingCount} file(s) missing.`);
      return false;
    }
    return true;
  };

  const handleUploadRevisions = async () => {
    if (!validateFiles()) return;

    setProcessing(true);
    setError(null);
    setUploadProgress({ current: 0, total: revisionCount, results: [] });

    try {
      let lastRevisionId = null;
      const results = [];

      for (let i = 0; i < revisionCount; i++) {
        const revisionNumber = i + 1;
        const file = revisionFiles[i];
        const revisionLabel = `${CONFIG.REVISION_PREFIX} ${revisionNumber}`;

        setUploadProgress({ 
          current: revisionNumber, 
          total: revisionCount, 
          results,
          currentLabel: revisionLabel,
          currentFile: file.name
        });

        try {
          const formData = new FormData();
          formData.append('file', file);
          formData.append('revision_label', revisionLabel);
          
          if (lastRevisionId) {
            formData.append('parent_revision_id', lastRevisionId);
          }
          
          // Add metadata
          Object.keys(metadata).forEach(key => {
            if (metadata[key]) {
              formData.append(key, metadata[key]);
            }
          });

          const response = await fetch(
            `${CONFIG.API_BASE_URL}/crs/revision-chains/${selectedChain.id}/upload_and_add_revision/`,
            {
              method: 'POST',
              headers: getAuthHeaders(),
              body: formData
            }
          );

          if (response.ok) {
            const data = await response.json();
            if (data.revision) {
              lastRevisionId = data.revision.id;
            }
            results.push({ 
              success: true, 
              label: revisionLabel, 
              fileName: file.name, 
              data: data 
            });
          } else {
            const error = await response.json();
            results.push({ 
              success: false, 
              label: revisionLabel, 
              fileName: file.name, 
              error: error.error || error.detail || 'Upload failed' 
            });
          }
        } catch (err) {
          results.push({ 
            success: false, 
            label: revisionLabel, 
            fileName: file.name, 
            error: err.message 
          });
        }
      }

      setUploadProgress({ ...uploadProgress, results, completed: true });
      setUploadResults(results);
      setCurrentStep(2);

      // Refresh chains
      await loadChains();

    } catch (error) {
      console.error('Upload error:', error);
      setError('Upload failed: ' + error.message);
    } finally {
      setProcessing(false);
    }
  };

  const handleDownloadExcel = async (chainId) => {
    try {
      const response = await fetch(
        `${CONFIG.API_BASE_URL}/crs/revision-chains/${chainId}/export_excel/`,
        { headers: getAuthHeaders() }
      );

      if (response.ok) {
        const blob = await response.blob();
        const url = window.URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `CRS_Chain_${chainId}_Export.xlsx`;
        document.body.appendChild(a);
        a.click();
        window.URL.revokeObjectURL(url);
        document.body.removeChild(a);
      } else {
        setError('Failed to download Excel');
      }
    } catch (error) {
      console.error('Error downloading Excel:', error);
      setError('Failed to download Excel');
    }
  };

  const resetUpload = () => {
    setCurrentStep(0);
    setSelectedChain(null);
    setRevisionFiles([]);
    setRevisionCount(CONFIG.DEFAULT_REVISIONS);
    setUploadProgress(null);
    setUploadResults(null);
    setError(null);
  };

  // Render functions
  const renderChainSelection = () => (
    <Box>
      <Typography variant="h6" gutterBottom>
        Step 1: Select Revision Chain
      </Typography>
      <Typography variant="body2" color="text.secondary" sx={{ mb: 3 }}>
        Choose a chain to upload multiple revisions, or create a new one.
      </Typography>

      <Grid container spacing={2}>
        {chains.map((chain) => (
          <Grid item xs={12} md={6} key={chain.id}>
            <Card 
              sx={{ 
                cursor: 'pointer',
                transition: 'all 0.2s',
                '&:hover': { 
                  boxShadow: 6,
                  borderColor: 'primary.main',
                  borderWidth: 2,
                  borderStyle: 'solid'
                }
              }}
              onClick={() => handleSelectChain(chain)}
            >
              <CardContent>
                <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'start' }}>
                  <Box sx={{ flex: 1 }}>
                    <Typography variant="h6" gutterBottom>
                      {chain.document_title || 'Untitled Chain'}
                    </Typography>
                    <Typography variant="body2" color="text.secondary">
                      Doc #: {chain.document_number || 'N/A'}
                    </Typography>
                    <Typography variant="body2" color="text.secondary">
                      Project: {chain.project_name || 'N/A'}
                    </Typography>
                    <Box sx={{ mt: 2 }}>
                      <Chip 
                        label={`${chain.revisions?.length || 0} Revisions`} 
                        size="small" 
                        color="primary"
                        sx={{ mr: 1 }}
                      />
                      <Chip 
                        label={`${chain.total_comments || 0} Comments`} 
                        size="small" 
                        variant="outlined"
                      />
                    </Box>
                  </Box>
                  <IconButton 
                    size="small"
                    onClick={(e) => {
                      e.stopPropagation();
                      handleDownloadExcel(chain.id);
                    }}
                  >
                    <DownloadIcon />
                  </IconButton>
                </Box>
              </CardContent>
            </Card>
          </Grid>
        ))}
      </Grid>

      <Button
        variant="outlined"
        startIcon={<AddIcon />}
        onClick={() => setShowCreateDialog(true)}
        sx={{ mt: 3 }}
        fullWidth
      >
        Create New Revision Chain
      </Button>
    </Box>
  );

  const renderFileUpload = () => (
    <Box>
      <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', mb: 3 }}>
        <Box>
          <Typography variant="h6">
            Step 2: Upload PDF Files
          </Typography>
          <Typography variant="body2" color="text.secondary">
            Selected Chain: <strong>{selectedChain?.document_title}</strong>
          </Typography>
        </Box>
        <Button size="small" onClick={resetUpload}>
          Change Chain
        </Button>
      </Box>

      {/* Number of Revisions */}
      <Paper sx={{ p: 2, mb: 3, bgcolor: 'background.default' }}>
        <TextField
          label="Number of Revisions to Upload"
          type="number"
          value={revisionCount}
          onChange={(e) => handleRevisionCountChange(parseInt(e.target.value) || 1)}
          inputProps={{ min: 1, max: CONFIG.MAX_REVISIONS }}
          fullWidth
          helperText={`Upload between 1 and ${CONFIG.MAX_REVISIONS} revisions. Each revision will be auto-linked.`}
        />
      </Paper>

      {/* File Upload Sections */}
      <Grid container spacing={2} sx={{ mb: 3 }}>
        {Array.from({ length: revisionCount }).map((_, idx) => {
          const file = revisionFiles[idx];
          return (
            <Grid item xs={12} md={6} key={idx}>
              <Paper 
                sx={{ 
                  p: 2, 
                  bgcolor: file ? 'success.50' : 'background.default',
                  border: file ? '2px solid' : '1px solid',
                  borderColor: file ? 'success.main' : 'divider'
                }}
              >
                <Box sx={{ display: 'flex', alignItems: 'center', mb: 1 }}>
                  {file ? (
                    <CheckIcon color="success" sx={{ mr: 1 }} />
                  ) : (
                    <FileIcon color="action" sx={{ mr: 1 }} />
                  )}
                  <Typography variant="subtitle2">
                    Revision {idx + 1} ({CONFIG.REVISION_PREFIX} {idx + 1})
                  </Typography>
                </Box>
                
                <Button
                  variant={file ? "outlined" : "contained"}
                  component="label"
                  fullWidth
                  startIcon={<UploadIcon />}
                  color={file ? "success" : "primary"}
                >
                  {file ? `✓ ${file.name}` : 'Upload PDF File *'}
                  <input
                    type="file"
                    hidden
                    accept={CONFIG.ACCEPTED_FILE_TYPE}
                    onChange={(e) => {
                      const selectedFile = e.target.files[0];
                      if (selectedFile) handleFileSelect(idx, selectedFile);
                    }}
                  />
                </Button>

                {file && (
                  <Box sx={{ mt: 1, display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                    <Typography variant="caption" color="text.secondary">
                      {(file.size / 1024 / 1024).toFixed(2)} MB
                    </Typography>
                    <IconButton 
                      size="small" 
                      onClick={() => handleFileSelect(idx, null)}
                    >
                      <CloseIcon fontSize="small" />
                    </IconButton>
                  </Box>
                )}
              </Paper>
            </Grid>
          );
        })}
      </Grid>

      {/* Optional Metadata */}
      <Paper sx={{ p: 2, mb: 3 }}>
        <Typography variant="subtitle2" gutterBottom>
          Optional Metadata (Applied to all revisions)
        </Typography>
        <Grid container spacing={2} sx={{ mt: 1 }}>
          <Grid item xs={12} md={6}>
            <TextField
              label="Project Name"
              fullWidth
              value={metadata.project_name}
              onChange={(e) => setMetadata({...metadata, project_name: e.target.value})}
            />
          </Grid>
          <Grid item xs={12} md={6}>
            <TextField
              label="Document Number"
              fullWidth
              value={metadata.document_number}
              onChange={(e) => setMetadata({...metadata, document_number: e.target.value})}
            />
          </Grid>
          <Grid item xs={12} md={6}>
            <TextField
              label="Contractor"
              fullWidth
              value={metadata.contractor}
              onChange={(e) => setMetadata({...metadata, contractor: e.target.value})}
            />
          </Grid>
          <Grid item xs={12} md={6}>
            <TextField
              label="Department"
              fullWidth
              value={metadata.department}
              onChange={(e) => setMetadata({...metadata, department: e.target.value})}
            />
          </Grid>
          <Grid item xs={12}>
            <TextField
              label="Notes"
              fullWidth
              multiline
              rows={2}
              value={metadata.notes}
              onChange={(e) => setMetadata({...metadata, notes: e.target.value})}
            />
          </Grid>
        </Grid>
      </Paper>

      {/* Action Buttons */}
      <Box sx={{ display: 'flex', gap: 2 }}>
        <Button
          variant="outlined"
          onClick={resetUpload}
          fullWidth
        >
          Cancel
        </Button>
        <Button
          variant="contained"
          onClick={handleUploadRevisions}
          disabled={processing || revisionFiles.filter(f => f).length !== revisionCount}
          startIcon={processing ? <CircularProgress size={20} /> : <CloudUploadIcon />}
          fullWidth
        >
          {processing ? 'Uploading...' : `Upload ${revisionCount} Revisions`}
        </Button>
      </Box>
    </Box>
  );

  const renderResults = () => (
    <Box>
      <Typography variant="h6" gutterBottom>
        Upload Complete!
      </Typography>
      <Typography variant="body2" color="text.secondary" sx={{ mb: 3 }}>
        {uploadResults?.filter(r => r.success).length || 0} of {revisionCount} revisions uploaded successfully.
      </Typography>

      <List>
        {uploadResults?.map((result, idx) => (
          <React.Fragment key={idx}>
            <ListItem>
              <ListItemIcon>
                {result.success ? (
                  <CheckIcon color="success" />
                ) : (
                  <ErrorIcon color="error" />
                )}
              </ListItemIcon>
              <ListItemText
                primary={`${result.label}: ${result.fileName}`}
                secondary={result.success 
                  ? `✓ Extracted ${result.data?.extraction_summary?.total_comments || 0} comments`
                  : `✗ ${result.error}`}
              />
            </ListItem>
            {idx < uploadResults.length - 1 && <Divider />}
          </React.Fragment>
        ))}
      </List>

      <Box sx={{ mt: 3, display: 'flex', gap: 2 }}>
        <Button
          variant="outlined"
          onClick={resetUpload}
          fullWidth
        >
          Upload More Revisions
        </Button>
        <Button
          variant="contained"
          onClick={() => handleDownloadExcel(selectedChain.id)}
          startIcon={<DownloadIcon />}
          fullWidth
        >
          Download Excel Report
        </Button>
      </Box>
    </Box>
  );

  return (
    <Box sx={{ p: 3, maxWidth: 1400, mx: 'auto' }}>
      {/* Header */}
      <Box sx={{ mb: 4 }}>
        <Typography variant="h4" gutterBottom>
          CRS Multi-Revision Upload
        </Typography>
        <Typography variant="body1" color="text.secondary">
          Upload multiple PDF revisions to a chain. Each file will be automatically processed and linked.
        </Typography>
      </Box>

      {/* Error Alert */}
      {error && (
        <Alert severity="error" sx={{ mb: 3 }} onClose={() => setError(null)}>
          {error}
        </Alert>
      )}

      {/* Progress */}
      {uploadProgress && !uploadProgress.completed && (
        <Alert severity="info" sx={{ mb: 3 }}>
          <Typography variant="body2" gutterBottom>
            Uploading {uploadProgress.currentLabel}... ({uploadProgress.current}/{uploadProgress.total})
          </Typography>
          <Typography variant="caption" display="block" gutterBottom>
            File: {uploadProgress.currentFile}
          </Typography>
          <LinearProgress 
            variant="determinate" 
            value={(uploadProgress.current / uploadProgress.total) * 100} 
            sx={{ mt: 1 }}
          />
        </Alert>
      )}

      {/* Stepper */}
      <Stepper activeStep={currentStep} sx={{ mb: 4 }}>
        <Step>
          <StepLabel>Select Chain</StepLabel>
        </Step>
        <Step>
          <StepLabel>Upload Files</StepLabel>
        </Step>
        <Step>
          <StepLabel>Review Results</StepLabel>
        </Step>
      </Stepper>

      {/* Main Content */}
      <Paper sx={{ p: 3 }}>
        {loading ? (
          <Box sx={{ display: 'flex', justifyContent: 'center', p: 5 }}>
            <CircularProgress />
          </Box>
        ) : (
          <>
            {currentStep === 0 && renderChainSelection()}
            {currentStep === 1 && renderFileUpload()}
            {currentStep === 2 && renderResults()}
          </>
        )}
      </Paper>

      {/* Create Chain Dialog */}
      <Dialog 
        open={showCreateDialog} 
        onClose={() => !processing && setShowCreateDialog(false)}
        maxWidth="md"
        fullWidth
      >
        <DialogTitle>Create New Revision Chain</DialogTitle>
        <form onSubmit={handleCreateChain}>
          <DialogContent>
            <Grid container spacing={2}>
              <Grid item xs={12}>
                <TextField
                  label="Document Title *"
                  fullWidth
                  required
                  value={chainForm.document_title}
                  onChange={(e) => setChainForm({...chainForm, document_title: e.target.value})}
                />
              </Grid>
              <Grid item xs={12} md={6}>
                <TextField
                  label="Project Name"
                  fullWidth
                  value={chainForm.project_name}
                  onChange={(e) => setChainForm({...chainForm, project_name: e.target.value})}
                />
              </Grid>
              <Grid item xs={12} md={6}>
                <TextField
                  label="Document Number"
                  fullWidth
                  value={chainForm.document_number}
                  onChange={(e) => setChainForm({...chainForm, document_number: e.target.value})}
                />
              </Grid>
              <Grid item xs={12}>
                <TextField
                  label="Description"
                  fullWidth
                  multiline
                  rows={3}
                  value={chainForm.description}
                  onChange={(e) => setChainForm({...chainForm, description: e.target.value})}
                />
              </Grid>
            </Grid>
          </DialogContent>
          <DialogActions>
            <Button onClick={() => setShowCreateDialog(false)} disabled={processing}>
              Cancel
            </Button>
            <Button 
              type="submit" 
              variant="contained" 
              disabled={processing}
              startIcon={processing && <CircularProgress size={20} />}
            >
              {processing ? 'Creating...' : 'Create Chain'}
            </Button>
          </DialogActions>
        </form>
      </Dialog>
    </Box>
  );
};

export default CRSMultiRevision;
