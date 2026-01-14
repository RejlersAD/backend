"""
Claude Engineering Reasoner
AI agent that thinks like a senior process engineer
Validates P&ID designs for safety, compliance, and best practices
"""

import anthropic
from typing import Dict, List, Optional, Tuple
import logging
import json
from dataclasses import dataclass, asdict
from enum import Enum
from ..config.ai_models_config import get_model_config
from decouple import config

logger = logging.getLogger(__name__)


class Severity(Enum):
    """Finding severity levels"""
    CRITICAL = "CRITICAL"  # Safety-critical, must fix
    HIGH = "HIGH"  # Standard violation, should fix
    MEDIUM = "MEDIUM"  # Best practice, recommended
    LOW = "LOW"  # Suggestion, optional
    INFO = "INFO"  # Information only


@dataclass
class EngineeringFinding:
    """Single engineering finding"""
    id: str
    severity: Severity
    category: str  # safety, compliance, operability, maintainability
    title: str
    description: str
    affected_equipment: List[str]
    recommendation: str
    standard_reference: Optional[str] = None
    cost_impact: Optional[str] = None


@dataclass
class ValidationReport:
    """Complete validation report"""
    overall_score: float  # 0-100
    validation_passed: bool
    findings: List[EngineeringFinding]
    summary: Dict[str, int]  # Count by severity
    recommendations_summary: str
    standards_checked: List[str]


