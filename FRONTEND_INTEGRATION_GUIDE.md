# 🎨 Frontend Integration Guide - CRS Multi-Revision

## ✅ What's Been Created

1. **Frontend Component:** `FRONTEND_CRS_MULTI_REVISION.jsx` - Complete React component
2. **Backend Endpoint:** Dashboard summary added to revision_views.py
3. **Container:** Updated and running with all endpoints

---

## 📋 Quick Integration Steps

### Option 1: Standalone Page (Recommended)

1. **Copy the component file to your frontend:**
   ```bash
   # From RAD_AI folder
   cp FRONTEND_CRS_MULTI_REVISION.jsx ../airflow_frontend/src/pages/CRSMultiRevision.jsx
   ```

2. **Add route in your router:**
   ```jsx
   // In your App.jsx or routes file
   import CRSMultiRevision from './pages/CRSMultiRevision';
   
   <Route path="/crs/multi-revision" element={<CRSMultiRevision />} />
   ```

3. **Add navigation link:**
   ```jsx
   <MenuItem onClick={() => navigate('/crs/multi-revision')}>
     Multi-Revision Management
   </MenuItem>
   ```

4. **Install Material-UI (if not installed):**
   ```bash
   npm install @mui/material @mui/icons-material @emotion/react @emotion/styled
   ```

### Option 2: Integrate into Existing CRS Page

Add upload dialog to existing `CRSDocuments.jsx` - see "Integration Code" below.

---

## 🔌 API Endpoints Available

All endpoints are ready at `http://localhost:8000/api/v1/crs/revision-chains/`

### 1. List Chains
```http
GET /api/v1/crs/revision-chains/
Headers: Authorization: Bearer {token}

Response: [
  {
    "id": 1,
    "document_title": "Building Design",
    "document_number": "PRJ-001",
    "total_revisions": 3,
    "total_comments_across_revisions": 125
  }
]
```

### 2. Create Chain
```http
POST /api/v1/crs/revision-chains/
Headers: 
  Authorization: Bearer {token}
  Content-Type: application/json
Body: {
  "document_title": "Building A Structural Review",
  "document_number": "PRJ-2026-001",
  "project_name": "Dubai Marina Tower",
  "description": "Structural design review"
}

Response: {
  "id": 5,
  "document_title": "Building A Structural Review",
  ...
}
```

### 3. Upload Revision with File
```http
POST /api/v1/crs/revision-chains/{id}/upload_and_add_revision/
Headers: Authorization: Bearer {token}
Content-Type: multipart/form-data

Form Data:
  file: [PDF file]
  revision_label: "Rev 0"
  parent_revision_id: 101 (optional)
  notes: "First submission"
  project_name: "Dubai Marina Tower" (optional)
  document_number: "PRJ-2026-001" (optional)
  contractor: "ABC Construction" (optional)
  department: "Structural" (optional)

Response: {
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

### 4. Download Excel
```http
GET /api/v1/crs/revision-chains/{id}/export_excel/
Headers: Authorization: Bearer {token}

Response: Excel file download
```

### 5. Dashboard Summary
```http
GET /api/v1/crs/revision-chains/dashboard_summary/
Headers: Authorization: Bearer {token}

Response: {
  "summary": {
    "total_chains": 10,
    "active_chains": 7,
    "total_revisions": 45,
    "total_comments": 523
  },
  "top_chains": [...],
  "recent_activities": [...]
}
```

---

## 💻 Integration Code Snippets

### Add Upload Dialog to Existing CRS Page

```jsx
import React, { useState } from 'react';
import { Button, Dialog, DialogTitle, DialogContent, DialogActions, TextField, Grid, Alert } from '@mui/material';
import { UploadFile as UploadIcon } from '@mui/icons-material';

const UploadRevisionDialog = ({ open, onClose, chainId, onSuccess }) => {
  const [form, setForm] = useState({
    file: null,
    revision_label: '',
    parent_revision_id: '',
    notes: ''
  });
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState(null);

  const handleSubmit = async (e) => {
    e.preventDefault();
    setLoading(true);

    const formData = new FormData();
    formData.append('file', form.file);
    formData.append('revision_label', form.revision_label);
    if (form.parent_revision_id) {
      formData.append('parent_revision_id', form.parent_revision_id);
    }
    if (form.notes) {
      formData.append('notes', form.notes);
    }

    try {
      const token = localStorage.getItem('access_token');
      const response = await fetch(
        `http://localhost:8000/api/v1/crs/revision-chains/${chainId}/upload_and_add_revision/`,
        {
          method: 'POST',
          headers: { 'Authorization': `Bearer ${token}` },
          body: formData
        }
      );

      if (response.ok) {
        const data = await response.json();
        setResult(data);
        onSuccess && onSuccess(data);
      }
    } catch (error) {
      console.error('Upload error:', error);
    } finally {
      setLoading(false);
    }
  };

  return (
    <Dialog open={open} onClose={onClose} maxWidth="md" fullWidth>
      <DialogTitle>Upload Revision</DialogTitle>
      <form onSubmit={handleSubmit}>
        <DialogContent>
          {result && (
            <Alert severity="success" sx={{ mb: 2 }}>
              Extracted {result.data.extraction_summary.total_comments} comments
            </Alert>
          )}

          <Grid container spacing={2}>
            <Grid item xs={12}>
              <Button variant="outlined" component="label" fullWidth>
                {form.file ? form.file.name : 'Select PDF *'}
                <input
                  type="file"
                  hidden
                  accept=".pdf"
                  onChange={(e) => setForm({...form, file: e.target.files[0]})}
                />
              </Button>
            </Grid>
            
            <Grid item xs={12} md={6}>
              <TextField
                label="Revision Label *"
                fullWidth
                required
                value={form.revision_label}
                onChange={(e) => setForm({...form, revision_label: e.target.value})}
                placeholder="Rev 0"
              />
            </Grid>
            
            <Grid item xs={12} md={6}>
              <TextField
                label="Parent Revision ID"
                fullWidth
                type="number"
                value={form.parent_revision_id}
                onChange={(e) => setForm({...form, parent_revision_id: e.target.value})}
              />
            </Grid>
            
            <Grid item xs={12}>
              <TextField
                label="Notes"
                fullWidth
                multiline
                rows={2}
                value={form.notes}
                onChange={(e) => setForm({...form, notes: e.target.value})}
              />
            </Grid>
          </Grid>
        </DialogContent>
        <DialogActions>
          <Button onClick={onClose}>Cancel</Button>
          <Button 
            type="submit" 
            variant="contained" 
            disabled={loading || !form.file || !form.revision_label}
          >
            {loading ? 'Uploading...' : 'Upload & Extract'}
          </Button>
        </DialogActions>
      </form>
    </Dialog>
  );
};

