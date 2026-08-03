"""
QHSE AI/ML Configuration - Soft-coded ML model settings
All AI features are configurable without code changes
"""
from decimal import Decimal

# ============================================================================
# AI MODELS REGISTRY - Central configuration for all ML models
# ============================================================================

AI_MODELS_CONFIG = {
    'risk_prediction': {
        'enabled': True,
        'model_type': 'gradient_boosting',
        'model_path': 'ml_models/qhse/risk_prediction_v1.pkl',
        'version': '1.0.0',
        'target': 'project_risk_score',
        'output_range': [0, 100],
        'confidence_threshold': 0.75,
        'features': [
            'audit_delay_days',
            'cars_open_ratio',
            'obs_open_ratio',
            'manhour_utilization_rate',
            'quality_cost_ratio',
            'kpi_achievement_percent',
            'project_completion_percent',
            'quality_plan_status_score',
            'cars_delayed_days',
            'obs_delayed_days'
        ],
        'feature_weights': {
            'audit_delay_days': 0.15,
            'cars_open_ratio': 0.20,
            'obs_open_ratio': 0.10,
            'manhour_utilization_rate': 0.15,
            'quality_cost_ratio': 0.15,
            'kpi_achievement_percent': 0.10,
            'project_completion_percent': 0.05,
            'quality_plan_status_score': 0.05,
            'cars_delayed_days': 0.03,
            'obs_delayed_days': 0.02
        },
        'risk_categories': {
            'critical': {'min': 80, 'max': 100, 'label': 'Critical Risk', 'color': '#DC2626'},
            'high': {'min': 60, 'max': 79, 'label': 'High Risk', 'color': '#F97316'},
            'medium': {'min': 40, 'max': 59, 'label': 'Medium Risk', 'color': '#EAB308'},
            'low': {'min': 20, 'max': 39, 'label': 'Low Risk', 'color': '#3B82F6'},
            'minimal': {'min': 0, 'max': 19, 'label': 'Minimal Risk', 'color': '#10B981'}
        },
        'retraining_schedule': 'weekly',
        'min_training_samples': 100
    },
    
    'car_classification': {
        'enabled': True,
        'model_type': 'text_classifier',
        'model_path': 'ml_models/qhse/car_classifier_v1.pkl',
        'version': '1.0.0',
        'target': 'car_category',
        'confidence_threshold': 0.70,
        'classes': {
            'quality_issue': {
                'label': 'Quality Issue',
                'severity': 'high',
                'keywords': ['defect', 'non-conformance', 'quality', 'specification', 'standard'],
                'average_resolution_days': 15
            },
            'safety_violation': {
                'label': 'Safety Violation',
                'severity': 'critical',
                'keywords': ['safety', 'hazard', 'injury', 'accident', 'ppe', 'risk'],
                'average_resolution_days': 7
            },
            'documentation_error': {
                'label': 'Documentation Error',
                'severity': 'low',
                'keywords': ['document', 'missing', 'incomplete', 'record', 'signature'],
                'average_resolution_days': 10
            },
            'process_defect': {
                'label': 'Process Defect',
                'severity': 'medium',
                'keywords': ['process', 'procedure', 'workflow', 'method', 'guideline'],
                'average_resolution_days': 12
            },
            'compliance_breach': {
                'label': 'Compliance Breach',
                'severity': 'critical',
                'keywords': ['compliance', 'regulation', 'audit', 'violation', 'requirement'],
                'average_resolution_days': 14
            },
            'environmental_impact': {
                'label': 'Environmental Impact',
                'severity': 'high',
                'keywords': ['environment', 'waste', 'emission', 'pollution', 'contamination'],
                'average_resolution_days': 18
            }
        },
        'retraining_schedule': 'monthly'
    },
    
    'manhour_prediction': {
        'enabled': True,
        'model_type': 'ensemble',
        'model_path': 'ml_models/qhse/manhour_predictor_v1.pkl',
        'version': '1.0.0',
        'target': 'required_manhours',
        'confidence_threshold': 0.80,
        'features': [
            'project_type',
            'project_complexity',
            'team_size',
            'project_duration_days',
            'historical_average_manhours',
            'client_requirements_count',
            'deliverables_count'
        ],
        'project_complexity_scoring': {
            'simple': {'score': 1, 'multiplier': 1.0},
            'moderate': {'score': 2, 'multiplier': 1.5},
            'complex': {'score': 3, 'multiplier': 2.0},
            'very_complex': {'score': 4, 'multiplier': 2.8}
        },
        'buffer_percentage': 15,  # Add 15% buffer to predictions
        'retraining_schedule': 'biweekly'
    },
    
    'safety_incident_prediction': {
        'enabled': True,
        'model_type': 'classification',
        'model_path': 'ml_models/qhse/safety_predictor_v1.pkl',
        'version': '1.0.0',
        'target': 'incident_probability',
        'confidence_threshold': 0.75,
        'features': [
            'project_type',
            'work_environment',
            'team_experience_avg',
            'safety_training_completion_rate',
            'ppe_compliance_rate',
            'near_miss_count',
            'safety_observations_count',
            'weather_conditions',
            'work_hours_per_week'
        ],
        'risk_levels': {
            'very_high': {'min': 0.80, 'color': '#DC2626', 'action': 'Immediate intervention required'},
            'high': {'min': 0.60, 'color': '#F97316', 'action': 'Enhanced controls needed'},
            'medium': {'min': 0.40, 'color': '#EAB308', 'action': 'Monitor closely'},
            'low': {'min': 0.20, 'color': '#3B82F6', 'action': 'Standard precautions'},
            'very_low': {'min': 0.0, 'color': '#10B981', 'action': 'Acceptable risk'}
        },
        'retraining_schedule': 'monthly'
    },
    
    'energy_forecasting': {
        'enabled': True,
        'model_type': 'time_series',
        'model_path': 'ml_models/qhse/energy_forecast_v1.pkl',
        'version': '1.0.0',
        'target': 'energy_consumption',
        'confidence_threshold': 0.85,
        'forecast_horizons': [7, 30, 90],  # days
        'features': [
            'historical_consumption',
            'temperature',
            'project_activity_level',
            'equipment_usage',
            'working_days',
            'seasonal_index'
        ],
        'retraining_schedule': 'weekly'
    },
    
    'anomaly_detection': {
        'enabled': True,
        'model_type': 'isolation_forest',
        'model_path': 'ml_models/qhse/anomaly_detector_v1.pkl',
        'version': '1.0.0',
        'contamination': 0.05,  # Expected % of anomalies
        'confidence_threshold': 0.90,
        'monitored_metrics': [
            'manhours_used',
            'quality_cost',
            'car_generation_rate',
            'audit_frequency',
            'project_completion_rate'
        ],
        'alert_thresholds': {
            'critical': 0.95,
            'high': 0.85,
            'medium': 0.75
        },
        'retraining_schedule': 'daily'
    },
    
    'nlp_remarks_analysis': {
        'enabled': True,
        'model_type': 'nlp',
        'model_path': 'ml_models/qhse/nlp_analyzer_v1.pkl',
        'version': '1.0.0',
        'tasks': ['sentiment', 'entity_extraction', 'topic_modeling', 'summarization'],
        'sentiment_threshold': 0.60,
        'entities_to_extract': [
            'PERSON', 'ORGANIZATION', 'DATE', 'LOCATION', 
            'ISSUE_TYPE', 'SEVERITY', 'ACTION_REQUIRED'
        ],
        'topics': {
            'quality': ['quality', 'defect', 'standard', 'specification'],
            'safety': ['safety', 'hazard', 'risk', 'accident'],
            'timeline': ['delay', 'deadline', 'schedule', 'overdue'],
            'cost': ['cost', 'budget', 'expense', 'financial'],
            'compliance': ['compliance', 'regulation', 'audit', 'requirement']
        }
    },
    
    'document_intelligence': {
        'enabled': True,
        'model_type': 'ocr_nlp',
        'ocr_engine': 'tesseract',  # or 'aws_textract'
        'version': '1.0.0',
        'supported_formats': ['pdf', 'jpg', 'png', 'tiff'],
        'confidence_threshold': 0.80,
        'auto_categorization': True,
        'categories': [
            'quality_plan',
            'audit_report',
            'car_document',
            'inspection_report',
            'safety_checklist',
            'environmental_report'
        ],
        'extraction_fields': {
            'quality_plan': ['project_no', 'revision', 'date', 'approver'],
            'audit_report': ['audit_date', 'auditor', 'findings', 'ncr_count'],
            'car_document': ['car_number', 'issue_date', 'category', 'severity']
        }
    }
}

