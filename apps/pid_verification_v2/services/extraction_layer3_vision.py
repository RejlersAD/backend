"""
PID Verification V2 - Layer 3: Vision AI Enhancement (BYOK)
===========================================================
Implements AI Vision-based extraction with BYOK support:
  - OpenAI GPT-4 Vision
  - Claude 3.5 Sonnet Vision
  - Google Gemini Vision

This layer is CONDITIONALLY executed based on extraction mode:
  - fast: NEVER runs (OCR-only)
  - balanced: Runs if Layer 1/2 confidence < 70% (smart fallback)
  - deep: ALWAYS runs (full Vision analysis)
  - vision_only: ONLY this layer runs (no OCR preprocessing)

Cost: Paid API calls (user BYOK or platform key)
"""

import os
import base64
import logging
import time
from typing import Dict, List, Optional
from decimal import Decimal
from PIL import Image
import io

# Import configuration
from ..extraction_config import LAYER2_AI_CONFIG

logger = logging.getLogger(__name__)


class Layer3VisionAIExtractor:
    """
    Layer 3: Vision AI enhancement service with BYOK support.
    
    Provides AI-powered extraction using vision models when:
      - Layer 1/2 confidence is low (balanced mode)
      - User wants highest accuracy (deep mode)
      - User selects vision-only mode
    """
    
    def __init__(
        self,
        mode: str = 'balanced',
        user_api_key: Optional[str] = None,
        provider: str = 'openai'
    ):
        """
        Initialize Layer 3 Vision AI extractor.
        
        Args:
            mode: 'fast' | 'balanced' | 'deep' | 'vision_only'
            user_api_key: User's API key (BYOK) or None to use platform key
            provider: 'openai' | 'claude' | 'gemini'
        """
        self.mode = mode
        self.user_api_key = user_api_key
        self.provider = provider
        self.config = LAYER2_AI_CONFIG
        self.total_cost = Decimal('0.0000')
        
        # Get mode configuration
        self.mode_config = next(
            (m for m in self.config['modes'] if m['id'] == mode),
            self.config['modes'][1]  # Default to balanced
        )
        
        # Get provider configuration
        self.provider_config = next(
            (p for p in self.config['providers'] if p['name'] == provider),
            self.config['providers'][0]  # Default to OpenAI
        )
    
    def should_trigger(self, layer1_result: Dict, layer2_result: Optional[Dict] = None) -> bool:
        """
        Determine if Layer 3 Vision AI should be triggered.
        
        Args:
            layer1_result: Results from Layer 1 extraction
            layer2_result: Results from Layer 2 extraction (if ran)
        
        Returns:
            True if Layer 3 should run, False otherwise
        """
        # Mode-based decision
        uses_vision = self.mode_config.get('uses_vision_api', False)
        
        if uses_vision == False:
            # fast mode: never use Vision AI
            return False
        
        if uses_vision == True:
            # deep or vision_only mode: always use Vision AI
            return True
        
        if uses_vision == 'conditional':
            # balanced mode: check if Layer 1/2 confidence is low
            trigger_config = self.mode_config.get('vision_trigger', {})
            confidence_threshold = trigger_config.get('ocr_confidence_below', 70)
            tags_threshold = trigger_config.get('tags_found_below', 10)
            
            # Calculate best confidence from Layer 1 and Layer 2
            best_confidence = 0
            
            # Layer 1 confidence
            for page_result in layer1_result.get('per_page_results', []):
                conf = page_result.get('confidence_score', 0)
                best_confidence = max(best_confidence, conf)
            
            # Layer 2 confidence (if available)
            if layer2_result:
                for page_result in layer2_result.get('per_page_results', []):
                    merged = page_result.get('merged_result', {})
                    conf = merged.get('best_confidence', 0)
                    best_confidence = max(best_confidence, conf)
            
            # Count total tags found
            aggregated = layer1_result.get('aggregated_data', {})
            if layer2_result:
                # Use Layer 2 aggregated if available
                aggregated = layer2_result.get('aggregated_data', aggregated)
            
            total_tags = (
                len(aggregated.get('equipment_tags', [])) +
                len(aggregated.get('line_numbers', [])) +
                len(aggregated.get('instrument_tags', []))
            )
            
            should_run = best_confidence < confidence_threshold or total_tags < tags_threshold
            
            if should_run:
                logger.info(
                    f"[Layer3] Triggering Vision AI: "
                    f"confidence={best_confidence:.1f}% (threshold={confidence_threshold}%), "
                    f"tags_found={total_tags} (threshold={tags_threshold})"
                )
            else:
                logger.info(
                    f"[Layer3] Skipping Vision AI: "
                    f"confidence={best_confidence:.1f}% >= {confidence_threshold}%, "
                    f"tags_found={total_tags} >= {tags_threshold}"
                )
            
            return should_run
        
        return False
    
    def extract_from_image(
        self,
        image: Image.Image,
        page_num: int,
        ocr_context: Optional[Dict] = None
    ) -> Dict:
        """
        Extract data from image using Vision AI.
        
        Args:
            image: PIL Image object
            page_num: Page number being processed
            ocr_context: Optional OCR results from Layer 1/2 for context
        
        Returns:
            {
                'provider': str,
                'model': str,
                'extracted_data': {...},
                'raw_response': str,
                'cost_usd': float,
                'processing_time': float,
            }
        """
        start_time = time.time()
        logger.info(f"[Layer3] Running {self.provider} Vision AI on page {page_num}")
        
        result = {
            'provider': self.provider,
            'model': self.provider_config['vision_model'],
            'extracted_data': {},
            'raw_response': '',
            'cost_usd': 0.0,
            'processing_time': 0.0,
            'error': None,
        }
        
        # Convert image to base64
        image_base64 = self._image_to_base64(image)
        
        # Build prompt based on mode
        prompt = self._build_vision_prompt(ocr_context)
        
        # Call appropriate Vision API
        if self.provider == 'openai':
            result = self._call_openai_vision(image_base64, prompt, page_num)
        elif self.provider == 'claude':
            result = self._call_claude_vision(image_base64, prompt, page_num)
        elif self.provider == 'gemini':
            result = self._call_gemini_vision(image_base64, prompt, page_num)
        else:
            result['error'] = f"Unknown provider: {self.provider}"
        
        end_time = time.time()
        result['processing_time'] = round(end_time - start_time, 2)
        
        # Track cost
        self.total_cost += Decimal(str(result['cost_usd']))
        
        logger.info(
            f"[Layer3] {self.provider} Vision complete for page {page_num} "
            f"in {result['processing_time']}s (cost: ${result['cost_usd']:.4f})"
        )
        
        return result
    
    def _call_openai_vision(self, image_base64: str, prompt: str, page_num: int) -> Dict:
        """Call OpenAI GPT-4 Vision API."""
        try:
            import openai
            
            # Use user key or platform key
            api_key = self.user_api_key or os.getenv('OPENAI_API_KEY')
            if not api_key:
                return {
                    'error': 'No API key provided (BYOK required or platform key missing)',
                    'cost_usd': 0.0,
                }
            
            client = openai.OpenAI(api_key=api_key)
            
            # Call Vision API
            response = client.chat.completions.create(
                model=self.provider_config['vision_model'],
                messages=[
                    {
                        "role": "system",
                        "content": "You are an expert P&ID extraction assistant. Extract all equipment tags, line numbers, symbols, and notes from engineering drawings with high accuracy."
                    },
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:image/png;base64,{image_base64}",
                                    "detail": "high"
                                }
                            }
                        ]
                    }
                ],
                max_tokens=self.provider_config['max_tokens'],
                temperature=self.provider_config['temperature'],
            )
            
            # Extract response
            raw_response = response.choices[0].message.content
            
            # Parse JSON response (if AI returns structured JSON)
            import json
            try:
                extracted_data = json.loads(raw_response)
            except json.JSONDecodeError:
                # If not JSON, treat as plain text
                extracted_data = {'raw_text': raw_response}
            
            # Calculate cost
            usage = response.usage
            cost_usd = self._calculate_openai_cost(usage)
            
            return {
                'provider': 'openai',
                'model': self.provider_config['vision_model'],
                'extracted_data': extracted_data,
                'raw_response': raw_response,
                'cost_usd': cost_usd,
                'tokens_used': {
                    'input': usage.prompt_tokens,
                    'output': usage.completion_tokens,
                    'total': usage.total_tokens,
                },
                'error': None,
            }
        
        except Exception as e:
            logger.error(f"[OpenAI Vision] API call failed: {str(e)}")
            return {
                'provider': 'openai',
                'error': str(e),
                'cost_usd': 0.0,
            }
    
    def _call_claude_vision(self, image_base64: str, prompt: str, page_num: int) -> Dict:
        """Call Anthropic Claude Vision API."""
        try:
            import anthropic
            
            # Use user key or platform key
            api_key = self.user_api_key or os.getenv('ANTHROPIC_API_KEY')
            if not api_key:
                return {
                    'error': 'No API key provided (BYOK required or platform key missing)',
                    'cost_usd': 0.0,
                }
            
            client = anthropic.Anthropic(api_key=api_key)
            
            # Call Vision API
            response = client.messages.create(
                model=self.provider_config['vision_model'],
                max_tokens=self.provider_config['max_tokens'],
                temperature=self.provider_config['temperature'],
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": "image/png",
                                    "data": image_base64,
                                },
                            },
                            {
                                "type": "text",
                                "text": prompt
                            }
                        ],
                    }
                ],
            )
            
            # Extract response
            raw_response = response.content[0].text
            
            # Parse JSON response
            import json
            try:
                extracted_data = json.loads(raw_response)
            except json.JSONDecodeError:
                extracted_data = {'raw_text': raw_response}
            
            # Calculate cost
            usage = response.usage
            cost_usd = self._calculate_claude_cost(usage)
            
            return {
                'provider': 'claude',
                'model': self.provider_config['vision_model'],
                'extracted_data': extracted_data,
                'raw_response': raw_response,
                'cost_usd': cost_usd,
                'tokens_used': {
                    'input': usage.input_tokens,
                    'output': usage.output_tokens,
                },
                'error': None,
            }
        
        except Exception as e:
            logger.error(f"[Claude Vision] API call failed: {str(e)}")
            return {
                'provider': 'claude',
                'error': str(e),
                'cost_usd': 0.0,
            }
    
    def _call_gemini_vision(self, image_base64: str, prompt: str, page_num: int) -> Dict:
        """Call Google Gemini Vision API."""
        # TODO: Implement Gemini Vision API
        # This is a placeholder - full implementation requires google-generativeai SDK
        return {
            'provider': 'gemini',
            'error': 'Gemini Vision not yet implemented',
            'cost_usd': 0.0,
        }
    
    def _build_vision_prompt(self, ocr_context: Optional[Dict] = None) -> str:
        """Build prompt for Vision AI based on mode and OCR context."""
        
        if self.mode == 'vision_only':
            # Pure Vision mode - no OCR context
            prompt = """Extract all information from this P&ID drawing in structured JSON format:

{
  "equipment_tags": ["tag1", "tag2", ...],
  "line_numbers": ["line1", "line2", ...],
  "instrument_tags": ["inst1", "inst2", ...],
  "symbols": [{"type": "valve/pump/etc", "location": "...", "tag": "..."}],
  "notes": ["note1", "note2", ...],
  "connections": [{"from": "tag1", "to": "tag2", "line": "line_number"}]
}

Be thorough and accurate. Extract ALL tags and line numbers visible."""
        
        elif self.mode == 'deep':
            # Deep mode - full analysis with OCR cross-validation
            ocr_tags = []
            if ocr_context:
                aggregated = ocr_context.get('aggregated_data', {})
                ocr_tags = aggregated.get('equipment_tags', [])[:20]  # First 20 for context
            
            prompt = f"""Perform deep analysis of this P&ID drawing. 

OCR detected these tags (validate and expand): {ocr_tags if ocr_tags else 'None detected - extract all'}

Extract in structured JSON format:
{{
  "equipment_tags": [...],
  "line_numbers": [...],
  "instrument_tags": [...],
  "symbols": [...],
  "notes": [...],
  "connections": [...],
  "validation": {{
    "ocr_tags_confirmed": [...],
    "ocr_tags_corrected": [{{"ocr": "...", "correct": "..."}}],
    "new_tags_found": [...]
  }}
}}

Cross-validate ALL OCR results and find any missed items."""
        
        else:  # balanced mode
            # Smart fallback - focus on weak areas
            prompt = """The OCR extraction had low confidence or found few items. 

Please extract all visible information from this P&ID drawing in structured JSON format:

{
  "equipment_tags": [...],
  "line_numbers": [...],
  "instrument_tags": [...],
  "symbols": [...],
  "notes": [...]
}

Focus on handwritten notes, rotated text, and unclear areas that OCR might have missed."""
        
        return prompt
    
    def _image_to_base64(self, image: Image.Image) -> str:
        """Convert PIL Image to base64 string."""
        buffered = io.BytesIO()
        image.save(buffered, format="PNG")
        return base64.b64encode(buffered.getvalue()).decode('utf-8')
    
    def _calculate_openai_cost(self, usage) -> float:
        """Calculate cost for OpenAI API call."""
        input_cost = (usage.prompt_tokens / 1000) * self.provider_config['cost_per_1k_tokens_input']
        output_cost = (usage.completion_tokens / 1000) * self.provider_config['cost_per_1k_tokens_output']
        
        # Add image cost (high-res mode)
        image_cost = self.provider_config.get('cost_per_image', 0.01275)
        
        total_cost = input_cost + output_cost + image_cost
        return round(total_cost, 4)
    
    def _calculate_claude_cost(self, usage) -> float:
        """Calculate cost for Claude API call."""
        input_cost = (usage.input_tokens / 1000) * self.provider_config['cost_per_1k_tokens_input']
        output_cost = (usage.output_tokens / 1000) * self.provider_config['cost_per_1k_tokens_output']
        total_cost = input_cost + output_cost
        return round(total_cost, 4)
    
    def get_total_cost(self) -> Decimal:
        """Get total cost accumulated during this session."""
        return self.total_cost
