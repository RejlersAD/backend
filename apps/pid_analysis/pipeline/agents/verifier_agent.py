"""
Verifier Agent - Controlled LLM for missed issues
Uses ONLY extracted data - NO hallucination
Uses MultiModelAIService for OpenAI + Gemini fallback
"""
import os
from typing import Dict, List, Any


class VerifierAgent:
    """
    Agent 1: Find missed issues ONLY from given data
    
    STRICT CONSTRAINTS:
    - Use ONLY data from extracted_data
    - DO NOT invent notes, specs, or references
    - DO NOT cross-contaminate from other documents
    - Uses MultiModelAIService for OpenAI (primary) + Gemini (fallback)
    """
    
    def __init__(self):
        # Import here to avoid circular imports
        from apps.pid_analysis.multi_model_service import MultiModelAIService
        self.ai_service = MultiModelAIService()
        print("[VERIFIER AGENT] Initialized with MultiModelAIService")
    
    def verify(
        self, 
        extracted_data: Dict[str, Any], 
        rule_issues: List[Dict[str, Any]],
        images_base64: List[str]
    ) -> List[Dict[str, Any]]:
        """
        Find additional issues missed by rules
        
        Args:
            extracted_data: Ground truth from extractor
            rule_issues: Issues already found by deterministic rules
            images_base64: P&ID images for visual verification
        
        Returns:
            Additional issues found (NOT in rule_issues)
        """
        print(f"[VERIFIER AGENT] Starting verification with ground truth data")
        
        # Build grounding context
        grounding = self._build_grounding_context(extracted_data)
        
        # Build prompt
        prompt = self._build_prompt(extracted_data, rule_issues, grounding)
        
        # Call LLM with STRICT constraints via MultiModelAIService
        messages = [
            {
                "role": "system",
                "content": self._get_system_prompt()
            },
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt}
                ]
            }
        ]
        
        # Add first image only (to avoid token limits)
        if images_base64:
            messages[1]["content"].append({
                "type": "image_url",
                "image_url": {
                    "url": f"data:image/png;base64,{images_base64[0]}"
                }
            })
        
        try:
            # Use MultiModelAIService - OpenAI first, falls back to Gemini on quota errors
            result_text = self.ai_service.chat_completion(
                messages=messages,
                model="auto",  # Let service decide (OpenAI priority)
                max_tokens=8000,
                temperature=0.0,  # DETERMINISTIC - mandatory for consistency
                use_vision=True
            )
            
            print(f"[VERIFIER AGENT] LLM response length: {len(result_text)}")
            
            # Parse issues from response
            additional_issues = self._parse_issues(result_text, extracted_data)
            print(f"[VERIFIER AGENT] Found {len(additional_issues)} additional issue(s)")
            
            return additional_issues
            
        except Exception as e:
            print(f"[VERIFIER AGENT ERROR] {e}")
            return []
    
    def _get_system_prompt(self) -> str:
        """System prompt with STRICT constraints"""
        return """You are a P&ID verification agent with STRICT constraints:

CRITICAL RULES:
1. Use ONLY data provided in the grounding context
2. DO NOT invent note numbers, spec references, or standards
3. DO NOT reference information from other documents
4. DO NOT hallucinate equipment or lines not in the extracted data
5. If you're not certain, DO NOT report the issue

Your task:
- Find REAL engineering issues missed by deterministic rules
- Verify against the provided ground truth data
- Report ONLY issues you can confirm from the given data

Format each issue as:
LINE: [line number or "Multiple"]
TYPE: [issue type]
DESC: [specific description]
REC: [recommendation]
"""
    
    def _build_grounding_context(self, extracted_data: Dict[str, Any]) -> str:
        """Build ground truth context for LLM"""
        lines = list(extracted_data.get('lines', set()))[:20]  # Limit for token economy
        equipment = list(extracted_data.get('equipment', set()))[:20]
        instruments = list(extracted_data.get('instruments', set()))[:20]
        notes = extracted_data.get('notes', {})
        deleted_notes = extracted_data.get('deleted_notes', set())
        spec_breaks = extracted_data.get('spec_breaks', [])
        
        context = f"""GROUND TRUTH DATA (SOURCE OF TRUTH):

Document ID: {extracted_data.get('document_id', 'N/A')}
Drawing Number: {extracted_data.get('drawing_number', 'N/A')}

LINE NUMBERS PRESENT ({len(extracted_data.get('lines', set()))} total):
{', '.join(lines[:20])}{"..." if len(lines) > 20 else ""}

EQUIPMENT TAGS PRESENT ({len(extracted_data.get('equipment', set()))} total):
{', '.join(equipment[:20])}{"..." if len(equipment) > 20 else ""}

INSTRUMENT TAGS PRESENT ({len(extracted_data.get('instruments', set()))} total):
{', '.join(instruments[:20])}{"..." if len(instruments) > 20 else ""}

ACTIVE NOTES: {', '.join(f'Note {k}' for k in notes.keys())}
DELETED NOTES (IGNORE): {', '.join(f'Note {n}' for n in deleted_notes)}

SPEC BREAKS PRESENT: {len(spec_breaks)}
REDUCERS DETECTED: {len(extracted_data.get('reducers', []))}

DO NOT reference any data NOT listed above.
"""
        return context
    
    def _build_prompt(
        self, 
        extracted_data: Dict[str, Any], 
        rule_issues: List[Dict[str, Any]],
        grounding: str
    ) -> str:
        """Build verification prompt"""
        existing_issues = '\n'.join([
            f"- {issue['line_number']}: {issue['issue_type']}"
            for issue in rule_issues[:10]
        ])
        
        return f"""{grounding}

ISSUES ALREADY FOUND BY DETERMINISTIC RULES:
{existing_issues or "None"}

YOUR TASK:
Review the P&ID image and the ground truth data above.
Find additional engineering issues that were NOT caught by the rules.

CONSTRAINTS:
- Use ONLY the line numbers, equipment, instruments, and notes listed in GROUND TRUTH
- DO NOT invent or reference notes not in the active notes list
- DO NOT reference deleted notes
- DO NOT assume information from other documents
- Look for: sizing issues, safety concerns, routing problems, spec compliance

Report each NEW issue in this format:
LINE: [line number]
TYPE: [issue type]
DESC: [description]
REC: [recommendation]
---
"""
    
    def _parse_issues(self, response_text: str, extracted_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Parse issues from LLM response"""
        issues = []
        
        # Split by separator
        blocks = response_text.split('---')
        
        for block in blocks:
            if not block.strip():
                continue
            
            issue = {}
            for line in block.strip().split('\n'):
                if line.startswith('LINE:'):
                    issue['line_number'] = line.replace('LINE:', '').strip()
                elif line.startswith('TYPE:'):
                    issue['issue_type'] = line.replace('TYPE:', '').strip()
                elif line.startswith('DESC:'):
                    issue['description'] = line.replace('DESC:', '').strip()
                elif line.startswith('REC:'):
                    issue['recommendation'] = line.replace('REC:', '').strip()
            
            # Only add if all fields present
            if all(k in issue for k in ['line_number', 'issue_type', 'description']):
                issue['confidence'] = 'medium'  # LLM is less certain than rules
                issue['source'] = 'verifier_agent'
                issues.append(issue)
        
        return issues
