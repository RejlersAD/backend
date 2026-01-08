# Comprehensive PFD Analysis System

## Overview
Automatic comprehensive analysis of Process Flow Diagrams (PFDs) using GPT-4 Vision, extracting all technical details including equipment specifications, line sizes, instrumentation, valves, safety devices, and more.

## Features

### Soft-Coded Configuration System
The analysis is driven by `ANALYSIS_CONFIG` in `comprehensive_analysis_service.py`:

```python
ANALYSIS_CONFIG = {
    "extraction_categories": {
        "equipment": {
            "attributes": ["tag", "description", "dimensions", "material", "pressure", "temperature", ...],
            "required": True
        },
        "piping_lines": {
            "attributes": ["line_number", "size", "class", "material", "from_equipment", ...],
            "required": True
        },
        "instruments": {
            "attributes": ["tag", "type", "range", "location", "function", ...],
            "required": True
        },
        # ... more categories
    },
    "analysis_levels": {
        "detailed": {"max_tokens": 4000, "include_all": True},
        "standard": {"max_tokens": 3000, "include_most": True},
        "quick": {"max_tokens": 2000, "include_key": True}
    }
}
```

### Analysis Categories

#### 1. Equipment
- Tag numbers (e.g., V-3601)
- Descriptions (e.g., "Sahil Export Gas KOD")
- Dimensions (e.g., "7800mm x 3300mm")
- Materials (e.g., "CS + SS 316L CLAD")
- Operating conditions (pressure, temperature)
- Design standards

#### 2. Piping Lines
- Line numbers (e.g., "14-01-08-1602")
- Sizes (e.g., "16 inch")
- Pressure classes (e.g., "300#")
- Materials (e.g., "CS")
- Connections (from/to equipment)
- Insulation requirements

#### 3. Instruments
- Instrument tags (e.g., "PT-3601-01")
- Types (pressure transmitter, flow meter, etc.)
- Ranges (e.g., "0-25 barg")
- Functions (measurement, control)
- Locations
- Connections to control systems

#### 4. Valves
- Valve tags (e.g., "SDV-3601-01")
- Types (gate, globe, ball, butterfly, etc.)
- Sizes (e.g., "16 inch")
- Actuation (manual, pneumatic, electric)
- Functions (isolation, control, safety)

#### 5. Safety Devices
- PSV tags (e.g., "PSV-3601-01")
- Set pressures (e.g., "20 barg")
- Sizes
- Discharge destinations (e.g., "HP Flare")
- Relief capacities

#### 6. Process Streams
- Stream names/descriptions
- Flow rates
- Compositions
- Operating conditions
- Source and destination

## Automatic Integration

### Upload Flow
When a PFD is uploaded via http://localhost:5173/pfd/upload:

1. **Vision Extraction** - Standard GPT-4 Vision extracts basic info
2. **Comprehensive Analysis** - Automatically runs detailed analysis:
   ```python
   analysis_results = analyze_pfd_comprehensive(
       file_path=pfd_file_path,
       document_info={
           'number': pfd_doc.document_number,
           'title': pfd_doc.document_title,
           'revision': pfd_doc.revision,
           'project': pfd_doc.project
       },
       analysis_level='detailed'
   )
   ```
3. **Storage** - Results saved to `pfd_doc.comprehensive_analysis` JSON field
4. **Logging** - Counts logged (equipment, piping, instruments, valves, safety devices)

### Non-Critical Failure
Analysis runs in a try/except block - if it fails, upload continues normally:
```python
try:
    # Run comprehensive analysis
    analysis_results = analyze_pfd_comprehensive(...)
    pfd_doc.comprehensive_analysis = analysis_results
    logger.info(f"✅ Comprehensive analysis complete: {counts}")
except Exception as e:
    logger.warning(f"⚠️ Comprehensive analysis failed: {str(e)}")
    # Upload continues - analysis is supplementary
```

## Analysis Levels

### Detailed (Default)
- Max tokens: 4000
- Extracts ALL attributes from ALL categories
- Includes notes, standards, references
- Generates comprehensive summaries

### Standard
- Max tokens: 3000
- Extracts most important attributes
- Focuses on key equipment and piping
- Basic summaries

### Quick
- Max tokens: 2000
- Key equipment and piping only
- Minimal detail
- Fast processing