export default UploadRevisionDialog;
```

### Fetch and Display Chains

```jsx
const [chains, setChains] = useState([]);

useEffect(() => {
  loadChains();
}, []);

const loadChains = async () => {
  const token = localStorage.getItem('access_token');
  const response = await fetch(
    'http://localhost:8000/api/v1/crs/revision-chains/',
    { headers: { 'Authorization': `Bearer ${token}` } }
  );
  
  if (response.ok) {
    const data = await response.json();
    setChains(Array.isArray(data) ? data : data.results || []);
  }
};

// Display
{chains.map(chain => (
  <Card key={chain.id}>
    <CardContent>
      <Typography variant="h6">{chain.document_title}</Typography>
      <Typography>Revisions: {chain.total_revisions}</Typography>
      <Button onClick={() => openUploadDialog(chain.id)}>
        Upload Revision
      </Button>
    </CardContent>
  </Card>
))}
```

### Download Excel

```jsx
const downloadExcel = async (chainId) => {
  const token = localStorage.getItem('access_token');
  const response = await fetch(
    `http://localhost:8000/api/v1/crs/revision-chains/${chainId}/export_excel/`,
    { headers: { 'Authorization': `Bearer ${token}` } }
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
};
```

---

## 🎨 UI Components Used

The component uses Material-UI components:
- `Dialog` - Modal dialogs
- `Card` - Chain display cards
- `TextField` - Form inputs
- `Button` - Actions
- `Chip` - Status badges
- `Alert` - Messages
- `Grid` - Layout
- `Tabs` - Tab navigation
- `Accordion` - Collapsible sections

---

## 🔐 Authentication

All API calls require JWT token:

```jsx
const getAuthHeaders = () => {
  const token = localStorage.getItem('access_token');
  return {
    'Authorization': `Bearer ${token}`
  };
};
```

---

## 📱 Key Features

### 1. Create Chain
- Form with title, document number, project name
- Validation for required fields
- Success feedback

### 2. Upload Revision
- File upload with drag-drop support
- All metadata fields (revision label, parent, notes, etc.)
- Real-time extraction results
- Success message with comment counts

### 3. View Chains
- Card-based grid layout
- Status badges (active, completed, etc.)
- Revision count and comment count
- Expandable revision list

### 4. Download Excel
- One-click download
- File named with chain ID
- Contains all 4 sheets

---

## 🧪 Testing the Integration

1. **Start Frontend:**
   ```bash
   cd airflow_frontend
   npm start
   ```

2. **Access Page:**
   - Navigate to `/crs/multi-revision`

3. **Create Test Chain:**
   - Click "Create Revision Chain"
   - Fill form and submit

4. **Upload Revision:**
   - Click "Upload Revision" on a chain
   - Select PDF with red/yellow comments
   - Enter revision label
   - Submit and see extraction results

5. **Download Excel:**
   - Click download icon
   - Open Excel to verify 4 sheets

---

## 🎯 Workflow Demo

```
User Flow:
1. Click "Create Revision Chain" → Enter details → Submit
2. See new chain card appear
3. Click "Upload Revision" on chain → Select PDF → "Rev 0" → Submit
4. See extraction summary: "45 comments extracted"
5. Click "Upload Revision" again → Select new PDF → "Rev 1" → Enter parent ID → Submit
6. See summary: "32 comments extracted, linked to Rev 0"
7. Click download icon → Excel file downloads with all data
```

---

## ✅ What's Working

- ✅ Create revision chains
- ✅ Upload PDFs with automatic extraction
- ✅ Link revisions (parent-child)
- ✅ View chains with stats
- ✅ Download Excel (4 sheets)
- ✅ Real-time extraction feedback
- ✅ Form validation
- ✅ Loading states
- ✅ Error handling

---

## 🚀 Deployment Checklist

Frontend:
- [ ] Copy component file
- [ ] Add route
- [ ] Add navigation link
- [ ] Test file upload
- [ ] Test Excel download

Backend:
- [x] Endpoints deployed
- [x] Container running
- [x] Excel export working
- [x] File upload working
- [x] Dashboard endpoint added

---

## 📞 API Configuration

Update the API base URL in the component if needed:

```jsx
// At the top of CRSMultiRevision.jsx
const API_BASE_URL = 'http://localhost:8000/api/v1';
// or
const API_BASE_URL = process.env.REACT_APP_API_URL || 'http://localhost:8000/api/v1';
```

---

**Status:** ✅ Ready to Integrate  
**Backend:** http://localhost:8000  
**Component:** FRONTEND_CRS_MULTI_REVISION.jsx  
**Documentation:** Complete
