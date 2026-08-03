"""
Reusable decorators for tracking AI operations
Can be applied to any function across features without code changes
"""

from functools import wraps
from apps.mlflow_integration.tracker import mlflow_tracker
import time
from typing import Callable, Any, Dict


def track_gpt_vision(feature: str, operation: str = 'image_analysis'):
    """
    Track GPT-4 Vision API calls
    
    Usage in any feature:
        from apps.mlflow_integration.decorators import track_gpt_vision
        
        @track_gpt_vision('pid_verification', 'diagram_extraction')
        def extract_pid_data(image):
            # your GPT-4V code
            return result
    """
    return mlflow_tracker.track_openai_call(
        feature=feature,
        operation=operation,
        model='gpt-4o-vision'
    )


def track_gpt_text(feature: str, operation: str = 'text_generation'):
    """
    Track GPT-4 text generation calls
    
    Usage:
        @track_gpt_text('pfd_conversion', 'spec_generation')
        def generate_pid_spec(data):
            # your GPT-4 code
            return result
    """
    return mlflow_tracker.track_openai_call(
        feature=feature,
        operation=operation,
        model='gpt-4o'
    )


def track_dalle_generation(feature: str):
    """
    Track DALL-E image generation
    
    Usage:
        @track_dalle_generation('pfd_conversion')
        def generate_pid_diagram(prompt):
            # your DALL-E code
            return image_url
    """
    return mlflow_tracker.track_openai_call(
        feature=feature,
        operation='image_generation',
        model='dall-e-3'
    )


def track_rag_operation(feature: str, operation: str = 'rag_query'):
    """
    Track RAG (Retrieval Augmented Generation) operations
    
    Usage:
        @track_rag_operation('pfd_conversion', 'knowledge_retrieval')
        def retrieve_similar_diagrams(query):
            # your RAG code
            return documents
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs):
            start_time = time.time()
            
            try:
                result = func(*args, **kwargs)
                
                # Track RAG metrics
                execution_time = time.time() - start_time
                
                metrics = {
                    'execution_time_seconds': execution_time,
                    'success': 1,
                }
                
                # Extract metrics from result if available
                if isinstance(result, dict):
                    if 'retrieved_docs' in result:
                        metrics['retrieved_docs_count'] = len(result['retrieved_docs'])
                    if 'similarity_score' in result:
                        metrics['avg_similarity_score'] = result['similarity_score']
                
                mlflow_tracker.log_experiment_result(
                    feature=feature,
                    operation=operation,
                    params={'function': func.__name__},
                    metrics=metrics,
                    tags={'operation_type': 'rag'}
                )
                
                return result
                
            except Exception as e:
                execution_time = time.time() - start_time
                mlflow_tracker.log_experiment_result(
                    feature=feature,
                    operation=operation,
                    params={'function': func.__name__},
                    metrics={
                        'execution_time_seconds': execution_time,
                        'success': 0
                    },
                    tags={
                        'operation_type': 'rag',
                        'error': str(e)
                    }
                )
                raise
        
        return wrapper
    return decorator


def track_custom_operation(
    feature: str,
    operation: str,
    auto_log_params: bool = True,
    auto_log_result: bool = True
):
    """
    Generic decorator for tracking any custom operation
    
    Usage:
        @track_custom_operation('crs_analysis', 'validation', auto_log_result=True)
        def validate_document(doc):
            # your code
            return {'valid': True, 'score': 0.95}
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs):
            start_time = time.time()
            
            params = {}
            if auto_log_params:
                # Log function arguments as parameters
                params['function'] = func.__name__
                if args:
                    params['args_count'] = len(args)
                if kwargs:
                    for k, v in kwargs.items():
                        if isinstance(v, (str, int, float, bool)):
                            params[k] = v
            
            try:
                result = func(*args, **kwargs)
                
                execution_time = time.time() - start_time
                metrics = {
                    'execution_time_seconds': execution_time,
                    'success': 1
                }
                
                # Auto-extract metrics from result
                if auto_log_result and isinstance(result, dict):
                    for k, v in result.items():
                        if isinstance(v, (int, float)) and k not in ['id', 'count']:
                            metrics[k] = v
                
                mlflow_tracker.log_experiment_result(
                    feature=feature,
                    operation=operation,
                    params=params,
                    metrics=metrics,
                    tags={'custom_operation': 'true'}
                )
                
                return result
                
            except Exception as e:
                execution_time = time.time() - start_time
                mlflow_tracker.log_experiment_result(
                    feature=feature,
                    operation=operation,
                    params=params,
                    metrics={
                        'execution_time_seconds': execution_time,
                        'success': 0
                    },
                    tags={'error': str(e)}
                )
                raise
        
        return wrapper
    return decorator
