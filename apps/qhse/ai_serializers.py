"""
QHSE AI Serializers - REST API serializers for AI/ML endpoints
"""
from rest_framework import serializers
from decimal import Decimal
from .models import QHSERunningProject, QHSESpotCheckRegister


class RiskPredictionSerializer(serializers.Serializer):
    """Serializer for risk prediction response"""
    project_no = serializers.CharField(source='project.project_no', read_only=True)
    project_title = serializers.CharField(source='project.project_title', read_only=True)
    risk_score = serializers.FloatField()
    risk_category = serializers.CharField()
    risk_label = serializers.CharField()
    risk_color = serializers.CharField()
    confidence = serializers.FloatField()
    recommendations = serializers.ListField(child=serializers.CharField())
    risk_factors = serializers.ListField(child=serializers.DictField())
    model_version = serializers.CharField()
    prediction_timestamp = serializers.CharField()


class CARClassificationRequestSerializer(serializers.Serializer):
    """Serializer for CAR classification request"""
    car_text = serializers.CharField(required=True, max_length=5000)
    context = serializers.DictField(required=False, default=dict)


class CARClassificationResponseSerializer(serializers.Serializer):
    """Serializer for CAR classification response"""
    category = serializers.CharField()
    label = serializers.CharField()
    severity = serializers.CharField()
    estimated_resolution_days = serializers.IntegerField()
    confidence = serializers.FloatField()
    keywords_matched = serializers.ListField(child=serializers.CharField())
    recommended_actions = serializers.ListField(child=serializers.CharField())
    model_version = serializers.CharField()
    classification_timestamp = serializers.CharField()


class ManhourPredictionRequestSerializer(serializers.Serializer):
    """Serializer for manhour prediction request"""
    estimated_duration_days = serializers.IntegerField(required=True, min_value=1)
    complexity = serializers.ChoiceField(
        choices=['simple', 'moderate', 'complex', 'very_complex'],
        default='moderate'
    )
    project_type = serializers.CharField(required=False, max_length=100)
    scope = serializers.CharField(required=False, max_length=500)


class ManhourPredictionResponseSerializer(serializers.Serializer):
    """Serializer for manhour prediction response"""
    predicted_manhours = serializers.FloatField()
    predicted_with_buffer = serializers.FloatField()
    buffer_percentage = serializers.FloatField()
    complexity_factor = serializers.FloatField()
    confidence = serializers.FloatField()
    breakdown = serializers.DictField()
    model_version = serializers.CharField()
    prediction_timestamp = serializers.CharField()


class AnomalyDetectionResponseSerializer(serializers.Serializer):
    """Serializer for anomaly detection response"""
    anomalies_detected = serializers.BooleanField()
    anomaly_count = serializers.IntegerField()
    anomalies = serializers.ListField(child=serializers.DictField())
    confidence = serializers.FloatField()
    model_version = serializers.CharField()
    detection_timestamp = serializers.CharField()


class ProjectRiskSummarySerializer(serializers.Serializer):
    """Serializer for project risk summary"""
    project_no = serializers.CharField()
    project_title = serializers.CharField()
    risk_score = serializers.FloatField()
    risk_category = serializers.CharField()
    risk_label = serializers.CharField()
    risk_color = serializers.CharField()
    confidence = serializers.FloatField()


class AIInsightsSerializer(serializers.Serializer):
    """Serializer for overall AI insights dashboard"""
    total_projects_analyzed = serializers.IntegerField()
    high_risk_projects = serializers.IntegerField()
    critical_risk_projects = serializers.IntegerField()
    average_risk_score = serializers.FloatField()
    total_open_cars = serializers.IntegerField()
    total_open_observations = serializers.IntegerField()
    anomalies_detected_count = serializers.IntegerField()
    top_risk_projects = ProjectRiskSummarySerializer(many=True)
    risk_distribution = serializers.DictField()
    recent_predictions = serializers.ListField(child=serializers.DictField())
    model_performance = serializers.DictField()


class QHSECopilotRequestSerializer(serializers.Serializer):
    """Serializer for QHSE Copilot chat request"""
    query = serializers.CharField(required=True, max_length=2000)
    project_no = serializers.CharField(required=False, allow_blank=True)
    context = serializers.DictField(required=False, default=dict)
    conversation_id = serializers.CharField(required=False, allow_blank=True)


class QHSECopilotResponseSerializer(serializers.Serializer):
    """Serializer for QHSE Copilot chat response"""
    response = serializers.CharField()
    confidence = serializers.FloatField()
    sources = serializers.ListField(child=serializers.DictField())
    suggested_actions = serializers.ListField(child=serializers.CharField())
    related_projects = serializers.ListField(child=serializers.CharField())
    conversation_id = serializers.CharField()
    timestamp = serializers.CharField()


class SafetyIncidentPredictionRequestSerializer(serializers.Serializer):
    """Serializer for safety incident prediction request"""
    project_type = serializers.CharField(required=True, max_length=100)
    work_environment = serializers.ChoiceField(
        choices=['indoor', 'outdoor', 'high_altitude', 'confined_space', 'underwater'],
        required=True
    )
    team_size = serializers.IntegerField(required=True, min_value=1)
    average_experience_years = serializers.FloatField(required=True, min_value=0)
    training_completion_rate = serializers.FloatField(required=True, min_value=0, max_value=100)
    ppe_compliance_rate = serializers.FloatField(required=True, min_value=0, max_value=100)
    near_miss_count = serializers.IntegerField(default=0, min_value=0)


