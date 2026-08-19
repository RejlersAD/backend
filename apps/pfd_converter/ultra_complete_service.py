"""
ULTRA COMPLETE P&ID SERVICE
============================
Unified service for RAG + Graph AI P&ID generation
Used by both API endpoints and standalone scripts

Ensures consistent behavior between:
- Frontend upload (/pfd/documents/upload/)
- Backend test scripts
- Direct API calls
"""

import logging
from typing import Dict, Optional
from .oil_gas_templates import OilGasProcessTemplates
from .rag_knowledge_base import OilGasRAGKnowledgeBase
from .advanced_graph_analyzer import AdvancedPIDGraphAnalyzer
from .graph_based_pid_generator import generate_graph_based_pid
from .generation_config import (
    get_intelligence_config,
    LAYOUT_CONFIG,
    DRAWING_STANDARDS,
    TITLE_BLOCK_CONFIG
)

logger = logging.getLogger(__name__)


class UltraCompletePIDService:
    """
    Service for generating ultra-complete P&IDs with RAG + Graph intelligence
    
    This service provides consistent P&ID generation across:
    - Web application uploads
    - Test scripts
    - API endpoints
    """
    
    def __init__(self, intelligence_level: str = 'ultra'):
        """
        Initialize service with intelligence level
        
        Args:
            intelligence_level: 'basic', 'professional', or 'ultra'
        """
        self.intelligence_level = intelligence_level
        self.config = get_intelligence_config(intelligence_level)
        self.logger = logger
        
        # Log configuration
        self.logger.info(f"🎯 Intelligence Level: {intelligence_level.upper()}")
        self.logger.info(f"   Completeness: {self.config['completeness']*100:.0f}%")
        self.logger.info(f"   Features: {', '.join(self.config['features'])}")
        
    def generate_from_extracted_data(
        self, 
        extracted_data: Dict,
        drawing_info: Dict,
        output_path: str
    ) -> Dict:
        """
        Generate ultra-complete P&ID from extracted PFD data
        
        Args:
            extracted_data: Extracted data from PFD (equipment, piping, instruments, valves)
            drawing_info: Drawing metadata (number, title, revision, project, etc.)
            output_path: Path to save generated PDF
            
        Returns:
            Dict with generation results and metrics
        """
        
        self.logger.info("=" * 90)
        self.logger.info(f"ULTRA COMPLETE P&ID GENERATION - Intelligence Level: {self.intelligence_level.upper()}")
        self.logger.info("=" * 90)
        
        if self.intelligence_level == 'basic':
            return self._generate_basic(extracted_data, drawing_info, output_path)
        elif self.intelligence_level == 'professional':
            return self._generate_professional(extracted_data, drawing_info, output_path)
        else:  # ultra
            return self._generate_ultra(extracted_data, drawing_info, output_path)
    
    def _generate_basic(self, extracted_data: Dict, drawing_info: Dict, output_path: str) -> Dict:
        """Basic conversion - Standard symbols only"""
        
        self.logger.info("[BASIC MODE] Standard PFD to P&ID conversion")
        
        # Use extracted data directly
        drawing_specs = {
            'drawing_number': drawing_info.get('drawing_number', 'P&ID-001'),
            'drawing_title': drawing_info.get('drawing_title', 'P&ID Drawing'),
            'revision': drawing_info.get('revision', 'A'),
            'project_name': drawing_info.get('project_name', ''),
            'project_code': drawing_info.get('project_code', ''),
            'equipment': extracted_data.get('equipment', []),
            'piping': extracted_data.get('process_streams', []) or extracted_data.get('piping', []),
            'process_streams': extracted_data.get('process_streams', []),
            'instruments': extracted_data.get('instruments', []),
            'instrumentation': extracted_data.get('instruments', []),
            'valves': extracted_data.get('valves', [])
        }
        
        # Generate with basic renderer
        result_path = generate_graph_based_pid(drawing_specs, output_path)
        
        return {
            'output_path': result_path,
            'intelligence_level': 'basic',
            'total_elements': (
                len(drawing_specs['equipment']) +
                len(drawing_specs['piping']) +
                len(drawing_specs['instruments']) +
                len(drawing_specs['valves'])
            ),
            'complexity_score': 0,
            'enhancements': []
        }
    
    def _generate_professional(self, extracted_data: Dict, drawing_info: Dict, output_path: str) -> Dict:
        """Professional conversion - Complete schedules and tables"""
        
        self.logger.info("[PROFESSIONAL MODE] Complete professional P&ID")
        
        # Enrich data with defaults
        enriched_data = self._enrich_extracted_data(extracted_data)
        
        drawing_specs = {
            'drawing_number': drawing_info.get('drawing_number', 'P&ID-001'),
            'drawing_title': drawing_info.get('drawing_title', 'P&ID Drawing'),
            'revision': drawing_info.get('revision', 'A'),
            'project_name': drawing_info.get('project_name', ''),
            'project_code': drawing_info.get('project_code', ''),
            'equipment': enriched_data['equipment'],
            'piping': enriched_data['piping'],
            'process_streams': enriched_data['piping'],
            'instruments': enriched_data['instruments'],
            'instrumentation': enriched_data['instruments'],
            'valves': enriched_data['valves']
        }
        
        result_path = generate_graph_based_pid(drawing_specs, output_path)
        
        return {
            'output_path': result_path,
            'intelligence_level': 'professional',
            'total_elements': (
                len(enriched_data['equipment']) +
                len(enriched_data['piping']) +
                len(enriched_data['instruments']) +
                len(enriched_data['valves'])
            ),
            'complexity_score': 0,
            'enhancements': ['data_enrichment', 'complete_schedules']
        }
    
    def _generate_ultra(self, extracted_data: Dict, drawing_info: Dict, output_path: str) -> Dict:
        """
        ULTRA mode - RAG + Advanced Graph Intelligence
        
        This is the breakthrough implementation from today's session:
        - RAG knowledge base retrieval
        - Advanced graph connectivity analysis
        - Auto-detection of missing systems
        - Auto-generation of utilities, drains, vents, control loops
        """
        
        self.logger.info("[ULTRA MODE] RAG + Advanced Graph Intelligence")
        
        # Step 1: Enrich extracted data
        self.logger.info("\n[STEP 1] Enriching extracted PFD data...")
        enriched_data = self._enrich_extracted_data(extracted_data)
        
        base_elements = (
            len(enriched_data['equipment']) +
            len(enriched_data['piping']) +
            len(enriched_data['instruments']) +
            len(enriched_data['valves'])
        )
        self.logger.info(f"✅ Base elements: {base_elements}")
        
        # Step 2: Advanced graph analysis
        self.logger.info("\n[STEP 2] Performing advanced graph analysis...")
        analyzer = AdvancedPIDGraphAnalyzer(
            equipment=enriched_data['equipment'],
            piping=enriched_data['piping'],
            instruments=enriched_data['instruments'],
            valves=enriched_data['valves']
        )
        
        connectivity = analyzer.analyze_connectivity()
        self.logger.info(f"   Graph Nodes: {connectivity['total_nodes']}")
        self.logger.info(f"   Graph Edges: {connectivity['total_edges']}")
        self.logger.info(f"   Initial Complexity: {connectivity['complexity_score']:.1f}")
        
        # Step 3: Find missing connections (RAG-powered)
        self.logger.info("\n[STEP 3] RAG Analysis - Finding missing connections...")
        missing = analyzer.find_missing_connections()
        
        total_missing = sum(len(v) for v in missing.values())
        self.logger.info(f"   Found {total_missing} missing connections")
        
        # Step 4: Generate utility network
        self.logger.info("\n[STEP 4] Generating utility distribution network...")
        utility_network = analyzer.generate_utility_network()
        total_utilities = sum(len(v) for v in utility_network.values())
        self.logger.info(f"   Generated {total_utilities} utility connections")
        
        # Step 5: Generate control loops
        self.logger.info("\n[STEP 5] Generating complete control loops...")
        control_loops = analyzer.generate_control_loops()
        self.logger.info(f"   Generated {len(control_loops)} control loops")
        
        # Step 6: Compile COMPLETE data
        self.logger.info("\n[STEP 6] Compiling COMPLETE P&ID data...")
        
        complete_data = self._compile_complete_data(
            enriched_data,
            missing,
            utility_network,
            control_loops
        )
        
        complete_elements = (
            len(complete_data['equipment']) +
            len(complete_data['piping']) +
            len(complete_data['instruments']) +
            len(complete_data['valves'])
        )
        
        self.logger.info(f"   Total elements: {complete_elements} (from {base_elements})")
        self.logger.info(f"   Equipment: {len(complete_data['equipment'])}")
        self.logger.info(f"   Piping: {len(complete_data['piping'])}")
        self.logger.info(f"   Instruments: {len(complete_data['instruments'])}")
        self.logger.info(f"   Valves: {len(complete_data['valves'])}")
        
        # Step 7: Generate PDF with strict alignment
        self.logger.info("\n[STEP 7] Generating PDF with strict alignment...")
        
        drawing_specs = {
            'drawing_number': drawing_info.get('drawing_number', 'P&ID-001'),
            'drawing_title': drawing_info.get('drawing_title', 'P&ID Drawing'),
            'revision': drawing_info.get('revision', 'A'),
            'project_name': drawing_info.get('project_name', ''),
            'project_code': drawing_info.get('project_code', ''),
            'client': drawing_info.get('client', 'SARB Oil & Gas Division'),
            'contractor': drawing_info.get('contractor', 'Rejlers Engineering AB'),
            'equipment': complete_data['equipment'],
            'piping': complete_data['piping'],
            'process_streams': complete_data['piping'],
            'instruments': complete_data['instruments'],
            'instrumentation': complete_data['instruments'],
            'valves': complete_data['valves']
        }
        
        result_path = generate_graph_based_pid(drawing_specs, output_path)
        
        # Final analysis
        final_connectivity = analyzer.analyze_connectivity()
        final_complexity = final_connectivity['complexity_score']
        
        improvement_factor = final_complexity / connectivity['complexity_score'] if connectivity['complexity_score'] > 0 else 0
        
        self.logger.info(f"\n✅ ULTRA COMPLETE P&ID Generated:")
        self.logger.info(f"   Output: {result_path}")
        self.logger.info(f"   Elements: {base_elements} → {complete_elements} ({complete_elements/base_elements:.1f}X increase)")
        self.logger.info(f"   Complexity: {connectivity['complexity_score']:.1f} → {final_complexity:.1f} ({improvement_factor:.1f}X improvement)")
        
        return {
            'output_path': result_path,
            'intelligence_level': 'ultra',
            'base_elements': base_elements,
            'total_elements': complete_elements,
            'element_increase_factor': complete_elements / base_elements if base_elements > 0 else 0,
            'initial_complexity': connectivity['complexity_score'],
            'final_complexity': final_complexity,
            'complexity_improvement_factor': improvement_factor,
            'missing_connections_found': total_missing,
            'utility_connections_generated': total_utilities,
            'control_loops_generated': len(control_loops),
            'enhancements': [
                'rag_knowledge_retrieval',
                'graph_connectivity_analysis',
                'missing_connection_detection',
                'utility_network_generation',
                'control_loop_generation',
                'strict_grid_alignment'
            ]
        }
    
    def _enrich_extracted_data(self, extracted_data: Dict) -> Dict:
        """
        Enrich extracted data with engineering defaults
        """
        enriched = {
            'equipment': [],
            'piping': [],
            'instruments': [],
            'valves': []
        }
        
        # Enrich equipment
        for eq in extracted_data.get('equipment', []):
            enriched_eq = dict(eq)
            
            # Add defaults if missing
            if 'operating_pressure' not in enriched_eq:
                eq_type = enriched_eq.get('type', '').lower()
                if 'vessel' in eq_type or 'separator' in eq_type:
                    enriched_eq['operating_pressure'] = '10 barg'
                elif 'pump' in eq_type:
                    enriched_eq['operating_pressure'] = '12 barg'
                elif 'tank' in eq_type:
                    enriched_eq['operating_pressure'] = 'Atmospheric'
                else:
                    enriched_eq['operating_pressure'] = '10 barg'
            
            if 'material' not in enriched_eq:
                enriched_eq['material'] = 'CS'
            
            enriched['equipment'].append(enriched_eq)
        
        # Enrich piping
        for pipe in (extracted_data.get('piping', []) or extracted_data.get('process_streams', [])):
            enriched_pipe = dict(pipe)
            
            if 'line_size' not in enriched_pipe:
                enriched_pipe['line_size'] = '6"'
            if 'material' not in enriched_pipe:
                enriched_pipe['material'] = 'CS'
            if 'rating' not in enriched_pipe:
                enriched_pipe['rating'] = '150#'
            if 'pipe_class' not in enriched_pipe:
                enriched_pipe['pipe_class'] = 'A1'
            
            enriched['piping'].append(enriched_pipe)
        
        # Copy instruments and valves as-is
        enriched['instruments'] = list(extracted_data.get('instruments', []))
        enriched['valves'] = list(extracted_data.get('valves', []))
        
        return enriched
    
    def _compile_complete_data(
        self,
        base_data: Dict,
        missing: Dict,
        utility_network: Dict,
        control_loops: Dict
    ) -> Dict:
        """
        Compile complete P&ID data from all sources
        """
        complete = {
            'equipment': list(base_data['equipment']),
            'piping': list(base_data['piping']),
            'instruments': list(base_data['instruments']),
            'valves': list(base_data['valves'])
        }
        
        # Add missing drain lines
        for drain in missing.get('drain_lines', []):
            complete['piping'].append({
                'from': drain['from'],
                'to': drain.get('to', 'Drain Header'),
                'line_number': drain['line_number'],
                'line_size': drain['line_size'],
                'material': 'CS',
                'rating': '150#',
                'pipe_class': 'DR',
                'fluid': 'Drain'
            })
        
        # Add missing vent lines
        for vent in missing.get('vent_lines', []):
            complete['piping'].append({
                'from': vent['from'],
                'to': vent.get('to', 'Vent Header'),
                'line_number': vent['line_number'],
                'line_size': vent['line_size'],
                'material': 'CS',
                'rating': '150#',
                'pipe_class': 'VT',
                'fluid': 'Vent'
            })
        
        # Add missing bypass lines
        for bypass in missing.get('bypass_lines', []):
            complete['piping'].append({
                'from': bypass['from'],
                'to': bypass['to'],
                'line_number': bypass['line_number'],
                'line_size': bypass['line_size'],
                'material': 'CS',
                'rating': '150#',
                'pipe_class': 'BP',
                'fluid': 'Bypass'
            })
        
        # Add utility connections
        for utility_type, connections in utility_network.items():
            for conn in connections:
                complete['piping'].append({
                    'from': conn['from'],
                    'to': conn['to'],
                    'line_number': conn['line_number'],
                    'line_size': conn.get('line_size', '1"'),
                    'material': conn.get('material', 'SS'),
                    'rating': '150#',
                    'pipe_class': 'UT',
                    'fluid': utility_type
                })
        
        # Add control loop components
        for loop_id, loop_data in control_loops.items():
            # Add transmitter
            if 'transmitter' in loop_data:
                complete['instruments'].append(loop_data['transmitter'])
            
            # Add controller
            if 'controller' in loop_data:
                complete['instruments'].append(loop_data['controller'])
            
            # Add control valve
            if 'valve' in loop_data:
                complete['valves'].append(loop_data['valve'])
        
        return complete


# Singleton instance for easy import
ultra_complete_service = UltraCompletePIDService(intelligence_level='ultra')


def generate_ultra_complete_pid(
    extracted_data: Dict,
    drawing_info: Dict,
    output_path: str,
    intelligence_level: str = 'ultra'
) -> Dict:
    """
    Convenience function for generating ultra-complete P&IDs
    
    Args:
        extracted_data: Extracted PFD data
        drawing_info: Drawing metadata
        output_path: Output PDF path
        intelligence_level: 'basic', 'professional', or 'ultra'
        
    Returns:
        Generation results with metrics
    """
    service = UltraCompletePIDService(intelligence_level=intelligence_level)
    return service.generate_from_extracted_data(extracted_data, drawing_info, output_path)
