# Transformer Document Upload - Frontend Integration Guide

## Overview
Modified the electrical datasheet smart upload page to support transformer-specific documents instead of SLD files.

## Backend Changes (Completed ✅)

### 1. API Endpoint
**URL**: `/api/v1/electrical-datasheet/smart-sld/process/`
**Method**: POST
**Content-Type**: multipart/form-data

### 2. Request Parameters

#### For Transformers (Power and Distribution):
```javascript
const formData = new FormData();

// Required: Specify equipment type as 'transformer'
formData.append('equipment_type', 'transformer');

// Project information (optional)
formData.append('project_name', 'Project XYZ');
formData.append('drawing_number', 'DWG-001');
formData.append('area', 'Area 1');

// Upload multiple files
formData.append('files', mvCalcFile);
formData.append('files', criteriaFile);
formData.append('files', formulaFile);
formData.append('files', lvCalcFile);

// Specify document type for each file
formData.append('doc_type_' + mvCalcFile.name, 'mv_trafo_calculation');
formData.append('doc_type_' + criteriaFile.name, 'criteria');
formData.append('doc_type_' + formulaFile.name, 'formula');
formData.append('doc_type_' + lvCalcFile.name, 'lv_trafo_calculation');

// Send request
axios.post('/api/v1/electrical-datasheet/smart-sld/process/', formData);
```

### 3. Supported Document Types for Transformers
```javascript
const TRANSFORMER_DOCUMENT_TYPES = [
  {
    type: 'mv_trafo_calculation',
    label: 'MV Trafo Calculation',
    description: 'Medium Voltage Transformer Calculation Document'
  },
  {
    type: 'criteria',
    label: 'Criteria',
    description: 'Transformer Selection Criteria Document'
  },
  {
    type: 'formula',
    label: 'Formula',
    description: 'Transformer Design Formula Document'
  },
  {
    type: 'lv_trafo_calculation',
    label: 'LV Trafo Calculation',
    description: 'Low Voltage Transformer Calculation Document'
  }
];
```

### 4. API to Get Supported Documents
**URL**: `/api/v1/electrical-datasheet/equipment-types/transformer/supported-documents/`
**Method**: GET

**Response**:
```json
{
  "equipment_type": "transformer",
  "equipment_name": "Transformer",
  "supported_documents": [
    {
      "type": "mv_trafo_calculation",
      "label": "MV Trafo Calculation",
      "description": "Medium Voltage Transformer Calculation Document",
      "required": false
    },
    {
      "type": "criteria",
      "label": "Criteria",
      "description": "Transformer Selection Criteria Document",
      "required": false
    },
    {
      "type": "formula",
      "label": "Formula",
      "description": "Transformer Design Formula Document",
      "required": false
    },
    {
      "type": "lv_trafo_calculation",
      "label": "LV Trafo Calculation",
      "description": "Low Voltage Transformer Calculation Document",
      "required": false
    }
  ]
}
```

### 5. Success Response
```json
{
  "success": true,
  "job_id": "550e8400-e29b-41d4-a716-446655440000",
  "equipment_type": "transformer",
  "documents_uploaded": 4,
  "documents": [
    {
      "filename": "mv_calc.pdf",
      "doc_type": "mv_trafo_calculation",
      "s3_key": "datasheets/uploaded/transformer/20260311_mv_calc.pdf",
      "size": 1024567,
      "content_type": "application/pdf"
    },
    {
      "filename": "criteria.pdf",
      "doc_type": "criteria",
      "s3_key": "datasheets/uploaded/transformer/20260311_criteria.pdf",
      "size": 512345,
      "content_type": "application/pdf"
    }
  ],
  "message": "Successfully uploaded 4 transformer documents"
}
```

## Frontend Implementation Steps (TODO)

### Step 1: Modify the Smart Upload Page Component

**File**: `airflow_frontend/src/pages/Engineering/Electrical/SingleLineDiagram.jsx` (or similar)

