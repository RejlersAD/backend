# PFD → P&ID Engineering Validation System

## 🎯 Overview

This system implements **engineering-first, soft-coded validation** for PFD to P&ID conversion, aligned with:
- **ADNOC DEP** (Design & Engineering Practice)
- **ASME B31.3** (Process Piping)
- **ASME B31.8** (Gas Transmission)
- **ISA-5.1** (Instrumentation Standards)
- **API RP 520/521** (Relief Systems)

## 🏗️ Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                     PFD Upload & Analysis                    │
│                   (frontend/src/pages/PFDUpload.jsx)         │
└────────────────────┬────────────────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────────────────┐
│              Engineering Standards Configuration             │
│        (engineering_standards_config.py)                     │
│  - Instrument Mapping (ISA-5.1)                             │
│  - Valve Specifications (ADNOC DEP)                         │
│  - Routing Rules (API RP 521)                               │
│  - Validation Rules (ASME B31.3)                            │
└────────────────────┬────────────────────────────────────────┘
                     │
        ┌────────────┼────────────┐
        │            │            │
        ▼            ▼            ▼
┌──────────────┐ ┌──────────────┐ ┌──────────────┐
│ S3 Reference │ │  Validation  │ │  Correction  │
│    Loader    │ │    Engine    │ │   System     │
│              │ │              │ │              │
│ Load ADNOC   │ │ Apply Rules  │ │ Incremental  │
│ P&ID samples │ │ Flag Issues  │ │ Fixes Only   │
└──────────────┘ └──────────────┘ └──────────────┘
        │            │            │
        └────────────┼────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────────────────┐
│              P&ID Output with Validation Report              │
│  - JSON graph (node-edge relationships)                      │
│  - Engineering holds (manual review required)                │
│  - Auto-corrections (with justification)                     │
│  - Reference traceability                                    │
└─────────────────────────────────────────────────────────────┘
```

## 📚 Core Modules

### 1. Engineering Standards Configuration
**File:** `engineering_standards_config.py`

**Purpose:** Soft-coded engineering rules (NO hardcoded logic)

**Components:**
- `InstrumentMapping`: Maps instrument tags to types (PIT, LCV, PSV, etc.)
- `ValveMapping`: Valve types with fail positions (SDV→FC, PCV→depends on service)
- `RoutingConfiguration`: Safety routing (HP Flare, LP Flare, Closed Drain)
- `ValidationRules`: Engineering checks with standards references

**Example Usage:**
```python
from apps.pfd_converter.engineering_standards_config import get_engineering_config

config = get_engineering_config()

# Get instrument definition
inst_def = config.get_instrument_definition('PIT-3901-01')
# Returns: {'measurement_type': 'Pressure', 'instrument_name': 'Pressure Indicator Transmitter', 
#           'safety_critical': True, 'standard': 'ISA-5.1'}

# Get valve spec with fail position
valve_spec = config.get_valve_specification('SDV', service='fire_protection')
# Returns: {'name': 'Shutdown Valve', 'fail_position': 'FO', 'safety_critical': True}
```

### 2. S3 Reference Loader
**File:** `s3_reference_loader.py`

**Purpose:** Learn from APPROVED P&IDs (NOT invented patterns)

**What It Does:**
1. Connects to S3: `rejlers-engineering-data` (me-central-1)
2. Loads P&IDs from ADNOC projects
3. Extracts patterns:
   - Instrument loops (PIT + PIC + PCV)
   - Valve configurations (SDV placements, fail positions)
   - Safety routing (PSV → Flare with knockout drum)
   - Tag naming conventions

**Example Usage:**
```python
from apps.pfd_converter.s3_reference_loader import S3ReferenceLoader

loader = S3ReferenceLoader()

# Build knowledge base from S3 references
knowledge_base = loader.build_knowledge_base(
    max_projects=5,           # Analyze 5 ADNOC projects
    max_docs_per_project=3    # 3 P&IDs per project
)

# Save for future use
loader.save_knowledge_base(Path('reference_knowledge_base.json'))

# Statistics
print(f"Analyzed {loader.stats['total_documents_analyzed']} P&IDs")
print(f"Projects: {loader.stats['adnoc_projects_covered']}")
print(f"Instrument tags found: {len(loader.stats['instrument_tags_found'])}")
```

### 3. Validation Engine
**File:** `validation_engine.py`

**Purpose:** Validate P&ID against engineering standards

**Validation Rules:**

| Rule ID | Description | Standard | Severity | Action |
|---------|-------------|----------|----------|--------|
| PRESS-001 | PSV required on pressure vessels | ASME B31.3, API RP 520 | CRITICAL | ENGINEERING_HOLD |
| LEVEL-001 | Level instruments required on liquid vessels | ISA-5.1 | HIGH | ADD_FROM_REFERENCE |
| CONTROL-001 | Control loops must be complete (T+C+V) | ISA-5.1 | HIGH | ENGINEERING_HOLD |
| PSV-001 | PSV routing (HP/LP flare) must be correct | API RP 521 | CRITICAL | ENGINEERING_HOLD |
| SDV-001 | SDV fail position must be correct | ADNOC DEP | CRITICAL | ENGINEERING_HOLD |
| TAG-001 | Instrument tags must follow ISA-5.1 | ISA-5.1 | MEDIUM | AUTO_CORRECT |

**Example Usage:**
```python
from apps.pfd_converter.validation_engine import validate_pid

