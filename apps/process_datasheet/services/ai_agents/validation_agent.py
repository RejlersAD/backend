"""
Validation Agent
Validates extracted data using AI-powered reasoning
"""
from typing import Dict, Any
from .base_agent import BaseAgent


class ValidationAgent(BaseAgent):
    """
    Agent specialized in validating datasheet data
    Uses AI reasoning combined with rule-based validation
    """
    
    AGENT_CONFIG = {
        'name': 'Validation Agent',
        'description': 'Validates datasheet data using engineering knowledge and rules',
        'model': 'gpt-4o',
        'temperature': 0.1,
        'max_tokens': 3000,
        'system_prompt': """You are an expert process engineer specializing in datasheet validation.

Your expertise:
1. Engineering logic and calculations
2. Industry standards (ADNOC, API, ASME, ISA)
3. Process safety considerations
4. Equipment specification best practices
5. Data consistency checks

Validation approach:
- Apply engineering judgment
- Check against industry standards
- Identify potential safety issues
- Flag inconsistencies and anomalies
- Suggest corrections where appropriate

You are thorough, conservative, and focused on safety and accuracy.""",
        'capabilities': [
            'engineering_validation',
            'standards_compliance',
            'safety_checks',
            'consistency_analysis',
            'intelligent_reasoning'
        ],
        'validation_types': [
            'technical',
            'safety',
            'standards',
            'consistency',
            'completeness'
        ]
    }
    
    def execute(self, task: Dict[str, Any]) -> Dict[str, Any]:
        """
        Execute validation task
        
        Args:
            task: {
                'type': 'validate',
                'data': {
                    'datasheet_data': Dict,
                    'equipment_type': str,
                    'validation_type': str,  # or 'all'
                    'rules': List[Dict]  # Optional custom rules
                }
            }
            
        Returns:
            Validation results with issues and recommendations
        """
        task_type = task.get('type')
        
        if task_type == 'validate':
            return self._validate_datasheet(task['data'])
        elif task_type == 'validate_technical':
            return self._validate_technical(task['data'])
        elif task_type == 'validate_safety':
            return self._validate_safety(task['data'])
        elif task_type == 'suggest_corrections':
            return self._suggest_corrections(task['data'])
        else:
            return {
                'success': False,
                'error': f'Unknown task type: {task_type}'
            }
    
    def _validate_datasheet(self, data: Dict) -> Dict[str, Any]:
        """Perform comprehensive datasheet validation"""
        try:
            datasheet_data = data.get('datasheet_data', {})
            equipment_type = data.get('equipment_type', 'unknown')
            validation_type = data.get('validation_type', 'all')
            
            prompt = f"""Validate this {equipment_type} datasheet data:

{self._format_context(datasheet_data)}

Validation Focus: {validation_type}

Check for:
1. Technical Correctness
   - Are values within reasonable ranges?
   - Do calculations make sense?
   - Are units consistent?

2. Engineering Logic
   - Design pressure > Operating pressure?
   - Temperature ranges logical?
   - Flow rates consistent (min < normal < max)?

3. Safety Considerations
   - Adequate safety margins?
   - Pressure vessel code compliance?
   - Materials suitable for service?

4. Standards Compliance
   - Following applicable codes (API, ASME, etc.)?
   - Required information present?

5. Data Consistency
   - Cross-field relationships valid?
   - No contradictions?

Identify issues, assess severity (Critical/High/Medium/Low), and suggest corrections."""
            
            schema = {
                'validation_result': {
                    'overall_status': 'str (Valid/Invalid/Warning)',
                    'confidence': 'float',
                    'issues': [
                        {
                            'category': 'str',
                            'severity': 'str',
                            'field': 'str',
                            'description': 'str',
                            'current_value': 'str',
                            'expected_range': 'str',
                            'recommendation': 'str'
                        }
                    ],
                    'technical_concerns': ['str'],
                    'safety_concerns': ['str'],
                    'standards_violations': ['str'],
                    'positive_findings': ['str']
                },
                'score': 'float',
                'requires_review': 'bool'
            }
            
            result = self.call_llm_structured(prompt, schema)
            
            self.log_execution(
                {'type': 'validate', 'equipment': equipment_type},
                {'success': True, 'score': result.get('score', 0)}
            )
            
            return {
                'success': True,
                'data': {
                    'validation': result,
                    'score': result.get('score', 0),
                    'status': result.get('validation_result', {}).get('overall_status', 'Unknown')
                }
            }
            
        except Exception as e:
            return {
                'success': False,
                'error': str(e)
            }
    
    def _validate_technical(self, data: Dict) -> Dict[str, Any]:
        """Focused technical validation"""
        try:
            datasheet_data = data.get('datasheet_data', {})
            equipment_type = data.get('equipment_type', 'unknown')
            
            prompt = f"""Perform deep technical validation for {equipment_type}:

Data:
{self._format_context(datasheet_data)}

Technical Checks:
1. Pressure/Temperature relationships
2. Flow calculations (if applicable)
3. Material compatibility
4. Sizing appropriateness
5. Operating envelope validity
6. Control valve Cv sizing (if applicable)
7. Cavitation risk assessment
8. Noise level predictions

Flag any technical concerns with engineering rationale."""
            
            schema = {
                'technical_validation': {
                    'calculations_valid': 'bool',
                    'sizing_appropriate': 'bool',
                    'materials_suitable': 'bool',
                    'issues': ['str'],
                    'recommendations': ['str'],
                    'confidence': 'float'
                }
            }
            
            result = self.call_llm_structured(prompt, schema)
            
            return {
                'success': True,
                'data': {'technical_validation': result}
            }
            
        except Exception as e:
            return {
                'success': False,
                'error': str(e)
            }
    
    def _validate_safety(self, data: Dict) -> Dict[str, Any]:
        """Safety-focused validation"""
        try:
            datasheet_data = data.get('datasheet_data', {})
            equipment_type = data.get('equipment_type', 'unknown')
            
            prompt = f"""Assess safety aspects of this {equipment_type} datasheet:

Data:
{self._format_context(datasheet_data)}

Safety Assessment:
1. Pressure safety margins adequate? (typically 10% minimum)
2. Temperature within material limits?
3. Emergency scenarios considered? (fail-safe positions)
4. Overpressure protection?
5. Hazardous service considerations?
6. Flammable/toxic fluid handling?
7. Relief valve requirements?

Identify any safety concerns with severity classification."""
            
            schema = {
                'safety_assessment': {
                    'safety_margins_adequate': 'bool',
                    'fail_safe_design': 'bool',
                    'critical_concerns': ['str'],
                    'warnings': ['str'],
                    'recommendations': ['str'],
                    'risk_level': 'str (Low/Medium/High/Critical)'
                }
            }
            
            result = self.call_llm_structured(prompt, schema)
            
            return {
                'success': True,
                'data': {'safety_assessment': result}
            }
            
        except Exception as e:
            return {
                'success': False,
                'error': str(e)
            }
    
    def _suggest_corrections(self, data: Dict) -> Dict[str, Any]:
        """Suggest corrections for validation issues"""
        try:
            datasheet_data = data.get('datasheet_data', {})
            issues = data.get('issues', [])
            
            prompt = f"""Given these validation issues, suggest specific corrections:

Current Data:
{self._format_context(datasheet_data)}

Issues:
{chr(10).join([f"- {issue}" for issue in issues])}

For each issue:
1. Root cause analysis
2. Specific correction needed
3. Updated value recommendation
4. Engineering justification"""
            
            schema = {
                'corrections': [
                    {
                        'issue': 'str',
                        'field': 'str',
                        'current_value': 'str',
                        'recommended_value': 'str',
                        'justification': 'str',
                        'confidence': 'float'
                    }
                ],
                'requires_recalculation': 'bool',
                'requires_expert_review': 'bool'
            }
            
            result = self.call_llm_structured(prompt, schema)
            
            return {
                'success': True,
                'data': {'corrections': result}
            }
            
        except Exception as e:
            return {
                'success': False,
                'error': str(e)
            }
