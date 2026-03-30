"""
Centralised AI provider configuration — soft-coded.

Edit AI_STRATEGY and TASK_PROVIDERS below to switch models without
touching any extraction code.

Supported provider IDs:
  'gemini'  — Google Gemini 2.0 Flash (cheap, 1M context, vision capable)
  'openai'  — OpenAI GPT-4o (more expensive, high accuracy)
  'ocr_only' — No LLM; rely solely on regex/OCR results

Per-task overrides in TASK_PROVIDERS take precedence over AI_STRATEGY.
"""

import os

# ─── Global default ──────────────────────────────────────────────────────────
# 'gemini' | 'openai' | 'ocr_only'
AI_STRATEGY = os.getenv('AI_STRATEGY', 'gemini')

# ─── Per-task provider (override AI_STRATEGY for specific tasks) ─────────────
# Tasks: 'pid_extraction' | 'hmb_extraction' | 'mov_mapping'
TASK_PROVIDERS = {
    'pid_extraction': os.getenv('AI_PID_PROVIDER', AI_STRATEGY),
    'hmb_extraction': os.getenv('AI_HMB_PROVIDER', AI_STRATEGY),
    'mov_mapping':    os.getenv('AI_MAPPER_PROVIDER', AI_STRATEGY),
}

# ─── Model names (edit here to upgrade without code changes) ─────────────────
GEMINI_VISION_MODEL  = os.getenv('GEMINI_VISION_MODEL',  'gemini-2.0-flash')
GEMINI_TEXT_MODEL    = os.getenv('GEMINI_TEXT_MODEL',    'gemini-2.0-flash')
OPENAI_VISION_MODEL  = os.getenv('OPENAI_VISION_MODEL',  'gpt-4o')
OPENAI_TEXT_MODEL    = os.getenv('OPENAI_TEXT_MODEL',    'gpt-4o')

# ─── Fallback chain per task ─────────────────────────────────────────────────
# If primary provider fails, try each in order, then give up.
# 'ocr_only' means: skip LLM, use regex/heuristic post-processing only.
FALLBACK_CHAIN = {
    'pid_extraction': ['gemini', 'openai', 'ocr_only'],
    'hmb_extraction': ['gemini', 'openai'],
    'mov_mapping':    ['gemini', 'openai'],
}


# ─── Public helpers ───────────────────────────────────────────────────────────

def get_provider(task: str) -> str:
    """Return the configured primary provider for a given task."""
    return TASK_PROVIDERS.get(task, AI_STRATEGY)


def get_fallback_chain(task: str) -> list:
    """Return ordered fallback list for a task, starting from the primary provider."""
    primary = get_provider(task)
    chain = FALLBACK_CHAIN.get(task, [primary])
    # Ensure primary is first
    ordered = [primary] + [p for p in chain if p != primary]
    return ordered


def build_gemini_client():
    """Return an initialised Gemini GenerativeModel client or None."""
    try:
        from google import genai  # google-genai SDK
        api_key = os.getenv('GEMINI_API_KEY') or os.getenv('GOOGLE_API_KEY')
        if not api_key:
            return None
        client = genai.Client(api_key=api_key)
        return client
    except Exception:
        return None


def build_openai_client():
    """Return an initialised OpenAI client or None."""
    try:
        from openai import OpenAI
        api_key = os.getenv('OPENAI_API_KEY')
        if not api_key:
            return None
        return OpenAI(api_key=api_key)
    except Exception:
        return None
