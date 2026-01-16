"""
DEXPI P&ID Converter Integration with Django Views
"""

from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework import status
from django.http import JsonResponse, HttpResponse
import json
import logging

from .dexpi_pid_converter import DEXPIPIDConverter

logger = logging.getLogger(__name__)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def convert_pfd_to_pid_dexpi(request):
    """
    API Endpoint: Convert PFD to P&ID using DEXPI rule-based converter
    
    POST /api/v1/pfd/convert-dexpi/
    
    Request Body:
    {
        "pfd_graph": {
            "nodes": [...],
            "edges": [...]
        },
        "project_info": {
            "project_name": "...",
            "project_code": "...",
            "area": "..."
        },
        "export_format": "json" | "neo4j" | "both"
    }
    
    Response:
    {
        "success": true,
        "pid_graph": {...},
        "neo4j_cypher": "...",
        "statistics": {...}
    }
    """
    
    try:
        # Parse request
        data = request.data
        pfd_graph = data.get('pfd_graph')
        project_info = data.get('project_info', {})
        export_format = data.get('export_format', 'json')
        
        if not pfd_graph:
            return Response({
                "success": False,
                "error": "Missing 'pfd_graph' in request body"
            }, status=status.HTTP_400_BAD_REQUEST)
        
        # Validate PFD graph structure
        if 'nodes' not in pfd_graph:
            return Response({
                "success": False,
                "error": "PFD graph must contain 'nodes' array"
            }, status=status.HTTP_400_BAD_REQUEST)
        
        logger.info(f"🔄 Starting DEXPI conversion for user: {request.user.email}")
        logger.info(f"   PFD nodes: {len(pfd_graph.get('nodes', []))}")
        logger.info(f"   PFD edges: {len(pfd_graph.get('edges', []))}")
        
        # Initialize converter
        converter = DEXPIPIDConverter(project_info=project_info)
        
        # Convert PFD to P&ID
        pid_graph = converter.convert(pfd_graph)
        
        # Prepare response
        response_data = {
            "success": True,
            "pid_graph": pid_graph,
            "statistics": pid_graph.get("statistics", {}),
            "metadata": pid_graph.get("metadata", {}),
            "control_loops": pid_graph.get("control_loops", [])
        }
        
        # Add Neo4j export if requested
        if export_format in ['neo4j', 'both']:
            cypher_statements = _generate_neo4j_cypher(pid_graph)
            response_data['neo4j_cypher'] = cypher_statements
        
        logger.info(f"✅ DEXPI conversion successful:")
        logger.info(f"   Total nodes: {pid_graph['metadata']['total_nodes']}")
        logger.info(f"   Total edges: {pid_graph['metadata']['total_edges']}")
        logger.info(f"   Control loops: {pid_graph['metadata']['control_loops']}")
        
        return Response(response_data, status=status.HTTP_200_OK)
    
    except Exception as e:
        logger.error(f"❌ DEXPI conversion failed: {str(e)}", exc_info=True)
        return Response({
            "success": False,
            "error": str(e)
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def convert_pfd_file_to_pid_dexpi(request):
    """
    API Endpoint: Upload PFD file and convert to P&ID using DEXPI converter
    
    POST /api/v1/pfd/upload-convert-dexpi/
    
    Form Data:
    - pfd_file: PDF/Image file
    - project_name: string
    - project_code: string
    - area: string
    
    Response:
    {
        "success": true,
        "pid_graph": {...},
        "statistics": {...}
    }
    """
    
    try:
        pfd_file = request.FILES.get('pfd_file')
        project_name = request.data.get('project_name', 'Untitled Project')
        project_code = request.data.get('project_code', 'PROJ-001')
        area = request.data.get('area', 'Process')
        
        if not pfd_file:
            return Response({
                "success": False,
                "error": "No PFD file uploaded"
            }, status=status.HTTP_400_BAD_REQUEST)
        
        logger.info(f"🔄 Processing PFD file: {pfd_file.name}")
        
        # Step 1: Extract PFD data using existing vision system
        from .services_advanced_pipeline import AdvancedPFDToPIDPipeline
        
        pipeline = AdvancedPFDToPIDPipeline()
        
        # Extract PFD graph using computer vision
        pfd_graph = pipeline._step1_extract_pfd_data(pfd_file)
        
        if not pfd_graph:
            return Response({
                "success": False,
                "error": "Failed to extract PFD data from file"
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        
        # Step 2: Convert using DEXPI rule-based converter
        project_info = {
            "project_name": project_name,
            "project_code": project_code,
            "area": area
        }
        
        converter = DEXPIPIDConverter(project_info=project_info)
        pid_graph = converter.convert(pfd_graph)
        
        logger.info(f"✅ File conversion successful: {pfd_file.name}")
        
        return Response({
            "success": True,
            "filename": pfd_file.name,
            "pid_graph": pid_graph,
            "statistics": pid_graph.get("statistics", {}),
            "metadata": pid_graph.get("metadata", {})
        }, status=status.HTTP_200_OK)
    
    except Exception as e:
        logger.error(f"❌ File conversion failed: {str(e)}", exc_info=True)
        return Response({
            "success": False,
            "error": str(e)
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_engineering_rules(request):
    """
    API Endpoint: Get list of engineering rules used in DEXPI converter
    
    GET /api/v1/pfd/engineering-rules/
    
    Response:
    {
        "rules": [
            {
                "name": "Pump Expansion Rule",
                "description": "...",
                "reference": "ADNOC DEP 31.40.10.31-Gen, API 610"
            },
            ...
        ]
    }
    """
    
    rules = [
        {
            "name": "Pump Expansion Rule",
            "description": "For each pump, adds: suction isolation valve, discharge check valve, discharge isolation valve, discharge pressure indicator, suction and discharge nozzles",
            "reference": "ADNOC DEP 31.40.10.31-Gen, API 610",
            "components_added": ["Isolation Valves (2)", "Check Valve (1)", "Pressure Indicator (1)", "Nozzles (2)"]
        },
        {
            "name": "Vessel Expansion Rule",
            "description": "For each vessel/tank, adds: inlet/outlet nozzles, level transmitter, pressure indicator, safety valve (if pressure vessel)",
            "reference": "ASME Section VIII, ADNOC DEP",
            "components_added": ["Nozzles (2)", "Level Transmitter (1)", "Pressure Indicator (1)", "Safety Valve (conditional)"]
        },
        {
            "name": "Heat Exchanger Rule",
            "description": "For each heat exchanger, adds: shell/tube nozzles, temperature indicators, pressure indicators",
            "reference": "TEMA Standards, ADNOC DEP",
            "components_added": ["Nozzles (4)", "Temperature Indicators (4)", "Pressure Indicators (2)"]
        },
        {
            "name": "Control Loop Rule",
            "description": "Creates complete control loops with transmitter, controller, and control valve with proper signal connections",
            "reference": "ISA-5.1, ISA-5.4",
            "components_added": ["Transmitter (FT/PT/TT/LT)", "Controller (FC/PC/TC/LC)", "Control Valve"]
        },
        {
            "name": "Pipe Rule",
            "description": "Preserves all PFD pipes and enhances with P&ID specifications (pipe class, material, size)",
            "reference": "ASME B31.3",
            "components_added": ["Pipe specifications", "Material codes", "Schedule"]
        },
        {
            "name": "Tagging Rule",
            "description": "All components assigned unique ISA-5.1 compliant tag numbers",
            "reference": "ISA-5.1",
            "tag_format": "PREFIX-###"
        }
    ]
    
    return Response({
        "success": True,
        "total_rules": len(rules),
        "rules": rules,
        "standards": ["DEXPI", "ISO 15926", "ISA-5.1", "ADNOC DEP", "API", "ASME"]
    }, status=status.HTTP_200_OK)


def _generate_neo4j_cypher(pid_graph: dict) -> str:
    """Generate Neo4j Cypher import statements"""
    
    cypher_lines = []
    cypher_lines.append("// DEXPI P&ID Graph - Neo4j Import\n")
    cypher_lines.append("// Generated by Rule-Based PFD to P&ID Converter\n\n")
    
    # Create nodes
    cypher_lines.append("// ===== CREATE NODES =====\n")
    for node in pid_graph.get("nodes", []):
        props = {
            "id": node["id"],
            "tag": node["tag"],
            "description": node["description"],
            **node.get("properties", {})
        }
        props_str = ", ".join([f"{k}: '{v}'" if isinstance(v, str) else f"{k}: {v}" 
                              for k, v in props.items()])
        cypher = f"CREATE (:{node['type']} {{{props_str}}})\n"
        cypher_lines.append(cypher)
    
    cypher_lines.append("\n// ===== CREATE RELATIONSHIPS =====\n")
    for edge in pid_graph.get("edges", []):
        props = edge.get("properties", {})
        props_str = ", ".join([f"{k}: '{v}'" if isinstance(v, str) else f"{k}: {v}" 
                              for k, v in props.items()]) if props else ""
        props_clause = f" {{{props_str}}}" if props_str else ""
        cypher = f"MATCH (a {{id: '{edge['from']}'}}), (b {{id: '{edge['to']}'}}) CREATE (a)-[:{edge['relationship']}{props_clause}]->(b)\n"
        cypher_lines.append(cypher)
    
    return "".join(cypher_lines)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def download_pid_graph(request):
    """
    API Endpoint: Download P&ID graph as file
    
    POST /api/v1/pfd/download-pid/
    
    Request Body:
    {
        "pid_graph": {...},
        "format": "json" | "neo4j"
    }
    
    Response: File download
    """
    
    try:
        pid_graph = request.data.get('pid_graph')
        file_format = request.data.get('format', 'json')
        
        if not pid_graph:
            return Response({
                "success": False,
                "error": "Missing 'pid_graph' in request body"
            }, status=status.HTTP_400_BAD_REQUEST)
        
        if file_format == 'json':
            # JSON download
            response = HttpResponse(
                json.dumps(pid_graph, indent=2),
                content_type='application/json'
            )
            response['Content-Disposition'] = 'attachment; filename="pid_dexpi_graph.json"'
            return response
        
        elif file_format == 'neo4j':
            # Neo4j Cypher download
            cypher = _generate_neo4j_cypher(pid_graph)
            response = HttpResponse(cypher, content_type='text/plain')
            response['Content-Disposition'] = 'attachment; filename="pid_neo4j_import.cypher"'
            return response
        
        else:
            return Response({
                "success": False,
                "error": f"Unsupported format: {file_format}"
            }, status=status.HTTP_400_BAD_REQUEST)
    
    except Exception as e:
        logger.error(f"❌ Download failed: {str(e)}")
        return Response({
            "success": False,
            "error": str(e)
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