## Output Structure

```json
{
  "all_equipment": [
    {
      "tag": "V-3601",
      "description": "Sahil Export Gas KOD",
      "dimensions": "7800mm x 3300mm",
      "material": "CS + SS 316L CLAD",
      "design_pressure": "22.4 barg",
      "design_temperature": "55°C / -29°C",
      "internals": "Demister pad",
      "notes": "Vessel designed for gas dehydration"
    }
  ],
  "all_piping": [
    {
      "line_number": "14-01-08-1602",
      "size": "16 inch",
      "class": "300#",
      "material": "CS",
      "from_equipment": "V-3601",
      "to_equipment": "ASAB GDS",
      "description": "Export gas line"
    }
  ],
  "all_instruments": [
    {
      "tag": "PT-3601-01",
      "type": "Pressure Transmitter",
      "range": "0-25 barg",
      "location": "V-3601 outlet",
      "function": "Pressure monitoring"
    },
    {
      "tag": "FT-3601-02",
      "type": "Flow Transmitter",
      "function": "Flow measurement"
    }
  ],
  "all_valves": [
    {
      "tag": "SDV-3601-01",
      "type": "Shutdown Valve",
      "size": "16 inch",
      "actuation": "Pneumatic",
      "function": "Emergency shutdown"
    }
  ],
  "all_safety_devices": [
    {
      "tag": "PSV-3601-01",
      "type": "Pressure Safety Valve",
      "set_pressure": "20 barg",
      "size": "3 inch",
      "discharge_to": "HP Flare"
    }
  ],
  "summaries": {
    "equipment_by_type": {
      "Vessels": 1
    },
    "piping_summary": {
      "sizes": ["16 inch"],
      "classes": ["300#"],
      "materials": ["CS"]
    },
    "instrumentation_summary": {
      "by_type": {
        "Pressure Transmitters": 1,
        "Flow Transmitters": 1
      }
    }
  }
}
```

## Example: P16093_PFD.pdf Analysis

