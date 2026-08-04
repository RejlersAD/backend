# MLflow Model Orchestration

## 🎯 Overview

MLflow integration provides **automated experiment tracking, model orchestration, and performance monitoring** for all AI operations across the AIFlow platform - without modifying any existing code!

## ✨ Key Features

- 🔍 **Automatic Tracking** - Tracks all AI operations using decorators
- 📊 **Experiment Management** - Organizes experiments by feature
- 📈 **Performance Metrics** - Tracks accuracy, latency, costs, tokens
- 🔬 **Model Comparison** - Compare different models and parameters
- 💰 **Cost Monitoring** - Track OpenAI API costs per feature
- 🚫 **Non-Invasive** - Zero changes to existing core logic
- 🔌 **Plugin Architecture** - Easily extensible for new features
- 🎚️ **Toggle Control** - Enable/disable via environment variable

## 🏗️ Architecture

```
apps/mlflow_integration/
├── __init__.py              # App initialization
├── apps.py                  # Django app configuration
├── tracker.py               # Central MLflow tracker
├── decorators.py            # Reusable decorators
├── signals.py               # Automatic tracking via Django signals
├── views.py                 # REST API endpoints
├── urls.py                  # URL routing
├── INTEGRATION_EXAMPLES.py  # Usage examples
└── README.md               # This file
```

## 🚀 Quick Start

### 1. MLflow is Already Running

MLflow server is automatically started with docker-compose:

```bash
# MLflow UI available at:
http://localhost:5000
```

### 2. Add Tracking to Your Code

#### Option A: Using Decorators (Recommended)

```python
from apps.mlflow_integration.decorators import track_gpt_vision

@track_gpt_vision('pid_verification', 'diagram_analysis')
def analyze_pid_diagram(image_data):
    # Your existing code - NO CHANGES NEEDED
    response = gpt4_analyze(image_data)
    return response
```

#### Option B: Manual Logging

```python
from apps.mlflow_integration.tracker import mlflow_tracker

mlflow_tracker.log_experiment_result(
    feature='pfd_conversion',
    operation='spec_generation',
    params={'model': 'gpt-4o', 'temperature': 0.7},
    metrics={'tokens_used': 1500, 'confidence': 0.95},
    tags={'version': '2.0'}
)
```

### 3. View Results

Open MLflow UI: **http://localhost:5000**

## 📚 Available Decorators

### 1. `@track_gpt_vision` - GPT-4 Vision Calls

```python
from apps.mlflow_integration.decorators import track_gpt_vision

@track_gpt_vision('pid_verification', 'image_analysis')
def extract_diagram_data(image):
    # Your GPT-4V code
    return result
```

**Tracks:** Model, execution time, tokens, cost, success/failure

### 2. `@track_gpt_text` - GPT-4 Text Generation

```python
from apps.mlflow_integration.decorators import track_gpt_text

@track_gpt_text('pfd_conversion', 'spec_generation')
def generate_specification(data):
    # Your GPT-4 code
    return spec
```

**Tracks:** Model parameters, tokens, confidence scores

### 3. `@track_dalle_generation` - DALL-E Image Generation

```python
from apps.mlflow_integration.decorators import track_dalle_generation

@track_dalle_generation('pfd_conversion')
def generate_pid_diagram(prompt):
    # Your DALL-E code
    return image_url
```

**Tracks:** Generation time, cost, prompt parameters

### 4. `@track_rag_operation` - RAG Queries

```python
from apps.mlflow_integration.decorators import track_rag_operation

@track_rag_operation('pfd_conversion', 'knowledge_retrieval')
def query_knowledge_base(query):
    # Your RAG code
    return documents
```

**Tracks:** Retrieved docs count, similarity scores

### 5. `@track_custom_operation` - Custom Operations

```python
from apps.mlflow_integration.decorators import track_custom_operation

@track_custom_operation('crs_analysis', 'validation')
def validate_document(doc):
    # Your custom code
    return {'valid': True, 'score': 0.95}
```

**Tracks:** Any custom metrics from return value

## 🎯 Feature Experiments

MLflow organizes tracking by feature:

| Feature | Experiment Name | Operations Tracked |
|---------|----------------|-------------------|
| P&ID Verification | `P&ID Design Verification` | Image analysis, validation, report generation |
| PFD Conversion | `PFD to P&ID Conversion` | PFD extraction, P&ID generation, visual creation |
| CRS Management | `CRS Document Analysis` | Document processing, validation, analysis |
| Visual Generation | `Visual Diagram Generation` | DALL-E image generation, PDF creation |

## 🔌 REST API Endpoints

### Get All Experiments

```bash
GET /api/v1/mlflow/tracking/experiments/
```

