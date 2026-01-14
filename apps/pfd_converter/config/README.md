# P&ID Output Configuration Guide

## Overview
The P&ID generation system now uses **soft-coded configuration** for easy customization of output format, title blocks, legends, and all visual aspects without modifying core generator code.

## Quick Start

### Using Default Configuration
```python
from apps.pfd_converter.programmatic_pid_generator import generate_pid_from_specs

drawing_specs = {
    'drawing_number': 'P&ID-001',
    'drawing_title': 'Process Flow Diagram',
    'project_name': 'Oil & Gas Project',
    'revision': 'A',
    'equipment': [...],
    'piping': [...],
    'instrumentation': [...]
}

# Generate with default configuration
pdf_path = generate_pid_from_specs(drawing_specs, 'output.pdf')
```

### Using Rejlers Configuration
```python
# Use Rejlers branding and formatting
pdf_path = generate_pid_from_specs(
    drawing_specs, 
    'output.pdf',
    config_name='rejlers'
)
```

### Using A0 Size Configuration
```python
# Generate larger A0 size drawing
pdf_path = generate_pid_from_specs(
    drawing_specs, 
    'output.pdf',
    config_name='a0'
)
```

### Custom Configuration Overrides
```python
from reportlab.lib.units import mm

# Override specific configuration values
custom_overrides = {
    'title_block': {
        'width': 250 * mm,
        'height': 120 * mm
    },
    'text_sizes': {
        'title': 8,  # Larger title text
        'equipment_tag': 6
    }
}

pdf_path = generate_pid_from_specs(
    drawing_specs,
    'output.pdf',
    config_overrides=custom_overrides
)
```

## Configuration Options

### Available Configurations
1. **`default`** - Standard A1 landscape format with basic title block
2. **`rejlers`** - Rejlers Abu Dhabi branding and formatting
3. **`a0`** - Larger A0 size for complex drawings

## Configuration Structure

### Page Settings
```python
{
    'page_size': landscape(A1),  # or landscape(A0)
    'margins': {
        'top': 20 * mm,
        'bottom': 20 * mm,
        'left': 20 * mm,
        'right': 20 * mm
    }
}
```

### Title Block Configuration
```python
{
    'title_block': {
        'enabled': True,
        'position': 'bottom-right',  # 'bottom-right', 'bottom-left', 'top-right', 'top-left'
        'width': 200 * mm,
        'height': 100 * mm,
        'border_width': 1.0,
        
        # Fields shown in title block
        'fields': [
            {
                'name': 'drawing_title',
                'font': 'Helvetica-Bold',
                'font_size': 6 * mm,
                'y_position': 75 * mm,
                'x_offset': 5 * mm,
                'formatter': lambda x: x.upper() if x else 'P&ID DRAWING'
            },
            # ... more fields
        ],
        
        # Divider lines
        'dividers': [
            {'y_position': 60 * mm},
            {'y_position': 40 * mm},
            {'y_position': 20 * mm}
        ]
    }
}
```

### Line Weights
```python
{
    'line_weights': {
        'border': 1.0,      # Border and title block
        'equipment': 0.7,   # Equipment outlines
        'process': 0.5,     # Process lines
        'instrument': 0.25, # Instrument signals
        'grid': 0.1         # Grid lines
    }
}
```

### Text Sizes
```python
{
    'text_sizes': {
        'title': 6,           # mm - Drawing title
        'equipment_tag': 5,   # mm - Equipment tags (V-3601)
        'equipment_name': 3,  # mm - Equipment names
        'line_number': 3,     # mm - Line numbers
        'instrument': 2.5,    # mm - Instrument tags
        'notes': 2.5          # mm - General notes
    }
}
```

### Symbol Sizes
```python
{
    'symbol_sizes': {
        'instrument_circle': 15 * mm,  # ISA instrument circle diameter
        'valve_width': 8 * mm,
        'valve_height': 8 * mm,
        'equipment_min_width': 40 * mm,
        'equipment_min_height': 60 * mm
    }
}
```

### Legend Configuration
```python
{
    'legend': {
        'enabled': True,
        'position': 'top-left',  # 'top-left', 'top-right', 'bottom-left', 'bottom-right'
        'x_offset': 10 * mm,
        'y_offset': 30 * mm,
        'title': 'LEGEND',
        'items': [
            {'symbol': '━━━', 'description': 'Process Line'},
            {'symbol': '- - -', 'description': 'Instrument Signal'},
            # ... more items
        ]
    }
}
```

### Notes Configuration
```python
{
    'notes': {
        'enabled': True,
        'position': 'bottom-left',
        'x_offset': 10 * mm,
        'y_offset': 60 * mm,
        'title': 'GENERAL NOTES',
        'items': [
            '1. All dimensions in millimeters unless noted',
            '2. All instruments per ISA 5.1 standard',
            # ... more notes
        ]
    }
}
```

## Creating Custom Configurations

### Method 1: Modify Existing Configuration File
Edit `backend/apps/pfd_converter/config/pid_output_config.py` and add your custom configuration:

```python
# Add to PID_OUTPUT_CONFIG dictionary
CLIENT_A_CONFIG = {
    **DEFAULT_CONFIG,
    'title_block': {
        # Your custom title block
    }
}

PID_OUTPUT_CONFIG = {
    'default': DEFAULT_CONFIG,
    'rejlers': REJLERS_CONFIG,
    'a0': A0_CONFIG,
    'client_a': CLIENT_A_CONFIG,  # Add your custom config
}
```

Then use it:
```python
pdf_path = generate_pid_from_specs(specs, 'output.pdf', config_name='client_a')
```

