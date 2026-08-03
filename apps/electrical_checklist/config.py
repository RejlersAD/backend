"""
SOFT-CODED: Electrical Checklist Template Configuration
Backend mirror of frontend config for validation and extraction
"""

CHECKLIST_TEMPLATE = {
    "id": "ups_battery_inspection",
    "name": "UPS/Battery System Inspection Checklist",
    "version": "1.0",
    "category": "Electrical",
    "description": "Comprehensive inspection checklist for UPS and battery systems",
    
    "sections": [
        {
            "id": "general_site_info",
            "order": 1,
            "name": "General Site Information",
            "fields": [
                {"key": "site_name", "label": "Site", "type": "text", "highlighted": True},
                {"key": "area_facility", "label": "Area / Facility", "type": "text", "highlighted": True},
                {"key": "substation", "label": "Substation / DB / MDB", "type": "text", "highlighted": True},
                {"key": "ups_room", "label": "UPS Room / Battery Room", "type": "text", "highlighted": True},
                {"key": "date_visit", "label": "Date of Visit", "type": "date", "highlighted": True},
                {"key": "attendance", "label": "Attendance (Site / Name / FEED)", "type": "text", "highlighted": True},
                {"key": "taking_industries", "label": "Taking Industries Completed", "type": "checkbox", "highlighted": False}
            ]
        },
        {
            "id": "ups_identification",
            "order": 2,
            "name": "UPS Identification (One Row Per UPS)",
            "fields": [
                {"key": "ups_tag", "label": "UPS Tag", "type": "text", "highlighted": True},
                {"key": "ups_type", "label": "UPS Type (AC / DC / Static)", "type": "select", "options": ["AC", "DC", "Static"], "highlighted": True},
                {"key": "application", "label": "Application (DCS / ESD / ICSS)", "type": "text", "highlighted": True},
                {"key": "ups_make_model", "label": "UPS Make & Model", "type": "text", "highlighted": True},
                {"key": "rated_capacity", "label": "Rated Capacity (kVA / A)", "type": "number", "highlighted": True}
            ]
        },
        {
            "id": "loading_data",
            "order": 3,
            "name": "Loading Data",
            "fields": [
                {"key": "pressure_operating_load", "label": "Pressure Operating Load (% / kVA / A)", "type": "text", "highlighted": True},
                {"key": "load_measurement", "label": "Load Measurement Source", "type": "text", "highlighted": True},
                {"key": "highest_observed_load", "label": "Highest Observed Load Encountered", "type": "text", "highlighted": True},
                {"key": "operating_condition", "label": "Operating Condition During Peak Load", "type": "text", "highlighted": True},
                {"key": "load_margin", "label": "Load Margin Adequate (<70%)", "type": "text", "highlighted": True}
            ]
        },
        {
            "id": "battery_system",
            "order": 4,
            "name": "Battery System Data",
            "fields": [
                {"key": "battery_make", "label": "Battery Make", "type": "text", "highlighted": True},
                {"key": "rating_voltage", "label": "Rating & Voltage", "type": "text", "highlighted": True},
                {"key": "battery_model", "label": "Battery Model", "type": "text", "highlighted": True},
                {"key": "type_manufacturer", "label": "Type of Manufacturer", "type": "text", "highlighted": True},
                {"key": "battery_ah_rating", "label": "Battery Ah Rating", "type": "number", "highlighted": True},
                {"key": "number_cells", "label": "Number of Cells", "type": "number", "highlighted": True},
                {"key": "battery_installation_year", "label": "Battery Installation Year", "type": "year", "highlighted": True},
                {"key": "battery_physical_condition", "label": "Battery Physical Condition", "type": "textarea", "highlighted": True},
                {"key": "design_life", "label": "Design Life and Replacement Year", "type": "text", "highlighted": True},
                {"key": "battery_disconnect", "label": "Battery Disconnect Panel on Capacity Test", "type": "textarea", "highlighted": True}
            ]
        },
        {
            "id": "feeder_ratings_protection",
            "order": 5,
            "name": "Feeder Ratings & Protection",
            "fields": [
                {"key": "normal_feeder_rating", "label": "Normal Input Feeder Rating (A)", "type": "number", "highlighted": True},
                {"key": "bypass_feeder_rating", "label": "Bypass Feeder Rating (A)", "type": "number", "highlighted": True},
                {"key": "ups_output_feeder", "label": "UPS Output Feeder Rating (A)", "type": "number", "highlighted": True},
                {"key": "downstream_db_ratings", "label": "Downstream DB Feeder Ratings", "type": "text", "highlighted": True},
                {"key": "feeder_coordination", "label": "Feeder Coordination OK", "type": "select", "options": ["Yes", "No"], "highlighted": True}
            ]
        },
        {
            "id": "signatures",
            "order": 14,
            "name": "Sign Data / Signatures",
            "is_signature_section": True,
            "extract_signatures": True,
            "fields": [
                {"key": "signature_1", "label": "Signature 1", "type": "signature"},
                {"key": "signature_2", "label": "Signature 2", "type": "signature"},
                {"key": "signature_3", "label": "Signature 3", "type": "signature"}
            ]
        }
    ]
}