class SafetyIncidentPredictionResponseSerializer(serializers.Serializer):
    """Serializer for safety incident prediction response"""
    risk_level = serializers.CharField()
    risk_score = serializers.FloatField()
    incident_probability = serializers.FloatField()
    confidence = serializers.FloatField()
    preventive_actions = serializers.ListField(child=serializers.CharField())
    risk_factors = serializers.ListField(child=serializers.DictField())
    recommended_controls = serializers.ListField(child=serializers.CharField())
    model_version = serializers.CharField()
    prediction_timestamp = serializers.CharField()


class EnergyForecastRequestSerializer(serializers.Serializer):
    """Serializer for energy forecasting request"""
    project_no = serializers.CharField(required=True)
    forecast_horizon_days = serializers.ChoiceField(
        choices=[7, 30, 90],
        default=30
    )


class EnergyForecastResponseSerializer(serializers.Serializer):
    """Serializer for energy forecasting response"""
    project_no = serializers.CharField()
    forecast_horizon_days = serializers.IntegerField()
    forecasted_values = serializers.ListField(child=serializers.DictField())
    total_forecasted_consumption = serializers.FloatField()
    confidence_intervals = serializers.DictField()
    insights = serializers.ListField(child=serializers.CharField())
    model_version = serializers.CharField()
    forecast_timestamp = serializers.CharField()


class DocumentIntelligenceRequestSerializer(serializers.Serializer):
    """Serializer for document intelligence request"""
    document_file = serializers.FileField(required=True)
    document_type = serializers.ChoiceField(
        choices=['audit_report', 'inspection_report', 'car_form', 'quality_plan', 'certificate', 'other'],
        required=False,
        default='other'
    )
    extract_fields = serializers.BooleanField(default=True)


class DocumentIntelligenceResponseSerializer(serializers.Serializer):
    """Serializer for document intelligence response"""
    document_id = serializers.CharField()
    detected_type = serializers.CharField()
    extracted_text = serializers.CharField()
    extracted_fields = serializers.DictField()
    confidence = serializers.FloatField()
    key_entities = serializers.ListField(child=serializers.DictField())
    summary = serializers.CharField()
    recommendations = serializers.ListField(child=serializers.CharField())
    processing_timestamp = serializers.CharField()


class NLPRemarksAnalysisRequestSerializer(serializers.Serializer):
    """Serializer for NLP remarks analysis request"""
    remarks_text = serializers.CharField(required=True, max_length=10000)
    analysis_types = serializers.ListField(
        child=serializers.ChoiceField(choices=['sentiment', 'entities', 'topics', 'summary']),
        default=['sentiment', 'entities']
    )


class NLPRemarksAnalysisResponseSerializer(serializers.Serializer):
    """Serializer for NLP remarks analysis response"""
    sentiment = serializers.DictField(required=False)
    entities = serializers.ListField(child=serializers.DictField(), required=False)
    topics = serializers.ListField(child=serializers.DictField(), required=False)
    summary = serializers.CharField(required=False)
    key_insights = serializers.ListField(child=serializers.CharField())
    confidence = serializers.FloatField()
    model_version = serializers.CharField()
    analysis_timestamp = serializers.CharField()


class ProjectComparisonRequestSerializer(serializers.Serializer):
    """Serializer for project comparison request"""
    project_nos = serializers.ListField(
        child=serializers.CharField(),
        min_length=2,
        max_length=5
    )
    comparison_metrics = serializers.ListField(
        child=serializers.CharField(),
        default=['risk_score', 'kpis', 'cars', 'quality_costs']
    )


class ProjectComparisonResponseSerializer(serializers.Serializer):
    """Serializer for project comparison response"""
    projects = serializers.ListField(child=serializers.DictField())
    comparison_matrix = serializers.DictField()
    insights = serializers.ListField(child=serializers.CharField())
    best_practices = serializers.ListField(child=serializers.DictField())
    recommendations = serializers.ListField(child=serializers.CharField())
    comparison_timestamp = serializers.CharField()


class ModelPerformanceSerializer(serializers.Serializer):
    """Serializer for ML model performance metrics"""
    model_name = serializers.CharField()
    version = serializers.CharField()
    accuracy = serializers.FloatField()
    precision = serializers.FloatField()
    recall = serializers.FloatField()
    f1_score = serializers.FloatField()
    total_predictions = serializers.IntegerField()
    avg_prediction_time_ms = serializers.FloatField()
    last_trained = serializers.DateTimeField()
    last_evaluated = serializers.DateTimeField()
    drift_detected = serializers.BooleanField()


class AIModelStatusSerializer(serializers.Serializer):
    """Serializer for AI model status"""
    model_name = serializers.CharField()
    enabled = serializers.BooleanField()
    loaded = serializers.BooleanField()
    version = serializers.CharField()
    type = serializers.CharField()
    description = serializers.CharField()
    last_prediction = serializers.DateTimeField(allow_null=True)
    performance_metrics = ModelPerformanceSerializer(allow_null=True)
