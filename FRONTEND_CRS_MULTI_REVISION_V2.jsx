/**
 * CRS Multi-Revision Management Component
 * Allows creating chains and uploading multiple revisions with automatic extraction
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
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Paper,
  Chip,
  IconButton,
  Alert,
  CircularProgress,
  Tabs,
  Tab,
  Accordion,
  AccordionSummary,
  AccordionDetails,
  List,
  ListItem,
  ListItemText,
  Divider,
  Tooltip
} from '@mui/material';
import {
  Add as AddIcon,
  UploadFile as UploadIcon,
  Download as DownloadIcon,
  Visibility as ViewIcon,
  ExpandMore as ExpandMoreIcon,
  Description as DescriptionIcon,
  Timeline as TimelineIcon,
  Link as LinkIcon
} from '@mui/icons-material';

const API_BASE_URL = 'http://localhost:8000/api/v1';

const CRSMultiRevision = () => {
  const [chains, setChains] = useState([]);
  const [loading, setLoading] = useState(true);
  const [showCreateDialog, setShowCreateDialog] = useState(false);
  const [showUploadDialog, setShowUploadDialog] = useState(false);
  const [selectedChain, setSelectedChain] = useState(null);
  const [tabValue, setTabValue] = useState(0);

  // Form states
  const [chainForm, setChainForm] = useState({
    document_title: '',
    document_number: '',
    project_name: '',
    description: ''
  });

  const [uploadForm, setUploadForm] = useState({
    file: null,
    revision_label: '',
    parent_revision_id: '',
    notes: '',
    project_name: '',
    document_number: '',
    contractor: '',
    department: '',
    auto_sequence: false,  // Enable auto-sequencing
    target_revision_count: 1  // How many revisions to upload
  });

  const [uploadResult, setUploadResult] = useState(null);
  const [processing, setProcessing] = useState(false);
  const [error, setError] = useState(null);
  const [batchProgress, setBatchProgress] = useState(null);  // Track batch upload progress

  useEffect(() => {
    loadChains();
  }, []);

  const getAuthHeaders = () => {
    // Try multiple token storage keys (soft coding)
    const token = localStorage.getItem('radai_access_token') || 
                  localStorage.getItem('access_token') || 
                  localStorage.getItem('access');
    
    if (!token) {
      console.warn('No authentication token found');
    }
    
    return {
      'Authorization': `Bearer ${token}`
    };
  };

  const loadChains = async () => {
    try {
      setLoading(true);
      const response = await fetch(`${API_BASE_URL}/crs/revision-chains/`, {
        headers: getAuthHeaders()
      });
      
      if (response.ok) {
        const data = await response.json();
        setChains(Array.isArray(data) ? data : data.results || []);
      }
    } catch (error) {
      console.error('Error loading chains:', error);
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
      const response = await fetch(`${API_BASE_URL}/crs/revision-chains/`, {
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
        alert('Revision chain created successfully!');
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

  const handleUploadRevision = async (e) => {
    e.preventDefault();
    
    if (!uploadForm.file) {
      setError('Please select a PDF file');
      return;
    }

    if (!uploadForm.revision_label && !uploadForm.auto_sequence) {
      setError('Please enter a revision label or enable auto-sequence');
      return;
    }

    // Check if batch upload is requested
    if (uploadForm.auto_sequence && uploadForm.target_revision_count > 1) {
      await handleBatchUpload();
      return;
    }

    // Single upload
    await uploadSingleRevision(uploadForm.file, uploadForm.revision_label, uploadForm.parent_revision_id);
  };

  // Smart batch upload: automatically creates sequential revisions
  const handleBatchUpload = async () => {
    setProcessing(true);
    setError(null);
    setUploadResult(null);
    setBatchProgress({ current: 0, total: uploadForm.target_revision_count, results: [] });

    try {
      let lastRevisionId = uploadForm.parent_revision_id || null;
      const results = [];

      for (let i = 1; i <= uploadForm.target_revision_count; i++) {
        // Generate smart revision label (Rev 1, Rev 2, etc.)
        const revisionLabel = uploadForm.revision_label 
          ? `${uploadForm.revision_label} ${i}` 
          : `Rev ${i}`;

        setBatchProgress({ 
          current: i, 
          total: uploadForm.target_revision_count, 
          results,
          currentLabel: revisionLabel 
        });

        try {
          const result = await uploadSingleRevision(
            uploadForm.file, 
            revisionLabel, 
            lastRevisionId,
            `Batch upload ${i}/${uploadForm.target_revision_count}`
          );

          if (result && result.revision) {
            lastRevisionId = result.revision.id;  // Link next revision to this one
            results.push({ success: true, label: revisionLabel, data: result });
          } else {
            results.push({ success: false, label: revisionLabel, error: 'No revision data returned' });
          }
        } catch (err) {
          results.push({ success: false, label: revisionLabel, error: err.message });
        }
      }

      setBatchProgress({ ...batchProgress, results, completed: true });
      setUploadResult({ 
        batch: true, 
        results, 
        message: `Successfully uploaded ${results.filter(r => r.success).length}/${uploadForm.target_revision_count} revisions` 
      });

      // Refresh chain data
      await loadChainDetails(selectedChain.id);

    } catch (error) {
      console.error('Batch upload error:', error);
      setError('Batch upload failed: ' + error.message);
    } finally {
      setProcessing(false);
    }
  };

  // Single revision upload helper
  const uploadSingleRevision = async (file, revisionLabel, parentRevisionId = null, additionalNotes = '') => {
    const formData = new FormData();
    formData.append('file', file);
    formData.append('revision_label', revisionLabel);
    
    if (parentRevisionId) {
      formData.append('parent_revision_id', parentRevisionId);
    }
    
    const notes = [uploadForm.notes, additionalNotes].filter(Boolean).join('; ');
    if (notes) {
      formData.append('notes', notes);
    }
    if (uploadForm.project_name) {
      formData.append('project_name', uploadForm.project_name);
    }
    if (uploadForm.document_number) {
      formData.append('document_number', uploadForm.document_number);
    }
    if (uploadForm.contractor) {
      formData.append('contractor', uploadForm.contractor);
    }
    if (uploadForm.department) {
      formData.append('department', uploadForm.department);
    }

    const response = await fetch(
      `${API_BASE_URL}/crs/revision-chains/${selectedChain.id}/upload_and_add_revision/`,
      {
        method: 'POST',
        headers: getAuthHeaders(),
        body: formData
      }
    );

    if (!response.ok) {
      const error = await response.json();
      throw new Error(error.error || error.detail || 'Upload failed');
    }

    const data = await response.json();
    
    // If single upload (not batch), show result and reset form
    if (!uploadForm.auto_sequence || uploadForm.target_revision_count === 1) {
      setUploadResult(data);
      
      // Refresh chain data
      await loadChainDetails(selectedChain.id);
      
      // Reset form
      setUploadForm({
        file: null,
        revision_label: '',
        parent_revision_id: '',
        notes: '',
        project_name: uploadForm.project_name,
        document_number: uploadForm.document_number,
        contractor: '',
        department: '',
        auto_sequence: false,
        target_revision_count: 1
      });
      
      // Reset file input
      const fileInput = document.getElementById('upload-file-input');
      if (fileInput) fileInput.value = '';
    }

    return data;
  };

  const loadChainDetails = async (chainId) => {
    try {
      const response = await fetch(`${API_BASE_URL}/crs/revision-chains/${chainId}/`, {
        headers: getAuthHeaders()
      });
      
      if (response.ok) {
        const data = await response.json();
        setSelectedChain(data);
        
        // Update in chains list
        setChains(chains.map(c => c.id === chainId ? data : c));
      }
    } catch (error) {
      console.error('Error loading chain details:', error);
    }
  };

  const handleDownloadExcel = async (chainId) => {
    try {
      const response = await fetch(
        `${API_BASE_URL}/crs/revision-chains/${chainId}/export_excel/`,
        {
          headers: getAuthHeaders()
        }
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
      }
    } catch (error) {
      console.error('Error downloading Excel:', error);
      setError('Failed to download Excel');
    }
  };

  const openUploadDialog = (chain) => {
    setSelectedChain(chain);
    setUploadForm({
      ...uploadForm,
      project_name: chain.project_name,
      document_number: chain.document_number
    });
    setShowUploadDialog(true);
    setUploadResult(null);
    setError(null);
  };

  const getStatusColor = (status) => {
    const colors = {
      'active': 'success',
      'completed': 'info',
      'on_hold': 'warning',
      'cancelled': 'error'
    };
    return colors[status] || 'default';
  };

  const getRiskColor = (risk) => {
    const colors = {
      'low': 'success',
      'medium': 'warning',
      'high': 'error',
      'critical': 'error'
    };
    return colors[risk] || 'default';
  };

  return (
    <Box sx={{ p: 3 }}>
      {/* Header */}
      <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', mb: 3 }}>
        <Typography variant="h4">
          CRS Multi-Revision Management
        </Typography>
        <Button
          variant="contained"
          startIcon={<AddIcon />}
          onClick={() => setShowCreateDialog(true)}
        >
          Create Revision Chain
        </Button>
      </Box>

      {/* Error Alert */}
      {error && (
        <Alert severity="error" sx={{ mb: 2 }} onClose={() => setError(null)}>
          {error}
        </Alert>
      )}

      {/* Tabs */}
      <Tabs value={tabValue} onChange={(e, newValue) => setTabValue(newValue)} sx={{ mb: 2 }}>
        <Tab label="Revision Chains" />
        <Tab label="Statistics" />
      </Tabs>

      {/* Tab Panels */}
      {tabValue === 0 && (
        <Grid container spacing={3}>
          {loading ? (
            <Box sx={{ display: 'flex', justifyContent: 'center', width: '100%', p: 5 }}>
              <CircularProgress />
            </Box>
          ) : chains.length === 0 ? (
            <Grid item xs={12}>
              <Card>
                <CardContent sx={{ textAlign: 'center', py: 5 }}>
                  <DescriptionIcon sx={{ fontSize: 60, color: 'text.secondary', mb: 2 }} />
                  <Typography variant="h6" color="text.secondary">
                    No Revision Chains Yet
                  </Typography>
                  <Typography variant="body2" color="text.secondary" sx={{ mt: 1 }}>
                    Create your first revision chain to start tracking document revisions
                  </Typography>
                  <Button
                    variant="contained"
                    startIcon={<AddIcon />}
                    onClick={() => setShowCreateDialog(true)}
                    sx={{ mt: 2 }}
                  >
                    Create Chain
                  </Button>
                </CardContent>
              </Card>
            </Grid>
          ) : (
            chains.map((chain) => (
              <Grid item xs={12} md={6} lg={4} key={chain.id}>
                <Card>
                  <CardContent>
                    <Box sx={{ display: 'flex', justifyContent: 'space-between', mb: 2 }}>
                      <Typography variant="h6" noWrap>
                        {chain.document_title}
                      </Typography>
                      <Chip 
                        label={chain.status} 
                        color={getStatusColor(chain.status)} 
                        size="small" 
                      />
                    </Box>
                    
                    <Typography variant="body2" color="text.secondary" gutterBottom>
                      <strong>Document #:</strong> {chain.document_number}
                    </Typography>
                    <Typography variant="body2" color="text.secondary" gutterBottom>
                      <strong>Project:</strong> {chain.project_name}
                    </Typography>
                    
                    <Divider sx={{ my: 2 }} />
                    
                    <Grid container spacing={2}>
                      <Grid item xs={6}>
                        <Typography variant="caption" color="text.secondary">
                          Revisions
                        </Typography>
                        <Typography variant="h6">
                          {chain.total_revisions}
                        </Typography>
                      </Grid>
                      <Grid item xs={6}>
                        <Typography variant="caption" color="text.secondary">
                          Total Comments
                        </Typography>
                        <Typography variant="h6">
                          {chain.total_comments_across_revisions || 0}
                        </Typography>
                      </Grid>
                      {chain.risk_level && (
                        <Grid item xs={12}>
                          <Chip 
                            label={`Risk: ${chain.risk_level}`} 
                            color={getRiskColor(chain.risk_level)} 
                            size="small"
                            sx={{ mt: 1 }}
                          />
                        </Grid>
                      )}
                    </Grid>
                    
                    <Box sx={{ mt: 2, display: 'flex', gap: 1 }}>
                      <Button
                        variant="contained"
                        startIcon={<UploadIcon />}
                        onClick={() => openUploadDialog(chain)}
                        fullWidth
                      >
                        Upload Revision
                      </Button>
                      <Tooltip title="Download Excel">
                        <IconButton
                          color="primary"
                          onClick={() => handleDownloadExcel(chain.id)}
                        >
                          <DownloadIcon />
                        </IconButton>
                      </Tooltip>
                    </Box>
                    
                    {/* Revisions List */}
                    {chain.revisions && chain.revisions.length > 0 && (
                      <Accordion sx={{ mt: 2 }}>
                        <AccordionSummary expandIcon={<ExpandMoreIcon />}>
                          <Typography variant="body2">
                            View Revisions ({chain.revisions.length})
                          </Typography>
                        </AccordionSummary>
                        <AccordionDetails>
                          <List dense>
                            {chain.revisions.map((rev) => (
                              <ListItem key={rev.id}>
                                <ListItemText
                                  primary={rev.revision_label}
                                  secondary={`${rev.total_comments} comments • ${rev.status}`}
                                />
                              </ListItem>
                            ))}
                          </List>
                        </AccordionDetails>
                      </Accordion>
                    )}
                  </CardContent>
                </Card>
              </Grid>
            ))
          )}
        </Grid>
      )}

      {tabValue === 1 && (
        <Card>
          <CardContent>
            <Typography variant="h6">Statistics Coming Soon</Typography>
            <Typography variant="body2" color="text.secondary">
              Dashboard statistics will be available here
            </Typography>
          </CardContent>
        </Card>
      )}

      {/* Create Chain Dialog */}
      <Dialog 
        open={showCreateDialog} 
        onClose={() => !processing && setShowCreateDialog(false)}
        maxWidth="sm"
        fullWidth
      >
        <DialogTitle>Create Revision Chain</DialogTitle>
        <form onSubmit={handleCreateChain}>
          <DialogContent>
            <TextField
              label="Document Title"
              fullWidth
              required
              value={chainForm.document_title}
              onChange={(e) => setChainForm({...chainForm, document_title: e.target.value})}
              sx={{ mb: 2 }}
            />
            <TextField
              label="Document Number"
              fullWidth
              required
              value={chainForm.document_number}
              onChange={(e) => setChainForm({...chainForm, document_number: e.target.value})}
              sx={{ mb: 2 }}
            />
            <TextField
              label="Project Name"
              fullWidth
              required
              value={chainForm.project_name}
              onChange={(e) => setChainForm({...chainForm, project_name: e.target.value})}
              sx={{ mb: 2 }}
            />
            <TextField
              label="Description"
              fullWidth
              multiline
              rows={3}
              value={chainForm.description}
              onChange={(e) => setChainForm({...chainForm, description: e.target.value})}
            />
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

      {/* Upload Revision Dialog */}
      <Dialog 
        open={showUploadDialog} 
        onClose={() => !processing && setShowUploadDialog(false)}
        maxWidth="md"
        fullWidth
      >
        <DialogTitle>
          Upload Revision to {selectedChain?.document_title}
        </DialogTitle>
        <form onSubmit={handleUploadRevision}>
          <DialogContent>
            {uploadResult && (
              <Alert severity="success" sx={{ mb: 2 }}>
                <Typography variant="subtitle2" gutterBottom>
                  {uploadResult.message}
                </Typography>
                <Typography variant="body2">
                  • Total Comments: {uploadResult.data.extraction_summary.total_comments}
                </Typography>
                <Typography variant="body2">
                  • Red Comments: {uploadResult.data.extraction_summary.red_comments}
                </Typography>
                <Typography variant="body2">
                  • Yellow Boxes: {uploadResult.data.extraction_summary.yellow_boxes}
                </Typography>
                <Typography variant="body2">
                  • Pages: {uploadResult.data.extraction_summary.pages_with_comments}
                </Typography>
              </Alert>
            )}

            <Grid container spacing={2}>
              <Grid item xs={12}>
                <Button
                  variant="outlined"
                  component="label"
                  fullWidth
                  startIcon={<UploadIcon />}
                >
                  {uploadForm.file ? uploadForm.file.name : 'Select PDF File *'}
                  <input
                    id="upload-file-input"
                    type="file"
                    hidden
                    accept=".pdf"
                    onChange={(e) => {
                      const file = e.target.files[0];
                      setUploadForm({...uploadForm, file});
                    }}
                  />
                </Button>
              </Grid>

              <Grid item xs={12} md={6}>
                <TextField
                  label="Revision Label *"
                  fullWidth
                  required
                  value={uploadForm.revision_label}
                  onChange={(e) => setUploadForm({...uploadForm, revision_label: e.target.value})}
                  placeholder="e.g., Rev 0, Rev 1, Rev A"
                  helperText="Enter revision identifier"
                />
              </Grid>

              <Grid item xs={12} md={6}>
                <TextField
                  label="Parent Revision ID"
                  fullWidth
                  type="number"
                  value={uploadForm.parent_revision_id}
                  onChange={(e) => setUploadForm({...uploadForm, parent_revision_id: e.target.value})}
                  helperText="Leave empty for first revision"
                />
              </Grid>

              <Grid item xs={12} md={6}>
                <TextField
                  label="Project Name"
                  fullWidth
                  value={uploadForm.project_name}
                  onChange={(e) => setUploadForm({...uploadForm, project_name: e.target.value})}
                />
              </Grid>

              <Grid item xs={12} md={6}>
                <TextField
                  label="Document Number"
                  fullWidth
                  value={uploadForm.document_number}
                  onChange={(e) => setUploadForm({...uploadForm, document_number: e.target.value})}
                />
              </Grid>

              <Grid item xs={12} md={6}>
                <TextField
                  label="Contractor"
                  fullWidth
                  value={uploadForm.contractor}
                  onChange={(e) => setUploadForm({...uploadForm, contractor: e.target.value})}
                />
              </Grid>

              <Grid item xs={12} md={6}>
                <TextField
                  label="Department"
                  fullWidth
                  value={uploadForm.department}
                  onChange={(e) => setUploadForm({...uploadForm, department: e.target.value})}
                />
              </Grid>

              <Grid item xs={12}>
                <TextField
                  label="Notes"
                  fullWidth
                  multiline
                  rows={3}
                  value={uploadForm.notes}
                  onChange={(e) => setUploadForm({...uploadForm, notes: e.target.value})}
                  placeholder="Enter any notes about this revision..."
                />
              </Grid>
            </Grid>

            <Alert severity="info" sx={{ mt: 2 }}>
              <Typography variant="body2">
                <strong>What happens next:</strong>
              </Typography>
              <Typography variant="body2">
                • PDF will be uploaded and processed automatically
              </Typography>
              <Typography variant="body2">
                • Red text and yellow box comments will be extracted
              </Typography>
              <Typography variant="body2">
                • Comments will be linked to previous revision (if specified)
              </Typography>
              <Typography variant="body2">
                • You'll receive an immediate extraction summary
              </Typography>
            </Alert>
          </DialogContent>
          <DialogActions>
            <Button onClick={() => setShowUploadDialog(false)} disabled={processing}>
              {uploadResult ? 'Close' : 'Cancel'}
            </Button>
            {!uploadResult && (
              <Button 
                type="submit" 
                variant="contained" 
                disabled={processing || !uploadForm.file || !uploadForm.revision_label}
                startIcon={processing && <CircularProgress size={20} />}
              >
                {processing ? 'Uploading & Extracting...' : 'Upload & Extract'}
              </Button>
            )}
          </DialogActions>
        </form>
      </Dialog>
    </Box>
  );
};

export default CRSMultiRevision;