# OCR Extraction Configuration (FREE EXTRACTORS FIRST!)
OCR_CONFIG = {
    # Primary extraction methods (FREE)
    "enable_tesseract": True,      # Tesseract OCR (fastest, free)
    "enable_easyocr": True,        # EasyOCR (ML-based, free, better accuracy)
    "enable_pymupdf_text": True,   # Extract embedded PDF text (instant, free)
    
    # Fallback to paid AI (OPTIONAL - disabled by default)
    "enable_ai_fallback": False,   # Set True to use Gemini/GPT-4o when OCR fails
    
    # OCR settings
    "tesseract_config": "--oem 3 --psm 6",  # OCR Engine Mode 3, Page Segmentation Mode 6
    "tesseract_lang": "eng",                # Language
    "easyocr_gpu": False,                   # Use GPU for EasyOCR (faster but needs GPU)
    "easyocr_languages": ["en"],            # Languages to detect
    
    # Image preprocessing
    "enhance_contrast": True,
    "enhance_sharpness": True,
    "dpi": 300,  # Resolution for PDF to image conversion
    
    # Signature detection (CV-based, FREE)
    "signature_detection_method": "opencv",  # opencv (free) or ai (paid)
    "signature_min_width": 100,
    "signature_max_width": 400,
    "signature_min_height": 30,
    "signature_max_height": 150,
    "signature_min_area": 3000,
    
    # Field matching thresholds
    "min_confidence_threshold": 60,  # Minimum OCR confidence to accept
    "field_match_threshold": 0.7,    # Fuzzy matching threshold for field labels
    
    # Performance
    "max_file_size_mb": 50,
    "supported_formats": [".pdf"],
    "timeout_seconds": 300
}

# AI Extraction Configuration (FALLBACK ONLY)
EXTRACTION_CONFIG = {
    "primary_engine": "gemini",  # Gemini 2.0 Flash (only if ai_fallback enabled)
    "fallback_engine": "openai",  # GPT-4o (only if ai_fallback enabled)
    "extract_highlighted_only": False,
    "extract_signatures": True,
    "signature_detection_threshold": 0.7,
    "max_file_size_mb": 50,
    "supported_formats": [".pdf"],
    "timeout_seconds": 300
}

# Soft-coded AI prompts
EXTRACTION_PROMPTS = {
    "system": """You are an expert electrical inspection checklist analyzer. 
Extract data from UPS/Battery System inspection checklists accurately.
Pay special attention to highlighted/yellow fields and signature areas.""",
    
    "field_extraction": """Analyze this page from an electrical inspection checklist.
Extract the following field data in JSON format:

{field_list}

For each field, provide:
- value: extracted text/data
- confidence: 0-100 score
- is_highlighted: boolean (if field has yellow background)
- page_number: which page it was found on

Return only valid JSON with no additional text.""",
    
    "signature_detection": """Detect and locate signatures in this document page.
For each signature found, provide:
- location: bounding box coordinates
- confidence: detection confidence 0-100
- type: handwritten/digital
- associated_label: nearby text label (e.g., "Inspector", "Approved by")

Return as JSON array."""
}
