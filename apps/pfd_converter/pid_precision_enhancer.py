"""
PID Precision Enhancement Service
==================================
Post-processing GPT-4o pass that validates and enriches extracted PFD data
for more accurate P&ID generation.

This service is PURELY ADDITIVE:
  - It does NOT modify the 6-step AdvancedPFDToPIDPipeline.
  - It adds a 'precision_enhanced' key to extracted_data.
  - It fails silently — the pipeline continues even if enhancement fails.

Feature control (environment variables with soft-coded defaults):
  ENABLE_PID_PRECISION_ENHANCER      (default: True)
  PID_PRECISION_MODEL                (default: gpt-4o)
  PID_PRECISION_TEMPERATURE          (default: 0.05)  ← low for determinism
  PID_PRECISION_MAX_TOKENS           (default: 6000)
  PID_MIN_INSTRUMENTS_PER_EQUIPMENT  (default: 2)
  PID_REQUIRE_PSV_FOR_VESSELS        (default: True)
  PID_REQUIRE_LEVEL_INDICATOR        (default: True)
  PID_REQUIRE_FLOW_INSTRUMENTS       (default: True)
  PID_MIN_SAFETY_DEVICES_PER_VESSEL  (default: 1)
  PID_APPLICABLE_STANDARDS           (default: ISA 5.1, ADNOC DEP, API 520, API 521)
"""

import json
import logging
from decouple import config

logger = logging.getLogger(__name__)

# ── Soft-coded feature flags and thresholds ───────────────────────────────────
ENABLE_PID_PRECISION_ENHANCER = config(
    'ENABLE_PID_PRECISION_ENHANCER', default=True, cast=bool
)
PRECISION_MODEL = config('PID_PRECISION_MODEL', default='gpt-4o')
PRECISION_TEMPERATURE = float(config('PID_PRECISION_TEMPERATURE', default='0.05'))
PRECISION_MAX_TOKENS = int(config('PID_PRECISION_MAX_TOKENS', default='6000'))

MIN_INSTRUMENTS_PER_EQUIPMENT = int(
    config('PID_MIN_INSTRUMENTS_PER_EQUIPMENT', default='2')
)
REQUIRE_PSV_FOR_VESSELS = config(
    'PID_REQUIRE_PSV_FOR_VESSELS', default=True, cast=bool
)
REQUIRE_LEVEL_INDICATOR = config(
    'PID_REQUIRE_LEVEL_INDICATOR', default=True, cast=bool
)
REQUIRE_FLOW_INSTRUMENTS = config(
    'PID_REQUIRE_FLOW_INSTRUMENTS', default=True, cast=bool
)
MIN_SAFETY_DEVICES_PER_VESSEL = int(
    config('PID_MIN_SAFETY_DEVICES_PER_VESSEL', default='1')
)
APPLICABLE_STANDARDS = config(
    'PID_APPLICABLE_STANDARDS', default='ISA 5.1, ADNOC DEP, API 520, API 521'
)

# Max items sent to AI to avoid token overflow
_MAX_EQUIPMENT_IN_PROMPT = int(config('PID_PRECISION_MAX_EQUIPMENT', default='25'))
_MAX_INSTRUMENTS_IN_PROMPT = int(config('PID_PRECISION_MAX_INSTRUMENTS', default='30'))
_MAX_PIPELINES_IN_PROMPT = int(config('PID_PRECISION_MAX_PIPELINES', default='20'))
# ─────────────────────────────────────────────────────────────────────────────


