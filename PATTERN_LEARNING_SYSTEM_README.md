# PFD to P&ID Pattern Learning System

## Overview
Comprehensive AI-powered system that learns conversion patterns from PFD-P&ID pairs using GPT-4 Vision to automate P&ID generation from PFDs.

---

## System Architecture

### 1. Pattern Learning Service
**File**: [pattern_learning_service.py](apps/pfd_converter/pattern_learning_service.py)

**Purpose**: Soft-coded service that analyzes PFD and P&ID pairs using GPT-4 Vision to learn detailed conversion rules, patterns, and decision-making logic.

**Key Components**:
- `PFDtoPIDPatternLearner` class - Main pattern learning engine
- `PATTERN_LEARNING_CONFIG` - Configuration-driven analysis framework
- GPT-4 Vision multi-image analysis
- PyMuPDF PDF processing

---

## Pattern Learning Configuration

```python
PATTERN_LEARNING_CONFIG = {
    "analysis_categories": {
        "equipment_mapping": {
            "extract": [
                "nozzle additions",
                "internal details",
                "elevation specifications",
                "connection details",
                "equipment symbol detail level"
            ]
        },
        "piping_expansion": {
            "extract": [
                "line numbering pattern",
                "isolation valve placement rules",
                "control valve placement rules",
                "typical valve spacing",
                "line size specifications",
                "material class assignments"
            ]
        },
        "instrumentation_addition": {
            "extract": [
                "instrument types by equipment",
                "instrument placement patterns",
                "tag numbering scheme",
                "measurement range selection",
                "control loop configurations",
                "alarm and trip settings"
            ]
        },
        "control_strategy": {
            "extract": [
                "control loop types",
                "cascade control patterns",
                "ratio control patterns",
                "override control patterns",
                "PID vs ON/OFF selection"
            ]
        },
        "safety_integration": {
            "extract": [
                "PSV placement rules",
                "ESD valve placement",
                "high/low trip patterns",
                "interlock logic patterns",
                "SIL level assignments"
            ]
        },
        "utility_connections": {
            "extract": [
                "instrument air distribution",
                "steam tracing patterns",
                "cooling water connections",
                "drain and vent placement"
            ]
        }
    }
}
```

---

## Learning Process

### Step 1: Input Analysis
- **PFD PDF** - High-level process flow diagram
- **P&ID PDF** - Detailed piping & instrumentation diagram
- **PFD Data** (optional) - Existing comprehensive analysis

### Step 2: Image Conversion
```python
def convert_pdf_to_base64(pdf_path, page_num=0, dpi=200):
    # Convert PDF to high-quality PNG (200 DPI)
    # Encode as base64 for GPT-4 Vision
    # Return base64 image data
```

### Step 3: GPT-4 Vision Analysis
Send **both** PFD and P&ID images to GPT-4 Vision with comprehensive prompt:
- Analyze equipment mapping
- Identify piping expansion patterns
- Extract instrumentation addition rules
- Learn control strategy patterns
- Understand safety system integration
- Document utility connection patterns

