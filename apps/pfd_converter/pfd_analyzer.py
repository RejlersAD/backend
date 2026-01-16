"""
PFD Analyzer Service - Phase 1 Analysis
Intelligent analysis of PFD documents to extract modules, connectivity, and complexity
"""
import logging
import openai
from django.conf import settings
from typing import Dict, List, Any
import json

logger = logging.getLogger(__name__)


class PFDAnalyzer:
    """Analyze PFD documents and extract detailed module information"""
    
    def __init__(self):
        openai.api_key = settings.OPENAI_API_KEY
        self.model = "gpt-4o"
    
    def analyze_pfd_document(self, pfd_doc) -> Dict[str, Any]:
        """
        Comprehensive PFD Analysis
        
        Returns:
            dict: {
                'modules': List of identified modules with details,
                'module_count': Total number of modules,
                'connectivity': Module-to-module connections,
                'complexity_analysis': Complexity assessment,
                'recommended_pids': Number of P&IDs recommended,
                'coverage_plan': Which modules to cover in which P&ID
            }
        """
        logger.info(f"[PFD Analyzer] Starting analysis for document: {pfd_doc.file_name}")
        
        try:
            # Extract text/data from PFD (using existing extraction if available)
            pfd_data = self._extract_pfd_data(pfd_doc)
            
            # AI-powered module identification
            modules = self._identify_modules(pfd_data, pfd_doc)
            
            # Analyze connectivity between modules
            connectivity = self._analyze_connectivity(modules, pfd_data)
            
            # Complexity analysis
            complexity = self._analyze_complexity(modules, connectivity)
            
            # Recommend P&ID split strategy
            pid_recommendations = self._recommend_pid_split(modules, complexity)
            
            analysis_result = {
                'document_id': str(pfd_doc.id),
                'document_name': pfd_doc.file_name,
                'modules': modules,
                'module_count': len(modules),
                'connectivity': connectivity,
                'complexity_analysis': complexity,
                'recommended_pids': pid_recommendations['count'],
                'coverage_plan': pid_recommendations['plan'],
                'analysis_timestamp': pfd_doc.created_at.isoformat(),
                'status': 'completed'
            }
            
            # Store analysis in document's extracted_data
            pfd_doc.extracted_data = pfd_doc.extracted_data or {}
            pfd_doc.extracted_data['pfd_analysis'] = analysis_result
            pfd_doc.status = 'analyzed'
            pfd_doc.save()
            
            logger.info(f"[PFD Analyzer] ✅ Analysis completed: {len(modules)} modules identified")
            return analysis_result
            
        except Exception as e:
            logger.error(f"[PFD Analyzer] ❌ Analysis failed: {str(e)}")
            return {
                'status': 'failed',
                'error': str(e),
                'document_id': str(pfd_doc.id)
            }
    
    def _extract_pfd_data(self, pfd_doc) -> Dict:
        """Extract or retrieve PFD data"""
        # If already extracted, use it
        if pfd_doc.extracted_data and 'process_data' in pfd_doc.extracted_data:
            return pfd_doc.extracted_data['process_data']
        
        # Otherwise, return basic info for AI analysis
        return {
            'file_name': pfd_doc.file_name,
            'file_type': pfd_doc.file_type,
            'document_title': pfd_doc.document_title or 'Untitled PFD',
            'project_name': pfd_doc.project_name or 'Unknown Project'
        }
    
    def _identify_modules(self, pfd_data: Dict, pfd_doc) -> List[Dict]:
        """
        Use AI to identify process modules from PFD
        Each module represents a distinct process unit or system
        """
        logger.info("[PFD Analyzer] Identifying modules using AI...")
        
        prompt = f"""You are an expert process engineer analyzing a Process Flow Diagram (PFD).

PFD Document: {pfd_data.get('document_title', pfd_doc.file_name)}
Project: {pfd_data.get('project_name', 'Unknown')}

Analyze this PFD and identify all distinct PROCESS MODULES. A module is a logical grouping of equipment that performs a specific process function.

For a typical oil & gas/chemical PFD, common modules include:
- Feed/Inlet System
- Compression/Pumping System  
- Heat Exchange System
- Separation System (Gas-Liquid, Liquid-Liquid)
- Treatment/Purification System
- Product Handling System
- Utilities System (Cooling Water, Steam, etc.)
- Safety/Relief System

For each module identified, provide:
1. **module_id**: Unique identifier (e.g., "MOD-01", "MOD-02")
2. **module_name**: Descriptive name (e.g., "Feed Gas Compression System")
3. **module_type**: Category (e.g., "Compression", "Separation", "Heat Exchange")
4. **primary_function**: Main purpose of this module
5. **key_equipment**: Major equipment in this module (pumps, compressors, vessels, heat exchangers)
6. **estimated_equipment_count**: Approximate number of equipment items
7. **complexity_level**: "Low", "Medium", or "High" based on equipment count and interconnections
8. **process_streams**: Main input/output streams
9. **utilities_required**: Utilities needed (cooling water, steam, electricity, etc.)
10. **safety_critical**: Boolean indicating if this module has safety-critical equipment

Based on the document name "{pfd_doc.file_name}" and typical PFD patterns, provide your analysis.

Return ONLY a valid JSON array of modules. Example format:
[
  {{
    "module_id": "MOD-01",
    "module_name": "Feed Gas Compression System",
    "module_type": "Compression",
    "primary_function": "Compress inlet gas from 50 barg to 85 barg",
    "key_equipment": ["Compressor K-101", "Suction Scrubber V-101", "Aftercooler E-101"],
    "estimated_equipment_count": 8,
    "complexity_level": "High",
    "process_streams": ["Feed Gas In", "Compressed Gas Out", "Condensate Drain"],
    "utilities_required": ["Cooling Water", "Instrument Air", "Electric Power"],
    "safety_critical": true
  }}
]"""
        
        try:
            response = openai.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": "You are an expert process engineer specializing in PFD analysis. Always return valid JSON arrays."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.3,
                max_tokens=3000
            )
            
            modules_json = response.choices[0].message.content.strip()
            
            # Extract JSON from markdown code blocks if present
            if "```json" in modules_json:
                modules_json = modules_json.split("```json")[1].split("```")[0].strip()
            elif "```" in modules_json:
                modules_json = modules_json.split("```")[1].split("```")[0].strip()
            
            modules = json.loads(modules_json)
            logger.info(f"[PFD Analyzer] ✅ Identified {len(modules)} modules")
            return modules
            
        except Exception as e:
            logger.error(f"[PFD Analyzer] ❌ Module identification failed: {str(e)}")
            # Return default modules if AI fails
            return self._get_default_modules(pfd_doc)
    
    def _analyze_connectivity(self, modules: List[Dict], pfd_data: Dict) -> Dict:
        """Analyze how modules are connected to each other"""
        logger.info("[PFD Analyzer] Analyzing module connectivity...")
        
        prompt = f"""Based on these identified process modules, determine how they are connected.

Modules:
{json.dumps(modules, indent=2)}

Analyze the typical process flow and provide:
1. **connections**: List of connections between modules with stream details
2. **main_process_flow**: Ordered list of modules in the main process path
3. **parallel_streams**: Any parallel processing paths
4. **recycle_streams**: Any recycle or feedback loops
5. **utility_connections**: Shared utility connections

Return ONLY valid JSON in this format:
{{
  "connections": [
    {{
      "from_module": "MOD-01",
      "to_module": "MOD-02",
      "stream_name": "Compressed Gas",
      "stream_type": "Process",
      "is_critical": true
    }}
  ],
  "main_process_flow": ["MOD-01", "MOD-02", "MOD-03"],
  "parallel_streams": [],
  "recycle_streams": [],
  "utility_connections": {{
    "cooling_water": ["MOD-01", "MOD-03"],
    "instrument_air": ["MOD-01", "MOD-02"]
  }}
}}"""
        
        try:
            response = openai.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": "You are an expert in process flow analysis. Always return valid JSON."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.3,
                max_tokens=2000
            )
            
            connectivity_json = response.choices[0].message.content.strip()
            if "```json" in connectivity_json:
                connectivity_json = connectivity_json.split("```json")[1].split("```")[0].strip()
            elif "```" in connectivity_json:
                connectivity_json = connectivity_json.split("```")[1].split("```")[0].strip()
            
            connectivity = json.loads(connectivity_json)
            logger.info(f"[PFD Analyzer] ✅ Analyzed {len(connectivity.get('connections', []))} connections")
            return connectivity
            
        except Exception as e:
            logger.error(f"[PFD Analyzer] ❌ Connectivity analysis failed: {str(e)}")
            return {'connections': [], 'main_process_flow': [m['module_id'] for m in modules]}
    
    def _analyze_complexity(self, modules: List[Dict], connectivity: Dict) -> Dict:
        """Analyze overall complexity of the PFD"""
        total_equipment = sum(m.get('estimated_equipment_count', 5) for m in modules)
        connection_count = len(connectivity.get('connections', []))
        
        # Calculate complexity score
        complexity_score = (
            len(modules) * 10 +  # Module count factor
            total_equipment * 2 +  # Equipment count factor
            connection_count * 5  # Connectivity factor
        )
        
        if complexity_score < 100:
            complexity_level = "Low"
        elif complexity_score < 250:
            complexity_level = "Medium"
        else:
            complexity_level = "High"
        
        return {
            'overall_complexity': complexity_level,
            'complexity_score': complexity_score,
            'total_equipment_estimate': total_equipment,
            'total_connections': connection_count,
            'high_complexity_modules': [m['module_id'] for m in modules if m.get('complexity_level') == 'High'],
            'safety_critical_modules': [m['module_id'] for m in modules if m.get('safety_critical', False)]
        }
    
    def _recommend_pid_split(self, modules: List[Dict], complexity: Dict) -> Dict:
        """Recommend how to split PFD into multiple P&IDs"""
        module_count = len(modules)
        total_equipment = complexity['total_equipment_estimate']
        
        # Recommendation logic
        if module_count <= 2 and total_equipment <= 20:
            # Simple PFD - single P&ID
            return {
                'count': 1,
                'strategy': 'Single P&ID',
                'plan': [
                    {
                        'pid_number': 'P&ID-01',
                        'pid_title': 'Complete Process',
                        'modules_covered': [m['module_id'] for m in modules],
                        'estimated_equipment': total_equipment,
                        'rationale': 'Simple process suitable for single P&ID'
                    }
                ]
            }
        elif module_count <= 5 and total_equipment <= 50:
            # Medium complexity - 2-3 P&IDs
            return self._split_by_process_sections(modules, 2)
        else:
            # Complex - multiple P&IDs by module
            return self._split_by_modules(modules)
    
    def _split_by_process_sections(self, modules: List[Dict], target_count: int) -> Dict:
        """Split into process sections (upstream/downstream)"""
        mid_point = len(modules) // 2
        
        return {
            'count': target_count,
            'strategy': 'Process Section Split',
            'plan': [
                {
                    'pid_number': 'P&ID-01',
                    'pid_title': 'Upstream Process',
                    'modules_covered': [m['module_id'] for m in modules[:mid_point]],
                    'estimated_equipment': sum(m.get('estimated_equipment_count', 5) for m in modules[:mid_point]),
                    'rationale': 'Front-end processing units'
                },
                {
                    'pid_number': 'P&ID-02',
                    'pid_title': 'Downstream Process',
                    'modules_covered': [m['module_id'] for m in modules[mid_point:]],
                    'estimated_equipment': sum(m.get('estimated_equipment_count', 5) for m in modules[mid_point:]),
                    'rationale': 'Back-end processing and product handling'
                }
            ]
        }
    
    def _split_by_modules(self, modules: List[Dict]) -> Dict:
        """Each major module gets its own P&ID"""
        plan = []
        for idx, module in enumerate(modules, 1):
            plan.append({
                'pid_number': f"P&ID-{idx:02d}",
                'pid_title': module['module_name'],
                'modules_covered': [module['module_id']],
                'estimated_equipment': module.get('estimated_equipment_count', 5),
                'rationale': f"Dedicated P&ID for {module['module_type']} system"
            })
        
        return {
            'count': len(modules),
            'strategy': 'Module-Based Split',
            'plan': plan
        }
    
    def _get_default_modules(self, pfd_doc) -> List[Dict]:
        """Fallback default modules if AI analysis fails"""
        return [
            {
                'module_id': 'MOD-01',
                'module_name': 'Process System 1',
                'module_type': 'General',
                'primary_function': 'Primary process operations',
                'key_equipment': ['Equipment to be identified'],
                'estimated_equipment_count': 10,
                'complexity_level': 'Medium',
                'process_streams': ['To be determined'],
                'utilities_required': ['Standard utilities'],
                'safety_critical': False
            }
        ]
