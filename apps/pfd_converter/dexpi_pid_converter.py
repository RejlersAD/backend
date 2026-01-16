"""
DEXPI-COMPATIBLE PFD TO P&ID CONVERTER
======================================
Deterministic rule-based conversion following ISO 15926 and DEXPI standards.
NO generative AI - pure engineering rules.

Author: Process Engineering Software Team
Date: January 2026
Standards: DEXPI, ISO 15926, ISA-5.1, ADNOC DEP
"""

import json
import logging
from typing import Dict, List, Tuple, Any, Optional
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger(__name__)


class NodeType(Enum):
    """DEXPI-compatible node types"""
    EQUIPMENT = "Equipment"
    PIPE = "Pipe"
    VALVE = "Valve"
    INSTRUMENT = "Instrument"
    NOZZLE = "Nozzle"
    CONTROL_LOOP = "ControlLoop"
    SIGNAL = "Signal"
    FITTING = "Fitting"


class EdgeType(Enum):
    """DEXPI-compatible relationship types"""
    CONNECTS_TO = "CONNECTS_TO"
    HAS_NOZZLE = "HAS_NOZZLE"
    CONTROLS = "CONTROLS"
    MEASURES = "MEASURES"
    SIGNAL_TO = "SIGNAL_TO"
    PART_OF_LOOP = "PART_OF_LOOP"
    FLOWS_TO = "FLOWS_TO"
    MOUNTED_ON = "MOUNTED_ON"


class ValveType(Enum):
    """Standard valve types per ISA-5.1"""
    ISOLATION = "Isolation Valve"
    CHECK = "Check Valve"
    CONTROL = "Control Valve"
    SAFETY = "Safety Valve"
    RELIEF = "Relief Valve"
    BALL = "Ball Valve"
    GATE = "Gate Valve"
    GLOBE = "Globe Valve"
    BUTTERFLY = "Butterfly Valve"


class InstrumentType(Enum):
    """Instrument types per ISA-5.1"""
    PRESSURE_INDICATOR = "PI"
    PRESSURE_TRANSMITTER = "PT"
    TEMPERATURE_INDICATOR = "TI"
    TEMPERATURE_TRANSMITTER = "TT"
    FLOW_INDICATOR = "FI"
    FLOW_TRANSMITTER = "FT"
    LEVEL_INDICATOR = "LI"
    LEVEL_TRANSMITTER = "LT"
    FLOW_CONTROLLER = "FC"
    PRESSURE_CONTROLLER = "PC"
    TEMPERATURE_CONTROLLER = "TC"
    LEVEL_CONTROLLER = "LC"


@dataclass
class PIDNode:
    """P&ID Node structure (DEXPI-compatible)"""
    id: str
    type: NodeType
    tag: str
    description: str
    properties: Dict[str, Any] = field(default_factory=dict)
    parent_equipment: Optional[str] = None
    
    def to_dict(self) -> Dict:
        return {
            "id": self.id,
            "type": self.type.value,
            "tag": self.tag,
            "description": self.description,
            "properties": self.properties,
            "parent_equipment": self.parent_equipment
        }


@dataclass
class PIDEdge:
    """P&ID Edge structure (DEXPI-compatible)"""
    from_node: str
    to_node: str
    relationship: EdgeType
    properties: Dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> Dict:
        return {
            "from": self.from_node,
            "to": self.to_node,
            "relationship": self.relationship.value,
            "properties": self.properties
        }