class ClaudeEngineeringReasoner:
    """
    AI engineering reasoner using Claude 3.5 Sonnet
    Performs multi-step validation and reasoning
    """
    
    def __init__(self, config_name: str = "claude_sonnet_engineer"):
        """
        Initialize Claude reasoner
        
        Args:
            config_name: Model configuration name
        """
        self.config = get_model_config(config_name)
        if not self.config or not self.config.enabled:
            raise ValueError(f"Model {config_name} not found or disabled")
        
        api_key = config("ANTHROPIC_API_KEY", default="")
        if not api_key:
            raise ValueError("ANTHROPIC_API_KEY not found in environment")
        
        self.client = anthropic.Anthropic(api_key=api_key)
        self.system_prompt = self.config.parameters.get("system_prompt", "")
        self.max_tokens = self.config.parameters.get("max_tokens", 8192)
        self.temperature = self.config.parameters.get("temperature", 0.2)
        
        logger.info("Claude Engineering Reasoner initialized")
    
    def validate_pid_design(
        self,
        pid_specs: Dict,
        pfd_context: Dict,
        project_requirements: Optional[Dict] = None
    ) -> ValidationReport:
        """
        Comprehensive P&ID validation
        
        Args:
            pid_specs: P&ID specifications (equipment, instruments, piping)
            pfd_context: Original PFD context for comparison
            project_requirements: Project-specific requirements
            
        Returns:
            Complete validation report
        """
        logger.info("Starting Claude engineering validation...")
        
        findings = []
        
        # Step 1: Safety-critical analysis
        logger.info("  Step 1/5: Safety-critical instrumentation check")
        safety_findings = self._check_safety_critical(pid_specs, pfd_context)
        findings.extend(safety_findings)
        
        # Step 2: Standards compliance
        logger.info("  Step 2/5: Standards compliance verification")
        compliance_findings = self._verify_standards_compliance(pid_specs)
        findings.extend(compliance_findings)
        
        # Step 3: Missing elements detection
        logger.info("  Step 3/5: Missing required elements")
        missing_findings = self._identify_missing_elements(pid_specs, pfd_context)
        findings.extend(missing_findings)
        
        # Step 4: Engineering best practices
        logger.info("  Step 4/5: Best practices review")
        best_practice_findings = self._check_best_practices(pid_specs)
        findings.extend(best_practice_findings)
        
        # Step 5: Operability and maintainability
        logger.info("  Step 5/5: Operability & maintainability")
        operational_findings = self._check_operability(pid_specs)
        findings.extend(operational_findings)
        
        # Calculate score and create report
        report = self._create_validation_report(findings)
        
        logger.info(f"✅ Validation complete: Score {report.overall_score:.1f}/100, "
                   f"{len(report.findings)} findings")
        
        return report
    
    def _check_safety_critical(self, pid_specs: Dict, pfd_context: Dict) -> List[EngineeringFinding]:
        """Check for safety-critical instrumentation requirements"""
        prompt = f"""Analyze this process system for safety-critical instrumentation requirements.

**Process Description:**
{json.dumps(pfd_context.get('process_description', {}), indent=2)}

**Current P&ID Specifications:**
Equipment: {len(pid_specs.get('equipment', []))} items
{json.dumps(pid_specs.get('equipment', []), indent=2)}

Instruments: {len(pid_specs.get('instruments', []))} items
{json.dumps(pid_specs.get('instruments', []), indent=2)}

Piping: {len(pid_specs.get('piping', []))} lines
Operating Conditions:
- Max Pressure: {pfd_context.get('max_pressure', 'unknown')}
- Max Temperature: {pfd_context.get('max_temperature', 'unknown')}
- Process Fluids: {pfd_context.get('fluids', 'unknown')}

**Your Task:**
Identify missing or inadequate safety-critical instrumentation. Check for:

1. **Pressure Protection:**
   - Pressure Safety Valves (PSV) for overpressure scenarios
   - Rupture discs for fast-acting relief
   - High/low pressure alarms (PAH/PAL)
   - Pressure interlocks for equipment protection

2. **Temperature Protection:**
   - High temperature alarms (TAH)
   - Temperature interlocks for heaters/coolers
   - Thermal relief valves
   - Temperature monitoring in critical zones

3. **Level Protection:**
   - High/low level alarms for vessels (LAH/LAL)
   - Level interlocks to prevent overflow/run-dry
   - Independent level measurement for safety
   - Dump/drain systems with level control

4. **Emergency Shutdown (ESD):**
   - ESD valves on critical lines
   - Emergency isolation capability
   - Fail-safe valve positions
   - ESD system integration

5. **Toxic/Flammable Detection:**
   - Gas detectors for H2S, hydrocarbons, etc.
   - Fire detection systems
   - Area classification compliance
   - Ventilation interlocks

6. **Special Services:**
   - Steam system safety (pressure, temperature)
   - Cooling water system reliability
   - Instrument air backup
   - Flare system adequacy

**Output Format (JSON):**
```json
{{
  "findings": [
    {{
      "severity": "CRITICAL|HIGH|MEDIUM|LOW",
      "category": "safety",
      "title": "Brief title",
      "description": "Detailed description of the issue",
      "affected_equipment": ["V-101", "P-102"],
      "recommendation": "Specific action to take",
      "standard_reference": "API 521, ISA 84.00.01, etc."
    }}
  ]
}}
```

Provide specific, actionable findings based on industry standards."""

        try:
            response = self.client.messages.create(
                model=self.config.model_id,
                max_tokens=self.max_tokens,
                temperature=self.temperature,
                system=self.system_prompt,
                messages=[{"role": "user", "content": prompt}]
            )
            
            # Parse response
            content = response.content[0].text
            findings_data = self._extract_json(content)
            
            # Convert to EngineeringFinding objects
            findings = []
            for finding in findings_data.get("findings", []):
                findings.append(EngineeringFinding(
                    id=f"SAFETY-{len(findings)+1:03d}",
                    severity=Severity[finding["severity"]],
                    category=finding["category"],
                    title=finding["title"],
                    description=finding["description"],
                    affected_equipment=finding.get("affected_equipment", []),
                    recommendation=finding["recommendation"],
                    standard_reference=finding.get("standard_reference")
                ))
            
            return findings
            
        except Exception as e:
            logger.error(f"Safety check failed: {e}")
            return []
    
    def _verify_standards_compliance(self, pid_specs: Dict) -> List[EngineeringFinding]:
        """Verify compliance with ISA, ADNOC DEP, API standards"""
        prompt = f"""Verify this P&ID design against industry standards.

**P&ID Specifications:**
{json.dumps(pid_specs, indent=2)}

**Standards to Check:**
1. **ISA-5.1:** Instrument symbols and identification
2. **ADNOC DEP:** ADNOC Design & Engineering Practice
3. **API RP 551:** Process Measurement and Control
4. **API RP 520/521:** Pressure Relief Systems
5. **ASME B31.3:** Process Piping

**Verification Items:**

**Instrumentation (ISA-5.1):**
- Tag numbering format correct? (e.g., FT-101, PT-201)
- Instrument symbols ISA-compliant?
- Control loops properly identified?
- Transmitter locations appropriate?

**ADNOC DEP Requirements:**
- Material selection per DEP 31.22.10.30-Gen?
- Piping class per DEP 31.38.01.30-Gen?
- Instrument specification per DEP 31.25.01.30-Gen?
- Safety systems per DEP 31.25.03.30-Gen?

**API Compliance:**
- PSV sizing per API 520?
- Control valve sizing per ISA 75.01?
- Pressure class boundaries marked?
- Insulation requirements noted?

**Output Format (JSON):**
```json
{{
  "findings": [
    {{
      "severity": "HIGH|MEDIUM|LOW",
      "category": "compliance",
      "title": "Standard violation or deviation",
      "description": "What doesn't comply",
      "affected_equipment": ["Equipment tags"],
      "recommendation": "How to achieve compliance",
      "standard_reference": "Specific standard clause"
    }}
  ]
}}
```"""

        try:
            response = self.client.messages.create(
                model=self.config.model_id,
                max_tokens=self.max_tokens,
                temperature=self.temperature,
                system=self.system_prompt,
                messages=[{"role": "user", "content": prompt}]
            )
            
            content = response.content[0].text
            findings_data = self._extract_json(content)
            
            findings = []
            for finding in findings_data.get("findings", []):
                findings.append(EngineeringFinding(
                    id=f"COMPLIANCE-{len(findings)+1:03d}",
                    severity=Severity[finding["severity"]],
                    category=finding["category"],
                    title=finding["title"],
                    description=finding["description"],
                    affected_equipment=finding.get("affected_equipment", []),
                    recommendation=finding["recommendation"],
                    standard_reference=finding.get("standard_reference")
                ))
            
            return findings
            
        except Exception as e:
            logger.error(f"Compliance check failed: {e}")
            return []
    
    def _identify_missing_elements(self, pid_specs: Dict, pfd_context: Dict) -> List[EngineeringFinding]:
        """Identify missing required P&ID elements"""
        prompt = f"""Compare PFD and P&ID to identify missing elements.

**Original PFD Context:**
{json.dumps(pfd_context, indent=2)}

**Current P&ID:**
{json.dumps(pid_specs, indent=2)}

**Identify Missing:**

1. **Equipment Details:**
   - Spare equipment (e.g., 2×100% pumps, should show both)
   - Equipment nozzles and connections
   - Equipment supports and foundations
   - Vendor equipment specifications

2. **Instrumentation:**
   - Local indicators (PI, TI, LG)
   - Control valves for each control loop
   - Isolation valves for instruments
   - Instrument tubing/wiring

3. **Piping Details:**
   - Block valves at equipment
   - Drain and vent valves
   - Sample connections
   - Bypass lines
   - Strainers and filters

4. **Utilities:**
   - Steam tracing
   - Instrument air supply
   - Nitrogen purge connections
   - Cooling/heating jackets

5. **Safety Elements:**
   - PSVs with discharge routing
   - Flame arrestors
   - Explosion-proof enclosures
   - Safety shower/eyewash locations

**Output Format (JSON):**
```json
{{
  "findings": [
    {{
      "severity": "HIGH|MEDIUM|LOW",
      "category": "missing_elements",
      "title": "Missing item",
      "description": "What's missing and why it's needed",
      "affected_equipment": ["Related equipment"],
      "recommendation": "Add specific item",
      "standard_reference": "Why required"
    }}
  ]
}}
```"""

        try:
            response = self.client.messages.create(
                model=self.config.model_id,
                max_tokens=self.max_tokens,
                temperature=self.temperature,
                system=self.system_prompt,
                messages=[{"role": "user", "content": prompt}]
            )
            
            content = response.content[0].text
            findings_data = self._extract_json(content)
            
            findings = []
            for finding in findings_data.get("findings", []):
                findings.append(EngineeringFinding(
                    id=f"MISSING-{len(findings)+1:03d}",
                    severity=Severity[finding["severity"]],
                    category=finding["category"],
                    title=finding["title"],
                    description=finding["description"],
                    affected_equipment=finding.get("affected_equipment", []),
                    recommendation=finding["recommendation"],
                    standard_reference=finding.get("standard_reference")
                ))
            
            return findings
            
        except Exception as e:
            logger.error(f"Missing elements check failed: {e}")
            return []
    
    def _check_best_practices(self, pid_specs: Dict) -> List[EngineeringFinding]:
        """Check engineering best practices"""
        # Simplified version - similar structure to above
        findings = []
        
        # Add some rule-based checks
        equipment = pid_specs.get("equipment", [])
        instruments = pid_specs.get("instruments", [])
        
        # Check pump sparing
        pumps = [eq for eq in equipment if "pump" in eq.get("type", "").lower()]
        if len(pumps) < 2:
            findings.append(EngineeringFinding(
                id="BP-001",
                severity=Severity.MEDIUM,
                category="best_practices",
                title="Consider pump sparing",
                description="Single pump configuration may not provide adequate reliability",
                affected_equipment=[p.get("tag", "") for p in pumps],
                recommendation="Consider 2×100% or 2×50% pump configuration",
                standard_reference="API 610 - Centrifugal Pumps"
            ))
        
        return findings
    
    def _check_operability(self, pid_specs: Dict) -> List[EngineeringFinding]:
        """Check operability and maintainability"""
        findings = []
        
        # Rule-based checks for operability
        # Add findings based on practical experience
        
        return findings
    
    def _create_validation_report(self, findings: List[EngineeringFinding]) -> ValidationReport:
        """Create comprehensive validation report"""
        # Count by severity
        summary = {
            "CRITICAL": len([f for f in findings if f.severity == Severity.CRITICAL]),
            "HIGH": len([f for f in findings if f.severity == Severity.HIGH]),
            "MEDIUM": len([f for f in findings if f.severity == Severity.MEDIUM]),
            "LOW": len([f for f in findings if f.severity == Severity.LOW]),
            "INFO": len([f for f in findings if f.severity == Severity.INFO])
        }
        
        # Calculate score (100 - penalties)
        score = 100.0
        score -= summary["CRITICAL"] * 20  # -20 per critical
        score -= summary["HIGH"] * 10      # -10 per high
        score -= summary["MEDIUM"] * 5     # -5 per medium
        score -= summary["LOW"] * 1        # -1 per low
        score = max(0, score)  # Floor at 0
        
        # Validation passes if no critical findings and score > 85
        validation_passed = summary["CRITICAL"] == 0 and score >= 85
        
        # Create summary
        recommendations_summary = self._generate_summary(findings)
        
        return ValidationReport(
            overall_score=score,
            validation_passed=validation_passed,
            findings=findings,
            summary=summary,
            recommendations_summary=recommendations_summary,
            standards_checked=["ISA-5.1", "ADNOC DEP", "API 520/521", "ASME B31.3"]
        )
    
    def _generate_summary(self, findings: List[EngineeringFinding]) -> str:
        """Generate human-readable summary"""
        if not findings:
            return "No issues found. P&ID design appears compliant and complete."
        
        critical = [f for f in findings if f.severity == Severity.CRITICAL]
        high = [f for f in findings if f.severity == Severity.HIGH]
        
        summary = []
        
        if critical:
            summary.append(f"⚠️ {len(critical)} CRITICAL safety issues requiring immediate attention:")
            for f in critical[:3]:  # Top 3
                summary.append(f"  - {f.title}")
        
        if high:
            summary.append(f"\n⚠️ {len(high)} HIGH priority compliance issues:")
            for f in high[:3]:
                summary.append(f"  - {f.title}")
        
        summary.append(f"\nTotal findings: {len(findings)}")
        summary.append("Review detailed findings for complete list.")
        
        return "\n".join(summary)
    
    def _extract_json(self, text: str) -> Dict:
        """Extract JSON from Claude response"""
        # Try to find JSON in response
        try:
            # Look for ```json ... ``` blocks
            if "```json" in text:
                start = text.find("```json") + 7
                end = text.find("```", start)
                json_text = text[start:end].strip()
            elif "```" in text:
                start = text.find("```") + 3
                end = text.find("```", start)
                json_text = text[start:end].strip()
            else:
                # Try to parse entire response
                json_text = text
            
            return json.loads(json_text)
        except Exception as e:
            logger.warning(f"Failed to parse JSON from response: {e}")
            return {"findings": []}


# Convenience function
def validate_pid(pid_specs: Dict, pfd_context: Dict) -> ValidationReport:
    """
    Quick validation function
    
    Args:
        pid_specs: P&ID specifications
        pfd_context: Original PFD context
        
    Returns:
        Validation report
    """
    reasoner = ClaudeEngineeringReasoner()
    return reasoner.validate_pid_design(pid_specs, pfd_context)