# P&ID data structure
pid_data = {
    'drawing_number': '14-01-08-1603',
    'drawing_title': 'Sahil Export Gas KOD',
    'equipment_list': [
        {'tag': 'V-3601-01', 'type': 'VESSEL', 'design_pressure': 22.4}
    ],
    'instrument_list': [
        {'tag': '14-01-PIT-3601-01', 'type': 'PIT'},
        {'tag': '14-01-PIC-3601-01', 'type': 'PIC'}
    ],
    'safety_devices': [
        {'tag': 'PSV-3601-01', 'type': 'PSV', 'set_pressure': 20, 
         'discharge_to': 'HP_FLARE', 'protected_equipment': ['V-3601-01']}
    ],
    'valve_list': [
        {'tag': 'SDV-3601-01', 'type': 'SDV', 'fail_position': 'FC'}
    ]
}

# Validate
result = validate_pid(pid_data)

# Print summary
print(result.get_summary())
# Output:
# Validation Result: ✅ PASSED
#   Critical: 0
#   High: 0
#   Medium: 0
#   Low: 0
#   Engineering Holds: 0
#   Auto Corrections: 0

# Review findings
for finding in result.findings:
    print(finding)
```

## 🔄 Integration with Existing Pipeline

### Current Flow:
```python
# backend/apps/pfd_converter/services_advanced_pipeline.py

class AdvancedPFDToPIDPipeline:
    def convert(self, pfd_file, project_info, cached_vision_data):
        # Step 1: Vision + OCR
        vision_data = self._step1_computer_vision_ocr(pfd_file)
        
        # Step 2: Build graph
        process_graph = self._step2_build_process_graph(vision_data)
        
        # Step 3: Apply rules
        enriched_graph = self._step3_apply_engineering_rules(process_graph)
        
        # ... existing steps
```

### **NEW: Add Validation Step**
```python
# ENHANCEMENT: Add validation after Step 5

from .validation_engine import validate_pid
from .engineering_standards_config import get_engineering_config

class AdvancedPFDToPIDPipeline:
    def convert(self, pfd_file, project_info, cached_vision_data):
        # ... existing steps 1-5 ...
        
        # Step 5: Generate P&ID specs
        pid_specs = self._step5_generate_pid_draft(classified_data, project_info)
        
        # NEW: Step 5.5: Engineering Validation
        logger.info("\n[STEP 5.5/6] ✅ Engineering Validation")
        logger.info("-" * 60)
        validation_result = validate_pid(pid_specs)
        
        # Handle validation findings
        if validation_result.engineering_holds:
            logger.warning(f"⚠️  {len(validation_result.engineering_holds)} items flagged for engineering review")
            for hold in validation_result.engineering_holds:
                logger.warning(f"   - {hold}")
        
        if validation_result.auto_corrections:
            logger.info(f"✅ Applying {len(validation_result.auto_corrections)} auto-corrections")
            pid_specs = self._apply_auto_corrections(pid_specs, validation_result.auto_corrections)
        
        # Add validation report to results
        pid_specs['validation_report'] = {
            'passed': validation_result.validation_passed,
            'findings': [f.__dict__ for f in validation_result.findings],
            'engineering_holds': [f.__dict__ for f in validation_result.engineering_holds],
            'summary': validation_result.get_summary()
        }
        
        # Step 6: Visual rendering (existing)
        drawing_path = self._step6_create_pid_drawing(pid_specs, classified_data)
        
        return results
```

## 🎯 Key Features

### 1. **SOFT-CODED Configuration**
✅ All rules in JSON/config (NOT hardcoded)  
✅ Easy to update without code changes  
✅ Project-specific overrides possible  

### 2. **Reference-Driven**
✅ Learns from ADNOC approved P&IDs in S3  
✅ Pattern matching against real drawings  
✅ Traceability to source documents  

### 3. **Engineering Holds**
✅ Ambiguous cases flagged for manual review  
✅ Never auto-assumes missing information  
✅ Each hold has engineering justification  

### 4. **Incremental Corrections**
✅ Small, isolated fixes  
✅ Traceable to standards  
✅ Preserves existing correct elements  

### 5. **Standards Compliance**
✅ ADNOC DEP  
✅ ASME B31.3/B31.8  
✅ ISA-5.1  
✅ API RP 520/521  

## 📊 Testing the System

### Test 1: Build Knowledge Base from S3
```python
# test/test_s3_reference_loader.py
from apps.pfd_converter.s3_reference_loader import S3ReferenceLoader

