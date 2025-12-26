"""
Example: How to integrate MLflow tracking into existing features
WITHOUT modifying core logic - just add decorators!
"""

# ============================================================
# EXAMPLE 1: P&ID Design Verification
# File: backend/apps/pid_analysis/ai_service.py
# ============================================================

from apps.mlflow_integration.decorators import track_gpt_vision, track_gpt_text

# Original function (no changes to core logic)
@track_gpt_vision('pid_verification', 'diagram_analysis')
def analyze_pid_with_gpt4_vision(image_data, drawing_number):
    """
    Analyze P&ID diagram using GPT-4 Vision
    MLflow will automatically track:
    - Execution time
    - Model parameters
    - Success/failure
    - Confidence scores
    """
    # Your existing code remains unchanged
    response = openai.chat.completions.create(
        model="gpt-4o",
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Analyze this P&ID..."},
                    {"type": "image_url", "image_url": {"url": image_data}}
                ]
            }
        ]
    )
    
    # Return result (MLflow tracks automatically)
    return {
        'analysis': response.choices[0].message.content,
        'tokens_used': response.usage.total_tokens,
        'confidence_score': 0.95
    }


# ============================================================
# EXAMPLE 2: PFD to P&ID Conversion
# File: backend/apps/pfd_converter/services.py
# ============================================================

from apps.mlflow_integration.decorators import track_gpt_vision, track_gpt_text, track_dalle_generation

@track_gpt_vision('pfd_conversion', 'pfd_extraction')
def extract_pfd_data(pfd_image):
    """Extract data from PFD image"""
    # Your existing extraction logic
    result = gpt4_vision_extract(pfd_image)
    return result


@track_gpt_text('pfd_conversion', 'pid_spec_generation')
def generate_pid_specification(pfd_data, knowledge_base):
    """Generate P&ID specification"""
    # Your existing generation logic
    spec = gpt4_generate_spec(pfd_data, knowledge_base)
    return {
        'specification': spec,
        'tokens_used': 1500,
        'confidence_score': 0.92
    }


@track_dalle_generation('pfd_conversion')
def generate_pid_visual(pid_spec):
    """Generate P&ID visual diagram"""
    # Your existing DALL-E logic
    image_url = dalle_generate(pid_spec)
    return {
        'image_url': image_url,
        'cost': 0.04
    }


# ============================================================
# EXAMPLE 3: CRS Document Analysis
# File: backend/apps/crs/document_processor.py
# ============================================================

from apps.mlflow_integration.decorators import track_custom_operation

@track_custom_operation('crs_analysis', 'document_validation')
def validate_crs_document(document):
    """Validate CRS document structure"""
    # Your existing validation logic
    is_valid = run_validation(document)
    
    return {
        'valid': is_valid,
        'validation_score': 0.88,
        'errors_found': 2
    }


# ============================================================
# EXAMPLE 4: RAG Knowledge Base Queries
# File: backend/apps/pfd_converter/rag_service.py
# ============================================================

from apps.mlflow_integration.decorators import track_rag_operation

@track_rag_operation('pfd_conversion', 'knowledge_retrieval')
def retrieve_similar_documents(query, knowledge_base):
    """Retrieve similar documents from knowledge base"""
    # Your existing RAG logic
    docs = knowledge_base.similarity_search(query, k=5)
    
    return {
        'retrieved_docs': docs,
        'similarity_score': 0.85,
        'tokens_used': 500
    }


# ============================================================
# EXAMPLE 5: Manual Tracking (when decorators aren't suitable)
# File: backend/apps/pid_analysis/views.py
# ============================================================

from apps.mlflow_integration.tracker import mlflow_tracker

def complex_analysis_workflow(request):
    """Complex workflow with multiple steps"""
    
    # Your existing workflow
    step1_result = process_step1()
    step2_result = process_step2()
    
    # Manually log the entire workflow result
    mlflow_tracker.log_experiment_result(
        feature='pid_verification',
        operation='complex_workflow',
        params={
            'user_id': request.user.id,
            'workflow_version': '2.0',
            'steps': 2
        },
        metrics={
            'total_time': 15.5,
            'step1_accuracy': 0.95,
            'step2_accuracy': 0.92,
            'overall_score': 0.935
        },
        tags={
            'workflow_type': 'complex',
            'automated': 'true'
        }
    )
    
    return response


# ============================================================
# EXAMPLE 6: Model Performance Tracking
# File: backend/apps/pid_analysis/evaluation.py
# ============================================================

from apps.mlflow_integration.tracker import mlflow_tracker

def evaluate_pid_model_performance():
    """Evaluate and track model performance"""
    
    # Your existing evaluation logic
    accuracy = 0.94
    precision = 0.91
    recall = 0.93
    f1_score = 0.92
    
    # Track the performance
    mlflow_tracker.track_model_performance(
        feature='pid_verification',
        model_name='gpt-4o-vision-v2',
        accuracy=accuracy,
        precision=precision,
        recall=recall,
        f1_score=f1_score,
        false_positives=12,
        false_negatives=8
    )


# ============================================================
# KEY BENEFITS
# ============================================================

"""
✅ NO CORE LOGIC CHANGES - Just add decorators!
✅ Automatic tracking - Set and forget
✅ All features tracked consistently
✅ Performance metrics aggregated
✅ Model comparison across features
✅ Cost tracking for OpenAI API calls
✅ Error tracking and debugging
✅ Historical experiment comparison
✅ Easy to enable/disable (MLFLOW_ENABLED env var)
✅ Future-proof - New features can use same decorators
"""


# ============================================================
# HOW TO USE IN YOUR CODE
# ============================================================

"""
Step 1: Import the decorator you need
    from apps.mlflow_integration.decorators import track_gpt_vision

Step 2: Add decorator above your function
    @track_gpt_vision('your_feature', 'your_operation')
    def your_function():
        # Your existing code - NO CHANGES NEEDED
        return result

Step 3: That's it! MLflow will automatically track:
    - Execution time
    - Success/failure
    - Parameters
    - Metrics (tokens, cost, confidence, etc.)
    - Errors and stack traces

Step 4: View results in MLflow UI
    - Open: http://localhost:5000
    - Compare experiments
    - Analyze performance trends
    - Export data for reporting
"""
