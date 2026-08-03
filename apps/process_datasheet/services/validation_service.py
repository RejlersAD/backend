"""
Validation Service
Rule-based validation engine for process datasheets
"""
from typing import Dict, Any, List, Tuple
import re
import operator


class ValidationService:
    """
    Validation engine for datasheet data
    Executes validation rules defined in equipment configurations
    """
    
    # Operators for rule evaluation
    OPERATORS = {
        '>=': operator.ge,
        '<=': operator.le,
        '>': operator.gt,
        '<': operator.lt,
        '==': operator.eq,
        '!=': operator.ne,
    }
    
    @staticmethod
    def validate_all(datasheet_data: Dict, equipment_config: Dict,
                    custom_rules: List[Dict] = None) -> Dict[str, Any]:
        """
        Execute all validations for a datasheet
        
        Args:
            datasheet_data: Current datasheet data
            equipment_config: Equipment type configuration with validation rules
            custom_rules: Additional project-specific validation rules
            
        Returns:
            Validation results with score and details
        """
        # Get validation rules from config
        config_rules = equipment_config.get('validationRules', [])
        all_rules = config_rules + (custom_rules or [])
        
        results = {
            'valid': True,
            'score': 100.0,
            'errors': [],
            'warnings': [],
            'info': [],
            'details': []
        }
        
        if not all_rules:
            return results
        
        # Execute each rule
        for rule in all_rules:
            rule_result = ValidationService.execute_rule(rule, datasheet_data)
            
            if not rule_result['passed']:
                severity = rule.get('severity', 'error')
                
                result_entry = {
                    'rule_id': rule.get('id'),
                    'severity': severity,
                    'message': rule.get('message', 'Validation failed'),
                    'check': rule.get('check'),
                    'section': rule.get('section', 'general')
                }
                
                # Categorize by severity
                if severity == 'error' or severity == 'critical':
                    results['errors'].append(result_entry)
                    results['valid'] = False
                elif severity == 'warning':
                    results['warnings'].append(result_entry)
                else:
                    results['info'].append(result_entry)
            
            results['details'].append(rule_result)
        
        # Calculate score
        total_rules = len(all_rules)
        passed_rules = sum(1 for d in results['details'] if d['passed'])
        results['score'] = round((passed_rules / total_rules * 100), 2) if total_rules > 0 else 100.0
        
        return results
    
    @staticmethod
    def execute_rule(rule: Dict, data: Dict) -> Dict[str, Any]:
        """
        Execute a single validation rule
        
        Args:
            rule: Validation rule definition
            data: Datasheet data
            
        Returns:
            Rule execution result
        """
        rule_id = rule.get('id')
        check = rule.get('check')
        
        try:
            passed = ValidationService.evaluate_expression(check, data)
            
            return {
                'rule_id': rule_id,
                'passed': passed,
                'message': rule.get('message') if not passed else None,
                'severity': rule.get('severity', 'error'),
                'error': None
            }
        except Exception as e:
            return {
                'rule_id': rule_id,
                'passed': False,
                'message': f"Rule evaluation error: {str(e)}",
                'severity': 'error',
                'error': str(e)
            }
    
    @staticmethod
    def evaluate_expression(expression: str, data: Dict) -> bool:
        """
        Evaluate a validation expression
        
        Supports expressions like:
        - "pressure_design >= pressure_operating * 1.1"
        - "temperature_operating >= -10 and temperature_operating <= 200"
        - "flow_rate_min <= flow_rate_normal <= flow_rate_max"
        
        Args:
            expression: Validation expression
            data: Datasheet data
            
        Returns:
            True if validation passes, False otherwise
        """
        # Replace field references with actual values
        evaluated_expr = ValidationService._substitute_values(expression, data)
        
        try:
            # Safely evaluate the expression
            result = eval(evaluated_expr, {"__builtins__": {}}, {})
            return bool(result)
        except Exception as e:
            raise ValueError(f"Failed to evaluate expression '{expression}': {str(e)}")
    
    @staticmethod
    def _substitute_values(expression: str, data: Dict) -> str:
        """
        Substitute field names with their values in an expression
        
        Args:
            expression: Expression with field names
            data: Datasheet data
            
        Returns:
            Expression with values substituted
        """
        # Find all field references (alphanumeric + underscore)
        field_pattern = r'\b([a-z_][a-z0-9_]*)\b'
        fields = re.findall(field_pattern, expression)
        
        result_expr = expression
        
        for field in fields:
            # Skip Python keywords and operators
            if field in ['and', 'or', 'not', 'in', 'is', 'True', 'False', 'None']:
                continue
            
            # Get value from data
            value = ValidationService._get_field_value(data, field)
            
            if value is not None:
                # Replace field name with value
                result_expr = re.sub(
                    r'\b' + field + r'\b',
                    str(value),
                    result_expr
                )
            else:
                # Field not found, replace with None
                result_expr = re.sub(
                    r'\b' + field + r'\b',
                    'None',
                    result_expr
                )
        
        return result_expr
    
    @staticmethod
    def _get_field_value(data: Dict, field_name: str) -> Any:
        """
        Get field value from nested datasheet data
        
        Args:
            data: Nested datasheet data
            field_name: Field identifier
            
        Returns:
            Field value or None
        """
        # Search in all sections
        for section_name, section_data in data.items():
            if isinstance(section_data, dict):
                if field_name in section_data:
                    value = section_data[field_name]
                    # Extract numeric value from value/unit structure
                    if isinstance(value, dict) and 'value' in value:
                        try:
                            return float(value['value']) if value['value'] not in [None, ''] else None
                        except (ValueError, TypeError):
                            return None
                    # Return boolean or string values as-is
                    if isinstance(value, (bool, str)):
                        return value
                    # Try to convert to float
                    try:
                        return float(value) if value not in [None, ''] else None
                    except (ValueError, TypeError):
                        return None
        
        # Try direct access
        if field_name in data:
            value = data[field_name]
            if isinstance(value, dict) and 'value' in value:
                try:
                    return float(value['value']) if value['value'] not in [None, ''] else None
                except (ValueError, TypeError):
                    return None
            try:
                return float(value) if value not in [None, ''] else None
            except (ValueError, TypeError):
                return value
        
        return None
    
    @staticmethod
    def validate_field_pattern(value: str, pattern: str) -> bool:
        """
        Validate field value against regex pattern
        
        Args:
            value: Field value
            pattern: Regex pattern
            
        Returns:
            True if pattern matches
        """
        if not value or not pattern:
            return True
        
        try:
            return bool(re.match(pattern, str(value)))
        except re.error:
            return False
    
    @staticmethod
    def validate_required_fields(data: Dict, equipment_config: Dict) -> List[str]:
        """
        Check for missing required fields
        
        Args:
            data: Datasheet data
            equipment_config: Equipment configuration
            
        Returns:
            List of missing required field IDs
        """
        missing_fields = []
        
        sections = equipment_config.get('sections', [])
        
        for section in sections:
            section_id = section.get('id')
            fields = section.get('fields', [])
            
            for field in fields:
                if field.get('required', False):
                    field_id = field.get('id')
                    
                    # Check if field exists and has value
                    value = ValidationService._get_field_value(data, field_id)
                    
                    if value is None or value == '':
                        missing_fields.append({
                            'field_id': field_id,
                            'field_label': field.get('label'),
                            'section': section_id
                        })
        
        return missing_fields
    
    @staticmethod
    def validate_field_patterns(data: Dict, equipment_config: Dict) -> List[Dict]:
        """
        Validate all fields with pattern requirements
        
        Args:
            data: Datasheet data
            equipment_config: Equipment configuration
            
        Returns:
            List of pattern validation failures
        """
        pattern_errors = []
        
        sections = equipment_config.get('sections', [])
        
        for section in sections:
            section_id = section.get('id')
            fields = section.get('fields', [])
            
            for field in fields:
                pattern = field.get('pattern')
                if pattern:
                    field_id = field.get('id')
                    value = ValidationService._get_field_value(data, field_id)
                    
                    if value and not ValidationService.validate_field_pattern(str(value), pattern):
                        pattern_errors.append({
                            'field_id': field_id,
                            'field_label': field.get('label'),
                            'section': section_id,
                            'pattern': pattern,
                            'value': value
                        })
        
        return pattern_errors
    
    @staticmethod
    def validate_completeness(data: Dict, equipment_config: Dict) -> Dict[str, Any]:
        """
        Calculate datasheet completeness percentage
        
        Args:
            data: Datasheet data
            equipment_config: Equipment configuration
            
        Returns:
            Completeness analysis
        """
        sections = equipment_config.get('sections', [])
        
        total_fields = 0
        completed_fields = 0
        section_completeness = {}
        
        for section in sections:
            section_id = section.get('id')
            fields = section.get('fields', [])
            
            section_total = len(fields)
            section_completed = 0
            
            for field in fields:
                total_fields += 1
                field_id = field.get('id')
                value = ValidationService._get_field_value(data, field_id)
                
                if value is not None and value != '':
                    completed_fields += 1
                    section_completed += 1
            
            section_completeness[section_id] = {
                'total': section_total,
                'completed': section_completed,
                'percentage': round((section_completed / section_total * 100), 2) if section_total > 0 else 0
            }
        
        overall_percentage = round((completed_fields / total_fields * 100), 2) if total_fields > 0 else 0
        
        return {
            'overall_percentage': overall_percentage,
            'total_fields': total_fields,
            'completed_fields': completed_fields,
            'sections': section_completeness
        }
    
    @staticmethod
    def validate_consistency(data: Dict, equipment_config: Dict) -> List[Dict]:
        """
        Check for data consistency issues
        
        Common checks:
        - Design values >= Operating values
        - Max values >= Normal values >= Min values
        - Inlet pressure > Outlet pressure
        
        Args:
            data: Datasheet data
            equipment_config: Equipment configuration
            
        Returns:
            List of consistency issues
        """
        issues = []
        
        # Common consistency checks
        consistency_checks = [
            {
                'fields': ['pressure_design', 'pressure_operating'],
                'check': lambda d, o: d >= o,
                'message': 'Design pressure should be greater than or equal to operating pressure'
            },
            {
                'fields': ['temperature_design_max', 'temperature_operating'],
                'check': lambda d, o: d >= o,
                'message': 'Maximum design temperature should be greater than operating temperature'
            },
            {
                'fields': ['temperature_operating', 'temperature_design_min'],
                'check': lambda o, d: o >= d,
                'message': 'Operating temperature should be greater than minimum design temperature'
            },
            {
                'fields': ['flow_rate_max', 'flow_rate_normal'],
                'check': lambda mx, n: mx >= n,
                'message': 'Maximum flow rate should be greater than normal flow rate'
            },
            {
                'fields': ['flow_rate_normal', 'flow_rate_min'],
                'check': lambda n, mn: n >= mn,
                'message': 'Normal flow rate should be greater than minimum flow rate'
            },
            {
                'fields': ['pressure_inlet', 'pressure_outlet'],
                'check': lambda i, o: i > o,
                'message': 'Inlet pressure should be greater than outlet pressure'
            }
        ]
        
        for check_def in consistency_checks:
            field_ids = check_def['fields']
            values = [ValidationService._get_field_value(data, fid) for fid in field_ids]
            
            # Skip if any value is missing
            if all(v is not None for v in values):
                try:
                    if not check_def['check'](*values):
                        issues.append({
                            'fields': field_ids,
                            'message': check_def['message'],
                            'values': dict(zip(field_ids, values))
                        })
                except Exception:
                    pass  # Skip if comparison fails
        
        return issues
