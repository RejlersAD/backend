"""
Rule-based quality checker - Fallback when AI is unavailable
Provides basic quality checking using predefined rules
"""

from .ai_config import (
    RULE_BASED_CHECKS,
    QUALITY_WEIGHTS,
    get_quality_level,
    get_critical_fields_for_equipment,
    validate_voltage_value,
    validate_frequency_value,
    validate_power_factor
)


class RuleBasedQualityChecker:
    """
    Rule-based quality checker for electrical datasheets
    Used as fallback when AI services are unavailable
    """
    
    def __init__(self):
        self.issues = []
        self.warnings = []
        self.passed_checks = []
    
    def check_datasheet(self, datasheet_data, equipment_type=None):
        """
        Perform rule-based quality checks on datasheet
        
        Args:
            datasheet_data: Dictionary of datasheet fields and values
            equipment_type: Equipment type object or code
        
        Returns:
            dict: Quality check results
        """
        self.issues = []
        self.warnings = []
        self.passed_checks = []
        
        # Determine equipment code
        equipment_code = None
        if equipment_type:
            if hasattr(equipment_type, 'code'):
                equipment_code = equipment_type.code
            elif isinstance(equipment_type, str):
                equipment_code = equipment_type
        
        # Extract equipment code from data if not provided
        if not equipment_code and datasheet_data.get('_equipment_code'):
            equipment_code = datasheet_data['_equipment_code']
        
        # Run checks
        critical_score = self._check_critical_fields(datasheet_data, equipment_code)
        validation_score = self._check_field_values(datasheet_data, equipment_code)
        standards_score = self._check_standards_compliance(datasheet_data, equipment_code)
        completeness_score = self._check_data_completeness(datasheet_data)
        
        # Calculate weighted compliance score
        compliance_score = (
            critical_score * QUALITY_WEIGHTS['critical_fields_present'] / 100 +
            validation_score * QUALITY_WEIGHTS['field_values_valid'] / 100 +
            standards_score * QUALITY_WEIGHTS['standards_compliance'] / 100 +
            completeness_score * QUALITY_WEIGHTS['data_completeness'] / 100
        )
        
        quality_level = get_quality_level(compliance_score)
        
        # Build analysis report
        analysis = self._build_analysis_report(
            compliance_score,
            quality_level,
            equipment_code
        )
        
        return {
            'compliance_score': round(compliance_score, 2),
            'quality_level': quality_level,
            'analysis': analysis,
            'issues': self.issues,
            'warnings': self.warnings,
            'passed_checks': self.passed_checks,
            'method': 'rule_based',
            'ai_used': False
        }
    
    def _check_critical_fields(self, data, equipment_code):
        """Check if all critical fields are present"""
        critical_fields = get_critical_fields_for_equipment(equipment_code or 'EE')
        
        present_fields = []
        missing_fields = []
        
        for field in critical_fields:
            # Normalize field name for checking
            field_variations = [
                field,
                field.replace('_', ' '),
                field.replace('_', '').lower(),
                field.lower()
            ]
            
            field_found = False
            for variation in field_variations:
                if variation in data or variation.upper() in data or variation.title() in data:
                    field_found = True
                    present_fields.append(field)
                    break
            
            if not field_found:
                missing_fields.append(field)
        
        # Calculate score
        if not critical_fields:
            score = 100
        else:
            score = (len(present_fields) / len(critical_fields)) * 100
        
        # Record results
        if missing_fields:
            self.issues.append(f"Missing critical fields: {', '.join(missing_fields)}")
        
        if present_fields:
            self.passed_checks.append(f"Found {len(present_fields)}/{len(critical_fields)} critical fields")
        
        return score
    
    def _check_field_values(self, data, equipment_code):
        """Validate field values against rules"""
        validation_results = []
        
        # Check voltage fields
        voltage_fields = ['voltage', 'rated_voltage', 'primary_voltage', 'secondary_voltage', 'voltage_rating']
        for field in voltage_fields:
            if field in data:
                is_valid, message = validate_voltage_value(data[field])
                validation_results.append(is_valid)
                if is_valid:
                    self.passed_checks.append(f"Voltage validation passed: {message}")
                else:
                    self.warnings.append(f"Voltage issue: {message}")
        
        # Check frequency
        frequency_fields = ['frequency', 'rated_frequency']
        for field in frequency_fields:
            if field in data:
                is_valid, message = validate_frequency_value(data[field])
                validation_results.append(is_valid)
                if is_valid:
                    self.passed_checks.append(f"Frequency validation passed: {message}")
                else:
                    self.warnings.append(f"Frequency issue: {message}")
        
        # Check power factor
        pf_fields = ['power_factor', 'powerfactor', 'pf']
        for field in pf_fields:
            if field in data:
                is_valid, message = validate_power_factor(data[field])
                validation_results.append(is_valid)
                if is_valid:
                    self.passed_checks.append(f"Power factor validation passed: {message}")
                else:
                    self.warnings.append(f"Power factor issue: {message}")
        
        # Calculate score
        if not validation_results:
            return 100  # No validatable fields found, assume OK
        
        score = (sum(validation_results) / len(validation_results)) * 100
        return score
    
    def _check_standards_compliance(self, data, equipment_code):
        """Check compliance with standards"""
        compliance_items = []
        
        # Check if standards are referenced
        standard_fields = ['standard', 'standards', 'applicable_standards', 'reference_standard']
        standards_found = False
        
        for field in standard_fields:
            if field in data and data[field]:
                standards_found = True
                compliance_items.append(True)
                self.passed_checks.append(f"Standards referenced: {data[field]}")
                break
        
        if not standards_found:
            self.warnings.append("No standards referenced in datasheet")
            compliance_items.append(False)
        
        # Check manufacturer information
        mfr_fields = ['manufacturer', 'make', 'vendor', 'supplier']
        mfr_found = False
        
        for field in mfr_fields:
            if field in data and data[field]:
                mfr_found = True
                compliance_items.append(True)
                self.passed_checks.append(f"Manufacturer specified: {data[field]}")
                break
        
        if not mfr_found:
            self.warnings.append("Manufacturer not specified")
            compliance_items.append(False)
        
        # Check model/catalog number
        model_fields = ['model', 'model_number', 'catalog_number', 'part_number', 'type']
        model_found = False
        
        for field in model_fields:
            if field in data and data[field]:
                model_found = True
                compliance_items.append(True)
                self.passed_checks.append(f"Model/Type specified: {data[field]}")
                break
        
        if not model_found:
            self.warnings.append("Model/Type not specified")
            compliance_items.append(False)
        
        # Calculate score
        if not compliance_items:
            return 50  # Neutral score if no checks performed
        
        score = (sum(compliance_items) / len(compliance_items)) * 100
        return score
    
    def _check_data_completeness(self, data):
        """Check overall data completeness"""
        total_fields = len(data)
        
        # Exclude metadata fields
        metadata_prefixes = ['_', 'meta_', 'internal_']
        actual_fields = [
            field for field in data.keys()
            if not any(field.startswith(prefix) for prefix in metadata_prefixes)
        ]
        
        filled_fields = [
            field for field in actual_fields
            if data[field] and str(data[field]).strip() and str(data[field]).lower() not in ['n/a', 'na', 'null', 'none', '-', '']
        ]
        
        if not actual_fields:
            score = 0
        else:
            score = (len(filled_fields) / len(actual_fields)) * 100
        
        self.passed_checks.append(f"Data completeness: {len(filled_fields)}/{len(actual_fields)} fields filled")
        
        return score
    
    def _build_analysis_report(self, compliance_score, quality_level, equipment_code):
        """Build comprehensive analysis report"""
        
        # Summary
        summary = f"Rule-based quality check completed with {quality_level.upper()} rating ({compliance_score:.1f}% compliance)."
        
        # Equipment specific notes
        equipment_note = ""
        if equipment_code:
            equipment_note = f" Equipment type: {equipment_code}."
        
        # Issues summary
        issues_summary = ""
        if self.issues:
            issues_summary = f" Found {len(self.issues)} critical issue(s)."
        
        # Warnings summary
        warnings_summary = ""
        if self.warnings:
            warnings_summary = f" {len(self.warnings)} warning(s) detected."
        
        # Passed checks summary
        passed_summary = ""
        if self.passed_checks:
            passed_summary = f" {len(self.passed_checks)} check(s) passed."
        
        # Recommendations
        recommendations = []
        
        if compliance_score < 60:
            recommendations.append("⚠️ This datasheet requires significant improvements.")
            recommendations.append("• Review and complete all missing critical fields")
            recommendations.append("• Verify all technical specifications")
            recommendations.append("• Ensure standards compliance")
        elif compliance_score < 75:
            recommendations.append("📋 This datasheet has some areas for improvement.")
            recommendations.append("• Address identified warnings")
            recommendations.append("• Complete optional fields for better documentation")
        elif compliance_score < 90:
            recommendations.append("✅ This datasheet meets basic quality standards.")
            recommendations.append("• Minor improvements recommended")
            recommendations.append("• Consider adding more detailed specifications")
        else:
            recommendations.append("🌟 Excellent datasheet quality!")
            recommendations.append("• All critical checks passed")
            recommendations.append("• Well-documented and complete")
        
        # Build full analysis
        analysis_parts = [
            summary + equipment_note + issues_summary + warnings_summary + passed_summary,
            "",
            "**Quality Assessment:**",
            f"- Compliance Score: {compliance_score:.1f}%",
            f"- Quality Level: {quality_level.upper()}",
            f"- Validation Method: Rule-Based (AI unavailable)",
            ""
        ]
        
        if self.issues:
            analysis_parts.append("**Critical Issues:**")
            for issue in self.issues:
                analysis_parts.append(f"❌ {issue}")
            analysis_parts.append("")
        
        if self.warnings:
            analysis_parts.append("**Warnings:**")
            for warning in self.warnings:
                analysis_parts.append(f"⚠️ {warning}")
            analysis_parts.append("")
        
        if self.passed_checks:
            analysis_parts.append("**Passed Checks:**")
            # Show top 5 passed checks to keep report concise
            for check in self.passed_checks[:5]:
                analysis_parts.append(f"✅ {check}")
            if len(self.passed_checks) > 5:
                analysis_parts.append(f"... and {len(self.passed_checks) - 5} more checks passed")
            analysis_parts.append("")
        
        analysis_parts.append("**Recommendations:**")
        analysis_parts.extend(recommendations)
        analysis_parts.append("")
        analysis_parts.append("---")
        analysis_parts.append("*Note: This analysis was performed using rule-based validation.*")
        analysis_parts.append("*For AI-powered detailed analysis, please check your OpenAI API quota.*")
        
        return "\n".join(analysis_parts)
