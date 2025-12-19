"""
P&ID Analysis Service - Simplified Version  
"""
import os
import base64
import io
import json
from typing import Dict, List, Any, Optional
from django.conf import settings
from openai import OpenAI
import fitz  # PyMuPDF
from PIL import Image


class PIDAnalysisService:
    """AI-Powered P&ID Analysis Service"""

    def __init__(self):
        """Initialize OpenAI client"""
        api_key = (
            os.getenv('OPENAI_API_KEY') or
            getattr(settings, 'OPENAI_API_KEY', None)
        )
        
        if not api_key:
            raise ValueError("OPENAI_API_KEY not configured")
        
        self.client = OpenAI(api_key=api_key)
        print('[INFO] PID Analysis Service initialized')

    def analyze_pid_drawing(self, pdf_file, drawing_number: Optional[str] = None) -> Dict[str, Any]:
        """
        Analyze P&ID drawing from PDF file
        
        Args:
            pdf_file: Django FieldFile or file path
            drawing_number: Optional drawing number for reference
            
        Returns:
            Dictionary with analysis results
        """
        try:
            print(f"[INFO] Starting P&ID analysis for drawing: {drawing_number or 'Unknown'}")
            
            # Convert PDF to images
            images_base64 = self._pdf_to_base64_images(pdf_file)
            print(f"[INFO] Converted {len(images_base64)} pages to images")
            
            # Analyze with OpenAI
            result = self.analyze_images(images_base64)
            
            print(f"[INFO] Analysis complete. Found {result.get('total_issues', 0)} issues")
            return result
            
        except Exception as e:
            print(f"[ERROR] Analysis failed: {str(e)}")
            raise

    def analyze_images(self, images_base64: List[str], rag_context: Optional[str] = None) -> Dict[str, Any]:
        """
        Analyze P&ID images using OpenAI Vision API
        
        Args:
            images_base64: List of base64-encoded images
            rag_context: Optional RAG context for enhanced analysis
            
        Returns:
            Dictionary with analysis results
        """
        try:
            # Build messages for OpenAI
            messages = [
                {
                    "role": "system",
                    "content": """You are an expert P&ID verification engineer. Analyze the provided P&ID drawing and identify issues related to:
                    
- Equipment specifications and design data
- Instrument alarm and trip setpoints
- Line numbering and sizing
- Valve specifications and sizing
- Safety systems and interlocks
- Process flow logic
- Standards compliance (API, ASME, ISA, IEC)

Return your analysis as JSON with this structure:
{
    "issues": [
        {
            "serial_number": 1,
            "pid_reference": "Equipment/Line/Instrument tag",
            "issue_observed": "Detailed description",
            "action_required": "Specific recommendation",
            "severity": "critical/major/minor/observation",
            "category": "equipment/instrument/line/valve/safety/other"
        }
    ],
    "total_issues": 0,
    "confidence": "High/Medium/Low"
}"""
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": f"Analyze this P&ID drawing thoroughly and identify all design issues, missing data, and non-compliances.{' Reference context: ' + rag_context if rag_context else ''}"
                        }
                    ] + [
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/png;base64,{img}",
                                "detail": "high"
                            }
                        }
                        for img in images_base64
                    ]
                }
            ]
            
            # Call OpenAI API
            print("[INFO] Calling OpenAI Vision API...")
            response = self.client.chat.completions.create(
                model="gpt-4o",
                messages=messages,
                max_tokens=16000,
                temperature=0.1
            )
            
            # Extract response
            response_text = response.choices[0].message.content.strip()
            tokens_used = response.usage.total_tokens
            
            print(f"[INFO] OpenAI response received. Tokens used: {tokens_used}")
            
            # Parse JSON response
            result = self._parse_analysis_response(response_text, tokens_used)
            
            return result
            
        except Exception as e:
            print(f"[ERROR] Analysis failed: {str(e)}")
            raise

    def _parse_analysis_response(self, response_text: str, tokens_used: int) -> Dict[str, Any]:
        """Parse OpenAI response and extract JSON"""
        try:
            # Try to find JSON in response
            json_start = response_text.find('{')
            json_end = response_text.rfind('}') + 1
            
            if json_start >= 0 and json_end > json_start:
                json_str = response_text[json_start:json_end]
                result = json.loads(json_str)
                result['tokens_used'] = tokens_used
                result['raw_response'] = response_text
                return result
            else:
                # Fallback: create basic response
                return {
                    'issues': [{
                        'serial_number': 1,
                        'pid_reference': 'ANALYSIS',
                        'issue_observed': 'Analysis completed - see raw response for details',
                        'action_required': 'Review raw analysis output',
                        'severity': 'observation',
                        'category': 'other'
                    }],
                    'total_issues': 1,
                    'confidence': 'Medium',
                    'tokens_used': tokens_used,
                    'raw_response': response_text
                }
                
        except json.JSONDecodeError as e:
            print(f"[ERROR] JSON parsing failed: {str(e)}")
            return {
                'issues': [{
                    'serial_number': 1,
                    'pid_reference': 'PARSING_ERROR',
                    'issue_observed': f'Failed to parse AI response: {str(e)}',
                    'action_required': 'Review raw response',
                    'severity': 'observation',
                    'category': 'other'
                }],
                'total_issues': 1,
                'confidence': 'Low',
                'tokens_used': tokens_used,
                'raw_response': response_text,
                'parsing_error': True
            }

    def _pdf_to_base64_images(self, pdf_file, dpi: int = 150) -> List[str]:
        """
        Convert PDF pages to base64-encoded PNG images
        
        Args:
            pdf_file: Django FieldFile or file path
            dpi: Resolution for rendering (default: 150)
            
        Returns:
            List of base64-encoded image strings
        """
        images_base64 = []
        
        try:
            # Soft-coded approach: Handle both file paths and file objects (S3/Django FileField)
            if isinstance(pdf_file, str):
                # Local file path
                doc = fitz.open(pdf_file)
            else:
                # File object (from S3 or Django FileField) - read content into memory
                pdf_file.seek(0)  # Ensure we're at the start
                pdf_bytes = pdf_file.read()
                doc = fitz.open(stream=pdf_bytes, filetype="pdf")
            
            # Convert each page
            for page_num in range(len(doc)):
                page = doc.load_page(page_num)
                
                # Render to image
                mat = fitz.Matrix(dpi/72, dpi/72)
                pix = page.get_pixmap(matrix=mat)
                
                # Convert to PIL Image
                img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
                
                # Convert to base64
                buffer = io.BytesIO()
                img.save(buffer, format="PNG", optimize=True)
                img_base64 = base64.b64encode(buffer.getvalue()).decode('utf-8')
                images_base64.append(img_base64)
            
            doc.close()
            return images_base64
            
        except Exception as e:
            print(f"[ERROR] PDF conversion failed: {str(e)}")
            raise
