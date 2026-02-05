from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework import status
from django.db.models import Q, Count
import json
import re
from datetime import datetime
from collections import defaultdict
from .adnoc_standards import adnoc_standards
from .s3_service import ElectricalDatasheetS3Service


class QualityCheckerMixin:
    """
    AI-Powered Quality Checker for Electrical Datasheets
    Performs consistency checks, validation, and generates comprehensive reports
    """
    
    @action(detail=True, methods=['post'])
    def quality_check(self, request, pk=None):
        """
        Perform comprehensive quality check on a datasheet
        Returns detailed consistency report with AI-powered insights
        """
        datasheet = self.get_object()
        equipment_type = datasheet.equipment_type
        
        # Initialize report structure
        report = {
            'datasheet_id': datasheet.id,
            'tag_number': datasheet.tag_number,
            'equipment_type': equipment_type.name,
            'check_timestamp': datetime.now().isoformat(),
            'overall_score': 0,
            'status': 'pending',
            'checks': {
                'completeness': {},
                'consistency': {},
                'standards_compliance': {},
                'technical_validation': {},
                'ai_insights': {}
            },
            'issues': [],
            'warnings': [],
            'recommendations': [],
            'summary': {}
        }
        
        # Run all quality checks
        self._check_data_completeness(datasheet, equipment_type, report)
        self._check_internal_consistency(datasheet, equipment_type, report)
        self._check_standards_compliance(datasheet, equipment_type, report)
        self._check_technical_validation(datasheet, equipment_type, report)
        self._generate_ai_insights(datasheet, equipment_type, report)
        
        # Calculate overall score
        report['overall_score'] = self._calculate_quality_score(report)
        report['status'] = self._determine_status(report['overall_score'])
        
        # Generate summary
        report['summary'] = self._generate_summary(report)
        
        return Response(report, status=status.HTTP_200_OK)
    
    @action(detail=False, methods=['post'])
    def batch_quality_check(self, request):
        """
        Perform quality check on multiple datasheets
        """
        datasheet_ids = request.data.get('datasheet_ids', [])
        
        if not datasheet_ids:
            return Response(
                {'error': 'No datasheet IDs provided'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        queryset = self.get_queryset().filter(id__in=datasheet_ids)
        results = []
        
        for datasheet in queryset:
            # Use the single quality_check logic
            request_mock = type('Request', (), {'data': {}})()
            response = self.quality_check(request_mock, pk=datasheet.id)
            results.append(response.data)
        
        batch_summary = {
            'total_checked': len(results),
            'passed': len([r for r in results if r['status'] == 'passed']),
            'failed': len([r for r in results if r['status'] == 'failed']),
            'warning': len([r for r in results if r['status'] == 'warning']),
            'average_score': sum(r['overall_score'] for r in results) / len(results) if results else 0,
            'results': results
        }
        
        return Response(batch_summary, status=status.HTTP_200_OK)
    
    def _check_data_completeness(self, datasheet, equipment_type, report):
        """Check if all required fields are completed"""
        completeness_check = {
            'total_fields': 0,
            'completed_fields': 0,
            'missing_required': [],
            'incomplete_sections': [],
            'completion_percentage': 0
        }
        
        form_data = datasheet.form_data or {}
        sections = equipment_type.sections or []
        
        for section in sections:
            section_complete = True
            fields = section.get('fields', [])
            
            for field in fields:
                completeness_check['total_fields'] += 1
                field_value = form_data.get(field['id'])
                
                if field_value:
                    completeness_check['completed_fields'] += 1
                else:
                    if field.get('required'):
                        completeness_check['missing_required'].append({
                            'field_id': field['id'],
                            'field_label': field['label'],
                            'section': section['name']
                        })
                        report['issues'].append({
                            'severity': 'high',
                            'category': 'completeness',
                            'message': f"Required field '{field['label']}' is missing in section '{section['name']}'"
                        })
                        section_complete = False
            
            if not section_complete:
                completeness_check['incomplete_sections'].append(section['name'])
        
        if completeness_check['total_fields'] > 0:
            completeness_check['completion_percentage'] = round(
                (completeness_check['completed_fields'] / completeness_check['total_fields']) * 100, 2
            )
        
        report['checks']['completeness'] = completeness_check
    
    def _check_internal_consistency(self, datasheet, equipment_type, report):
        """Check internal consistency between related fields"""
        consistency_check = {
            'total_checks': 0,
            'passed_checks': 0,
            'failed_checks': [],
            'consistency_score': 0
        }
        
        form_data = datasheet.form_data or {}
        equipment_id = equipment_type.id
        
        # Equipment-specific consistency checks
        if equipment_id == 'transformer':
            self._check_transformer_consistency(form_data, consistency_check, report)
        elif equipment_id == 'switchgear_11kv':
            self._check_switchgear_consistency(form_data, consistency_check, report)
        
        if consistency_check['total_checks'] > 0:
            consistency_check['consistency_score'] = round(
                (consistency_check['passed_checks'] / consistency_check['total_checks']) * 100, 2
            )
        
        report['checks']['consistency'] = consistency_check
    
    def _check_transformer_consistency(self, form_data, consistency_check, report):
        """Transformer-specific consistency checks"""
        checks = [
            # Check power rating consistency
            {
                'name': 'Power Rating Validation',
                'check': lambda: self._validate_power_rating(form_data),
                'message': 'Power rating should be positive and reasonable (typically 100 kVA - 500 MVA)'
            },
            # Check voltage ratio consistency
            {
                'name': 'Voltage Ratio Validation',
                'check': lambda: self._validate_voltage_ratio(form_data),
                'message': 'Primary voltage should be higher than secondary voltage for step-down transformer'
            },
            # Check connection type and vector group consistency
            {
                'name': 'Vector Group Consistency',
                'check': lambda: self._validate_vector_group(form_data),
                'message': 'Vector group must match the connection types (e.g., Dyn11 for Delta-Star)'
            },
            # Check cooling type and power rating
            {
                'name': 'Cooling System Adequacy',
                'check': lambda: self._validate_cooling_adequacy(form_data),
                'message': 'Cooling type should be appropriate for the power rating'
            },
            # Check impedance voltage range
            {
                'name': 'Impedance Voltage Range',
                'check': lambda: self._validate_impedance(form_data),
                'message': 'Impedance voltage should be between 4% and 15% for power transformers'
            }
        ]
        
        for check_item in checks:
            consistency_check['total_checks'] += 1
            try:
                is_valid, details = check_item['check']()
                if is_valid:
                    consistency_check['passed_checks'] += 1
                else:
                    consistency_check['failed_checks'].append({
                        'check': check_item['name'],
                        'message': check_item['message'],
                        'details': details
                    })
                    report['warnings'].append({
                        'severity': 'medium',
                        'category': 'consistency',
                        'check': check_item['name'],
                        'message': check_item['message'],
                        'details': details
                    })
            except Exception as e:
                consistency_check['failed_checks'].append({
                    'check': check_item['name'],
                    'message': f'Error running check: {str(e)}'
                })
    
    def _check_switchgear_consistency(self, form_data, consistency_check, report):
        """Switchgear-specific consistency checks"""
        checks = [
            # Check rated current vs short circuit current
            {
                'name': 'Current Rating Validation',
                'check': lambda: self._validate_switchgear_currents(form_data),
                'message': 'Short circuit current must be higher than rated current'
            },
            # Check breaking capacity
            {
                'name': 'Breaking Capacity Validation',
                'check': lambda: self._validate_breaking_capacity(form_data),
                'message': 'Rated breaking capacity should be at least equal to short-time current'
            },
            # Check busbar arrangement and panels
            {
                'name': 'Panel Configuration Validation',
                'check': lambda: self._validate_panel_configuration(form_data),
                'message': 'Total panels should match sum of individual panel types'
            },
            # Check CT ratio appropriateness
            {
                'name': 'CT Ratio Validation',
                'check': lambda: self._validate_ct_ratio(form_data),
                'message': 'CT primary rating should be appropriate for rated current'
            },
            # Check protection relay configuration
            {
                'name': 'Protection Relay Validation',
                'check': lambda: self._validate_protection_relays(form_data),
                'message': 'Protection relays should be appropriate for the application'
            }
        ]
        
        for check_item in checks:
            consistency_check['total_checks'] += 1
            try:
                is_valid, details = check_item['check']()
                if is_valid:
                    consistency_check['passed_checks'] += 1
                else:
                    consistency_check['failed_checks'].append({
                        'check': check_item['name'],
                        'message': check_item['message'],
                        'details': details
                    })
                    report['warnings'].append({
                        'severity': 'medium',
                        'category': 'consistency',
                        'check': check_item['name'],
                        'message': check_item['message'],
                        'details': details
                    })
            except Exception as e:
                consistency_check['failed_checks'].append({
                    'check': check_item['name'],
                    'message': f'Error running check: {str(e)}'
                })
    
    def _validate_power_rating(self, form_data):
        """Validate transformer power rating"""
        rated_power = form_data.get('rated_power')
        if not rated_power:
            return False, 'Power rating not specified'
        
        try:
            power = float(rated_power)
            if power <= 0:
                return False, 'Power rating must be positive'
            if power < 100 or power > 500000:
                return False, f'Power rating {power} kVA seems unusual (typical range: 100 kVA - 500 MVA)'
            return True, 'Power rating is valid'
        except (ValueError, TypeError):
            return False, 'Power rating is not a valid number'
    
    def _validate_voltage_ratio(self, form_data):
        """Validate voltage ratio for transformers"""
        primary_voltage = form_data.get('primary_voltage')
        secondary_voltage = form_data.get('secondary_voltage')
        
        if not primary_voltage or not secondary_voltage:
            return True, 'Voltage data not available for validation'
        
        try:
            v_primary = float(primary_voltage)
            v_secondary = float(secondary_voltage)
            
            # For step-down transformers (most common)
            if v_primary > 0 and v_secondary > 0:
                ratio = v_primary / v_secondary
                if ratio < 1:
                    return False, f'Primary voltage ({v_primary} kV) is lower than secondary voltage ({v_secondary} kV) - unusual for step-down transformer'
                return True, f'Voltage ratio is {ratio:.2f}:1'
            return False, 'Invalid voltage values'
        except (ValueError, TypeError):
            return False, 'Voltage values are not valid numbers'
    
    def _validate_vector_group(self, form_data):
        """Validate vector group matches connection types"""
        vector_group = form_data.get('vector_group', '').upper()
        primary_connection = form_data.get('connection_type_primary', '')
        secondary_connection = form_data.get('connection_type_secondary', '')
        
        if not vector_group:
            return True, 'Vector group not specified'
        
        # Vector group validation rules
        vector_rules = {
            'DYN11': {'primary': 'delta', 'secondary': 'star_grounded'},
            'DY11': {'primary': 'delta', 'secondary': 'star'},
            'YYN0': {'primary': 'star_grounded', 'secondary': 'star_grounded'},
            'YD11': {'primary': 'star', 'secondary': 'delta'},
            'DD0': {'primary': 'delta', 'secondary': 'delta'}
        }
        
        if vector_group in vector_rules:
            expected = vector_rules[vector_group]
            if (primary_connection == expected['primary'] and 
                secondary_connection == expected['secondary']):
                return True, f'Vector group {vector_group} matches connection types'
            else:
                return False, f'Vector group {vector_group} does not match connection types (Primary: {primary_connection}, Secondary: {secondary_connection})'
        
        return True, 'Vector group validation passed'
    
    def _validate_cooling_adequacy(self, form_data):
        """Validate cooling type is adequate for power rating"""
        cooling_type = form_data.get('cooling_type', '')
        rated_power = form_data.get('rated_power')
        
        if not cooling_type or not rated_power:
            return True, 'Cooling or power data not available'
        
        try:
            power_kva = float(rated_power)
            
            # Cooling adequacy rules (simplified)
            if power_kva < 5000 and cooling_type in ['ONAN', 'AN']:
                return True, 'Natural cooling appropriate for this power rating'
            elif power_kva >= 5000 and power_kva < 30000 and cooling_type in ['ONAN', 'ONAF', 'AN', 'AF']:
                return True, 'Cooling type appropriate for this power rating'
            elif power_kva >= 30000 and cooling_type in ['ONAF', 'OFAF', 'OFWF']:
                return True, 'Forced cooling appropriate for high power rating'
            else:
                return False, f'Cooling type {cooling_type} may be inadequate for {power_kva} kVA'
        except (ValueError, TypeError):
            return False, 'Invalid power rating for cooling validation'
    
    def _validate_impedance(self, form_data):
        """Validate impedance voltage percentage"""
        impedance = form_data.get('impedance_voltage')
        
        if not impedance:
            return True, 'Impedance not specified'
        
        try:
            impedance_pct = float(impedance)
            if impedance_pct < 4 or impedance_pct > 15:
                return False, f'Impedance {impedance_pct}% is outside typical range (4-15%)'
            return True, f'Impedance {impedance_pct}% is within normal range'
        except (ValueError, TypeError):
            return False, 'Impedance value is not a valid number'
    
    def _validate_switchgear_currents(self, form_data):
        """Validate switchgear current ratings"""
        rated_current = form_data.get('rated_current')
        short_time_current = form_data.get('rated_short_time_current')
        
        if not rated_current or not short_time_current:
            return True, 'Current ratings not available for validation'
        
        try:
            i_rated = float(rated_current)
            i_short = float(short_time_current)
            
            if i_short <= i_rated:
                return False, f'Short-time current ({i_short} kA) should be higher than rated current ({i_rated} A)'
            
            # Typically short circuit current is 20-40 times rated current
            ratio = (i_short * 1000) / i_rated
            if ratio < 10:
                return False, f'Short-time current seems low compared to rated current (ratio: {ratio:.1f}:1)'
            
            return True, f'Current ratings are consistent (short-time/rated ratio: {ratio:.1f}:1)'
        except (ValueError, TypeError):
            return False, 'Current values are not valid numbers'
    
    def _validate_breaking_capacity(self, form_data):
        """Validate breaking capacity"""
        breaking_capacity = form_data.get('rated_breaking_capacity')
        short_time_current = form_data.get('rated_short_time_current')
        
        if not breaking_capacity or not short_time_current:
            return True, 'Breaking capacity data not available'
        
        try:
            i_break = float(breaking_capacity)
            i_short = float(short_time_current)
            
            if i_break < i_short:
                return False, f'Breaking capacity ({i_break} kA) is less than short-time current ({i_short} kA)'
            
            return True, f'Breaking capacity ({i_break} kA) is adequate'
        except (ValueError, TypeError):
            return False, 'Breaking capacity values are not valid numbers'
    
    def _validate_panel_configuration(self, form_data):
        """Validate panel configuration consistency"""
        total_panels = form_data.get('number_of_panels')
        incoming = form_data.get('incoming_panels', 0)
        outgoing = form_data.get('outgoing_feeder_panels', 0)
        bus_coupler = form_data.get('bus_coupler_panels', 0)
        metering = form_data.get('metering_panels', 0)
        vt = form_data.get('vt_panels', 0)
        
        if not total_panels:
            return True, 'Panel configuration not specified'
        
        try:
            total = int(total_panels)
            sum_panels = int(incoming or 0) + int(outgoing or 0) + int(bus_coupler or 0) + int(metering or 0) + int(vt or 0)
            
            if sum_panels != total:
                return False, f'Panel count mismatch: Total specified as {total}, but sum of individual types is {sum_panels}'
            
            return True, f'Panel configuration is consistent ({total} panels total)'
        except (ValueError, TypeError):
            return False, 'Panel count values are not valid numbers'
    
    def _validate_ct_ratio(self, form_data):
        """Validate CT ratio is appropriate"""
        rated_current = form_data.get('rated_current')
        ct_primary = form_data.get('ct_ratio_primary')
        
        if not rated_current or not ct_primary:
            return True, 'CT ratio data not available'
        
        try:
            i_rated = float(rated_current)
            ct_prim = float(ct_primary)
            
            # CT primary should be 1.2 to 1.5 times rated current
            ratio = ct_prim / i_rated
            if ratio < 1.0 or ratio > 2.0:
                return False, f'CT primary ({ct_prim} A) seems inappropriate for rated current ({i_rated} A) - ratio: {ratio:.2f}'
            
            return True, f'CT ratio appropriate (CT/Rated: {ratio:.2f})'
        except (ValueError, TypeError):
            return False, 'CT ratio values are not valid numbers'
    
    def _validate_protection_relays(self, form_data):
        """Validate protection relay configuration"""
        protection_relays = form_data.get('protection_relays', [])
        
        if not protection_relays or not isinstance(protection_relays, list):
            return True, 'Protection relay data not available'
        
        # Check for essential protection
        essential_protections = ['overcurrent', 'earth_fault']
        missing_essential = [p for p in essential_protections if p not in protection_relays]
        
        if missing_essential:
            return False, f'Missing essential protection: {", ".join(missing_essential)}'
        
        return True, f'{len(protection_relays)} protection relays configured'
    
    def _check_standards_compliance(self, datasheet, equipment_type, report):
        """Check compliance with industry standards and ADNOC specifications"""
        standards_check = {
            'applicable_standards': equipment_type.standards or [],
            'compliance_status': {},
            'non_compliant_items': [],
            'adnoc_validation': {}
        }
        
        form_data = datasheet.form_data or {}
        design_standards = form_data.get('design_standard', [])
        
        # Check general standards compliance
        if isinstance(design_standards, list):
            for std in equipment_type.standards:
                is_compliant = any(std.replace(' ', '_').upper() in ds.upper() for ds in design_standards)
                standards_check['compliance_status'][std] = is_compliant
                
                if not is_compliant:
                    standards_check['non_compliant_items'].append(std)
                    report['warnings'].append({
                        'severity': 'medium',
                        'category': 'standards',
                        'message': f'Design standard {std} not explicitly mentioned'
                    })
        
        # Perform ADNOC-specific validation
        equipment_id = equipment_type.id
        if equipment_id == 'transformer':
            adnoc_result = self._validate_adnoc_transformer_standards(form_data, report)
            standards_check['adnoc_validation'] = adnoc_result
        elif equipment_id == 'switchgear_11kv':
            adnoc_result = self._validate_adnoc_switchgear_standards(form_data, report)
            standards_check['adnoc_validation'] = adnoc_result
        
        report['checks']['standards_compliance'] = standards_check
    
    def _validate_adnoc_transformer_standards(self, form_data, report):
        """
        Validate transformer datasheet against ADNOC standards.
        Extracts voltage class and validates all parameters.
        """
        # Determine voltage class from primary voltage
        primary_voltage = form_data.get('primary_voltage', '')
        voltage_class = self._determine_voltage_class(primary_voltage)
        
        # Load ADNOC standards for this voltage class
        adnoc_stds = adnoc_standards.get_transformer_standards(voltage_class)
        
        if not adnoc_stds or 'voltage_classes' not in adnoc_stds:
            return {
                'status': 'skipped',
                'message': f'No ADNOC standards available for voltage class: {voltage_class}'
            }
        
        voltage_class_std = adnoc_stds['voltage_classes'].get(voltage_class, {})
        if not voltage_class_std:
            return {
                'status': 'skipped',
                'message': f'No specific standards for {voltage_class}'
            }
        
        validation_results = {
            'voltage_class': voltage_class,
            'standard_source': adnoc_stds.get('source'),
            'checks_performed': 0,
            'checks_passed': 0,
            'checks_failed': 0,
            'details': []
        }
        
        # Validate voltage ratings
        self._validate_voltage_range(form_data, voltage_class_std, validation_results, report)
        
        # Validate power rating
        self._validate_power_range(form_data, voltage_class_std, validation_results, report)
        
        # Validate frequency
        self._validate_frequency(form_data, voltage_class_std, validation_results, report)
        
        # Validate connection type
        self._validate_connection_type(form_data, voltage_class_std, validation_results, report)
        
        # Validate cooling type
        self._validate_cooling_type(form_data, voltage_class_std, validation_results, report)
        
        # Validate impedance
        self._validate_impedance_range(form_data, voltage_class_std, validation_results, report)
        
        # Validate tap range
        self._validate_tap_range(form_data, voltage_class_std, validation_results, report)
        
        # Validate insulation class
        self._validate_insulation_class(form_data, voltage_class_std, validation_results, report)
        
        # Calculate compliance percentage
        if validation_results['checks_performed'] > 0:
            compliance_pct = (validation_results['checks_passed'] / validation_results['checks_performed']) * 100
            validation_results['compliance_percentage'] = round(compliance_pct, 1)
            validation_results['status'] = 'passed' if compliance_pct >= 80 else 'failed'
        
        return validation_results
    
    def _validate_adnoc_switchgear_standards(self, form_data, report):
        """
        Validate switchgear datasheet against ADNOC standards.
        """
        # Determine voltage class (typically 11kV for this equipment type)
        voltage_class = '11kv'
        
        # Load ADNOC standards
        adnoc_stds = adnoc_standards.get_switchgear_standards(voltage_class)
        
        if not adnoc_stds or 'voltage_classes' not in adnoc_stds:
            return {
                'status': 'skipped',
                'message': 'No ADNOC standards available for switchgear'
            }
        
        voltage_class_std = adnoc_stds['voltage_classes'].get(voltage_class, {})
        
        validation_results = {
            'voltage_class': voltage_class,
            'standard_source': adnoc_stds.get('source'),
            'checks_performed': 0,
            'checks_passed': 0,
            'checks_failed': 0,
            'details': []
        }
        
        # Validate rated voltage
        self._validate_sg_rated_voltage(form_data, voltage_class_std, validation_results, report)
        
        # Validate rated current
        self._validate_sg_rated_current(form_data, voltage_class_std, validation_results, report)
        
        # Validate short circuit current
        self._validate_sg_short_circuit(form_data, voltage_class_std, validation_results, report)
        
        # Validate breaking capacity
        self._validate_sg_breaking_capacity(form_data, voltage_class_std, validation_results, report)
        
        # Validate insulation levels
        self._validate_sg_insulation_level(form_data, voltage_class_std, validation_results, report)
        
        # Validate circuit breaker type
        self._validate_sg_cb_type(form_data, voltage_class_std, validation_results, report)
        
        # Calculate compliance
        if validation_results['checks_performed'] > 0:
            compliance_pct = (validation_results['checks_passed'] / validation_results['checks_performed']) * 100
            validation_results['compliance_percentage'] = round(compliance_pct, 1)
            validation_results['status'] = 'passed' if compliance_pct >= 80 else 'failed'
        
        return validation_results
    
    def _determine_voltage_class(self, voltage_str):
        """Determine voltage class from voltage value"""
        try:
            voltage = float(str(voltage_str).replace('kV', '').strip())
            if 10 <= voltage <= 12:
                return '11kv'
            elif 30 <= voltage <= 35:
                return '33kv'
            elif 60 <= voltage <= 70:
                return '66kv'
            elif 130 <= voltage <= 140:
                return '132kv'
        except (ValueError, TypeError):
            pass
        return 'all'
    
    # ADNOC Validation Helper Methods - Transformer
    
    def _validate_voltage_range(self, form_data, standards, validation_results, report):
        """Validate primary voltage against ADNOC standards"""
        validation_results['checks_performed'] += 1
        
        primary_voltage = form_data.get('primary_voltage')
        if not primary_voltage:
            validation_results['details'].append({
                'check': 'Primary Voltage',
                'status': 'skipped',
                'message': 'Primary voltage not specified'
            })
            return
        
        try:
            voltage = float(str(primary_voltage).replace('kV', '').strip())
            voltage_std = standards.get('primary_voltage', {})
            v_min = voltage_std.get('min')
            v_max = voltage_std.get('max')
            
            if v_min and v_max and v_min <= voltage <= v_max:
                validation_results['checks_passed'] += 1
                validation_results['details'].append({
                    'check': 'Primary Voltage',
                    'status': 'passed',
                    'value': f'{voltage} kV',
                    'standard_range': f'{v_min}-{v_max} kV'
                })
            else:
                validation_results['checks_failed'] += 1
                validation_results['details'].append({
                    'check': 'Primary Voltage',
                    'status': 'failed',
                    'value': f'{voltage} kV',
                    'standard_range': f'{v_min}-{v_max} kV',
                    'message': f'Voltage {voltage} kV is outside ADNOC standard range'
                })
                report['issues'].append({
                    'severity': 'high',
                    'category': 'adnoc_standards',
                    'check': 'Primary Voltage',
                    'message': f'Primary voltage {voltage} kV not within ADNOC standard range ({v_min}-{v_max} kV)'
                })
        except (ValueError, TypeError):
            validation_results['details'].append({
                'check': 'Primary Voltage',
                'status': 'error',
                'message': 'Invalid voltage format'
            })
    
    def _validate_power_range(self, form_data, standards, validation_results, report):
        """Validate power rating against ADNOC standards"""
        validation_results['checks_performed'] += 1
        
        rated_power = form_data.get('rated_power')
        if not rated_power:
            validation_results['details'].append({
                'check': 'Power Rating',
                'status': 'skipped',
                'message': 'Power rating not specified'
            })
            return
        
        try:
            power = float(rated_power)
            power_std = standards.get('ratings', {})
            p_min = power_std.get('min')
            p_max = power_std.get('max')
            
            if p_min and p_max and p_min <= power <= p_max:
                validation_results['checks_passed'] += 1
                validation_results['details'].append({
                    'check': 'Power Rating',
                    'status': 'passed',
                    'value': f'{power} kVA',
                    'standard_range': f'{p_min}-{p_max} kVA'
                })
            else:
                validation_results['checks_failed'] += 1
                validation_results['details'].append({
                    'check': 'Power Rating',
                    'status': 'failed',
                    'value': f'{power} kVA',
                    'standard_range': f'{p_min}-{p_max} kVA'
                })
                report['issues'].append({
                    'severity': 'high',
                    'category': 'adnoc_standards',
                    'check': 'Power Rating',
                    'message': f'Power rating {power} kVA not within ADNOC standard range'
                })
        except (ValueError, TypeError):
            validation_results['details'].append({
                'check': 'Power Rating',
                'status': 'error',
                'message': 'Invalid power rating format'
            })
    
    def _validate_frequency(self, form_data, standards, validation_results, report):
        """Validate frequency against ADNOC standards"""
        validation_results['checks_performed'] += 1
        
        frequency = form_data.get('frequency')
        if not frequency:
            return
        
        try:
            freq = float(frequency)
            freq_std = standards.get('frequency', {})
            expected_freq = freq_std.get('value', 50)
            tolerance = freq_std.get('tolerance', 1)
            
            if abs(freq - expected_freq) <= tolerance:
                validation_results['checks_passed'] += 1
                validation_results['details'].append({
                    'check': 'Frequency',
                    'status': 'passed',
                    'value': f'{freq} Hz',
                    'standard': f'{expected_freq} Hz ± {tolerance} Hz'
                })
            else:
                validation_results['checks_failed'] += 1
                report['warnings'].append({
                    'severity': 'medium',
                    'category': 'adnoc_standards',
                    'check': 'Frequency',
                    'message': f'Frequency {freq} Hz deviates from standard {expected_freq} Hz'
                })
        except (ValueError, TypeError):
            pass
    
    def _validate_connection_type(self, form_data, standards, validation_results, report):
        """Validate connection type against ADNOC standards"""
        validation_results['checks_performed'] += 1
        
        connection_types = standards.get('connection_types', [])
        vector_group = form_data.get('vector_group', '').upper()
        
        if not vector_group:
            return
        
        if vector_group in [ct.upper() for ct in connection_types]:
            validation_results['checks_passed'] += 1
            validation_results['details'].append({
                'check': 'Connection Type',
                'status': 'passed',
                'value': vector_group,
                'standard_options': connection_types
            })
        else:
            validation_results['checks_failed'] += 1
            report['warnings'].append({
                'severity': 'medium',
                'category': 'adnoc_standards',
                'check': 'Connection Type',
                'message': f'Vector group {vector_group} not in ADNOC standard list: {", ".join(connection_types)}'
            })
    
    def _validate_cooling_type(self, form_data, standards, validation_results, report):
        """Validate cooling type against ADNOC standards"""
        validation_results['checks_performed'] += 1
        
        cooling_types = standards.get('cooling', [])
        cooling = form_data.get('cooling_type', '').upper()
        
        if not cooling:
            return
        
        if cooling in [ct.upper() for ct in cooling_types]:
            validation_results['checks_passed'] += 1
            validation_results['details'].append({
                'check': 'Cooling Type',
                'status': 'passed',
                'value': cooling
            })
        else:
            validation_results['checks_failed'] += 1
            report['warnings'].append({
                'severity': 'medium',
                'category': 'adnoc_standards',
                'check': 'Cooling Type',
                'message': f'Cooling type {cooling} not in ADNOC standard list: {", ".join(cooling_types)}'
            })
    
    def _validate_impedance_range(self, form_data, standards, validation_results, report):
        """Validate impedance against ADNOC standards"""
        validation_results['checks_performed'] += 1
        
        impedance = form_data.get('impedance_voltage')
        if not impedance:
            return
        
        try:
            imp = float(str(impedance).replace('%', '').strip())
            imp_std = standards.get('impedance', {})
            imp_min = imp_std.get('min')
            imp_max = imp_std.get('max')
            
            if imp_min and imp_max and imp_min <= imp <= imp_max:
                validation_results['checks_passed'] += 1
                validation_results['details'].append({
                    'check': 'Impedance Voltage',
                    'status': 'passed',
                    'value': f'{imp}%',
                    'standard_range': f'{imp_min}-{imp_max}%'
                })
            else:
                validation_results['checks_failed'] += 1
                report['warnings'].append({
                    'severity': 'medium',
                    'category': 'adnoc_standards',
                    'check': 'Impedance',
                    'message': f'Impedance {imp}% outside ADNOC range ({imp_min}-{imp_max}%)'
                })
        except (ValueError, TypeError):
            pass
    
    def _validate_tap_range(self, form_data, standards, validation_results, report):
        """Validate tap changer range"""
        validation_results['checks_performed'] += 1
        
        tap_range = form_data.get('tap_range')
        if tap_range:
            validation_results['checks_passed'] += 1
            validation_results['details'].append({
                'check': 'Tap Range',
                'status': 'passed',
                'value': tap_range
            })
    
    def _validate_insulation_class(self, form_data, standards, validation_results, report):
        """Validate insulation class"""
        validation_results['checks_performed'] += 1
        
        insulation_class = form_data.get('insulation_class', '').upper()
        if not insulation_class:
            return
        
        insulation_std = standards.get('insulation_class', {})
        valid_classes = insulation_std.get('options', [])
        
        if insulation_class in valid_classes:
            validation_results['checks_passed'] += 1
            validation_results['details'].append({
                'check': 'Insulation Class',
                'status': 'passed',
                'value': insulation_class
            })
        else:
            validation_results['checks_failed'] += 1
    
    # ADNOC Validation Helper Methods - Switchgear
    
    def _validate_sg_rated_voltage(self, form_data, standards, validation_results, report):
        """Validate switchgear rated voltage"""
        validation_results['checks_performed'] += 1
        
        rated_voltage = form_data.get('rated_voltage')
        if not rated_voltage:
            return
        
        try:
            voltage = float(str(rated_voltage).replace('kV', '').strip())
            voltage_std = standards.get('rated_voltage', {})
            expected = voltage_std.get('value')
            
            if expected and abs(voltage - expected) < 2:  # ±2kV tolerance
                validation_results['checks_passed'] += 1
                validation_results['details'].append({
                    'check': 'Rated Voltage',
                    'status': 'passed',
                    'value': f'{voltage} kV',
                    'standard': f'{expected} kV'
                })
            else:
                validation_results['checks_failed'] += 1
                report['issues'].append({
                    'severity': 'high',
                    'category': 'adnoc_standards',
                    'check': 'Rated Voltage',
                    'message': f'Rated voltage {voltage} kV does not match ADNOC standard {expected} kV'
                })
        except (ValueError, TypeError):
            pass
    
    def _validate_sg_rated_current(self, form_data, standards, validation_results, report):
        """Validate switchgear rated current"""
        validation_results['checks_performed'] += 1
        
        rated_current = form_data.get('rated_current')
        if not rated_current:
            return
        
        try:
            current = float(rated_current)
            current_options = standards.get('rated_current', {}).get('options', [])
            
            if current in current_options:
                validation_results['checks_passed'] += 1
                validation_results['details'].append({
                    'check': 'Rated Current',
                    'status': 'passed',
                    'value': f'{current} A'
                })
            else:
                validation_results['checks_failed'] += 1
                report['warnings'].append({
                    'severity': 'medium',
                    'category': 'adnoc_standards',
                    'check': 'Rated Current',
                    'message': f'Rated current {current} A not in ADNOC standard options: {current_options}'
                })
        except (ValueError, TypeError):
            pass
    
    def _validate_sg_short_circuit(self, form_data, standards, validation_results, report):
        """Validate short circuit current"""
        validation_results['checks_performed'] += 1
        
        sc_current = form_data.get('rated_short_time_current')
        if not sc_current:
            return
        
        try:
            current = float(sc_current)
            sc_options = standards.get('rated_short_time_current', {}).get('values', [])
            
            if current in sc_options:
                validation_results['checks_passed'] += 1
                validation_results['details'].append({
                    'check': 'Short Circuit Current',
                    'status': 'passed',
                    'value': f'{current} kA'
                })
            else:
                validation_results['checks_failed'] += 1
        except (ValueError, TypeError):
            pass
    
    def _validate_sg_breaking_capacity(self, form_data, standards, validation_results, report):
        """Validate breaking capacity"""
        validation_results['checks_performed'] += 1
        
        breaking_current = form_data.get('rated_breaking_current')
        if breaking_current:
            validation_results['checks_passed'] += 1
            validation_results['details'].append({
                'check': 'Breaking Capacity',
                'status': 'passed',
                'value': f'{breaking_current} kA'
            })
    
    def _validate_sg_insulation_level(self, form_data, standards, validation_results, report):
        """Validate insulation level"""
        validation_results['checks_performed'] += 1
        
        insulation_std = standards.get('insulation_level', {})
        power_freq = form_data.get('power_frequency_withstand')
        impulse = form_data.get('lightning_impulse_withstand')
        
        checks_passed_count = 0
        if power_freq:
            try:
                pf = float(power_freq)
                expected_pf = insulation_std.get('power_frequency', {}).get('value')
                if expected_pf and abs(pf - expected_pf) < 5:
                    checks_passed_count += 1
            except (ValueError, TypeError):
                pass
        
        if impulse:
            try:
                imp = float(impulse)
                expected_imp = insulation_std.get('impulse', {}).get('value')
                if expected_imp and abs(imp - expected_imp) < 10:
                    checks_passed_count += 1
            except (ValueError, TypeError):
                pass
        
        if checks_passed_count > 0:
            validation_results['checks_passed'] += 1
            validation_results['details'].append({
                'check': 'Insulation Level',
                'status': 'passed'
            })
    
    def _validate_sg_cb_type(self, form_data, standards, validation_results, report):
        """Validate circuit breaker type"""
        validation_results['checks_performed'] += 1
        
        cb_type = form_data.get('circuit_breaker_type', '').lower()
        if not cb_type:
            return
        
        cb_std = standards.get('circuit_breaker', {})
        valid_types = [t.lower() for t in cb_std.get('type', [])]
        
        if any(vt in cb_type for vt in valid_types):
            validation_results['checks_passed'] += 1
            validation_results['details'].append({
                'check': 'Circuit Breaker Type',
                'status': 'passed',
                'value': cb_type
            })
        else:
            validation_results['checks_failed'] += 1
    
    def _check_technical_validation(self, datasheet, equipment_type, report):
        """Perform technical validation checks"""
        technical_check = {
            'environmental_conditions': self._validate_environmental(datasheet.form_data),
            'nameplate_rating': self._validate_nameplate_ratings(datasheet.form_data, equipment_type.id),
            'accessories': self._validate_accessories(datasheet.form_data, equipment_type.id)
        }
        
        report['checks']['technical_validation'] = technical_check
    
    def _validate_environmental(self, form_data):
        """Validate environmental conditions"""
        ambient_max = form_data.get('ambient_temp_max')
        ambient_min = form_data.get('ambient_temp_min')
        
        validation = {'status': 'passed', 'notes': []}
        
        if ambient_max and ambient_min:
            try:
                t_max = float(ambient_max)
                t_min = float(ambient_min)
                
                if t_max <= t_min:
                    validation['status'] = 'failed'
                    validation['notes'].append('Maximum temperature must be higher than minimum')
                elif t_max > 60:
                    validation['status'] = 'warning'
                    validation['notes'].append(f'Maximum temperature {t_max}°C is very high')
                elif t_min < -30:
                    validation['status'] = 'warning'
                    validation['notes'].append(f'Minimum temperature {t_min}°C is very low')
                else:
                    validation['notes'].append(f'Temperature range: {t_min}°C to {t_max}°C is acceptable')
            except (ValueError, TypeError):
                validation['status'] = 'error'
                validation['notes'].append('Invalid temperature values')
        
        return validation
    
    def _validate_nameplate_ratings(self, form_data, equipment_id):
        """Validate nameplate ratings"""
        validation = {'status': 'passed', 'notes': []}
        
        if equipment_id == 'transformer':
            required_ratings = ['rated_power', 'primary_voltage', 'secondary_voltage', 'frequency']
        elif equipment_id == 'switchgear_11kv':
            required_ratings = ['rated_voltage', 'rated_current', 'rated_frequency']
        else:
            return validation
        
        missing = [r for r in required_ratings if not form_data.get(r)]
        
        if missing:
            validation['status'] = 'failed'
            validation['notes'].append(f'Missing nameplate ratings: {", ".join(missing)}')
        else:
            validation['notes'].append('All nameplate ratings provided')
        
        return validation
    
    def _validate_accessories(self, form_data, equipment_id):
        """Validate accessories and auxiliary equipment"""
        validation = {'status': 'passed', 'notes': []}
        
        if equipment_id == 'transformer':
            protection_devices = form_data.get('protection_devices', [])
            if not protection_devices:
                validation['status'] = 'warning'
                validation['notes'].append('No protection devices specified')
            else:
                validation['notes'].append(f'{len(protection_devices)} protection devices specified')
        
        elif equipment_id == 'switchgear_11kv':
            metering = form_data.get('metering_equipment', [])
            if not metering:
                validation['status'] = 'warning'
                validation['notes'].append('No metering equipment specified')
            else:
                validation['notes'].append(f'{len(metering)} metering devices specified')
        
        return validation
    
    def _generate_ai_insights(self, datasheet, equipment_type, report):
        """Generate AI-powered insights and recommendations"""
        insights = {
            'optimization_suggestions': [],
            'cost_efficiency': [],
            'reliability_enhancements': [],
            'maintenance_recommendations': [],
            'energy_efficiency': []
        }
        
        form_data = datasheet.form_data or {}
        equipment_id = equipment_type.id
        
        # AI-powered insights based on data patterns
        if equipment_id == 'transformer':
            insights['optimization_suggestions'].extend(
                self._generate_transformer_insights(form_data)
            )
        elif equipment_id == 'switchgear_11kv':
            insights['optimization_suggestions'].extend(
                self._generate_switchgear_insights(form_data)
            )
        
        # Generic insights
        self._add_generic_insights(form_data, insights, report)
        
        report['checks']['ai_insights'] = insights
        
        # Add top recommendations to main report
        all_recommendations = (
            insights['optimization_suggestions'] +
            insights['cost_efficiency'] +
            insights['reliability_enhancements']
        )
        
        report['recommendations'].extend(all_recommendations[:5])  # Top 5 recommendations
    
    def _generate_transformer_insights(self, form_data):
        """Generate transformer-specific AI insights"""
        insights = []
        
        # Cooling optimization
        cooling_type = form_data.get('cooling_type', '')
        rated_power = form_data.get('rated_power')
        
        if cooling_type == 'ONAN' and rated_power:
            try:
                power = float(rated_power)
                if power > 10000:
                    insights.append({
                        'type': 'optimization',
                        'priority': 'medium',
                        'title': 'Cooling System Upgrade',
                        'description': f'Consider ONAF cooling for {power} kVA transformer to improve efficiency and reduce losses',
                        'potential_benefit': 'Up to 15% reduction in operating temperature'
                    })
            except:
                pass
        
        # Loss reduction
        no_load_loss = form_data.get('no_load_loss')
        load_loss = form_data.get('load_loss')
        
        if no_load_loss and load_loss:
            try:
                total_loss = float(no_load_loss) + float(load_loss)
                if rated_power:
                    power = float(rated_power)
                    loss_percentage = (total_loss / power) * 100
                    
                    if loss_percentage > 2:
                        insights.append({
                            'type': 'efficiency',
                            'priority': 'high',
                            'title': 'High Loss Percentage',
                            'description': f'Total losses are {loss_percentage:.2f}% of rated power. Consider specifying low-loss transformer',
                            'potential_benefit': 'Significant energy cost savings over lifetime'
                        })
            except:
                pass
        
        # Insulation upgrade
        insulation_type = form_data.get('insulation_type', '')
        if insulation_type == 'mineral_oil':
            insights.append({
                'type': 'reliability',
                'priority': 'low',
                'title': 'Insulation Upgrade Option',
                'description': 'Consider synthetic or natural ester for better fire safety and environmental performance',
                'potential_benefit': 'Enhanced safety and reduced environmental impact'
            })
        
        return insights
    
    def _generate_switchgear_insights(self, form_data):
        """Generate switchgear-specific AI insights"""
        insights = []
        
        # Circuit breaker type optimization
        cb_type = form_data.get('circuit_breaker_type', '')
        rated_voltage = form_data.get('rated_voltage')
        
        if cb_type == 'air_cb' and rated_voltage:
            try:
                voltage = float(rated_voltage)
                if voltage >= 11:
                    insights.append({
                        'type': 'optimization',
                        'priority': 'high',
                        'title': 'Circuit Breaker Technology',
                        'description': 'Consider VCB instead of Air CB for 11kV application - better performance and maintenance',
                        'potential_benefit': 'Lower maintenance costs and longer service life'
                    })
            except:
                pass
        
        # SCADA integration
        scada = form_data.get('scada_integration', '')
        if scada == 'no':
            insights.append({
                'type': 'reliability',
                'priority': 'medium',
                'title': 'SCADA Integration',
                'description': 'Consider SCADA integration for remote monitoring and faster fault detection',
                'potential_benefit': 'Improved uptime and reduced maintenance costs'
            })
        
        # Protection enhancement
        protection_relays = form_data.get('protection_relays', [])
        if isinstance(protection_relays, list) and 'differential' not in protection_relays:
            insights.append({
                'type': 'reliability',
                'priority': 'medium',
                'title': 'Enhanced Protection',
                'description': 'Consider adding differential protection for critical feeders',
                'potential_benefit': 'Faster fault clearance and equipment protection'
            })
        
        # Communication protocol
        comm_protocol = form_data.get('communication_protocol', [])
        if isinstance(comm_protocol, list) and 'iec61850' not in comm_protocol:
            insights.append({
                'type': 'optimization',
                'priority': 'low',
                'title': 'Modern Communication Protocol',
                'description': 'IEC 61850 provides better interoperability and future-proofing',
                'potential_benefit': 'Enhanced integration with modern systems'
            })
        
        return insights
    
    def _add_generic_insights(self, form_data, insights, report):
        """Add generic insights applicable to all equipment"""
        
        # Documentation completeness
        completion_pct = report['checks']['completeness'].get('completion_percentage', 0)
        if completion_pct < 100:
            insights['optimization_suggestions'].append({
                'type': 'documentation',
                'priority': 'high' if completion_pct < 70 else 'medium',
                'title': 'Complete Data Entry',
                'description': f'Datasheet is {completion_pct:.0f}% complete. Complete all fields for comprehensive documentation',
                'potential_benefit': 'Better maintenance planning and troubleshooting'
            })
        
        # Standards compliance
        design_standards = form_data.get('design_standard', [])
        if not design_standards or (isinstance(design_standards, list) and len(design_standards) < 2):
            insights['reliability_enhancements'].append({
                'type': 'compliance',
                'priority': 'high',
                'title': 'Standards Documentation',
                'description': 'Specify all applicable design standards for compliance verification',
                'potential_benefit': 'Ensures regulatory compliance and quality assurance'
            })
        
        # Testing requirements
        testing_required = form_data.get('testing_required', [])
        if not testing_required or (isinstance(testing_required, list) and len(testing_required) < 3):
            insights['reliability_enhancements'].append({
                'type': 'quality',
                'priority': 'medium',
                'title': 'Comprehensive Testing',
                'description': 'Specify comprehensive testing requirements including routine and type tests',
                'potential_benefit': 'Ensures equipment quality and performance'
            })
    
    def _calculate_quality_score(self, report):
        """Calculate overall quality score (0-100)"""
        scores = []
        
        # Completeness score (40% weight)
        completion_pct = report['checks']['completeness'].get('completion_percentage', 0)
        scores.append(('completeness', completion_pct, 0.40))
        
        # Consistency score (30% weight)
        consistency_score = report['checks']['consistency'].get('consistency_score', 100)
        scores.append(('consistency', consistency_score, 0.30))
        
        # Issues penalty (15% weight)
        issue_count = len(report['issues'])
        issue_score = max(0, 100 - (issue_count * 10))
        scores.append(('issues', issue_score, 0.15))
        
        # Warnings penalty (15% weight)
        warning_count = len(report['warnings'])
        warning_score = max(0, 100 - (warning_count * 5))
        scores.append(('warnings', warning_score, 0.15))
        
        # Calculate weighted score
        total_score = sum(score * weight for _, score, weight in scores)
        
        return round(total_score, 2)
    
    def _determine_status(self, score):
        """Determine quality status based on score"""
        if score >= 90:
            return 'passed'
        elif score >= 70:
            return 'warning'
        else:
            return 'failed'
    
    def _generate_summary(self, report):
        """Generate executive summary of quality check"""
        summary = {
            'status_text': '',
            'key_findings': [],
            'action_required': [],
            'strengths': []
        }
        
        score = report['overall_score']
        status = report['status']
        
        # Status text
        if status == 'passed':
            summary['status_text'] = f'Excellent quality ({score:.0f}/100). Datasheet meets all requirements.'
        elif status == 'warning':
            summary['status_text'] = f'Acceptable quality ({score:.0f}/100). Some improvements recommended.'
        else:
            summary['status_text'] = f'Needs improvement ({score:.0f}/100). Critical issues must be addressed.'
        
        # Key findings
        completion = report['checks']['completeness']['completion_percentage']
        summary['key_findings'].append(f'Data completeness: {completion:.0f}%')
        
        consistency = report['checks']['consistency']['consistency_score']
        if consistency < 100:
            summary['key_findings'].append(f'Consistency score: {consistency:.0f}% - some checks failed')
        
        # Actions required
        if report['issues']:
            summary['action_required'].append(f'{len(report["issues"])} critical issues require immediate attention')
        
        if report['warnings']:
            summary['action_required'].append(f'{len(report["warnings"])} warnings should be reviewed')
        
        missing_required = len(report['checks']['completeness'].get('missing_required', []))
        if missing_required:
            summary['action_required'].append(f'{missing_required} required fields are missing')
        
        # Strengths
        if completion >= 90:
            summary['strengths'].append('Comprehensive data documentation')
        
        if consistency >= 90:
            summary['strengths'].append('Internally consistent data')
        
        if not report['issues']:
            summary['strengths'].append('No critical issues identified')
        
        return summary
