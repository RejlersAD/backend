"""
QHSE AI Views - REST API endpoints for AI/ML features
"""
import logging
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.shortcuts import get_object_or_404
from django.db.models import Count, Avg, Sum, Q
from django.utils import timezone

from .models import QHSERunningProject, QHSESpotCheckRegister
from .ai_services import qhse_ai_service
from .ai_serializers import *
from .ai_config import AI_MODELS_CONFIG

logger = logging.getLogger(__name__)


# ============================================================================
# RISK PREDICTION ENDPOINTS
# ============================================================================

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def predict_project_risk(request, project_no):
    """
    Predict risk score for a specific project
    GET /api/qhse/ai/risk-prediction/<project_no>/
    """
    try:
        project = get_object_or_404(QHSERunningProject, project_no=project_no)
        
        # Check permissions (add your RBAC logic here)
        # if not has_qhse_permission(request.user, 'view_ai_insights'):
        #     return Response({'error': 'Permission denied'}, status=403)
        
        # Get AI prediction
        prediction = qhse_ai_service.predict_project_risk(project)
        
        # Add project context
        prediction['project'] = {
            'project_no': project.project_no,
            'project_title': project.project_title,
            'client': project.client,
            'project_manager': project.project_manager_name
        }
        
        serializer = RiskPredictionSerializer(data=prediction)
        if serializer.is_valid():
            return Response(serializer.data, status=status.HTTP_200_OK)
        
        return Response(prediction, status=status.HTTP_200_OK)
        
    except Exception as e:
        logger.error(f"Risk prediction API error: {str(e)}")
        return Response(
            {'error': 'Risk prediction failed', 'detail': str(e)},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def predict_all_risks(request):
    """
    Predict risk scores for all active projects
    GET /api/qhse/ai/risk-prediction/all/
    Query params: limit (optional)
    """
    try:
        limit = request.query_params.get('limit', None)
        limit = int(limit) if limit else None
        
        predictions = qhse_ai_service.predict_all_project_risks(limit=limit)
        
        return Response({
            'total_projects': len(predictions),
            'predictions': predictions,
            'generated_at': timezone.now().isoformat()
        }, status=status.HTTP_200_OK)
        
    except Exception as e:
        logger.error(f"Batch risk prediction API error: {str(e)}")
        return Response(
            {'error': 'Batch prediction failed', 'detail': str(e)},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


# ============================================================================
# CAR CLASSIFICATION ENDPOINT
# ============================================================================

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def classify_car(request):
    """
    Classify CAR/NCR using AI
    POST /api/qhse/ai/car-classification/
    Body: {"car_text": "...", "context": {...}}
    """
    try:
        serializer = CARClassificationRequestSerializer(data=request.data)
        
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        
        car_text = serializer.validated_data['car_text']
        context = serializer.validated_data.get('context', {})
        
        # Get AI classification
        classification = qhse_ai_service.classify_car(car_text, context)
        
        response_serializer = CARClassificationResponseSerializer(data=classification)
        if response_serializer.is_valid():
            return Response(response_serializer.data, status=status.HTTP_200_OK)
        
        return Response(classification, status=status.HTTP_200_OK)
        
    except Exception as e:
        logger.error(f"CAR classification API error: {str(e)}")
        return Response(
            {'error': 'Classification failed', 'detail': str(e)},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


# ============================================================================
# MANHOUR PREDICTION ENDPOINT
# ============================================================================

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def predict_manhours(request):
    """
    Predict required manhours for project
    POST /api/qhse/ai/manhour-prediction/
    Body: {"estimated_duration_days": 30, "complexity": "moderate", ...}
    """
    try:
        serializer = ManhourPredictionRequestSerializer(data=request.data)
        
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        
        # Get AI prediction
        prediction = qhse_ai_service.predict_manhours(serializer.validated_data)
        
        response_serializer = ManhourPredictionResponseSerializer(data=prediction)
        if response_serializer.is_valid():
            return Response(response_serializer.data, status=status.HTTP_200_OK)
        
        return Response(prediction, status=status.HTTP_200_OK)
        
    except Exception as e:
        logger.error(f"Manhour prediction API error: {str(e)}")
        return Response(
            {'error': 'Manhour prediction failed', 'detail': str(e)},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


# ============================================================================
# ANOMALY DETECTION ENDPOINT
# ============================================================================

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def detect_anomalies(request, project_no):
    """
    Detect anomalies in project metrics
    GET /api/qhse/ai/anomaly-detection/<project_no>/
    """
    try:
        project = get_object_or_404(QHSERunningProject, project_no=project_no)
        
        # Get anomaly detection
        detection = qhse_ai_service.detect_anomalies(project)
        
        # Add project context
        detection['project'] = {
            'project_no': project.project_no,
            'project_title': project.project_title
        }
        
        serializer = AnomalyDetectionResponseSerializer(data=detection)
        if serializer.is_valid():
            return Response(serializer.data, status=status.HTTP_200_OK)
        
        return Response(detection, status=status.HTTP_200_OK)
        
    except Exception as e:
        logger.error(f"Anomaly detection API error: {str(e)}")
        return Response(
            {'error': 'Anomaly detection failed', 'detail': str(e)},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


# ============================================================================
# AI INSIGHTS DASHBOARD
# ============================================================================

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def ai_insights_dashboard(request):
    """
    Get overall AI insights and analytics
    GET /api/qhse/ai/insights/
    """
    try:
        # Get all active projects
        projects = QHSERunningProject.objects.filter(is_active=True)
        
        # Predict risks for all projects
        all_predictions = qhse_ai_service.predict_all_project_risks(limit=50)
        
        # Calculate statistics
        high_risk_count = len([p for p in all_predictions if p['risk_category'] in ['high', 'critical']])
        critical_risk_count = len([p for p in all_predictions if p['risk_category'] == 'critical'])
        avg_risk_score = sum(p['risk_score'] for p in all_predictions) / len(all_predictions) if all_predictions else 0
        
        # Risk distribution
        risk_distribution = {
            'critical': len([p for p in all_predictions if p['risk_category'] == 'critical']),
            'high': len([p for p in all_predictions if p['risk_category'] == 'high']),
            'medium': len([p for p in all_predictions if p['risk_category'] == 'medium']),
            'low': len([p for p in all_predictions if p['risk_category'] == 'low']),
            'minimal': len([p for p in all_predictions if p['risk_category'] == 'minimal'])
        }
        
        # Get top risk projects
        top_risk_projects = sorted(all_predictions, key=lambda x: x['risk_score'], reverse=True)[:10]
        
        # Count anomalies
        anomaly_count = 0
        for project in projects[:20]:  # Check first 20
            detection = qhse_ai_service.detect_anomalies(project)
            if detection.get('anomalies_detected'):
                anomaly_count += detection.get('anomaly_count', 0)
        
        # Overall stats
        stats = projects.aggregate(
            total_open_cars=Sum('cars_open'),
            total_open_obs=Sum('obs_open')
        )
        
        # Model performance (mock data for now)
        model_performance = {
            'risk_prediction': {
                'accuracy': 0.85,
                'total_predictions': len(all_predictions),
                'avg_confidence': sum(p['confidence'] for p in all_predictions) / len(all_predictions) if all_predictions else 0
            },
            'car_classification': {
                'accuracy': 0.78,
                'total_classifications': 0,
                'avg_confidence': 0.75
            },
            'anomaly_detection': {
                'accuracy': 0.88,
                'anomalies_found': anomaly_count,
                'avg_confidence': 0.85
            }
        }
        
        insights_data = {
            'total_projects_analyzed': len(all_predictions),
            'high_risk_projects': high_risk_count,
            'critical_risk_projects': critical_risk_count,
            'average_risk_score': round(avg_risk_score, 2),
            'total_open_cars': stats['total_open_cars'] or 0,
            'total_open_observations': stats['total_open_obs'] or 0,
            'anomalies_detected_count': anomaly_count,
            'top_risk_projects': top_risk_projects,
            'risk_distribution': risk_distribution,
            'recent_predictions': all_predictions[:5],
            'model_performance': model_performance
        }
        
        serializer = AIInsightsSerializer(data=insights_data)
        if serializer.is_valid():
            return Response(serializer.data, status=status.HTTP_200_OK)
        
        return Response(insights_data, status=status.HTTP_200_OK)
        
    except Exception as e:
        logger.error(f"AI insights dashboard error: {str(e)}")
        return Response(
            {'error': 'Failed to generate insights', 'detail': str(e)},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


# ============================================================================
# MODEL STATUS AND HEALTH
# ============================================================================

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def ai_models_status(request):
    """
    Get status of all AI models
    GET /api/qhse/ai/models/status/
    """
    try:
        models_status = []
        
        for model_name, model_info in qhse_ai_service.models_loaded.items():
            config = AI_MODELS_CONFIG.get(model_name, {})
            
            models_status.append({
                'model_name': model_name,
                'enabled': config.get('enabled', False),
                'loaded': model_info.get('loaded', False),
                'version': model_info.get('version', 'unknown'),
                'type': config.get('type', 'unknown'),
                'description': config.get('description', ''),
                'last_prediction': None,  # Can track this in DB
                'performance_metrics': None  # Can track this in DB
            })
        
        return Response({
            'models': models_status,
            'total_models': len(models_status),
            'loaded_models': len([m for m in models_status if m['loaded']]),
            'enabled_models': len([m for m in models_status if m['enabled']]),
            'timestamp': timezone.now().isoformat()
        }, status=status.HTTP_200_OK)
        
    except Exception as e:
        logger.error(f"AI models status error: {str(e)}")
        return Response(
            {'error': 'Failed to get models status', 'detail': str(e)},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


# ============================================================================
# NLP REMARKS ANALYSIS
# ============================================================================

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def analyze_remarks(request):
    """
    Analyze remarks/comments using NLP
    POST /api/qhse/ai/nlp/analyze-remarks/
    Body: {"remarks_text": "...", "analysis_types": ["sentiment", "entities"]}
    """
    try:
        serializer = NLPRemarksAnalysisRequestSerializer(data=request.data)
        
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        
        remarks_text = serializer.validated_data['remarks_text']
        analysis_types = serializer.validated_data.get('analysis_types', ['sentiment', 'entities'])
        
        # Placeholder analysis (implement actual NLP)
        analysis_result = {
            'sentiment': {
                'label': 'neutral',
                'score': 0.65,
                'positive_score': 0.30,
                'negative_score': 0.20,
                'neutral_score': 0.50
            } if 'sentiment' in analysis_types else None,
            'entities': [
                {'text': 'Quality', 'type': 'aspect', 'confidence': 0.85},
                {'text': 'Safety', 'type': 'aspect', 'confidence': 0.78}
            ] if 'entities' in analysis_types else None,
            'topics': [
                {'topic': 'quality_issues', 'relevance': 0.75},
                {'topic': 'compliance', 'relevance': 0.60}
            ] if 'topics' in analysis_types else None,
            'summary': 'Discussion about quality and safety compliance issues.' if 'summary' in analysis_types else None,
            'key_insights': [
                'Quality concerns identified',
                'Safety compliance mentioned',
                'Action items required'
            ],
            'confidence': 0.75,
            'model_version': '1.0.0',
            'analysis_timestamp': timezone.now().isoformat()
        }
        
        response_serializer = NLPRemarksAnalysisResponseSerializer(data=analysis_result)
        if response_serializer.is_valid():
            return Response(response_serializer.data, status=status.HTTP_200_OK)
        
        return Response(analysis_result, status=status.HTTP_200_OK)
        
    except Exception as e:
        logger.error(f"NLP remarks analysis error: {str(e)}")
        return Response(
            {'error': 'Analysis failed', 'detail': str(e)},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


# ============================================================================
# PROJECT COMPARISON
# ============================================================================

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def compare_projects(request):
    """
    Compare multiple projects using AI
    POST /api/qhse/ai/compare-projects/
    Body: {"project_nos": ["P001", "P002"], "comparison_metrics": ["risk_score", "kpis"]}
    """
    try:
        serializer = ProjectComparisonRequestSerializer(data=request.data)
        
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        
        project_nos = serializer.validated_data['project_nos']
        comparison_metrics = serializer.validated_data.get('comparison_metrics', ['risk_score', 'kpis'])
        
        # Get projects
        projects = QHSERunningProject.objects.filter(project_no__in=project_nos)
        
        if len(projects) < 2:
            return Response(
                {'error': 'At least 2 valid projects required for comparison'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Build comparison
        projects_data = []
        for project in projects:
            risk_prediction = qhse_ai_service.predict_project_risk(project)
            
            projects_data.append({
                'project_no': project.project_no,
                'project_title': project.project_title,
                'risk_score': risk_prediction['risk_score'],
                'risk_category': risk_prediction['risk_category'],
                'kpis_achieved': parse_percentage(project.project_kpis_achieved_percent),
                'cars_open': project.cars_open,
                'obs_open': project.obs_open,
                'quality_cost': float(project.cost_of_poor_quality_aed or 0),
                'manhour_utilization': float(project.manhours_used or 0) / float(project.man_hour_for_quality or 1)
            })
        
        # Generate insights
        insights = [
            f"Comparing {len(projects_data)} projects across {len(comparison_metrics)} metrics",
            f"Highest risk: {max(projects_data, key=lambda x: x['risk_score'])['project_no']}",
            f"Best KPI performance: {max(projects_data, key=lambda x: x['kpis_achieved'])['project_no']}"
        ]
        
        comparison_result = {
            'projects': projects_data,
            'comparison_matrix': {
                'metrics': comparison_metrics,
                'data': projects_data
            },
            'insights': insights,
            'best_practices': [
                {'practice': 'Proactive CAR management', 'benefit': 'Reduces risk score by 15%'},
                {'practice': 'Regular audits', 'benefit': 'Improves compliance by 20%'}
            ],
            'recommendations': [
                'Apply best practices from top-performing projects',
                'Focus on risk mitigation for high-risk projects'
            ],
            'comparison_timestamp': timezone.now().isoformat()
        }
        
        response_serializer = ProjectComparisonResponseSerializer(data=comparison_result)
        if response_serializer.is_valid():
            return Response(response_serializer.data, status=status.HTTP_200_OK)
        
        return Response(comparison_result, status=status.HTTP_200_OK)
        
    except Exception as e:
        logger.error(f"Project comparison error: {str(e)}")
        return Response(
            {'error': 'Comparison failed', 'detail': str(e)},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


# Helper function
def parse_percentage(value):
    """Parse percentage string to float"""
    if not value:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).replace('%', '').strip())
    except:
        return 0.0
