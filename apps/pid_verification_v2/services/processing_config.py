"""
P&ID Verification V2 — Processing Configuration
=================================================

Soft-coded configuration for the entire P&ID verification processing pipeline.
All constants, thresholds, and parameters are defined here for easy tuning.

**Philosophy**: Configuration over hard-coding. Change behavior by editing
constants, not by touching core logic.

Author: RADAI Team
Last Updated: 2026-07-24
"""

from typing import Dict, List, Any
from dataclasses import dataclass


# ===========================================================================
# PROCESSING STAGES CONFIGURATION
# ===========================================================================

@dataclass
class ProcessingStage:
    """Definition of a processing stage with timing and logging configuration."""
    id: str
    name: str
    description: str
    timeout_seconds: int
    retry_count: int
    critical: bool  # If True, failure aborts entire pipeline
    log_level: str  # 'INFO', 'WARNING', 'ERROR'


# V2 Processing Pipeline Stages
# Order matters — stages execute sequentially
PROCESSING_STAGES: List[ProcessingStage] = [
    ProcessingStage(
        id='file_validation',
        name='File Validation',
        description='Validate uploaded file format and integrity',
        timeout_seconds=30,
        retry_count=0,
        critical=True,
        log_level='INFO'
    ),
    ProcessingStage(
        id='segmentation',
        name='Document Segmentation',
        description='Split multi-page PDF into individual drawings',
        timeout_seconds=120,
        retry_count=1,
        critical=True,
        log_level='INFO'
    ),
    ProcessingStage(
        id='extraction',
        name='P&ID Element Extraction',
        description='OCR and pattern recognition to extract instruments, valves, tags, lines',
        timeout_seconds=300,
        retry_count=2,
        critical=True,
        log_level='INFO'
    ),
    ProcessingStage(
        id='graph_building',
        name='Connectivity Graph Build',
        description='Build process flow connectivity graph from extracted elements',
        timeout_seconds=60,
        retry_count=1,
        critical=False,  # Can continue without graph
        log_level='INFO'
    ),
    ProcessingStage(
        id='rule_engine',
        name='Deterministic Rule Engine',
        description='Apply industry-standard P&ID quality rules',
        timeout_seconds=120,
        retry_count=1,
        critical=False,
        log_level='INFO'
    ),
    ProcessingStage(
        id='comparison_engine',
        name='V2 Comparison Engine',
        description='Cross-document comparison with Legend, Line List, Equipment, Instrument Index',
        timeout_seconds=180,
        retry_count=2,
        critical=False,  # Continue even if comparison fails
        log_level='INFO'
    ),
    ProcessingStage(
        id='ai_analysis',
        name='AI Enhancement (BYOK)',
        description='Optional AI-powered analysis using user-provided API keys',
        timeout_seconds=300,
        retry_count=1,
        critical=False,  # Optional feature
        log_level='INFO'
    ),
    ProcessingStage(
        id='report_generation',
        name='Report Generation',
        description='Generate Excel, PDF, and JSON reports',
        timeout_seconds=120,
        retry_count=2,
        critical=False,
        log_level='INFO'
    ),
]


# ===========================================================================
# COMPARISON ENGINE CONFIGURATION
# ===========================================================================

# Fuzzy matching threshold (0.0 to 1.0)
# Higher = stricter matching, Lower = more permissive
COMPARISON_MATCH_THRESHOLD = 0.85

# Partial match threshold (items that partially match but don't meet main threshold)
COMPARISON_PARTIAL_THRESHOLD = 0.70

# Attribute weights for weighted similarity comparison
# Higher weight = more important for matching
ATTRIBUTE_WEIGHTS = {
    'legend': {
        'symbol_code': 1.0,
        'description': 0.7,
        'size': 0.5,
    },
    'line_list': {
        'tag': 1.0,
        'size': 0.9,
        'fluid_code': 0.8,
        'from_to': 0.6,
        'line_class': 0.7,
        'material': 0.6,
    },
    'equipment': {
        'tag': 1.0,
        'type': 0.8,
        'description': 0.6,
        'service': 0.7,
        'duty': 0.5,
    },
    'instrument': {
        'tag': 1.0,
        'type': 0.9,
        'service': 0.7,
        'range': 0.6,
        'location': 0.5,
    },
}

