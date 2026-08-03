"""
MLflow API Views
Provides REST API endpoints for MLflow operations
"""

from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from apps.mlflow_integration.tracker import mlflow_tracker
from apps.rbac.permissions import HasPermission
import mlflow


class MLflowViewSet(viewsets.ViewSet):
    """
    MLflow experiment tracking and model management endpoints
    """
    permission_classes = [IsAuthenticated, HasPermission]
    required_permissions = ['mlflow.view_experiments']
    
    @action(detail=False, methods=['get'])
    def experiments(self, request):
        """List all MLflow experiments"""
        try:
            if not mlflow_tracker.enabled:
                return Response({
                    'enabled': False,
                    'message': 'MLflow tracking is disabled'
                })
            
            experiments = []
            for exp_key, exp_name in mlflow_tracker.experiments.items():
                try:
                    exp = mlflow.get_experiment_by_name(exp_name)
                    if exp:
                        experiments.append({
                            'key': exp_key,
                            'name': exp_name,
                            'experiment_id': exp.experiment_id,
                            'artifact_location': exp.artifact_location,
                            'lifecycle_stage': exp.lifecycle_stage
                        })
                except Exception:
                    pass
            
            return Response({
                'enabled': True,
                'tracking_uri': mlflow_tracker.tracking_uri,
                'experiments': experiments
            })
            
        except Exception as e:
            return Response({
                'error': str(e)
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
    
    @action(detail=False, methods=['get'])
    def runs(self, request):
        """Get recent runs for a feature"""
        feature = request.query_params.get('feature', 'pid_verification')
        limit = int(request.query_params.get('limit', 10))
        
        try:
            runs_df = mlflow_tracker.compare_models(feature, runs_limit=limit)
            
            if runs_df is None or runs_df.empty:
                return Response({
                    'runs': [],
                    'message': 'No runs found'
                })
            
            # Convert DataFrame to JSON-serializable format
            runs = runs_df.to_dict('records')
            
            return Response({
                'feature': feature,
                'runs': runs
            })
            
        except Exception as e:
            return Response({
                'error': str(e)
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
    
    @action(detail=False, methods=['get'])
    def metrics(self, request):
        """Get aggregated metrics for a feature"""
        feature = request.query_params.get('feature')
        
        if not feature:
            return Response({
                'error': 'Feature parameter is required'
            }, status=status.HTTP_400_BAD_REQUEST)
        
        try:
            runs_df = mlflow_tracker.compare_models(feature, runs_limit=50)
            
            if runs_df is None or runs_df.empty:
                return Response({
                    'metrics': {},
                    'message': 'No data available'
                })
            
            # Calculate aggregate metrics
            metrics = {
                'total_runs': len(runs_df),
                'avg_execution_time': runs_df.get('metrics.execution_time_seconds', 0).mean(),
                'success_rate': runs_df.get('metrics.success', 0).mean() * 100,
            }
            
            # Add feature-specific metrics
            if 'metrics.confidence_score' in runs_df.columns:
                metrics['avg_confidence'] = runs_df['metrics.confidence_score'].mean()
            
            if 'metrics.tokens_used' in runs_df.columns:
                metrics['total_tokens'] = runs_df['metrics.tokens_used'].sum()
                metrics['avg_tokens'] = runs_df['metrics.tokens_used'].mean()
            
            if 'metrics.cost_usd' in runs_df.columns:
                metrics['total_cost'] = runs_df['metrics.cost_usd'].sum()
            
            return Response({
                'feature': feature,
                'metrics': metrics
            })
            
        except Exception as e:
            return Response({
                'error': str(e)
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
    
    @action(detail=False, methods=['post'])
    def log_custom(self, request):
        """Manually log a custom experiment"""
        data = request.data
        
        required_fields = ['feature', 'operation', 'params', 'metrics']
        if not all(field in data for field in required_fields):
            return Response({
                'error': f'Missing required fields: {required_fields}'
            }, status=status.HTTP_400_BAD_REQUEST)
        
        try:
            mlflow_tracker.log_experiment_result(
                feature=data['feature'],
                operation=data['operation'],
                params=data['params'],
                metrics=data['metrics'],
                tags=data.get('tags', {}),
                artifacts=data.get('artifacts')
            )
            
            return Response({
                'message': 'Experiment logged successfully'
            })
            
        except Exception as e:
            return Response({
                'error': str(e)
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
    
    @action(detail=False, methods=['get'])
    def health(self, request):
        """Check MLflow service health"""
        try:
            if not mlflow_tracker.enabled:
                return Response({
                    'status': 'disabled',
                    'enabled': False
                })
            
            # Try to ping MLflow tracking server
            mlflow.get_tracking_uri()
            
            return Response({
                'status': 'healthy',
                'enabled': True,
                'tracking_uri': mlflow_tracker.tracking_uri
            })
            
        except Exception as e:
            return Response({
                'status': 'unhealthy',
                'enabled': mlflow_tracker.enabled,
                'error': str(e)
            }, status=status.HTTP_503_SERVICE_UNAVAILABLE)
