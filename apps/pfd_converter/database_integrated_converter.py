"""
Enhanced PFD to P&ID Converter with Database Integration
=========================================================

Integrates all extracted databases:
1. PFD-P&ID Reference Database (47 paired examples)
2. Master Legend Database (10,107 legend items)
3. Legend Search Index (850 symbol codes, 2,225 keywords)

Uses intelligent soft-coding techniques for superior P&ID generation.
"""

import json
import boto3
import os
from decouple import config
import logging
from typing import Dict, List, Any, Optional
from openai import OpenAI
import re
from collections import defaultdict

logger = logging.getLogger(__name__)

# AWS S3 Configuration
AWS_ACCESS_KEY = config('AWS_ACCESS_KEY_ID', default='')
AWS_SECRET_KEY = config('AWS_SECRET_ACCESS_KEY', default='')
AWS_REGION = config('AWS_REGION', default='me-central-1')
S3_BUCKET = config('AWS_S3_BUCKET_NAME', default='rejlers-engineering-data')

# Initialize clients
s3_client = boto3.client(
    's3',
    aws_access_key_id=AWS_ACCESS_KEY,
    aws_secret_access_key=AWS_SECRET_KEY,
    region_name=AWS_REGION
)

openai_client = OpenAI(api_key=config('OPENAI_API_KEY', default=''))


