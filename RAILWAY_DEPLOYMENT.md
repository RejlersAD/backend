# Railway Deployment Guide for RAG System

## Quick Deployment Steps

### 1. Add Environment Variables to Railway

Go to Railway Dashboard → aiflowbackend → Variables and add:

```env
RAG_ENABLED=true
EMBEDDING_MODEL=text-embedding-3-small
CHUNK_SIZE=1000
CHUNK_OVERLAP=200
RAG_TOP_K=5
RAG_SIMILARITY_THRESHOLD=0.7
```

### 2. Deploy to Railway

Railway will automatically deploy when you push to GitHub.

**Manual Deployment (if needed):**
1. Go to Railway Dashboard
2. Click on aiflowbackend service
3. Click "Deploy" button
4. Wait for build to complete

### 3. Run Database Migration

After deployment, run migration in Railway CLI:

```bash
python manage.py migrate pid_analysis
```

**Or use Railway CLI:**
```bash
railway run python manage.py migrate pid_analysis
```

### 4. Verify Deployment

**Check Migration:**
```bash
railway run python manage.py showmigrations pid_analysis
```

Expected output:
```
pid_analysis
 [X] 0001_initial
 [X] 0002_piddrawing_error_message
 [X] 0003_referencedocument
```

**Test RAG Service:**
```bash
railway run python test_rag_simple.py
```

Expected output:
```
============================================================
TESTING SIMPLIFIED RAG SYSTEM
============================================================
OpenAI API Key: ✓ Set
RAG Enabled: true

Test 1: Generating embedding...
✓ Generated embedding with 1536 dimensions

Test 2: Adding reference document...
✓ Added document with 1 chunks

✓ ALL TESTS PASSED - RAG SYSTEM IS OPERATIONAL
```

### 5. Test API Endpoints

**Get auth token:**
```bash
curl -X POST https://aiflowbackend-production.up.railway.app/api/v1/auth/login/ \
  -H "Content-Type: application/json" \
  -d '{"username":"tanzeem.agra@rejlers.ae","password":"Tanzeem@123"}'
```

**Test reference documents endpoint:**
```bash
curl https://aiflowbackend-production.up.railway.app/api/v1/pid/reference-documents/ \
  -H "Authorization: Bearer <token>"
```

Expected: `[]` (empty list initially)

### 6. Upload Test Reference Document

Create a test PDF with ADNOC standards and upload via API or admin panel.

**Via API:**
```bash
curl -X POST https://aiflowbackend-production.up.railway.app/api/v1/pid/reference-documents/upload/ \
  -H "Authorization: Bearer <token>" \
  -F "file=@test_standard.pdf" \
  -F "title=ADNOC P&ID Standards" \
  -F "category=standard"
```

### 7. Verify RAG Integration

1. Upload a P&ID drawing
2. Check analysis output
3. Verify reference context is included (if RAG_ENABLED=true)

## Environment Variable Reference

| Variable | Default | Description |
|----------|---------|-------------|
| RAG_ENABLED | false | Enable/disable RAG system |
| EMBEDDING_MODEL | text-embedding-3-small | OpenAI embedding model |
| CHUNK_SIZE | 1000 | Characters per chunk |
| CHUNK_OVERLAP | 200 | Overlap between chunks |
| RAG_TOP_K | 5 | Number of chunks to retrieve |
| RAG_SIMILARITY_THRESHOLD | 0.7 | Minimum similarity score |

## Troubleshooting

### Migration Fails

**Error:** `django.db.utils.ProgrammingError: relation "pid_analysis_referencedocument" already exists`

**Solution:**
```bash
railway run python manage.py migrate pid_analysis --fake 0003
```

### RAG Not Working

**Check environment variables:**
```bash
railway run python -c "import os; print('RAG_ENABLED:', os.getenv('RAG_ENABLED'))"
```

**Check OpenAI key:**
```bash
railway run python -c "from openai import OpenAI; c = OpenAI(); print('OpenAI OK')"
```

### Embedding Fails

**Error:** `Failed to generate embedding`

**Solutions:**
1. Verify OpenAI API key is valid
2. Check API quota/credits
3. Test locally first: `python test_rag_simple.py`

## Rollback Plan

If RAG causes issues, disable it:

```bash
# Set in Railway Variables
RAG_ENABLED=false
```

System will continue working without RAG context.

## Monitoring

**Check logs for RAG activity:**
```bash
railway logs
```

Look for:
- `[INFO] RAG enabled - retrieving context`
- `[INFO] Retrieved RAG context (X chars)`
- `[INFO] Document embedded successfully`

## Cost Monitoring

Monitor OpenAI usage:
1. Go to OpenAI dashboard
2. Check usage for embedding API
3. Expected: ~$0.00026 per document

## Success Criteria

✅ Migration applied
✅ Environment variables set
✅ Test script passes
✅ API endpoints accessible
✅ Document upload works
✅ P&ID analysis includes RAG context

## Next Steps

1. Upload official ADNOC standards
2. Upload engineering guidelines
3. Upload equipment specifications
4. Test with real P&ID drawings
5. Monitor analysis quality improvement

## Support

Issues? Check:
- RAG_README.md - Technical documentation
- RAG_IMPLEMENTATION_SUMMARY.md - Implementation details
- GitHub Issues: https://github.com/rejlersabudhabi1-RAD/aiflow_backend/issues