# ============================================================================
# FEATURE ENGINEERING RULES - Soft-coded feature calculations
# ============================================================================

FEATURE_ENGINEERING_CONFIG = {
    'risk_prediction': {
        'audit_delay_days': {
            'calculation': 'delay_in_audits_no_days',
            'normalization': 'min_max',
            'range': [0, 90]
        },
        'cars_open_ratio': {
            'calculation': 'cars_open / (cars_open + cars_closed + 1)',
            'normalization': 'standard',
            'handle_zero': 'add_one'
        },
        'obs_open_ratio': {
            'calculation': 'obs_open / (obs_open + obs_closed + 1)',
            'normalization': 'standard',
            'handle_zero': 'add_one'
        },
        'manhour_utilization_rate': {
            'calculation': 'manhours_used / (man_hour_for_quality + 1)',
            'normalization': 'min_max',
            'range': [0, 2]
        },
        'quality_cost_ratio': {
            'calculation': 'cost_of_poor_quality_aed / 100000',  # Normalize by 100K
            'normalization': 'log_transform',
            'handle_zero': 'add_one'
        },
        'kpi_achievement_percent': {
            'calculation': 'parse_percentage(project_kpis_achieved_percent)',
            'normalization': 'min_max',
            'range': [0, 100]
        },
        'project_completion_percent': {
            'calculation': 'parse_percentage(project_completion_percent)',
            'normalization': 'min_max',
            'range': [0, 100]
        }
    }
}

