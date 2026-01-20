/**
 * CRS Multi-Revision Upload - SIMPLIFIED
 * Large, obvious upload buttons for each revision
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
  Alert,
  Paper,
  Chip,
  CircularProgress,
  Stack,
  Divider
} from '@mui/material';
import {
  CloudUpload as UploadIcon,
  CheckCircle as CheckIcon,
  Download as DownloadIcon,
  Refresh as RefreshIcon
} from '@mui/icons-material';

const CRSMultiRevisionSimple = () => {
  const [chains, setChains] = useState([]);
  const [selectedChain, setSelectedChain] = useState(null);
  const [revisionCount, setRevisionCount] = useState(3);
  const [files, setFiles] = useState({});
  const [loading, setLoading] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState(null);
  const [success, setSuccess] = useState(null);

  const API_BASE = '/api/v1';

  // Get auth token
  const getToken = () => {
    return localStorage.getItem('radai_access_token') || 
           localStorage.getItem('access_token') || 
           localStorage.getItem('access');
  };

  const getHeaders = () => ({
    'Authorization': `Bearer ${getToken()}`
  });

  // Load chains on mount
  useEffect(() => {
    loadChains();
  }, []);

  const loadChains = async () => {
    try {
      setLoading(true);
      const response = await fetch(`${API_BASE}/crs/revision-chains/`, {
        headers: getHeaders()
      });
      if (response.ok) {
        const data = await response.json();
        setChains(Array.isArray(data) ? data : data.results || []);
      }
    } catch (err) {
      console.error('Error loading chains:', err);
    } finally {
      setLoading(false);
    }
  };

  const handleFileSelect = (revNumber, file) => {
    setFiles({...files, [revNumber]: file});
    setError(null);
  };

  const handleUpload = async () => {
    if (!selectedChain) {
      setError('Please select a chain first');
      return;
    }

    const fileCount = Object.keys(files).length;
    if (fileCount === 0) {
      setError('Please upload at least one file');
      return;
    }

    setUploading(true);
    setError(null);
    setSuccess(null);

    try {
      let successCount = 0;
      let lastRevisionId = null;

      // Upload each file in sequence
      for (let i = 1; i <= revisionCount; i++) {
        const file = files[i];
        if (!file) continue;

        const formData = new FormData();
        formData.append('file', file);
        formData.append('revision_label', `Rev ${i}`);
        if (lastRevisionId) {
          formData.append('parent_revision_id', lastRevisionId);
        }

        const response = await fetch(
          `${API_BASE}/crs/revision-chains/${selectedChain.id}/upload_and_add_revision/`,
          {
            method: 'POST',
            headers: getHeaders(),
            body: formData
          }
        );

        if (response.ok) {
          const data = await response.json();
          lastRevisionId = data.revision.id;
          successCount++;
        } else {
          console.error(`Failed to upload Rev ${i}`);
        }
      }

      setSuccess(`Successfully uploaded ${successCount} of ${fileCount} revisions!`);
      setFiles({});
      loadChains();
    } catch (err) {
      setError('Upload failed: ' + err.message);
    } finally {
      setUploading(false);
    }
  };

  const handleDownloadExcel = async () => {
    if (!selectedChain) return;
    
    try {
      const response = await fetch(
        `${API_BASE}/crs/revision-chains/${selectedChain.id}/export_excel/`,
        { headers: getHeaders() }
      );
      
      if (response.ok) {
        const blob = await response.blob();
        const url = window.URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `CRS_Chain_${selectedChain.chain_id}_Export.xlsx`;
        a.click();
      }
    } catch (err) {
      setError('Failed to download Excel: ' + err.message);
    }
  };

  return (
    <Box sx={{ p: 3 }}>
      <Typography variant="h4" gutterBottom>
        📄 CRS Multi-Revision Upload
      </Typography>
      <Typography variant="body2" color="text.secondary" gutterBottom sx={{ mb: 3 }}>
        Upload multiple PDF revisions with individual upload buttons
      </Typography>

      {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}
      {success && <Alert severity="success" sx={{ mb: 2 }}>{success}</Alert>}

      {/* STEP 1: SELECT CHAIN */}
      <Paper sx={{ p: 3, mb: 3 }}>
        <Typography variant="h6" gutterBottom>
          Step 1: Select Document Chain
        </Typography>
        
        {loading ? (
          <Box sx={{ display: 'flex', justifyContent: 'center', p: 3 }}>
            <CircularProgress />
          </Box>
        ) : (
          <Grid container spacing={2}>
            {chains.map(chain => (
              <Grid item xs={12} md={6} lg={4} key={chain.id}>
                <Card 
                  sx={{ 
                    cursor: 'pointer',
                    border: selectedChain?.id === chain.id ? '2px solid' : '1px solid',
                    borderColor: selectedChain?.id === chain.id ? 'primary.main' : 'divider',
                    '&:hover': { boxShadow: 3 }
                  }}
                  onClick={() => setSelectedChain(chain)}
                >
                  <CardContent>
                    <Typography variant="h6" noWrap>
                      {chain.document_title || chain.chain_id}
                    </Typography>
                    <Typography variant="body2" color="text.secondary">
                      {chain.document_number || 'No number'}
                    </Typography>
                    <Box sx={{ mt: 1 }}>
                      <Chip label={`${chain.total_revisions || 0} Revisions`} size="small" />
                    </Box>
                  </CardContent>
                </Card>
              </Grid>
            ))}
          </Grid>
        )}
      </Paper>

      {/* STEP 2: UPLOAD FILES */}
      {selectedChain && (
        <Paper sx={{ p: 3, mb: 3 }}>
          <Box sx={{ display: 'flex', alignItems: 'center', mb: 2 }}>
            <Typography variant="h6" sx={{ flexGrow: 1 }}>
              Step 2: Upload Revision Files
            </Typography>
            <TextField
              type="number"
              label="Number of Revisions"
              value={revisionCount}
              onChange={(e) => setRevisionCount(Math.min(10, Math.max(1, parseInt(e.target.value) || 1)))}
              size="small"
              sx={{ width: 150 }}
              InputProps={{ inputProps: { min: 1, max: 10 } }}
            />
          </Box>

          <Alert severity="info" sx={{ mb: 3 }}>
            Selected: <strong>{selectedChain.document_title || selectedChain.chain_id}</strong>
          </Alert>

          {/* LARGE UPLOAD BUTTONS */}
          <Grid container spacing={3}>
            {Array.from({ length: revisionCount }, (_, i) => i + 1).map(revNum => (
              <Grid item xs={12} md={6} lg={4} key={revNum}>
                <Paper 
                  elevation={files[revNum] ? 0 : 1}
                  sx={{ 
                    p: 3, 
                    textAlign: 'center',
                    bgcolor: files[revNum] ? 'success.light' : 'background.paper',
                    border: files[revNum] ? '2px solid' : '1px dashed',
                    borderColor: files[revNum] ? 'success.main' : 'divider',
                    minHeight: 180
                  }}
                >
                  <Typography variant="h5" gutterBottom fontWeight="bold">
                    Revision {revNum}
                  </Typography>
                  <Typography variant="body2" color="text.secondary" gutterBottom>
                    Rev {revNum}
                  </Typography>

                  {files[revNum] ? (
                    <>
                      <CheckIcon sx={{ fontSize: 48, color: 'success.main', my: 2 }} />
                      <Typography variant="body2" noWrap>
                        {files[revNum].name}
                      </Typography>
                      <Typography variant="caption" color="text.secondary">
                        {(files[revNum].size / 1024 / 1024).toFixed(2)} MB
                      </Typography>
                      <Button
                        size="small"
                        onClick={() => handleFileSelect(revNum, null)}
                        sx={{ mt: 1 }}
                      >
                        Remove
                      </Button>
                    </>
                  ) : (
                    <>
                      <Button
                        variant="contained"
                        component="label"
                        size="large"
                        startIcon={<UploadIcon />}
                        sx={{ mt: 2, mb: 1 }}
                        fullWidth
                      >
                        Upload CRS Sheet {revNum}
                        <input
                          type="file"
                          hidden
                          accept=".pdf"
                          onChange={(e) => {
                            if (e.target.files[0]) {
                              handleFileSelect(revNum, e.target.files[0]);
                            }
                          }}
                        />
                      </Button>
                      <Typography variant="caption" color="text.secondary">
                        PDF files only
                      </Typography>
                    </>
                  )}
                </Paper>
              </Grid>
            ))}
          </Grid>

          {/* UPLOAD ALL BUTTON */}
          <Box sx={{ mt: 4, display: 'flex', gap: 2, justifyContent: 'center' }}>
            <Button
              variant="outlined"
              size="large"
              onClick={() => {
                setFiles({});
                setError(null);
                setSuccess(null);
              }}
              startIcon={<RefreshIcon />}
            >
              Clear All
            </Button>
            <Button
              variant="contained"
              size="large"
              onClick={handleUpload}
              disabled={uploading || Object.keys(files).length === 0}
              startIcon={uploading ? <CircularProgress size={20} /> : <UploadIcon />}
              sx={{ minWidth: 200 }}
            >
              {uploading ? 'Uploading...' : `Upload ${Object.keys(files).length} Files`}
            </Button>
            <Button
              variant="contained"
              color="success"
              size="large"
              onClick={handleDownloadExcel}
              startIcon={<DownloadIcon />}
            >
              Download Excel
            </Button>
          </Box>
        </Paper>
      )}

      {/* No Chain Selected Message */}
      {!selectedChain && !loading && chains.length > 0 && (
        <Alert severity="info">
          👆 Please select a document chain above to start uploading revisions
        </Alert>
      )}
    </Box>
  );
};

export default CRSMultiRevisionSimple;
