"""
AI Provider Configuration - Soft-coded cost-optimized extraction
Implements intelligent cost management with fallback strategies

COST COMPARISON (per 1000 images/pages):
- Tier 1 (FREE): Local OCR (PaddleOCR/Tesseract) - $0
- Tier 2 (LOW): GPT-3.5-turbo text interpretation - ~$0.50
- Tier 3 (HIGH): GPT-4o Vision - ~$50-100

RECOMMENDED: Tier 1 (Free) or Tier 2 (Low Cost)
"""

import os
from typing import Dict, List
import logging

logger = logging.getLogger(__name__)


class AIProviderConfig:
    """
    Soft-coded AI provider configuration with intelligent cost management
    """
    
    # ========================================================================
    # MAIN CONFIGURATION - EDIT HERE TO CHANGE BEHAVIOR
    # ========================================================================
    
    ENABLED_PROVIDERS = {
        'local_ocr': True,          # FREE - PaddleOCR/Tesseract (Recommended)
        'gpt_3.5_turbo': True,      # LOW COST - ~$0.0005 per page (Recommended)
        'gpt_4_turbo': False,       # MEDIUM COST - ~$0.01 per page
        'gpt_4o_vision': False,     # HIGH COST - ~$0.05-0.10 per page (Use only if needed)
    }
    
    # Priority order (tries each method until success)
    EXTRACTION_STRATEGY = 'cost_optimized'  # Options: 'cost_optimized', 'quality_first', 'speed_first', 'local_only'
    
    STRATEGIES = {
        'cost_optimized': {
            'methods': ['local_ocr', 'gpt_3.5_turbo'],  # Try free first, then cheap
            'description': 'Minimize costs while maintaining accuracy',
            'estimated_cost_per_page': 0.0005  # ~$0.50 per 1000 pages
        },
        'quality_first': {
            'methods': ['gpt_4o_vision', 'gpt_4_turbo', 'local_ocr'],  # Try best first
            'description': 'Maximum accuracy, higher cost',
            'estimated_cost_per_page': 0.075  # ~$75 per 1000 pages
        },
        'speed_first': {
            'methods': ['local_ocr'],  # Fastest, no network calls
            'description': 'Fastest processing, no API costs',
            'estimated_cost_per_page': 0.0  # FREE
        },
        'local_only': {
            'methods': ['local_ocr'],  # No external APIs
            'description': 'Complete offline processing, zero cost',
            'estimated_cost_per_page': 0.0  # FREE
        }
    }
    
    # ========================================================================
    # COST TRACKING CONFIGURATION
    # ========================================================================
    
    COST_TRACKING = {
        'enabled': True,
        'warn_threshold': 10.0,      # Warn if daily cost exceeds $10
        'block_threshold': 100.0,    # Block if daily cost exceeds $100
        'reset_period': 'daily'      # Options: 'hourly', 'daily', 'monthly'
    }
    
    # ========================================================================
    # PROVIDER-SPECIFIC CONFIGURATIONS
    # ========================================================================
    
    LOCAL_OCR_CONFIG = {
        'engine': 'paddleocr',  # Options: 'paddleocr', 'tesseract', 'easyocr', 'hybrid'
        'languages': ['en'],
        'use_gpu': False,  # Set True if GPU available (faster)
        'confidence_threshold': 0.65,
        'preprocessing': {
            'enhance_contrast': True,
            'denoise': True,
            'binarize': True,
            'deskew': True
        },
        'cost': 0.0  # FREE
    }
    
    GPT_35_TURBO_CONFIG = {
        'model': 'gpt-3.5-turbo',
        'temperature': 0.1,
        'max_tokens': 2000,
        'cost_per_1k_input_tokens': 0.0005,   # $0.50 per 1M tokens
        'cost_per_1k_output_tokens': 0.0015,  # $1.50 per 1M tokens
        'average_cost_per_page': 0.0005,      # Estimated
        'timeout': 30
    }
    
    GPT_4_TURBO_CONFIG = {
        'model': 'gpt-4-turbo',
        'temperature': 0.1,
        'max_tokens': 2000,
        'cost_per_1k_input_tokens': 0.01,     # $10 per 1M tokens
        'cost_per_1k_output_tokens': 0.03,    # $30 per 1M tokens
        'average_cost_per_page': 0.01,
        'timeout': 60
    }
    
    GPT_4O_VISION_CONFIG = {
        'model': 'gpt-4o',
        'temperature': 0.1,
        'max_tokens': 4096,
        'image_detail': 'high',  # Options: 'low', 'high', 'auto'
        'cost_per_1k_input_tokens': 0.005,    # $5 per 1M tokens
        'cost_per_1k_output_tokens': 0.015,   # $15 per 1M tokens
        'cost_per_image_high': 0.075,         # $0.075 per high-detail image
        'cost_per_image_low': 0.025,          # $0.025 per low-detail image
        'average_cost_per_page': 0.075,
        'timeout': 90
    }
    
    # ========================================================================
    # QUALITY & FALLBACK CONFIGURATION
    # ========================================================================
    
    QUALITY_CHECKS = {
        'min_confidence': 0.6,           # Minimum confidence to accept results
        'min_equipment_extracted': 1,     # Minimum equipment items to consider success
        'require_validation': True,       # Validate extracted data structure
        'retry_on_low_quality': True,     # Retry with next method if quality low
        'max_retries_per_method': 2       # Max retries for same method
    }
    
    FALLBACK_RULES = {
        'on_api_error': 'next_method',        # Options: 'next_method', 'fail', 'cache'
        'on_low_confidence': 'next_method',   # Try next method if confidence low
        'on_timeout': 'next_method',          # Try next method on timeout
        'on_quota_exceeded': 'local_only',    # Switch to local-only if quota hit
        'on_network_error': 'local_only'      # Use local OCR if network down
    }
    
    # ========================================================================
    # HYBRID EXTRACTION CONFIGURATION
    # ========================================================================
    
    HYBRID_CONFIG = {
        'use_local_ocr_first': True,           # Always extract text locally first
        'send_text_to_llm': True,              # Send OCR text to LLM for interpretation
        'send_images_if_text_fails': False,    # Send images only if OCR fails (expensive)
        'combine_methods': False,              # Combine results from multiple methods
        'prefer_structured_output': True       # Prefer JSON output from LLMs
    }
    
    # ========================================================================
    # CACHING CONFIGURATION
    # ========================================================================
    
    CACHE_CONFIG = {
        'enabled': True,
        'cache_ocr_results': True,        # Cache OCR text extraction
        'cache_llm_results': True,        # Cache LLM interpretations
        'cache_ttl_hours': 24,            # Cache for 24 hours
        'cache_key_prefix': 'sld_extract_'
    }
    
    # ========================================================================
    # METHODS
    # ========================================================================
    
    @classmethod
    def get_active_strategy(cls) -> Dict:
        """Get the currently active extraction strategy"""
        strategy_name = cls.EXTRACTION_STRATEGY
        strategy = cls.STRATEGIES.get(strategy_name, cls.STRATEGIES['cost_optimized'])
        
        # Filter methods based on enabled providers
        enabled_methods = [
            method for method in strategy['methods']
            if cls.ENABLED_PROVIDERS.get(method, False)
        ]
        
        if not enabled_methods:
            logger.warning("[AIProviderConfig] No enabled methods! Defaulting to local_ocr")
            enabled_methods = ['local_ocr']
        
        return {
            'name': strategy_name,
            'methods': enabled_methods,
            'description': strategy.get('description', ''),
            'estimated_cost_per_page': strategy.get('estimated_cost_per_page', 0.0)
        }
    
    @classmethod
    def estimate_cost(cls, num_pages: int, method: str = None) -> Dict:
        """
        Estimate cost for processing given number of pages
        
        Args:
            num_pages: Number of pages to process
            method: Specific method (if None, uses active strategy)
        
        Returns:
            Dict with cost breakdown
        """
        if method:
            config_map = {
                'local_ocr': cls.LOCAL_OCR_CONFIG,
                'gpt_3.5_turbo': cls.GPT_35_TURBO_CONFIG,
                'gpt_4_turbo': cls.GPT_4_TURBO_CONFIG,
                'gpt_4o_vision': cls.GPT_4O_VISION_CONFIG
            }
            config = config_map.get(method, {})
            cost_per_page = config.get('average_cost_per_page', 0.0)
        else:
            strategy = cls.get_active_strategy()
            cost_per_page = strategy['estimated_cost_per_page']
        
        total_cost = num_pages * cost_per_page
        
        return {
            'num_pages': num_pages,
            'cost_per_page': cost_per_page,
            'total_cost': total_cost,
            'currency': 'USD',
            'breakdown': f"${cost_per_page:.4f} × {num_pages} pages = ${total_cost:.2f}"
        }
    
    @classmethod
    def get_provider_config(cls, method: str) -> Dict:
        """Get configuration for specific provider method"""
        config_map = {
            'local_ocr': cls.LOCAL_OCR_CONFIG,
            'gpt_3.5_turbo': cls.GPT_35_TURBO_CONFIG,
            'gpt_4_turbo': cls.GPT_4_TURBO_CONFIG,
            'gpt_4o_vision': cls.GPT_4O_VISION_CONFIG
        }
        return config_map.get(method, {})
    
    @classmethod
    def get_openai_api_key(cls) -> str:
        """Get OpenAI API key from environment"""
        api_key = os.getenv('OPENAI_API_KEY', '')
        if not api_key:
            logger.warning("[AIProviderConfig] OPENAI_API_KEY not configured")
        return api_key
    
    @classmethod
    def is_openai_available(cls) -> bool:
        """Check if OpenAI API is available"""
        return bool(cls.get_openai_api_key())
    
    @classmethod
    def get_recommended_config(cls) -> Dict:
        """
        Get recommended configuration based on environment
        """
        has_openai = cls.is_openai_available()
        
        if not has_openai:
            return {
                'strategy': 'local_only',
                'reason': 'No OpenAI API key configured',
                'cost': 'FREE',
                'quality': 'Medium',
                'speed': 'Fast'
            }
        else:
            return {
                'strategy': 'cost_optimized',
                'reason': 'Best balance of cost and quality',
                'cost': 'Very Low (~$0.50 per 1000 pages)',
                'quality': 'High',
                'speed': 'Medium'
            }


# ========================================================================
# USAGE EXAMPLE
# ========================================================================
"""
# Get active strategy
strategy = AIProviderConfig.get_active_strategy()
print(f"Using strategy: {strategy['name']}")
print(f"Methods: {strategy['methods']}")

# Estimate cost
cost = AIProviderConfig.estimate_cost(num_pages=100)
print(f"Processing 100 pages will cost: ${cost['total_cost']:.2f}")

# Get provider config
ocr_config = AIProviderConfig.get_provider_config('local_ocr')
print(f"OCR engine: {ocr_config['engine']}")

# Get recommendation
recommendation = AIProviderConfig.get_recommended_config()
print(f"Recommended: {recommendation['strategy']} - {recommendation['reason']}")
"""
