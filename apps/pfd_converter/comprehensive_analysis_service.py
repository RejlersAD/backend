"""
PFD Comprehensive Analysis Service
===================================

Soft-coded, configurable service for comprehensive PFD analysis using GPT-4 Vision.
Automatically extracts equipment, piping, instrumentation, and all technical details.

Configuration-driven approach:
- Analysis patterns defined in config
- Reusable prompts
- Structured output format
- Automatic integration with upload flow
"""

import openai
from openai import OpenAI
from decouple import config
import json
import base64
import fitz  # PyMuPDF
from PIL import Image
import io
import os
import logging
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# Initialize OpenAI
OPENAI_API_KEY = config('OPENAI_API_KEY', default='')
openai_client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY and OPENAI_API_KEY != '' else None


# Soft-coded configuration for analysis patterns
ANALYSIS_CONFIG = {
    "extraction_categories": [
        {
            "name": "equipment",
            "description": "All equipment items (vessels, pumps, heat exchangers, reactors, tanks, columns, drums, filters, compressors, etc.)",
            "attributes": [
                "tag", "type", "name", "design_temperature", "design_pressure",
                "dimensions", "material", "capacity", "duty", "specifications"
            ]
        },
        {
            "name": "piping_lines",
            "description": "All piping and line connections",
            "attributes": [
                "line_number", "size", "class", "material", "specification",
                "from_equipment", "to_equipment", "flow_direction", "description"
            ]
        },
        {
            "name": "instruments",
            "description": "All instrumentation (transmitters, indicators, controllers, switches, valves)",
            "attributes": [
                "tag", "type", "function", "location", "range", "set_point",
                "control_loop", "interlock", "alarm_settings"
            ]
        },
        {
            "name": "valves",
            "description": "All valves (control, isolation, safety, check, etc.)",
            "attributes": [
                "tag", "type", "size", "actuation", "fail_position",
                "special_notes", "control_system"
            ]
        },
        {
            "name": "safety_devices",
            "description": "All safety equipment (PSV, rupture discs, ESD valves, flame arrestors)",
            "attributes": [
                "tag", "type", "set_pressure", "size", "capacity",
                "discharge_location", "relieving_scenario"
            ]
        },
        {
            "name": "streams",
            "description": "Process streams and connections",
            "attributes": [
                "stream_name", "stream_number", "from_location", "to_location",
                "temperature", "pressure", "flow_rate", "composition", "phase"
            ]
        }
    ],
    
    "analysis_levels": {
        "detailed": {
            "extract_specifications": True,
            "extract_notes": True,
            "extract_design_basis": True,
            "extract_operating_conditions": True,
            "max_tokens": 4000
        },
        "standard": {
            "extract_specifications": True,
            "extract_notes": True,
            "extract_design_basis": False,
            "extract_operating_conditions": True,
            "max_tokens": 3000
        },
        "quick": {
            "extract_specifications": False,
            "extract_notes": False,
            "extract_design_basis": False,
            "extract_operating_conditions": False,
            "max_tokens": 2000
        }
    },
    
    "gpt4_vision_settings": {
        "model": "gpt-4o",
        "temperature": 0.1,
        "image_detail": "high",
        "dpi": 200
    }
}