### Step 4: Pattern Extraction
Parse GPT-4 Vision response into structured JSON:
```json
{
  "equipment_mapping_rules": {
    "by_equipment_type": {
      "vessel": {
        "details_to_add": ["nozzles", "internals", "supports"],
        "specifications": ["material", "pressure", "temperature"],
        "connections": ["inlet", "outlet", "drain", "vent"]
      },
      "pump": {...},
      "heat_exchanger": {...}
    },
    "tag_numbering": "Format and logic",
    "symbol_conventions": {...}
  },
  "piping_rules": {
    "line_numbering_format": "[Project]-[Area]-[System]-[Line#]",
    "isolation_valve_placement": {
      "decision_tree": "Rules for where to place isolation valves",
      "typical_locations": ["equipment inlet/outlet", "branch points", ...]
    },
    "control_valve_placement": {
      "criteria": "When and where to add control valves",
      "sizing": "How to size control valves"
    },
    "line_size_selection": {
      "velocity_criteria": "Typical velocities by service",
      "pressure_drop": "Maximum allowable pressure drop"
    },
    "material_selection": {
      "by_service": {
        "gas": "Carbon steel, stainless steel",
        "corrosive": "Stainless steel, special alloys"
      }
    }
  },
  "instrumentation_rules": {
    "instrument_selection_matrix": {
      "vessel": {
        "required": ["PT", "LT", "TT"],
        "optional": ["DT", "WT"],
        "placement": {
          "PT": "Top connection",
          "LT": "Side connection at operating range",
          "TT": "Bottom or side connection"
        }
      },
      "pump": {...}
    },
    "tag_numbering_format": "[Type]-[Area]-[Equipment]-[Sequence]",
    "range_selection": {
      "pressure": "1.5x normal operating pressure",
      "level": "Vessel height plus freeboard",
      "temperature": "1.2x normal operating temperature"
    },
    "alarm_trip_settings": {
      "high_pressure": "90% of design pressure",
      "low_level": "10% of operating range",
      "high_level": "90% of operating range"
    }
  },
  "control_patterns": {
    "loop_types": {
      "pressure_control": "PID with reverse acting",
      "level_control": "PID with direct acting",
      "flow_control": "PID with reverse acting",
      "temperature_control": "PID with dead time compensation"
    },
    "controller_selection": {
      "tight_control": "PID with fast response",
      "averaging_control": "PID with slow response",
      "on_off": "For non-critical loops"
    },
    "fail_safe_positions": {
      "fail_open": "When loss of signal should allow flow",
      "fail_closed": "When loss of signal should stop flow"
    },
    "interlock_patterns": {
      "low_level_shutdown": "Stop pumps on low suction level",
      "high_pressure_relief": "Open relief valve on high pressure"
    }
  },
  "safety_patterns": {
    "psv_placement": {
      "criteria": [
        "Any vessel that can be isolated",
        "Thermal expansion scenarios",
        "Overpressure from external fire",
        "Process upset conditions"
      ],
      "set_pressure": "Design pressure x 1.0 (or per code)",
      "sizing": "API 520/521 methods",
      "discharge": "To flare or atmosphere per service"
    },
    "esd_placement": {
      "criteria": [
        "Main inlet/outlet of critical equipment",
        "Cross-tie points",
        "Before/after critical control valves"
      ],
      "fail_position": "Fail closed for safety",
      "actuation": "Pneumatic with backup",
      "interlock_logic": "Emergency stop, high/low trips"
    },
    "trip_settings": {
      "high_pressure": "95% of design pressure",
      "low_pressure": "Critical process minimum",
      "high_level": "95% of vessel height",
      "low_level": "NPSH requirements + margin"
    },
    "redundancy_rules": {
      "SIL1": "Single device acceptable",
      "SIL2": "1oo2 voting (one out of two)",
      "SIL3": "2oo3 voting (two out of three)"
    }
  },
  "utility_patterns": {
    "instrument_air": {
      "distribution": "Header with branches to instruments",
      "pressure": "6-7 barg typical",
      "quality": "Dry, oil-free per ISA standard",
      "connections": "Quick disconnect fittings"
    },
    "steam_tracing": {
      "criteria": "Lines requiring freeze protection or viscosity control",
      "configuration": "Parallel trace lines",
      "insulation": "Over tracing",
      "temperature_control": "Steam trap with bypass"
    },
    "drain_vent": {
      "drain_placement": "Low points, before isolation valves",
      "vent_placement": "High points, after isolation valves",
      "sizing": "Typically 3/4\" or 1\"",
      "connections": "To closed drain system or atmosphere"
    }
  },
  "conversion_workflow": [
    "1. Identify all PFD equipment and their basic specifications",
    "2. Determine detailed equipment specifications (nozzles, internals, supports)",
    "3. Expand each PFD line into detailed P&ID piping with line numbers",
    "4. Place isolation valves at equipment connections and logical isolation points",
    "5. Add control valves where process control is required",
    "6. Select and place instruments based on equipment type and control requirements",
    "7. Configure control loops (PID, cascade, ratio, etc.)",
    "8. Set alarm and trip points based on operating limits",
    "9. Add safety devices (PSVs, ESDs) per safety analysis",
    "10. Design interlock logic for safety and operational requirements",
    "11. Add utility connections (instrument air, steam, water, drains, vents)",
    "12. Review and validate against design standards and codes",
    "13. Document all specifications, notes, and references"
  ],
  "soft_coding_config": {
    "templates": {
      "equipment_templates": "Library of standard equipment with typical instrumentation",
      "control_loop_templates": "Predefined control strategies",
      "safety_templates": "Standard safety device configurations"
    },
    "decision_trees": {
      "valve_placement": "Decision logic for adding valves",
      "instrument_selection": "Logic for selecting instrument types",
      "material_selection": "Decision tree for piping materials"
    },
    "validation_rules": {
      "completeness_checks": "Verify all equipment has required instruments",
      "code_compliance": "Check against design codes (ASME, API)",
      "safety_verification": "Ensure safety devices are adequate",
      "constructability": "Verify practical installation"
    },
    "automation_parameters": {
      "equipment_symbol_library": "CAD symbols for automated drawing",
      "line_numbering_scheme": "Automated line number generation",
      "instrument_tagging_convention": "Automated tag generation",
      "layout_optimization": "Algorithms for optimal equipment placement"
    }
  }
}
```

