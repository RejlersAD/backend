# RAG (Retrieval Augmented Generation) System for P&ID Analysis

## Overview

This RAG system enhances P&ID analysis by providing context from reference standards, guidelines, and documentation. It uses OpenAI embeddings and similarity search to retrieve relevant information during analysis.

## Architecture

### Components

1. **RAG Service** (`rag_service.py`)
   - Generates embeddings using OpenAI's `text-embedding-3-small` model
   - Stores embeddings in PostgreSQL as JSON
   - Performs cosine similarity search for context retrieval

2. **Document Processor** (`document_processor.py`)
   - Extracts text from PDF, DOCX, and TXT files
   - Intelligently chunks text with configurable overlap
   - Cleans and preprocesses document content

3. **Reference Document Model** (`models.py`)
   - Stores uploaded reference documents
   - Tracks embedding status (pending/processing/completed/failed)
   - Maintains document metadata and chunk information

4. **API Endpoints** (`views.py`, `urls.py`)
   - Upload reference documents
   - List/retrieve/delete documents
   - Activate/deactivate documents
   - Reprocess failed documents

## Features

### ✅ Implemented

- ✅ OpenAI embedding generation (text-embedding-3-small, 1536 dimensions)
- ✅ Multi-format document support (PDF, DOCX, TXT)
- ✅ Intelligent text chunking with overlap
- ✅ PostgreSQL storage for embeddings (JSON field)
- ✅ Cosine similarity search
- ✅ Soft-coded configuration via environment variables
- ✅ Integration with P&ID analysis workflow
- ✅ Document lifecycle management (upload, activate, deactivate, reprocess)
- ✅ Metadata tracking (title, category, filename, chunk count)

### 📋 Configuration

All RAG settings are configured via environment variables:

```env
# Enable/disable RAG
RAG_ENABLED=true

# Embedding model (OpenAI)
EMBEDDING_MODEL=text-embedding-3-small

# Document chunking
CHUNK_SIZE=1000
CHUNK_OVERLAP=200

# Retrieval settings
RAG_TOP_K=5
RAG_SIMILARITY_THRESHOLD=0.7
```

## Usage

### 1. Upload Reference Document

**Endpoint:** `POST /api/v1/pid/reference-documents/upload/`

**Request:**
```json
{
  "file": <file upload>,
  "title": "ADNOC P&ID Design Standards",
  "description": "Official ADNOC standards for P&ID design",
  "category": "standard"
}
```

**Categories:**
- `standard` - Industry standards
- `guideline` - Design guidelines
- `specification` - Equipment specifications
- `procedure` - Procedures and workflows
- `other` - Other reference materials

**Response:**
```json
{
  "id": 1,
  "title": "ADNOC P&ID Design Standards",
  "description": "Official ADNOC standards for P&ID design",
  "category": "standard",
  "file": "/media/reference_documents/adnoc_standards.pdf",
  "file_url": "https://...storage.../adnoc_standards.pdf",
  "chunk_count": 15,
  "embedding_status": "completed",
  "is_active": true,
  "uploaded_by": 1,
  "uploaded_by_username": "tanzeem",
  "created_at": "2025-01-15T10:00:00Z",
  "updated_at": "2025-01-15T10:00:30Z"
}
```

### 2. List Reference Documents

**Endpoint:** `GET /api/v1/pid/reference-documents/`

**Response:**
```json
[
  {
    "id": 1,
    "title": "ADNOC P&ID Design Standards",
    "category": "standard",
    "embedding_status": "completed",
    "chunk_count": 15,
    "is_active": true,
    ...
  },
  ...
]
```

### 3. P&ID Analysis with RAG

When RAG is enabled, the analysis automatically retrieves relevant context:

1. User uploads P&ID drawing
2. System extracts drawing number
3. RAG service searches for relevant chunks
4. Top 5 most similar chunks are retrieved
5. Context is injected into analysis prompt
6. OpenAI analyzes with enhanced context

