"""
Configuration for Ultra Complete P&ID Generation
Central configuration for the Ultra intelligence level with RAG + Graph AI
Note: Basic and Professional levels maintained for backward compatibility only
"""

# Intelligence Levels Configuration
# PRIMARY: Ultra (95%+) - RAG + Graph AI
# Legacy: Basic and Professional (maintained for backward compatibility)
INTELLIGENCE_LEVELS = {
    'ultra': {
        'completeness': 0.95,  # 95%+ completeness
        'description': 'Ultra-complete P&ID with RAG + Graph AI intelligence',
        'features': [
            'equipment_layout',
            'basic_piping',
            'primary_instruments',
            'detailed_piping',
            'instruments',
            'valves',
            'engineering_standards',
            'data_enrichment',
            'rag_knowledge',
            'graph_connectivity',
            'missing_detection',
            'utility_network',
            'control_loops',
            'strict_alignment'
        ],
        'rag_enabled': True,
        'graph_analysis': True,
        'missing_detection': True,
        'utility_generation': True,
        'control_loops': True,
        'is_primary': True
    },
    # Legacy levels - maintained for backward compatibility
    'basic': {
        'completeness': 0.40,
        'description': 'Basic P&ID (Legacy - use Ultra instead)',
        'features': [
            'equipment_layout',
            'basic_piping',
            'primary_instruments'
        ],
        'rag_enabled': False,
        'graph_analysis': False,
        'missing_detection': False,
        'utility_generation': False,
        'control_loops': False,
        'is_primary': False,
        'deprecated': True
    },
    'professional': {
        'completeness': 0.70,
        'description': 'Professional P&ID (Legacy - use Ultra instead)',
        'features': [
            'equipment_layout',
            'basic_piping',
            'primary_instruments',
            'detailed_piping',
            'instruments',
            'valves',
            'engineering_standards',
            'data_enrichment'
        ],
        'rag_enabled': True,
        'graph_analysis': False,
        'missing_detection': False,
        'utility_generation': False,
        'control_loops': False,
        'is_primary': False,
        'deprecated': True
    }
}

# Layout Configuration
LAYOUT_CONFIG = {
    'page_size': 'A1_landscape',  # A1 landscape for large P&IDs
    'margins': {
        'left': 50,   # mm
        'right': 50,
        'top': 40,
        'bottom': 40
    },
    'grid': {
        'enabled': True,
        'size': 50,  # mm
        'snap_tolerance': 10  # mm
    },
    'spacing': {
        'equipment_horizontal': 200,  # mm
        'equipment_vertical': 150,
        'instrument_offset': 30,
        'valve_offset': 50
    }
}

# Drawing Standards
DRAWING_STANDARDS = {
    'line_weights': {
        'process': 0.5,  # mm
        'instrument': 0.25,
        'utility': 0.35,
        'signal': 0.2
    },
    'colors': {
        'process': (0, 0, 0),        # Black
        'instrument': (0, 0, 255),   # Blue
        'signal': (255, 0, 0),       # Red
        'utility': (0, 128, 0)       # Green
    },
    'fonts': {
        'title': {'name': 'Helvetica-Bold', 'size': 16},
        'equipment': {'name': 'Helvetica-Bold', 'size': 10},
        'instrument': {'name': 'Helvetica', 'size': 8},
        'notes': {'name': 'Helvetica', 'size': 7}
    }
}

# Title Block Configuration
TITLE_BLOCK_CONFIG = {
    'height': 80,  # mm
    'fields': [
        'drawing_number',
        'drawing_title',
        'revision',
        'project_name',
        'project_code',
        'client',
        'contractor',
        'date',
        'drawn_by',
        'checked_by',
        'approved_by'
    ],
    'defaults': {
        'client': 'SARB Oil & Gas Division',
        'contractor': 'Rejlers Engineering AB',
        'drawn_by': 'AI System',
        'status': 'For Review'
    }
}

# RAG Configuration
RAG_CONFIG = {
    'knowledge_base_path': 'domain_knowledge/oil_gas/',
    'search_top_k': 5,
    'similarity_threshold': 0.7,
    'context_window': 2000
}

# Graph Analysis Configuration
GRAPH_CONFIG = {
    'connectivity_analysis': True,
    'routing_algorithm': 'a_star',  # a_star, dijkstra, orthogonal
    'layout_algorithm': 'force_directed',  # force_directed, hierarchical, topological
    'min_path_clearance': 30,  # mm
    'prefer_orthogonal': True
}

# Utility Systems
UTILITY_SYSTEMS = {
    'instrument_air': {
        'enabled': True,
        'pressure': '7 barg',
        'material': 'Carbon Steel'
    },
    'cooling_water': {
        'enabled': True,
        'supply_temp': '32°C',
        'return_temp': '42°C'
    },
    'nitrogen': {
        'enabled': True,
        'pressure': '10 barg'
    },
    'steam': {
        'enabled': True,
        'pressure': '12 barg',
        'temperature': '195°C'
    }
}

# Generation Parameters
GENERATION_PARAMS = {
    'auto_add_instruments': True,
    'auto_add_valves': True,
    'auto_add_utilities': True,
    'auto_safety_devices': True,
    'include_legends': True,
    'include_notes': True,
    'include_iso_table': True
}

# Output Configuration
OUTPUT_CONFIG = {
    'base_dir': 'media/pid_drawings_ultra/',
    'filename_template': '{drawing_number}_{intelligence_level}.pdf',
    'backup_originals': True,
    'generate_preview': True,
    'preview_resolution': 150  # DPI
}

# Validation Rules
VALIDATION_RULES = {
    'min_equipment': 1,
    'max_equipment': 100,
    'require_connections': True,
    'validate_tag_format': True,
    'check_instrument_loops': True,
    'verify_valve_placement': True
}


def get_intelligence_config(level: str) -> dict:
    """Get configuration for specified intelligence level"""
    return INTELLIGENCE_LEVELS.get(level, INTELLIGENCE_LEVELS['ultra'])


def get_output_path(drawing_number: str, intelligence_level: str) -> str:
    """Generate output path based on configuration"""
    import os
    filename = OUTPUT_CONFIG['filename_template'].format(
        drawing_number=drawing_number,
        intelligence_level=intelligence_level
    )
    return os.path.join(OUTPUT_CONFIG['base_dir'], filename)


def validate_config() -> bool:
    """Validate configuration consistency"""
    required_keys = ['basic', 'professional', 'ultra']
    return all(key in INTELLIGENCE_LEVELS for key in required_keys)
