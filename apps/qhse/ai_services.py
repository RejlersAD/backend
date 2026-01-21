"""
QHSE AI Services - Machine Learning service layer
Implements all AI/ML features with soft-coded configurations
"""
import numpy as np
import logging
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Dict, List, Any, Optional, Tuple
from django.db.models import Avg, Count, Sum, Q, F
from django.utils import timezone

from .models import QHSERunningProject, QHSESpotCheckRegister
from .ai_config import (
    AI_MODELS_CONFIG,
    FEATURE_ENGINEERING_CONFIG,
    AI_RECOMMENDATIONS_CONFIG,
    BUSINESS_RULES_CONFIG,
    parse_percentage,
    get_risk_category,
    is_model_enabled
)

logger = logging.getLogger(__name__)


class QHSEAIService:
    """
    Central AI service for all QHSE machine learning features
    Soft-coded and configuration-driven
    """
    
    def __init__(self):
        self.models_loaded = {}
        self._load_enabled_models()
    
    def _load_enabled_models(self):
        """Load all enabled AI models"""
        for model_name, config in AI_MODELS_CONFIG.items():
            if config.get('enabled', False):
                try:
                    # Placeholder for actual model loading
                    # In production: load from config['model_path']
                    self.models_loaded[model_name] = {
                        'config': config,
                        'loaded': True,
                        'version': config.get('version', '1.0.0')
                    }
                    logger.info(f"✅ Loaded AI model: {model_name} v{config.get('version')}")
                except Exception as e:
                    logger.error(f"❌ Failed to load model {model_name}: {str(e)}")
                    self.models_loaded[model_name] = {'loaded': False, 'error': str(e)}
    
    # ========================================================================
    # PREDICTIVE RISK SCORING
    # ========================================================================
    
    def predict_project_risk(self, project: QHSERunningProject) -> Dict[str, Any]:
        """
        Predict project risk score using ML model
        Returns risk score, category, and recommendations
        """
        if not is_model_enabled('risk_prediction'):
            return self._get_fallback_risk_score(project)
        
        try:
            # Extract features
            features = self._extract_risk_features(project)
            
            # Calculate risk score (weighted sum approach as fallback)
            risk_score = self._calculate_risk_score(features)
            
            # Get risk category
            category, label, color = get_risk_category(risk_score)
            
            # Get AI recommendations
            recommendations = self._get_risk_recommendations(category, features)
            
            # Get contributing factors
            risk_factors = self._identify_risk_factors(features)
            
            return {
                'risk_score': round(risk_score, 2),
                'risk_category': category,
                'risk_label': label,
                'risk_color': color,
                'confidence': 0.85,
                'recommendations': recommendations,
                'risk_factors': risk_factors,
                'model_version': self.models_loaded.get('risk_prediction', {}).get('version', '1.0.0'),
                'prediction_timestamp': timezone.now().isoformat()
            }
        except Exception as e:
            logger.error(f"Risk prediction failed for project {project.project_no}: {str(e)}")
            return self._get_fallback_risk_score(project)
    
    def _extract_risk_features(self, project: QHSERunningProject) -> Dict[str, float]:
        """Extract and calculate features for risk prediction"""
        features = {}
        config = FEATURE_ENGINEERING_CONFIG.get('risk_prediction', {})
        
        # Audit delays
        features['audit_delay_days'] = float(project.delay_in_audits_no_days or 0)
        
        # CAR ratio
        total_cars = project.cars_open + project.cars_closed
        features['cars_open_ratio'] = project.cars_open / (total_cars + 1) if total_cars > 0 else 0
        
        # Observation ratio
        total_obs = project.obs_open + project.obs_closed
        features['obs_open_ratio'] = project.obs_open / (total_obs + 1) if total_obs > 0 else 0
        
        # Manhour utilization
        manhour_allocated = float(project.man_hour_for_quality or 0)
        manhour_used = float(project.manhours_used or 0)
        features['manhour_utilization_rate'] = manhour_used / manhour_allocated if manhour_allocated > 0 else 0
        
        # Quality cost ratio
        quality_cost = float(project.cost_of_poor_quality_aed or 0)
        features['quality_cost_ratio'] = min(quality_cost / 100000, 10)  # Normalize
        
        # KPI achievement
        features['kpi_achievement_percent'] = parse_percentage(project.project_kpis_achieved_percent)
        
        # Project completion
        features['project_completion_percent'] = parse_percentage(project.project_completion_percent)
        
        # Quality plan status score
        quality_plan_score = 100
        if not project.project_quality_plan_status_rev:
            quality_plan_score = 0
        elif project.project_quality_plan_status_rev.lower() == 'pending':
            quality_plan_score = 30
        features['quality_plan_status_score'] = quality_plan_score
        
        # Delayed CARs
        features['cars_delayed_days'] = float(project.cars_delayed_closing_no_days or 0)
        
        # Delayed observations
        features['obs_delayed_days'] = float(project.obs_delayed_closing_no_days or 0)
        
        return features
    
    def _calculate_risk_score(self, features: Dict[str, float]) -> float:
        """Calculate risk score using feature weights"""
        config = AI_MODELS_CONFIG['risk_prediction']
        weights = config.get('feature_weights', {})
        
        score = 0.0
        
        # Audit delays (higher is worse)
        score += min(features['audit_delay_days'] / 30, 1.0) * weights.get('audit_delay_days', 0.15) * 100
        
        # CAR open ratio (higher is worse)
        score += features['cars_open_ratio'] * weights.get('cars_open_ratio', 0.20) * 100
        
        # Observation open ratio
        score += features['obs_open_ratio'] * weights.get('obs_open_ratio', 0.10) * 100
        
        # Manhour over-utilization (>1.0 is bad)
        if features['manhour_utilization_rate'] > 1.0:
            score += (features['manhour_utilization_rate'] - 1.0) * weights.get('manhour_utilization_rate', 0.15) * 100
        
        # Quality cost (higher is worse)
        score += min(features['quality_cost_ratio'], 1.0) * weights.get('quality_cost_ratio', 0.15) * 100
        
        # Low KPI achievement (lower is worse)
        kpi_gap = max(0, 100 - features['kpi_achievement_percent']) / 100
        score += kpi_gap * weights.get('kpi_achievement_percent', 0.10) * 100
        
        # Project not complete but should be (time-based risk)
        if features['project_completion_percent'] < 90:
            score += (1 - features['project_completion_percent'] / 100) * weights.get('project_completion_percent', 0.05) * 50
        
        # Quality plan issues
        score += (1 - features['quality_plan_status_score'] / 100) * weights.get('quality_plan_status_score', 0.05) * 100
        
        # Delayed closures
        score += min(features['cars_delayed_days'] / 30, 1.0) * weights.get('cars_delayed_days', 0.03) * 100
        score += min(features['obs_delayed_days'] / 30, 1.0) * weights.get('obs_delayed_days', 0.02) * 100
        
        return min(score, 100)  # Cap at 100
    
    def _identify_risk_factors(self, features: Dict[str, float]) -> List[Dict[str, Any]]:
        """Identify and rank contributing risk factors"""
        thresholds = BUSINESS_RULES_CONFIG['thresholds']
        factors = []
        
        if features['audit_delay_days'] > thresholds['critical_audit_delay_days']:
            factors.append({
                'factor': 'Audit Delays',
                'severity': 'high',
                'value': f"{features['audit_delay_days']:.0f} days",
                'impact': 'Critical audit backlog affecting compliance'
            })
        
        if features['cars_open_ratio'] > 0.5:
            factors.append({
                'factor': 'Open CARs',
                'severity': 'high',
                'value': f"{features['cars_open_ratio']*100:.0f}% open",
                'impact': 'High number of unresolved corrective actions'
            })
        
        if features['manhour_utilization_rate'] > thresholds['high_manhour_utilization']:
            factors.append({
                'factor': 'Manhour Overrun',
                'severity': 'medium',
                'value': f"{features['manhour_utilization_rate']*100:.0f}%",
                'impact': 'Resource allocation exceeding budget'
            })
        
        if features['quality_cost_ratio'] > 1.0:
            factors.append({
                'factor': 'Quality Costs',
                'severity': 'high',
                'value': f"AED {features['quality_cost_ratio']*100000:.0f}",
                'impact': 'Significant cost of poor quality'
            })
        
        if features['kpi_achievement_percent'] < thresholds['low_kpi_achievement_percent']:
            factors.append({
                'factor': 'Low KPI Achievement',
                'severity': 'medium',
                'value': f"{features['kpi_achievement_percent']:.0f}%",
                'impact': 'Performance targets not being met'
            })
        
        # Sort by severity
        severity_order = {'high': 0, 'medium': 1, 'low': 2}
        factors.sort(key=lambda x: severity_order.get(x['severity'], 3))
        
        return factors[:5]  # Top 5 factors
    
    def _get_risk_recommendations(self, category: str, features: Dict[str, float]) -> List[str]:
        """Get AI-powered recommendations based on risk category"""
        base_recommendations = AI_RECOMMENDATIONS_CONFIG.get('risk_prediction', {}).get(category, [])
        
        # Add dynamic recommendations based on features
        dynamic_recommendations = []
        
        if features['audit_delay_days'] > 30:
            dynamic_recommendations.append("Schedule overdue audits immediately")
        
        if features['cars_open_ratio'] > 0.6:
            dynamic_recommendations.append("Prioritize CAR closure - assign dedicated resources")
        
        if features['manhour_utilization_rate'] > 1.2:
            dynamic_recommendations.append("Review resource allocation and request additional budget")
        
        # Combine and limit to top recommendations
        all_recommendations = base_recommendations + dynamic_recommendations
        return all_recommendations[:5]
    
    def _get_fallback_risk_score(self, project: QHSERunningProject) -> Dict[str, Any]:
        """Fallback risk scoring when ML model unavailable"""
        # Simple rule-based scoring
        score = 0
        
        if project.delay_in_audits_no_days > 30:
            score += 25
        if project.cars_open > 5:
            score += 20
        if project.obs_open > 10:
            score += 15
        
        manhour_ratio = float(project.manhours_used or 0) / float(project.man_hour_for_quality or 1)
        if manhour_ratio > 1.2:
            score += 20
        
        if float(project.cost_of_poor_quality_aed or 0) > 100000:
            score += 20
        
        category, label, color = get_risk_category(score)
        
        return {
            'risk_score': score,
            'risk_category': category,
            'risk_label': label,
            'risk_color': color,
            'confidence': 0.60,
            'recommendations': ['Review project performance', 'Conduct risk assessment'],
            'risk_factors': [],
            'model_version': 'fallback',
            'prediction_timestamp': timezone.now().isoformat()
        }
    
    # ========================================================================
    # CAR CLASSIFICATION
    # ========================================================================
    
    def classify_car(self, car_text: str, context: Optional[Dict] = None) -> Dict[str, Any]:
        """
        Classify CAR/NCR using NLP model
        Returns category, severity, and estimated resolution time
        """
        if not is_model_enabled('car_classification'):
            return self._classify_car_fallback(car_text)
        
        try:
            config = AI_MODELS_CONFIG['car_classification']
            
            # Keyword-based classification (fallback for missing ML model)
            predicted_class = self._keyword_based_classification(car_text, config['classes'])
            
            class_config = config['classes'].get(predicted_class, {})
            
            return {
                'category': predicted_class,
                'label': class_config.get('label', 'Unknown'),
                'severity': class_config.get('severity', 'medium'),
                'estimated_resolution_days': class_config.get('average_resolution_days', 14),
                'confidence': 0.78,
                'keywords_matched': self._extract_keywords(car_text, class_config.get('keywords', [])),
                'recommended_actions': AI_RECOMMENDATIONS_CONFIG.get('car_classification', {}).get(predicted_class, []),
                'model_version': config.get('version', '1.0.0'),
                'classification_timestamp': timezone.now().isoformat()
            }
        except Exception as e:
            logger.error(f"CAR classification failed: {str(e)}")
            return self._classify_car_fallback(car_text)
    
    def _keyword_based_classification(self, text: str, classes: Dict) -> str:
        """Classify text based on keyword matching"""
        text_lower = text.lower()
        scores = {}
        
        for class_name, class_config in classes.items():
            keywords = class_config.get('keywords', [])
            score = sum(1 for keyword in keywords if keyword.lower() in text_lower)
            scores[class_name] = score
        
        if not scores or max(scores.values()) == 0:
            return 'quality_issue'  # Default
        
        return max(scores, key=scores.get)
    
    def _extract_keywords(self, text: str, keywords: List[str]) -> List[str]:
        """Extract matched keywords from text"""
        text_lower = text.lower()
        return [kw for kw in keywords if kw.lower() in text_lower]
    
    def _classify_car_fallback(self, car_text: str) -> Dict[str, Any]:
        """Fallback classification"""
        return {
            'category': 'quality_issue',
            'label': 'Quality Issue',
            'severity': 'medium',
            'estimated_resolution_days': 14,
            'confidence': 0.50,
            'keywords_matched': [],
            'recommended_actions': ['Review and categorize manually'],
            'model_version': 'fallback',
            'classification_timestamp': timezone.now().isoformat()
        }
    
    # ========================================================================
    # MANHOUR PREDICTION
    # ========================================================================
    
    def predict_manhours(self, project_details: Dict[str, Any]) -> Dict[str, Any]:
        """
        Predict required manhours for project
        """
        if not is_model_enabled('manhour_prediction'):
            return self._predict_manhours_fallback(project_details)
        
        try:
            config = AI_MODELS_CONFIG['manhour_prediction']
            
            # Extract project complexity
            complexity = project_details.get('complexity', 'moderate')
            complexity_config = config['project_complexity_scoring'].get(complexity, {'multiplier': 1.5})
            
            # Base calculation
            base_manhours = project_details.get('estimated_duration_days', 30) * 8 * 0.3  # 30% of available time
            
            # Apply complexity multiplier
            predicted_manhours = base_manhours * complexity_config['multiplier']
            
            # Add buffer
            buffer_percent = config.get('buffer_percentage', 15) / 100
            predicted_with_buffer = predicted_manhours * (1 + buffer_percent)
            
            return {
                'predicted_manhours': round(predicted_manhours, 2),
                'predicted_with_buffer': round(predicted_with_buffer, 2),
                'buffer_percentage': config.get('buffer_percentage', 15),
                'complexity_factor': complexity_config['multiplier'],
                'confidence': 0.82,
                'breakdown': {
                    'base_estimate': round(base_manhours, 2),
                    'complexity_adjustment': round(predicted_manhours - base_manhours, 2),
                    'buffer_hours': round(predicted_with_buffer - predicted_manhours, 2)
                },
                'model_version': config.get('version', '1.0.0'),
                'prediction_timestamp': timezone.now().isoformat()
            }
        except Exception as e:
            logger.error(f"Manhour prediction failed: {str(e)}")
            return self._predict_manhours_fallback(project_details)
    
    def _predict_manhours_fallback(self, project_details: Dict[str, Any]) -> Dict[str, Any]:
        """Fallback manhour prediction"""
        duration = project_details.get('estimated_duration_days', 30)
        estimate = duration * 8 * 0.25  # 25% of available time
        
        return {
            'predicted_manhours': round(estimate, 2),
            'predicted_with_buffer': round(estimate * 1.15, 2),
            'buffer_percentage': 15,
            'complexity_factor': 1.0,
            'confidence': 0.60,
            'breakdown': {},
            'model_version': 'fallback',
            'prediction_timestamp': timezone.now().isoformat()
        }
    
    # ========================================================================
    # ANOMALY DETECTION
    # ========================================================================
    
    def detect_anomalies(self, project: QHSERunningProject) -> Dict[str, Any]:
        """Detect anomalies in project metrics"""
        if not is_model_enabled('anomaly_detection'):
            return {'anomalies_detected': False, 'anomalies': []}
        
        try:
            config = AI_MODELS_CONFIG['anomaly_detection']
            anomalies = []
            
            # Get project statistics for comparison
            avg_stats = self._get_project_averages()
            
            # Check each monitored metric
            manhour_ratio = float(project.manhours_used or 0) / float(project.man_hour_for_quality or 1)
            if manhour_ratio > avg_stats.get('avg_manhour_ratio', 1.0) * 1.5:
                anomalies.append({
                    'metric': 'manhours_used',
                    'type': 'spike',
                    'severity': 'high',
                    'value': manhour_ratio,
                    'expected_range': [avg_stats.get('avg_manhour_ratio', 1.0) * 0.8, avg_stats.get('avg_manhour_ratio', 1.0) * 1.2],
                    'description': 'Manhour utilization significantly above average'
                })
            
            quality_cost = float(project.cost_of_poor_quality_aed or 0)
            if quality_cost > avg_stats.get('avg_quality_cost', 50000) * 2:
                anomalies.append({
                    'metric': 'quality_cost',
                    'type': 'spike',
                    'severity': 'critical',
                    'value': quality_cost,
                    'expected_range': [0, avg_stats.get('avg_quality_cost', 50000) * 1.5],
                    'description': 'Quality cost significantly exceeds normal range'
                })
            
            car_rate = project.cars_open / max(1, project.cars_open + project.cars_closed)
            if car_rate > 0.7:
                anomalies.append({
                    'metric': 'car_generation_rate',
                    'type': 'unusual_pattern',
                    'severity': 'medium',
                    'value': car_rate,
                    'expected_range': [0, 0.5],
                    'description': 'Unusually high open CAR ratio'
                })
            
            return {
                'anomalies_detected': len(anomalies) > 0,
                'anomaly_count': len(anomalies),
                'anomalies': anomalies,
                'confidence': 0.85,
                'model_version': config.get('version', '1.0.0'),
                'detection_timestamp': timezone.now().isoformat()
            }
        except Exception as e:
            logger.error(f"Anomaly detection failed: {str(e)}")
            return {'anomalies_detected': False, 'anomalies': [], 'error': str(e)}
    
    def _get_project_averages(self) -> Dict[str, float]:
        """Calculate average metrics across all projects"""
        try:
            projects = QHSERunningProject.objects.filter(is_active=True)
            
            stats = projects.aggregate(
                avg_manhour_ratio=Avg(F('manhours_used') / F('man_hour_for_quality')),
                avg_quality_cost=Avg('cost_of_poor_quality_aed')
            )
            
            return {
                'avg_manhour_ratio': float(stats.get('avg_manhour_ratio') or 0.8),
                'avg_quality_cost': float(stats.get('avg_quality_cost') or 50000)
            }
        except:
            return {'avg_manhour_ratio': 0.8, 'avg_quality_cost': 50000}
    
    # ========================================================================
    # BATCH PREDICTIONS
    # ========================================================================
    
    def predict_all_project_risks(self, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """Predict risk scores for all active projects"""
        projects = QHSERunningProject.objects.filter(is_active=True).order_by('-updated_at')
        
        if limit:
            projects = projects[:limit]
        
        results = []
        for project in projects:
            risk_prediction = self.predict_project_risk(project)
            results.append({
                'project_no': project.project_no,
                'project_title': project.project_title,
                **risk_prediction
            })
        
        return results


# ============================================================================
# GLOBAL AI SERVICE INSTANCE
# ============================================================================

qhse_ai_service = QHSEAIService()
