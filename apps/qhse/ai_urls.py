"""
QHSE AI URLs - URL routing for AI/ML endpoints
"""
from django.urls import path
from . import ai_views, ai_models_registry

app_name = 'qhse_ai'

urlpatterns = [
    # Risk Prediction
    path(
        'risk-prediction/<str:project_no>/',
        ai_views.predict_project_risk,
        name='predict_project_risk'
    ),
    path(
        'risk-prediction/all/',
        ai_views.predict_all_risks,
        name='predict_all_risks'
    ),
    
    # CAR Classification
    path(
        'car-classification/',
        ai_views.classify_car,
        name='classify_car'
    ),
    
    # Manhour Prediction
    path(
        'manhour-prediction/',
        ai_views.predict_manhours,
        name='predict_manhours'
    ),
    
    # Anomaly Detection
    path(
        'anomaly-detection/<str:project_no>/',
        ai_views.detect_anomalies,
        name='detect_anomalies'
    ),
    
    # AI Insights Dashboard
    path(
        'insights/',
        ai_views.ai_insights_dashboard,
        name='ai_insights_dashboard'
    ),
    
    # Model Status
    path(
        'models/status/',
        ai_views.ai_models_status,
        name='ai_models_status'
    ),
    
    # AI Models Registry (Real-time dynamic tracking)
    path(
        'models/registry/',
        ai_models_registry.get_all_ai_models_registry,
        name='models_registry'
    ),
    path(
        'models/registry/<str:model_id>/',
        ai_models_registry.get_model_detail,
        name='model_detail'
    ),
    
    # NLP Analysis
    path(
        'nlp/analyze-remarks/',
        ai_views.analyze_remarks,
        name='analyze_remarks'
    ),
    
    # Project Comparison
    path(
        'compare-projects/',
        ai_views.compare_projects,
        name='compare_projects'
    ),
]