# ============================================================================
# AI RECOMMENDATIONS ENGINE - Smart action suggestions
# ============================================================================

AI_RECOMMENDATIONS_CONFIG = {
    'risk_prediction': {
        'critical': [
            'Schedule emergency project review meeting',
            'Assign additional quality resources immediately',
            'Escalate to senior management',
            'Implement daily monitoring and reporting',
            'Consider project timeline adjustment'
        ],
        'high': [
            'Conduct root cause analysis',
            'Increase audit frequency',
            'Review and update quality plan',
            'Provide additional team training',
            'Weekly progress review meetings'
        ],
        'medium': [
            'Monitor key metrics weekly',
            'Review resource allocation',
            'Update risk mitigation strategies',
            'Conduct mid-project assessment'
        ],
        'low': [
            'Continue standard monitoring',
            'Maintain current quality practices',
            'Share best practices with team'
        ]
    },
    
    'car_classification': {
        'quality_issue': [
            'Conduct quality audit',
            'Review specifications and standards',
            'Implement additional inspections',
            'Update quality control procedures'
        ],
        'safety_violation': [
            'Immediate safety stand-down',
            'Mandatory safety re-training',
            'Review and update safety procedures',
            'Increase safety supervision'
        ],
        'documentation_error': [
            'Document review and correction',
            'Update document control procedures',
            'Provide documentation training',
            'Implement approval workflows'
        ],
        'compliance_breach': [
            'Immediate compliance audit',
            'Notify relevant authorities if required',
            'Implement corrective measures',
            'Review compliance management system'
        ]
    }
}

# ============================================================================
# AI ALERTS CONFIGURATION - Automated notification rules
# ============================================================================

AI_ALERTS_CONFIG = {
    'risk_prediction': {
        'enabled': True,
        'alert_on_score_above': 60,
        'alert_channels': ['email', 'dashboard', 'sms'],
        'escalation_rules': {
            'critical': {
                'immediate': ['project_manager', 'qhse_manager', 'senior_management'],
                'within_1_hour': ['executive_team']
            },
            'high': {
                'within_4_hours': ['project_manager', 'qhse_manager']
            },
            'medium': {
                'daily_digest': ['project_manager']
            }
        }
    },
    
    'anomaly_detection': {
        'enabled': True,
        'alert_threshold': 0.85,
        'alert_channels': ['email', 'dashboard'],
        'recipients': ['qhse_manager', 'data_analyst']
    },
    
    'safety_incident_prediction': {
        'enabled': True,
        'alert_on_probability_above': 0.60,
        'alert_channels': ['email', 'sms', 'dashboard'],
        'recipients': ['safety_officer', 'project_manager', 'hse_team']
    }
}