**Example Enhanced Prompt:**
```
**REFERENCE CONTEXT FROM STANDARDS AND DOCUMENTATION:**

[STANDARD: ADNOC P&ID Design Standards]
Equipment Tagging Requirements:
- All pumps must be tagged as P-XXX-YYY
- All vessels must be tagged as V-XXX-YYY
...

---

[Original P&ID analysis prompt]

**Important:** Use the reference context above to enhance your analysis...
```

### 4. Document Management

**Activate Document:**
```
POST /api/v1/pid/reference-documents/{id}/activate/
```

**Deactivate Document:**
```
POST /api/v1/pid/reference-documents/{id}/deactivate/
```

**Reprocess Failed Document:**
```
POST /api/v1/pid/reference-documents/{id}/reprocess/
```

**Delete Document:**
```
DELETE /api/v1/pid/reference-documents/{id}/
```

## Data Flow

```
┌─────────────────────┐
│ Upload Document     │
│ (PDF/DOCX/TXT)     │
└──────────┬──────────┘
           │
           ▼
┌─────────────────────┐
│ Extract Text        │
│ (DocumentProcessor) │
└──────────┬──────────┘
           │
           ▼
┌─────────────────────┐
│ Chunk Text          │
│ (1000 chars/chunk)  │
└──────────┬──────────┘
           │
           ▼
┌─────────────────────┐
│ Generate Embeddings │
│ (OpenAI API)        │
└──────────┬──────────┘
           │
           ▼
┌─────────────────────┐
│ Store in PostgreSQL │
│ (JSON field)        │
└─────────────────────┘

┌─────────────────────┐
│ P&ID Analysis       │
│ Request             │
└──────────┬──────────┘
           │
           ▼
┌─────────────────────┐
│ Generate Query      │
│ Embedding           │
└──────────┬──────────┘
           │
           ▼
┌─────────────────────┐
│ Similarity Search   │
│ (Cosine)            │
└──────────┬──────────┘
           │
           ▼
┌─────────────────────┐
│ Retrieve Top 5      │
│ Chunks              │
└──────────┬──────────┘
           │
           ▼
┌─────────────────────┐
│ Augment Prompt      │
│ with Context        │
└──────────┬──────────┘
           │
           ▼
┌─────────────────────┐
│ Send to OpenAI      │
│ for Analysis        │
└─────────────────────┘
```

## Database Schema

### ReferenceDocument Model

| Field | Type | Description |
|-------|------|-------------|
| id | AutoField | Primary key |
| title | CharField | Document title |
| description | TextField | Document description |
| category | CharField | Document category |
| file | FileField | Uploaded file |
| content_text | TextField | Extracted text |
| chunk_count | IntegerField | Number of chunks |
| vector_db_ids | JSONField | Chunk data with embeddings |
| embedding_status | CharField | Status (pending/processing/completed/failed) |
| is_active | BooleanField | Whether to use in RAG |
| uploaded_by | ForeignKey | User who uploaded |
| created_at | DateTimeField | Creation timestamp |
| updated_at | DateTimeField | Update timestamp |

### vector_db_ids JSON Structure

```json
[
  {
    "id": "doc_001_chunk_0",
    "text": "Equipment Tagging Requirements...",
    "embedding": [0.001, 0.033, -0.015, ...],
    "metadata": {
      "title": "ADNOC Standards",
      "category": "standard",
      "filename": "adnoc.pdf",
      "chunk_index": 0,
      "chunk_size": 850
    }
  },
  ...
]
```

## Testing

### Quick Test

```bash
cd backend
python test_rag_simple.py
```

**Expected Output:**
```
============================================================
TESTING SIMPLIFIED RAG SYSTEM
============================================================
OpenAI API Key: ✓ Set
RAG Enabled: true
Embedding Model: text-embedding-3-small

Test 1: Generating embedding...
✓ Generated embedding with 1536 dimensions

Test 2: Adding reference document...
[INFO] Processing 1 chunks for document test_001
✓ Added document with 1 chunks

============================================================
✓ ALL TESTS PASSED - RAG SYSTEM IS OPERATIONAL
============================================================
```