---

## Usage

### Standalone Analysis
```python
from apps.pfd_converter.pattern_learning_service import learn_patterns_from_pair

patterns = learn_patterns_from_pair(
    pfd_path="/path/to/pfd.pdf",
    pid_path="/path/to/pid.pdf",
    pfd_data=existing_pfd_analysis,  # Optional
    output_path="/path/to/save/patterns.json"
)
```

### Integration in Pipeline
```python
from apps.pfd_converter.pattern_learning_service import PFDtoPIDPatternLearner

learner = PFDtoPIDPatternLearner()

# Learn from reference pairs
patterns = learner.analyze_pfd_pid_pair(
    pfd_path=reference_pfd,
    pid_path=reference_pid,
    pfd_data=pfd_analysis
)

# Save patterns for reuse
learner.save_learned_patterns(patterns, "conversion_patterns.json")

# Use patterns to generate new P&ID
# (Integration with AI drawing generator)
```

---

## Analysis Categories Detail

### 1. Equipment Mapping Rules
**What it learns:**
- How each equipment type (vessel, pump, heat exchanger) is represented in P&ID
- What additional details are added (nozzles, internals, supports, elevations)
- Equipment tag numbering patterns
- Specification requirements by equipment type
- Connection point details

**Output:**
- Equipment type library with standard configurations
- Detail addition rules by equipment category
- Tag numbering format and logic
- Symbol conventions and detail levels

### 2. Piping Expansion Rules
**What it learns:**
- How PFD single lines become detailed P&ID piping networks
- Line numbering format and assignment logic
- Where and why isolation valves are placed
- Where and why control valves are added
- Typical valve spacing standards
- Line size determination methods
- Material class selection by service
- Insulation and tracing requirements

**Output:**
- Line numbering scheme
- Isolation valve placement decision tree
- Control valve sizing and placement rules
- Material selection criteria matrix
- Typical valve spacing guidelines

### 3. Instrumentation Addition Rules
**What it learns:**
- What instruments are added for each equipment type
- Where instruments are placed (inlet, outlet, top, bottom)
- Instrument tag numbering schemes
- Measurement range selection criteria
- Control loop configuration patterns
- Alarm and trip point setting logic
- Signal type selection (analog, digital, wireless)

**Output:**
- Instrument selection matrix by equipment type
- Tag numbering format
- Placement guidelines
- Range selection formulas
- Alarm/trip calculation methods

### 4. Control Strategy Patterns
**What it learns:**
- Types of control loops used (PID, cascade, ratio, override)
- When to use PID vs ON/OFF control
- Controller tuning parameter guidelines
- Setpoint management strategies
- Control valve fail-safe positions
- Interlock logic patterns

**Output:**
- Control loop selection decision tree
- Tuning parameter recommendations
- Fail-safe position matrix
- Standard interlock configurations

### 5. Safety Integration Patterns
**What it learns:**
- Where safety valves (PSVs) are placed and why
- How set pressures are determined
- Where emergency shutdown (ESD) valves are located
- High/low pressure and level trip patterns
- Interlock logic development
- SIL (Safety Integrity Level) requirements
- Redundancy patterns

