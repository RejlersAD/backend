"""
CRS Comment Cleaner - Intelligent Text Cleaning with OpenAI
Uses GPT-3.5-turbo to intelligently clean and filter PDF comments
Soft-coded approach - rules are configurable without code changes
"""

import os
import re
import json
import logging
from typing import Optional, List, Tuple
from dataclasses import dataclass
from pathlib import Path

# Try to import OpenAI
try:
    from openai import OpenAI
    OPENAI_AVAILABLE = True
except ImportError:
    OPENAI_AVAILABLE = False

logger = logging.getLogger(__name__)

# Default config file path
DEFAULT_CONFIG_PATH = Path(__file__).parent.parent / 'config' / 'crs_config.json'


@dataclass
class CleaningResult:
    """Result of comment cleaning operation"""
    original_text: str
    cleaned_text: str
    should_skip: bool
    skip_reason: Optional[str] = None
    cleaning_method: str = "rule-based"  # "rule-based", "openai", "hybrid"


class CommentCleanerConfig:
    """
    Configuration for comment cleaning rules
    Loaded from JSON config file for soft-coding approach
    """
    
    DEFAULT_CONFIG = {
        # Names to recognize (Hindu, Muslim, Indian, Western)
        "common_names": {
            "hindu": [
                "Sreejith", "Rajeev", "Arun", "Vijay", "Suresh", "Ramesh", "Prakash",
                "Ganesh", "Harish", "Mahesh", "Naveen", "Pradeep", "Rajan", "Sanjay",
                "Ashok", "Dinesh", "Girish", "Krishna", "Mohan", "Narayan", "Pavan",
                "Rajesh", "Shiva", "Venkat", "Bhaskar", "Chandra", "Deepak", "Gautam"
            ],
            "muslim": [
                "Ahmed", "Ali", "Farhan", "Hassan", "Ibrahim", "Jameel", "Khalid",
                "Mahmood", "Nasir", "Omar", "Qasim", "Rahman", "Saleem", "Tariq",
                "Usman", "Waseem", "Yusuf", "Zaheer", "Imran", "Faisal", "Arif",
                "Bashir", "Danish", "Ehsan", "Faiz", "Ghulam", "Hamid", "Irfan"
            ],
            "indian": [
                "Agarwal", "Banerjee", "Chatterjee", "Das", "Ghosh", "Iyer", "Joshi",
                "Kumar", "Menon", "Nair", "Patel", "Rao", "Sharma", "Trivedi", "Verma",
                "Singh", "Gupta", "Reddy", "Shah", "Pillai", "Deshpande", "Kulkarni"
            ],
            "western": [
                "John", "William", "James", "Robert", "Michael", "David", "Richard",
                "Charles", "Thomas", "Daniel", "Matthew", "Andrew", "Christopher",
                "Joseph", "Edward", "Peter", "Mark", "Paul", "Steven", "Kevin",
                "Smith", "Johnson", "Williams", "Brown", "Jones", "Miller", "Davis"
            ]
        },
        
        # Patterns that indicate technical elements (should be SKIPPED)
        "skip_patterns": [
            r"^Typewriter\s*\d+\s*$",  # Just "Typewriter 166"
            r"^SHX\s*Text\s*$",  # SHX Text only
            r"^AutoCAD.*$",  # AutoCAD elements
            r"^\d+[\.\-]?\d*$",  # Pure numbers like "166" or "3.14"
            r"^[A-Z]{1,3}\s*\d+$",  # Codes like "EL 100", "P 42"
            r"^RACK\s*\.\s*\d+$",  # RACK.100
            r"^-\s*\d+[\.\-]?\d*$",  # -100.5
            r"^\/\s*\d+[\.\-]?\d*$",  # /100.5
        ],
        
        # Patterns to remove from START of comment
        "prefix_patterns": [
            r"^(Mr|Mrs|Ms|Dr|Prof|Miss|Sir|Madam)\s+",  # Titles
            r"^Typewriter\s*\d+\s+",  # Typewriter with number
            r"^SHX\s*Text\s+",  # SHX Text prefix
            r"^AutoCAD\s+SHX\s+Text\s+",  # AutoCAD SHX Text
            r"^(Text Box|Callout|Call Out|Free Text|Note|Highlight)\s+",  # Annotation types
        ],
        
        # Annotation type labels to remove
        "annotation_labels": [
            "Text Box", "Callout", "Call Out", "Free Text", "Note",
            "Highlight", "Underline", "Strikeout", "Squiggly",
            "Popup", "Rectangle", "Ellipse", "Line", "Polygon"
        ],
        
        # OpenAI configuration
        "openai": {
            "model": "gpt-3.5-turbo",
            "max_tokens": 150,
            "temperature": 0.1,
            "enabled": True,
            "fallback_to_rules": True
        },
        
        # Minimum meaningful comment length
        "min_comment_length": 5,
        
        # Maximum comment length (avoid processing very long texts)
        "max_comment_length": 2000
    }
    
    def __init__(self, config_path: Optional[str] = None):
        """
        Initialize configuration
        
        Args:
            config_path: Optional path to JSON config file
        """
        self.config = self.DEFAULT_CONFIG.copy()
        
        # Try to load from provided path or default path
        config_file = config_path or DEFAULT_CONFIG_PATH
        
        if config_file and os.path.exists(config_file):
            try:
                with open(config_file, 'r') as f:
                    full_config = json.load(f)
                    # Extract comment_cleaning section if present
                    if 'comment_cleaning' in full_config:
                        self._merge_config(full_config['comment_cleaning'])
                    else:
                        self._merge_config(full_config)
                logger.info(f"✅ Loaded CRS config from: {config_file}")
            except Exception as e:
                logger.warning(f"⚠️ Failed to load config from {config_file}: {e}")
    
    def _merge_config(self, custom_config: dict):
        """Merge custom config with defaults (deep merge)"""
        for key, value in custom_config.items():
            if isinstance(value, dict) and key in self.config and isinstance(self.config[key], dict):
                # Deep merge for nested dicts
                self.config[key].update(value)
            else:
                self.config[key] = value
    
    def get_all_names(self) -> List[str]:
        """Get all configured names"""
        names = []
        for category in self.config["common_names"].values():
            names.extend(category)
        return names
    
    def save_config(self, config_path: str):
        """Save current configuration to file"""
        with open(config_path, 'w') as f:
            json.dump(self.config, f, indent=2)


