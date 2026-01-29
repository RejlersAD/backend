"""
Field Extractor Agent
Extracts specific field values from documents using AI
"""
from typing import Dict, Any, List
from .base_agent import BaseAgent


class FieldExtractorAgent(BaseAgent):
    """
    Agent specialized in extracting field values from documents
    Soft-coded field definitions from equipment configuration
    """
    
    AGENT_CONFIG = {
        'name': 'Field Extractor',
        'description': 'Extracts specific field values from technical documents with high accuracy',
        'model': 'gpt-4o',
        'temperature': 0.0,  # Zero temperature for maximum consistency
        'max_tokens': 4000,
        'system_prompt': """You are a precision field extraction specialist for engineering datasheets.

Your mission:
1. Extract EXACT values from documents - no interpretation or calculation
2. Maintain units and precision as shown in source
3. Handle various formats (tables, text, diagrams)
4. Identify missing or unclear fields
5. Provide confidence scores for each extraction

Rules:
- Extract values EXACTLY as written
- Include units if present
- Mark uncertain extractions with lower confidence
- Never guess or calculate - only extract what you see
- Be consistent with technical notation

You are highly reliable and conservative in your assessments.""",
        'capabilities': [
            'field_extraction',
            'unit_detection',
            'precision_maintenance',
            'multi_format_parsing',
            'confidence_scoring'
        ],
        'extraction_modes': {
            'precise': {'temperature': 0.0, 'confidence_threshold': 0.9},
            'flexible': {'temperature': 0.2, 'confidence_threshold': 0.7},
            'exploratory': {'temperature': 0.3, 'confidence_threshold': 0.5}
        }
    }
    
    def execute(self, task: Dict[str, Any]) -> Dict[str, Any]:
        """
        Execute field extraction task
        
        Args:
            task: {
                'type': 'extract',
                'data': {
                    'document_text': str,
                    'fields': List[Dict],  # Field definitions from equipment config
                    'mode': str  # 'precise', 'flexible', or 'exploratory'
                }
            }
            
        Returns:
            Extracted field values with confidence scores
        """
        task_type = task.get('type')
        
        if task_type == 'extract':
            return self._extract_fields(task['data'])
        elif task_type == 'extract_section':
            return self._extract_section(task['data'])
        elif task_type == 'verify_extraction':
            return self._verify_extraction(task['data'])
        else:
            return {
                'success': False,
                'error': f'Unknown task type: {task_type}'
            }
    
    def _extract_fields(self, data: Dict) -> Dict[str, Any]:
        """Extract all fields from document"""
        try:
            document_text = data.get('document_text', '')
            fields = data.get('fields', [])
            mode = data.get('mode', 'precise')
            
            # Apply mode settings
            mode_config = self.config['extraction_modes'].get(mode, {})
            temperature = mode_config.get('temperature', 0.0)
            confidence_threshold = mode_config.get('confidence_threshold', 0.9)
            
            # Build field descriptions
            field_descriptions = []
            for field in fields:
                desc = f"- {field['label']} (ID: {field['id']}, Type: {field['type']}"
                if field.get('unit'):
                    desc += f", Unit: {field['unit']}"
                if field.get('pattern'):
                    desc += f", Format: {field['pattern']}"
                desc += ")"
                field_descriptions.append(desc)
            
            prompt = f"""Extract the following field values from this technical document:

{chr(10).join(field_descriptions)}

Document:
{document_text}

For each field:
1. Find the exact value as written in the document
2. Include the unit if present
3. Maintain precision (decimal places) as shown
4. Assign confidence score (0.0-1.0) based on clarity

If a field is not found or unclear, omit it from results or mark with low confidence."""
            
            # Build schema dynamically from fields
            schema = {
                'extracted_fields': {
                    field['id']: {
                        'value': 'str or float',
                        'unit': 'str (if applicable)',
                        'confidence': 'float',
                        'source_location': 'str (where found in document)'
                    }
                    for field in fields
                },
                'overall_confidence': 'float',
                'missing_fields': ['str']
            }
            
            result = self.call_llm_structured(prompt, schema, temperature=temperature)
            
            # Filter by confidence threshold
            extracted = result.get('extracted_fields', {})
            filtered_fields = {
                field_id: field_data
                for field_id, field_data in extracted.items()
                if field_data.get('confidence', 0) >= confidence_threshold
            }
            
            self.log_execution(
                {'type': 'extract', 'field_count': len(fields), 'mode': mode},
                {'success': True, 'extracted': len(filtered_fields)}
            )
            
            return {
                'success': True,
                'data': {
                    'extracted_fields': filtered_fields,
                    'all_extractions': extracted,  # Include low-confidence for review
                    'overall_confidence': result.get('overall_confidence', 0.0),
                    'missing_fields': result.get('missing_fields', []),
                    'mode': mode,
                    'threshold': confidence_threshold
                }
            }
            
        except Exception as e:
            return {
                'success': False,
                'error': str(e)
            }
    
    def _extract_section(self, data: Dict) -> Dict[str, Any]:
        """Extract fields from a specific section"""
        try:
            document_text = data.get('document_text', '')
            section_id = data.get('section_id', '')
            section_fields = data.get('fields', [])
            
            prompt = f"""Extract values for the {section_id} section:

Fields to extract:
{chr(10).join([f"- {f['label']}" for f in section_fields])}

Document:
{document_text}

Focus only on this section. Extract exact values with units."""
            
            schema = {
                field['id']: {
                    'value': 'str or float',
                    'unit': 'str',
                    'confidence': 'float'
                }
                for field in section_fields
            }
            
            result = self.call_llm_structured(prompt, schema)
            
            return {
                'success': True,
                'data': {
                    'section_id': section_id,
                    'fields': result
                }
            }
            
        except Exception as e:
            return {
                'success': False,
                'error': str(e)
            }
    
    def _verify_extraction(self, data: Dict) -> Dict[str, Any]:
        """Verify previously extracted values"""
        try:
            document_text = data.get('document_text', '')
            extracted_data = data.get('extracted_data', {})
            
            prompt = f"""Verify these extracted values against the source document:

Extracted Values:
{self._format_context(extracted_data)}

Source Document:
{document_text}

For each field:
1. Confirm if value matches document (Yes/No)
2. If mismatch, provide correct value
3. Assign verification confidence (0.0-1.0)"""
            
            schema = {
                'verifications': {
                    'field_id': {
                        'matches': 'bool',
                        'correct_value': 'str (if mismatch)',
                        'confidence': 'float',
                        'notes': 'str'
                    }
                },
                'overall_accuracy': 'float',
                'discrepancies_found': 'int'
            }
            
            result = self.call_llm_structured(prompt, schema)
            
            return {
                'success': True,
                'data': {
                    'verification': result,
                    'accuracy': result.get('overall_accuracy', 0.0)
                }
            }
            
        except Exception as e:
            return {
                'success': False,
                'error': str(e)
            }
    
    def extract_with_context(self, document_text: str, field: Dict,
                            context_hints: List[str] = None) -> Dict:
        """
        Extract a single field with contextual hints
        
        Args:
            document_text: Source document
            field: Field definition
            context_hints: Optional context keywords to guide extraction
            
        Returns:
            Extracted value with confidence
        """
        try:
            context_text = ""
            if context_hints:
                context_text = f"\nContext hints: {', '.join(context_hints)}"
            
            prompt = f"""Extract: {field['label']}
Type: {field['type']}
{f"Expected unit: {field['unit']}" if field.get('unit') else ""}
{context_text}

Document:
{document_text}

Extract the exact value as it appears."""
            
            schema = {
                'value': 'str or float',
                'unit': 'str',
                'confidence': 'float',
                'location': 'str'
            }
            
            result = self.call_llm_structured(prompt, schema)
            
            return {
                'success': True,
                'field_id': field['id'],
                'extraction': result
            }
            
        except Exception as e:
            return {
                'success': False,
                'field_id': field['id'],
                'error': str(e)
            }