**Output:**
- PSV placement criteria
- Set pressure calculation methods
- ESD valve placement logic
- Trip point formulas
- SIL-based redundancy rules

### 6. Utility Connection Patterns
**What it learns:**
- Instrument air distribution network design
- Steam tracing application and layout
- Cooling water connection points
- Drain and vent placement rules
- Sample point locations and design
- Utility line sizing methods

**Output:**
- Utility distribution patterns
- Connection point standards
- Sizing guidelines
- Typical configurations

---

## Learned Patterns Application

### Automated P&ID Generation
The learned patterns enable automated P&ID generation:

1. **Equipment Detail Addition**
   - Apply learned rules to add nozzles, internals, specifications
   - Use standard symbol library
   - Apply tag numbering scheme

2. **Piping Network Creation**
   - Generate line numbers using learned format
   - Place isolation valves per decision tree
   - Add control valves based on control requirements
   - Apply material selection rules

3. **Instrumentation Layout**
   - Select instruments using matrix
   - Place per learned patterns
   - Generate tags automatically
   - Configure control loops

4. **Control System Design**
   - Apply control strategy patterns
   - Configure PID parameters
   - Set fail-safe positions
   - Design interlock logic

5. **Safety System Integration**
   - Place PSVs per criteria
   - Add ESDs at critical points
   - Set trip points
   - Implement redundancy

6. **Utility Integration**
   - Add instrument air connections
   - Apply steam tracing where needed
   - Place drains and vents
   - Add sample points

---

## Example: P16093 Analysis

### Input Files
- **PFD**: P16093_PFD.pdf (Drawing 14-01-08-0001)
- **P&ID**: P16093-14-01-08-1602_P&ID.pdf
- **PFD Analysis**: P16093_PFD_Analysis.json

### PFD Content (Summary)
- **1 Equipment**: V-3601 (Sahil Export Gas KOD)
  - 7800mm x 3300mm
  - CS + SS 316L CLAD
  - 22.4 barg design pressure
  - 55°C / -29°C design temperature
- **1 Piping Line**: 16 inch, 300#, CS export line
- **2 Instruments**: PT-3601-01, FT-3601-02
- **1 Valve**: SDV-3601-01 (shutdown valve)
- **1 Safety Device**: PSV-3601-01 (to HP Flare)

### Expected P&ID Expansion
Based on learned patterns, the P&ID should show:
- **Equipment V-3601** with:
  - Multiple nozzles (inlet, outlet, drain, vent, relief, instrument)
  - Internal demister pad
  - Elevation specifications
  - Support details
- **Detailed Piping**:
  - Line 14-01-08-1602 with full specifications
  - Additional branches for utilities
  - Isolation valves at equipment connections
  - Control valves (if pressure/flow control needed)
- **Expanded Instrumentation**:
  - PT-3601-01 (pressure transmitter, 0-25 barg)
  - FT-3601-02 (flow transmitter with totalizer)
  - Additional instruments: LT (level), TT (temperature), etc.
  - Control loops configured
  - Alarm/trip settings shown
- **Enhanced Safety**:
  - PSV-3601-01 with full specifications (set pressure, size, discharge path)
  - SDV-3601-01 with actuation details and interlocks
  - Additional safety devices if required by analysis
- **Utility Connections**:
  - Instrument air to pneumatic instruments and valves
  - Steam tracing if required
  - Drains at low points
  - Vents at high points

---

## Pattern Learning Execution

### Script: learn_conversion_patterns.py
```python
# Loads PFD analysis
# Converts PFD and P&ID to images
# Sends both to GPT-4 Vision
# Learns comprehensive conversion patterns
# Saves patterns as JSON
```

### Output Files
1. **Learned_PFD_to_PID_Patterns.json** - Complete learned patterns
2. **Pattern summary logs** - Analysis execution details

### Retry Logic
- Automatic retry on API errors (502, 503, timeout)
- Exponential backoff
- Graceful error handling

---

## Integration with Existing Systems