# Comparison result severity thresholds
COMPARISON_SEVERITY_THRESHOLDS = {
    'critical': 0.90,  # Missing/mismatch rate > 90% = critical
    'high': 0.50,      # > 50% = high
    'medium': 0.20,    # > 20% = medium
    'low': 0.00,       # <= 20% = low
}


# ===========================================================================
# EXTRACTION CONFIGURATION
# ===========================================================================

# OCR engine settings
OCR_CONFIG = {
    'dpi': 300,
    'language': 'eng',
    'psm': 3,  # Fully automatic page segmentation
    'oem': 3,  # Default OCR Engine Mode
    'timeout': 120,  # seconds
}

# Pattern recognition thresholds
PATTERN_RECOGNITION = {
    'tag_min_confidence': 0.75,
    'instrument_min_confidence': 0.70,
    'valve_min_confidence': 0.65,
    'equipment_min_confidence': 0.70,
    'line_size_min_confidence': 0.80,
}

# Tag patterns (regex)
TAG_PATTERNS = {
    'instrument': r'^[A-Z]{2,3}[ICAV]-?\d{3,5}[A-Z]?$',  # FIC-101, TT-2001A
    'equipment': r'^[ECPTVR]-?\d{3,5}[A-Z]?$',           # E-101, P-2001A
    'valve': r'^[HVXG]V-?\d{3,5}[A-Z]?$',                # HV-101, XV-2001A
    'line': r'^\d+-?[A-Z]{2,4}-?\d+-?[A-Z0-9]+$',       # 2-HW-6-B1M
}


# ===========================================================================
# RULE ENGINE CONFIGURATION
# ===========================================================================

# Rule execution settings
RULE_ENGINE_CONFIG = {
    'parallel_execution': False,  # Run rules sequentially for now
    'skip_on_error': True,        # Continue if one rule fails
    'max_findings_per_rule': 1000,
    'deduplication_enabled': True,
}

# Rule severity weights (for prioritization)
RULE_SEVERITY_WEIGHTS = {
    'critical': 100,
    'high': 50,
    'medium': 20,
    'low': 10,
}


# ===========================================================================
# AI ANALYSIS CONFIGURATION (BYOK)
# ===========================================================================

# AI provider settings
AI_PROVIDERS = {
    'openai': {
        'model': 'gpt-4-turbo-preview',
        'temperature': 0.3,
        'max_tokens': 4096,
        'timeout': 120,
    },
    'claude': {
        'model': 'claude-3-opus-20240229',
        'temperature': 0.3,
        'max_tokens': 4096,
        'timeout': 120,
    },
}

# AI analysis modes
AI_ANALYSIS_MODES = {
    'standard': {
        'enabled': False,
        'description': 'Rule-based analysis only (no AI)',
    },
    'enhanced_openai': {
        'enabled': True,
        'provider': 'openai',
        'description': 'GPT-4 powered analysis for complex patterns',
    },
    'deep_claude': {
        'enabled': True,
        'provider': 'claude',
        'description': 'Claude 3 Opus for detailed technical review',
    },
    'hybrid': {
        'enabled': True,
        'providers': ['openai', 'claude'],
        'description': 'Combined analysis from multiple AI models',
    },
}


# ===========================================================================
# REPORTING CONFIGURATION
# ===========================================================================

# Report formats to generate
REPORT_FORMATS = {
    'excel': {
        'enabled': True,
        'template': 'pid_verification_report_template.xlsx',
        'max_rows': 10000,
    },
    'pdf': {
        'enabled': True,
        'template': 'pid_verification_report_template.pdf',
        'page_size': 'A4',
    },
    'json': {
        'enabled': True,
        'pretty_print': True,
        'indent': 2,
    },
}

# Report sections
REPORT_SECTIONS = [
    'executive_summary',
    'extraction_statistics',
    'rule_findings',
    'comparison_results',
    'ai_insights',
    'recommendations',
    'appendices',
]


# ===========================================================================
# PERFORMANCE & OPTIMIZATION
# ===========================================================================

# Celery task settings
TASK_CONFIG = {
    'soft_time_limit': 1800,  # 30 minutes
    'time_limit': 2100,       # 35 minutes (hard limit)
    'max_retries': 3,
    'retry_delay': 60,        # seconds
    'acks_late': True,
    'reject_on_worker_lost': True,
}

# Caching configuration
CACHE_CONFIG = {
    'enabled': True,
    'ttl': 86400,  # 24 hours
    'reuse_cache_on_same_project': True,
    'ignore_failed_cache': True,
    'ignore_degraded_cache': True,  # Cache with 0 drawings
}

