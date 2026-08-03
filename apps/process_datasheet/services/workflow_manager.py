"""
Workflow Manager
Soft-coded workflow orchestration for datasheet processing
"""
import logging
from typing import Dict, Any, List
from django.conf import settings

from .ai_agents import (
    DocumentAnalyzerAgent,
    FieldExtractorAgent,
    ValidationAgent,
    QualityCheckerAgent
)
from .ai_agents.base_agent import AgentOrchestrator
from .calculation_service import CalculationService
from .validation_service import ValidationService

logger = logging.getLogger(__name__)


# Soft-coded workflow configurations
WORKFLOW_CONFIGS = {
    'pdf_extraction_complete': {
        'name': 'Complete PDF Extraction Workflow',
        'description': 'Full pipeline: Analyze → Extract → Validate → Quality Check',
        'steps': [
            {
                'agent': 'document_analyzer',
                'action': 'analyze',
                'config': {
                    'extract_metadata': True,
                    'assess_quality': True
                }
            },
            {
                'agent': 'field_extractor',
                'action': 'extract',
                'config': {
                    'mode': 'precise',
                    'verify_extractions': True
                }
            },
            {
                'agent': 'validation_agent',
                'action': 'validate',
                'config': {
                    'validation_type': 'all'
                }
            },
            {
                'agent': 'quality_checker',
                'action': 'check_quality',
                'config': {
                    'generate_checklist': True
                }
            }
        ],
        'on_error': 'continue',
        'parallel_steps': []  # Steps that can run in parallel
    },
    
    'quick_extraction': {
        'name': 'Quick Extraction Workflow',
        'description': 'Fast extraction without deep validation',
        'steps': [
            {
                'agent': 'field_extractor',
                'action': 'extract',
                'config': {
                    'mode': 'flexible'
                }
            }
        ],
        'on_error': 'stop'
    },
    
    'validation_only': {
        'name': 'Validation Only Workflow',
        'description': 'Validate existing datasheet without extraction',
        'steps': [
            {
                'agent': 'validation_agent',
                'action': 'validate',
                'config': {
                    'validation_type': 'all'
                }
            },
            {
                'agent': 'quality_checker',
                'action': 'check_quality',
                'config': {}
            }
        ],
        'on_error': 'stop'
    },
    
    'quality_assurance': {
        'name': 'Quality Assurance Workflow',
        'description': 'Final QA before approval',
        'steps': [
            {
                'agent': 'validation_agent',
                'action': 'validate_technical'
            },
            {
                'agent': 'validation_agent',
                'action': 'validate_safety'
            },
            {
                'agent': 'quality_checker',
                'action': 'verify_completeness'
            },
            {
                'agent': 'quality_checker',
                'action': 'assess_readiness'
            }
        ],
        'on_error': 'continue'
    }
}


