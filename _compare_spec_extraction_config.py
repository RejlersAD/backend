"""
Spec Customization Configuration Comparison Tool
=================================================
This script helps diagnose differences between local and production
extraction results by showing the active configuration values.

Run locally with: python _compare_spec_extraction_config.py
"""
import os
import sys
import django

# Setup Django
sys.path.insert(0, os.path.dirname(__file__))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from apps.spec_customization.services.config import SPEC_EXTRACTION_CONFIG
from apps.spec_customization.services.advanced_validation import ADVANCED_VALIDATION_CONFIG


def print_separator(title):
    """Print a nice section separator."""
    print("\n" + "=" * 80)
    print(f"  {title}")
    print("=" * 80)


def print_config():
    """Print current Spec Customization configuration."""
    
    print_separator("ENVIRONMENT INFORMATION")
    print(f"Environment: {os.getenv('ENVIRONMENT', 'development')}")
    print(f"Debug Mode: {os.getenv('DEBUG', 'True')}")
    print(f"Django Settings Module: {os.getenv('DJANGO_SETTINGS_MODULE', 'config.settings')}")
    
    print_separator("SPEC EXTRACTION CONFIG (from config.py)")
    
    # Chunking settings
    print("\n📄 CHUNKING SETTINGS:")
    print(f"  chunk_size_pages:        {SPEC_EXTRACTION_CONFIG['chunk_size_pages']} pages")
    print(f"  page_overlap:            {SPEC_EXTRACTION_CONFIG['page_overlap']} pages")
    print(f"  max_chunks_parallel:     {SPEC_EXTRACTION_CONFIG['max_chunks_parallel']}")
    
    # AI Engine settings
    print("\n🤖 AI ENGINE SETTINGS:")
    print(f"  ai_engines:              {', '.join(SPEC_EXTRACTION_CONFIG['ai_engines'])}")
    print(f"  gemini_model:            {SPEC_EXTRACTION_CONFIG['gemini_model']}")
    print(f"  openai_model:            {SPEC_EXTRACTION_CONFIG['openai_model']}")
    print(f"  openai_max_tokens:       {SPEC_EXTRACTION_CONFIG['openai_max_tokens']}")
    print(f"  gemini_temperature:      {SPEC_EXTRACTION_CONFIG['gemini_temperature']}")
    print(f"  openai_temperature:      {SPEC_EXTRACTION_CONFIG['openai_temperature']}")
    
    # Cost guard rails
    print("\n💰 COST GUARD RAILS:")
    print(f"  skip_ai_if_text_chars_gte: {SPEC_EXTRACTION_CONFIG['skip_ai_if_text_chars_gte']} chars")
    print(f"  max_ai_pages_per_job:      {SPEC_EXTRACTION_CONFIG['max_ai_pages_per_job']} pages")
    
    print_separator("ADVANCED VALIDATION CONFIG (from advanced_validation.py)")
    
    # Ensemble settings
    print("\n🔄 MULTI-MODEL ENSEMBLE:")
    ensemble_enabled = ADVANCED_VALIDATION_CONFIG.get('enable_ensemble_extraction', False)
    print(f"  enable_ensemble_extraction:   {ensemble_enabled}")
    if ensemble_enabled:
        print(f"    ✅ ENABLED - Gemini + OpenAI run in parallel")
        print(f"  ensemble_consensus_threshold: {ADVANCED_VALIDATION_CONFIG['ensemble_consensus_threshold']} (70%)")
        print(f"  ensemble_voting_strategy:     {ADVANCED_VALIDATION_CONFIG['ensemble_voting_strategy']}")
    else:
        print(f"    ❌ DISABLED - Using waterfall (sequential) mode only")
    
    # Validation settings
    print("\n✅ VALIDATION LAYERS:")
    print(f"  Component Count Validation:   {ADVANCED_VALIDATION_CONFIG['enable_component_count_validation']}")
    print(f"    min_components_per_class:   {ADVANCED_VALIDATION_CONFIG['min_components_per_class']}")
    print(f"    warn_if_components_below:   {ADVANCED_VALIDATION_CONFIG['warn_if_components_below']}")
    
    print(f"  Material Standard Validation: {ADVANCED_VALIDATION_CONFIG['enable_material_standard_validation']}")
    print(f"    known_standards_count:      {len(ADVANCED_VALIDATION_CONFIG['known_material_standards'])}")
    
    print(f"  Size Range Validation:        {ADVANCED_VALIDATION_CONFIG['enable_size_range_validation']}")
    print(f"  Template Comparison:          {ADVANCED_VALIDATION_CONFIG['enable_template_comparison']}")
    print(f"  Auto-Retry on Low Confidence: {ADVANCED_VALIDATION_CONFIG['enable_auto_retry']}")
    
    # API Keys check (without exposing the actual keys)
    print_separator("API KEYS STATUS")
    
    openai_key = os.getenv('OPENAI_API_KEY', '')
    gemini_key = os.getenv('GOOGLE_API_KEY', '') or os.getenv('GEMINI_API_KEY', '')
    
    print(f"  OpenAI API Key:  {'✅ Configured' if openai_key else '❌ Missing'}")
    if openai_key:
        print(f"    Key prefix:    {openai_key[:10]}...")
    
    print(f"  Gemini API Key:  {'✅ Configured' if gemini_key else '❌ Missing'}")
    if gemini_key:
        print(f"    Key prefix:    {gemini_key[:10]}...")
    
    # Environment variables that can override config
    print_separator("ENVIRONMENT VARIABLE OVERRIDES")
    
    env_overrides = [
        'SPEC_CHUNK_SIZE_PAGES',
        'SPEC_MAX_CHUNKS_PARALLEL',
        'SPEC_PAGE_OVERLAP',
    ]
    
    overrides_found = False
    for env_var in env_overrides:
        value = os.getenv(env_var)
        if value:
            print(f"  {env_var}: {value}")
            overrides_found = True
    
    if not overrides_found:
        print("  No environment variable overrides detected.")
        print("  All settings using defaults from config.py")
    
    print_separator("DIAGNOSTIC SUMMARY")
    
    # Calculate expected behavior
    print("\n📊 EXPECTED EXTRACTION BEHAVIOR:")
    
    if ensemble_enabled:
        print("  ✅ Multi-model ensemble ACTIVE")
        print("     → Both Gemini + OpenAI will process each chunk")
        print("     → Results merged with 70% consensus voting")
        print("     → Expected accuracy: 95-98%")
        print("     → Expected components: 50-200+ per class")
    else:
        print("  ⚠️  Waterfall mode ACTIVE (no ensemble)")
        print("     → Only one model processes each chunk (Gemini first)")
        print("     → Falls back to OpenAI on failure")
        print("     → Expected accuracy: 85-92%")
        print("     → May miss component categories")
    
    print(f"\n  Chunk size: {SPEC_EXTRACTION_CONFIG['chunk_size_pages']} pages")
    print(f"  Max tokens: {SPEC_EXTRACTION_CONFIG['openai_max_tokens']}")
    
    if SPEC_EXTRACTION_CONFIG['chunk_size_pages'] < 8:
        print("  ⚠️  WARNING: Chunk size <8 may fragment component tables!")
    
    if SPEC_EXTRACTION_CONFIG['openai_max_tokens'] < 12000:
        print("  ⚠️  WARNING: Max tokens <12000 may truncate large component lists!")
    
    # Check if both API keys are available
    if not (openai_key and gemini_key):
        print("\n  ❌ CRITICAL: Missing API keys!")
        if not gemini_key:
            print("     → Gemini API key missing - primary extraction will fail")
        if not openai_key:
            print("     → OpenAI API key missing - ensemble/fallback disabled")
    else:
        print("\n  ✅ All API keys configured")
    
    print("\n" + "=" * 80)


if __name__ == '__main__':
    try:
        print_config()
    except Exception as e:
        print(f"\n❌ ERROR: {e}")
        import traceback
        traceback.print_exc()