# Database query optimization
DB_CONFIG = {
    'bulk_create_batch_size': 500,
    'select_related_depth': 2,
    'prefetch_related_enabled': True,
}


# ===========================================================================
# ERROR HANDLING & RECOVERY
# ===========================================================================

# Error recovery strategies
ERROR_RECOVERY = {
    'auto_retry_transient': True,
    'retry_delay_multiplier': 2,  # Exponential backoff
    'max_retry_delay': 300,       # 5 minutes max
    'fallback_to_partial_results': True,
    'save_intermediate_state': True,
}

# Error notification thresholds
ERROR_THRESHOLDS = {
    'consecutive_failures': 5,
    'failure_rate_percent': 20,
    'notify_admin_on_critical': True,
}


# ===========================================================================
# LOGGING & MONITORING
# ===========================================================================

# Logging configuration
LOGGING_CONFIG = {
    'log_extraction_details': True,
    'log_comparison_mismatches': True,
    'log_ai_requests': False,  # Don't log API keys
    'log_performance_metrics': True,
    'verbose_mode': False,
}

# Performance metrics to track
PERFORMANCE_METRICS = [
    'total_processing_time',
    'extraction_time',
    'comparison_time',
    'ai_analysis_time',
    'report_generation_time',
    'findings_count',
    'accuracy_score',
]


# ===========================================================================
# FEATURE FLAGS
# ===========================================================================

# Feature toggles (enable/disable features without code changes)
FEATURE_FLAGS = {
    'v2_comparison_engine': True,
    'byok_ai_analysis': True,
    'graph_connectivity': True,
    'pdf_report_generation': True,
    'excel_export': True,
    's3_upload': True,
    'cross_discipline_recommendations': False,  # Future feature
    'real_time_collaboration': False,           # Future feature
}


# ===========================================================================
# HELPER FUNCTIONS
# ===========================================================================

def get_stage_config(stage_id: str) -> ProcessingStage:
    """Get configuration for a specific processing stage."""
    for stage in PROCESSING_STAGES:
        if stage.id == stage_id:
            return stage
    raise ValueError(f"Unknown processing stage: {stage_id}")


def is_feature_enabled(feature_name: str) -> bool:
    """Check if a feature is enabled."""
    return FEATURE_FLAGS.get(feature_name, False)


def get_comparison_threshold(comparison_type: str = 'main') -> float:
    """Get comparison threshold based on type."""
    if comparison_type == 'partial':
        return COMPARISON_PARTIAL_THRESHOLD
    return COMPARISON_MATCH_THRESHOLD


def get_severity_from_rate(error_rate: float) -> str:
    """Determine severity based on error rate."""
    for severity, threshold in sorted(
        COMPARISON_SEVERITY_THRESHOLDS.items(),
        key=lambda x: x[1],
        reverse=True
    ):
        if error_rate >= threshold:
            return severity
    return 'low'


def get_processing_timeout(stage_id: str) -> int:
    """Get timeout for a specific stage."""
    stage = get_stage_config(stage_id)
    return stage.timeout_seconds


# ===========================================================================
# CONFIGURATION VALIDATION
# ===========================================================================

def validate_config():
    """Validate configuration consistency and raise errors if invalid."""
    errors = []
    
    # Check thresholds are in valid range
    if not 0.0 <= COMPARISON_MATCH_THRESHOLD <= 1.0:
        errors.append("COMPARISON_MATCH_THRESHOLD must be between 0.0 and 1.0")
    
    if not 0.0 <= COMPARISON_PARTIAL_THRESHOLD <= 1.0:
        errors.append("COMPARISON_PARTIAL_THRESHOLD must be between 0.0 and 1.0")
    
    # Check attribute weights sum
    for comp_type, weights in ATTRIBUTE_WEIGHTS.items():
        if not all(0.0 <= w <= 1.0 for w in weights.values()):
            errors.append(f"Invalid weight in {comp_type}: weights must be between 0.0 and 1.0")
    
    # Check stages have unique IDs
    stage_ids = [s.id for s in PROCESSING_STAGES]
    if len(stage_ids) != len(set(stage_ids)):
        errors.append("Duplicate stage IDs found in PROCESSING_STAGES")
    
    if errors:
        raise ValueError(f"Configuration validation failed:\n" + "\n".join(f"  - {e}" for e in errors))


# Validate on import
validate_config()