def test_build_knowledge_base():
    loader = S3ReferenceLoader()
    kb = loader.build_knowledge_base(max_projects=2, max_docs_per_project=2)
    
    assert kb['statistics']['total_documents'] > 0
    assert len(kb['statistics']['projects_covered']) > 0
    assert kb['statistics']['unique_instruments'] > 0
    
    print("✅ Knowledge base built successfully")
    print(f"   Documents: {kb['statistics']['total_documents']}")
    print(f"   Projects: {kb['statistics']['projects_covered']}")
```

### Test 2: Validate Sample P&ID
```python
# test/test_validation_engine.py
from apps.pfd_converter.validation_engine import validate_pid

def test_validation():
    # Sample P&ID with issues
    pid_data = {
        'drawing_number': 'TEST-001',
        'equipment_list': [
            {'tag': 'V-001', 'type': 'VESSEL', 'design_pressure': 25}  # Missing PSV!
        ],
        'safety_devices': []
    }
    
    result = validate_pid(pid_data)
    
    assert not result.validation_passed, "Should fail - missing PSV"
    assert result.critical_count > 0
    assert any(f.rule_id == 'PRESS-001' for f in result.findings)
    
    print("✅ Validation correctly identified missing PSV")
```

### Test 3: Configuration Export
```python
# test/test_engineering_config.py
from apps.pfd_converter.engineering_standards_config import get_engineering_config

def test_config_export():
    config = get_engineering_config()
    config.export_config(Path('engineering_config_export.json'))
    
    # Review exported config for accuracy
    with open('engineering_config_export.json', 'r') as f:
        exported = json.load(f)
    
    assert 'instrument_mapping' in exported
    assert 'valve_mapping' in exported
    assert 'validation_rules' in exported
    
    print("✅ Configuration exported for review")
```

## 🚀 Next Steps

### Phase 1: Integration (Current)
- [x] Engineering standards configuration module
- [x] S3 reference loader
- [x] Validation engine with rules
- [ ] Frontend validation report display
- [ ] API endpoint for validation

### Phase 2: Enhancement
- [ ] Machine learning pattern extraction from references
- [ ] Automatic correction suggestions (with approval workflow)
- [ ] Custom project-specific rule overrides
- [ ] Integration with existing prompts

### Phase 3: Advanced Features
- [ ] CAD drawing generation from validated specs
- [ ] Interactive engineering review interface
- [ ] Version control for P&ID iterations
- [ ] Compliance report generation

## 📝 Usage in Production

### 1. Initialize Configuration
```python
# backend/apps/pfd_converter/apps.py
from django.apps import AppConfig

class PfdConverterConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.pfd_converter'
    
    def ready(self):
        # Load engineering configuration on startup
        from .engineering_standards_config import get_engineering_config
        from .s3_reference_loader import load_reference_knowledge
        
        get_engineering_config()  # Initialize config
        load_reference_knowledge()  # Load reference KB (cached)
```

### 2. Add Validation to API
```python
# backend/apps/pfd_converter/views_enhanced.py
from rest_framework.decorators import api_view
from .validation_engine import validate_pid

@api_view(['POST'])
def validate_pid_endpoint(request):
    """
    POST /api/v1/pfd/validate/
    Validate P&ID data against engineering standards
    """
    pid_data = request.data
    
    validation_result = validate_pid(pid_data)
    
    return Response({
        'passed': validation_result.validation_passed,
        'summary': validation_result.get_summary(),
        'findings': [
            {
                'rule_id': f.rule_id,
                'severity': f.severity.value,
                'description': f.description,
                'element_id': f.element_id,
                'recommended_action': f.recommended_action.value,
                'engineering_justification': f.engineering_justification,
                'reference_standard': f.reference_standard
            }
            for f in validation_result.findings
        ],
        'engineering_holds': len(validation_result.engineering_holds),
        'auto_corrections': len(validation_result.auto_corrections)
    })
```

## 🎓 Engineering Philosophy

### ✅ DO:
- Learn from approved reference P&IDs
- Apply documented standards (ADNOC DEP, ASME, ISA)
- Flag ambiguities for engineering review
- Provide traceable justifications
- Make incremental, explainable corrections

### ❌ DON'T:
- Auto-generate without reference evidence
- Assume missing instruments/valves
- Change tag naming conventions arbitrarily
- Override safety philosophy
- Make wholesale regenerations

### 🎯 Goal:
**Engineer-acceptable semi-automated P&ID**  
**Accuracy > Automation**  
**Engineering Correctness > AI Creativity**

---

**Contact:** Engineering Team  
**Last Updated:** January 8, 2026  
**Version:** 1.0  
**AWS Region:** me-central-1 (Middle East - UAE)
