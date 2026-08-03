"""
Validation Framework for Electrical Datasheets
Pluggable validators for different equipment types and validation categories
Implements comprehensive quality checks following project standards
"""

from typing import Dict, List, Any, Optional
from abc import ABC, abstractmethod
import re
from decimal import Decimal, InvalidOperation
import logging

logger = logging.getLogger(__name__)


class ValidationIssueDTO:
    """Data Transfer Object for validation issues"""
    
    def __init__(
        self,
        sheet_name: str = '',
        section: str = '',
        item: str = '',
        severity: str = 'error',
        code: str = '',
        message: str = '',
        expected: str = '',
        actual: str = '',
        rule_name: str = '',
        category: str = '',
        row_number: Optional[int] = None,
        column_name: str = '',
    ):
        self.sheet_name = sheet_name
        self.section = section
        self.item = item
        self.severity = severity  # 'error', 'warning', 'info'
        self.code = code
        self.message = message
        self.expected = expected
        self.actual = actual
        self.rule_name = rule_name
        self.category = category  # 'document_control', 'technical_content', 'consistency', 'standards'
        self.row_number = row_number
        self.column_name = column_name
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary"""
        return {
            'sheet_name': self.sheet_name,
            'section': self.section,
            'item': self.item,
            'severity': self.severity,
            'code': self.code,
            'message': self.message,
            'expected': self.expected,
            'actual': self.actual,
            'rule_name': self.rule_name,
            'category': self.category,
            'row_number': self.row_number,
            'column_name': self.column_name,
        }


class BaseValidator(ABC):
    """Base class for all validators"""
    
    def __init__(self, parsed_data: Dict[str, Any]):
        self.parsed_data = parsed_data
        self.issues: List[ValidationIssueDTO] = []
    
    @abstractmethod
    def validate(self) -> List[ValidationIssueDTO]:
        """
        Run validation and return list of issues
        
        Returns:
            List of ValidationIssueDTO objects
        """
        pass
    
    def add_issue(self, **kwargs):
        """Helper to add an issue"""
        issue = ValidationIssueDTO(**kwargs)
        self.issues.append(issue)
    
    def get_field_value(self, section: str, field_pattern: str, sheet_name: str = None) -> Optional[str]:
        """
        Helper to find a field value in technical data
        
        Args:
            section: Section name (e.g., 'GENERAL DATA')
            field_pattern: Pattern to match in description (case-insensitive)
            sheet_name: Optional specific sheet name
            
        Returns:
            Field value or None
        """
        technical_data = self.parsed_data.get('technical_data', {})
        
        # Search in all sheets if no specific sheet
        sheets_to_search = [sheet_name] if sheet_name else technical_data.keys()
        
        for sheet in sheets_to_search:
            if sheet not in technical_data:
                continue
            
            sheet_data = technical_data[sheet]
            sections = sheet_data.get('sections', {})
            
            if section in sections:
                items = sections[section]
                pattern = re.compile(field_pattern, re.IGNORECASE)
                
                for item in items:
                    if pattern.search(item['description']):
                        # Return specified data, or vendor data if specified is empty
                        specified = item.get('specified_design_data', '').strip()
                        vendor = item.get('vendor_data', '').strip()
                        return specified if specified else vendor
        
        return None


class DocumentControlValidator(BaseValidator):
    """Validator for document control and administrative information"""
    
    def validate(self) -> List[ValidationIssueDTO]:
        """Validate document control fields"""
        self.issues = []
        doc_control = self.parsed_data.get('document_control', {})
        
        # Check required fields
        required_fields = {
            'company_doc_number': 'Company Document Number',
            'contractor_doc_number': 'Contractor Document Number',
            'rejlers_doc_number': 'Rejlers Document Number',
            'document_title': 'Document Title',
            'revision': 'Revision',
            'doc_status': 'Document Status',
            'doc_purpose': 'Document Purpose',
            'project_name': 'Project Name',
            'project_location': 'Location',
            'agreement_number': 'Agreement Number',
        }
        
        for field_key, field_name in required_fields.items():
            value = doc_control.get(field_key, '').strip()
            
            if not value:
                self.add_issue(
                    sheet_name='Cover Sheet',
                    section='Document Control',
                    item=field_name,
                    severity='error',
                    code='DOC_CTRL_001',
                    message=f'{field_name} is missing or empty',
                    expected='Non-empty value',
                    actual='Empty',
                    rule_name='Required Field Check',
                    category='document_control',
                )
        
        # Check document title matches equipment type
        title = doc_control.get('document_title', '').upper()
        equipment_type = self.parsed_data.get('equipment_type', 'unknown')
        
        title_equipment_map = {
            'ups': ['UPS', 'UNINTERRUPTIBLE POWER'],
            'vfd': ['VFD', 'VARIABLE FREQUENCY DRIVE', 'VSD'],
            'ner': ['NEUTRAL EARTHING RESISTOR', 'NER'],
            'power_cable': ['POWER CABLE'],
            'control_cable': ['CONTROL CABLE'],
            'earthing_cable': ['EARTHING CABLE'],
        }
        
        if equipment_type != 'unknown':
            expected_keywords = title_equipment_map.get(equipment_type, [])
            if expected_keywords and not any(keyword in title for keyword in expected_keywords):
                self.add_issue(
                    sheet_name='Cover Sheet',
                    section='Document Control',
                    item='Document Title',
                    severity='warning',
                    code='DOC_CTRL_002',
                    message=f'Document title may not match equipment type: {equipment_type}',
                    expected=f'Title containing one of: {", ".join(expected_keywords)}',
                    actual=title[:100],
                    rule_name='Document Title Equipment Type Match',
                    category='document_control',
                )
        
        # Check revision history exists
        revision_history = self.parsed_data.get('revision_history', [])
        if not revision_history:
            self.add_issue(
                sheet_name='Revision History',
                section='Revision History',
                item='Revision Entries',
                severity='error',
                code='DOC_CTRL_003',
                message='Revision history sheet exists but contains no revision entries',
                expected='At least one revision entry',
                actual='No entries found',
                rule_name='Revision History Presence',
                category='document_control',
            )
        
        # Check hold sheet
        holds = self.parsed_data.get('holds', [])
        if not holds:
            self.add_issue(
                sheet_name='Holds',
                section='Holds',
                item='Hold Sheet',
                severity='warning',
                code='DOC_CTRL_004',
                message='Hold sheet not found or improperly formatted',
                expected='Hold sheet with either holds or "NIL"',
                actual='Sheet not found or empty',
                rule_name='Hold Sheet Presence',
                category='document_control',
            )
        
        return self.issues


class TechnicalFieldValidator(BaseValidator):
    """Validator for technical field presence and formatting"""
    
    def validate(self) -> List[ValidationIssueDTO]:
        """Validate technical field presence and formats"""
        self.issues = []
        
        # Common required fields across equipment types
        self._check_general_data_fields()
        self._check_environmental_conditions()
        
        # Equipment-specific checks
        equipment_type = self.parsed_data.get('equipment_type', 'unknown')
        
        if equipment_type == 'ups':
            self._validate_ups_fields()
        elif equipment_type == 'vfd':
            self._validate_vfd_fields()
        elif equipment_type in ['power_cable', 'control_cable', 'earthing_cable']:
            self._validate_cable_fields()
        elif equipment_type == 'ner':
            self._validate_ner_fields()
        
        return self.issues
    
    def _check_general_data_fields(self):
        """Check GENERAL DATA section fields"""
        required_fields = [
            ('TAG NO', 'TAG NO.'),
            ('TITLE', 'Equipment Title'),
            ('PROJECT SPECIFICATION', 'Project Specification Reference'),
            ('DESIGN LIFE', 'Design Life'),
            ('CRITICALITY RATING', 'Criticality Rating'),
            ('INSPECTION CLASS', 'Inspection Class'),
            ('MATERIAL CERTIFICATION', 'Material Certification'),
        ]
        
        for pattern, field_name in required_fields:
            value = self.get_field_value('GENERAL DATA', pattern)
            
            if not value or value.strip() == '':
                self.add_issue(
                    sheet_name='Technical Data',
                    section='GENERAL DATA',
                    item=field_name,
                    severity='error',
                    code='TECH_FIELD_001',
                    message=f'{field_name} is missing or blank',
                    expected='Non-empty value',
                    actual='Empty or not found',
                    rule_name='Required Technical Field',
                    category='technical_content',
                )
    
    def _check_environmental_conditions(self):
        """Check ENVIRONMENTAL CONDITIONS section"""
        required_fields = [
            ('TYPE OF INSTALLATION', 'Type of Installation'),
            ('ATMOSPHERE', 'Atmosphere'),
            ('ALTITUDE', 'Altitude'),
            ('AMBIENT.*TEMPERATURE', 'Ambient Temperature'),
            ('HUMIDITY', 'Humidity'),
            ('IP.*PROTECTION', 'IP Degree of Protection'),
            ('SITE CLASS', 'Site Class Definition'),
        ]
        
        for pattern, field_name in required_fields:
            value = self.get_field_value('ENVIRONMENTAL CONDITIONS', pattern)
            
            if not value or value.strip() == '':
                self.add_issue(
                    sheet_name='Technical Data',
                    section='ENVIRONMENTAL CONDITIONS',
                    item=field_name,
                    severity='error',
                    code='TECH_FIELD_002',
                    message=f'{field_name} in ENVIRONMENTAL CONDITIONS is missing',
                    expected='Non-empty value',
                    actual='Empty or not found',
                    rule_name='Environmental Conditions Field',
                    category='technical_content',
                )
    
    def _validate_ups_fields(self):
        """Validate UPS-specific fields"""
        ups_required = [
            ('INPUT.*VOLTAGE', 'Input Voltage', 'UPS INPUT'),
            ('FREQUENCY', 'Frequency', 'UPS INPUT'),
            ('FAULT LEVEL', 'Fault Level', 'UPS INPUT'),
            ('UPS.*RATING', 'UPS Rating', 'UPS RATING'),
            ('BATTERY.*TYPE', 'Battery Type', 'BATTERY'),
            ('NUMBER OF CELLS', 'Number of Cells', 'BATTERY'),
            ('AUTONOMY', 'Battery Autonomy', 'BATTERY'),
        ]
        
        for pattern, field_name, section in ups_required:
            value = self.get_field_value(section, pattern)
            
            if not value or value.strip() == '':
                self.add_issue(
                    sheet_name='Technical Data',
                    section=section,
                    item=field_name,
                    severity='error',
                    code='UPS_FIELD_001',
                    message=f'UPS field "{field_name}" is missing',
                    expected='Non-empty value',
                    actual='Empty or not found',
                    rule_name='UPS Required Field',
                    category='technical_content',
                )
        
        # Check battery type specifically
        battery_type = self.get_field_value('BATTERY', 'BATTERY.*TYPE')
        if battery_type and 'VRLA' not in battery_type.upper() and 'VALVE REGULATED' not in battery_type.upper():
            self.add_issue(
                sheet_name='Technical Data',
                section='BATTERY',
                item='Battery Type',
                severity='warning',
                code='UPS_FIELD_002',
                message='Battery type should be VRLA (Valve Regulated Lead Acid)',
                expected='SEALED MAINTENANCE FREE LEAD ACID (VRLA) or similar',
                actual=battery_type,
                rule_name='UPS Battery Type',
                category='technical_content',
            )
    
    def _validate_vfd_fields(self):
        """Validate VFD-specific fields"""
        vfd_required = [
            ('VSD.*RATING', 'VSD Rating', 'VFD RATING'),
            ('INPUT.*VOLTAGE', 'Input Voltage', 'VFD INPUT'),
            ('OUTPUT.*VOLTAGE', 'Output Voltage', 'VFD OUTPUT'),
            ('HARMONIC', 'Harmonic Levels', 'VFD PERFORMANCE'),
            ('EFFICIENCY', 'Efficiency', 'VFD PERFORMANCE'),
        ]
        
        for pattern, field_name, section in vfd_required:
            value = self.get_field_value(section, pattern)
            
            if not value or value.strip() == '':
                self.add_issue(
                    sheet_name='Technical Data',
                    section=section,
                    item=field_name,
                    severity='error',
                    code='VFD_FIELD_001',
                    message=f'VFD field "{field_name}" is missing',
                    expected='Non-empty value',
                    actual='Empty or not found',
                    rule_name='VFD Required Field',
                    category='technical_content',
                )
    
    def _validate_cable_fields(self):
        """Validate cable-specific fields"""
        cable_required = [
            ('REFERENCE.*SPECIFICATIONS', 'Reference Specifications', 'GENERAL'),
            ('IEC.*STANDARDS?', 'IEC Standards', 'GENERAL'),
            ('CONDUCTOR.*MATERIAL', 'Conductor Material', 'CONSTRUCTION'),
            ('INSULATION.*MATERIAL', 'Insulation Material', 'CONSTRUCTION'),
            ('VOLTAGE.*RATING', 'Voltage Rating', 'ELECTRICAL'),
            ('CURRENT.*RATING', 'Current Rating', 'ELECTRICAL'),
        ]
        
        for pattern, field_name, section in cable_required:
            value = self.get_field_value(section, pattern)
            
            if not value or value.strip() == '':
                self.add_issue(
                    sheet_name='Technical Data',
                    section=section,
                    item=field_name,
                    severity='error',
                    code='CABLE_FIELD_001',
                    message=f'Cable field "{field_name}" is missing',
                    expected='Non-empty value',
                    actual='Empty or not found',
                    rule_name='Cable Required Field',
                    category='technical_content',
                )
        
        # Check for specific standards
        ref_specs = self.get_field_value('GENERAL', 'REFERENCE.*SPECIFICATIONS')
        if ref_specs:
            required_standards = ['BGS-EU-001', 'BGS-EE-011']
            for standard in required_standards:
                if standard not in ref_specs:
                    self.add_issue(
                        sheet_name='Technical Data',
                        section='GENERAL',
                        item='Reference Specifications',
                        severity='warning',
                        code='CABLE_FIELD_002',
                        message=f'Expected standard "{standard}" not found in reference specifications',
                        expected=f'Reference to {standard}',
                        actual=ref_specs[:200],
                        rule_name='Cable Standards Reference',
                        category='standards',
                    )
    
    def _validate_ner_fields(self):
        """Validate NER-specific fields"""
        ner_required = [
            ('SYSTEM.*VOLTAGE', 'System Voltage', 'GENERAL'),
            ('CURRENT.*RATING', 'Current Rating', 'RATINGS'),
            ('TIME.*RATING', 'Time Rating', 'RATINGS'),
            ('RESISTANCE.*VALUE', 'Resistance Value', 'RATINGS'),
            ('RESISTOR.*MATERIAL', 'Resistor Material', 'CONSTRUCTION'),
        ]
        
        for pattern, field_name, section in ner_required:
            value = self.get_field_value(section, pattern)
            
            if not value or value.strip() == '':
                self.add_issue(
                    sheet_name='Technical Data',
                    section=section,
                    item=field_name,
                    severity='error',
                    code='NER_FIELD_001',
                    message=f'NER field "{field_name}" is missing',
                    expected='Non-empty value',
                    actual='Empty or not found',
                    rule_name='NER Required Field',
                    category='technical_content',
                )
        
        # Check for IEEE standards
        standards_value = self.get_field_value('GENERAL', 'STANDARDS?')
        if standards_value:
            required_standards = ['IEEE C57.32', 'IEEE-142']
            for standard in required_standards:
                if standard not in standards_value:
                    self.add_issue(
                        sheet_name='Technical Data',
                        section='GENERAL',
                        item='Standards',
                        severity='warning',
                        code='NER_FIELD_002',
                        message=f'Expected standard "{standard}" not found',
                        expected=f'Reference to {standard}',
                        actual=standards_value[:200],
                        rule_name='NER Standards Reference',
                        category='standards',
                    )


class ValueRangeValidator(BaseValidator):
    """Validator for value ranges and format checks"""
    
    def validate(self) -> List[ValidationIssueDTO]:
        """Validate value ranges"""
        self.issues = []
        
        self._validate_voltage_values()
        self._validate_frequency()
        self._validate_environmental_values()
        self._validate_criticality_inspection()
        
        return self.issues
    
    def _extract_numeric(self, value: str) -> Optional[float]:
        """Extract numeric value from string"""
        if not value:
            return None
        
        # Remove common units and extract number
        value_clean = re.sub(r'[^\d.\-+]', '', str(value))
        
        try:
            return float(value_clean)
        except (ValueError, InvalidOperation):
            return None
    
    def _validate_voltage_values(self):
        """Validate voltage values are within expected ranges"""
        voltage_patterns = [
            ('VOLTAGE', 'GENERAL'),
            ('NOMINAL.*VOLTAGE', 'GENERAL'),
            ('INPUT.*VOLTAGE', 'INPUT'),
            ('OUTPUT.*VOLTAGE', 'OUTPUT'),
        ]
        
        for pattern, section in voltage_patterns:
            value = self.get_field_value(section, pattern)
            
            if value:
                numeric = self._extract_numeric(value)
                
                if numeric is None:
                    self.add_issue(
                        sheet_name='Technical Data',
                        section=section,
                        item='Voltage',
                        severity='error',
                        code='VAL_RANGE_001',
                        message='Voltage value is not numeric',
                        expected='Numeric value',
                        actual=value,
                        rule_name='Voltage Format Check',
                        category='technical_content',
                    )
                elif numeric > 0:
                    # Check if LV (<=1000V)
                    if numeric <= 1000:
                        # LV equipment: typically 415V ±10%
                        if numeric < 380 or numeric > 450:
                            if not (numeric in [220, 230, 240, 380, 400, 415, 440]):
                                self.add_issue(
                                    sheet_name='Technical Data',
                                    section=section,
                                    item='Voltage',
                                    severity='warning',
                                    code='VAL_RANGE_002',
                                    message='LV voltage value outside typical range',
                                    expected='Typically 415V ±10% or standard voltages (220, 380, 400, 415, 440)',
                                    actual=value,
                                    rule_name='LV Voltage Range',
                                    category='consistency',
                                )
    
    def _validate_frequency(self):
        """Validate frequency is 50 Hz ±2%"""
        frequency = self.get_field_value('GENERAL', 'FREQUENCY')
        
        if frequency:
            numeric = self._extract_numeric(frequency)
            
            if numeric is None:
                self.add_issue(
                    sheet_name='Technical Data',
                    section='GENERAL',
                    item='Frequency',
                    severity='error',
                    code='VAL_RANGE_003',
                    message='Frequency value is not numeric',
                    expected='50 or 60',
                    actual=frequency,
                    rule_name='Frequency Format Check',
                    category='technical_content',
                )
            elif numeric < 49 or numeric > 61:
                self.add_issue(
                    sheet_name='Technical Data',
                    section='GENERAL',
                    item='Frequency',
                    severity='error',
                    code='VAL_RANGE_004',
                    message='Frequency value outside acceptable range',
                    expected='50 Hz ±2% (49-51 Hz) or 60 Hz',
                    actual=frequency,
                    rule_name='Frequency Range Check',
                    category='consistency',
                )
    
    def _validate_environmental_values(self):
        """Validate environmental condition values"""
        # Ambient temperature
        temp = self.get_field_value('ENVIRONMENTAL CONDITIONS', 'AMBIENT.*TEMPERATURE')
        if temp:
            # Extract min and max if range
            numbers = re.findall(r'-?\d+\.?\d*', temp)
            if numbers:
                for num_str in numbers:
                    num = float(num_str)
                    if num < -50 or num > 70:
                        self.add_issue(
                            sheet_name='Technical Data',
                            section='ENVIRONMENTAL CONDITIONS',
                            item='Ambient Temperature',
                            severity='warning',
                            code='VAL_RANGE_005',
                            message='Ambient temperature outside typical engineering range',
                            expected='Typically -20°C to +60°C',
                            actual=temp,
                            rule_name='Ambient Temperature Range',
                            category='consistency',
                        )
        
        # Humidity
        humidity = self.get_field_value('ENVIRONMENTAL CONDITIONS', 'HUMIDITY')
        if humidity:
            numeric = self._extract_numeric(humidity)
            if numeric and (numeric < 0 or numeric > 100):
                self.add_issue(
                    sheet_name='Technical Data',
                    section='ENVIRONMENTAL CONDITIONS',
                    item='Humidity',
                    severity='error',
                    code='VAL_RANGE_006',
                    message='Humidity value outside valid range',
                    expected='0-100%',
                    actual=humidity,
                    rule_name='Humidity Range Check',
                    category='consistency',
                )
        
        # Altitude
        altitude = self.get_field_value('ENVIRONMENTAL CONDITIONS', 'ALTITUDE')
        if altitude:
            numeric = self._extract_numeric(altitude)
            if numeric and numeric > 3000:
                self.add_issue(
                    sheet_name='Technical Data',
                    section='ENVIRONMENTAL CONDITIONS',
                    item='Altitude',
                    severity='warning',
                    code='VAL_RANGE_007',
                    message='Altitude value unusually high',
                    expected='Typically 0-2000m',
                    actual=altitude,
                    rule_name='Altitude Range Check',
                    category='consistency',
                )
    
    def _validate_criticality_inspection(self):
        """Validate criticality rating and inspection class"""
        criticality = self.get_field_value('GENERAL DATA', 'CRITICALITY RATING')
        inspection = self.get_field_value('GENERAL DATA', 'INSPECTION CLASS')
        
        if criticality:
            crit_num = self._extract_numeric(criticality)
            if crit_num and (crit_num < 2 or crit_num > 4):
                self.add_issue(
                    sheet_name='Technical Data',
                    section='GENERAL DATA',
                    item='Criticality Rating',
                    severity='warning',
                    code='VAL_RANGE_008',
                    message='Criticality Rating outside standard range',
                    expected='2, 3, or 4',
                    actual=criticality,
                    rule_name='Criticality Rating Range',
                    category='consistency',
                )
        
        if inspection:
            insp_num = self._extract_numeric(inspection)
            if insp_num and (insp_num < 2 or insp_num > 4):
                self.add_issue(
                    sheet_name='Technical Data',
                    section='GENERAL DATA',
                    item='Inspection Class',
                    severity='warning',
                    code='VAL_RANGE_009',
                    message='Inspection Class outside standard range',
                    expected='2, 3, or 4',
                    actual=inspection,
                    rule_name='Inspection Class Range',
                    category='consistency',
                )


class CrossFieldConsistencyValidator(BaseValidator):
    """Validator for cross-field consistency checks"""
    
    def validate(self) -> List[ValidationIssueDTO]:
        """Validate consistency across related fields"""
        self.issues = []
        
        self._check_criticality_inspection_consistency()
        
        equipment_type = self.parsed_data.get('equipment_type', 'unknown')
        
        if equipment_type == 'ups':
            self._check_ups_consistency()
        elif equipment_type in ['power_cable', 'control_cable', 'earthing_cable']:
            self._check_cable_consistency()
        elif equipment_type == 'ner':
            self._check_ner_consistency()
        
        return self.issues
    
    def _check_criticality_inspection_consistency(self):
        """Check that criticality rating matches inspection class"""
        criticality = self.get_field_value('GENERAL DATA', 'CRITICALITY RATING')
        inspection = self.get_field_value('GENERAL DATA', 'INSPECTION CLASS')
        material_cert = self.get_field_value('GENERAL DATA', 'MATERIAL CERTIFICATION')
        
        # TODO: Add project-specific rules for criticality/inspection/material cert alignment
        # For now, just check they are present together
        if criticality and not inspection:
            self.add_issue(
                sheet_name='Technical Data',
                section='GENERAL DATA',
                item='Inspection Class',
                severity='warning',
                code='CONSISTENCY_001',
                message='Criticality Rating is specified but Inspection Class is missing',
                expected='Inspection Class value',
                actual='Empty',
                rule_name='Criticality-Inspection Consistency',
                category='consistency',
            )
    
    def _check_ups_consistency(self):
        """Check UPS-specific consistency"""
        # Battery autonomy time should be present
        autonomy = self.get_field_value('BATTERY', 'AUTONOMY')
        if not autonomy:
            self.add_issue(
                sheet_name='Technical Data',
                section='BATTERY',
                item='Battery Autonomy',
                severity='error',
                code='UPS_CONSISTENCY_001',
                message='Battery autonomy time is required for UPS',
                expected='Autonomy time (e.g., 30 minutes)',
                actual='Empty or not found',
                rule_name='UPS Battery Autonomy',
                category='consistency',
            )
        
        # DC voltage values should be filled
        dc_fields = ['FLOAT.*VOLTAGE', 'BOOST.*VOLTAGE', 'EQUALIZING.*VOLTAGE']
        for pattern in dc_fields:
            value = self.get_field_value('BATTERY', pattern)
            if not value:
                self.add_issue(
                    sheet_name='Technical Data',
                    section='BATTERY',
                    item=pattern.replace('.*', ' '),
                    severity='warning',
                    code='UPS_CONSISTENCY_002',
                    message=f'DC voltage field "{pattern}" should be specified',
                    expected='Voltage value',
                    actual='Empty',
                    rule_name='UPS DC Voltage Completeness',
                    category='consistency',
                )
    
    def _check_cable_consistency(self):
        """Check cable-specific consistency"""
        # If LSZH is specified, check marking mentions LSZH
        insulation = self.get_field_value('CONSTRUCTION', 'INSULATION')
        sheath = self.get_field_value('CONSTRUCTION', 'SHEATH')
        marking = self.get_field_value('CONSTRUCTION', 'MARKING')
        
        if insulation and 'LSZH' in insulation.upper():
            if marking and 'LSZH' not in marking.upper():
                self.add_issue(
                    sheet_name='Technical Data',
                    section='CONSTRUCTION',
                    item='Cable Marking',
                    severity='warning',
                    code='CABLE_CONSISTENCY_001',
                    message='LSZH insulation specified but marking does not mention LSZH',
                    expected='Marking to include "LSZH"',
                    actual=marking[:200],
                    rule_name='LSZH Marking Consistency',
                    category='consistency',
                )
        
        # XLPE insulation check
        if insulation and 'XLPE' in insulation.upper():
            if marking and 'XLPE' not in marking.upper():
                self.add_issue(
                    sheet_name='Technical Data',
                    section='CONSTRUCTION',
                    item='Cable Marking',
                    severity='info',
                    code='CABLE_CONSISTENCY_002',
                    message='XLPE insulation specified but marking does not mention XLPE',
                    expected='Marking to include "XLPE"',
                    actual=marking[:200],
                    rule_name='XLPE Marking Consistency',
                    category='consistency',
                )
    
    def _check_ner_consistency(self):
        """Check NER-specific consistency"""
        # Check time rating and current rating are both present
        time_rating = self.get_field_value('RATINGS', 'TIME.*RATING')
        current_rating = self.get_field_value('RATINGS', 'CURRENT.*RATING')
        
        if time_rating and not current_rating:
            self.add_issue(
                sheet_name='Technical Data',
                section='RATINGS',
                item='Current Rating',
                severity='error',
                code='NER_CONSISTENCY_001',
                message='Time rating specified but current rating is missing',
                expected='Current rating (e.g., 400 A)',
                actual='Empty',
                rule_name='NER Rating Completeness',
                category='consistency',
            )
        
        if current_rating and not time_rating:
            self.add_issue(
                sheet_name='Technical Data',
                section='RATINGS',
                item='Time Rating',
                severity='error',
                code='NER_CONSISTENCY_002',
                message='Current rating specified but time rating is missing',
                expected='Time rating (e.g., 10 s)',
                actual='Empty',
                rule_name='NER Rating Completeness',
                category='consistency',
            )


class ValidationEngine:
    """Main validation engine that coordinates all validators"""
    
    def __init__(self, parsed_data: Dict[str, Any]):
        self.parsed_data = parsed_data
        self.all_issues: List[ValidationIssueDTO] = []
    
    def run_validation(self) -> List[ValidationIssueDTO]:
        """
        Run all validators and aggregate issues
        
        Returns:
            List of all validation issues
        """
        validators = [
            DocumentControlValidator(self.parsed_data),
            TechnicalFieldValidator(self.parsed_data),
            ValueRangeValidator(self.parsed_data),
            CrossFieldConsistencyValidator(self.parsed_data),
        ]
        
        for validator in validators:
            try:
                issues = validator.validate()
                self.all_issues.extend(issues)
            except Exception as e:
                logger.error(f"Error running validator {validator.__class__.__name__}: {str(e)}")
        
        return self.all_issues
    
    def get_summary(self) -> Dict[str, Any]:
        """
        Get validation summary statistics
        
        Returns:
            Dictionary with count of errors, warnings, info
        """
        error_count = sum(1 for issue in self.all_issues if issue.severity == 'error')
        warning_count = sum(1 for issue in self.all_issues if issue.severity == 'warning')
        info_count = sum(1 for issue in self.all_issues if issue.severity == 'info')
        
        # Calculate validation score (100 - deductions)
        score = 100.0
        score -= error_count * 5.0  # Each error deducts 5 points
        score -= warning_count * 2.0  # Each warning deducts 2 points
        score -= info_count * 0.5  # Each info deducts 0.5 points
        score = max(0.0, score)  # Floor at 0
        
        return {
            'total_issues': len(self.all_issues),
            'error_count': error_count,
            'warning_count': warning_count,
            'info_count': info_count,
            'validation_score': round(score, 2),
            'status': 'passed' if error_count == 0 else 'failed',
        }