class WorkflowManager:
    """
    Manages soft-coded workflows for datasheet processing
    All workflows are configuration-driven, not hardcoded
    """
    
    def __init__(self):
        """Initialize workflow manager with orchestrator and agents"""
        self.orchestrator = AgentOrchestrator()
        
        # Register agents
        self._register_agents()
        
        # Register workflows
        self._register_workflows()
        
        logger.info("Workflow Manager initialized")
    
    def _register_agents(self):
        """Register all available agents"""
        try:
            self.orchestrator.register_agent('document_analyzer', DocumentAnalyzerAgent())
            self.orchestrator.register_agent('field_extractor', FieldExtractorAgent())
            self.orchestrator.register_agent('validation_agent', ValidationAgent())
            self.orchestrator.register_agent('quality_checker', QualityCheckerAgent())
            
            logger.info("All agents registered successfully")
        except Exception as e:
            logger.error(f"Failed to register agents: {str(e)}")
    
    def _register_workflows(self):
        """Register all workflows from configuration"""
        for workflow_id, workflow_config in WORKFLOW_CONFIGS.items():
            self.orchestrator.register_workflow(workflow_id, workflow_config)
        
        logger.info(f"Registered {len(WORKFLOW_CONFIGS)} workflows")
    
    def execute_workflow(self, workflow_id: str, data: Dict) -> Dict[str, Any]:
        """
        Execute a workflow
        
        Args:
            workflow_id: Workflow identifier
            data: Input data for workflow
            
        Returns:
            Workflow execution results
        """
        logger.info(f"Executing workflow: {workflow_id}")
        
        try:
            result = self.orchestrator.execute_workflow(workflow_id, data)
            
            logger.info(f"Workflow {workflow_id} completed: Success={result['success']}")
            
            return result
            
        except Exception as e:
            logger.error(f"Workflow execution failed: {str(e)}")
            return {
                'workflow_id': workflow_id,
                'success': False,
                'error': str(e)
            }
    
    def extract_from_pdf(self, pdf_path: str, equipment_type, workflow: str = 'pdf_extraction_complete') -> Dict:
        """
        Extract datasheet from PDF using specified workflow
        
        Args:
            pdf_path: Path to PDF file
            equipment_type: EquipmentType model instance
            workflow: Workflow ID to use
            
        Returns:
            Extraction results
        """
        # Read PDF content
        try:
            import pdfplumber
            with pdfplumber.open(pdf_path) as pdf:
                document_text = ""
                for page in pdf.pages:
                    document_text += page.extract_text() or ""
        except Exception as e:
            return {
                'success': False,
                'error': f'Failed to read PDF: {str(e)}'
            }
        
        # Prepare workflow data
        workflow_data = {
            'document_text': document_text,
            'equipment_type': equipment_type.name,
            'equipment_config': equipment_type.configuration,
            'fields': self._get_all_fields(equipment_type.configuration)
        }
        
        # Execute workflow
        result = self.execute_workflow(workflow, workflow_data)
        
        if result['success']:
            # Add calculations
            if 'extracted_fields' in result.get('data', {}):
                datasheet_data = self._format_extracted_data(
                    result['data']['extracted_fields'],
                    equipment_type.configuration
                )
                
                # Run calculations
                calculated_values = CalculationService.calculate_all(
                    datasheet_data,
                    equipment_type.configuration
                )
                
                result['data']['calculated_values'] = calculated_values
                result['data']['datasheet_data'] = datasheet_data
        
        return result
    
    def validate_datasheet(self, datasheet_data: Dict, equipment_type,
                          workflow: str = 'validation_only') -> Dict:
        """
        Validate datasheet using specified workflow
        
        Args:
            datasheet_data: Datasheet data to validate
            equipment_type: EquipmentType model instance
            workflow: Workflow ID to use
            
        Returns:
            Validation results
        """
        # Run rule-based validation first
        validation_results = ValidationService.validate_all(
            datasheet_data,
            equipment_type.configuration
        )
        
        # Run calculations
        calculated_values = CalculationService.calculate_all(
            datasheet_data,
            equipment_type.configuration
        )
        
        # Prepare workflow data
        workflow_data = {
            'datasheet_data': datasheet_data,
            'equipment_type': equipment_type.name,
            'equipment_config': equipment_type.configuration,
            'validation_results': validation_results,
            'calculated_values': calculated_values
        }
        
        # Execute AI validation workflow
        result = self.execute_workflow(workflow, workflow_data)
        
        return result
    
    def quality_check(self, datasheet_data: Dict, equipment_type,
                     validation_results: Dict, calculated_values: Dict) -> Dict:
        """
        Perform quality assurance check
        
        Args:
            datasheet_data: Datasheet data
            equipment_type: EquipmentType model instance
            validation_results: Previous validation results
            calculated_values: Calculated values
            
        Returns:
            Quality assessment results
        """
        workflow_data = {
            'datasheet_data': datasheet_data,
            'equipment_type': equipment_type.name,
            'equipment_config': equipment_type.configuration,
            'validation_results': validation_results,
            'calculated_values': calculated_values
        }
        
        result = self.execute_workflow('quality_assurance', workflow_data)
        
        return result
    
    def _get_all_fields(self, equipment_config: Dict) -> List[Dict]:
        """Extract all fields from equipment configuration"""
        fields = []
        for section in equipment_config.get('sections', []):
            fields.extend(section.get('fields', []))
        return fields
    
    def _format_extracted_data(self, extracted_fields: Dict,
                               equipment_config: Dict) -> Dict:
        """Format extracted fields into datasheet structure"""
        datasheet_data = {}
        
        # Organize by section
        for section in equipment_config.get('sections', []):
            section_id = section['id']
            datasheet_data[section_id] = {}
            
            for field in section.get('fields', []):
                field_id = field['id']
                if field_id in extracted_fields:
                    datasheet_data[section_id][field_id] = extracted_fields[field_id]
        
        return datasheet_data
    
    def list_workflows(self) -> List[Dict]:
        """List all available workflows"""
        return [
            {
                'id': workflow_id,
                'name': config['name'],
                'description': config['description'],
                'steps': len(config['steps'])
            }
            for workflow_id, config in WORKFLOW_CONFIGS.items()
        ]
    
    def get_workflow_config(self, workflow_id: str) -> Dict:
        """Get configuration for a specific workflow"""
        return WORKFLOW_CONFIGS.get(workflow_id)
    
    def add_custom_workflow(self, workflow_id: str, workflow_config: Dict):
        """
        Add a custom workflow at runtime
        
        Args:
            workflow_id: Unique workflow identifier
            workflow_config: Workflow configuration
        """
        WORKFLOW_CONFIGS[workflow_id] = workflow_config
        self.orchestrator.register_workflow(workflow_id, workflow_config)
        logger.info(f"Added custom workflow: {workflow_id}")


# Global workflow manager instance
_workflow_manager = None


def get_workflow_manager() -> WorkflowManager:
    """Get or create global workflow manager instance"""
    global _workflow_manager
    if _workflow_manager is None:
        _workflow_manager = WorkflowManager()
    return _workflow_manager
