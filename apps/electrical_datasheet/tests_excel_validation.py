"""
Unit Tests for Excel Validation Framework
Tests for parsers, validators, and quality checks
"""

import pytest
from django.test import TestCase
from decimal import Decimal
from backend.apps.electrical_datasheet.excel_validation_framework import (
    ValidationIssueDTO,
    DocumentControlValidator,
    TechnicalFieldValidator,
    ValueRangeValidator,
    CrossFieldConsistencyValidator,
    ValidationEngine,
)


class TestDocumentControlValidator(TestCase):
    """Test document control validation"""
    
    def setUp(self):
        self.sample_data = {
            'document_control': {
                'company_doc_number': 'DS-13-574-EC-7004',
                'contractor_doc_number': 'ABC-001',
                'rejlers_doc_number': 'REJ-001',
                'document_title': 'TECHNICAL DATA SHEET FOR NEUTRAL EARTHING RESISTOR',
                'revision': 'A',
                'doc_status': 'APPROVED FOR ENGINEERING',
                'doc_purpose': 'FOR APPROVAL',
                'project_name': 'Borouge EU3 H2 Extraction Unit',
                'project_location': 'Ruwais',
                'agreement_number': '5900863',
            },
            'equipment_type': 'ner',
            'revision_history': [
                {'revision': 'A', 'date': '2024-01-01', 'description': 'ISSUED FOR REVIEW'}
            ],
            'holds': [{'status': 'NIL'}],
        }
    
    def test_all_required_fields_present(self):
        """Test validation passes when all required fields are present"""
        validator = DocumentControlValidator(self.sample_data)
        issues = validator.validate()
        
        # Should have no errors for missing fields
        missing_field_errors = [
            issue for issue in issues 
            if issue.code == 'DOC_CTRL_001'
        ]
        self.assertEqual(len(missing_field_errors), 0)
    
    def test_missing_company_doc_number(self):
        """Test validation fails when company doc number is missing"""
        data = self.sample_data.copy()
        data['document_control']['company_doc_number'] = ''
        
        validator = DocumentControlValidator(data)
        issues = validator.validate()
        
        # Should have error for missing company doc number
        company_doc_errors = [
            issue for issue in issues 
            if issue.code == 'DOC_CTRL_001' and 'Company Document Number' in issue.message
        ]
        self.assertGreater(len(company_doc_errors), 0)
        self.assertEqual(company_doc_errors[0].severity, 'error')
    
    def test_document_title_matches_equipment_type(self):
        """Test document title matches detected equipment type"""
        validator = DocumentControlValidator(self.sample_data)
        issues = validator.validate()
        
        # Should have no warnings about title mismatch
        title_warnings = [
            issue for issue in issues 
            if issue.code == 'DOC_CTRL_002'
        ]
        self.assertEqual(len(title_warnings), 0)
    
    def test_revision_history_present(self):
        """Test validation checks for revision history"""
        validator = DocumentControlValidator(self.sample_data)
        issues = validator.validate()
        
        # Should have no errors for revision history
        revision_errors = [
            issue for issue in issues 
            if issue.code == 'DOC_CTRL_003'
        ]
        self.assertEqual(len(revision_errors), 0)


class TestValueRangeValidator(TestCase):
    """Test value range validation"""
    
    def setUp(self):
        self.sample_data = {
            'equipment_type': 'ups',
            'technical_data': {
                'Technical Data Sheet': {
                    'sections': {
                        'GENERAL': [
                            {
                                'description': 'FREQUENCY',
                                'specified_design_data': '50',
                                'vendor_data': '',
                                'section': 'GENERAL'
                            },
                            {
                                'description': 'NOMINAL VOLTAGE',
                                'specified_design_data': '415',
                                'vendor_data': '',
                                'section': 'GENERAL'
                            }
                        ],
                        'ENVIRONMENTAL CONDITIONS': [
                            {
                                'description': 'AMBIENT TEMPERATURE',
                                'specified_design_data': '-10 to +50',
                                'vendor_data': '',
                                'section': 'ENVIRONMENTAL CONDITIONS'
                            },
                            {
                                'description': 'HUMIDITY',
                                'specified_design_data': '85',
                                'vendor_data': '',
                                'section': 'ENVIRONMENTAL CONDITIONS'
                            }
                        ]
                    }
                }
            }
        }
    
    def test_frequency_validation(self):
        """Test frequency value is validated correctly"""
        validator = ValueRangeValidator(self.sample_data)
        issues = validator.validate()
        
        # 50 Hz should be valid
        frequency_errors = [
            issue for issue in issues 
            if 'Frequency' in issue.item and issue.severity == 'error'
        ]
        self.assertEqual(len(frequency_errors), 0)
    
    def test_voltage_in_range(self):
        """Test voltage values are validated"""
        validator = ValueRangeValidator(self.sample_data)
        issues = validator.validate()
        
        # 415V should be valid for LV equipment
        voltage_errors = [
            issue for issue in issues 
            if 'Voltage' in issue.item and issue.code == 'VAL_RANGE_002'
        ]
        self.assertEqual(len(voltage_errors), 0)
    
    def test_invalid_frequency(self):
        """Test invalid frequency value is caught"""
        data = self.sample_data.copy()
        data['technical_data']['Technical Data Sheet']['sections']['GENERAL'][0]['specified_design_data'] = '70'
        
        validator = ValueRangeValidator(data)
        issues = validator.validate()
        
        # 70 Hz should be invalid
        frequency_errors = [
            issue for issue in issues 
            if 'Frequency' in issue.item and issue.severity == 'error'
        ]
        self.assertGreater(len(frequency_errors), 0)


