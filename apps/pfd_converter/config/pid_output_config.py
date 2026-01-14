"""
P&ID Output Configuration - Soft Coded Settings
==================================================
Configuration for P&ID drawing generation matching expected output format.
This allows easy customization without modifying core generator code.

Usage:
    from .config.pid_output_config import PID_OUTPUT_CONFIG
    config = PID_OUTPUT_CONFIG['default']  # or 'rejlers', 'client_a', etc.
"""

from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.lib.pagesizes import A1, A0, landscape
from datetime import datetime


# ==========================================
# DEFAULT CONFIGURATION
# ==========================================
DEFAULT_CONFIG = {
    # Page Settings
    'page_size': landscape(A1),  # A1 landscape (841mm x 594mm)
    'margins': {
        'top': 20 * mm,
        'bottom': 20 * mm,
        'left': 20 * mm,
        'right': 20 * mm
    },
    
    # Title Block Configuration (matching P&ID-001_Drawing.pdf output)
    'title_block': {
        'enabled': True,
        'position': 'bottom-right',  # 'bottom-right', 'bottom-left', 'top-right'
        'width': 200 * mm,
        'height': 100 * mm,
        'border_width': 1.0,  # Line width in points
        
        # Title block fields (in order)
        'fields': [
            {
                'name': 'drawing_title',
                'label': 'DRAWING TITLE',
                'font': 'Helvetica-Bold',
                'font_size': 6 * mm,
                'y_position': 75 * mm,  # From bottom of title block
                'x_offset': 5 * mm,
                'formatter': lambda x: x.upper() if x else 'P&ID DRAWING'
            },
            {
                'name': 'project_name',
                'label': 'Project',
                'font': 'Helvetica',
                'font_size': 3 * mm,
                'y_position': 50 * mm,
                'x_offset': 5 * mm,
                'formatter': lambda x: f"Project: {x}" if x else 'Project: N/A'
            },
            {
                'name': 'drawing_number',
                'label': 'Drawing No',
                'font': 'Helvetica-Bold',
                'font_size': 5 * mm,
                'y_position': 28 * mm,
                'x_offset': 5 * mm,
                'formatter': lambda x: f"Drawing No: {x}" if x else 'Drawing No: PID-001'
            },
            {
                'name': 'revision',
                'label': 'Rev',
                'font': 'Helvetica-Bold',
                'font_size': 5 * mm,
                'y_position': 8 * mm,
                'x_offset': 5 * mm,
                'formatter': lambda x: f"Rev: {x}" if x else 'Rev: A'
            },
            {
                'name': 'date',
                'label': 'Date',
                'font': 'Helvetica-Bold',
                'font_size': 5 * mm,
                'y_position': 8 * mm,
                'x_offset': 100 * mm,
                'formatter': lambda x: f"Date: {x}" if x else f"Date: {datetime.now().strftime('%Y-%m-%d')}"
            },
            {
                'name': 'generated_timestamp',
                'label': 'Generated',
                'font': 'Helvetica',
                'font_size': 2.5 * mm,
                'y_position': 2 * mm,
                'x_offset': 5 * mm,
                'formatter': lambda x: f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            }
        ],
        
        # Divider lines in title block
        'dividers': [
            {'y_position': 60 * mm},
            {'y_position': 40 * mm},
            {'y_position': 20 * mm}
        ]
    },
    
    # Line Weights (ISO/ISA standards)
    'line_weights': {
        'border': 1.0,      # Border and title block
        'equipment': 0.7,   # Equipment outlines
        'process': 0.5,     # Process lines
        'instrument': 0.25, # Instrument signals
        'grid': 0.1         # Grid lines (optional)
    },
    
    # Text Sizes (in mm)
    'text_sizes': {
        'title': 6,           # Drawing title
        'equipment_tag': 5,   # Equipment tags (V-3601)
        'equipment_name': 3,  # Equipment names
        'line_number': 3,     # Line numbers
        'instrument': 2.5,    # Instrument tags
        'notes': 2.5          # General notes
    },
    
    # Symbol Sizes
    'symbol_sizes': {
        'instrument_circle': 15 * mm,  # ISA instrument circle diameter
        'valve_width': 8 * mm,          # Valve symbol width
        'valve_height': 8 * mm,         # Valve symbol height
        'equipment_min_width': 40 * mm,
        'equipment_min_height': 60 * mm
    },
    
    # Colors (technical drawings typically use black)
    'colors': {
        'primary': colors.black,
        'secondary': colors.black,
        'equipment': colors.black,
        'piping': colors.black,
        'instruments': colors.black,
        'text': colors.black
    },
    
    # Legend Configuration
    'legend': {
        'enabled': True,
        'position': 'top-left',  # 'top-left', 'top-right', 'bottom-left'
        'x_offset': 10 * mm,
        'y_offset': 30 * mm,
        'title': 'LEGEND',
        'title_font': 'Helvetica-Bold',
        'title_size': 3 * mm,
        'item_font': 'Helvetica',
        'item_size': 2.5 * mm,
        'line_spacing': 6 * mm,
        'items': [
            {'symbol': '━━━', 'description': 'Process Line'},
            {'symbol': '- - -', 'description': 'Instrument Signal'},
            {'symbol': '⬡', 'description': 'Gate Valve'},
            {'symbol': '◇', 'description': 'Control Valve'},
            {'symbol': '○', 'description': 'Instrument (Field)'},
            {'symbol': '◯', 'description': 'Instrument (Panel)'}
        ]
    },
    
    # Notes Configuration
    'notes': {
        'enabled': True,
        'position': 'bottom-left',
        'x_offset': 10 * mm,
        'y_offset': 60 * mm,
        'title': 'GENERAL NOTES',
        'title_font': 'Helvetica-Bold',
        'title_size': 3 * mm,
        'item_font': 'Helvetica',
        'item_size': 2.5 * mm,
        'line_spacing': 6 * mm,
        'items': [
            '1. All dimensions in millimeters unless noted',
            '2. All instruments per ISA 5.1 standard',
            '3. Line numbers indicate: Size-Fluid-Spec-Line Number',
            '4. Equipment tags per project standards'
        ]
    },
    
    # Layout Grid (for automatic equipment placement)
    'layout_grid': {
        'enabled': True,
        'columns': 4,
        'rows': 3,
        'spacing_x': 150 * mm,
        'spacing_y': 120 * mm,
        'start_x': 100 * mm,
        'start_y': 150 * mm
    }
}