Add equipment type detection:
```jsx
const [selectedEquipmentType, setSelectedEquipmentType] = useState(null);
const [supportedDocuments, setSupportedDocuments] = useState([]);
const [uploadedFiles, setUploadedFiles] = useState([]);
const [fileDocTypes, setFileDocTypes] = useState({});

// Fetch supported documents when equipment type changes
useEffect(() => {
  if (selectedEquipmentType === 'transformer') {
    axios.get(`/api/v1/electrical-datasheet/equipment-types/transformer/supported-documents/`)
      .then(response => {
        setSupportedDocuments(response.data.supported_documents);
      });
  }
}, [selectedEquipmentType]);
```

### Step 2: Update File Upload Component

For transformers, show document type selector for each file:
```jsx
{selectedEquipmentType === 'transformer' && uploadedFiles.length > 0 && (
  <Box>
    <Typography variant="h6">Assign Document Types</Typography>
    {uploadedFiles.map((file, index) => (
      <Box key={index} sx={{ mb: 2 }}>
        <Typography variant="body2">{file.name}</Typography>
        <FormControl fullWidth size="small">
          <InputLabel>Document Type</InputLabel>
          <Select
            value={fileDocTypes[file.name] || ''}
            onChange={(e) => setFileDocTypes({
              ...fileDocTypes,
              [file.name]: e.target.value
            })}
          >
            {supportedDocuments.map(doc => (
              <MenuItem key={doc.type} value={doc.type}>
                {doc.label} - {doc.description}
              </MenuItem>
            ))}
          </Select>
        </FormControl>
      </Box>
    ))}
  </Box>
)}
```

### Step 3: Update Form Submission

```jsx
const handleSubmit = async () => {
  const formData = new FormData();
  
  // For transformers
  if (selectedEquipmentType === 'transformer') {
    formData.append('equipment_type', 'transformer');
    
    uploadedFiles.forEach(file => {
      formData.append('files', file);
      const docType = fileDocTypes[file.name];
      if (docType) {
        formData.append(`doc_type_${file.name}`, docType);
      }
    });
  } else {
    // Regular SLD upload
    uploadedFiles.forEach(file => {
      formData.append('files', file);
    });
    formData.append('datasheet_transformer', 'true');
  }
  
  // Add project info
  formData.append('project_name', projectName);
  formData.append('drawing_number', drawingNumber);
  
  try {
    const response = await axios.post(
      '/api/v1/electrical-datasheet/smart-sld/process/',
      formData,
      {
        headers: { 'Content-Type': 'multipart/form-data' }
      }
    );
    
    console.log('Upload success:', response.data);
    // Show success message
  } catch (error) {
    console.error('Upload failed:', error);
    // Show error message
  }
};
```

### Step 4: Add Equipment Type Selector

At the top of the upload form:
```jsx
<FormControl fullWidth sx={{ mb: 3 }}>
  <InputLabel>Equipment Type</InputLabel>
  <Select
    value={selectedEquipmentType || ''}
    onChange={(e) => setSelectedEquipmentType(e.target.value)}
  >
    <MenuItem value="sld">Single Line Diagram (General)</MenuItem>
    <MenuItem value="transformer">Transformer (Power and Distribution)</MenuItem>
  </Select>
</FormControl>

{selectedEquipmentType === 'transformer' && (
  <Alert severity="info" sx={{ mb: 2 }}>
    For transformers, please upload: MV Trafo Calculation, Criteria, Formula, and LV Trafo Calculation documents.
  </Alert>
)}

{selectedEquipmentType === 'sld' && (
  <Alert severity="info" sx={{ mb: 2 }}>
    Upload SLD (Single Line Diagram) files in PDF, PNG, or JPG format.
  </Alert>
)}
```

## Testing Checklist

- [ ] Test transformer document upload with all 4 document types
- [ ] Verify document type assignment UI works correctly
- [ ] Test with mixed document types
- [ ] Verify success response is displayed
- [ ] Test error handling (missing document types, invalid files)
- [ ] Verify documents are stored correctly in S3/local storage
- [ ] Test regular SLD upload still works for other equipment types

## Notes

1. **Core Logic Unchanged**: The SLD processing logic remains intact and works for all other equipment types
2. **Transformer-Only Feature**: This special handling only applies when `equipment_type='transformer'`
3. **Backward Compatible**: Existing SLD upload functionality is not affected
4. **Extensible**: Can easily add more equipment-specific document types in the future
