"""
PID Verification V2 - Extraction Configuration (Soft-Coded)
Multi-layer extraction strategy with open-source OCR + AI enhancement
"""

# ============================================================================
# LAYER 1: FREE OPEN-SOURCE OCR EXTRACTORS
# ============================================================================
LAYER1_OCR_CONFIG = {
    'engines': [
        {
            'name': 'pytesseract',
            'enabled': True,
            'priority': 1,  # Try first
            'psm_modes': [11, 6, 3],  # Page Segmentation Modes
            'dpi': 150,  # Base resolution
            'legend_dpi': 300,  # Higher for legend sheets
            'preprocessing': {
                'grayscale': True,
                'auto_contrast': True,
                'denoise': False,  # Can be slow
                'deskew': True,
            },
            'confidence_threshold': 60,  # Below this, trigger fallback
        },
        {
            'name': 'pymupdf',
            'enabled': True,
            'priority': 1,  # Run in parallel with Tesseract
            'extract_text_layer': True,  # Fast for PDFs with text layer
            'extract_words': True,  # Word-level extraction with coordinates
            'extract_blocks': True,  # Block-level for structure
        },
        {
            'name': 'pdfplumber',
            'enabled': True,
            'priority': 1,  # Run in parallel
            'extract_tables': True,  # Good for legend tables
            'extract_text': True,
            'table_settings': {
                'vertical_strategy': 'lines',
                'horizontal_strategy': 'lines',
                'snap_tolerance': 3,
            },
        },
    ],
    
    # Fallback engines (triggered if primary extraction is weak)
    'fallback_engines': [
        {
            'name': 'easyocr',
            'enabled': True,
            'priority': 2,
            'languages': ['en'],
            'gpu': False,  # Set True if GPU available
            'batch_size': 1,
            'confidence_threshold': 0.5,
            'trigger_condition': {
                'min_confidence': 60,  # Use if primary < 60%
                'min_tags_found': 5,   # Use if < 5 tags found
            },
        },
        {
            'name': 'paddleocr',
            'enabled': True,
            'priority': 3,
            'lang': 'en',
            'use_gpu': False,
            'use_angle_cls': True,  # Detect text rotation
            'trigger_condition': {
                'min_confidence': 50,
                'min_tags_found': 3,
            },
        },
    ],
    
    # Yellow-region highlighting detection (for marked-up drawings)
    'yellow_region_ocr': {
        'enabled': True,
        'color_ranges': {
            'yellow': {'hue': [20, 40], 'saturation': [100, 255], 'value': [100, 255]},
            'orange': {'hue': [10, 20], 'saturation': [150, 255], 'value': [150, 255]},
        },
        'min_region_size': 100,  # pixels
        'ocr_engine': 'tesseract',
        'enhanced_dpi': 300,
    },
}

# ============================================================================
# LAYER 2: AI ENHANCEMENT & BYOK
# ============================================================================
LAYER2_AI_CONFIG = {
    'modes': [
        {
            'id': 'fast',
            'label': 'Fast',
            'description': 'OCR-only with enhanced post-processing',
            'uses_vision_api': False,
            'cost_per_page': 0.0,
            'estimated_accuracy': '75-85%',
            'recommended': False,
            'features': ['ocr_only', 'spatial_grouping', 'regex_patterns'],
        },
        {
            'id': 'balanced',
            'label': 'Balanced ⭐',
            'description': 'Smart Vision fallback when OCR is weak',
            'uses_vision_api': 'conditional',
            'cost_per_page': 0.01,  # $0.01 base
            'cost_per_page_max': 0.15,
            'estimated_accuracy': '90-95%',
            'recommended': True,  # Default
            'features': ['ocr_primary', 'vision_fallback', 'cross_validation'],
            'vision_trigger': {
                'ocr_confidence_below': 70,
                'tags_found_below': 10,
                'has_handwritten_notes': True,
                'legend_quality_poor': True,
            },
        },
        {
            'id': 'deep',
            'label': 'Deep',
            'description': 'Full Vision analysis + OCR cross-validation',
            'uses_vision_api': True,
            'requires_api_key': True,  # BYOK required
            'cost_per_page': 0.20,
            'cost_per_page_max': 0.50,
            'estimated_accuracy': '95-98%',
            'recommended': False,
            'features': ['vision_primary', 'ocr_validation', 'chain_of_thought', 'multi_pass'],
        },
        {
            'id': 'vision_only',
            'label': 'Vision-Only',
            'description': 'Pure AI Vision (no OCR)',
            'uses_vision_api': True,
            'requires_api_key': True,
            'cost_per_page': 0.25,
            'cost_per_page_max': 0.60,
            'estimated_accuracy': '92-97%',
            'recommended': False,
            'features': ['vision_only', 'no_ocr_preprocessing'],
        },
    ],
    
    'providers': [
        {
            'name': 'openai',
            'model': 'gpt-4o',
            'vision_model': 'gpt-4o',
            'max_tokens': 4096,
            'temperature': 0.1,  # Low for consistency
            'cost_per_1k_tokens_input': 0.0025,
            'cost_per_1k_tokens_output': 0.010,
            'cost_per_image': 0.01275,  # High-res mode
        },
        {
            'name': 'claude',
            'model': 'claude-3-5-sonnet-20241022',
            'vision_model': 'claude-3-5-sonnet-20241022',
            'max_tokens': 4096,
            'temperature': 0.1,
            'cost_per_1k_tokens_input': 0.003,
            'cost_per_1k_tokens_output': 0.015,
        },
        {
            'name': 'gemini',
            'model': 'gemini-1.5-flash',
            'vision_model': 'gemini-1.5-flash',
            'max_tokens': 8192,
            'temperature': 0.1,
            'cost_per_1k_tokens_input': 0.000125,
            'cost_per_1k_tokens_output': 0.000375,
        },
    ],
    
    'default_provider': 'openai',
    'default_mode': 'balanced',
}