## Performance

- **Embedding Generation:** ~200ms per chunk
- **Document Processing:** Depends on file size
  - Small PDF (10 pages): ~2-3 seconds
  - Large PDF (100 pages): ~15-20 seconds
- **Similarity Search:** ~100ms for 100 chunks
- **Storage:** ~6KB per 1000-char chunk (with embedding)

## Cost Estimation

Using OpenAI `text-embedding-3-small`:
- **Cost:** $0.02 per 1M tokens
- **Average Document:** 10,000 words = ~13,000 tokens
- **Cost per Document:** ~$0.00026
- **100 Documents:** ~$0.026
- **1000 Documents:** ~$0.26

## Migration Guide

### From No RAG to RAG

1. **Add environment variables:**
   ```env
   RAG_ENABLED=true
   EMBEDDING_MODEL=text-embedding-3-small
   CHUNK_SIZE=1000
   CHUNK_OVERLAP=200
   RAG_TOP_K=5
   RAG_SIMILARITY_THRESHOLD=0.7
   ```

2. **Run migrations:**
   ```bash
   python manage.py migrate pid_analysis
   ```

3. **Upload reference documents:**
   - Use API or admin panel
   - Upload standards, guidelines, specifications

4. **Verify:**
   - Check embedding_status = "completed"
   - Ensure is_active = true
   - Test P&ID analysis

## Troubleshooting

### Embedding Failed

**Symptom:** `embedding_status = "failed"`

**Solutions:**
1. Check OpenAI API key validity
2. Verify document has extractable text
3. Check document file format (PDF/DOCX/TXT)
4. Reprocess document: `POST /api/v1/pid/reference-documents/{id}/reprocess/`

### No Context Retrieved

**Symptom:** RAG enabled but no context in analysis

**Solutions:**
1. Verify documents are active: `is_active = true`
2. Check similarity threshold (try lowering it)
3. Ensure documents are completed: `embedding_status = "completed"`
4. Verify drawing number matches document content

### Slow Performance

**Symptom:** Document upload takes too long

**Solutions:**
1. Reduce CHUNK_SIZE (e.g., from 1000 to 500)
2. Process large documents asynchronously
3. Increase CHUNK_OVERLAP (less chunks = faster)
4. Use smaller embedding model (if available)

## Future Enhancements

### Potential Improvements

- ⏳ Async document processing with Celery
- ⏳ Support for vector databases (ChromaDB, Pinecone)
- ⏳ Advanced chunking strategies (semantic chunking)
- ⏳ Multi-language support
- ⏳ Document versioning
- ⏳ Automatic document updates
- ⏳ RAG analytics dashboard

## API Reference

### Authentication

All endpoints require JWT authentication:
```
Authorization: Bearer <jwt_token>
```

### Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/v1/pid/reference-documents/upload/` | Upload document |
| GET | `/api/v1/pid/reference-documents/` | List documents |
| GET | `/api/v1/pid/reference-documents/{id}/` | Get document |
| PUT | `/api/v1/pid/reference-documents/{id}/` | Update document |
| DELETE | `/api/v1/pid/reference-documents/{id}/` | Delete document |
| POST | `/api/v1/pid/reference-documents/{id}/activate/` | Activate document |
| POST | `/api/v1/pid/reference-documents/{id}/deactivate/` | Deactivate document |
| POST | `/api/v1/pid/reference-documents/{id}/reprocess/` | Reprocess document |

## Security Considerations

1. **Access Control:** Documents are user-scoped (uploaded_by)
2. **File Validation:** Only PDF, DOCX, TXT allowed
3. **Size Limits:** Max 100MB per file
4. **API Key Security:** OpenAI key stored in environment variables
5. **SQL Injection:** Protected by Django ORM
6. **XSS:** Content cleaned before storage

## Support

For issues or questions:
- GitHub Issues: https://github.com/rejlersabudhabi1-RAD/aiflow_backend
- Email: tanzeem.agra@rejlers.ae
