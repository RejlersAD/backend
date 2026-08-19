"""
AI-Powered P&ID Extraction Engine
Handles vision API calls to OpenAI GPT-4o and Claude 3.5 Sonnet for P&ID element extraction.
"""
import json
import base64
import re
from typing import Dict, List, Optional, Tuple
from pathlib import Path
import requests
from io import BytesIO
from PIL import Image

from .ai_config import (
    VISION_API_CONFIG,
    VISION_PROMPTS,
    EXTRACTION_PATTERNS,
    API_LIMITS,
)


class PIDExtractionEngine:
    """Vision API extraction engine for P&ID documents."""
    
    def __init__(self, openai_key: Optional[str] = None, claude_key: Optional[str] = None, mode: str = 'hybrid'):
        """
        Initialize extraction engine with BYOK credentials.
        
        Args:
            openai_key: OpenAI API key (for enhanced/hybrid mode)
            claude_key: Claude API key (for deep/hybrid mode)
            mode: Analysis mode (standard, enhanced_openai, deep_claude, hybrid)
        """
        self.openai_key = openai_key
        self.claude_key = claude_key
        self.mode = mode
        self.config = VISION_API_CONFIG
        
    def _encode_image(self, image_path_or_bytes) -> str:
        """Encode image to base64 string."""
        if isinstance(image_path_or_bytes, (str, Path)):
            with open(image_path_or_bytes, 'rb') as f:
                return base64.b64encode(f.read()).decode('utf-8')
        elif isinstance(image_path_or_bytes, bytes):
            return base64.b64encode(image_path_or_bytes).decode('utf-8')
        else:
            raise ValueError("image_path_or_bytes must be file path or bytes")
    
    def _call_openai_vision(self, image_base64: str, prompt: str) -> Dict:
        """
        Call OpenAI GPT-4 Vision API.
        
        Args:
            image_base64: Base64-encoded image
            prompt: Extraction prompt
            
        Returns:
            API response as dict
        """
        if not self.openai_key:
            raise ValueError("OpenAI API key required for this mode")
        
        openai_config = self.config['providers']['openai']
        
        headers = {
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {self.openai_key}'
        }
        
        payload = {
            'model': openai_config['model'],
            'messages': [
                {
                    'role': 'user',
                    'content': [
                        {
                            'type': 'text',
                            'text': prompt
                        },
                        {
                            'type': 'image_url',
                            'image_url': {
                                'url': f'data:image/png;base64,{image_base64}'
                            }
                        }
                    ]
                }
            ],
            'temperature': openai_config['temperature'],
            'max_tokens': openai_config['max_tokens']
        }
        
        response = requests.post(
            'https://api.openai.com/v1/chat/completions',
            headers=headers,
            json=payload,
            timeout=openai_config['timeout']
        )
        
        response.raise_for_status()
        return response.json()
    
    def _call_claude_vision(self, image_base64: str, prompt: str) -> Dict:
        """
        Call Claude 3.5 Sonnet Vision API.
        
        Args:
            image_base64: Base64-encoded image
            prompt: Extraction prompt
            
        Returns:
            API response as dict
        """
        if not self.claude_key:
            raise ValueError("Claude API key required for this mode")
        
        claude_config = self.config['providers']['claude']
        
        headers = {
            'Content-Type': 'application/json',
            'x-api-key': self.claude_key,
            'anthropic-version': '2023-06-01'
        }
        
        payload = {
            'model': claude_config['model'],
            'messages': [
                {
                    'role': 'user',
                    'content': [
                        {
                            'type': 'image',
                            'source': {
                                'type': 'base64',
                                'media_type': 'image/png',
                                'data': image_base64
                            }
                        },
                        {
                            'type': 'text',
                            'text': prompt
                        }
                    ]
                }
            ],
            'temperature': claude_config['temperature'],
            'max_tokens': claude_config['max_tokens']
        }
        
        response = requests.post(
            'https://api.anthropic.com/v1/messages',
            headers=headers,
            json=payload,
            timeout=claude_config['timeout']
        )
        
        response.raise_for_status()
        return response.json()
    
    def _extract_json_from_response(self, response_text: str) -> Optional[Dict | List]:
        """
        Extract JSON from API response, handling various formats.
        
        Args:
            response_text: Raw response text
            
        Returns:
            Parsed JSON or None if parsing fails
        """
        # Remove markdown code blocks if present
        response_text = re.sub(r'```json\s*', '', response_text)
        response_text = re.sub(r'```\s*$', '', response_text)
        response_text = response_text.strip()
        
        try:
            return json.loads(response_text)
        except json.JSONDecodeError:
            # Try to find JSON array or object in text
            json_match = re.search(r'(\[.*\]|\{.*\})', response_text, re.DOTALL)
            if json_match:
                try:
                    return json.loads(json_match.group(1))
                except json.JSONDecodeError:
                    pass
        
        return None
    
    def extract_equipment(self, image_path_or_bytes, sheet_number: str = None) -> List[Dict]:
        """
        Extract equipment tags from P&ID sheet.
        
        Args:
            image_path_or_bytes: P&ID image (path or bytes)
            sheet_number: Sheet identifier
            
        Returns:
            List of equipment items with tags, types, service, confidence
        """
        image_base64 = self._encode_image(image_path_or_bytes)
        prompt = VISION_PROMPTS['equipment_extraction']
        
        # Choose provider based on mode
        provider = self.config['extraction_strategy']['default_provider']
        
        try:
            if provider == 'claude' and self.claude_key:
                response = self._call_claude_vision(image_base64, prompt)
                response_text = response['content'][0]['text']
            elif self.openai_key:
                response = self._call_openai_vision(image_base64, prompt)
                response_text = response['choices'][0]['message']['content']
            else:
                return []
            
            # Parse JSON response
            equipment_list = self._extract_json_from_response(response_text)
            
            if equipment_list and isinstance(equipment_list, list):
                # Add sheet number if provided
                if sheet_number:
                    for item in equipment_list:
                        if 'sheet' not in item:
                            item['sheet'] = sheet_number
                return equipment_list
            
        except Exception as e:
            print(f"Equipment extraction error: {e}")
        
        return []
    
    def extract_lines(self, image_path_or_bytes, sheet_number: str = None) -> List[Dict]:
        """
        Extract piping line numbers from P&ID sheet.
        
        Args:
            image_path_or_bytes: P&ID image (path or bytes)
            sheet_number: Sheet identifier
            
        Returns:
            List of lines with line_number, size, spec, insulation, from/to, confidence
        """
        image_base64 = self._encode_image(image_path_or_bytes)
        prompt = VISION_PROMPTS['line_extraction']
        
        # Claude is better for complex line number parsing
        provider = 'claude' if self.claude_key else 'openai'
        
        try:
            if provider == 'claude' and self.claude_key:
                response = self._call_claude_vision(image_base64, prompt)
                response_text = response['content'][0]['text']
            elif self.openai_key:
                response = self._call_openai_vision(image_base64, prompt)
                response_text = response['choices'][0]['message']['content']
            else:
                return []
            
            # Parse JSON response
            line_list = self._extract_json_from_response(response_text)
            
            if line_list and isinstance(line_list, list):
                # Add sheet number if provided
                if sheet_number:
                    for item in line_list:
                        if 'sheet' not in item:
                            item['sheet'] = sheet_number
                return line_list
            
        except Exception as e:
            print(f"Line extraction error: {e}")
        
        return []
    
    def extract_instruments(self, image_path_or_bytes, sheet_number: str = None) -> List[Dict]:
        """
        Extract instrument tags from P&ID sheet.
        
        Args:
            image_path_or_bytes: P&ID image (path or bytes)
            sheet_number: Sheet identifier
            
        Returns:
            List of instruments with tag, type, location, associated_equipment, confidence
        """
        image_base64 = self._encode_image(image_path_or_bytes)
        prompt = VISION_PROMPTS['instrument_extraction']
        
        # OpenAI is better for symbol recognition
        provider = self.config['extraction_strategy']['symbol_recognition_provider']
        
        try:
            if provider == 'openai' and self.openai_key:
                response = self._call_openai_vision(image_base64, prompt)
                response_text = response['choices'][0]['message']['content']
            elif self.claude_key:
                response = self._call_claude_vision(image_base64, prompt)
                response_text = response['content'][0]['text']
            else:
                return []
            
            # Parse JSON response
            instrument_list = self._extract_json_from_response(response_text)
            
            if instrument_list and isinstance(instrument_list, list):
                # Add sheet number if provided
                if sheet_number:
                    for item in instrument_list:
                        if 'sheet' not in item:
                            item['sheet'] = sheet_number
                return instrument_list
            
        except Exception as e:
            print(f"Instrument extraction error: {e}")
        
        return []
    
    def extract_quick_overview(self, image_path_or_bytes) -> Dict:
        """
        Get quick overview of P&ID sheet (count of elements, quality assessment).
        
        Args:
            image_path_or_bytes: P&ID image (path or bytes)
            
        Returns:
            Overview dict with counts and quality metrics
        """
        image_base64 = self._encode_image(image_path_or_bytes)
        prompt = VISION_PROMPTS['quick_overview']
        
        provider = self.config['extraction_strategy']['default_provider']
        
        try:
            if provider == 'claude' and self.claude_key:
                response = self._call_claude_vision(image_base64, prompt)
                response_text = response['content'][0]['text']
            elif self.openai_key:
                response = self._call_openai_vision(image_base64, prompt)
                response_text = response['choices'][0]['message']['content']
            else:
                return {}
            
            # Parse JSON response
            overview = self._extract_json_from_response(response_text)
            
            if overview and isinstance(overview, dict):
                return overview
            
        except Exception as e:
            print(f"Quick overview error: {e}")
        
        return {}
    
    def extract_all(self, image_path_or_bytes, sheet_number: str = None) -> Dict:
        """
        Extract all elements from P&ID sheet (equipment, lines, instruments).
        
        Args:
            image_path_or_bytes: P&ID image (path or bytes)
            sheet_number: Sheet identifier
            
        Returns:
            Dict with equipment, lines, instruments, overview
        """
        return {
            'sheet_number': sheet_number,
            'equipment': self.extract_equipment(image_path_or_bytes, sheet_number),
            'lines': self.extract_lines(image_path_or_bytes, sheet_number),
            'instruments': self.extract_instruments(image_path_or_bytes, sheet_number),
            'overview': self.extract_quick_overview(image_path_or_bytes),
        }