# ============================================================================
# MULTI-FILE & MULTI-PAGE HANDLING
# ============================================================================
MULTI_FILE_CONFIG = {
    'parallel_processing': {
        'enabled': True,
        'max_workers': 4,  # Process up to 4 files simultaneously
        'chunk_size': 5,   # Process 5 pages per chunk
    },
    
    'file_types': {
        'pid_drawing': {
            'max_files': 50,
            'max_pages_per_file': 100,
            'priority': 1,
            'extraction_profile': 'detailed',  # Extract all symbols, tags, connections
        },
        'legend_sheet': {
            'max_files': 10,
            'max_pages_per_file': 20,
            'priority': 2,
            'extraction_profile': 'legend',  # Focus on symbol definitions
        },
        'equipment_list': {
            'max_files': 5,
            'max_pages_per_file': 50,
            'priority': 3,
            'extraction_profile': 'tabular',  # Focus on table extraction
        },
        'line_list': {
            'max_files': 5,
            'max_pages_per_file': 50,
            'priority': 3,
            'extraction_profile': 'tabular',
        },
        'pms': {
            'max_files': 5,
            'max_pages_per_file': 50,
            'priority': 3,
            'extraction_profile': 'tabular',
        },
    },
    
    'page_processing': {
        'batch_size': 10,  # Process 10 pages before saving checkpoint
        'checkpoint_enabled': True,  # Save progress for resume on failure
        'timeout_per_page': 120,  # 2 minutes max per page
        'retry_failed_pages': True,
        'max_retries': 2,
    },
}

# ============================================================================
# DATA STORAGE & COMPARISON
# ============================================================================
STORAGE_CONFIG = {
    'database': {
        'enabled': True,
        'model': 'PIDVExtractionResult',  # Django model
        'save_per_page': True,  # Save each page result separately
        'save_per_file': True,  # Save aggregated file result
        'save_per_project': True,  # Save project-level summary
    },
    
    'json_export': {
        'enabled': True,
        'format': 'hierarchical',  # project → files → pages → data
        'include_metadata': True,
        'include_confidence_scores': True,
        'include_coordinates': True,  # Bounding boxes for all extracted elements
        'pretty_print': True,
        'path_template': 'extractions/{project_id}/{file_type}/{filename}_{timestamp}.json',
    },
    
    's3_storage': {
        'enabled': True,
        'bucket': 'radai-pidv2-extractions',
        'prefix': 'extractions/',
        'store_raw_json': True,
        'store_images': True,  # Processed images with annotations
        'retention_days': 90,
    },
}