class PIDPrecisionEnhancer:
    """
    Post-processing AI service for P&ID completeness and accuracy improvement.

    Usage:
        enhancer = PIDPrecisionEnhancer()
        extracted_data = enhancer.enhance(extracted_data, engineering_context)

    The returned dict is identical to the input with one new key added:
        extracted_data['precision_enhanced'] = { ... }
    """

    def __init__(self):
        self._client = None

    @property
    def client(self):
        """Lazy-init OpenAI client."""
        if self._client is None:
            from openai import OpenAI
            self._client = OpenAI(api_key=config('OPENAI_API_KEY'))
        return self._client

    # ── Public API ────────────────────────────────────────────────────────────

    def enhance(self, extracted_data: dict, engineering_context: dict) -> dict:
        """
        Enrich extracted PFD data with a GPT-4o engineering validation pass.

        Args:
            extracted_data:      dict returned by Step 1 (GPT-4o Vision)
            engineering_context: dict with optional keys:
                                   fluid_service, operating_pressure,
                                   operating_temperature, applicable_standards,
                                   design_basis

        Returns:
            extracted_data with 'precision_enhanced' key populated.
        """
        if not ENABLE_PID_PRECISION_ENHANCER:
            logger.info(
                "[PrecisionEnhancer] Disabled via ENABLE_PID_PRECISION_ENHANCER=False"
            )
            return extracted_data

        try:
            logger.info(
                "[PrecisionEnhancer] Starting GPT-4o engineering validation pass …"
            )
            precision_result = self._run_precision_pass(
                extracted_data, engineering_context
            )
            extracted_data['precision_enhanced'] = precision_result

            stats = (
                f"{len(precision_result.get('additional_instruments', []))} instruments, "
                f"{len(precision_result.get('safety_additions', []))} safety devices, "
                f"{len(precision_result.get('utility_connections', []))} utility connections"
            )
            logger.info(f"[PrecisionEnhancer] ✅ Complete — added: {stats}")

        except Exception as exc:
            logger.warning(
                f"[PrecisionEnhancer] ⚠️ Enhancement failed (non-critical): {exc}"
            )
            extracted_data['precision_enhanced'] = {
                'status': 'failed',
                'error': str(exc),
            }

        return extracted_data

    # ── Private helpers ───────────────────────────────────────────────────────

    def _run_precision_pass(
        self, extracted_data: dict, engineering_context: dict
    ) -> dict:
        """Build the prompt and call GPT-4o for the precision review."""
        equipment_list = extracted_data.get('equipment', [])
        instruments = (
            extracted_data.get('instruments', [])
            or extracted_data.get('instrument_list', [])
            or extracted_data.get('text_annotations', [])
        )
        pipelines = (
            extracted_data.get('pipelines', [])
            or extracted_data.get('process_streams', [])
            or extracted_data.get('piping_specifications', [])
        )

        # Resolve effective standards
        standards = (
            engineering_context.get('applicable_standards', '').strip()
            or APPLICABLE_STANDARDS
        )

        prompt = self._build_prompt(
            equipment_list[:_MAX_EQUIPMENT_IN_PROMPT],
            instruments[:_MAX_INSTRUMENTS_IN_PROMPT],
            pipelines[:_MAX_PIPELINES_IN_PROMPT],
            engineering_context,
            standards,
        )

        response = self.client.chat.completions.create(
            model=PRECISION_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are an expert P&ID instrumentation and process safety engineer "
                        "specialising in Oil & Gas projects. Return structured JSON only."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            temperature=PRECISION_TEMPERATURE,
            max_tokens=PRECISION_MAX_TOKENS,
            response_format={"type": "json_object"},
        )

        result_text = response.choices[0].message.content
        precision_data = json.loads(result_text)
        precision_data['status'] = 'success'
        precision_data['model_used'] = PRECISION_MODEL
        return precision_data

    def _build_prompt(
        self,
        equipment_list: list,
        instruments: list,
        pipelines: list,
        engineering_context: dict,
        standards: str,
    ) -> str:
        """Build the engineering validation prompt."""
        fluid_service = engineering_context.get('fluid_service', 'Hydrocarbon Gas / Liquid')
        operating_pressure = engineering_context.get('operating_pressure', 'Not specified')
        operating_temperature = engineering_context.get('operating_temperature', 'Not specified')
        design_basis = engineering_context.get('design_basis', 'Not specified')

        # Validation rules communicated to the model
        validation_rules = []
        if REQUIRE_PSV_FOR_VESSELS:
            validation_rules.append(
                "Every pressure vessel MUST have at least one PSV (per API 520/521)."
            )
        if REQUIRE_LEVEL_INDICATOR:
            validation_rules.append(
                "Every vessel/separator/drum with liquid hold-up MUST have a level transmitter "
                "and level indicator (ISA 5.1)."
            )
        if REQUIRE_FLOW_INSTRUMENTS:
            validation_rules.append(
                "Every major process line MUST have a flow element (FT or FI) on the inlet "
                f"or outlet (minimum {MIN_INSTRUMENTS_PER_EQUIPMENT} instruments per equipment item)."
            )
        rules_text = "\n".join(f"  - {r}" for r in validation_rules)

        return f"""You are a SENIOR INSTRUMENTATION & PROCESS SAFETY ENGINEER reviewing a P&ID for \
an Oil & Gas facility. Your role is to identify gaps and produce structured recommendations.

═══════════════════════════════════════════════════════════════════
ENGINEERING CONTEXT
═══════════════════════════════════════════════════════════════════
Fluid Service          : {fluid_service}
Operating Pressure     : {operating_pressure} barg
Operating Temperature  : {operating_temperature} °C
Applicable Standards   : {standards}
Design Basis           : {design_basis}

═══════════════════════════════════════════════════════════════════
MANDATORY VALIDATION RULES
═══════════════════════════════════════════════════════════════════
{rules_text}

═══════════════════════════════════════════════════════════════════
EXTRACTED PFD DATA (from Vision AI)
═══════════════════════════════════════════════════════════════════
Equipment ({len(equipment_list)} items):
{json.dumps(equipment_list, indent=2)}

Existing Instruments ({len(instruments)} items):
{json.dumps(instruments, indent=2)}

Process Streams / Pipelines ({len(pipelines)} items):
{json.dumps(pipelines, indent=2)}

═══════════════════════════════════════════════════════════════════
TASK
═══════════════════════════════════════════════════════════════════
Perform a comprehensive P&ID completeness review per {standards}.
For EACH equipment item, identify ALL missing P&ID instruments, valves, \
and safety devices using proper ISA 5.1 tag numbering.

Return ONLY a valid JSON object with these EXACT keys — no markdown, no prose:

{{
  "instrument_loop_gaps": [
    {{
      "equipment_tag": "<e.g. V-101>",
      "equipment_type": "<separator|vessel|pump|heat_exchanger|...>",
      "missing_loops": [
        {{
          "loop_type": "<pressure|level|flow|temperature|...>",
          "required_tags": ["<PT-101>", "<PIC-101>", "<PCV-101>"],
          "justification": "<ISA 5.1 / DEP ref + reason>"
        }}
      ]
    }}
  ],
  "safety_additions": [
    {{
      "equipment_tag": "<V-101>",
      "device_type": "<PSV|ESDV|BDV|SDV|...>",
      "tag": "<PSV-101>",
      "set_pressure_barg": 0.0,
      "fail_position": "<Fail-Closed|Fail-Open|Fail-Last>",
      "justification": "<API 520 ref or DEP ref>",
      "discharge_to": "<Flare header|Atmosphere|Safe location>"
    }}
  ],
  "utility_connections": [
    {{
      "equipment_or_instrument_tag": "<LCV-101A>",
      "utility_type": "<Instrument Air|Nitrogen|Cooling Water|Steam|Electrical>",
      "connection_point": "<actuator|seal|cooling jacket|...>",
      "spec": "<e.g. IA 6 bar / N2 7 bar>"
    }}
  ],
  "stream_tag_recommendations": [
    {{
      "stream_description": "<Feed gas inlet>",
      "recommended_tag": "<4\\"-GA-101-B1A>",
      "line_spec": "<B1A>",
      "fluid_code": "<GA|LQ|LS|...>",
      "justification": "<ISA / ADNOC line designation rationale>"
    }}
  ],
  "esd_valve_recommendations": [
    {{
      "location": "<Inlet to V-101>",
      "tag": "<ESDV-101>",
      "type": "<Full Bore Ball Valve>",
      "size_inch": 4,
      "fail_position": "Fail-Closed",
      "justification": "<ESD philosophy reference>"
    }}
  ],
  "additional_instruments": [
    {{
      "tag": "<PT-101>",
      "type": "<Pressure Transmitter>",
      "equipment_tag": "<V-101>",
      "service": "<Separator operating pressure>",
      "range": "<0-15 barg>",
      "loop_function": "<indication|control|safety>"
    }}
  ],
  "compliance_check": {{
    "isa51_compliant": true,
    "adnoc_dep_compliant": true,
    "api_520_compliant": true,
    "missing_items_count": 0,
    "critical_gaps": [],
    "completeness_score": 95,
    "notes": "<brief engineering summary>"
  }}
}}"""
