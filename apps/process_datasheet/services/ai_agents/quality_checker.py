"""
Quality Checker Agent
Final quality assurance and completeness verification
"""
from typing import Dict, Any
from .base_agent import BaseAgent


class QualityCheckerAgent(BaseAgent):
    """
    Agent specialized in quality assurance and final verification
    Ensures datasheet meets all requirements before approval
    """
    
    AGENT_CONFIG = {
        'name': 'Quality Checker',
        'description': 'Final quality assurance and completeness verification',
        'model': 'gpt-4o',
        'temperature': 0.1,
        'max_tokens': 2500,
        'system_prompt': """You are a quality assurance specialist for engineering datasheets.

Your role:
1. Final completeness verification
2. Cross-field consistency checks
3. Documentation quality assessment
4. Readiness for approval determination
5. Identification of missing information

Quality criteria:
- All required fields populated
- Data internally consistent
- Calculations verified
- Standards properly referenced
- Documentation clear and professional
- Ready for client delivery

You have high standards and ensure nothing is overlooked.""",
        'capabilities': [
            'completeness_verification',
            'consistency_checking',
            'quality_scoring',
            'readiness_assessment',
            'gap_identification'
        ],
        'quality_gates': {
            'completeness': 0.95,
            'consistency': 0.90,
            'accuracy': 0.95,
            'documentation': 0.85
        }
    }
    
    def execute(self, task: Dict[str, Any]) -> Dict[str, Any]:
        """
        Execute quality check task
        
        Args:
            task: {
                'type': 'check_quality',
                'data': {
                    'datasheet_data': Dict,
                    'validation_results': Dict,
                    'calculated_values': Dict,
                    'equipment_config': Dict
                }
            }
            
        Returns:
            Quality assessment with gate pass/fail status
        """
        task_type = task.get('type')
        
        if task_type == 'check_quality':
            return self._check_quality(task['data'])
        elif task_type == 'verify_completeness':
            return self._verify_completeness(task['data'])
        elif task_type == 'assess_readiness':
            return self._assess_readiness(task['data'])
        elif task_type == 'generate_checklist':
            return self._generate_checklist(task['data'])
        else:
            return {
                'success': False,
                'error': f'Unknown task type: {task_type}'
            }
    
    def _check_quality(self, data: Dict) -> Dict[str, Any]:
        """Comprehensive quality check"""
        try:
            datasheet_data = data.get('datasheet_data', {})
            validation_results = data.get('validation_results', {})
            calculated_values = data.get('calculated_values', {})
            equipment_config = data.get('equipment_config', {})
            
            # Count fields
            total_fields = self._count_total_fields(equipment_config)
            filled_fields = self._count_filled_fields(datasheet_data, equipment_config)
            
            prompt = f"""Perform final quality assurance check:

Datasheet Data:
{self._format_context(datasheet_data)}

Previous Validation:
- Score: {validation_results.get('score', 'N/A')}
- Issues: {len(validation_results.get('errors', []))} errors, {len(validation_results.get('warnings', []))} warnings

Calculations:
- {len(calculated_values)} formulas executed
- {sum(1 for v in calculated_values.values() if v.get('success'))} successful

Completeness:
- {filled_fields}/{total_fields} fields completed ({filled_fields/total_fields*100 if total_fields > 0 else 0:.1f}%)

Quality Assessment:
1. Completeness (all required fields?)
2. Consistency (cross-field validation?)
3. Accuracy (calculations correct?)
4. Documentation (clear and professional?)
5. Standards (properly referenced?)

Determine:
- Overall quality score (0-100)
- Pass/Fail for each quality gate
- Items requiring attention
- Readiness for approval (Yes/No/Conditional)"""
            
            schema = {
                'quality_assessment': {
                    'overall_score': 'float',
                    'gates': {
                        'completeness': {
                            'score': 'float',
                            'passed': 'bool',
                            'issues': ['str']
                        },
                        'consistency': {
                            'score': 'float',
                            'passed': 'bool',
                            'issues': ['str']
                        },
                        'accuracy': {
                            'score': 'float',
                            'passed': 'bool',
                            'issues': ['str']
                        },
                        'documentation': {
                            'score': 'float',
                            'passed': 'bool',
                            'issues': ['str']
                        }
                    },
                    'missing_items': ['str'],
                    'action_items': ['str'],
                    'readiness': 'str (Approved/Conditional/Rejected)',
                    'approval_recommendation': 'str'
                }
            }
            
            result = self.call_llm_structured(prompt, schema)
            
            # Check quality gates
            gates_passed = all(
                result.get('quality_assessment', {}).get('gates', {})
                .get(gate, {}).get('passed', False)
                for gate in self.config['quality_gates'].keys()
            )
            
            self.log_execution(
                {'type': 'quality_check'},
                {'success': True, 'gates_passed': gates_passed}
            )
            
            return {
                'success': True,
                'data': {
                    'quality_assessment': result.get('quality_assessment', {}),
                    'gates_passed': gates_passed,
                    'overall_score': result.get('quality_assessment', {}).get('overall_score', 0)
                }
            }
            
        except Exception as e:
            return {
                'success': False,
                'error': str(e)
            }
    
    def _verify_completeness(self, data: Dict) -> Dict[str, Any]:
        """Verify all required fields are complete"""
        try:
            datasheet_data = data.get('datasheet_data', {})
            equipment_config = data.get('equipment_config', {})
            
            # Build list of required fields
            required_fields = []
            sections = equipment_config.get('sections', [])
            
            for section in sections:
                for field in section.get('fields', []):
                    if field.get('required', False):
                        required_fields.append({
                            'id': field['id'],
                            'label': field['label'],
                            'section': section['name']
                        })
            
            # Check which are missing
            missing_fields = []
            for field in required_fields:
                if not self._field_has_value(datasheet_data, field['id']):
                    missing_fields.append(field)
            
            completeness = {
                'total_required': len(required_fields),
                'completed': len(required_fields) - len(missing_fields),
                'missing': missing_fields,
                'percentage': ((len(required_fields) - len(missing_fields)) / len(required_fields) * 100) if required_fields else 100
            }
            
            return {
                'success': True,
                'data': {'completeness': completeness}
            }
            
        except Exception as e:
            return {
                'success': False,
                'error': str(e)
            }
    
    def _assess_readiness(self, data: Dict) -> Dict[str, Any]:
        """Assess readiness for approval"""
        try:
            quality_assessment = data.get('quality_assessment', {})
            validation_results = data.get('validation_results', {})
            
            # Readiness criteria
            criteria = {
                'completeness': quality_assessment.get('gates', {}).get('completeness', {}).get('passed', False),
                'no_critical_errors': len([e for e in validation_results.get('errors', []) if e.get('severity') == 'critical']) == 0,
                'quality_score': quality_assessment.get('overall_score', 0) >= 85,
                'all_gates_passed': all(
                    gate.get('passed', False)
                    for gate in quality_assessment.get('gates', {}).values()
                )
            }
            
            ready = all(criteria.values())
            
            if ready:
                recommendation = "Approved - Ready for technical review"
            elif criteria['completeness'] and criteria['no_critical_errors']:
                recommendation = "Conditional - Minor issues to resolve"
            else:
                recommendation = "Not Ready - Significant issues present"
            
            return {
                'success': True,
                'data': {
                    'readiness': {
                        'ready': ready,
                        'criteria': criteria,
                        'recommendation': recommendation
                    }
                }
            }
            
        except Exception as e:
            return {
                'success': False,
                'error': str(e)
            }
    
    def _generate_checklist(self, data: Dict) -> Dict[str, Any]:
        """Generate action checklist"""
        try:
            quality_assessment = data.get('quality_assessment', {})
            validation_results = data.get('validation_results', {})
            
            checklist = []
            
            # Add items from quality gates
            for gate_name, gate_data in quality_assessment.get('gates', {}).items():
                if not gate_data.get('passed', False):
                    for issue in gate_data.get('issues', []):
                        checklist.append({
                            'category': gate_name,
                            'priority': 'High',
                            'item': issue,
                            'status': 'Open'
                        })
            
            # Add validation errors
            for error in validation_results.get('errors', []):
                checklist.append({
                    'category': 'Validation',
                    'priority': 'Critical' if error.get('severity') == 'critical' else 'High',
                    'item': error.get('message', ''),
                    'status': 'Open'
                })
            
            # Add action items
            for action in quality_assessment.get('action_items', []):
                checklist.append({
                    'category': 'Action',
                    'priority': 'Medium',
                    'item': action,
                    'status': 'Open'
                })
            
            return {
                'success': True,
                'data': {
                    'checklist': checklist,
                    'total_items': len(checklist)
                }
            }
            
        except Exception as e:
            return {
                'success': False,
                'error': str(e)
            }
    
    def _count_total_fields(self, equipment_config: Dict) -> int:
        """Count total fields in configuration"""
        count = 0
        for section in equipment_config.get('sections', []):
            count += len(section.get('fields', []))
        return count
    
    def _count_filled_fields(self, datasheet_data: Dict, equipment_config: Dict) -> int:
        """Count filled fields in datasheet"""
        count = 0
        for section in equipment_config.get('sections', []):
            for field in section.get('fields', []):
                if self._field_has_value(datasheet_data, field['id']):
                    count += 1
        return count
    
    def _field_has_value(self, data: Dict, field_id: str) -> bool:
        """Check if field has a value"""
        for section_data in data.values():
            if isinstance(section_data, dict) and field_id in section_data:
                value = section_data[field_id]
                if isinstance(value, dict):
                    return value.get('value') not in [None, '', 0]
                return value not in [None, '', 0]
        return False