Analyzed successfully with results saved to `P16093_PFD_Analysis.json`:
- **Drawing**: 14-01-08-0001 "PROCESS FLOW DIAGRAM EXPORT PIPELINE TO ASAB GDS"
- **Equipment**: 1 vessel (V-3601) with full specifications
- **Piping**: 1 main line (16" 300# CS)
- **Instruments**: 2 transmitters (PT, FT)
- **Valves**: 1 shutdown valve (SDV)
- **Safety**: 1 PSV to HP Flare

## Configuration Customization

### Adding New Categories
```python
"new_category": {
    "attributes": ["attr1", "attr2", "attr3"],
    "required": False,
    "priority": 3
}
```

### Modifying Analysis Levels
```python
"custom_level": {
    "max_tokens": 3500,
    "include_all": True,
    "custom_instructions": "Focus on safety-critical items"
}
```

### Adjusting GPT-4 Vision Settings
```python
"gpt4_vision_settings": {
    "model": "gpt-4o",
    "temperature": 0.1,  # Lower = more consistent
    "max_tokens": 4000,
    "detail": "high",    # Image detail level
    "image_dpi": 200     # PDF conversion quality
}
```

## Database Storage

### Model Field
```python
class PFDDocument(models.Model):
    # ... existing fields ...
    comprehensive_analysis = models.JSONField(
        default=dict,
        help_text="Comprehensive GPT-4 Vision analysis with equipment specs, line sizes, instruments, etc."
    )
```

### Migration
```bash
# Created automatically
apps/pfd_converter/migrations/0003_pfddocument_comprehensive_analysis.py
```

## API Access

### Retrieve Analysis
```python
from apps.pfd_converter.models import PFDDocument

pfd = PFDDocument.objects.get(id=123)
analysis = pfd.comprehensive_analysis

# Access specific data
equipment = analysis.get('all_equipment', [])
piping = analysis.get('all_piping', [])
instruments = analysis.get('all_instruments', [])
```

### Example Query
```python
# Get all pressure transmitters
pts = [
    inst for inst in pfd.comprehensive_analysis.get('all_instruments', [])
    if inst.get('type') == 'Pressure Transmitter'
]

# Get all 16-inch lines
large_lines = [
    pipe for pipe in pfd.comprehensive_analysis.get('all_piping', [])
    if '16' in pipe.get('size', '')
]
```

## Logging

Analysis logs include:
- Start/completion messages
- Equipment count
- Piping count
- Instrument count
- Valve count
- Safety device count
- Warning if analysis fails (non-critical)

Example log:
```
✅ Comprehensive analysis complete:
   - Equipment: 1
   - Piping: 1
   - Instruments: 2
   - Valves: 1
   - Safety Devices: 1
```

## Testing

### Manual Test
1. Upload a PFD at http://localhost:5173/pfd/upload
2. Check backend logs for analysis completion
3. Retrieve document and inspect `comprehensive_analysis` field
4. Verify all categories populated correctly

### Programmatic Test
```python
from apps.pfd_converter.comprehensive_analysis_service import analyze_pfd_comprehensive

# Analyze a PFD file
results = analyze_pfd_comprehensive(
    file_path='/path/to/pfd.pdf',
    document_info={
        'number': 'P16093',
        'title': 'Test PFD',
        'revision': 'A',
        'project': 'Test Project'
    },
    analysis_level='detailed'
)

# Check results
print(f"Found {len(results['all_equipment'])} equipment items")
print(f"Found {len(results['all_piping'])} piping lines")
```

## Performance

- **PDF Conversion**: ~1-2 seconds per page (PyMuPDF)
- **GPT-4 Vision Analysis**: ~10-20 seconds per page
- **Total Time**: ~15-25 seconds for typical single-page PFD
- **Multi-page**: Analysis combines results from all pages

## Error Handling

### PDF Conversion Errors
```python
except Exception as pdf_error:
    logger.error(f"Failed to convert PDF: {pdf_error}")
    return {
        "error": "Failed to convert PDF to images",
        "details": str(pdf_error)
    }
```

### GPT-4 Vision Errors
```python
except Exception as vision_error:
    logger.warning(f"GPT-4 Vision analysis failed: {vision_error}")
    # Falls back to basic extraction
```

### Upload Flow Errors
- Analysis failure does NOT break upload
- Warning logged but process continues
- Basic vision extraction still works

## Future Enhancements

### Planned Features
1. **Real-time Analysis Progress** - WebSocket updates
2. **Comparison Mode** - Compare analyses across revisions
3. **Export to Excel** - Equipment/piping lists
4. **Search API** - Query across all PFDs
5. **ML Validation** - Verify extracted data against standards
6. **Auto-Linking** - Connect P&IDs to PFDs using equipment tags

### Configuration Extensions
1. **Industry Templates** - Oil & Gas, Power, Chemical
2. **Custom Validation Rules** - Check standards compliance
3. **Unit Conversion** - Automatic metric/imperial
4. **Multi-language** - Support for non-English drawings

## Troubleshooting

### Analysis Not Running
1. Check logs: `docker logs radai_backend_local | grep "comprehensive"`
2. Verify migration applied: `docker exec radai_backend_local python manage.py showmigrations pfd_converter`
3. Check model has field: `pfd_doc.comprehensive_analysis`

### Empty Results
1. Verify PDF is readable (not scanned image)
2. Check GPT-4 API key configured
3. Try lower analysis level ('quick' instead of 'detailed')
4. Check file size limits

### Slow Performance
1. Use 'quick' analysis level
2. Reduce image DPI in config (150 instead of 200)
3. Check GPT-4 API rate limits
4. Consider caching for repeated analyses

## Support

For issues or questions:
1. Check backend logs
2. Review `comprehensive_analysis_service.py` configuration
3. Test with P16093_PFD.pdf (known working example)
4. Verify OpenAI API access

## Summary

The comprehensive analysis system provides automatic, detailed extraction of all technical information from PFDs using a soft-coded, configurable approach. It integrates seamlessly into the upload flow and stores results in a structured JSON format for easy querying and display.

**Key Benefits:**
- ✅ Automatic execution on every PFD upload
- ✅ Soft-coded configuration for easy customization
- ✅ Comprehensive extraction (equipment, piping, instruments, valves, safety)
- ✅ Non-critical failure (doesn't break uploads)
- ✅ Structured JSON storage
- ✅ Reusable service class
- ✅ Multiple analysis levels
- ✅ Multi-page support