class TestValidationEngine(TestCase):
    """Test validation engine coordination"""
    
    def setUp(self):
        self.complete_data = {
            'equipment_type': 'ner',
            'document_control': {
                'company_doc_number': 'DS-13-574-ER-701',
                'contractor_doc_number': 'ABC-001',
                'rejlers_doc_number': 'REJ-001',
                'document_title': 'TECHNICAL DATA SHEET FOR NEUTRAL EARTHING RESISTOR',
                'revision': 'A',
                'doc_status': 'APPROVED',
                'doc_purpose': 'FOR APPROVAL',
                'project_name': 'Test Project',
                'project_location': 'Ruwais',
                'agreement_number': '123456',
            },
            'revision_history': [
                {'revision': 'A', 'date': '2024-01-01', 'description': 'ISSUED'}
            ],
            'holds': [{'status': 'NIL'}],
            'technical_data': {
                'Technical Data Sheet': {
                    'sections': {
                        'GENERAL DATA': [
                            {'description': 'TAG NO.', 'specified_design_data': 'NER-001', 'section': 'GENERAL DATA'},
                            {'description': 'TITLE', 'specified_design_data': 'NER', 'section': 'GENERAL DATA'},
                            {'description': 'CRITICALITY RATING', 'specified_design_data': '3', 'section': 'GENERAL DATA'},
                        ],
                        'ENVIRONMENTAL CONDITIONS': [
                            {'description': 'AMBIENT TEMPERATURE', 'specified_design_data': '-10 to +50', 'section': 'ENVIRONMENTAL CONDITIONS'},
                        ]
                    }
                }
            }
        }
    
    def test_validation_engine_runs_all_validators(self):
        """Test that validation engine runs all validators"""
        engine = ValidationEngine(self.complete_data)
        issues = engine.run_validation()
        
        # Should return a list of issues
        self.assertIsInstance(issues, list)
    
    def test_validation_summary(self):
        """Test validation summary generation"""
        engine = ValidationEngine(self.complete_data)
        engine.run_validation()
        summary = engine.get_summary()
        
        # Should have summary keys
        self.assertIn('total_issues', summary)
        self.assertIn('error_count', summary)
        self.assertIn('warning_count', summary)
        self.assertIn('info_count', summary)
        self.assertIn('validation_score', summary)
        self.assertIn('status', summary)
        
        # Validation score should be between 0 and 100
        self.assertGreaterEqual(summary['validation_score'], 0)
        self.assertLessEqual(summary['validation_score'], 100)
    
    def test_score_calculation(self):
        """Test validation score is calculated correctly"""
        # Create data with known issues
        data_with_issues = self.complete_data.copy()
        data_with_issues['document_control']['company_doc_number'] = ''  # This will create an error
        
        engine = ValidationEngine(data_with_issues)
        engine.run_validation()
        summary = engine.get_summary()
        
        # Score should be less than 100 due to error
        self.assertLess(summary['validation_score'], 100)
        
        # Should have at least one error
        self.assertGreater(summary['error_count'], 0)


class TestValidationIssueDTO(TestCase):
    """Test ValidationIssueDTO"""
    
    def test_issue_dto_creation(self):
        """Test creating a validation issue DTO"""
        issue = ValidationIssueDTO(
            sheet_name='Cover Sheet',
            section='Document Control',
            item='Company Document Number',
            severity='error',
            code='DOC_CTRL_001',
            message='Company Document Number is missing',
            expected='Non-empty value',
            actual='Empty',
            rule_name='Required Field Check',
            category='document_control'
        )
        
        self.assertEqual(issue.severity, 'error')
        self.assertEqual(issue.code, 'DOC_CTRL_001')
        self.assertEqual(issue.category, 'document_control')
    
    def test_issue_dto_to_dict(self):
        """Test converting issue DTO to dictionary"""
        issue = ValidationIssueDTO(
            sheet_name='Technical Data',
            section='GENERAL',
            item='Frequency',
            severity='warning',
            code='VAL_RANGE_001',
            message='Test message'
        )
        
        issue_dict = issue.to_dict()
        
        self.assertIsInstance(issue_dict, dict)
        self.assertEqual(issue_dict['severity'], 'warning')
        self.assertEqual(issue_dict['code'], 'VAL_RANGE_001')


# Run tests
if __name__ == '__main__':
    pytest.main([__file__, '-v'])