# ==========================================
# REJLERS-SPECIFIC CONFIGURATION
# ==========================================
REJLERS_CONFIG = {
    **DEFAULT_CONFIG,  # Inherit default configuration
    
    # Override title block for Rejlers branding
    'title_block': {
        **DEFAULT_CONFIG['title_block'],
        'fields': [
            {
                'name': 'company_name',
                'label': 'Company',
                'font': 'Helvetica-Bold',
                'font_size': 5 * mm,
                'y_position': 90 * mm,
                'x_offset': 5 * mm,
                'formatter': lambda x: 'REJLERS ABU DHABI'
            },
            {
                'name': 'company_subtitle',
                'label': 'Subtitle',
                'font': 'Helvetica',
                'font_size': 2.5 * mm,
                'y_position': 85 * mm,
                'x_offset': 5 * mm,
                'formatter': lambda x: 'Engineering & Design Consultancy'
            },
            {
                'name': 'drawing_title',
                'label': 'DRAWING TITLE',
                'font': 'Helvetica-Bold',
                'font_size': 6 * mm,
                'y_position': 70 * mm,
                'x_offset': 5 * mm,
                'formatter': lambda x: x.upper() if x else 'P&ID DRAWING'
            },
            {
                'name': 'project_name',
                'label': 'Project',
                'font': 'Helvetica',
                'font_size': 3 * mm,
                'y_position': 50 * mm,
                'x_offset': 5 * mm,
                'formatter': lambda x: f"Project: {x}" if x else 'Project: N/A'
            },
            {
                'name': 'drawing_number',
                'label': 'Drawing No',
                'font': 'Helvetica-Bold',
                'font_size': 5 * mm,
                'y_position': 28 * mm,
                'x_offset': 5 * mm,
                'formatter': lambda x: f"Drawing No: {x}" if x else 'Drawing No: PID-001'
            },
            {
                'name': 'revision',
                'label': 'Rev',
                'font': 'Helvetica-Bold',
                'font_size': 5 * mm,
                'y_position': 8 * mm,
                'x_offset': 5 * mm,
                'formatter': lambda x: f"Rev: {x}" if x else 'Rev: A'
            },
            {
                'name': 'date',
                'label': 'Date',
                'font': 'Helvetica-Bold',
                'font_size': 5 * mm,
                'y_position': 8 * mm,
                'x_offset': 100 * mm,
                'formatter': lambda x: f"Date: {x}" if x else f"Date: {datetime.now().strftime('%Y-%m-%d')}"
            },
            {
                'name': 'generated_timestamp',
                'label': 'Generated',
                'font': 'Helvetica',
                'font_size': 2.5 * mm,
                'y_position': 2 * mm,
                'x_offset': 5 * mm,
                'formatter': lambda x: f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            }
        ]
    }
}