class TagGenerator:
    """Generate unique ISA-5.1 compliant tags"""
    
    def __init__(self):
        self.counters = {
            "V": 100,      # Valves
            "PI": 100,     # Pressure Indicators
            "PT": 100,     # Pressure Transmitters
            "TI": 100,     # Temperature Indicators
            "TT": 100,     # Temperature Transmitters
            "FI": 100,     # Flow Indicators
            "FT": 100,     # Flow Transmitters
            "LI": 100,     # Level Indicators
            "LT": 100,     # Level Transmitters
            "FC": 100,     # Flow Controllers
            "PC": 100,     # Pressure Controllers
            "TC": 100,     # Temperature Controllers
            "LC": 100,     # Level Controllers
            "CV": 100,     # Control Valves
            "FCV": 100,    # Flow Control Valves
            "PCV": 100,    # Pressure Control Valves
            "N": 100,      # Nozzles
        }
    
    def generate(self, prefix: str, area: str = "") -> str:
        """Generate unique tag number"""
        self.counters[prefix] += 1
        if area:
            return f"{prefix}-{area}-{self.counters[prefix]:03d}"
        return f"{prefix}-{self.counters[prefix]:03d}"


class DEXPIPIDConverter:
    """
    DEXPI-Compatible PFD to P&ID Converter
    Uses deterministic engineering rules - NO AI generation
    """
    
    def __init__(self, project_info: Dict = None):
        self.project_info = project_info or {}
        self.tag_generator = TagGenerator()
        self.nodes: List[PIDNode] = []
        self.edges: List[PIDEdge] = []
        self.equipment_map: Dict[str, str] = {}  # PFD ID -> P&ID ID mapping
        self.control_loops: List[Dict] = []
        
        logger.info("✅ DEXPI P&ID Converter initialized (Rule-based, no AI)")
    
    def convert(self, pfd_graph: Dict) -> Dict:
        """
        Main conversion entry point
        
        Args:
            pfd_graph: PFD semantic graph in JSON format
            
        Returns:
            DEXPI-compatible P&ID graph in JSON format
        """
        logger.info("🔄 Starting deterministic PFD to P&ID conversion...")
        
        # Step 1: Preserve all PFD nodes
        self._preserve_pfd_nodes(pfd_graph.get("nodes", []))
        
        # Step 2: Apply engineering expansion rules
        self._apply_pump_expansion_rule()
        self._apply_vessel_expansion_rule()
        self._apply_heat_exchanger_rule()
        self._apply_control_loop_rule(pfd_graph)
        
        # Step 3: Preserve and enhance pipes
        self._process_pipes(pfd_graph.get("nodes", []), pfd_graph.get("edges", []))
        
        # Step 4: Create nozzle connections
        self._create_nozzle_connections(pfd_graph.get("edges", []))
        
        # Step 5: Build final P&ID graph
        pid_graph = self._build_output_graph()
        
        logger.info(f"✅ P&ID conversion complete: {len(self.nodes)} nodes, {len(self.edges)} edges")
        return pid_graph
    
    def _preserve_pfd_nodes(self, pfd_nodes: List[Dict]):
        """
        Rule: Preserve ALL PFD elements (no deletion)
        """
        logger.info("📋 Preserving PFD nodes...")
        
        for node in pfd_nodes:
            node_type = node.get("type", "").lower()
            
            # Map PFD types to DEXPI types
            if node_type in ["pump", "vessel", "tank", "reactor", "heat_exchanger", 
                           "compressor", "turbine", "filter"]:
                dexpi_type = NodeType.EQUIPMENT
            elif node_type in ["pipe", "pipeline"]:
                dexpi_type = NodeType.PIPE
            elif node_type in ["instrument", "sensor", "transmitter", "indicator"]:
                dexpi_type = NodeType.INSTRUMENT
            elif node_type in ["valve"]:
                dexpi_type = NodeType.VALVE
            else:
                dexpi_type = NodeType.EQUIPMENT  # Default
            
            pid_node = PIDNode(
                id=node.get("id", f"NODE_{len(self.nodes)}"),
                type=dexpi_type,
                tag=node.get("tag", f"EQ-{len(self.nodes):03d}"),
                description=node.get("description", node.get("label", "Equipment")),
                properties={
                    "pfd_type": node.get("type"),
                    "original_pfd_node": True,
                    **node.get("properties", {})
                }
            )
            
            self.nodes.append(pid_node)
            self.equipment_map[node.get("id")] = pid_node.id
            
        logger.info(f"✅ Preserved {len(self.nodes)} PFD nodes")
    
    def _apply_pump_expansion_rule(self):
        """
        ENGINEERING RULE: PUMP EXPANSION
        
        For each pump, add:
        1. Suction isolation valve (upstream)
        2. Discharge check valve (downstream)
        3. Discharge isolation valve (downstream)
        4. Discharge pressure indicator (PI)
        5. Suction and discharge nozzles
        
        Reference: ADNOC DEP 31.40.10.31-Gen, API 610
        """
        logger.info("🔧 Applying Pump Expansion Rule...")
        
        pump_nodes = [n for n in self.nodes if "pump" in n.description.lower() 
                     or n.properties.get("pfd_type", "").lower() == "pump"]
        
        for pump in pump_nodes:
            logger.info(f"  Expanding pump: {pump.tag}")
            
            # 1. Suction nozzle
            suction_nozzle = PIDNode(
                id=f"{pump.id}_SUCTION_NOZZLE",
                type=NodeType.NOZZLE,
                tag=self.tag_generator.generate("N"),
                description=f"Suction Nozzle",
                properties={"position": "suction", "nominal_diameter": "DN100"},
                parent_equipment=pump.id
            )
            self.nodes.append(suction_nozzle)
            self.edges.append(PIDEdge(pump.id, suction_nozzle.id, EdgeType.HAS_NOZZLE))
            
            # 2. Suction isolation valve
            suction_valve = PIDNode(
                id=f"{pump.id}_SUCTION_VALVE",
                type=NodeType.VALVE,
                tag=self.tag_generator.generate("V"),
                description=f"Suction Isolation Valve",
                properties={
                    "valve_type": ValveType.GATE.value,
                    "normally_open": True,
                    "size": "DN100"
                },
                parent_equipment=pump.id
            )
            self.nodes.append(suction_valve)
            self.edges.append(PIDEdge(suction_valve.id, suction_nozzle.id, EdgeType.CONNECTS_TO))
            
            # 3. Discharge nozzle
            discharge_nozzle = PIDNode(
                id=f"{pump.id}_DISCHARGE_NOZZLE",
                type=NodeType.NOZZLE,
                tag=self.tag_generator.generate("N"),
                description=f"Discharge Nozzle",
                properties={"position": "discharge", "nominal_diameter": "DN100"},
                parent_equipment=pump.id
            )
            self.nodes.append(discharge_nozzle)
            self.edges.append(PIDEdge(pump.id, discharge_nozzle.id, EdgeType.HAS_NOZZLE))
            
            # 4. Discharge check valve
            check_valve = PIDNode(
                id=f"{pump.id}_CHECK_VALVE",
                type=NodeType.VALVE,
                tag=self.tag_generator.generate("V"),
                description=f"Discharge Check Valve",
                properties={
                    "valve_type": ValveType.CHECK.value,
                    "size": "DN100"
                },
                parent_equipment=pump.id
            )
            self.nodes.append(check_valve)
            self.edges.append(PIDEdge(discharge_nozzle.id, check_valve.id, EdgeType.CONNECTS_TO))
            
            # 5. Discharge isolation valve
            discharge_valve = PIDNode(
                id=f"{pump.id}_DISCHARGE_VALVE",
                type=NodeType.VALVE,
                tag=self.tag_generator.generate("V"),
                description=f"Discharge Isolation Valve",
                properties={
                    "valve_type": ValveType.GATE.value,
                    "normally_open": True,
                    "size": "DN100"
                },
                parent_equipment=pump.id
            )
            self.nodes.append(discharge_valve)
            self.edges.append(PIDEdge(check_valve.id, discharge_valve.id, EdgeType.CONNECTS_TO))
            
            # 6. Discharge pressure indicator
            pressure_indicator = PIDNode(
                id=f"{pump.id}_PI",
                type=NodeType.INSTRUMENT,
                tag=self.tag_generator.generate("PI"),
                description=f"Discharge Pressure Indicator",
                properties={
                    "instrument_type": InstrumentType.PRESSURE_INDICATOR.value,
                    "location": "discharge",
                    "range": "0-25 bar",
                    "mounted_on": "pipe"
                },
                parent_equipment=pump.id
            )
            self.nodes.append(pressure_indicator)
            self.edges.append(PIDEdge(pressure_indicator.id, discharge_valve.id, EdgeType.MEASURES))
            
            logger.info(f"    ✅ Added: 2 nozzles, 3 valves, 1 PI for {pump.tag}")
        
        logger.info(f"✅ Pump expansion complete for {len(pump_nodes)} pumps")
    
    def _apply_vessel_expansion_rule(self):
        """
        ENGINEERING RULE: VESSEL/TANK EXPANSION
        
        For each vessel/tank, add:
        1. Inlet nozzle(s)
        2. Outlet nozzle(s)
        3. Level transmitter (LT)
        4. Pressure indicator (PI)
        5. Safety valve (PSV) if pressure vessel
        
        Reference: ASME Section VIII, ADNOC DEP
        """
        logger.info("🔧 Applying Vessel Expansion Rule...")
        
        vessel_nodes = [n for n in self.nodes if any(v in n.description.lower() 
                       for v in ["vessel", "tank", "drum", "separator", "reactor"])]
        
        for vessel in vessel_nodes:
            logger.info(f"  Expanding vessel: {vessel.tag}")
            
            # 1. Inlet nozzle
            inlet_nozzle = PIDNode(
                id=f"{vessel.id}_INLET_NOZZLE",
                type=NodeType.NOZZLE,
                tag=self.tag_generator.generate("N"),
                description="Inlet Nozzle",
                properties={"position": "top", "nominal_diameter": "DN150"},
                parent_equipment=vessel.id
            )
            self.nodes.append(inlet_nozzle)
            self.edges.append(PIDEdge(vessel.id, inlet_nozzle.id, EdgeType.HAS_NOZZLE))
            
            # 2. Outlet nozzle
            outlet_nozzle = PIDNode(
                id=f"{vessel.id}_OUTLET_NOZZLE",
                type=NodeType.NOZZLE,
                tag=self.tag_generator.generate("N"),
                description="Outlet Nozzle",
                properties={"position": "bottom", "nominal_diameter": "DN150"},
                parent_equipment=vessel.id
            )
            self.nodes.append(outlet_nozzle)
            self.edges.append(PIDEdge(vessel.id, outlet_nozzle.id, EdgeType.HAS_NOZZLE))
            
            # 3. Level transmitter
            level_transmitter = PIDNode(
                id=f"{vessel.id}_LT",
                type=NodeType.INSTRUMENT,
                tag=self.tag_generator.generate("LT"),
                description="Level Transmitter",
                properties={
                    "instrument_type": InstrumentType.LEVEL_TRANSMITTER.value,
                    "range": "0-100%",
                    "mounted_on": vessel.id
                },
                parent_equipment=vessel.id
            )
            self.nodes.append(level_transmitter)
            self.edges.append(PIDEdge(level_transmitter.id, vessel.id, EdgeType.MEASURES))
            
            # 4. Pressure indicator
            pressure_indicator = PIDNode(
                id=f"{vessel.id}_PI",
                type=NodeType.INSTRUMENT,
                tag=self.tag_generator.generate("PI"),
                description="Pressure Indicator",
                properties={
                    "instrument_type": InstrumentType.PRESSURE_INDICATOR.value,
                    "range": "0-10 bar",
                    "mounted_on": vessel.id
                },
                parent_equipment=vessel.id
            )
            self.nodes.append(pressure_indicator)
            self.edges.append(PIDEdge(pressure_indicator.id, vessel.id, EdgeType.MEASURES))
            
            # 5. Safety valve (if pressure vessel)
            if "pressure" in vessel.description.lower() or "separator" in vessel.description.lower():
                safety_valve = PIDNode(
                    id=f"{vessel.id}_PSV",
                    type=NodeType.VALVE,
                    tag=self.tag_generator.generate("V"),
                    description="Pressure Safety Valve",
                    properties={
                        "valve_type": ValveType.SAFETY.value,
                        "set_pressure": "10 bar",
                        "size": "DN50"
                    },
                    parent_equipment=vessel.id
                )
                self.nodes.append(safety_valve)
                self.edges.append(PIDEdge(safety_valve.id, vessel.id, EdgeType.MOUNTED_ON))
                logger.info(f"    ✅ Added: 2 nozzles, 1 LT, 1 PI, 1 PSV for {vessel.tag}")
            else:
                logger.info(f"    ✅ Added: 2 nozzles, 1 LT, 1 PI for {vessel.tag}")
        
        logger.info(f"✅ Vessel expansion complete for {len(vessel_nodes)} vessels")
    
    def _apply_heat_exchanger_rule(self):
        """
        ENGINEERING RULE: HEAT EXCHANGER EXPANSION
        
        For each heat exchanger, add:
        1. Shell side inlet/outlet nozzles
        2. Tube side inlet/outlet nozzles
        3. Shell side pressure indicators (2x)
        4. Tube side pressure indicators (2x)
        5. Temperature indicators (4x)
        
        Reference: TEMA Standards, ADNOC DEP
        """
        logger.info("🔧 Applying Heat Exchanger Rule...")
        
        hex_nodes = [n for n in self.nodes if "heat" in n.description.lower() 
                    or "exchanger" in n.description.lower() or "cooler" in n.description.lower()]
        
        for hex_node in hex_nodes:
            logger.info(f"  Expanding heat exchanger: {hex_node.tag}")
            
            # Shell side nozzles
            shell_inlet = self._create_nozzle(hex_node, "shell_inlet", "Shell Inlet")
            shell_outlet = self._create_nozzle(hex_node, "shell_outlet", "Shell Outlet")
            
            # Tube side nozzles
            tube_inlet = self._create_nozzle(hex_node, "tube_inlet", "Tube Inlet")
            tube_outlet = self._create_nozzle(hex_node, "tube_outlet", "Tube Outlet")
            
            # Temperature indicators
            self._create_instrument(hex_node, "TI", "Shell Inlet Temperature", "shell_inlet")
            self._create_instrument(hex_node, "TI", "Shell Outlet Temperature", "shell_outlet")
            self._create_instrument(hex_node, "TI", "Tube Inlet Temperature", "tube_inlet")
            self._create_instrument(hex_node, "TI", "Tube Outlet Temperature", "tube_outlet")
            
            # Pressure indicators
            self._create_instrument(hex_node, "PI", "Shell Side Pressure", "shell")
            self._create_instrument(hex_node, "PI", "Tube Side Pressure", "tube")
            
            logger.info(f"    ✅ Added: 4 nozzles, 4 TI, 2 PI for {hex_node.tag}")
        
        logger.info(f"✅ Heat exchanger expansion complete for {len(hex_nodes)} units")
    
    def _apply_control_loop_rule(self, pfd_graph: Dict):
        """
        ENGINEERING RULE: CONTROL LOOP CREATION
        
        If PFD contains flow/pressure/temperature control:
        1. Create transmitter (FT/PT/TT)
        2. Create controller (FC/PC/TC)
        3. Create control valve (FCV/PCV/TCV)
        4. Assign unique loop number
        5. Connect using SIGNAL_TO edges
        
        Reference: ISA-5.1, ISA-5.4 (Control Loop Diagram)
        """
        logger.info("🔧 Applying Control Loop Rule...")
        
        # Search for control indicators in PFD
        control_keywords = ["control", "fc", "pc", "tc", "lc", "fic", "pic", "tic"]
        
        nodes_with_control = [n for n in pfd_graph.get("nodes", []) 
                             if any(kw in n.get("description", "").lower() 
                                   or kw in n.get("tag", "").lower() 
                                   for kw in control_keywords)]
        
        for control_node in nodes_with_control:
            loop_type = self._detect_control_type(control_node)
            
            if loop_type:
                self._create_control_loop(control_node, loop_type)
        
        logger.info(f"✅ Created {len(self.control_loops)} control loops")
    
    def _detect_control_type(self, node: Dict) -> Optional[str]:
        """Detect control loop type from node description"""
        desc = node.get("description", "").lower()
        tag = node.get("tag", "").lower()
        combined = f"{desc} {tag}"
        
        if any(kw in combined for kw in ["flow", "fc", "fic"]):
            return "FLOW"
        elif any(kw in combined for kw in ["pressure", "pc", "pic"]):
            return "PRESSURE"
        elif any(kw in combined for kw in ["temperature", "tc", "tic"]):
            return "TEMPERATURE"
        elif any(kw in combined for kw in ["level", "lc", "lic"]):
            return "LEVEL"
        return None
    
    def _create_control_loop(self, reference_node: Dict, loop_type: str):
        """Create complete control loop with transmitter, controller, and valve"""
        logger.info(f"  Creating {loop_type} control loop...")
        
        loop_id = f"LOOP_{len(self.control_loops) + 100}"
        
        # 1. Transmitter
        if loop_type == "FLOW":
            transmitter_prefix = "FT"
            controller_prefix = "FC"
            valve_prefix = "FCV"
        elif loop_type == "PRESSURE":
            transmitter_prefix = "PT"
            controller_prefix = "PC"
            valve_prefix = "PCV"
        elif loop_type == "TEMPERATURE":
            transmitter_prefix = "TT"
            controller_prefix = "TC"
            valve_prefix = "TCV"
        elif loop_type == "LEVEL":
            transmitter_prefix = "LT"
            controller_prefix = "LC"
            valve_prefix = "LCV"
        else:
            return
        
        # Create transmitter
        transmitter = PIDNode(
            id=f"{loop_id}_TRANSMITTER",
            type=NodeType.INSTRUMENT,
            tag=self.tag_generator.generate(transmitter_prefix),
            description=f"{loop_type.title()} Transmitter",
            properties={
                "instrument_type": transmitter_prefix,
                "loop_id": loop_id,
                "measurement_type": loop_type.lower()
            }
        )
        self.nodes.append(transmitter)
        
        # Create controller
        controller = PIDNode(
            id=f"{loop_id}_CONTROLLER",
            type=NodeType.INSTRUMENT,
            tag=self.tag_generator.generate(controller_prefix),
            description=f"{loop_type.title()} Controller",
            properties={
                "instrument_type": controller_prefix,
                "loop_id": loop_id,
                "control_type": "PID"
            }
        )
        self.nodes.append(controller)
        
        # Create control valve
        control_valve = PIDNode(
            id=f"{loop_id}_VALVE",
            type=NodeType.VALVE,
            tag=self.tag_generator.generate("CV"),
            description=f"{loop_type.title()} Control Valve",
            properties={
                "valve_type": ValveType.CONTROL.value,
                "loop_id": loop_id,
                "actuator": "pneumatic",
                "fail_position": "fail_close"
            }
        )
        self.nodes.append(control_valve)
        
        # Create signal connections
        self.edges.append(PIDEdge(transmitter.id, controller.id, EdgeType.SIGNAL_TO,
                                 {"signal_type": "4-20mA"}))
        self.edges.append(PIDEdge(controller.id, control_valve.id, EdgeType.CONTROLS,
                                 {"signal_type": "3-15 psig"}))
        
        # Store loop info
        self.control_loops.append({
            "loop_id": loop_id,
            "type": loop_type,
            "transmitter": transmitter.tag,
            "controller": controller.tag,
            "valve": control_valve.tag
        })
        
        logger.info(f"    ✅ Loop {loop_id}: {transmitter.tag} -> {controller.tag} -> {control_valve.tag}")
    
    def _process_pipes(self, pfd_nodes: List[Dict], pfd_edges: List[Dict]):
        """
        PIPE RULE: Preserve all PFD pipes and enhance with P&ID details
        """
        logger.info("🔧 Processing pipes...")
        
        pipe_nodes = [n for n in pfd_nodes if n.get("type", "").lower() in ["pipe", "pipeline"]]
        
        for pipe in pipe_nodes:
            # Enhance pipe with P&ID properties
            pipe_id = pipe.get("id")
            if pipe_id in self.equipment_map:
                pid_pipe_id = self.equipment_map[pipe_id]
                pipe_node = next((n for n in self.nodes if n.id == pid_pipe_id), None)
                
                if pipe_node:
                    # Add pipe class and specifications
                    pipe_node.properties.update({
                        "pipe_class": "150#",
                        "material": "CS",
                        "insulation": "None",
                        "nominal_diameter": "DN100",
                        "schedule": "Sch40"
                    })
        
        logger.info(f"✅ Processed {len(pipe_nodes)} pipes")
    
    def _create_nozzle_connections(self, pfd_edges: List[Dict]):
        """Create nozzle connections based on PFD flow edges"""
        logger.info("🔧 Creating nozzle connections...")
        
        for edge in pfd_edges:
            from_id = edge.get("from")
            to_id = edge.get("to")
            
            # Map to P&ID IDs
            pid_from = self.equipment_map.get(from_id)
            pid_to = self.equipment_map.get(to_id)
            
            if pid_from and pid_to:
                # Create connection edge
                self.edges.append(PIDEdge(
                    pid_from, pid_to, EdgeType.CONNECTS_TO,
                    {"flow_direction": edge.get("properties", {}).get("direction", "forward")}
                ))
        
        logger.info(f"✅ Created {len(pfd_edges)} connections")
    
    def _create_nozzle(self, parent: PIDNode, position: str, description: str) -> PIDNode:
        """Helper: Create nozzle"""
        nozzle = PIDNode(
            id=f"{parent.id}_{position.upper()}",
            type=NodeType.NOZZLE,
            tag=self.tag_generator.generate("N"),
            description=description,
            properties={"position": position, "nominal_diameter": "DN100"},
            parent_equipment=parent.id
        )
        self.nodes.append(nozzle)
        self.edges.append(PIDEdge(parent.id, nozzle.id, EdgeType.HAS_NOZZLE))
        return nozzle
    
    def _create_instrument(self, parent: PIDNode, prefix: str, description: str, location: str):
        """Helper: Create instrument"""
        instrument = PIDNode(
            id=f"{parent.id}_{prefix}_{location}",
            type=NodeType.INSTRUMENT,
            tag=self.tag_generator.generate(prefix),
            description=description,
            properties={
                "instrument_type": prefix,
                "location": location,
                "mounted_on": parent.id
            },
            parent_equipment=parent.id
        )
        self.nodes.append(instrument)
        self.edges.append(PIDEdge(instrument.id, parent.id, EdgeType.MEASURES))
    
    def _build_output_graph(self) -> Dict:
        """Build final DEXPI-compatible P&ID graph"""
        
        output = {
            "metadata": {
                "standard": "DEXPI",
                "iso_compliance": "ISO 15926",
                "isa_compliance": "ISA-5.1",
                "project": self.project_info.get("project_name", "Unknown"),
                "area": self.project_info.get("area", "Process"),
                "conversion_method": "Rule-based deterministic",
                "total_nodes": len(self.nodes),
                "total_edges": len(self.edges),
                "control_loops": len(self.control_loops)
            },
            "nodes": [node.to_dict() for node in self.nodes],
            "edges": [edge.to_dict() for edge in self.edges],
            "control_loops": self.control_loops,
            "statistics": {
                "equipment": len([n for n in self.nodes if n.type == NodeType.EQUIPMENT]),
                "valves": len([n for n in self.nodes if n.type == NodeType.VALVE]),
                "instruments": len([n for n in self.nodes if n.type == NodeType.INSTRUMENT]),
                "nozzles": len([n for n in self.nodes if n.type == NodeType.NOZZLE]),
                "pipes": len([n for n in self.nodes if n.type == NodeType.PIPE]),
                "control_loops": len(self.control_loops)
            }
        }
        
        return output
    
    def save_to_json(self, pid_graph: Dict, output_path: str):
        """Save P&ID graph to JSON file"""
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(pid_graph, f, indent=2, ensure_ascii=False)
        logger.info(f"✅ P&ID graph saved to: {output_path}")
    
    def export_to_neo4j_cypher(self, pid_graph: Dict, output_path: str):
        """Export as Neo4j Cypher statements for direct ingestion"""
        
        cypher_statements = []
        cypher_statements.append("// DEXPI P&ID Graph - Neo4j Import\n")
        cypher_statements.append("// Generated by Rule-Based PFD to P&ID Converter\n\n")
        
        # Create nodes
        cypher_statements.append("// ===== CREATE NODES =====\n")
        for node in pid_graph["nodes"]:
            props = ", ".join([f"{k}: '{v}'" if isinstance(v, str) else f"{k}: {v}" 
                              for k, v in node["properties"].items()])
            cypher = f"CREATE (:{node['type']} {{id: '{node['id']}', tag: '{node['tag']}', description: '{node['description']}', {props}}})\n"
            cypher_statements.append(cypher)
        
        cypher_statements.append("\n// ===== CREATE RELATIONSHIPS =====\n")
        for edge in pid_graph["edges"]:
            props_str = ", ".join([f"{k}: '{v}'" if isinstance(v, str) else f"{k}: {v}" 
                                  for k, v in edge["properties"].items()]) if edge["properties"] else ""
            props_clause = f" {{{props_str}}}" if props_str else ""
            cypher = f"MATCH (a {{id: '{edge['from']}'}}), (b {{id: '{edge['to']}'}}) CREATE (a)-[:{edge['relationship']}{props_clause}]->(b)\n"
            cypher_statements.append(cypher)
        
        with open(output_path, 'w', encoding='utf-8') as f:
            f.writelines(cypher_statements)
        
        logger.info(f"✅ Neo4j Cypher export saved to: {output_path}")


