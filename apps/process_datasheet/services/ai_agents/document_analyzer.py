"""
Document Analyzer Agent
Analyzes PDF documents and identifies structure and content
"""
from typing import Dict, Any
from .base_agent import BaseAgent


class DocumentAnalyzerAgent(BaseAgent):
    """
    Agent specialized in analyzing document structure and content
    Soft-coded configuration for different document types
    """
    
    AGENT_CONFIG = {
        'name': 'Document Analyzer',
        'description': 'Analyzes document structure, layout, and content organization',
        'model': 'gpt-4o',
        'temperature': 0.1,
        'max_tokens': 3000,
        'system_prompt': """You are an expert technical document analyzer specializing in engineering datasheets.

Your responsibilities:
1. Identify document type and standard (ADNOC, API, ASME, etc.)
2. Analyze document structure (sections, tables, diagrams)
3. Detect key information areas (equipment data, operating conditions, materials)
4. Assess document quality and completeness
5. Extract metadata (document number, revision, date, project)

Provide detailed, structured analysis with confidence scores.""",
        'capabilities': [
            'document_classification',
            'structure_analysis',
            'metadata_extraction',
            'quality_assessment',
            'standard_identification'
        ],
        'confidence_thresholds': {
            'document_type': 0.9,
            'structure': 0.8,
            'metadata': 0.85
        }
    }
    
    def execute(self, task: Dict[str, Any]) -> Dict[str, Any]:
        """
        Execute document analysis task
        
        Args:
            task: {
                'type': 'analyze',
                'data': {
                    'document_text': str,
                    'equipment_type': str,
                    'expected_standard': str (optional)
                }
            }
            
        Returns:
            Analysis results with structure and metadata
        """
        task_type = task.get('type')
        
        if task_type == 'analyze':
            return self._analyze_document(task['data'])
        elif task_type == 'extract_metadata':
            return self._extract_metadata(task['data'])
        elif task_type == 'assess_quality':
            return self._assess_quality(task['data'])
        else:
            return {
                'success': False,
                'error': f'Unknown task type: {task_type}'
            }
    
    def _analyze_document(self, data: Dict) -> Dict[str, Any]:
        """Perform complete document analysis"""
        try:
            document_text = data.get('document_text', '')
            equipment_type = data.get('equipment_type', 'unknown')
            
            prompt = f"""Analyze this technical datasheet for {equipment_type}:

{document_text[:5000]}  # First 5000 chars

Provide analysis of:
1. Document Type (Process Datasheet, Specification Sheet, etc.)
2. Applicable Standard (ADNOC DEP, API, ASME, etc.)
3. Document Structure (sections identified)
4. Key Information Areas (locations of critical data)
5. Completeness Assessment (what's present, what's missing)
6. Data Organization Quality (tables, clarity, formatting)

Consider:
- Is this a complete datasheet or draft?
- Are all required sections present?
- Is the data well-organized?
- What standards are referenced?"""
            
            schema = {
                'document_type': 'str',
                'standard': 'str',
                'revision': 'str',
                'structure': {
                    'sections': ['str'],
                    'has_tables': 'bool',
                    'has_diagrams': 'bool'
                },
                'key_areas': {
                    'equipment_identification': 'str',
                    'operating_conditions': 'str',
                    'materials': 'str',
                    'dimensions': 'str'
                },
                'completeness': {
                    'percentage': 'float',
                    'missing_sections': ['str']
                },
                'quality_score': 'float',
                'confidence': 'float'
            }
            
            result = self.call_llm_structured(prompt, schema)
            
            self.log_execution(
                {'type': 'analyze', 'equipment': equipment_type},
                {'success': True, 'confidence': result.get('confidence', 0.0)}
            )
            
            return {
                'success': True,
                'data': {
                    'analysis': result,
                    'document_type': result.get('document_type'),
                    'standard': result.get('standard')
                }
            }
            
        except Exception as e:
            return {
                'success': False,
                'error': str(e)
            }
    
    def _extract_metadata(self, data: Dict) -> Dict[str, Any]:
        """Extract document metadata"""
        try:
            document_text = data.get('document_text', '')
            
            prompt = f"""Extract metadata from this technical document:

{document_text[:3000]}

Extract:
- Document Number
- Revision Number
- Date
- Project Number/Name
- Client/Company
- Equipment Tag Number
- Equipment Description
- Preparer Name
- Checker Name
- Approver Name"""
            
            schema = {
                'document_number': 'str',
                'revision': 'str',
                'date': 'str',
                'project_number': 'str',
                'project_name': 'str',
                'client': 'str',
                'tag_number': 'str',
                'equipment_description': 'str',
                'prepared_by': 'str',
                'checked_by': 'str',
                'approved_by': 'str',
                'confidence': 'float'
            }
            
            result = self.call_llm_structured(prompt, schema)
            
            return {
                'success': True,
                'data': {'metadata': result}
            }
            
        except Exception as e:
            return {
                'success': False,
                'error': str(e)
            }
    
    def _assess_quality(self, data: Dict) -> Dict[str, Any]:
        """Assess document quality"""
        try:
            analysis = data.get('analysis', {})
            
            # Calculate quality score based on multiple factors
            factors = {
                'completeness': analysis.get('completeness', {}).get('percentage', 0) / 100,
                'structure': 1.0 if analysis.get('structure', {}).get('has_tables') else 0.5,
                'standard_compliance': 0.8 if analysis.get('standard') else 0.3,
                'clarity': analysis.get('quality_score', 0.5)
            }
            
            overall_quality = sum(factors.values()) / len(factors)
            
            issues = []
            if factors['completeness'] < 0.7:
                issues.append('Incomplete data (< 70%)')
            if not analysis.get('structure', {}).get('has_tables'):
                issues.append('Missing structured tables')
            if not analysis.get('standard'):
                issues.append('No standard reference found')
            
            return {
                'success': True,
                'data': {
                    'quality_assessment': {
                        'overall_score': overall_quality,
                        'factors': factors,
                        'issues': issues,
                        'recommendation': 'Ready for extraction' if overall_quality > 0.7 else 'Needs review'
                    }
                }
            }
            
        except Exception as e:
            return {
                'success': False,
                'error': str(e)
            }