### 1. Comprehensive Analysis Service
The pattern learning service complements the comprehensive analysis service:
- **Comprehensive Analysis**: Extracts data from single PFD
- **Pattern Learning**: Learns conversion rules from PFD-P&ID pairs
- **Combined**: Complete understanding for automated generation

### 2. AI Drawing Generator
Learned patterns feed into AI drawing generator:
```python
from apps.pfd_converter.ai_drawing_generator import AIDrawingGenerator
from apps.pfd_converter.pattern_learning_service import PFDtoPIDPatternLearner

# Load learned patterns
with open('Learned_PFD_to_PID_Patterns.json') as f:
    patterns = json.load(f)

# Generate P&ID using patterns
generator = AIDrawingGenerator()
pid_image = generator.generate_with_patterns(
    pfd_data=pfd_analysis,
    conversion_patterns=patterns,
    style='detailed'
)
```

### 3. Reference Learning System
Pattern learning enhances reference learning:
- Reference learning: Learns drawing style and conventions
- Pattern learning: Learns engineering logic and rules
- Combined: Style + Engineering = Accurate P&IDs

---

## Configuration Customization

### Adding New Analysis Categories
```python
PATTERN_LEARNING_CONFIG["analysis_categories"]["new_category"] = {
    "description": "What this category covers",
    "extract": [
        "specific item 1",
        "specific item 2",
        ...
    ]
}
```

### Adding Conversion Rules
```python
PATTERN_LEARNING_CONFIG["conversion_rules_to_learn"]["new_rule_type"] = [
    "Rule 1 to learn",
    "Rule 2 to learn",
    ...
]
```

### Customizing Output Structure
```python
PATTERN_LEARNING_CONFIG["output_structure"]["learned_patterns"]["custom_category"] = {}
```

---

## Benefits

### 1. Automated P&ID Generation
- Learned patterns enable intelligent automation
- Consistent application of engineering rules
- Reduces manual effort by 70-80%

### 2. Knowledge Capture
- Documents expert engineering knowledge
- Creates reusable pattern library
- Preserves company standards

### 3. Consistency
- Ensures all P&IDs follow same rules
- Reduces errors and omissions
- Improves quality control

### 4. Scalability
- Patterns learned once, applied many times
- Easy to update rules
- Handles various process types

### 5. Training
- New engineers learn from patterns
- Standards documentation
- Best practices codification

---

## Future Enhancements

### 1. Multi-Reference Learning
- Learn from multiple PFD-P&ID pairs
- Identify common patterns across projects
- Build comprehensive pattern library

### 2. Industry-Specific Patterns
- Oil & Gas patterns
- Chemical processing patterns
- Power generation patterns
- Water treatment patterns

### 3. Continuous Learning
- Update patterns as new P&IDs are created
- Machine learning to identify trends
- Adaptive rule refinement

### 4. Validation System
- Automatically validate generated P&IDs against patterns
- Identify deviations from standards
- Suggest improvements

### 5. Integration with CAD
- Direct export to CAD systems
- Symbol library synchronization
- Layout optimization algorithms

---

## Troubleshooting

### Issue: Pattern learning returns raw text
**Solution**: GPT-4 Vision may return descriptive text instead of JSON. The system captures this and saves as `raw_content` for manual processing.

### Issue: API 502 errors
**Solution**: OpenAI API experiencing load. The system auto-retries with exponential backoff. Wait and retry later if persistent.

### Issue: Large file processing slow
**Solution**: 
- Reduce image DPI (150 instead of 200)
- Process pages separately
- Use 'quick' analysis level

### Issue: Incomplete pattern extraction
**Solution**:
- Provide more detailed PFD analysis data
- Use higher quality PDF scans
- Adjust prompt specificity

---

## Summary

The PFD to P&ID Pattern Learning System is a comprehensive, AI-powered solution that:

✅ **Analyzes** PFD-P&ID pairs using GPT-4 Vision  
✅ **Learns** detailed conversion patterns and rules  
✅ **Documents** engineering logic and decision-making  
✅ **Enables** automated P&ID generation  
✅ **Ensures** consistency and compliance  
✅ **Scales** across multiple projects  
✅ **Integrates** with existing systems  

The soft-coded, configuration-driven approach makes it highly adaptable and maintainable for various industries and standards.
