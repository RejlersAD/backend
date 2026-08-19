"""
Advanced ML Detection Engine
Real-time anomaly detection using multiple ML techniques
"""
import numpy as np
from sklearn.ensemble import IsolationForest, RandomForestClassifier, GradientBoostingClassifier
from sklearn.preprocessing import StandardScaler
from sklearn.decomposition import PCA
from scipy import stats
import pandas as pd
from datetime import datetime, timedelta
from django.utils import timezone
from django.core.cache import cache
import logging
import json

logger = logging.getLogger(__name__)


class MLDetectionEngine:
    """
    Advanced ML-based detection engine with multiple algorithms
    Soft-coded configuration for flexible deployment
    """
    
    def __init__(self, config=None):
        self.config = config or self._default_config()
        self.models = {}
        self.scalers = {}
        self.initialize_models()
    
    def _default_config(self):
        """Default soft-coded configuration"""
        return {
            'anomaly_detection': {
                'contamination': 0.1,
                'n_estimators': 100,
                'max_samples': 'auto',
                'random_state': 42
            },
            'threshold_detection': {
                'std_multiplier': 3.0,
                'window_size': 100,
                'min_samples': 10
            },
            'pattern_recognition': {
                'sequence_length': 10,
                'similarity_threshold': 0.85
            },
            'prediction': {
                'forecast_horizon': 30,
                'confidence_interval': 0.95
            },
            'features': {
                'use_temporal_features': True,
                'use_statistical_features': True,
                'use_frequency_features': True
            }
        }
    
    def initialize_models(self):
        """Initialize ML models based on configuration"""
        # Isolation Forest for anomaly detection
        self.models['isolation_forest'] = IsolationForest(
            contamination=self.config['anomaly_detection']['contamination'],
            n_estimators=self.config['anomaly_detection']['n_estimators'],
            max_samples=self.config['anomaly_detection']['max_samples'],
            random_state=self.config['anomaly_detection']['random_state']
        )
        
        # Random Forest for classification
        self.models['random_forest'] = RandomForestClassifier(
            n_estimators=100,
            random_state=42
        )
        
        # Gradient Boosting for complex patterns
        self.models['gradient_boost'] = GradientBoostingClassifier(
            n_estimators=100,
            random_state=42
        )
        
        # Standard Scaler
        self.scalers['standard'] = StandardScaler()
    
    def extract_features(self, data_point, historical_data=None):
        """
        Extract features from data point for ML analysis
        Soft-coded feature engineering
        """
        features = {}
        
        # Basic features
        if isinstance(data_point, dict):
            for key, value in data_point.items():
                if isinstance(value, (int, float)):
                    features[key] = value
        
        # Statistical features
        if historical_data and self.config['features']['use_statistical_features']:
            df = pd.DataFrame(historical_data)
            for col in df.select_dtypes(include=[np.number]).columns:
                features[f'{col}_mean'] = df[col].mean()
                features[f'{col}_std'] = df[col].std()
                features[f'{col}_median'] = df[col].median()
                features[f'{col}_q25'] = df[col].quantile(0.25)
                features[f'{col}_q75'] = df[col].quantile(0.75)
        
        # Temporal features
        if self.config['features']['use_temporal_features']:
            now = datetime.now()
            features['hour'] = now.hour
            features['day_of_week'] = now.weekday()
            features['is_weekend'] = 1 if now.weekday() >= 5 else 0
            features['is_business_hours'] = 1 if 9 <= now.hour <= 17 else 0
        
        return features
    
    def detect_anomaly_isolation_forest(self, data_point, historical_data):
        """
        Detect anomalies using Isolation Forest
        """
        try:
            # Extract features
            features = self.extract_features(data_point, historical_data)
            feature_vector = np.array([list(features.values())])
            
            # Check if model is trained
            cache_key = 'ml_isolation_forest_trained'
            if not cache.get(cache_key):
                # Train on historical data if available
                if historical_data and len(historical_data) > 10:
                    X_train = []
                    for hist_point in historical_data:
                        hist_features = self.extract_features(hist_point)
                        X_train.append(list(hist_features.values()))
                    
                    X_train = np.array(X_train)
                    self.models['isolation_forest'].fit(X_train)
                    cache.set(cache_key, True, 3600)  # Cache for 1 hour
            
            # Predict
            prediction = self.models['isolation_forest'].predict(feature_vector)
            anomaly_score = self.models['isolation_forest'].score_samples(feature_vector)[0]
            
            is_anomaly = prediction[0] == -1
            confidence = abs(anomaly_score)
            
            return {
                'is_anomaly': is_anomaly,
                'confidence': float(confidence),
                'anomaly_score': float(anomaly_score),
                'method': 'isolation_forest',
                'features_used': list(features.keys())
            }
        
        except Exception as e:
            logger.error(f"Isolation Forest detection error: {e}")
            return {'is_anomaly': False, 'confidence': 0.0, 'error': str(e)}
    
    def detect_anomaly_statistical(self, value, historical_values):
        """
        Statistical anomaly detection using Z-score and IQR
        """
        try:
            if not historical_values or len(historical_values) < self.config['threshold_detection']['min_samples']:
                return {'is_anomaly': False, 'confidence': 0.0, 'method': 'statistical', 'reason': 'insufficient_data'}
            
            values = np.array(historical_values)
            mean = np.mean(values)
            std = np.std(values)
            
            # Z-score method
            if std > 0:
                z_score = abs((value - mean) / std)
                is_anomaly_zscore = z_score > self.config['threshold_detection']['std_multiplier']
            else:
                z_score = 0
                is_anomaly_zscore = False
            
            # IQR method
            q1 = np.percentile(values, 25)
            q3 = np.percentile(values, 75)
            iqr = q3 - q1
            lower_bound = q1 - 1.5 * iqr
            upper_bound = q3 + 1.5 * iqr
            is_anomaly_iqr = value < lower_bound or value > upper_bound
            
            # Combined decision
            is_anomaly = is_anomaly_zscore or is_anomaly_iqr
            confidence = min(z_score / 5.0, 1.0)  # Normalize to 0-1
            
            return {
                'is_anomaly': is_anomaly,
                'confidence': float(confidence),
                'z_score': float(z_score),
                'iqr_bounds': [float(lower_bound), float(upper_bound)],
                'method': 'statistical',
                'value': float(value),
                'mean': float(mean),
                'std': float(std)
            }
        
        except Exception as e:
            logger.error(f"Statistical detection error: {e}")
            return {'is_anomaly': False, 'confidence': 0.0, 'error': str(e)}
    
    def detect_pattern(self, sequence, known_patterns):
        """
        Pattern recognition in time series data
        """
        try:
            if len(sequence) < self.config['pattern_recognition']['sequence_length']:
                return {'pattern_detected': False, 'confidence': 0.0}
            
            # Normalize sequence
            seq_array = np.array(sequence[-self.config['pattern_recognition']['sequence_length']:])
            seq_normalized = (seq_array - np.mean(seq_array)) / (np.std(seq_array) + 1e-8)
            
            best_match = None
            best_similarity = 0.0
            
            for pattern_name, pattern_data in known_patterns.items():
                pattern_array = np.array(pattern_data)
                pattern_normalized = (pattern_array - np.mean(pattern_array)) / (np.std(pattern_array) + 1e-8)
                
                # Calculate correlation
                correlation = np.corrcoef(seq_normalized, pattern_normalized)[0, 1]
                
                if correlation > best_similarity:
                    best_similarity = correlation
                    best_match = pattern_name
            
            pattern_detected = best_similarity >= self.config['pattern_recognition']['similarity_threshold']
            
            return {
                'pattern_detected': pattern_detected,
                'pattern_name': best_match,
                'confidence': float(best_similarity),
                'method': 'pattern_recognition'
            }
        
        except Exception as e:
            logger.error(f"Pattern detection error: {e}")
            return {'pattern_detected': False, 'confidence': 0.0, 'error': str(e)}
    
    def predict_future_anomaly(self, historical_data, forecast_horizon=None):
        """
        Predict future anomalies using time series forecasting
        """
        try:
            horizon = forecast_horizon or self.config['prediction']['forecast_horizon']
            
            if len(historical_data) < 30:
                return {'prediction': None, 'confidence': 0.0, 'reason': 'insufficient_data'}
            
            # Simple moving average prediction
            values = np.array([d.get('value', 0) for d in historical_data])
            ma_short = np.mean(values[-7:])  # 7-day MA
            ma_long = np.mean(values[-30:])  # 30-day MA
            
            # Trend detection
            trend = ma_short - ma_long
            
            # Predict next values
            predictions = []
            for i in range(horizon):
                pred_value = values[-1] + trend * (i + 1)
                predictions.append(float(pred_value))
            
            # Calculate volatility
            volatility = np.std(values[-30:])
            
            # Anomaly prediction
            will_anomaly = abs(trend) > 2 * volatility
            confidence = min(abs(trend) / (3 * volatility), 1.0) if volatility > 0 else 0.0
            
            return {
                'will_anomaly': will_anomaly,
                'confidence': float(confidence),
                'predictions': predictions,
                'trend': float(trend),
                'volatility': float(volatility),
                'method': 'time_series_prediction'
            }
        
        except Exception as e:
            logger.error(f"Prediction error: {e}")
            return {'will_anomaly': False, 'confidence': 0.0, 'error': str(e)}
    
    def analyze_realtime(self, data_point, stream_type, historical_data=None):
        """
        Comprehensive real-time analysis combining multiple methods
        """
        results = {
            'timestamp': timezone.now().isoformat(),
            'stream_type': stream_type,
            'detections': {},
            'overall_anomaly': False,
            'max_confidence': 0.0,
            'alerts': []
        }
        
        try:
            # Method 1: Isolation Forest
            if historical_data:
                iso_result = self.detect_anomaly_isolation_forest(data_point, historical_data)
                results['detections']['isolation_forest'] = iso_result
            
            # Method 2: Statistical Analysis
            if 'value' in data_point and historical_data:
                hist_values = [d.get('value', 0) for d in historical_data if 'value' in d]
                stat_result = self.detect_anomaly_statistical(data_point['value'], hist_values)
                results['detections']['statistical'] = stat_result
            
            # Method 3: Pattern Recognition
            if historical_data:
                sequence = [d.get('value', 0) for d in historical_data if 'value' in d]
                sequence.append(data_point.get('value', 0))
                
                # Define some known problematic patterns
                known_patterns = {
                    'spike': [0, 0, 0, 10, 0, 0, 0, 0, 0, 0],
                    'drop': [10, 10, 10, 0, 10, 10, 10, 10, 10, 10],
                    'increasing': [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
                }
                
                pattern_result = self.detect_pattern(sequence, known_patterns)
                results['detections']['pattern'] = pattern_result
            
            # Method 4: Predictive Analysis
            if historical_data:
                pred_result = self.predict_future_anomaly(historical_data)
                results['detections']['prediction'] = pred_result
            
            # Aggregate results
            confidences = []
            for method, result in results['detections'].items():
                if result.get('is_anomaly') or result.get('pattern_detected') or result.get('will_anomaly'):
                    results['overall_anomaly'] = True
                    conf = result.get('confidence', 0.0)
                    confidences.append(conf)
                    
                    # Generate alert
                    if conf > 0.7:
                        results['alerts'].append({
                            'method': method,
                            'confidence': conf,
                            'details': result
                        })
            
            # Calculate max confidence
            results['max_confidence'] = max(confidences) if confidences else 0.0
            
            # Overall risk score
            results['risk_score'] = self._calculate_risk_score(results['detections'])
            
        except Exception as e:
            logger.error(f"Real-time analysis error: {e}")
            results['error'] = str(e)
        
        return results
    
    def _calculate_risk_score(self, detections):
        """Calculate overall risk score from multiple detections"""
        scores = []
        weights = {
            'isolation_forest': 0.3,
            'statistical': 0.3,
            'pattern': 0.2,
            'prediction': 0.2
        }
        
        for method, result in detections.items():
            confidence = result.get('confidence', 0.0)
            weight = weights.get(method, 0.1)
            scores.append(confidence * weight)
        
        return sum(scores)


class AlertGenerator:
    """Generate alerts from ML detection results"""
    
    def __init__(self):
        self.alert_history = {}
    
    def should_alert(self, detection_result, config_id):
        """
        Determine if alert should be triggered
        Implements cooldown period to prevent alert fatigue
        """
        from .models import DetectionConfig
        
        try:
            config = DetectionConfig.objects.get(id=config_id)
            
            # Check if alert is active
            if not config.is_active:
                return False
            
            # Check confidence threshold
            if detection_result['max_confidence'] < 0.7:
                return False
            
            # Check cooldown period
            cache_key = f'alert_cooldown_{config_id}'
            last_alert = cache.get(cache_key)
            
            if last_alert:
                return False
            
            # Set cooldown
            cache.set(cache_key, timezone.now(), config.cooldown_period_seconds)
            
            return True
            
        except Exception as e:
            logger.error(f"Alert decision error: {e}")
            return False
    
    def generate_alert(self, detection_result, config):
        """Generate alert from detection result"""
        from .models import RealTimeAlert
        
        try:
            # Determine severity based on confidence and risk
            severity = self._determine_severity(
                detection_result['max_confidence'],
                detection_result['risk_score']
            )
            
            # Create alert
            alert = RealTimeAlert.objects.create(
                alert_type='anomaly_detected',
                title=f"Anomaly detected in {detection_result['stream_type']}",
                description=self._generate_description(detection_result),
                severity=severity,
                detection_config=config,
                confidence_score=detection_result['max_confidence'],
                detected_data=detection_result,
                context={
                    'methods_triggered': list(detection_result['detections'].keys()),
                    'risk_score': detection_result['risk_score']
                }
            )
            
            return alert
            
        except Exception as e:
            logger.error(f"Alert generation error: {e}")
            return None
    
    def _determine_severity(self, confidence, risk_score):
        """Determine alert severity"""
        if confidence >= 0.95 or risk_score >= 0.9:
            return 'critical'
        elif confidence >= 0.85 or risk_score >= 0.7:
            return 'high'
        elif confidence >= 0.75 or risk_score >= 0.5:
            return 'medium'
        else:
            return 'low'
    
    def _generate_description(self, result):
        """Generate human-readable description"""
        methods = []
        for method, detection in result['detections'].items():
            if detection.get('is_anomaly') or detection.get('pattern_detected') or detection.get('will_anomaly'):
                methods.append(f"{method.replace('_', ' ').title()}")
        
        return f"Multiple detection methods triggered: {', '.join(methods)}. " \
               f"Maximum confidence: {result['max_confidence']:.2%}, " \
               f"Risk score: {result['risk_score']:.2%}"
