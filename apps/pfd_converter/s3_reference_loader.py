"""
S3 Reference Sample Loader
===========================

PURPOSE:
Load and analyze approved P&ID samples from S3 to extract engineering patterns

This module:
1. Connects to S3 bucket (rejlers-engineering-data, me-central-1)
2. Loads approved P&ID drawings from ADNOC projects
3. Extracts instrument patterns, valve configurations, routing topologies
4. Builds a "ground truth" knowledge base from real engineering drawings
5. Enables pattern matching for PFD → P&ID conversion

CRITICAL PRINCIPLE:
DO NOT invent or assume - ONLY learn from approved reference drawings
"""

import boto3
import json
import os
import re
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple
from dataclasses import dataclass, field
import logging
from io import BytesIO
import fitz  # PyMuPDF
from collections import Counter, defaultdict

logger = logging.getLogger(__name__)


@dataclass
class ReferencePIDPattern:
    """Pattern extracted from reference P&ID"""
    pattern_id: str
    pattern_type: str  # 'instrument_loop', 'valve_config', 'safety_routing', 'equipment_detail'
    source_document: str  # S3 key of source P&ID
    project_name: str
    description: str
    elements: List[Dict]  # List of P&ID elements in this pattern
    frequency: int = 1  # How many times this pattern appears in references
    confidence: float = 1.0  # Confidence score (0-1)
    engineering_justification: str = ""  # Why this pattern is correct
    
    def __str__(self):
        return f"{self.pattern_type}: {self.description} (from {self.project_name})"


@dataclass
class InstrumentLoopPattern:
    """
    Instrument control loop pattern from reference P&IDs
    Example: Pressure Control Loop = PIT + PIC + PCV + PSH/PSL alarms
    """
    loop_type: str  # 'pressure_control', 'level_control', 'flow_control', 'temperature_control'
    instruments: List[Dict]  # List of instrument tags and their roles
    valve_config: Optional[Dict] = None  # Associated control valve
    interlocks: List[Dict] = field(default_factory=list)  # Safety interlocks
    alarms: List[Dict] = field(default_factory=list)  # Alarm points
    source_projects: Set[str] = field(default_factory=set)  # Which ADNOC projects use this
    frequency: int = 1
    
    def to_dict(self):
        return {
            'loop_type': self.loop_type,
            'instruments': self.instruments,
            'valve_config': self.valve_config,
            'interlocks': self.interlocks,
            'alarms': self.alarms,
            'source_projects': list(self.source_projects),
            'frequency': self.frequency
        }


@dataclass
class SafetyRoutingPattern:
    """
    Safety system routing from reference P&IDs
    Example: PSV → HP Flare routing with knockout drum
    """
    routing_type: str  # 'psv_to_flare', 'sdv_isolation', 'drain_system'
    source_equipment: str  # What equipment this routing comes from
    destination: str  # 'HP_FLARE', 'LP_FLARE', 'CLOSED_DRAIN', etc.
    intermediate_equipment: List[str] = field(default_factory=list)  # e.g., knockout drum
    valves_in_path: List[Dict] = field(default_factory=list)
    line_size: Optional[str] = None
    material_spec: Optional[str] = None
    source_projects: Set[str] = field(default_factory=set)
    frequency: int = 1


