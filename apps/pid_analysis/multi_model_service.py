"""
Multi-Model AI Service - Supports OpenAI and Google Gemini
Provides unified interface for both models with automatic fallback
"""
import os
from typing import List, Dict, Any, Optional
from django.conf import settings
from openai import OpenAI
try:
    from google import genai as google_genai
    from google.genai import types as genai_types
    GENAI_SDK = 'new'
except ImportError:
    try:
        import google.generativeai as google_genai
        genai_types = None
        GENAI_SDK = 'legacy'
    except ImportError:
        google_genai = None
        genai_types = None
        GENAI_SDK = None


class MultiModelAIService:
    """
    Unified AI service supporting multiple providers
    - OpenAI (GPT-4o, GPT-4o-mini)
    - Google Gemini (gemini-1.5-pro, gemini-1.5-flash)
    """
    
    def __init__(self):
        """Initialize AI clients based on available API keys"""
        self.provider = os.getenv('AI_MODEL_PROVIDER', 'both').lower()
        
        # Initialize OpenAI
        self.openai_client = None
        openai_key = os.getenv('OPENAI_API_KEY') or getattr(settings, 'OPENAI_API_KEY', None)
        if openai_key and self.provider in ['openai', 'both']:
            self.openai_client = OpenAI(
                api_key=openai_key,
                timeout=180.0,
                max_retries=2
            )
            print("[AI SERVICE] ✅ OpenAI client initialized")
        
        # Initialize Gemini
        self.gemini_client = None
        self.gemini_api_key = None
        gemini_key = os.getenv('GEMINI_API_KEY') or getattr(settings, 'GEMINI_API_KEY', None)
        if gemini_key and self.provider in ['gemini', 'both'] and google_genai:
            try:
                if GENAI_SDK == 'new':
                    # New google-genai SDK: use Client object
                    self.gemini_client = google_genai.Client(api_key=gemini_key)
                else:
                    # Legacy google-generativeai SDK
                    google_genai.configure(api_key=gemini_key)
                    self.gemini_client = google_genai
                self.gemini_api_key = gemini_key
                print(f"[AI SERVICE] ✅ Gemini client initialized (SDK: {GENAI_SDK}, using stable 2.0-flash)")
            except Exception as e:
                print(f"[AI SERVICE] ⚠️  Gemini initialization failed: {e}")
                if self.provider == 'gemini':
                    raise
        
        if not self.openai_client and not self.gemini_client:
            raise ValueError("No AI API keys configured. Set OPENAI_API_KEY or GEMINI_API_KEY")
        
        print(f"[AI SERVICE] Provider mode: {self.provider}")
    
    def chat_completion(
        self,
        messages: List[Dict[str, Any]],
        model: str = "auto",
        max_tokens: int = 4000,
        temperature: float = 0.3,
        use_vision: bool = False
    ) -> str:
        """
        Unified chat completion across providers
        
        Args:
            messages: List of message dicts with 'role' and 'content'
            model: 'auto', 'openai', 'gemini', or specific model name
            max_tokens: Maximum tokens in response
            temperature: Sampling temperature (0.0-1.0)
            use_vision: Whether to use vision-capable models
        
        Returns:
            str: AI response text
        """
        
        # Model selection logic
        if model == "auto":
            if self.provider == "openai" or (self.provider == "both" and self.openai_client):
                model = "openai"
            elif self.provider == "gemini" or (self.provider == "both" and self.gemini_client):
                model = "gemini"
        
        # Route to appropriate provider
        if model.startswith("gpt") or model == "openai":
            if not self.openai_client:
                print("[AI SERVICE] OpenAI not available, falling back to Gemini")
                return self._gemini_completion(messages, max_tokens, temperature, use_vision)
            return self._openai_completion(messages, model, max_tokens, temperature, use_vision)
        
        elif model.startswith("gemini") or model == "gemini":
            if not self.gemini_client:
                print("[AI SERVICE] Gemini not available, falling back to OpenAI")
                return self._openai_completion(messages, "gpt-4o-mini", max_tokens, temperature, use_vision)
            return self._gemini_completion(messages, max_tokens, temperature, use_vision)
        
        # Default fallback
        if self.openai_client:
            return self._openai_completion(messages, "gpt-4o-mini", max_tokens, temperature, use_vision)
        else:
            return self._gemini_completion(messages, max_tokens, temperature, use_vision)
    
    def _openai_completion(
        self,
        messages: List[Dict[str, Any]],
        model: str,
        max_tokens: int,
        temperature: float,
        use_vision: bool
    ) -> str:
        """OpenAI completion"""
        try:
            # Select model
            if model == "openai" or model == "auto":
                model = "gpt-4o" if use_vision else "gpt-4o-mini"
            
            print(f"[OPENAI] Using model: {model}")
            
            # DETERMINISM: Use seed=42 for reproducible outputs
            response = self.openai_client.chat.completions.create(
                model=model,
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
                seed=42,  # Reproducible outputs
                timeout=180.0
            )
            
            result = response.choices[0].message.content or ""
            print(f"[OPENAI] Response length: {len(result)} chars")
            return result.strip()
            
        except Exception as e:
            error_str = str(e)
            
            # Check for 429 quota exceeded error
            if "429" in error_str or "quota" in error_str.lower() or "insufficient_quota" in error_str.lower():
                print(f"[OPENAI ERROR] ❌ 429 - QUOTA EXCEEDED")
                print(f"[OPENAI ERROR] Full error: {error_str}")
                print(f"[OPENAI ERROR] ⚠️ OpenAI API key has exceeded quota or billing limit")
                print(f"[OPENAI ERROR] 💡 Please add credits or update API key in .env file")
                
                # Attempt Gemini fallback if available
                if self.gemini_client:
                    print("[OPENAI] 🔄 Attempting fallback to Gemini (one-time)...")
                    try:
                        result = self._gemini_completion(messages, max_tokens, temperature, use_vision)
                        print("[OPENAI] ✅ Gemini fallback successful")
                        return result
                    except Exception as gemini_error:
                        print(f"[GEMINI ERROR] ❌ Fallback failed: {gemini_error}")
                        raise Exception(f"Both models failed. OpenAI quota exceeded, Gemini error: {str(gemini_error)}")
                else:
                    raise Exception(f"OpenAI quota exceeded (429) and no Gemini fallback available")
            
            # Other OpenAI errors (not quota-related)
            print(f"[OPENAI ERROR] {e}")
            raise Exception(f"OpenAI API error: {str(e)}")
    
    def _gemini_completion(
        self,
        messages: List[Dict[str, Any]],
        max_tokens: int,
        temperature: float,
        use_vision: bool
    ) -> str:
        """Google Gemini completion"""
        try:
            # Use stable Gemini 2.0-flash (fast, supports vision, widely available)
            model_name = "gemini-2.0-flash"
            print(f"[GEMINI] Using model: {model_name} (SDK: {GENAI_SDK})")
            
            # Collect all text parts and image parts
            prompt_parts = []
            for msg in messages:
                content = msg['content']
                if isinstance(content, list):
                    for item in content:
                        if item['type'] == 'text':
                            prompt_parts.append(item['text'])
                        elif item['type'] == 'image_url':
                            url = item['image_url']['url']
                            if url.startswith('data:'):
                                # Base64 image — decode for new SDK
                                import base64
                                header, b64data = url.split(',', 1)
                                mime = header.split(':')[1].split(';')[0]
                                if GENAI_SDK == 'new' and genai_types:
                                    img_bytes = base64.b64decode(b64data)
                                    prompt_parts.append(
                                        google_genai.types.Part.from_bytes(data=img_bytes, mime_type=mime)
                                    )
                                else:
                                    # Legacy SDK: pass raw base64 url string
                                    prompt_parts.append(url)
                            else:
                                prompt_parts.append(url)
                else:
                    if content:
                        prompt_parts.append(content)
            
            if GENAI_SDK == 'new':
                # New google-genai SDK
                response = self.gemini_client.models.generate_content(
                    model=model_name,
                    contents=prompt_parts,
                    config=google_genai.types.GenerateContentConfig(
                        max_output_tokens=max_tokens,
                        temperature=temperature,
                    )
                )
                result = response.text or ""
            else:
                # Legacy google-generativeai SDK
                model = self.gemini_client.GenerativeModel(model_name)
                generation_config = {
                    'max_output_tokens': max_tokens,
                    'temperature': temperature,
                }
                response = model.generate_content(prompt_parts, generation_config=generation_config)
                result = response.text or ""
            
            print(f"[GEMINI] Response length: {len(result)} chars")
            return result.strip()
            
        except Exception as e:
            error_str = str(e)
            
            # Check for model not found (404) error
            if "404" in error_str or "not found" in error_str.lower():
                print(f"[GEMINI ERROR] ❌ 404 - MODEL NOT FOUND")
                print(f"[GEMINI ERROR] Full error: {error_str}")
                print(f"[GEMINI ERROR] ⚠️ The requested Gemini model is unavailable")
                print(f"[GEMINI ERROR] 💡 Using stable models: gemini-1.5-pro / gemini-1.5-flash")
            elif "429" in error_str or "quota" in error_str.lower():
                print(f"[GEMINI ERROR] ❌ 429 - QUOTA EXCEEDED")
                print(f"[GEMINI ERROR] Full error: {error_str}")
                print(f"[GEMINI ERROR] ⚠️ Gemini API quota or rate limit exceeded")
            else:
                print(f"[GEMINI ERROR] ❌ {error_str}")
            
            # NO FALLBACK back to OpenAI — prevents infinite loops
            raise Exception(f"Gemini API error: {str(e)}")
    
    def vision_analysis(
        self,
        images_base64: List[str],
        prompt: str,
        model: str = "auto",
        max_tokens: int = 4000,
        temperature: float = 0.3
    ) -> str:
        """
        Vision analysis with images
        
        Args:
            images_base64: List of base64-encoded images
            prompt: Text prompt for analysis
            model: Model to use ('auto', 'openai', 'gemini')
            max_tokens: Maximum response tokens
            temperature: Sampling temperature
        
        Returns:
            str: Analysis result
        """
        # Build message with images
        content = [{"type": "text", "text": prompt}]
        
        for img_b64 in images_base64:
            content.append({
                "type": "image_url",
                "image_url": {
                    "url": f"data:image/png;base64,{img_b64}",
                    "detail": "high"
                }
            })
        
        messages = [{"role": "user", "content": content}]
        
        return self.chat_completion(
            messages=messages,
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            use_vision=True
        )