class CommentCleaner:
    """
    Intelligent comment cleaner with OpenAI integration
    
    Features:
    - Rule-based cleaning for common patterns
    - OpenAI GPT-3.5-turbo for intelligent text cleaning
    - Name recognition (Hindu, Muslim, Indian, Western)
    - Configurable via JSON config file
    """
    
    def __init__(self, config_path: Optional[str] = None):
        """
        Initialize comment cleaner
        
        Args:
            config_path: Optional path to JSON config file
        """
        self.config = CommentCleanerConfig(config_path)
        self.openai_client = None
        self._init_openai()
    
    def _init_openai(self):
        """Initialize OpenAI client if API key is available"""
        if not OPENAI_AVAILABLE:
            logger.info("OpenAI library not available, using rule-based cleaning only")
            return
            
        api_key = os.environ.get('OPENAI_API_KEY')
        if api_key and self.config.config["openai"]["enabled"]:
            try:
                self.openai_client = OpenAI(api_key=api_key)
                logger.info("✅ OpenAI client initialized successfully")
            except Exception as e:
                logger.warning(f"⚠️ Failed to initialize OpenAI client: {e}")
                self.openai_client = None
        else:
            if not api_key:
                logger.info("OPENAI_API_KEY not set, using rule-based cleaning only")
            else:
                logger.info("OpenAI is disabled in config, using rule-based cleaning only")
    
    def clean_comment(self, text: str) -> CleaningResult:
        """
        Clean a single comment text
        
        Strategy:
        1. Check if should be skipped (technical elements)
        2. Apply rule-based cleaning
        3. Use OpenAI for intelligent cleaning if available
        4. Validate result
        
        Args:
            text: Raw comment text
            
        Returns:
            CleaningResult with cleaned text or skip indication
        """
        if not text or not text.strip():
            return CleaningResult(
                original_text=text or "",
                cleaned_text="",
                should_skip=True,
                skip_reason="Empty comment"
            )
        
        text = text.strip()
        
        # Step 1: Check if should be skipped
        skip_check = self._should_skip(text)
        if skip_check[0]:
            return CleaningResult(
                original_text=text,
                cleaned_text="",
                should_skip=True,
                skip_reason=skip_check[1]
            )
        
        # Step 2: Apply rule-based cleaning first
        rule_cleaned = self._apply_rule_cleaning(text)
        
        # If rule cleaning already gives good result, return it
        if rule_cleaned and self._is_meaningful_comment(rule_cleaned):
            # Check if OpenAI cleaning would improve it
            if self.openai_client and len(rule_cleaned) > 20:
                try:
                    openai_cleaned = self._openai_clean(rule_cleaned)
                    if openai_cleaned and openai_cleaned != "SKIP":
                        return CleaningResult(
                            original_text=text,
                            cleaned_text=openai_cleaned,
                            should_skip=False,
                            cleaning_method="hybrid"
                        )
                except Exception as e:
                    logger.warning(f"OpenAI cleaning failed: {e}")
            
            return CleaningResult(
                original_text=text,
                cleaned_text=rule_cleaned,
                should_skip=False,
                cleaning_method="rule-based"
            )
        
        # Step 3: Try OpenAI for difficult cases
        if self.openai_client:
            try:
                openai_cleaned = self._openai_clean(text)
                if openai_cleaned == "SKIP":
                    return CleaningResult(
                        original_text=text,
                        cleaned_text="",
                        should_skip=True,
                        skip_reason="OpenAI determined as technical element",
                        cleaning_method="openai"
                    )
                if openai_cleaned and self._is_meaningful_comment(openai_cleaned):
                    return CleaningResult(
                        original_text=text,
                        cleaned_text=openai_cleaned,
                        should_skip=False,
                        cleaning_method="openai"
                    )
            except Exception as e:
                logger.warning(f"OpenAI cleaning failed: {e}")
        
        # Fallback to rule-based result
        if rule_cleaned and self._is_meaningful_comment(rule_cleaned):
            return CleaningResult(
                original_text=text,
                cleaned_text=rule_cleaned,
                should_skip=False,
                cleaning_method="rule-based"
            )
        
        # If nothing works, skip this comment
        return CleaningResult(
            original_text=text,
            cleaned_text="",
            should_skip=True,
            skip_reason="Could not extract meaningful content"
        )
    
    def _should_skip(self, text: str) -> Tuple[bool, Optional[str]]:
        """
        Check if comment should be skipped entirely
        
        Returns:
            Tuple of (should_skip, reason)
        """
        # Check against skip patterns
        for pattern in self.config.config["skip_patterns"]:
            if re.match(pattern, text.strip(), re.IGNORECASE):
                return (True, f"Matches skip pattern: {pattern}")
        
        # Check if too short
        if len(text.strip()) < self.config.config["min_comment_length"]:
            return (True, "Comment too short")
        
        # Check if purely technical (numbers, coordinates, etc.)
        if self._is_purely_technical(text):
            return (True, "Purely technical content")
        
        return (False, None)
    
    def _is_purely_technical(self, text: str) -> bool:
        """Check if text is purely technical (no real comment content)"""
        # Remove common prefixes
        cleaned = text.strip()
        
        # Pattern: "Name Typewriter NUMBER" with no additional content
        name_typewriter_pattern = r"^[A-Z][a-z]+\s+[A-Z][a-z]+\s+(Typewriter|Text Box|Callout)\s*\d*$"
        if re.match(name_typewriter_pattern, cleaned, re.IGNORECASE):
            return True
        
        # Pattern: Just annotation label with number
        annotation_only = r"^(Typewriter|Text Box|Callout|SHX Text|AutoCAD)\s*\d*$"
        if re.match(annotation_only, cleaned, re.IGNORECASE):
            return True
        
        # Pattern: Pure coordinate/dimension
        if re.match(r"^[\d\s\.\-\/\,]+$", cleaned):
            return True
        
        return False
    
    def _apply_rule_cleaning(self, text: str) -> str:
        """
        Apply rule-based cleaning
        
        Rules:
        1. Remove names at START only (keep if in middle/end)
        2. Remove Typewriter/annotation prefixes
        3. Clean up whitespace
        """
        cleaned = text.strip()
        original_length = len(cleaned)
        
        # Step 1: Remove annotation type prefixes with names
        # Pattern: "Name Name AnnotationType NUMBER actual_comment"
        all_names = self.config.get_all_names()
        
        for annotation in self.config.config["annotation_labels"]:
            # Pattern: "FirstName LastName AnnotationType number"
            pattern = rf"^([A-Z][a-z]+\s+[A-Z][a-z]+\s+)?{re.escape(annotation)}\s*\d*\s+"
            cleaned = re.sub(pattern, "", cleaned, flags=re.IGNORECASE)
        
        # Step 2: Remove name prefixes at start
        name_at_start = r"^([A-Z][a-z]+\s+[A-Z][a-z]+(\s+[A-Z][a-z]+)?)\s+"
        match = re.match(name_at_start, cleaned)
        if match:
            potential_name = match.group(1)
            words = potential_name.split()
            
            # Check if ALL words are recognized names
            if all(any(word.lower() == name.lower() for name in all_names) 
                   for word in words):
                # Remove name from start
                cleaned = cleaned[len(match.group(0)):].strip()
        
        # Step 3: Apply prefix patterns
        for pattern in self.config.config["prefix_patterns"]:
            cleaned = re.sub(pattern, "", cleaned, flags=re.IGNORECASE)
        
        # Step 4: Clean up whitespace
        cleaned = re.sub(r'\s+', ' ', cleaned).strip()
        
        # Step 5: Remove trailing annotation labels
        for annotation in self.config.config["annotation_labels"]:
            if cleaned.lower().endswith(annotation.lower()):
                cleaned = cleaned[:-len(annotation)].strip()
        
        # Safety check: if we removed too much, restore original
        if len(cleaned) < original_length * 0.2 and original_length > 20:
            return text.strip()
        
        return cleaned
    
    def _openai_clean(self, text: str) -> str:
        """
        Use OpenAI to intelligently clean comment text
        
        Args:
            text: Comment text to clean
            
        Returns:
            Cleaned text or "SKIP" if should be skipped
        """
        if not self.openai_client:
            return text
        
        prompt = f"""You are a PDF comment text cleaner for engineering documents.

TASK: Clean the following comment text extracted from a PDF annotation.

RULES:
1. Return "SKIP" if the text is ONLY technical/AutoCAD elements with NO actual comment content:
   - Just "Typewriter 166" = SKIP
   - Just "SHX Text" = SKIP
   - Pure numbers/coordinates like "100.5" = SKIP
   - Drawing codes with no comment = SKIP

2. REMOVE from START:
   - Person names (e.g., "Sreejith Rajeev", "John Smith")
   - Annotation types (e.g., "Typewriter 166", "Text Box", "Callout")
   - Title prefixes (Mr, Mrs, Dr, etc.)

3. KEEP names that appear IN THE MIDDLE or END of meaningful comments:
   - "Update design as requested by Sreejith" → KEEP as is
   - "Coordinate with John for approval" → KEEP as is

4. Preserve the ACTUAL COMMENT CONTENT exactly as written.

EXAMPLES:
- "Sreejith Rajeev Typewriter 166 Update design" → "Update design"
- "John Smith Text Box Check valve sizing" → "Check valve sizing"
- "Typewriter 166" → "SKIP"
- "Update the P&ID as discussed with Ahmed" → "Update the P&ID as discussed with Ahmed"
- "AutoCAD SHX Text" → "SKIP"
- "Please verify with Michael before finalizing" → "Please verify with Michael before finalizing"

INPUT TEXT:
{text}

OUTPUT (cleaned text or SKIP):"""

        try:
            response = self.openai_client.chat.completions.create(
                model=self.config.config["openai"]["model"],
                messages=[
                    {"role": "system", "content": "You clean PDF comment text. Respond with ONLY the cleaned text or 'SKIP'. No explanations."},
                    {"role": "user", "content": prompt}
                ],
                max_tokens=self.config.config["openai"]["max_tokens"],
                temperature=self.config.config["openai"]["temperature"]
            )
            
            result = response.choices[0].message.content.strip()
            
            # Handle SKIP response
            if result.upper() == "SKIP":
                return "SKIP"
            
            # Return cleaned text
            return result if result else text
            
        except Exception as e:
            logger.error(f"OpenAI API error: {e}")
            return text
    
    def _is_meaningful_comment(self, text: str) -> bool:
        """Check if text is a meaningful comment"""
        if not text or len(text.strip()) < self.config.config["min_comment_length"]:
            return False
        
        # Check if it's not just annotation labels
        text_lower = text.lower().strip()
        for annotation in self.config.config["annotation_labels"]:
            if text_lower == annotation.lower():
                return False
        
        return True
    
    def clean_comments_batch(self, comments: List[str]) -> List[CleaningResult]:
        """
        Clean a batch of comments
        
        Args:
            comments: List of comment texts
            
        Returns:
            List of CleaningResult objects
        """
        return [self.clean_comment(comment) for comment in comments]


# Singleton instance for easy access
_cleaner_instance = None


def get_comment_cleaner(config_path: Optional[str] = None) -> CommentCleaner:
    """Get or create comment cleaner singleton"""
    global _cleaner_instance
    if _cleaner_instance is None:
        _cleaner_instance = CommentCleaner(config_path)
    return _cleaner_instance


def clean_comment_text(text: str) -> Tuple[str, bool]:
    """
    Convenience function to clean a single comment
    
    Args:
        text: Comment text to clean
        
    Returns:
        Tuple of (cleaned_text, should_skip)
    """
    cleaner = get_comment_cleaner()
    result = cleaner.clean_comment(text)
    return (result.cleaned_text, result.should_skip)
