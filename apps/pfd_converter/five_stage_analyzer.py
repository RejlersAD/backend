"""
5-Stage PFD Analysis Service
Advanced AI-powered analysis system for PFD to P&ID conversion

Stages:
1. Module Identification: Identify all modules (PV, LV, A/B, etc.) with details
2. Module Details: Create comprehensive understanding of each module
3. Complexity Analysis: Determine number of P&IDs needed
4. Module Coverage: Map modules to specific P&ID drawings
5. Connectivity Analysis: Analyze connections and relationships between modules
"""
import logging
import os
from typing import Dict, List, Any, Optional
from django.conf import settings
import base64
from openai import OpenAI

logger = logging.getLogger(__name__)


class FiveStageAnalyzer:
    """
    AI-powered 5-stage PFD analysis system
    """
    
    def __init__(self, pfd_file_path: str, document_info: Dict[str, Any]):
        """
        Initialize analyzer with PFD file and document information
        
        Args:
            pfd_file_path: Path to PFD PDF file
            document_info: Dictionary containing document metadata
        """
        self.pfd_file_path = pfd_file_path
        self.document_info = document_info
        self.client = OpenAI(api_key=settings.OPENAI_API_KEY)
        
        logger.info(f"[5-Stage Analyzer] Initialized for document: {document_info.get('document_number', 'Unknown')}")
    
    def analyze_all_stages(self) -> Dict[str, Any]:
        """
        Execute all 5 stages of analysis
        
        Returns:
            Dictionary with results from all stages
        """
        logger.info("🚀 Starting 5-stage PFD analysis...")
        
        results = {
            'stage1': {},
            'stage2': {},
            'stage3': {},
            'stage4': {},
            'stage5': {},
            'status': 'in_progress',
            'errors': []
        }
        
        try:
            # Stage 1: Module Identification
            logger.info("📊 Stage 1: Module Identification")
            results['stage1'] = self.stage1_identify_modules()
            
            # Stage 2: Module Details
            logger.info("📝 Stage 2: Module Details Analysis")
            results['stage2'] = self.stage2_module_details(results['stage1'])
            
            # Stage 3: Complexity Analysis
            logger.info("🔍 Stage 3: PID Complexity Analysis")
            results['stage3'] = self.stage3_complexity_analysis(results['stage1'], results['stage2'])
            
            # Stage 4: Module Coverage
            logger.info("📑 Stage 4: Module Coverage Mapping")
            results['stage4'] = self.stage4_module_coverage(results['stage1'], results['stage2'], results['stage3'])
            
            # Stage 5: Connectivity Analysis
            logger.info("🔗 Stage 5: Connectivity Analysis")
            results['stage5'] = self.stage5_connectivity_analysis(results['stage1'], results['stage2'])
            
            results['status'] = 'completed'
            logger.info("✅ All 5 stages completed successfully")
            
        except Exception as e:
            logger.error(f"❌ Error in 5-stage analysis: {str(e)}")
            results['status'] = 'failed'
            results['errors'].append(str(e))
        
        return results
    
    def stage1_identify_modules(self) -> Dict[str, Any]:
        """
        Stage 1: Identify all modules from the PFD
        
        Returns:
            Dictionary with identified modules and their basic information
        """
        logger.info("  → Analyzing PFD to identify modules...")
        
        # Convert PDF to base64 for GPT-4 Vision
        image_base64 = self._convert_pdf_to_base64()
        
        prompt = f"""You are an expert Process Engineer analyzing a Process Flow Diagram (PFD).

TASK: Identify ALL distinct modules/systems in this PFD and provide detailed information about each.

Common module types include:
- PV (Pressure Vessel)
- LV (Level Vessel)
- A (Absorber)
- B (Blower/Boiler)
- C (Compressor/Condenser)
- E (Exchanger)
- F (Filter/Furnace)
- H (Heater)
- P (Pump)
- R (Reactor)
- S (Separator)
- T (Tank/Tower)
- V (Valve/Vessel)

Document Info:
- Project: {self.document_info.get('project_name', 'N/A')}
- Document Number: {self.document_info.get('document_number', 'N/A')}
- Revision: {self.document_info.get('revision', 'N/A')}

For EACH module identified, provide:
1. Module ID (e.g., PV-101, LV-201)
2. Module Type (e.g., Pressure Vessel, Level Vessel)
3. Module Name/Description
4. Primary Function
5. Operating Conditions (pressure, temperature if visible)
6. Key Equipment Associated
7. Location in PFD (section/area)

Return the result as a structured JSON object with this format:
{{
    "total_modules": <number>,
    "modules": [
        {{
            "module_id": "PV-101",
            "module_type": "Pressure Vessel",
            "name": "Feed Separator",
            "function": "Separates liquid from gas in feed stream",
            "operating_pressure": "150 psig",
            "operating_temperature": "120°F",
            "key_equipment": ["PV-101", "PI-101", "TI-101"],
            "location": "Section A - Feed Processing",
            "connections_in": ["From upstream unit"],
            "connections_out": ["To downstream unit"]
        }}
    ],
    "module_summary": {{
        "pressure_vessels": <count>,
        "level_vessels": <count>,
        "pumps": <count>,
        "heat_exchangers": <count>,
        "other": <count>
    }}
}}

Be thorough and identify EVERY module visible in the PFD."""

        try:
            response = self.client.chat.completions.create(
                model="gpt-4-vision-preview",
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:image/jpeg;base64,{image_base64}"
                                }
                            }
                        ]
                    }
                ],
                max_tokens=4000,
                temperature=0.1
            )
            
            content = response.choices[0].message.content
            
            # Extract JSON from response
            import json
            import re
            json_match = re.search(r'\{[\s\S]*\}', content)
            if json_match:
                result = json.loads(json_match.group())
                logger.info(f"  ✓ Identified {result.get('total_modules', 0)} modules")
                return result
            else:
                logger.warning("  ⚠ No JSON found in response, returning raw content")
                return {'raw_response': content}
                
        except Exception as e:
            logger.error(f"  ✗ Stage 1 failed: {str(e)}")
            return {'error': str(e)}
    
    def stage2_module_details(self, stage1_result: Dict[str, Any]) -> Dict[str, Any]:
        """
        Stage 2: Create detailed understanding of all modules
        
        Args:
            stage1_result: Results from Stage 1
            
        Returns:
            Dictionary with detailed module specifications
        """
        logger.info("  → Creating detailed module specifications...")
        
        modules = stage1_result.get('modules', [])
        if not modules:
            logger.warning("  ⚠ No modules from Stage 1")
            return {'error': 'No modules to analyze'}
        
        image_base64 = self._convert_pdf_to_base64()
        
        prompt = f"""You are an expert Process Engineer creating detailed specifications for each module.

Based on the {len(modules)} modules identified in Stage 1, provide COMPREHENSIVE details for each:

Modules to analyze:
{self._format_modules_list(modules)}

For EACH module, provide:
1. **Equipment List**: All equipment items in the module (vessels, pumps, instruments)
2. **Instrumentation**: All instruments with tag numbers (pressure, temperature, level, flow)
3. **Piping Details**: Major piping connections, line sizes, materials
4. **Control Systems**: Control loops, interlocks, safety systems
5. **Utilities Required**: Steam, cooling water, electricity, nitrogen, etc.
6. **Safety Features**: Relief valves, emergency systems, alarms
7. **Design Parameters**: Design pressure, temperature, flow rates
8. **Material Specifications**: Materials of construction for key equipment

Return as JSON:
{{
    "module_details": [
        {{
            "module_id": "PV-101",
            "equipment": [
                {{"tag": "PV-101", "type": "Pressure Vessel", "size": "10ft x 30ft", "material": "CS"}},
                {{"tag": "P-101A/B", "type": "Centrifugal Pump", "flow": "100 GPM", "head": "150 ft"}}
            ],
            "instruments": [
                {{"tag": "PI-101", "type": "Pressure Indicator", "range": "0-200 psig", "location": "Vessel top"}},
                {{"tag": "TI-101", "type": "Temperature Indicator", "range": "0-250°F"}}
            ],
            "piping": [
                {{"line_number": "6-PV-101-A", "size": "6 inch", "material": "CS", "service": "Feed"}}
            ],
            "controls": [
                {{"loop": "PC-101", "type": "Pressure Control", "setpoint": "150 psig"}}
            ],
            "utilities": ["Cooling Water", "Instrument Air"],
            "safety": ["PSV-101 set at 200 psig", "High pressure alarm"],
            "design_parameters": {{
                "design_pressure": "200 psig",
                "design_temperature": "250°F",
                "operating_pressure": "150 psig",
                "operating_temperature": "120°F"
            }}
        }}
    ]
}}"""

        try:
            response = self.client.chat.completions.create(
                model="gpt-4-vision-preview",
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:image/jpeg;base64,{image_base64}"
                                }
                            }
                        ]
                    }
                ],
                max_tokens=4000,
                temperature=0.1
            )
            
            content = response.choices[0].message.content
            
            import json
            import re
            json_match = re.search(r'\{[\s\S]*\}', content)
            if json_match:
                result = json.loads(json_match.group())
                logger.info(f"  ✓ Created details for {len(result.get('module_details', []))} modules")
                return result
            else:
                return {'raw_response': content}
                
        except Exception as e:
            logger.error(f"  ✗ Stage 2 failed: {str(e)}")
            return {'error': str(e)}
    
    def stage3_complexity_analysis(self, stage1_result: Dict[str, Any], stage2_result: Dict[str, Any]) -> Dict[str, Any]:
        """
        Stage 3: Analyze complexity and determine number of P&IDs needed
        
        Args:
            stage1_result: Results from Stage 1
            stage2_result: Results from Stage 2
            
        Returns:
            Dictionary with complexity analysis and P&ID recommendations
        """
        logger.info("  → Analyzing complexity for P&ID generation...")
        
        modules = stage1_result.get('modules', [])
        total_modules = len(modules)
        
        # Complexity heuristics (soft-coded for easy adjustment)
        COMPLEXITY_THRESHOLDS = {
            'simple': {'max_modules': 5, 'pids_needed': 1},
            'moderate': {'max_modules': 15, 'pids_needed': 2},
            'complex': {'max_modules': 25, 'pids_needed': 3},
            'very_complex': {'max_modules': float('inf'), 'pids_needed': 4}
        }
        
        # Determine complexity level
        complexity_level = 'simple'
        pids_needed = 1
        
        for level, threshold in COMPLEXITY_THRESHOLDS.items():
            if total_modules <= threshold['max_modules']:
                complexity_level = level
                pids_needed = threshold['pids_needed']
                break
        
        # Count equipment, instruments, piping
        module_details = stage2_result.get('module_details', [])
        total_equipment = sum(len(m.get('equipment', [])) for m in module_details)
        total_instruments = sum(len(m.get('instruments', [])) for m in module_details)
        total_piping = sum(len(m.get('piping', [])) for m in module_details)
        
        # Adjust based on detail count
        if total_equipment > 50 or total_instruments > 100:
            pids_needed += 1
        
        logger.info(f"  ✓ Complexity: {complexity_level}, P&IDs needed: {pids_needed}")
        
        return {
            'complexity_level': complexity_level,
            'pids_needed': pids_needed,
            'total_modules': total_modules,
            'total_equipment': total_equipment,
            'total_instruments': total_instruments,
            'total_piping_lines': total_piping,
            'complexity_factors': {
                'module_count': total_modules,
                'equipment_count': total_equipment,
                'instrument_density': total_instruments / max(total_modules, 1),
                'piping_complexity': total_piping / max(total_modules, 1)
            },
            'recommendation': f"Create {pids_needed} P&ID drawing(s) based on {complexity_level} complexity",
            'drawing_split_strategy': self._suggest_drawing_split(modules, pids_needed)
        }
    
    def stage4_module_coverage(self, stage1_result: Dict[str, Any], stage2_result: Dict[str, Any], 
                               stage3_result: Dict[str, Any]) -> Dict[str, Any]:
        """
        Stage 4: Determine which modules are covered in each P&ID
        
        Args:
            stage1_result: Results from Stage 1
            stage2_result: Results from Stage 2
            stage3_result: Results from Stage 3
            
        Returns:
            Dictionary mapping modules to P&ID drawings
        """
        logger.info("  → Mapping modules to P&ID drawings...")
        
        modules = stage1_result.get('modules', [])
        pids_needed = stage3_result.get('pids_needed', 1)
        
        # Group modules by location/function for logical P&ID split
        module_groups = self._group_modules_logically(modules)
        
        # Distribute modules across P&IDs
        pid_coverage = []
        modules_per_pid = len(modules) // pids_needed if pids_needed > 0 else len(modules)
        
        for i in range(pids_needed):
            start_idx = i * modules_per_pid
            end_idx = start_idx + modules_per_pid if i < pids_needed - 1 else len(modules)
            
            pid_modules = modules[start_idx:end_idx]
            
            pid_coverage.append({
                'pid_number': f"P&ID-{i+1:02d}",
                'drawing_title': self._generate_pid_title(pid_modules, i+1),
                'modules_covered': [m.get('module_id') for m in pid_modules],
                'module_count': len(pid_modules),
                'equipment_count': self._count_equipment_in_modules(pid_modules, stage2_result),
                'primary_function': self._determine_primary_function(pid_modules),
                'process_area': self._determine_process_area(pid_modules)
            })
        
        logger.info(f"  ✓ Mapped {len(modules)} modules to {pids_needed} P&ID(s)")
        
        return {
            'total_pids': pids_needed,
            'pid_coverage': pid_coverage,
            'coverage_summary': {
                'total_modules': len(modules),
                'modules_per_pid_avg': len(modules) / pids_needed if pids_needed > 0 else 0
            }
        }
    
    def stage5_connectivity_analysis(self, stage1_result: Dict[str, Any], stage2_result: Dict[str, Any]) -> Dict[str, Any]:
        """
        Stage 5: Analyze connectivity and relationships between modules
        
        Args:
            stage1_result: Results from Stage 1
            stage2_result: Results from Stage 2
            
        Returns:
            Dictionary with connectivity information
        """
        logger.info("  → Analyzing module connectivity...")
        
        modules = stage1_result.get('modules', [])
        
        # Build connectivity map
        connections = []
        for module in modules:
            module_id = module.get('module_id')
            connections_out = module.get('connections_out', [])
            
            for connection in connections_out:
                connections.append({
                    'from_module': module_id,
                    'to_module': self._extract_module_from_connection(connection),
                    'connection_type': 'process_flow',
                    'description': connection
                })
        
        # Identify critical paths
        critical_paths = self._identify_critical_paths(modules, connections)
        
        # Analyze utilities connectivity
        utility_connections = self._analyze_utility_connections(stage2_result)
        
        logger.info(f"  ✓ Identified {len(connections)} process connections")
        
        return {
            'total_connections': len(connections),
            'process_connections': connections,
            'critical_paths': critical_paths,
            'utility_connections': utility_connections,
            'connectivity_matrix': self._build_connectivity_matrix(modules, connections),
            'isolated_modules': self._find_isolated_modules(modules, connections),
            'flow_direction': 'forward'  # Can be enhanced to detect flow direction
        }
    
    # Helper methods
    
    def _convert_pdf_to_base64(self) -> str:
        """Convert PDF file to base64 for GPT-4 Vision"""
        try:
            # For now, convert first page of PDF to image
            from pdf2image import convert_from_path
            
            images = convert_from_path(self.pfd_file_path, first_page=1, last_page=1)
            if images:
                import io
                buffer = io.BytesIO()
                images[0].save(buffer, format='JPEG')
                return base64.b64encode(buffer.getvalue()).decode('utf-8')
            return ""
        except Exception as e:
            logger.error(f"Error converting PDF to base64: {str(e)}")
            return ""
    
    def _format_modules_list(self, modules: List[Dict]) -> str:
        """Format modules list for prompt"""
        return "\n".join([f"- {m.get('module_id')}: {m.get('name', 'N/A')}" for m in modules])
    
    def _suggest_drawing_split(self, modules: List[Dict], pids_needed: int) -> List[Dict]:
        """Suggest how to split drawings"""
        strategy = []
        modules_per_pid = len(modules) // pids_needed if pids_needed > 0 else len(modules)
        
        for i in range(pids_needed):
            strategy.append({
                'pid_number': i + 1,
                'suggested_modules': modules_per_pid,
                'focus': f"Process Area {i+1}" if pids_needed > 1 else "Complete Process"
            })
        
        return strategy
    
    def _group_modules_logically(self, modules: List[Dict]) -> Dict[str, List[Dict]]:
        """Group modules by location or function"""
        groups = {}
        for module in modules:
            location = module.get('location', 'General')
            if location not in groups:
                groups[location] = []
            groups[location].append(module)
        return groups
    
    def _generate_pid_title(self, modules: List[Dict], pid_number: int) -> str:
        """Generate descriptive P&ID title"""
        if not modules:
            return f"P&ID Sheet {pid_number}"
        
        primary_function = modules[0].get('function', 'Process')
        return f"{primary_function} - P&ID Sheet {pid_number}"
    
    def _count_equipment_in_modules(self, modules: List[Dict], stage2_result: Dict) -> int:
        """Count total equipment in given modules"""
        module_ids = [m.get('module_id') for m in modules]
        module_details = stage2_result.get('module_details', [])
        
        count = 0
        for detail in module_details:
            if detail.get('module_id') in module_ids:
                count += len(detail.get('equipment', []))
        return count
    
    def _determine_primary_function(self, modules: List[Dict]) -> str:
        """Determine primary function of module group"""
        if not modules:
            return "General Process"
        
        functions = [m.get('function', '') for m in modules]
        return functions[0] if functions else "General Process"
    
    def _determine_process_area(self, modules: List[Dict]) -> str:
        """Determine process area"""
        if not modules:
            return "Area A"
        
        locations = [m.get('location', '') for m in modules]
        return locations[0] if locations else "Area A"
    
    def _extract_module_from_connection(self, connection: str) -> str:
        """Extract module ID from connection description"""
        # Simple extraction - can be enhanced
        import re
        match = re.search(r'([A-Z]+-\d+)', connection)
        return match.group(1) if match else "Unknown"
    
    def _identify_critical_paths(self, modules: List[Dict], connections: List[Dict]) -> List[Dict]:
        """Identify critical process paths"""
        # Simplified - can be enhanced with graph analysis
        return [
            {
                'path_id': 'main_process',
                'description': 'Primary process flow',
                'modules': [m.get('module_id') for m in modules[:min(5, len(modules))]]
            }
        ]
    
    def _analyze_utility_connections(self, stage2_result: Dict) -> Dict:
        """Analyze utility requirements"""
        module_details = stage2_result.get('module_details', [])
        
        utilities = {}
        for detail in module_details:
            for utility in detail.get('utilities', []):
                if utility not in utilities:
                    utilities[utility] = []
                utilities[utility].append(detail.get('module_id'))
        
        return utilities
    
    def _build_connectivity_matrix(self, modules: List[Dict], connections: List[Dict]) -> List[List[int]]:
        """Build connectivity matrix"""
        # Simplified matrix representation
        module_ids = [m.get('module_id') for m in modules]
        n = len(module_ids)
        matrix = [[0] * n for _ in range(n)]
        
        for conn in connections:
            from_idx = module_ids.index(conn['from_module']) if conn['from_module'] in module_ids else -1
            to_idx = module_ids.index(conn['to_module']) if conn['to_module'] in module_ids else -1
            
            if from_idx >= 0 and to_idx >= 0:
                matrix[from_idx][to_idx] = 1
        
        return matrix
    
    def _find_isolated_modules(self, modules: List[Dict], connections: List[Dict]) -> List[str]:
        """Find modules with no connections"""
        connected_modules = set()
        for conn in connections:
            connected_modules.add(conn['from_module'])
            connected_modules.add(conn['to_module'])
        
        all_modules = {m.get('module_id') for m in modules}
        return list(all_modules - connected_modules)


def analyze_pfd_five_stages(pfd_file_path: str, document_info: Dict[str, Any]) -> Dict[str, Any]:
    """
    Main entry point for 5-stage PFD analysis
    
    Args:
        pfd_file_path: Path to PFD PDF file
        document_info: Document metadata
        
    Returns:
        Complete 5-stage analysis results
    """
    analyzer = FiveStageAnalyzer(pfd_file_path, document_info)
    return analyzer.analyze_all_stages()
