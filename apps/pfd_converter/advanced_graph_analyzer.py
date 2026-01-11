"""
ADVANCED GRAPH INTELLIGENCE FOR P&ID COMPLETION
================================================
Uses graph algorithms to analyze connectivity and auto-generate missing systems
"""

import networkx as nx
from typing import Dict, List, Tuple, Set
from collections import defaultdict

class AdvancedPIDGraphAnalyzer:
    """Advanced graph analysis for P&ID completion"""
    
    def __init__(self, equipment: List[Dict], piping: List[Dict], instruments: List[Dict], valves: List[Dict]):
        self.equipment = {e['tag']: e for e in equipment}
        self.piping = piping
        self.instruments = {i['tag']: i for i in instruments}
        self.valves = {v['tag']: v for v in valves}
        
        # Build process graph
        self.graph = nx.DiGraph()
        self._build_process_graph()
    
    def _build_process_graph(self):
        """Build directed graph of process connections"""
        # Add equipment nodes
        for tag, equip in self.equipment.items():
            self.graph.add_node(tag, type='equipment', data=equip)
        
        # Add piping edges
        for pipe in self.piping:
            from_node = pipe.get('from', '')
            to_node = pipe.get('to', '')
            
            # Handle header connections
            if from_node and to_node:
                if from_node not in self.graph:
                    self.graph.add_node(from_node, type='header', data={'tag': from_node})
                if to_node not in self.graph:
                    self.graph.add_node(to_node, type='header', data={'tag': to_node})
                
                self.graph.add_edge(from_node, to_node, type='pipe', data=pipe)
    
    def find_missing_connections(self) -> Dict[str, List[Dict]]:
        """Graph analysis: Find missing standard connections"""
        from .rag_knowledge_base import OilGasRAGKnowledgeBase
        
        missing = {
            'drain_lines': [],
            'vent_lines': [],
            'bypass_lines': [],
            'relief_lines': [],
            'instrument_connections': [],
            'utility_connections': [],
            'startup_lines': []
        }
        
        # Analyze each equipment
        for tag, equip in self.equipment.items():
            equip_type = equip.get('type', '').lower()
            
            # RAG retrieval: Get standard pattern for this equipment type
            pattern = OilGasRAGKnowledgeBase.retrieve_equipment_pattern(equip_type)
            
            if not pattern:
                continue
            
            # Check for missing drain
            if not self._has_connection_type(tag, 'drain'):
                missing['drain_lines'].append({
                    'from': tag,
                    'to': 'Drain Header',
                    'line_number': f"XX-{tag[-3:]}-2HE-DR",
                    'line_size': "2\"",
                    'service': f"{tag} Drain"
                })
            
            # Check for missing vent
            if not self._has_connection_type(tag, 'vent'):
                missing['vent_lines'].append({
                    'from': tag,
                    'to': 'Vent Header',
                    'line_number': f"XX-{tag[-3:]}-1HE-VT",
                    'line_size': "1\"",
                    'service': f"{tag} Vent"
                })
            
            # Check for missing relief (vessels only)
            if equip_type == 'vessel' and not self._has_relief_valve(tag):
                missing['relief_lines'].append({
                    'from': tag,
                    'to': 'Flare Header',
                    'line_number': f"XX-{tag[-3:]}-3HE-RL",
                    'line_size': "3\"",
                    'service': f"{tag} Relief"
                })
        
        # Find pumps that need bypass lines
        pump_nodes = [tag for tag, data in self.graph.nodes(data=True) 
                      if data.get('type') == 'equipment' and 
                      data.get('data', {}).get('type') == 'pump']
        
        for pump_tag in pump_nodes:
            # Check if pump has min flow bypass
            if not self._has_bypass_line(pump_tag):
                # Find upstream vessel (suction source)
                predecessors = list(self.graph.predecessors(pump_tag))
                if predecessors:
                    source_vessel = predecessors[0]
                    missing['bypass_lines'].append({
                        'from': pump_tag,
                        'to': source_vessel,
                        'line_number': f"XX-{pump_tag[-3:]}-2HE-BP",
                        'line_size': "2\"",
                        'service': f"{pump_tag} Min Flow Bypass",
                        'control_valve': True
                    })
        
        return missing
    
    def _has_connection_type(self, equipment_tag: str, connection_type: str) -> bool:
        """Check if equipment has specific connection type"""
        for pipe in self.piping:
            if pipe.get('from') == equipment_tag and connection_type.lower() in pipe.get('service', '').lower():
                return True
        return False
    
    def _has_relief_valve(self, equipment_tag: str) -> bool:
        """Check if equipment has relief valve"""
        for valve in self.valves.values():
            if valve.get('connected_to') == equipment_tag and valve.get('type') == 'safety':
                return True
        return False
    
    def _has_bypass_line(self, pump_tag: str) -> bool:
        """Check if pump has bypass line"""
        successors = list(self.graph.successors(pump_tag))
        for pipe in self.piping:
            if pipe.get('from') == pump_tag and 'bypass' in pipe.get('service', '').lower():
                return True
        return False
    
    def generate_utility_network(self) -> Dict[str, List[Dict]]:
        """Generate complete utility distribution network"""
        from .rag_knowledge_base import OilGasRAGKnowledgeBase
        
        utility_network = {
            'instrument_air': [],
            'nitrogen': [],
            'cooling_water': [],
            'steam': []
        }
        
        # Instrument air to all control valves
        for valve_tag, valve in self.valves.items():
            if valve.get('actuator') == 'pneumatic':
                utility_network['instrument_air'].append({
                    'from': 'IA Header',
                    'to': valve_tag,
                    'line_number': f"XX-{valve_tag[-3:]}-1IA",
                    'line_size': "1\"",
                    'service': f"Instrument Air to {valve_tag}",
                    'includes': ['filter_regulator', 'isolation_valve']
                })
        
        # Nitrogen to vessels for purging
        for tag, equip in self.equipment.items():
            if equip.get('type') in ['vessel', 'tank']:
                utility_network['nitrogen'].append({
                    'from': 'N2 Header',
                    'to': tag,
                    'line_number': f"XX-{tag[-3:]}-1N2",
                    'line_size': "1\"",
                    'service': f"Nitrogen Purge to {tag}",
                    'includes': ['pressure_regulator', 'non_return_valve', 'isolation_valve']
                })
        
        # Seal flush/cooling water to pumps
        for tag, equip in self.equipment.items():
            if equip.get('type') == 'pump':
                utility_network['cooling_water'].append({
                    'from': 'CW Supply Header',
                    'to': tag,
                    'line_number': f"XX-{tag[-3:]}-1CW-S",
                    'line_size': "1/2\"",
                    'service': f"Seal Flush Supply to {tag}"
                })
                utility_network['cooling_water'].append({
                    'from': tag,
                    'to': 'CW Return Header',
                    'line_number': f"XX-{tag[-3:]}-1CW-R",
                    'line_size': "1/2\"",
                    'service': f"Seal Flush Return from {tag}"
                })
        
        return utility_network
    
    def generate_control_loops(self) -> Dict[str, Dict]:
        """Generate complete control loops based on equipment"""
        from .rag_knowledge_base import OilGasRAGKnowledgeBase
        
        control_loops = {}
        loop_number = 300  # Start from 300 series
        
        # Pressure control for vessels
        for tag, equip in self.equipment.items():
            if equip.get('type') == 'vessel':
                # Get RAG pattern for pressure control
                pattern = OilGasRAGKnowledgeBase.retrieve_control_loop_pattern('pressure_control')
                
                control_loops[f"PC-{loop_number}"] = {
                    'type': 'pressure_control',
                    'equipment': tag,
                    'transmitter': {
                        'tag': f"PT-{loop_number}",
                        'type': 'pressure transmitter',
                        'range': f"0-{float(equip.get('operating_pressure', '30').split()[0]) * 1.5} barg",
                        'location': 'field',
                        'connected_to': tag
                    },
                    'controller': {
                        'tag': f"PIC-{loop_number}",
                        'type': 'pressure indicator controller',
                        'location': 'control_room',
                        'setpoint': equip.get('operating_pressure', '25 barg')
                    },
                    'valve': {
                        'tag': f"PV-{loop_number}",
                        'type': 'control',
                        'actuator': 'pneumatic',
                        'fail_position': 'fail open',
                        'service': f'{tag} Pressure Control'
                    },
                    'alarms': [
                        {'tag': f"PAH-{loop_number}", 'type': 'high', 'setpoint': '90% of design'},
                        {'tag': f"PAL-{loop_number}", 'type': 'low', 'setpoint': '80% of normal'}
                    ]
                }
                loop_number += 1
        
        # Level control for vessels and tanks
        for tag, equip in self.equipment.items():
            if equip.get('type') in ['vessel', 'tank']:
                pattern = OilGasRAGKnowledgeBase.retrieve_control_loop_pattern('level_control')
                
                control_loops[f"LC-{loop_number}"] = {
                    'type': 'level_control',
                    'equipment': tag,
                    'transmitter': {
                        'tag': f"LT-{loop_number}",
                        'type': 'level transmitter',
                        'range': '0-100%',
                        'location': 'field',
                        'connected_to': tag
                    },
                    'controller': {
                        'tag': f"LIC-{loop_number}",
                        'type': 'level indicator controller',
                        'location': 'control_room',
                        'setpoint': '50%'
                    },
                    'valve': {
                        'tag': f"LV-{loop_number}",
                        'type': 'control',
                        'actuator': 'pneumatic',
                        'fail_position': 'fail close',
                        'service': f'{tag} Level Control'
                    },
                    'alarms': [
                        {'tag': f"LAHH-{loop_number}", 'type': 'high_high', 'setpoint': '90%', 'action': 'trip'},
                        {'tag': f"LAH-{loop_number}", 'type': 'high', 'setpoint': '80%'},
                        {'tag': f"LAL-{loop_number}", 'type': 'low', 'setpoint': '20%'},
                        {'tag': f"LALL-{loop_number}", 'type': 'low_low', 'setpoint': '10%', 'action': 'trip'}
                    ],
                    'switches': [
                        {'tag': f"LSHH-{loop_number}", 'type': 'high_high_switch'},
                        {'tag': f"LSLL-{loop_number}", 'type': 'low_low_switch'}
                    ]
                }
                loop_number += 1
        
        # Flow control for pump discharges
        for tag, equip in self.equipment.items():
            if equip.get('type') == 'pump' and equip.get('status') == 'Operating':
                pattern = OilGasRAGKnowledgeBase.retrieve_control_loop_pattern('flow_control')
                
                control_loops[f"FC-{loop_number}"] = {
                    'type': 'flow_control',
                    'equipment': tag,
                    'transmitter': {
                        'tag': f"FT-{loop_number}",
                        'type': 'flow transmitter',
                        'range': f"0-{float(equip.get('design_flow', '200').split()[0])} m3/h",
                        'location': 'field',
                        'meter_type': 'Orifice plate'
                    },
                    'controller': {
                        'tag': f"FIC-{loop_number}",
                        'type': 'flow indicator controller',
                        'location': 'control_room'
                    },
                    'valve': {
                        'tag': f"FV-{loop_number}",
                        'type': 'control',
                        'actuator': 'pneumatic',
                        'service': f'{tag} Flow Control'
                    }
                }
                loop_number += 1
        
        return control_loops
    
    def analyze_connectivity(self) -> Dict[str, any]:
        """Comprehensive graph connectivity analysis"""
        analysis = {
            'total_nodes': self.graph.number_of_nodes(),
            'total_edges': self.graph.number_of_edges(),
            'equipment_nodes': len([n for n, d in self.graph.nodes(data=True) if d.get('type') == 'equipment']),
            'header_nodes': len([n for n, d in self.graph.nodes(data=True) if d.get('type') == 'header']),
            'is_connected': nx.is_weakly_connected(self.graph),
            'isolated_nodes': list(nx.isolates(self.graph)),
            'dead_ends': self._find_dead_ends(),
            'complexity_score': self._calculate_complexity_score()
        }
        
        return analysis
    
    def _find_dead_ends(self) -> List[str]:
        """Find equipment with no outlet (dead ends)"""
        dead_ends = []
        for node in self.graph.nodes():
            node_data = self.graph.nodes[node]
            if node_data.get('type') == 'equipment':
                out_degree = self.graph.out_degree(node)
                if out_degree == 0:
                    dead_ends.append(node)
        return dead_ends
    
    def _calculate_complexity_score(self) -> float:
        """Calculate P&ID complexity score (higher = more complete)"""
        score = 0.0
        
        # Base score from element counts
        score += len(self.equipment) * 5
        score += len(self.piping) * 3
        score += len(self.instruments) * 4
        score += len(self.valves) * 3
        
        # Bonus for control loops
        controllers = [i for i in self.instruments.values() if 'controller' in i.get('type', '').lower()]
        score += len(controllers) * 10
        
        # Bonus for safety devices
        safety_valves = [v for v in self.valves.values() if v.get('type') in ['safety', 'esd']]
        score += len(safety_valves) * 8
        
        # Bonus for connectivity
        avg_connections = self.graph.number_of_edges() / max(self.graph.number_of_nodes(), 1)
        score += avg_connections * 20
        
        return score