### Method 2: Runtime Overrides
```python
from apps.pfd_converter.config.pid_output_config import get_config, merge_config

# Get base configuration
base_config = get_config('default')

# Define your overrides
overrides = {
    'title_block': {
        'fields': [
            {
                'name': 'company_logo',
                'font': 'Helvetica-Bold',
                'font_size': 8 * mm,
                'y_position': 95 * mm,
                'x_offset': 5 * mm,
                'formatter': lambda x: 'YOUR COMPANY NAME'
            },
            # ... rest of your fields
        ]
    }
}

# Merge configurations
custom_config = merge_config(base_config, overrides)

# Use merged configuration
from apps.pfd_converter.programmatic_pid_generator import ProgrammaticPIDGenerator
generator = ProgrammaticPIDGenerator(drawing_specs, config_overrides=overrides)
pdf_path = generator.generate('output.pdf')
```

## Title Block Field Formatters

Formatters allow you to transform field values dynamically:

```python
# Uppercase transformation
'formatter': lambda x: x.upper() if x else 'DEFAULT'

# Date formatting
'formatter': lambda x: f"Date: {datetime.now().strftime('%Y-%m-%d')}"

# Conditional formatting
'formatter': lambda x: f"Rev: {x}" if x else 'Rev: A'

# Complex formatting
'formatter': lambda x: f"Drawing No: {x.replace('_', '-')}" if x else 'Drawing No: N/A'
```

## Environment-Based Configuration

You can set different configurations based on environment:

```python
import os

# In your settings or service
config_name = os.getenv('PID_OUTPUT_CONFIG', 'default')

# Use environment-specific configuration
pdf_path = generate_pid_from_specs(specs, 'output.pdf', config_name=config_name)
```

Add to your `.env` file:
```bash
# For production
PID_OUTPUT_CONFIG=rejlers

# For development
PID_OUTPUT_CONFIG=default
```

## Aligning with Expected Output

To match the format shown in `P&ID-001_Drawing.pdf`:

1. **Use the default configuration** - It's already aligned
2. **Or create custom config** matching your exact requirements:

```python
CUSTOM_CONFIG = {
    **DEFAULT_CONFIG,
    'title_block': {
        'enabled': True,
        'position': 'bottom-right',
        'width': 200 * mm,
        'height': 100 * mm,
        'border_width': 1.0,
        'fields': [
            # Match exact field positions from your expected output
        ]
    }
}
```

## Testing Your Configuration

```python
# Test script
from apps.pfd_converter.programmatic_pid_generator import generate_pid_from_specs

test_specs = {
    'drawing_number': 'TEST-001',
    'drawing_title': 'Test Drawing',
    'project_name': 'Test Project',
    'revision': 'A',
    'equipment': [],
    'piping': [],
    'instrumentation': []
}

# Generate test PDF
pdf_path = generate_pid_from_specs(
    test_specs,
    'test_output.pdf',
    config_name='default'  # or 'rejlers', 'a0', etc.
)

print(f"Test PDF generated: {pdf_path}")
```

## Troubleshooting

### Title Block Not Showing
Check that `'enabled': True` in title block configuration.

### Wrong Position
Verify `'position'` value: `'bottom-right'`, `'bottom-left'`, `'top-right'`, `'top-left'`

### Text Not Visible
- Check font sizes in `'text_sizes'`
- Verify y_position values in title block fields
- Ensure margins don't overlap with content

### Configuration Not Applied
```python
# Make sure you're passing the config_name or config_overrides
pdf_path = generate_pid_from_specs(
    specs,
    'output.pdf',
    config_name='your_config'  # ← Don't forget this!
)
```

## Best Practices

1. **Start with default configuration** and override only what you need
2. **Test configurations** with minimal drawing specs first
3. **Document custom configurations** for team reference
4. **Use environment variables** for deployment-specific configs
5. **Keep field formatters simple** for maintainability
6. **Version control your configs** for tracking changes

## Example: Complete Custom Implementation

```python
from apps.pfd_converter.programmatic_pid_generator import generate_pid_from_specs
from reportlab.lib.units import mm
from datetime import datetime

# Define drawing specifications
drawing_specs = {
    'drawing_number': 'P16093-14-01-08-1602',
    'drawing_title': 'PROCESS FLOW DIAGRAM',
    'project_name': 'OIL & GAS PROCESSING FACILITY',
    'revision': 'B',
    'date': '2026-01-12',
    'equipment': [
        {'tag': 'V-3601', 'name': 'Feed Tank', 'type': 'vessel'},
        {'tag': 'P-3601A/B', 'name': 'Feed Pumps', 'type': 'pump'},
    ],
    'piping': [
        {
            'from_equipment': 'V-3601',
            'to_equipment': 'P-3601A',
            'line_number': '1"-P-001-CS'
        }
    ],
    'instrumentation': [
        {'tag': 'LT-3601', 'type': 'level_transmitter'},
        {'tag': 'PT-3601', 'type': 'pressure_transmitter'}
    ],
    'valves': [
        {'tag': 'HV-3601', 'type': 'gate'},
        {'tag': 'PCV-3601', 'type': 'control'}
    ]
}

# Generate with Rejlers configuration
pdf_path = generate_pid_from_specs(
    drawing_specs,
    'P16093_PID_Output.pdf',
    config_name='rejlers'
)

print(f"✅ P&ID generated successfully: {pdf_path}")
```

## Support

For issues or questions about configuration:
1. Check this guide
2. Review `pid_output_config.py` for available options
3. Test with minimal specs to isolate issues
4. Contact the development team

---

**Last Updated:** January 12, 2026
**Version:** 1.0