class DatabaseIntegratedConverter:
    """
    Enhanced PFD to P&ID converter with comprehensive database integration
    """
    
    def __init__(self):
        self.model = config('OPENAI_MODEL', default='gpt-4o')
        
        # Load databases
        self.reference_db = self._load_reference_database()
        self.legend_db = self._load_legend_database()
        self.search_index = self._load_search_index()
        self.metadata_db = self._load_metadata()
        
        logger.info("✅ Database Integrated Converter initialized")
        logger.info(f"   • Reference PFD-P&ID pairs: {len(self.reference_db.get('files', []))}")
        logger.info(f"   • Legend items: {sum(cat['count'] for cat in self.legend_db.get('categories', {}).values())}")
        logger.info(f"   • Symbol codes indexed: {len(self.search_index.get('by_symbol_code', {}))}")
    
    def _load_reference_database(self) -> dict:
        """Load PFD-P&ID reference pairs from S3"""
        try:
            response = s3_client.get_object(
                Bucket=S3_BUCKET,
                Key='pfd_database/metadata.json'
            )
            data = json.loads(response['Body'].read())
            logger.info(f"✅ Loaded reference database: {data.get('total_pfds', 0)} PFDs, {data.get('total_pids', 0)} P&IDs")
            return data
        except Exception as e:
            logger.warning(f"⚠️ Could not load reference database: {str(e)}")
            return {'files': [], 'categories': {}}
    
    def _load_legend_database(self) -> dict:
        """Load comprehensive legend database from S3"""
        try:
            response = s3_client.get_object(
                Bucket=S3_BUCKET,
                Key='pfd_database/master_legend_database.json'
            )
            data = json.loads(response['Body'].read())
            logger.info(f"✅ Loaded legend database: {data['metadata']['total_items_extracted']} items")
            return data
        except Exception as e:
            logger.warning(f"⚠️ Could not load legend database: {str(e)}")
            return {'categories': {}, 'metadata': {}}
    
    def _load_search_index(self) -> dict:
        """Load searchable legend index from S3"""
        try:
            response = s3_client.get_object(
                Bucket=S3_BUCKET,
                Key='pfd_database/master_legend_search_index.json'
            )
            data = json.loads(response['Body'].read())
            logger.info(f"✅ Loaded search index: {len(data.get('by_symbol_code', {}))} symbols")
            return data
        except Exception as e:
            logger.warning(f"⚠️ Could not load search index: {str(e)}")
            return {'by_symbol_code': {}, 'by_keyword': {}, 'by_category': {}}
    
    def _load_metadata(self) -> dict:
        """Load database metadata"""
        try:
            response = s3_client.get_object(
                Bucket=S3_BUCKET,
                Key='pfd_database/index.json'
            )
            data = json.loads(response['Body'].read())
            return data
        except Exception as e:
            logger.warning(f"⚠️ Could not load metadata: {str(e)}")
            return {}
    
    def find_similar_reference(self, pfd_data: dict, category: str = None) -> Optional[dict]:
        """
        Find similar PFD-P&ID reference pair from database
        
        Args:
            pfd_data: Extracted PFD data
            category: Equipment category (e.g., 'PUMP', 'CONTROL_VALVE')
        
        Returns:
            dict: Reference pair information with S3 paths
        """
        logger.info(f"🔍 Searching for similar reference (category: {category})")
        
        # Extract equipment types from PFD data
        equipment_types = set()
        for eq in pfd_data.get('equipment', []):
            eq_type = eq.get('type', '').upper()
            if eq_type:
                equipment_types.add(eq_type)
        
        # Search in reference database
        matches = []
        for cat_name, cat_data in self.reference_db.get('categories', {}).items():
            # Calculate match score
            score = 0
            
            # Category match
            if category and category.upper() in cat_name.upper():
                score += 50
            
            # Equipment type match
            for eq_type in equipment_types:
                if eq_type in cat_name.upper():
                    score += 30
            
            if score > 0:
                matches.append({
                    'category': cat_name,
                    'score': score,
                    'pfd_count': cat_data.get('pfds', 0),
                    'pid_count': cat_data.get('pids', 0)
                })
        
        # Sort by score
        matches.sort(key=lambda x: x['score'], reverse=True)
        
        if matches:
            best_match = matches[0]
            logger.info(f"✅ Found best match: {best_match['category']} (score: {best_match['score']})")
            return best_match
        
        logger.info("⚠️ No close match found, using general references")
        return None
    
    def get_relevant_legends(self, equipment_types: List[str], keywords: List[str] = None) -> dict:
        """
        Get relevant legend items based on equipment and keywords
        
        Args:
            equipment_types: List of equipment types (PUMP, VALVE, etc.)
            keywords: Additional keywords to search
        
        Returns:
            dict: Categorized legend items
        """
        logger.info(f"📚 Retrieving legends for: {equipment_types}")
        
        relevant_legends = defaultdict(list)
        
        # Search by equipment types
        for eq_type in equipment_types:
            eq_upper = eq_type.upper()
            
            # Direct category match
            for category, data in self.legend_db.get('categories', {}).items():
                if eq_upper in category.upper():
                    items = data.get('items', [])[:50]  # Limit to 50 items per category
                    relevant_legends[category].extend(items)
            
            # Symbol code search
            for code, info in self.search_index.get('by_symbol_code', {}).items():
                if eq_upper in code or eq_upper in info.get('description', '').upper():
                    relevant_legends['symbol_codes'].append({
                        'code': code,
                        'info': info
                    })
        
        # Keyword search
        if keywords:
            for keyword in keywords:
                kw_upper = keyword.upper()
                keyword_items = self.search_index.get('by_keyword', {}).get(kw_upper, [])
                if keyword_items:
                    relevant_legends['keywords'].extend(keyword_items[:20])
        
        total_items = sum(len(items) for items in relevant_legends.values())
        logger.info(f"✅ Retrieved {total_items} relevant legend items across {len(relevant_legends)} categories")
        
        return dict(relevant_legends)
    
    def enhance_pid_generation_with_db(self, pfd_data: dict, project_info: dict = None) -> dict:
        """
        Generate enhanced P&ID specifications using database knowledge
        
        Args:
            pfd_data: Extracted PFD data
            project_info: Project information
        
        Returns:
            dict: Enhanced P&ID specifications
        """
        logger.info("\n" + "="*80)
        logger.info("🚀 ENHANCED P&ID GENERATION WITH DATABASE INTEGRATION")
        logger.info("="*80)
        
        # Extract equipment types and keywords
        equipment_types = []
        keywords = []
        
        for eq in pfd_data.get('equipment', []):
            eq_type = eq.get('type', '')
            if eq_type:
                equipment_types.append(eq_type)
                keywords.append(eq_type)
        
        for stream in pfd_data.get('process_streams', []):
            stream_name = stream.get('name', '')
            if stream_name:
                keywords.append(stream_name)
        
        # Find similar reference
        reference = self.find_similar_reference(pfd_data)
        
        # Get relevant legends
        legends = self.get_relevant_legends(equipment_types, keywords)
        
        # Build enhanced context for AI
        context = self._build_enhanced_context(pfd_data, reference, legends, project_info)
        
        # Generate P&ID with enhanced prompt
        pid_specs = self._generate_with_enhanced_prompt(context, pfd_data, project_info)
        
        logger.info("✅ Enhanced P&ID generation complete")
        return pid_specs
    
    def _build_enhanced_context(self, pfd_data: dict, reference: dict, legends: dict, project_info: dict) -> str:
        """Build comprehensive context from all databases"""
        
        context_parts = []
        
        # Reference database context
        if reference:
            context_parts.append(f"""
REFERENCE DATABASE MATCH:
Category: {reference['category']}
Available Examples: {reference['pfd_count']} PFDs, {reference['pid_count']} P&IDs
Match Confidence: {reference['score']}%

Use this category's patterns and standards for P&ID generation.
""")
        
        # Legend database context
        if legends:
            context_parts.append("\nLEGEND DATABASE - RELEVANT SYMBOLS AND STANDARDS:")
            
            for category, items in legends.items():
                if items:
                    context_parts.append(f"\n{category.upper()} ({len(items)} items):")
                    for i, item in enumerate(items[:10], 1):  # Show top 10
                        if isinstance(item, dict):
                            desc = item.get('description', str(item))[:100]
                            code = item.get('symbol_code', item.get('code', 'N/A'))
                            context_parts.append(f"  {i}. [{code}] {desc}")
        
        # Equipment catalog
        eq_count = len(pfd_data.get('equipment', []))
        stream_count = len(pfd_data.get('process_streams', []))
        context_parts.append(f"""
PFD ANALYSIS SUMMARY:
- Equipment Items: {eq_count}
- Process Streams: {stream_count}
- Project: {project_info.get('project_name', 'N/A')}
""")
        
        return "\n".join(context_parts)
    
    def _generate_with_enhanced_prompt(self, context: str, pfd_data: dict, project_info: dict) -> dict:
        """
        Generate P&ID specifications with database-enhanced prompt
        """
        logger.info("🤖 Generating P&ID with AI + Database Knowledge...")
        
        prompt = f"""You are an expert process engineer with access to a comprehensive database of PFD/P&ID standards, symbols, and reference examples.

{context}

TASK: Generate complete, professional P&ID specifications from the following PFD data.

PFD DATA:
{json.dumps(pfd_data, indent=2)}

PROJECT INFORMATION:
{json.dumps(project_info or {}, indent=2)}

GENERATE COMPREHENSIVE P&ID SPECIFICATIONS INCLUDING:

1. **Drawing Information**
   - Drawing number (format: {project_info.get('project_code', 'PROJ')}-XX-XX-XX-XXXX)
   - Title: "{project_info.get('project_name', 'Process')} - P&ID"
   - Revision: A
   - Scale: 1:50
   - Sheet size: A1

2. **Equipment List** (for EVERY equipment in PFD)
   - Tag number (preserve from PFD or generate if missing)
   - Equipment type and description
   - Design pressure and temperature
   - Materials of construction
   - Size/capacity specifications
   - Nozzle schedule with sizes and ratings
   - Supporting equipment (e.g., pumps need motors, valves, bypass lines)

3. **Instrumentation** (use ISA standards from legend database)
   - Control loops (pressure, temperature, flow, level)
   - Transmitters with tag numbers (PT, TT, FT, LT format)
   - Control valves with actuators
   - Indicators and local instruments
   - Safety instruments (PSV, rupture discs, ESD valves)
   - Interlocks and alarms

4. **Piping Specifications**
   - Line numbers (format: XX-YYYY-MMMM-SS-NN)
   - Pipe sizes and schedule
   - Material specifications
   - Insulation requirements
   - Pressure ratings
   - Flow directions

5. **Valves** (based on legend database standards)
   - Isolation valves at equipment nozzles
   - Control valves for process control
   - Check valves for flow protection
   - Relief/safety valves
   - Drain and vent valves
   - Valve specifications (size, type, rating, actuator type)

6. **Utilities and Connections**
   - Cooling water supply/return
   - Steam connections
   - Nitrogen/instrument air
   - Electrical power connections
   - Tie-in points to existing systems

7. **Safety Systems**
   - Pressure relief devices with full specifications
   - Emergency shutdown valves
   - Fire protection systems
   - Blowdown systems
   - Safety interlocks

8. **Process Control Strategy**
   - Control loops with detailed descriptions
   - Setpoints and operating ranges
   - Cascade control where applicable
   - Ratio control for feeds
   - Override controls for safety

CRITICAL REQUIREMENTS:
- Use ISA symbol standards from the legend database
- Follow ADNOC DEP standards for tag numbering
- Include ALL equipment shown in PFD with full detail
- Every equipment must have isolation valves
- Every control loop must be fully specified
- All streams must have line numbers
- Include utility connections for every equipment
- Add safety devices as per industry standards

RESPONSE FORMAT: JSON only, structured as shown below.

{{
  "drawing_info": {{
    "drawing_number": "...",
    "title": "...",
    "revision": "A",
    "scale": "1:50",
    "sheet_size": "A1",
    "date": "2026-01-08"
  }},
  "equipment_list": [
    {{
      "tag": "P-101",
      "type": "Centrifugal Pump",
      "description": "Main Feed Pump",
      "design_pressure": "25 barg",
      "design_temperature": "150°C",
      "material": "316 SS",
      "capacity": "100 m3/h",
      "head": "50 m",
      "motor_power": "15 kW",
      "nozzles": [
        {{"id": "N1", "size": "4\\"", "rating": "150#", "service": "Suction"}},
        {{"id": "N2", "size": "3\\"", "rating": "300#", "service": "Discharge"}}
      ],
      "supporting_equipment": [
        {{"type": "Motor", "spec": "15 kW, 1450 RPM, 415V"}},
        {{"type": "Isolation Valves", "count": 2, "size": "As per line"}},
        {{"type": "Check Valve", "location": "Discharge"}},
        {{"type": "Pressure Gauge", "location": "Discharge"}}
      ]
    }}
  ],
  "instrument_list": [
    {{
      "tag": "PT-101",
      "type": "Pressure Transmitter",
      "service": "Discharge Pressure",
      "range": "0-30 barg",
      "output": "4-20 mA",
      "location": "P-101 Discharge",
      "connected_to": "PIC-101"
    }},
    {{
      "tag": "PIC-101",
      "type": "Pressure Indicator Controller",
      "service": "Discharge Pressure Control",
      "setpoint": "20 barg",
      "controls": "PCV-101"
    }}
  ],
  "piping_specifications": [
    {{
      "line_number": "01-P101-001-CS-3",
      "from": "P-101",
      "to": "V-101",
      "size": "3\\"",
      "schedule": "40",
      "material": "Carbon Steel",
      "design_pressure": "25 barg",
      "design_temp": "150°C",
      "insulation": "50mm mineral wool",
      "flow_direction": "P-101 → V-101",
      "valves_on_line": ["MOV-101", "XV-101", "Check valve"]
    }}
  ],
  "valve_list": [
    {{
      "tag": "MOV-101",
      "type": "Motor Operated Valve",
      "size": "3\\"",
      "rating": "300#",
      "service": "Isolation - P-101 Discharge",
      "actuator": "Electric motor, 415V",
      "body_material": "Carbon Steel",
      "trim_material": "316 SS"
    }}
  ],
  "safety_devices": [
    {{
      "tag": "PSV-101",
      "type": "Pressure Safety Valve",
      "protected_equipment": "P-101",
      "set_pressure": "23 barg",
      "capacity": "120 m3/h",
      "discharge_to": "Flare Header"
    }}
  ],
  "utilities": [
    {{
      "type": "Cooling Water",
      "supply_pressure": "6 barg",
      "return_pressure": "3 barg",
      "connections": ["E-101", "E-102"]
    }}
  ],
  "control_philosophy": [
    {{
      "loop_id": "PIC-101",
      "description": "Pump discharge pressure control",
      "controlled_variable": "Pressure",
      "manipulated_variable": "PCV-101 position",
      "setpoint": "20 barg",
      "operating_range": "18-22 barg"
    }}
  ],
  "notes": [
    "All equipment shall be designed per ADNOC DEP standards",
    "Instruments follow ISA-5.1 symbology",
    "Refer to legend for symbol definitions"
  ]
}}"""
        
        try:
            response = openai_client.chat.completions.create(
                model=self.model,
                messages=[
                    {
                        "role": "system",
                        "content": "You are an expert process engineering AI with comprehensive knowledge of P&ID design, ISA standards, and industry best practices. You have access to extensive databases of reference drawings and symbols."
                    },
                    {
                        "role": "user",
                        "content": prompt
                    }
                ],
                temperature=0.2,
                max_tokens=16000,
                response_format={"type": "json_object"}
            )
            
            pid_specs = json.loads(response.choices[0].message.content)
            
            logger.info(f"✅ Generated P&ID specs:")
            logger.info(f"   • Equipment: {len(pid_specs.get('equipment_list', []))}")
            logger.info(f"   • Instruments: {len(pid_specs.get('instrument_list', []))}")
            logger.info(f"   • Piping lines: {len(pid_specs.get('piping_specifications', []))}")
            logger.info(f"   • Valves: {len(pid_specs.get('valve_list', []))}")
            logger.info(f"   • Safety devices: {len(pid_specs.get('safety_devices', []))}")
            
            return pid_specs
            
        except Exception as e:
            logger.error(f"❌ Error generating P&ID specs: {str(e)}")
            raise
    
    def search_legend_by_code(self, code: str) -> Optional[dict]:
        """Search legend database by symbol code"""
        return self.search_index.get('by_symbol_code', {}).get(code.upper())
    
    def search_legend_by_keyword(self, keyword: str) -> List[dict]:
        """Search legend database by keyword"""
        return self.search_index.get('by_keyword', {}).get(keyword.upper(), [])
    
    def get_category_legends(self, category: str) -> List[dict]:
        """Get all legends for a specific category"""
        return self.legend_db.get('categories', {}).get(category.upper(), {}).get('items', [])
