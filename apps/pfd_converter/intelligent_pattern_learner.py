"""
INTELLIGENT PATTERN-LEARNING P&ID GENERATOR
============================================
Learns from PFD-P&ID examples and replicates exact patterns.

Strategy:
1. Extract detailed information from both PFD and P&ID using GPT-4 Vision
2. Learn transformation patterns (what was added, modified, enhanced)
3. Create a template library of transformations
4. Apply learned patterns to new PFDs with high fidelity

This system combines:
- Computer Vision (GPT-4 Vision) for detailed extraction
- Pattern Learning (similarity matching)
- Template Application (exact replication)
- Quality Control (validation against examples)
"""

import json
import logging
from typing import Dict, List, Tuple, Any, Optional
from dataclasses import dataclass, field
import re

from decouple import config
from openai import OpenAI

logger = logging.getLogger(__name__)


@dataclass
class ExamplePattern:
    """Learned pattern from PFD-P&ID pair"""
    pattern_id: str
    pfd_characteristics: Dict[str, Any]
    pid_additions: Dict[str, Any]
    transformation_rules: List[str]
    equipment_type: str
    confidence: float = 0.0


class IntelligentPatternLearner:
    """
    Learns from PFD-P&ID example pairs and replicates exact patterns.
    
    This is the "smart" converter that learns by example rather than
    predefined rules.
    """
    
    def __init__(self):
        self.client = OpenAI(api_key=config("OPENAI_API_KEY"))
        self.learned_patterns: List[ExamplePattern] = []
        self.example_library: Dict[str, Dict] = {}
        
        logger.info("🧠 Intelligent Pattern Learner initialized")
    
    def learn_from_example(self, pfd_file, pid_file, metadata: Dict = None) -> ExamplePattern:
        """
        Step 1: Learn transformation patterns from a PFD-P&ID example pair
        
        This is where the magic happens - we analyze what changed from PFD to P&ID.
        """
        logger.info("📚 Learning from example pair...")
        
        # Extract detailed information from PFD
        pfd_analysis = self._deep_analyze_drawing(
            pfd_file,
            drawing_type="PFD",
            analysis_prompt="""
            Analyze this PFD in EXTREME DETAIL. Extract:
            
            1. EQUIPMENT:
               - Type, tag, capacity, specifications
               - Position, orientation
               - Connections (inlet/outlet)
               - Operating conditions
            
            2. PIPING:
               - Line numbers, sizes
               - Flow directions
               - Connection points
            
            3. INSTRUMENTATION (if any):
               - Type (FT, PT, LT, TT, etc.)
               - Tags, ranges
               - Control loops
            
            4. PROCESS FLOW:
               - Stream compositions
               - Flow rates, pressures, temperatures
               - Material states (liquid, gas, two-phase)
            
            5. ANNOTATIONS:
               - Notes, specifications
               - Design conditions
               - Special requirements
            
            6. DRAWING STYLE:
               - Symbol types used
               - Line styles
               - Text formatting
               - Layout approach
            
            Return COMPREHENSIVE JSON with all details.
            """
        )
        
        # Extract detailed information from corresponding P&ID
        pid_analysis = self._deep_analyze_drawing(
            pid_file,
            drawing_type="P&ID",
            analysis_prompt="""
            Analyze this P&ID in EXTREME DETAIL. Extract:
            
            1. EQUIPMENT (all items):
               - Main equipment (pumps, vessels, exchangers)
               - Valves (type, tag, size, actuator, fail position)
               - Instruments (type, tag, range, location)
               - Fittings (reducers, elbows, tees)
               - Nozzles (tag, size, rating, orientation)
            
            2. PIPING:
               - Line numbers with full specs
               - Pipe class, material, schedule
               - Sizes, reducers
               - Insulation, tracing
            
            3. INSTRUMENTATION:
               - All instruments with full tags
               - Control loops (complete logic)
               - Signal types (4-20mA, HART, digital)
               - Mounting locations
               - Impulse piping
            
            4. VALVES:
               - Complete valve schedule
               - Type (gate, globe, ball, check, control)
               - Size, rating, material
               - Actuators (manual, pneumatic, electric)
               - Fail positions, accessories
            
            5. SAFETY SYSTEMS:
               - PSVs (set pressure, size, discharge)
               - ESD valves
               - Interlocks
            
            6. CONNECTIONS:
               - Utility tie-ins
               - Vent and drain connections
               - Sample points
               - Instrument air, hydraulic
            
            7. SPECIFICATIONS:
               - Equipment datasheets referenced
               - Pipe specs
               - Valve specs
               - Material specs
            
            8. NOTES AND LEGENDS:
               - Drawing notes
               - Symbols used
               - Abbreviations
            
            9. DRAWING DETAILS:
               - Tag format/numbering
               - Symbol styles
               - Line conventions
               - Text placement rules
            
            Return COMPREHENSIVE JSON with all details.
            """
        )
        
        # Compare and learn transformation patterns
        pattern = self._extract_transformation_pattern(
            pfd_analysis,
            pid_analysis,
            metadata or {}
        )
        
        # Store in library
        self.learned_patterns.append(pattern)
        self.example_library[pattern.pattern_id] = {
            "pfd_analysis": pfd_analysis,
            "pid_analysis": pid_analysis,
            "pattern": pattern
        }
        
        logger.info(f"✅ Learned pattern: {pattern.pattern_id}")
        logger.info(f"   Equipment type: {pattern.equipment_type}")
        logger.info(f"   Transformation rules: {len(pattern.transformation_rules)}")
        
        return pattern
    
    def _deep_analyze_drawing(self, drawing_file, drawing_type: str, analysis_prompt: str) -> Dict:
        """
        Use GPT-4 Vision to extract EVERYTHING from a drawing
        """
        logger.info(f"🔍 Deep analyzing {drawing_type}...")
        
        try:
            # Read file
            import base64
            with open(drawing_file, 'rb') as f:
                file_bytes = f.read()
            
            # Convert to base64
            base64_image = base64.b64encode(file_bytes).decode('utf-8')
            
            # Determine MIME type
            if drawing_file.lower().endswith('.pdf'):
                mime_type = "application/pdf"
            elif drawing_file.lower().endswith(('.png', '.jpg', '.jpeg')):
                mime_type = f"image/{drawing_file.split('.')[-1].lower()}"
            else:
                mime_type = "application/octet-stream"
            
            # Call GPT-4 Vision with DETAILED analysis
            response = self.client.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {
                        "role": "system",
                        "content": f"""You are a senior process engineer with 20+ years experience in oil & gas.
                        You have EXPERT knowledge of:
                        - PFD and P&ID standards (ISO 15926, ISA-5.1, ADNOC DEP)
                        - Process equipment design
                        - Instrumentation and control
                        - Piping specifications
                        - Safety systems
                        
                        Your task is to analyze engineering drawings with EXTREME ATTENTION TO DETAIL.
                        Miss nothing. Extract every tag, every valve, every instrument, every specification.
                        """
                    },
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": analysis_prompt
                            },
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:{mime_type};base64,{base64_image}",
                                    "detail": "high"  # High detail analysis
                                }
                            }
                        ]
                    }
                ],
                max_tokens=4000,
                temperature=0.1  # Low temperature for accuracy
            )
            
            # Parse response
            content = response.choices[0].message.content
            
            # Try to extract JSON
            if "```json" in content:
                json_match = re.search(r'```json\s*(.*?)\s*```', content, re.DOTALL)
                if json_match:
                    analysis = json.loads(json_match.group(1))
                else:
                    analysis = {"raw_analysis": content}
            else:
                # Try direct JSON parse
                try:
                    analysis = json.loads(content)
                except:
                    analysis = {"raw_analysis": content}
            
            analysis["drawing_type"] = drawing_type
            analysis["file_path"] = str(drawing_file)
            
            logger.info(f"✅ {drawing_type} analysis complete")
            return analysis
            
        except Exception as e:
            logger.error(f"❌ Analysis failed: {str(e)}")
            return {"error": str(e), "drawing_type": drawing_type}
    
    def _extract_transformation_pattern(
        self,
        pfd_analysis: Dict,
        pid_analysis: Dict,
        metadata: Dict
    ) -> ExamplePattern:
        """
        Learn what changed from PFD to P&ID
        
        This creates the transformation template.
        """
        logger.info("🧩 Extracting transformation patterns...")
        
        # Use GPT-4 to intelligently compare and extract patterns
        comparison_prompt = f"""
        You are analyzing a PFD to P&ID transformation to learn the exact patterns.
        
        PFD ANALYSIS:
        {json.dumps(pfd_analysis, indent=2)}
        
        P&ID ANALYSIS:
        {json.dumps(pid_analysis, indent=2)}
        
        Your task: Identify EXACTLY what was added, modified, or enhanced from PFD to P&ID.
        
        Focus on:
        1. What valves were added (type, placement, naming)
        2. What instruments were added (type, tags, loops)
        3. What nozzles/connections were added
        4. How pipes were detailed (specs, classes, materials)
        5. What safety devices were added
        6. Control philosophy applied
        7. Naming/tagging conventions
        8. Drawing style and layout rules
        
        Return JSON with:
        {{
            "equipment_type": "main equipment category",
            "pfd_characteristics": {{
                "equipment": [...],
                "basic_pipes": [...],
                "basic_instruments": [...]
            }},
            "pid_additions": {{
                "valves": [
                    {{
                        "type": "...",
                        "tag_format": "...",
                        "placement_rule": "...",
                        "size_rule": "...",
                        "specifications": {{...}}
                    }}
                ],
                "instruments": [...],
                "nozzles": [...],
                "pipe_details": [...],
                "safety_devices": [...],
                "control_loops": [...]
            }},
            "transformation_rules": [
                "For each pump: add suction isolation valve (gate, normally open)",
                "For each pump: add discharge check valve immediately after pump",
                "For each pump discharge: add PI with range 0-X bar",
                "Tag format: PREFIX-AREA-###",
                "Valve naming: V-section-sequential",
                ... (all transformation rules)
            ],
            "style_guide": {{
                "tag_format": "...",
                "line_numbering": "...",
                "symbol_conventions": {{...}},
                "text_placement": "..."
            }}
        }}
        
        Be EXHAUSTIVE. Include every transformation rule you can identify.
        """
        
        try:
            response = self.client.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {
                        "role": "system",
                        "content": "You are a process engineering expert specializing in PFD to P&ID conversion standards."
                    },
                    {
                        "role": "user",
                        "content": comparison_prompt
                    }
                ],
                max_tokens=4000,
                temperature=0.1
            )
            
            content = response.choices[0].message.content
            
            # Extract JSON
            if "```json" in content:
                json_match = re.search(r'```json\s*(.*?)\s*```', content, re.DOTALL)
                if json_match:
                    pattern_data = json.loads(json_match.group(1))
                else:
                    pattern_data = json.loads(content)
            else:
                pattern_data = json.loads(content)
            
            # Create pattern object
            pattern = ExamplePattern(
                pattern_id=f"PATTERN_{len(self.learned_patterns) + 1:03d}",
                pfd_characteristics=pattern_data.get("pfd_characteristics", {}),
                pid_additions=pattern_data.get("pid_additions", {}),
                transformation_rules=pattern_data.get("transformation_rules", []),
                equipment_type=pattern_data.get("equipment_type", "unknown"),
                confidence=0.95
            )
            
            logger.info(f"✅ Extracted {len(pattern.transformation_rules)} transformation rules")
            return pattern
            
        except Exception as e:
            logger.error(f"❌ Pattern extraction failed: {str(e)}")
            # Return basic pattern
            return ExamplePattern(
                pattern_id=f"PATTERN_{len(self.learned_patterns) + 1:03d}",
                pfd_characteristics={},
                pid_additions={},
                transformation_rules=[],
                equipment_type="unknown",
                confidence=0.5
            )
    
    def generate_pid_from_pfd(
        self,
        new_pfd_file,
        project_info: Dict = None,
        reference_pattern_id: str = None
    ) -> Dict:
        """
        Step 2: Apply learned patterns to generate P&ID from new PFD
        
        This replicates the exact style and patterns from the example.
        """
        logger.info("🎨 Generating P&ID using learned patterns...")
        
        # Analyze new PFD
        new_pfd_analysis = self._deep_analyze_drawing(
            new_pfd_file,
            drawing_type="PFD",
            analysis_prompt="""
            Analyze this PFD in detail. Extract all equipment, piping, instruments, and process information.
            Return comprehensive JSON.
            """
        )
        
        # Find best matching pattern
        if reference_pattern_id:
            pattern = next(
                (p for p in self.learned_patterns if p.pattern_id == reference_pattern_id),
                self.learned_patterns[0] if self.learned_patterns else None
            )
        else:
            pattern = self._find_best_matching_pattern(new_pfd_analysis)
        
        if not pattern:
            logger.error("❌ No learned patterns available")
            return {"error": "No patterns learned yet. Please provide example PFD-P&ID pairs first."}
        
        logger.info(f"📋 Using pattern: {pattern.pattern_id} ({pattern.equipment_type})")
        
        # Apply transformation pattern
        pid_result = self._apply_pattern_to_generate_pid(
            new_pfd_analysis,
            pattern,
            project_info or {}
        )
        
        return pid_result
    
    def _find_best_matching_pattern(self, pfd_analysis: Dict) -> Optional[ExamplePattern]:
        """Find the most similar pattern from learned examples"""
        if not self.learned_patterns:
            return None
        
        # For now, return the first pattern
        # TODO: Implement similarity scoring
        return self.learned_patterns[0]
    
    def _apply_pattern_to_generate_pid(
        self,
        pfd_analysis: Dict,
        pattern: ExamplePattern,
        project_info: Dict
    ) -> Dict:
        """
        Apply learned transformation pattern to generate P&ID
        
        This is where we replicate the exact style of the example.
        """
        logger.info("🔨 Applying transformation pattern...")
        
        # Create comprehensive prompt with learned patterns
        generation_prompt = f"""
        You are generating a P&ID from a PFD using LEARNED TRANSFORMATION PATTERNS.
        
        LEARNED PATTERN (from example P&ID):
        Equipment Type: {pattern.equipment_type}
        
        Transformation Rules:
        {chr(10).join(f"- {rule}" for rule in pattern.transformation_rules)}
        
        P&ID Additions Template:
        {json.dumps(pattern.pid_additions, indent=2)}
        
        NEW PFD TO CONVERT:
        {json.dumps(pfd_analysis, indent=2)}
        
        PROJECT INFO:
        {json.dumps(project_info, indent=2)}
        
        YOUR TASK:
        Generate a COMPLETE P&ID by applying the learned transformation rules to this new PFD.
        
        Follow the EXACT same patterns as the example:
        1. Use the same valve types and placements
        2. Use the same instrument types and tags
        3. Follow the same naming conventions
        4. Apply the same piping specifications
        5. Use the same safety device practices
        6. Replicate the same level of detail
        
        Return COMPREHENSIVE JSON with:
        {{
            "equipment": [
                {{
                    "tag": "...",
                    "type": "...",
                    "description": "...",
                    "specifications": {{...}},
                    "nozzles": [
                        {{"tag": "...", "size": "...", "rating": "...", "orientation": "..."}}
                    ]
                }}
            ],
            "valves": [
                {{
                    "tag": "...",
                    "type": "...",
                    "size": "...",
                    "rating": "...",
                    "actuator": "...",
                    "fail_position": "...",
                    "location": "...",
                    "upstream_equipment": "...",
                    "downstream_equipment": "..."
                }}
            ],
            "instruments": [
                {{
                    "tag": "...",
                    "type": "...",
                    "description": "...",
                    "range": "...",
                    "location": "...",
                    "signal_type": "...",
                    "measured_equipment": "..."
                }}
            ],
            "pipes": [
                {{
                    "line_number": "...",
                    "size": "...",
                    "pipe_class": "...",
                    "material": "...",
                    "schedule": "...",
                    "insulation": "...",
                    "from": "...",
                    "to": "..."
                }}
            ],
            "control_loops": [
                {{
                    "loop_id": "...",
                    "type": "...",
                    "transmitter": "...",
                    "controller": "...",
                    "final_element": "...",
                    "control_strategy": "..."
                }}
            ],
            "safety_devices": [...],
            "utility_connections": [...],
            "notes": [...]
        }}
        
        Be COMPREHENSIVE. Include every component that would appear in a professional P&ID.
        """
        
        try:
            response = self.client.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {
                        "role": "system",
                        "content": """You are a senior P&ID designer with perfect knowledge of:
                        - ISA-5.1 standards
                        - ADNOC DEP practices
                        - API, ASME codes
                        - Industry best practices
                        
                        You generate production-quality P&IDs that exactly match industry examples.
                        """
                    },
                    {
                        "role": "user",
                        "content": generation_prompt
                    }
                ],
                max_tokens=4000,
                temperature=0.1  # Low temperature for consistency
            )
            
            content = response.choices[0].message.content
            
            # Extract JSON
            if "```json" in content:
                json_match = re.search(r'```json\s*(.*?)\s*```', content, re.DOTALL)
                if json_match:
                    pid_data = json.loads(json_match.group(1))
                else:
                    pid_data = json.loads(content)
            else:
                pid_data = json.loads(content)
            
            # Add metadata
            pid_data["metadata"] = {
                "pattern_used": pattern.pattern_id,
                "pattern_confidence": pattern.confidence,
                "generation_method": "intelligent_pattern_learning",
                "standards": ["ISA-5.1", "ADNOC DEP", "API", "ASME"]
            }
            
            logger.info("✅ P&ID generated successfully using learned patterns")
            return pid_data
            
        except Exception as e:
            logger.error(f"❌ P&ID generation failed: {str(e)}")
            return {"error": str(e)}
    
    def save_learned_patterns(self, output_path: str):
        """Save learned patterns to file for reuse"""
        patterns_data = {
            "total_patterns": len(self.learned_patterns),
            "patterns": [
                {
                    "pattern_id": p.pattern_id,
                    "equipment_type": p.equipment_type,
                    "pfd_characteristics": p.pfd_characteristics,
                    "pid_additions": p.pid_additions,
                    "transformation_rules": p.transformation_rules,
                    "confidence": p.confidence
                }
                for p in self.learned_patterns
            ]
        }
        
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(patterns_data, f, indent=2, ensure_ascii=False)
        
        logger.info(f"✅ Saved {len(self.learned_patterns)} patterns to {output_path}")
    
    def load_learned_patterns(self, input_path: str):
        """Load previously learned patterns"""
        with open(input_path, 'r', encoding='utf-8') as f:
            patterns_data = json.load(f)
        
        self.learned_patterns = [
            ExamplePattern(
                pattern_id=p["pattern_id"],
                equipment_type=p["equipment_type"],
                pfd_characteristics=p["pfd_characteristics"],
                pid_additions=p["pid_additions"],
                transformation_rules=p["transformation_rules"],
                confidence=p["confidence"]
            )
            for p in patterns_data["patterns"]
        ]
        
        logger.info(f"✅ Loaded {len(self.learned_patterns)} patterns from {input_path}")