# ============================================================================
# ML PIPELINE CONFIGURATION - Training and deployment settings
# ============================================================================

ML_PIPELINE_CONFIG = {
    'data_sources': {
        'running_projects': 'apps.qhse.models.QHSERunningProject',
        'spot_checks': 'apps.qhse.models.QHSESpotCheckRegister'
    },
    
    'training': {
        'test_size': 0.2,
        'validation_size': 0.1,
        'random_state': 42,
        'cross_validation_folds': 5,
        'auto_feature_selection': True,
        'handle_imbalanced_data': True
    },
    
    'evaluation_metrics': {
        'classification': ['accuracy', 'precision', 'recall', 'f1_score', 'roc_auc'],
        'regression': ['mae', 'rmse', 'r2_score', 'mape'],
        'time_series': ['mae', 'rmse', 'smape']
    },
    
    'model_versioning': {
        'enabled': True,
        'store_path': 'ml_models/qhse/',
        'max_versions': 5,
        'auto_rollback_on_poor_performance': True,
        'performance_threshold': 0.75
    },
    
    'monitoring': {
        'enabled': True,
        'track_predictions': True,
        'track_feature_drift': True,
        'alert_on_model_degradation': True,
        'degradation_threshold': 0.10  # Alert if accuracy drops by 10%
    }
}

# ============================================================================
# QHSE COPILOT CONFIGURATION - Conversational AI assistant
# ============================================================================

QHSE_COPILOT_CONFIG = {
    'enabled': True,
    'model': 'gpt-4',  # or 'gpt-3.5-turbo'
    'temperature': 0.7,
    'max_tokens': 500,
    'system_prompt': """You are QHSE Copilot, an AI assistant specialized in Quality, Health, Safety, and Environment management. 
You help users analyze projects, identify risks, recommend actions, and answer questions about QHSE data.
Always provide accurate, actionable insights based on available data.""",
    
    'capabilities': [
        'project_risk_analysis',
        'car_analysis',
        'safety_recommendations',
        'data_queries',
        'report_generation',
        'trend_analysis'
    ],
    
    'example_queries': [
        "Which projects are at highest risk this week?",
        "Summarize the quality issues for Project X",
        "What are the top 3 safety concerns?",
        "Generate an executive summary of QHSE performance",
        "Show me projects with overdue CARs"
    ]
}

# ============================================================================
# SOFT-CODED BUSINESS RULES - No hardcoded logic
# ============================================================================

BUSINESS_RULES_CONFIG = {
    'risk_scoring_weights': {
        'audit_delays': 20,
        'open_cars': 25,
        'open_observations': 15,
        'manhour_overrun': 15,
        'quality_costs': 10,
        'kpi_achievement': 10,
        'completion_delays': 5
    },
    
    'thresholds': {
        'critical_audit_delay_days': 30,
        'high_car_count': 5,
        'high_quality_cost_aed': 100000,
        'low_kpi_achievement_percent': 70,
        'high_manhour_utilization': 1.2,  # 120%
        'low_safety_training_completion': 80
    },
    
    'auto_actions': {
        'high_risk_project': {
            'enabled': True,
            'actions': [
                'create_alert',
                'notify_manager',
                'update_dashboard',
                'generate_report'
            ]
        },
        'overdue_car': {
            'enabled': True,
            'actions': [
                'send_reminder',
                'escalate_to_manager',
                'update_status'
            ]
        }
    }
}

# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

def parse_percentage(value):
    """Extract numeric value from percentage string"""
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        return float(value.replace('%', '').strip() or 0)
    return 0.0

def get_risk_category(score):
    """Get risk category based on score"""
    config = AI_MODELS_CONFIG['risk_prediction']['risk_categories']
    for category, params in config.items():
        if params['min'] <= score <= params['max']:
            return category, params['label'], params['color']
    return 'unknown', 'Unknown', '#6B7280'

def get_model_config(model_name):
    """Get configuration for specific model"""
    return AI_MODELS_CONFIG.get(model_name, {})

def is_model_enabled(model_name):
    """Check if model is enabled"""
    config = AI_MODELS_CONFIG.get(model_name, {})
    return config.get('enabled', False)
