"""
MLflow Tracker - Central model orchestration and experiment tracking
Provides decorators and utilities to track AI operations across all features
"""

import mlflow
import mlflow.openai
from functools import wraps
import time
import os
from typing import Dict, Any, Optional, Callable
from django.conf import settings
import json


class MLflowTracker:
    """Central MLflow tracking manager"""
    
    def __init__(self):
        self.tracking_uri = os.getenv('MLFLOW_TRACKING_URI', 'http://mlflow:5000')
        self.enabled = os.getenv('MLFLOW_ENABLED', 'true').lower() == 'true'
        self.experiments = {
            'pid_verification': 'P&ID Design Verification',
            'pfd_conversion': 'PFD to P&ID Conversion',
            'crs_analysis': 'CRS Document Analysis',
            'visual_generation': 'Visual Diagram Generation'
        }
        
    def initialize(self):
        """Initialize MLflow tracking"""
        if not self.enabled:
            print('[MLflow] ⏭️  Tracking disabled')
            return
            
        try:
            mlflow.set_tracking_uri(self.tracking_uri)
            
            # Create experiments for each feature
            for exp_key, exp_name in self.experiments.items():
                try:
                    mlflow.create_experiment(exp_name)
                    print(f'[MLflow] 📊 Created experiment: {exp_name}')
                except Exception:
                    # Experiment already exists
                    pass
                    
            print(f'[MLflow] ✅ Initialized with URI: {self.tracking_uri}')
        except Exception as e:
            print(f'[MLflow] ⚠️  Initialization failed: {e}')
            self.enabled = False
    
    def get_experiment_name(self, feature: str) -> str:
        """Get experiment name for a feature"""
        return self.experiments.get(feature, 'General AI Operations')
    
    def track_openai_call(
        self,
        feature: str,
        operation: str,
        model: str,
        **kwargs
    ) -> Callable:
        """
        Decorator to track OpenAI API calls
        
        Usage:
            @mlflow_tracker.track_openai_call('pid_verification', 'image_analysis', 'gpt-4o-vision')
            def analyze_pid(image_data):
                # your code
                return result
        """
        def decorator(func: Callable) -> Callable:
            @wraps(func)
            def wrapper(*args, **func_kwargs):
                if not self.enabled:
                    return func(*args, **func_kwargs)
                
                experiment_name = self.get_experiment_name(feature)
                mlflow.set_experiment(experiment_name)
                
                with mlflow.start_run(run_name=f"{operation}_{int(time.time())}"):
                    # Log parameters
                    mlflow.log_param('feature', feature)
                    mlflow.log_param('operation', operation)
                    mlflow.log_param('model', model)
                    mlflow.log_param('function', func.__name__)
                    
                    # Log additional kwargs as parameters
                    for key, value in kwargs.items():
                        if isinstance(value, (str, int, float, bool)):
                            mlflow.log_param(key, value)
                    
                    # Track execution time
                    start_time = time.time()
                    
                    try:
                        # Execute the function
                        result = func(*args, **func_kwargs)
                        
                        # Log execution metrics
                        execution_time = time.time() - start_time
                        mlflow.log_metric('execution_time_seconds', execution_time)
                        mlflow.log_metric('success', 1)
                        
                        # Log result metadata if available
                        if isinstance(result, dict):
                            if 'tokens_used' in result:
                                mlflow.log_metric('tokens_used', result['tokens_used'])
                            if 'cost' in result:
                                mlflow.log_metric('cost_usd', result['cost'])
                            if 'confidence_score' in result:
                                mlflow.log_metric('confidence_score', result['confidence_score'])
                        
                        mlflow.set_tag('status', 'success')
                        
                        return result
                        
                    except Exception as e:
                        # Log failure
                        execution_time = time.time() - start_time
                        mlflow.log_metric('execution_time_seconds', execution_time)
                        mlflow.log_metric('success', 0)
                        mlflow.set_tag('status', 'failed')
                        mlflow.set_tag('error', str(e))
                        raise
                
            return wrapper
        return decorator
    
    def log_experiment_result(
        self,
        feature: str,
        operation: str,
        params: Dict[str, Any],
        metrics: Dict[str, float],
        artifacts: Optional[Dict[str, str]] = None,
        tags: Optional[Dict[str, str]] = None
    ):
        """
        Manually log experiment results
        
        Args:
            feature: Feature name (pid_verification, pfd_conversion, crs_analysis)
            operation: Operation name (e.g., 'extraction', 'generation', 'validation')
            params: Dictionary of parameters
            metrics: Dictionary of metrics (must be numeric)
            artifacts: Dictionary of artifact paths to log
            tags: Dictionary of tags
        """
        if not self.enabled:
            return
        
        try:
            experiment_name = self.get_experiment_name(feature)
            mlflow.set_experiment(experiment_name)
            
            with mlflow.start_run(run_name=f"{operation}_{int(time.time())}"):
                # Log parameters
                for key, value in params.items():
                    if isinstance(value, (str, int, float, bool)):
                        mlflow.log_param(key, value)
                    elif isinstance(value, (dict, list)):
                        mlflow.log_param(key, json.dumps(value))
                
                # Log metrics
                for key, value in metrics.items():
                    if isinstance(value, (int, float)):
                        mlflow.log_metric(key, value)
                
                # Log tags
                if tags:
                    for key, value in tags.items():
                        mlflow.set_tag(key, str(value))
                
                # Log artifacts
                if artifacts:
                    for name, path in artifacts.items():
                        if os.path.exists(path):
                            mlflow.log_artifact(path, artifact_path=name)
                            
        except Exception as e:
            print(f'[MLflow] ⚠️  Failed to log experiment: {e}')
    
    def track_model_performance(
        self,
        feature: str,
        model_name: str,
        accuracy: float,
        precision: Optional[float] = None,
        recall: Optional[float] = None,
        f1_score: Optional[float] = None,
        **kwargs
    ):
        """
        Track model performance metrics
        
        Args:
            feature: Feature name
            model_name: Model identifier
            accuracy: Accuracy score (0-1 or 0-100)
            precision: Precision score
            recall: Recall score
            f1_score: F1 score
            **kwargs: Additional metrics
        """
        metrics = {
            'accuracy': accuracy,
        }
        
        if precision is not None:
            metrics['precision'] = precision
        if recall is not None:
            metrics['recall'] = recall
        if f1_score is not None:
            metrics['f1_score'] = f1_score
        
        metrics.update(kwargs)
        
        self.log_experiment_result(
            feature=feature,
            operation='model_evaluation',
            params={'model_name': model_name},
            metrics=metrics,
            tags={'evaluation': 'true'}
        )
    
    def compare_models(self, feature: str, runs_limit: int = 10):
        """
        Compare recent model runs for a feature
        
        Returns:
            DataFrame with run comparisons
        """
        if not self.enabled:
            return None
        
        try:
            experiment_name = self.get_experiment_name(feature)
            experiment = mlflow.get_experiment_by_name(experiment_name)
            
            if not experiment:
                return None
            
            runs = mlflow.search_runs(
                experiment_ids=[experiment.experiment_id],
                max_results=runs_limit,
                order_by=["start_time DESC"]
            )
            
            return runs
            
        except Exception as e:
            print(f'[MLflow] ⚠️  Failed to compare models: {e}')
            return None


# Global tracker instance
mlflow_tracker = MLflowTracker()