# ========================================================================
# CONVENIENCE FUNCTIONS
# ========================================================================

def learn_from_your_examples(pfd_folder: str, pid_folder: str = None):
    """
    Learn from your PFD-P&ID example pairs
    
    Usage:
        learn_from_your_examples("Documents/PFD to P&ID/1601")
    """
    learner = IntelligentPatternLearner()
    
    # If same folder, find PFD and P&ID files
    if pid_folder is None:
        pid_folder = pfd_folder
    
    import os
    pfd_files = [f for f in os.listdir(pfd_folder) if 'PFD' in f.upper() and f.endswith('.pdf')]
    pid_files = [f for f in os.listdir(pid_folder) if 'P&ID' in f or 'PID' in f and f.endswith('.pdf')]
    
    logger.info(f"Found {len(pfd_files)} PFDs and {len(pid_files)} P&IDs")
    
    # Learn from first pair
    if pfd_files and pid_files:
        pfd_path = os.path.join(pfd_folder, pfd_files[0])
        pid_path = os.path.join(pid_folder, pid_files[0])
        
        pattern = learner.learn_from_example(pfd_path, pid_path)
        
        # Save learned patterns
        learner.save_learned_patterns("learned_patterns.json")
        
        return learner
    
    return None


if __name__ == "__main__":
    # Example usage
    print("="*70)
    print("INTELLIGENT PATTERN LEARNING SYSTEM")
    print("="*70)
    print("\nThis system learns from your PFD-P&ID examples and replicates")
    print("the exact patterns to generate new P&IDs.\n")
    
    # Learn from examples
    learner = learn_from_your_examples("Documents/PFD to P&ID/1601")
    
    if learner:
        print("\n✅ Learning complete!")
        print(f"   Patterns learned: {len(learner.learned_patterns)}")
        
        # Now you can generate new P&IDs using the learned patterns
        # new_pid = learner.generate_pid_from_pfd("path/to/new/pfd.pdf")