# ==========================================
# A0 SIZE CONFIGURATION (Larger drawings)
# ==========================================
A0_CONFIG = {
    **DEFAULT_CONFIG,
    'page_size': landscape(A0),  # A0 landscape (1189mm x 841mm)
    'margins': {
        'top': 25 * mm,
        'bottom': 25 * mm,
        'left': 25 * mm,
        'right': 25 * mm
    },
    'title_block': {
        **DEFAULT_CONFIG['title_block'],
        'width': 250 * mm,
        'height': 120 * mm
    },
    'layout_grid': {
        **DEFAULT_CONFIG['layout_grid'],
        'columns': 5,
        'rows': 4,
        'spacing_x': 180 * mm,
        'spacing_y': 150 * mm
    }
}


# ==========================================
# ADNOC FORMAT CONFIGURATION (Professional Oil & Gas Standard)
# ==========================================
ADNOC_CONFIG = {
    **DEFAULT_CONFIG,
    
    # Override title block with comprehensive ADNOC format
    'title_block': {
        'enabled': True,
        'position': 'bottom-right',
        'width': 200 * mm,
        'height': 400 * mm,  # Much taller for all sections
        'border_width': 0.7,
        
        # Comprehensive field structure matching graph_based_pid_generator
        'sections': {
            # Title Section (Top)
            'title': {
                'y_position': 360 * mm,  # From bottom of title block
                'fields': [
                    {
                        'name': 'drawing_title',
                        'font': 'Helvetica-Bold',
                        'font_size': 8 * mm,
                        'alignment': 'center',
                        'y_offset': 20 * mm,
                        'formatter': lambda x: x.upper() if x else 'P&ID DRAWING'
                    },
                    {
                        'name': 'project_name',
                        'font': 'Helvetica',
                        'font_size': 4 * mm,
                        'alignment': 'center',
                        'y_offset': 10 * mm,
                        'formatter': lambda x: x if x else 'Project Name'
                    }
                ]
            },
            
            # Project Information Section
            'project_info': {
                'y_position': 330 * mm,
                'fields': [
                    {
                        'name': 'client',
                        'label': 'CLIENT:',
                        'font': 'Helvetica',
                        'font_size': 3 * mm,
                        'y_offset': 7 * mm,
                        'x_label_offset': 5 * mm,
                        'x_value_offset': 30 * mm,
                        'formatter': lambda x: x if x else 'ADNOC - Abu Dhabi National Oil Company'
                    },
                    {
                        'name': 'project_code',
                        'label': 'PROJECT:',
                        'font': 'Helvetica',
                        'font_size': 3 * mm,
                        'y_offset': 13 * mm,
                        'x_label_offset': 5 * mm,
                        'x_value_offset': 30 * mm,
                        'formatter': lambda x: f"{x} - {'{project_name}'}" if x else 'PROJECT-CODE'
                    },
                    {
                        'name': 'contractor',
                        'label': 'CONTRACTOR:',
                        'font': 'Helvetica',
                        'font_size': 3 * mm,
                        'y_offset': 19 * mm,
                        'x_label_offset': 5 * mm,
                        'x_value_offset': 30 * mm,
                        'formatter': lambda x: x if x else 'Rejlers AB - Engineering Solutions'
                    }
                ]
            },
            
            # Drawing Identification Section
            'identification': {
                'y_position': 280 * mm,
                'fields': [
                    {
                        'name': 'drawing_number',
                        'label': 'DWG NO:',
                        'font': 'Helvetica-Bold',
                        'font_size': 6 * mm,
                        'y_offset': 10 * mm,
                        'x_offset': 5 * mm,
                        'formatter': lambda x: f"DWG NO: {x}" if x else 'DWG NO: PID-001'
                    },
                    {
                        'name': 'revision',
                        'label': 'REV:',
                        'font': 'Helvetica-Bold',
                        'font_size': 5 * mm,
                        'y_offset': 10 * mm,
                        'x_offset': 170 * mm,
                        'alignment': 'right',
                        'formatter': lambda x: f"REV: {x}" if x else 'REV: A'
                    },
                    {
                        'name': 'sheet_number',
                        'font': 'Helvetica',
                        'font_size': 3 * mm,
                        'y_offset': 17 * mm,
                        'x_offset': 5 * mm,
                        'formatter': lambda x: 'SHEET: 1 of 1'
                    },
                    {
                        'name': 'scale',
                        'font': 'Helvetica',
                        'font_size': 3 * mm,
                        'y_offset': 17 * mm,
                        'x_offset': 50 * mm,
                        'formatter': lambda x: 'SCALE: NTS'
                    },
                    {
                        'name': 'status',
                        'font': 'Helvetica-Bold',
                        'font_size': 3 * mm,
                        'y_offset': 17 * mm,
                        'x_offset': 100 * mm,
                        'formatter': lambda x: x if x else 'STATUS: IFA'
                    }
                ]
            },
            
            # Revision History Table
            'revision_table': {
                'y_position': 230 * mm,
                'enabled': True,
                'headers': ['REV', 'DATE', 'DESCRIPTION', 'BY', 'CHK', 'APP'],
                'column_widths': [12*mm, 25*mm, 98*mm, 20*mm, 20*mm, 20*mm],
                'header_font_size': 2.5 * mm,
                'row_font_size': 2.5 * mm,
                'row_height': 17 * mm,
                'rows': [
                    {
                        'rev': lambda x: x.get('revision', 'A'),
                        'date': lambda x: datetime.now().strftime("%d-%b-%Y"),
                        'description': lambda x: 'AI-Generated P&ID from PFD',
                        'by': lambda x: 'AI',
                        'chk': lambda x: 'ENG',
                        'app': lambda x: 'PM'
                    }
                ]
            },
            
            # Approval Section
            'approval': {
                'y_position': 160 * mm,
                'fields': [
                    {
                        'label': 'PREPARED BY:',
                        'value': 'AI System',
                        'date': datetime.now().strftime("%d-%b-%Y"),
                        'x_offset': 5 * mm,
                        'font_size': 2.5 * mm
                    },
                    {
                        'label': 'CHECKED BY:',
                        'value': 'Engineering',
                        'date': '______________',
                        'x_offset': 90 * mm,
                        'font_size': 2.5 * mm
                    },
                    {
                        'label': 'APPROVED BY:',
                        'value': 'Project Manager',
                        'date': '______________',
                        'x_offset': 175 * mm,
                        'font_size': 2.5 * mm
                    }
                ]
            },
            
            # Standards & References
            'standards': {
                'y_position': 120 * mm,
                'fields': [
                    {
                        'text': 'STANDARDS: ISA 5.1, ISO 10628, ASME B31.3',
                        'font_size': 2 * mm,
                        'y_offset': 4 * mm,
                        'x_offset': 3 * mm
                    },
                    {
                        'text': 'UNITS: Metric (mm, kg, kPa) unless noted',
                        'font_size': 2 * mm,
                        'y_offset': 8 * mm,
                        'x_offset': 3 * mm
                    }
                ]
            },
            
            # Bottom info
            'bottom_info': {
                'y_position': 8 * mm,
                'fields': [
                    {
                        'name': 'revision_footer',
                        'formatter': lambda x: f"Rev: {x.get('revision', 'A')}",
                        'x_offset': 5 * mm,
                        'font_size': 2 * mm
                    },
                    {
                        'name': 'date_footer',
                        'formatter': lambda x: f"Date: {datetime.now().strftime('%Y-%m-%d')}",
                        'x_offset': 100 * mm,
                        'font_size': 2 * mm
                    },
                    {
                        'name': 'generation_method',
                        'formatter': lambda x: 'Generated: AI-Powered P&ID System',
                        'x_offset': 5 * mm,
                        'y_offset': 2 * mm,
                        'font_size': 2 * mm
                    }
                ]
            }
        },
        
        # Section divider lines
        'dividers': [
            {'y_position': 360 * mm},  # After title
            {'y_position': 305 * mm},  # After project info
            {'y_position': 257 * mm},  # After identification
            {'y_position': 192 * mm},  # After revision table
            {'y_position': 138 * mm},  # After approval
            {'y_position': 110 * mm},  # After standards
        ],
        
        # Simple fields for backward compatibility
        'fields': []  # Not used in ADNOC format
    }
}


# ==========================================
# CONFIGURATION REGISTRY
# ==========================================
PID_OUTPUT_CONFIG = {
    'default': DEFAULT_CONFIG,
    'rejlers': REJLERS_CONFIG,
    'adnoc': ADNOC_CONFIG,
    'a0': A0_CONFIG,
}


# ==========================================
# UTILITY FUNCTIONS
# ==========================================
def get_config(config_name: str = 'default'):
    """
    Get configuration by name
    
    Args:
        config_name: Name of configuration ('default', 'rejlers', 'a0')
        
    Returns:
        dict: Configuration dictionary
    """
    return PID_OUTPUT_CONFIG.get(config_name, DEFAULT_CONFIG)


def merge_config(base_config: dict, overrides: dict):
    """
    Merge configuration with overrides
    
    Args:
        base_config: Base configuration dictionary
        overrides: Override values
        
    Returns:
        dict: Merged configuration
    """
    import copy
    merged = copy.deepcopy(base_config)
    
    def deep_merge(base, override):
        for key, value in override.items():
            if key in base and isinstance(base[key], dict) and isinstance(value, dict):
                deep_merge(base[key], value)
            else:
                base[key] = value
    
    deep_merge(merged, overrides)
    return merged