# ============================================================================
# CROSS-FILE COMPARISON & FINDINGS ENGINE
# ============================================================================
COMPARISON_CONFIG = {
    'enabled': True,
    
    'comparison_rules': [
        {
            'name': 'tag_consistency',
            'description': 'Compare equipment tags across PID, Equipment List, Line List',
            'files_required': ['pid_drawing', 'equipment_list'],
            'check_type': 'cross_reference',
            'severity': 'high',
            'ai_enhanced': True,  # Use AI to detect variations (e.g., "P-101" vs "P101")
        },
        {
            'name': 'line_number_consistency',
            'description': 'Check line numbers appear consistently',
            'files_required': ['pid_drawing', 'line_list'],
            'check_type': 'cross_reference',
            'severity': 'high',
            'ai_enhanced': True,
        },
        {
            'name': 'symbol_legend_match',
            'description': 'Verify all PID symbols exist in legend',
            'files_required': ['pid_drawing', 'legend_sheet'],
            'check_type': 'symbol_validation',
            'severity': 'medium',
            'ai_enhanced': True,
        },
        {
            'name': 'spec_material_match',
            'description': 'Check material specs match between Line List and PMS',
            'files_required': ['line_list', 'pms'],
            'check_type': 'cross_reference',
            'severity': 'medium',
            'ai_enhanced': True,
        },
        {
            'name': 'duplicate_tags',
            'description': 'Find duplicate equipment tags in same PID',
            'files_required': ['pid_drawing'],
            'check_type': 'internal_validation',
            'severity': 'critical',
            'ai_enhanced': False,  # Simple rule-based
        },
        {
            'name': 'missing_tags',
            'description': 'Equipment in list but not on PID',
            'files_required': ['pid_drawing', 'equipment_list'],
            'check_type': 'completeness',
            'severity': 'high',
            'ai_enhanced': True,
        },
    ],
    
    'ai_comparison': {
        'enabled': True,
        'provider': 'openai',
        'model': 'gpt-4o',
        'temperature': 0.2,
        'max_tokens': 8192,
        'system_prompt': '''You are an expert P&ID quality checker. Compare extraction results from multiple documents and identify:
1. Inconsistencies in equipment tags, line numbers, and symbols
2. Missing or extra items compared to reference documents
3. Potential errors or anomalies in the data
4. Data quality issues (OCR errors, ambiguous symbols)

Output findings in structured JSON format with severity (critical/high/medium/low) and suggested corrections.''',
    },
    
    'output_format': {
        'format': 'json',
        'include_suggestions': True,
        'include_confidence': True,
        'group_by_severity': True,
        'max_findings_per_rule': 100,
    },
}

# ============================================================================
# PROGRESS TRACKING & NOTIFICATIONS
# ============================================================================
PROGRESS_CONFIG = {
    'websocket_updates': {
        'enabled': True,
        'channel_prefix': 'pidv2_extraction_',
        'update_frequency': 'per_page',  # Send update after each page
    },
    
    'celery_progress': {
        'enabled': True,
        'backend': 'redis',
        'track_steps': [
            'file_upload',
            'preprocessing',
            'layer1_ocr',
            'layer2_fallback',
            'layer3_vision',
            'data_aggregation',
            'cross_file_comparison',
            'findings_generation',
            'storage',
        ],
    },
    
    'email_notifications': {
        'enabled': False,
        'send_on_completion': True,
        'send_on_error': True,
        'include_summary': True,
    },
}

# ============================================================================
# EXTRACTION PROFILES (Soft-coded rules per document type)
# ============================================================================
EXTRACTION_PROFILES = {
    'detailed': {
        # For PID drawings - extract everything
        'targets': [
            'equipment_tags',
            'line_numbers',
            'symbols',
            'connections',
            'notes',
            'dimensions',
            'flowdirection_arrows',
            'instrumentation',
        ],
        'ocr_settings': {
            'dpi': 200,
            'psm_mode': 11,  # Sparse text
            'use_spatial_grouping': True,
        },
        'regex_patterns': {
            'equipment_tag': r'[A-Z]{1,2}-\d{3,4}[A-Z]?',
            'line_number': r'\d{1,2}"-[A-Z]{2,4}-\d{4,6}-[A-Z0-9]{2,4}',
            'instrument_tag': r'[A-Z]{2,3}-\d{3,4}[A-Z]?',
        },
    },
    
    'legend': {
        # For legend sheets - focus on symbol definitions
        'targets': [
            'symbol_definitions',
            'symbol_images',
            'descriptions',
            'tables',
        ],
        'ocr_settings': {
            'dpi': 300,
            'psm_mode': 6,  # Assume uniform block
            'use_table_detection': True,
        },
        'table_extraction': {
            'enabled': True,
            'header_detection': True,
            'merge_cells': True,
        },
    },
    
    'tabular': {
        # For Equipment List, Line List, PMS - focus on tables
        'targets': [
            'tables',
            'headers',
            'rows',
            'columns',
        ],
        'ocr_settings': {
            'dpi': 150,
            'psm_mode': 6,
            'use_table_detection': True,
        },
        'table_extraction': {
            'enabled': True,
            'header_detection': True,
            'auto_column_mapping': True,
            'skip_empty_rows': True,
        },
    },
}