**Response:**
```json
{
  "enabled": true,
  "tracking_uri": "http://mlflow:5000",
  "experiments": [
    {
      "key": "pid_verification",
      "name": "P&ID Design Verification",
      "experiment_id": "1"
    }
  ]
}
```

### Get Recent Runs

```bash
GET /api/v1/mlflow/tracking/runs/?feature=pfd_conversion&limit=10
```

**Response:**
```json
{
  "feature": "pfd_conversion",
  "runs": [
    {
      "run_id": "abc123",
      "metrics.execution_time": 2.5,
      "metrics.confidence_score": 0.95,
      "tags.status": "success"
    }
  ]
}
```

### Get Aggregated Metrics

```bash
GET /api/v1/mlflow/tracking/metrics/?feature=pid_verification
```

**Response:**
```json
{
  "feature": "pid_verification",
  "metrics": {
    "total_runs": 150,
    "avg_execution_time": 3.2,
    "success_rate": 94.5,
    "avg_confidence": 0.92,
    "total_cost": 25.50
  }
}
```

### Health Check

```bash
GET /api/v1/mlflow/tracking/health/
```

## 🔧 Configuration

### Environment Variables

```bash
# Enable/disable MLflow tracking
MLFLOW_ENABLED=true

# MLflow tracking server URI
MLFLOW_TRACKING_URI=http://mlflow:5000

# Backend storage (PostgreSQL)
MLFLOW_BACKEND_STORE_URI=postgresql://postgres:postgres@db:5432/radai_db
```

### Disable Tracking

```bash
# In .env or docker-compose.yml
MLFLOW_ENABLED=false
```

When disabled, all decorators become no-ops (zero performance impact).

## 📊 What Gets Tracked?

### Automatic Tracking (via Decorators)

- ✅ Function name
- ✅ Feature name
- ✅ Operation type
- ✅ Execution time
- ✅ Success/failure status
- ✅ Model parameters
- ✅ Tokens used (if available)
- ✅ API costs (if available)
- ✅ Confidence scores (if available)
- ✅ Error messages and stack traces

### Automatic Tracking (via Django Signals)

The following models are automatically tracked when saved:

- **P&ID Analysis Reports** - Tracks issue counts, confidence scores
- **PFD Conversions** - Tracks conversion status, extraction method
- **CRS Documents** - Tracks processing time, document type

## 🎨 Use Cases

### 1. Monitor AI Performance

Track how well your AI models are performing:
- Accuracy trends over time
- Confidence score distributions
- Error rates and types

### 2. Cost Optimization

Monitor OpenAI API costs:
- Total tokens used per feature
- Cost per operation
- Identify expensive operations

### 3. Model Comparison

Compare different models or parameters:
- GPT-4 vs GPT-3.5
- Temperature variations
- Prompt engineering results

### 4. Debugging

Quickly identify issues:
- Failed operations with stack traces
- Performance bottlenecks
- Anomaly detection

### 5. Reporting

Generate reports for stakeholders:
- AI operation statistics
- Cost breakdowns
- Performance metrics

## 🔮 Future Features

Ready to add new AI features? Just:

1. Import the appropriate decorator
2. Add it to your function
3. MLflow automatically tracks it!

```python
# New feature - Automatic tracking!
from apps.mlflow_integration.decorators import track_gpt_text

@track_gpt_text('new_feature', 'new_operation')
def new_ai_function():
    # Your code
    return result
```

## 🛠️ Troubleshooting

### MLflow UI Not Loading

```bash
# Check MLflow container status
docker-compose ps mlflow

# View MLflow logs
docker-compose logs mlflow

# Restart MLflow
docker-compose restart mlflow
```

### Experiments Not Appearing

1. Check `MLFLOW_ENABLED=true` in environment
2. Verify tracking URI is correct
3. Check backend container can reach MLflow container

### Tracking Not Working

```python
# Test MLflow connection
from apps.mlflow_integration.tracker import mlflow_tracker

print(f"MLflow enabled: {mlflow_tracker.enabled}")
print(f"Tracking URI: {mlflow_tracker.tracking_uri}")
```

## 📖 Examples

See [INTEGRATION_EXAMPLES.py](INTEGRATION_EXAMPLES.py) for comprehensive examples of:
- Decorator usage in different features
- Manual logging
- Model performance tracking
- Custom metrics
- Error handling

## 🤝 Contributing

To add tracking to a new feature:

1. Choose the appropriate decorator from `decorators.py`
2. Add it to your function (no code changes needed)
3. Test with MLflow UI
4. Document in INTEGRATION_EXAMPLES.py

## 📝 License

Part of the AIFlow project - same license applies.

---

**MLflow UI:** http://localhost:5000  
**API Docs:** http://localhost:8000/api/docs/