class ComprehensivePFDAnalyzer:
    """
    Comprehensive PFD analysis service with soft-coded patterns
    Automatically extracts all technical information from PFD drawings
    """
    
    def __init__(self, analysis_level: str = "detailed"):
        self.client = openai_client
        self.config = ANALYSIS_CONFIG
        self.analysis_level = analysis_level
        self.level_config = self.config["analysis_levels"].get(analysis_level, self.config["analysis_levels"]["detailed"])
    
    def analyze_pfd_file(self, pfd_file_path: str, document_info: Dict = None) -> Dict:
        """
        Main entry point: Analyze PFD file comprehensively
        
        Args:
            pfd_file_path: Path to PFD PDF file
            document_info: Optional metadata (drawing number, project, etc.)
            
        Returns:
            Comprehensive analysis dictionary with all extracted data
        """
        logger.info(f"🔍 Starting comprehensive PFD analysis: {pfd_file_path}")
        
        if not self.client:
            logger.warning("⚠️ OpenAI client not configured, using basic extraction")
            return self._basic_analysis_fallback(pfd_file_path, document_info)
        
        try:
            # Step 1: Convert PDF to images
            images = self._convert_pdf_to_images(pfd_file_path)
            
            # Step 2: Analyze each page
            all_page_analyses = []
            for page_num, image in enumerate(images, 1):
                logger.info(f"   Analyzing page {page_num}/{len(images)}...")
                page_analysis = self._analyze_page_with_vision(image, page_num)
                all_page_analyses.append(page_analysis)
            
            # Step 3: Combine and structure results
            comprehensive_report = self._structure_analysis(all_page_analyses, document_info)
            
            logger.info(f"✅ Analysis complete:")
            logger.info(f"   - Equipment: {len(comprehensive_report['all_equipment'])}")
            logger.info(f"   - Piping Lines: {len(comprehensive_report['all_piping'])}")
            logger.info(f"   - Instruments: {len(comprehensive_report['all_instruments'])}")
            
            return comprehensive_report
            
        except Exception as e:
            logger.error(f"❌ PFD analysis failed: {str(e)}", exc_info=True)
            return self._basic_analysis_fallback(pfd_file_path, document_info)
    
    def _convert_pdf_to_images(self, pdf_path: str) -> List[Image.Image]:
        """Convert PDF pages to PIL Images using PyMuPDF"""
        try:
            pdf_document = fitz.open(pdf_path)
            images = []
            
            dpi = self.config["gpt4_vision_settings"]["dpi"]
            zoom = dpi / 72  # 72 is default DPI
            
            for page_num in range(pdf_document.page_count):
                page = pdf_document[page_num]
                pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom))
                img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
                images.append(img)
            
            pdf_document.close()
            logger.info(f"   ✅ Converted {len(images)} page(s) at {dpi} DPI")
            return images
            
        except Exception as e:
            logger.error(f"   ❌ PDF conversion failed: {e}")
            raise
    
    def _analyze_page_with_vision(self, image: Image.Image, page_num: int) -> Dict:
        """Analyze single page using GPT-4 Vision with soft-coded prompts"""
        
        # Save image to bytes
        img_byte_arr = io.BytesIO()
        image.save(img_byte_arr, format='PNG')
        img_byte_arr.seek(0)
        image_data = base64.b64encode(img_byte_arr.read()).decode('utf-8')
        
        # Build dynamic prompt from configuration
        prompt = self._build_analysis_prompt(page_num)
        
        try:
            response = self.client.chat.completions.create(
                model=self.config["gpt4_vision_settings"]["model"],
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:image/png;base64,{image_data}",
                                    "detail": self.config["gpt4_vision_settings"]["image_detail"]
                                }
                            }
                        ]
                    }
                ],
                max_tokens=self.level_config["max_tokens"],
                temperature=self.config["gpt4_vision_settings"]["temperature"]
            )
            
            content = response.choices[0].message.content
            analysis = self._parse_json_response(content)
            
            return analysis
            
        except Exception as e:
            logger.error(f"   ❌ Vision analysis failed for page {page_num}: {e}")
            return {}
    
    def _build_analysis_prompt(self, page_num: int) -> str:
        """Build analysis prompt dynamically from configuration"""
        
        prompt = f"""Analyze this Process Flow Diagram (PFD) - Page {page_num} in EXTREME DETAIL.

Extract EVERY piece of information visible on this drawing.

"""
        
        # Add extraction categories from config
        for idx, category in enumerate(self.config["extraction_categories"], 1):
            prompt += f"""**{idx}. {category['name'].upper().replace('_', ' ')}**
{category['description']}

Extract for EACH item:
"""
            for attr in category['attributes']:
                prompt += f"- {attr.replace('_', ' ').title()}\n"
            prompt += "\n"
        
        # Add conditional extraction based on level
        if self.level_config["extract_notes"]:
            prompt += """**DRAWING NOTES & SPECIFICATIONS**
- All numbered notes
- Design standards (ASME, API, ISA, etc.)
- Special requirements
- Material specifications
- Operating conditions

"""
        
        if self.level_config["extract_design_basis"]:
            prompt += """**DESIGN BASIS**
- Design philosophy
- Safety considerations
- Process requirements
- Performance criteria

"""
        
        # Add output format
        prompt += """**OUTPUT FORMAT** (JSON):
```json
{
  "page": """ + str(page_num) + """,
  "drawing_info": {
    "drawing_number": "",
    "title": "",
    "revision": "",
    "date": "",
    "project": "",
    "client": ""
  },
"""
        
        # Add JSON structure for each category
        for category in self.config["extraction_categories"]:
            prompt += f'  "{category["name"]}": [],\n'
        
        if self.level_config["extract_notes"]:
            prompt += '  "notes": [],\n'
        
        prompt += """  "standards": []
}
```

Be EXTREMELY thorough. Extract EVERY visible detail, number, and specification."""
        
        return prompt
    
    def _parse_json_response(self, content: str) -> Dict:
        """Parse JSON from GPT-4 response (handles markdown code blocks)"""
        try:
            if '```json' in content:
                json_start = content.find('```json') + 7
                json_end = content.find('```', json_start)
                json_str = content[json_start:json_end].strip()
            elif '```' in content:
                json_start = content.find('```') + 3
                json_end = content.find('```', json_start)
                json_str = content[json_start:json_end].strip()
            else:
                json_str = content.strip()
            
            return json.loads(json_str)
        except Exception as e:
            logger.error(f"Failed to parse JSON response: {e}")
            return {}
    
    def _structure_analysis(self, all_pages: List[Dict], document_info: Dict = None) -> Dict:
        """Combine multi-page analysis into structured comprehensive report"""
        
        report = {
            "analysis_version": "1.0",
            "analysis_level": self.analysis_level,
            "document_info": document_info or {},
            "total_pages": len(all_pages),
            "drawing_info": {},
            "summaries": {},
            "all_equipment": [],
            "all_piping": [],
            "all_instruments": [],
            "all_valves": [],
            "all_safety_devices": [],
            "all_streams": [],
            "all_notes": [],
            "standards": []
        }
        
        # Aggregate data from all pages
        for page_data in all_pages:
            if not page_data:
                continue
            
            # Drawing info from first page
            if not report["drawing_info"] and page_data.get("drawing_info"):
                report["drawing_info"] = page_data["drawing_info"]
            
            # Collect all items
            for category in self.config["extraction_categories"]:
                category_name = category["name"]
                category_key = f"all_{category_name}"
                if category_key in report:
                    report[category_key].extend(page_data.get(category_name, []))
            
            # Notes and standards
            report["all_notes"].extend(page_data.get("notes", []))
            report["standards"].extend(page_data.get("standards", []))
        
        # Generate summaries
        report["summaries"] = self._generate_summaries(report)
        
        return report
    
    def _generate_summaries(self, report: Dict) -> Dict:
        """Generate statistical summaries from extracted data"""
        
        summaries = {}
        
        # Equipment summary
        summaries["equipment"] = {
            "total_count": len(report["all_equipment"]),
            "by_type": {}
        }
        for eq in report["all_equipment"]:
            eq_type = eq.get("type", "unknown")
            summaries["equipment"]["by_type"][eq_type] = \
                summaries["equipment"]["by_type"].get(eq_type, 0) + 1
        
        # Piping summary
        summaries["piping"] = {
            "total_lines": len(report["all_piping"]),
            "line_sizes": list(set([p.get("size", "") for p in report["all_piping"] if p.get("size")])),
            "piping_classes": list(set([p.get("class", "") for p in report["all_piping"] if p.get("class")])),
            "materials": list(set([p.get("material", "") for p in report["all_piping"] if p.get("material")]))
        }
        
        # Instrumentation summary
        summaries["instrumentation"] = {
            "total_instruments": len(report["all_instruments"]),
            "by_type": {},
            "by_function": {}
        }
        for inst in report["all_instruments"]:
            inst_type = inst.get("type", "unknown")
            inst_func = inst.get("function", "unknown")
            summaries["instrumentation"]["by_type"][inst_type] = \
                summaries["instrumentation"]["by_type"].get(inst_type, 0) + 1
            summaries["instrumentation"]["by_function"][inst_func] = \
                summaries["instrumentation"]["by_function"].get(inst_func, 0) + 1
        
        # Safety devices summary
        summaries["safety"] = {
            "total_devices": len(report["all_safety_devices"]),
            "by_type": {}
        }
        for safety in report["all_safety_devices"]:
            safety_type = safety.get("type", "unknown")
            summaries["safety"]["by_type"][safety_type] = \
                summaries["safety"]["by_type"].get(safety_type, 0) + 1
        
        return summaries
    
    def _basic_analysis_fallback(self, pfd_file_path: str, document_info: Dict = None) -> Dict:
        """Fallback analysis when GPT-4 Vision is not available"""
        logger.warning("Using basic fallback analysis (GPT-4 Vision not available)")
        
        return {
            "analysis_version": "1.0",
            "analysis_level": "fallback",
            "document_info": document_info or {},
            "drawing_info": {},
            "summaries": {
                "equipment": {"total_count": 0, "by_type": {}},
                "piping": {"total_lines": 0, "line_sizes": [], "piping_classes": []},
                "instrumentation": {"total_instruments": 0, "by_type": {}},
                "safety": {"total_devices": 0, "by_type": {}}
            },
            "all_equipment": [],
            "all_piping": [],
            "all_instruments": [],
            "all_valves": [],
            "all_safety_devices": [],
            "all_streams": [],
            "all_notes": [],
            "standards": [],
            "error": "GPT-4 Vision not available, basic analysis only"
        }


# Convenience function for integration
def analyze_pfd_comprehensive(pfd_file_path: str, document_info: Dict = None, analysis_level: str = "detailed") -> Dict:
    """
    Analyze PFD file comprehensively
    
    Args:
        pfd_file_path: Path to PFD PDF file
        document_info: Optional metadata dict
        analysis_level: "detailed", "standard", or "quick"
        
    Returns:
        Comprehensive analysis dictionary
    """
    analyzer = ComprehensivePFDAnalyzer(analysis_level=analysis_level)
    return analyzer.analyze_pfd_file(pfd_file_path, document_info)