class S3ReferenceLoader:
    """
    Loads and analyzes approved P&ID references from S3
    Builds engineering knowledge base from ADNOC project drawings
    """
    
    def __init__(self, bucket_name: str = None, region: str = None):
        self.bucket_name = bucket_name or os.getenv('AWS_STORAGE_BUCKET_NAME', 'rejlers-engineering-data')
        self.region = region or os.getenv('AWS_S3_REGION_NAME', 'me-central-1')
        
        # Initialize S3 client
        self.s3_client = boto3.client(
            's3',
            region_name=self.region,
            aws_access_key_id=os.getenv('AWS_ACCESS_KEY_ID'),
            aws_secret_access_key=os.getenv('AWS_SECRET_ACCESS_KEY')
        )
        
        # Storage for extracted patterns
        self.instrument_loop_patterns: List[InstrumentLoopPattern] = []
        self.safety_routing_patterns: List[SafetyRoutingPattern] = []
        self.reference_patterns: List[ReferencePIDPattern] = []
        
        # Statistics
        self.stats = {
            'total_documents_analyzed': 0,
            'adnoc_projects_covered': set(),
            'instrument_tags_found': set(),
            'valve_types_found': set(),
            'safety_devices_found': set()
        }
        
        logger.info(f"🔗 S3ReferenceLoader initialized")
        logger.info(f"   Bucket: {self.bucket_name}")
        logger.info(f"   Region: {self.region}")
    
    def load_adnoc_projects(self, project_filter: Optional[str] = None) -> List[str]:
        """
        Load list of ADNOC projects available in S3
        
        Args:
            project_filter: Optional filter (e.g., '5900837' for specific project)
        
        Returns:
            List of project folder paths in S3
        """
        logger.info("📁 Loading ADNOC project list from S3...")
        
        try:
            # List top-level folders in ADNOC_P&IDs
            response = self.s3_client.list_objects_v2(
                Bucket=self.bucket_name,
                Prefix='ADNOC_P&IDs/',
                Delimiter='/'
            )
            
            projects = []
            for prefix in response.get('CommonPrefixes', []):
                project_path = prefix['Prefix']
                project_name = project_path.replace('ADNOC_P&IDs/', '').strip('/')
                
                if project_filter and project_filter not in project_name:
                    continue
                
                projects.append(project_path)
                self.stats['adnoc_projects_covered'].add(project_name)
            
            logger.info(f"✅ Found {len(projects)} ADNOC projects in S3")
            return projects
            
        except Exception as e:
            logger.error(f"❌ Error loading ADNOC projects: {e}")
            return []
    
    def load_pid_samples(self, project_path: str, max_samples: int = 10) -> List[Dict]:
        """
        Load P&ID PDF files from a specific project folder
        
        Args:
            project_path: S3 path to project (e.g., 'ADNOC_P&IDs/5900837_...')
            max_samples: Maximum number of P&IDs to load per project
        
        Returns:
            List of P&ID file metadata
        """
        logger.info(f"📄 Loading P&ID samples from: {project_path}")
        
        try:
            response = self.s3_client.list_objects_v2(
                Bucket=self.bucket_name,
                Prefix=project_path,
                MaxKeys=max_samples
            )
            
            pid_files = []
            for obj in response.get('Contents', []):
                key = obj['Key']
                
                # Filter for P&ID files (typically contain 'P&ID', 'PID', or 'P-ID' in filename)
                filename = key.split('/')[-1].lower()
                if any(pattern in filename for pattern in ['p&id', 'pid', 'p-id', 'piping']):
                    if filename.endswith('.pdf'):
                        pid_files.append({
                            's3_key': key,
                            'filename': key.split('/')[-1],
                            'size': obj['Size'],
                            'project': project_path.split('/')[-2]
                        })
            
            logger.info(f"   Found {len(pid_files)} P&ID files")
            return pid_files
            
        except Exception as e:
            logger.error(f"❌ Error loading P&ID samples: {e}")
            return []
    
    def extract_text_from_pid(self, s3_key: str) -> str:
        """
        Extract text content from P&ID PDF
        
        Args:
            s3_key: S3 key of the P&ID file
        
        Returns:
            Extracted text content
        """
        try:
            # Download file from S3
            response = self.s3_client.get_object(Bucket=self.bucket_name, Key=s3_key)
            pdf_bytes = response['Body'].read()
            
            # Open PDF with PyMuPDF
            pdf_document = fitz.open(stream=pdf_bytes, filetype="pdf")
            
            # Extract text from all pages
            text_content = ""
            for page_num in range(pdf_document.page_count):
                page = pdf_document[page_num]
                text_content += page.get_text()
            
            pdf_document.close()
            
            return text_content
            
        except Exception as e:
            logger.error(f"❌ Error extracting text from {s3_key}: {e}")
            return ""
    
    def analyze_instrument_patterns(self, text_content: str, project_name: str) -> List[InstrumentLoopPattern]:
        """
        Analyze P&ID text to identify instrument loop patterns
        
        WHY THIS APPROACH:
        - Learns from REAL approved P&IDs
        - Identifies common instrument groupings
        - Understands which instruments appear together
        
        Args:
            text_content: Extracted text from P&ID
            project_name: Name of ADNOC project
        
        Returns:
            List of identified instrument loop patterns
        """
        patterns = []
        
        # Extract all instrument tags (format: XXXX-XX-XXX-XXX)
        # Common ADNOC format: [AREA]-[INSTRUMENT]-[NUMBER]
        instrument_tags = re.findall(r'\b\d{4}-\d{2}-[A-Z]{2,6}-\d{4}-\d{2}\b', text_content)
        
        # Also try simpler format: XXX-XX
        instrument_tags += re.findall(r'\b[A-Z]{2,6}-\d{4}-\d{2}\b', text_content)
        
        # Group instruments by type
        instrument_groups = defaultdict(list)
        for tag in instrument_tags:
            # Extract instrument code (e.g., 'PIT', 'LIC', 'PSV')
            parts = tag.split('-')
            if len(parts) >= 2:
                instrument_code = parts[-2]  # Usually second to last part
                instrument_groups[instrument_code[0]].append({  # First letter (P, L, F, T)
                    'tag': tag,
                    'code': instrument_code
                })
                self.stats['instrument_tags_found'].add(tag)
        
        # Identify control loops (presence of transmitter + controller + valve)
        # Example: PIT + PIC + PCV = pressure control loop
        for measure_type, instruments in instrument_groups.items():
            codes = [inst['code'] for inst in instruments]
            
            # Check for control loop pattern
            has_transmitter = any('IT' in code or 'T' == code[-1] for code in codes)
            has_controller = any('IC' in code or 'C' in code for code in codes)
            has_valve = any('CV' in code or 'V' in code for code in codes)
            
            if has_transmitter and has_controller:
                loop_type = {
                    'P': 'pressure_control',
                    'L': 'level_control',
                    'F': 'flow_control',
                    'T': 'temperature_control'
                }.get(measure_type, f'{measure_type}_control')
                
                pattern = InstrumentLoopPattern(
                    loop_type=loop_type,
                    instruments=instruments,
                    valve_config={'has_control_valve': has_valve},
                    source_projects={project_name}
                )
                patterns.append(pattern)
        
        # Identify safety instrumentation (PSH, PSL, PSDH, PSDL)
        safety_instruments = re.findall(r'\b[A-Z]{4,6}-\d{4}-\d{2}\b', text_content)
        for tag in safety_instruments:
            if any(suffix in tag for suffix in ['PSH', 'PSL', 'PSDH', 'PSDL', 'LSH', 'LSL', 'LSDH', 'LSDL']):
                self.stats['safety_devices_found'].add(tag)
        
        return patterns
    
    def analyze_valve_configurations(self, text_content: str) -> List[Dict]:
        """
        Extract valve type patterns from P&ID
        
        Identifies:
        - SDV (Shutdown Valve) placements
        - MOV (Motor Operated Valve) usage
        - PSV (Pressure Safety Valve) routing
        - Check valve locations
        """
        valve_patterns = []
        
        # Common valve abbreviations in ADNOC P&IDs
        valve_types = ['SDV', 'MOV', 'PSV', 'PRV', 'BDV', 'RO', 'CV', 'PCV', 'LCV', 'FCV']
        
        for valve_type in valve_types:
            # Find all occurrences
            matches = re.findall(rf'{valve_type}[- ]\d{{4}}[- ]\d{{2}}', text_content)
            for match in matches:
                valve_patterns.append({
                    'type': valve_type,
                    'tag': match,
                })
                self.stats['valve_types_found'].add(valve_type)
        
        return valve_patterns
    
    def analyze_safety_routing(self, text_content: str) -> List[SafetyRoutingPattern]:
        """
        Analyze safety system routing patterns
        
        Identifies:
        - PSV → Flare routing
        - Drain system routing (open/closed)
        - Emergency depressurization paths
        """
        routing_patterns = []
        
        # Look for flare routing keywords
        if 'HP FLARE' in text_content or 'HIGH PRESSURE FLARE' in text_content:
            # PSV to HP Flare pattern
            routing_patterns.append(SafetyRoutingPattern(
                routing_type='psv_to_hp_flare',
                source_equipment='PSV',
                destination='HP_FLARE',
                intermediate_equipment=['Knockout Drum'] if 'KNOCKOUT' in text_content or 'KO DRUM' in text_content else []
            ))
        
        if 'LP FLARE' in text_content or 'LOW PRESSURE FLARE' in text_content:
            routing_patterns.append(SafetyRoutingPattern(
                routing_type='psv_to_lp_flare',
                source_equipment='PSV',
                destination='LP_FLARE'
            ))
        
        if 'CLOSED DRAIN' in text_content:
            routing_patterns.append(SafetyRoutingPattern(
                routing_type='closed_drain',
                source_equipment='Equipment Drains',
                destination='CLOSED_DRAIN'
            ))
        
        return routing_patterns
    
    def build_knowledge_base(self, max_projects: int = 5, max_docs_per_project: int = 3) -> Dict:
        """
        Build comprehensive knowledge base from S3 reference P&IDs
        
        This is the main function that:
        1. Loads ADNOC project P&IDs
        2. Analyzes each P&ID for patterns
        3. Aggregates patterns across multiple projects
        4. Creates a "ground truth" knowledge base
        
        Args:
            max_projects: Maximum number of ADNOC projects to analyze
            max_docs_per_project: Maximum P&IDs to analyze per project
        
        Returns:
            Knowledge base dictionary
        """
        logger.info("="*70)
        logger.info("🏗️  BUILDING ENGINEERING KNOWLEDGE BASE FROM S3 REFERENCES")
        logger.info("="*70)
        
        # Load ADNOC projects
        projects = self.load_adnoc_projects()[:max_projects]
        
        for project_path in projects:
            project_name = project_path.split('/')[-2]
            logger.info(f"\n📊 Analyzing project: {project_name}")
            
            # Load P&ID samples from this project
            pid_files = self.load_pid_samples(project_path, max_docs_per_project)
            
            for pid_file in pid_files:
                logger.info(f"   📄 Processing: {pid_file['filename']}")
                
                # Extract text
                text_content = self.extract_text_from_pid(pid_file['s3_key'])
                
                if not text_content:
                    logger.warning(f"      ⚠️  No text extracted")
                    continue
                
                # Analyze patterns
                instrument_patterns = self.analyze_instrument_patterns(text_content, project_name)
                valve_configs = self.analyze_valve_configurations(text_content)
                safety_routing = self.analyze_safety_routing(text_content)
                
                # Aggregate
                self.instrument_loop_patterns.extend(instrument_patterns)
                self.safety_routing_patterns.extend(safety_routing)
                
                self.stats['total_documents_analyzed'] += 1
                
                logger.info(f"      ✅ Found {len(instrument_patterns)} instrument patterns, "
                           f"{len(valve_configs)} valves, {len(safety_routing)} safety routes")
        
        # Aggregate and consolidate patterns
        knowledge_base = self._consolidate_patterns()
        
        logger.info("\n" + "="*70)
        logger.info("✅ KNOWLEDGE BASE BUILD COMPLETE")
        logger.info("="*70)
        logger.info(f"   Documents Analyzed: {self.stats['total_documents_analyzed']}")
        logger.info(f"   ADNOC Projects: {len(self.stats['adnoc_projects_covered'])}")
        logger.info(f"   Instrument Tags: {len(self.stats['instrument_tags_found'])}")
        logger.info(f"   Valve Types: {len(self.stats['valve_types_found'])}")
        logger.info(f"   Safety Devices: {len(self.stats['safety_devices_found'])}")
        logger.info("="*70)
        
        return knowledge_base
    
    def _consolidate_patterns(self) -> Dict:
        """
        Consolidate extracted patterns into unified knowledge base
        """
        # Group similar instrument loop patterns
        loop_frequency = Counter()
        for pattern in self.instrument_loop_patterns:
            loop_frequency[pattern.loop_type] += 1
        
        # Group safety routing patterns
        routing_frequency = Counter()
        for pattern in self.safety_routing_patterns:
            routing_frequency[pattern.routing_type] += 1
        
        knowledge_base = {
            'instrument_loops': {
                loop_type: {
                    'frequency': freq,
                    'examples': [p.to_dict() for p in self.instrument_loop_patterns if p.loop_type == loop_type][:3]
                }
                for loop_type, freq in loop_frequency.items()
            },
            'safety_routing': {
                route_type: freq
                for route_type, freq in routing_frequency.items()
            },
            'statistics': {
                'total_documents': self.stats['total_documents_analyzed'],
                'projects_covered': list(self.stats['adnoc_projects_covered']),
                'unique_instruments': len(self.stats['instrument_tags_found']),
                'valve_types': list(self.stats['valve_types_found']),
                'safety_devices': len(self.stats['safety_devices_found'])
            }
        }
        
        return knowledge_base
    
    def save_knowledge_base(self, output_path: Path):
        """Save knowledge base to JSON file"""
        knowledge_base = self._consolidate_patterns()
        
        with open(output_path, 'w') as f:
            json.dump(knowledge_base, f, indent=2)
        
        logger.info(f"💾 Knowledge base saved to: {output_path}")


# Convenience function
def load_reference_knowledge(rebuild: bool = False) -> Dict:
    """
    Load reference knowledge base (build if doesn't exist or rebuild=True)
    
    Args:
        rebuild: Force rebuild from S3 references
    
    Returns:
        Knowledge base dictionary
    """
    kb_path = Path(__file__).parent / 'reference_knowledge_base.json'
    
    if kb_path.exists() and not rebuild:
        logger.info(f"📚 Loading existing knowledge base from {kb_path}")
        with open(kb_path, 'r') as f:
            return json.load(f)
    
    # Build from S3
    logger.info("🏗️  Building knowledge base from S3 references...")
    loader = S3ReferenceLoader()
    knowledge_base = loader.build_knowledge_base(max_projects=3, max_docs_per_project=2)
    loader.save_knowledge_base(kb_path)
    
    return knowledge_base