# ========================================================================
# EXAMPLE USAGE
# ========================================================================

if __name__ == "__main__":
    
    # Example PFD graph input
    pfd_graph_example = {
        "nodes": [
            {
                "id": "PUMP_101",
                "type": "pump",
                "tag": "P-101",
                "description": "Feed Pump",
                "properties": {"capacity": "100 m3/h", "head": "50 m"}
            },
            {
                "id": "VESSEL_201",
                "type": "vessel",
                "tag": "V-201",
                "description": "Separator Vessel",
                "properties": {"volume": "10 m3", "pressure": "10 bar"}
            },
            {
                "id": "PIPE_301",
                "type": "pipe",
                "tag": "L-301",
                "description": "Feed Line",
                "properties": {}
            }
        ],
        "edges": [
            {"from": "PUMP_101", "to": "PIPE_301", "relationship": "FLOWS_TO"},
            {"from": "PIPE_301", "to": "VESSEL_201", "relationship": "CONNECTS_TO"}
        ]
    }
    
    # Initialize converter
    project_info = {
        "project_name": "Gas Processing Unit",
        "project_code": "PROJ-001",
        "area": "Process"
    }
    
    converter = DEXPIPIDConverter(project_info=project_info)
    
    # Convert PFD to P&ID
    pid_graph = converter.convert(pfd_graph_example)
    
    # Save outputs
    converter.save_to_json(pid_graph, "pid_dexpi_graph.json")
    converter.export_to_neo4j_cypher(pid_graph, "pid_neo4j_import.cypher")
    
    # Print summary
    print("\n" + "="*70)
    print("P&ID CONVERSION SUMMARY")
    print("="*70)
    print(f"Total Nodes: {pid_graph['metadata']['total_nodes']}")
    print(f"  - Equipment: {pid_graph['statistics']['equipment']}")
    print(f"  - Valves: {pid_graph['statistics']['valves']}")
    print(f"  - Instruments: {pid_graph['statistics']['instruments']}")
    print(f"  - Nozzles: {pid_graph['statistics']['nozzles']}")
    print(f"Total Edges: {pid_graph['metadata']['total_edges']}")
    print(f"Control Loops: {pid_graph['metadata']['control_loops']}")
    print("="*70)
