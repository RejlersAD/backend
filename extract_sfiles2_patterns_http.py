"""
SFILES2 Pattern Extraction - HTTP Download Method
Downloads SFILES2 repository from GitHub using HTTP instead of git
Extracts flowsheet patterns and integrates with existing knowledge bases
"""

import os
import json
import zipfile
import io
import requests
from datetime import datetime

def download_github_repo(owner, repo, branch='main'):
    """
    Download GitHub repository as ZIP file using HTTP
    
    Args:
        owner: Repository owner
        repo: Repository name
        branch: Branch name (default: main)
    
    Returns:
        Path to extracted directory
    """
    print(f"\n{'='*80}")
    print(f"DOWNLOADING SFILES2 REPOSITORY")
    print(f"{'='*80}\n")
    
    # Download URL for repository ZIP
    url = f"https://github.com/{owner}/{repo}/archive/refs/heads/{branch}.zip"
    print(f"📥 Downloading from: {url}")
    
    try:
        # Download ZIP file
        response = requests.get(url, timeout=30)
        response.raise_for_status()
        
        print(f"✅ Downloaded {len(response.content):,} bytes")
        
        # Extract ZIP to temp directory
        extract_path = f"./temp_sfiles2_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        os.makedirs(extract_path, exist_ok=True)
        
        print(f"📂 Extracting to: {extract_path}")
        with zipfile.ZipFile(io.BytesIO(response.content)) as zip_ref:
            zip_ref.extractall(extract_path)
        
        # Find extracted folder (should be SFILES2-main or similar)
        extracted_folders = [f for f in os.listdir(extract_path) if os.path.isdir(os.path.join(extract_path, f))]
        if extracted_folders:
            repo_path = os.path.join(extract_path, extracted_folders[0])
            print(f"✅ Repository extracted to: {repo_path}")
            return repo_path
        else:
            raise Exception("No folder found in extracted ZIP")
            
    except requests.exceptions.RequestException as e:
        print(f"❌ Failed to download repository: {e}")
        return None
    except Exception as e:
        print(f"❌ Error extracting repository: {e}")
        return None


def analyze_sfiles2_structure(repo_path):
    """
    Analyze SFILES2 repository structure and identify relevant files
    
    Args:
        repo_path: Path to SFILES2 repository
    
    Returns:
        Dictionary with file inventory
    """
    print(f"\n{'='*80}")
    print(f"ANALYZING SFILES2 STRUCTURE")
    print(f"{'='*80}\n")
    
    inventory = {
        'python_files': [],
        'json_files': [],
        'xml_files': [],
        'demonstration_files': [],
        'flowsheet_data': []
    }
    
    # Walk through repository
    for root, dirs, files in os.walk(repo_path):
        for file in files:
            file_path = os.path.join(root, file)
            relative_path = os.path.relpath(file_path, repo_path)
            
            # Categorize files
            if file.endswith('.py'):
                inventory['python_files'].append(relative_path)
                if 'demonstration' in file.lower() or 'example' in file.lower():
                    inventory['demonstration_files'].append(relative_path)
            elif file.endswith('.json'):
                inventory['json_files'].append(relative_path)
            elif file.endswith('.xml') or file.endswith('.graphml'):
                inventory['xml_files'].append(relative_path)
            elif 'flowsheet' in file.lower() or 'sfiles' in file.lower():
                inventory['flowsheet_data'].append(relative_path)
    
    # Print summary
    print(f"📊 Repository Analysis:")
    print(f"   - Python files: {len(inventory['python_files'])}")
    print(f"   - JSON files: {len(inventory['json_files'])}")
    print(f"   - XML/GraphML files: {len(inventory['xml_files'])}")
    print(f"   - Demonstration files: {len(inventory['demonstration_files'])}")
    print(f"   - Flowsheet data files: {len(inventory['flowsheet_data'])}")
    
    return inventory


def extract_sfiles_notation_patterns(repo_path):
    """
    Extract SFILES notation patterns from Python code and examples
    
    Args:
        repo_path: Path to SFILES2 repository
    
    Returns:
        List of SFILES pattern examples
    """
    print(f"\n{'='*80}")
    print(f"EXTRACTING SFILES NOTATION PATTERNS")
    print(f"{'='*80}\n")
    
    patterns = []
    
    # Look for SFILES strings in demonstration files
    demo_file = os.path.join(repo_path, 'run_demonstration.py')
    if os.path.exists(demo_file):
        print(f"📄 Analyzing: run_demonstration.py")
        
        with open(demo_file, 'r', encoding='utf-8') as f:
            content = f.read()
            
            # Extract SFILES strings (patterns enclosed in quotes with SFILES notation)
            import re
            sfiles_pattern = r'sfiles[_\w]*\s*=\s*["\']([^"\']+)["\']'
            matches = re.findall(sfiles_pattern, content)
            
            for match in matches:
                if '(' in match and ')' in match:  # Basic validation
                    patterns.append({
                        'sfiles_string': match,
                        'source': 'run_demonstration.py',
                        'type': 'example'
                    })
                    print(f"   ✓ Found SFILES: {match[:80]}{'...' if len(match) > 80 else ''}")
    
    print(f"\n✅ Extracted {len(patterns)} SFILES notation patterns")
    return patterns


def extract_unit_operation_mappings(repo_path):
    """
    Extract unit operation abbreviations from OntoCape mapping
    
    Args:
        repo_path: Path to SFILES2 repository
    
    Returns:
        Dictionary of unit operation mappings
    """
    print(f"\n{'='*80}")
    print(f"EXTRACTING UNIT OPERATION MAPPINGS")
    print(f"{'='*80}\n")
    
    mappings = {}
    
    # Look for OntoCape SFILES mapping file
    mapping_file = os.path.join(repo_path, 'Flowsheet_Class', 'OntoCape_SFILES_mapping.py')
    if os.path.exists(mapping_file):
        print(f"📄 Analyzing: OntoCape_SFILES_mapping.py")
        
        with open(mapping_file, 'r', encoding='utf-8') as f:
            content = f.read()
            
            # Extract mapping dictionary
            import re
            mapping_pattern = r'["\']([^"\']+)["\']\s*:\s*["\']([^"\']+)["\']'
            matches = re.findall(mapping_pattern, content)
            
            for full_name, abbreviation in matches:
                mappings[abbreviation] = full_name
                print(f"   ✓ {abbreviation:10s} → {full_name}")
    
    print(f"\n✅ Extracted {len(mappings)} unit operation mappings")
    return mappings


def integrate_with_knowledge_bases(sfiles_patterns, unit_mappings):
    """
    Integrate SFILES2 patterns with existing PFD/P&ID knowledge bases
    
    Args:
        sfiles_patterns: List of SFILES notation patterns
        unit_mappings: Dictionary of unit operation mappings
    
    Returns:
        Updated knowledge bases
    """
    print(f"\n{'='*80}")
    print(f"INTEGRATING WITH KNOWLEDGE BASES")
    print(f"{'='*80}\n")
    
    # Load existing knowledge bases
    pfd_kb_path = '/app/pfd_knowledge_base.json'
    pid_kb_path = '/app/pid_knowledge_base.json'
    
    # Output paths for updated KBs
    pfd_kb_output_path = '/app/pfd_knowledge_base_with_sfiles2.json'
    pid_kb_output_path = '/app/pid_knowledge_base_with_sfiles2.json'
    
    pfd_kb = []
    pid_kb = []
    
    if os.path.exists(pfd_kb_path):
        with open(pfd_kb_path, 'r') as f:
            pfd_kb = json.load(f)
        # Handle both list and dict formats
        if isinstance(pfd_kb, list):
            print(f"✅ Loaded existing PFD knowledge base: {len(pfd_kb)} documents")
        else:
            print(f"✅ Loaded existing PFD knowledge base: {len(pfd_kb.get('documents', []))} documents")
    
    if os.path.exists(pid_kb_path):
        with open(pid_kb_path, 'r') as f:
            pid_kb = json.load(f)
        # Handle both list and dict formats
        if isinstance(pid_kb, list):
            print(f"✅ Loaded existing P&ID knowledge base: {len(pid_kb)} documents")
        else:
            print(f"✅ Loaded existing P&ID knowledge base: {len(pid_kb.get('documents', []))} documents")
    
    # Create SFILES2 integration section
    sfiles2_integration = {
        'source': 'SFILES2 Research Repository',
        'repository': 'https://github.com/process-intelligence-research/SFILES2',
        'extraction_date': datetime.now().isoformat(),
        'notation_patterns': sfiles_patterns,
        'unit_operation_mappings': unit_mappings,
        'insights': {
            'notation_system': 'SFILES 2.0 (Simplified Flowsheet Input Line Entry System)',
            'representation': 'Text-based flowsheet representation similar to SMILES for molecules',
            'applications': [
                'Automatic flowsheet generation',
                'Process synthesis',
                'Process optimization',
                'Flowsheet pattern recognition'
            ],
            'key_concepts': {
                'nodes': 'Unit operations enclosed in parentheses: (pump), (hex), (dist)',
                'connections': 'Sequential connections indicated by adjacency',
                'branches': 'Branches denoted by square brackets [ ]',
                'recycles': 'Material recycles marked with numbers',
                'heat_integration': 'Heat integration tagged with curly braces { }',
                'control': 'Control structure represented with special notation'
            }
        }
    }
    
    # Convert list format to dict format with SFILES2 integration
    if isinstance(pfd_kb, list):
        pfd_kb_dict = {
            'documents': pfd_kb,
            'sfiles2_integration': sfiles2_integration
        }
        print(f"✅ Added SFILES2 integration to PFD knowledge base")
    else:
        pfd_kb_dict = pfd_kb.copy()
        pfd_kb_dict['sfiles2_integration'] = sfiles2_integration
        print(f"✅ Added SFILES2 integration to PFD knowledge base")
    
    if isinstance(pid_kb, list):
        pid_kb_dict = {
            'documents': pid_kb,
            'sfiles2_integration': sfiles2_integration
        }
        print(f"✅ Added SFILES2 integration to P&ID knowledge base")
    else:
        pid_kb_dict = pid_kb.copy()
        pid_kb_dict['sfiles2_integration'] = sfiles2_integration
        print(f"✅ Added SFILES2 integration to P&ID knowledge base")
    
    # Save updated knowledge bases to new files
    with open(pfd_kb_output_path, 'w') as f:
        json.dump(pfd_kb_dict, f, indent=2)
    print(f"💾 Saved updated PFD knowledge base to: {pfd_kb_output_path}")
    
    with open(pid_kb_output_path, 'w') as f:
        json.dump(pid_kb_dict, f, indent=2)
    print(f"💾 Saved updated P&ID knowledge base to: {pid_kb_output_path}")
    
    return pfd_kb_dict, pid_kb_dict


def generate_sfiles2_documentation():
    """
    Generate documentation for SFILES2 integration
    
    Returns:
        Documentation string
    """
    doc = """
# SFILES2 Integration Summary

## Repository Information
- **Name**: SFILES 2.0 (Simplified Flowsheet Input Line Entry System)
- **Source**: https://github.com/process-intelligence-research/SFILES2
- **Purpose**: Text-based representation of process flowsheets (PFDs and P&IDs)
- **Research**: Process Intelligence Research Group

## Key Concepts

### SFILES Notation System
SFILES 2.0 is inspired by SMILES (Simplified Molecular Input Line Entry System) used in chemistry.
It provides a compact text-based representation of process flowsheets.

### Notation Elements

1. **Unit Operations**: Enclosed in parentheses
   - (raw) = Raw material input
   - (prod) = Product output
   - (pump) = Pump (pp)
   - (hex) = Heat exchanger
   - (dist) = Distillation column
   - (r) = Reactor
   - (mix) = Mixer
   - (splt) = Splitter
   - (flash) = Flash drum
   - (comp) = Compressor
   - (v) = Valve

2. **Connections**: Sequential unit operations are connected by adjacency
   - Example: (raw)(pump)(hex)(dist)(prod)

3. **Branches**: Indicated by square brackets
   - Example: (splt)[(prod)](prod) = Splitter with two product streams

4. **Recycles**: Material recycles marked with numbers
   - Example: (r)1(sep)(r)<1 = Reactor with recycle loop

5. **Heat Integration**: Tagged with curly braces
   - Example: (hex){1}...(hex){1} = Heat integrated exchangers

6. **Control Structure**: Special notation for control loops
   - Represented with underscore notation: _#

## Unit Operation Abbreviations

| Abbreviation | Full Name |
|--------------|-----------|
| raw | RawMaterial |
| prod | OutputProduct |
| hex | HeatExchanger |
| pump/pp | Pump |
| comp | Compressor |
| turb | Expander/Turbine |
| r | ChemicalReactor |
| splt | SplittingUnit |
| mix | MixingUnit |
| dist | DistillationSystem |
| flash | FlashUnit |
| abs | AbsorptionColumn |
| v | Valve |
| sep | SeparationUnit |
| tank | StorageUnit |

## Integration with RAG System

The SFILES2 patterns have been integrated into both PFD and P&ID knowledge bases:

1. **Notation Patterns**: Real SFILES examples from research repository
2. **Unit Mappings**: Comprehensive abbreviation dictionary
3. **Concept Library**: Key flowsheet representation concepts
4. **Application Context**: Process synthesis and optimization insights

## Benefits for AI System

1. **Structured Representation**: Learn systematic flowsheet encoding
2. **Pattern Recognition**: Understand common process configurations
3. **Abbreviation Standards**: Use standardized unit operation names
4. **Topology Understanding**: Recognize branches, recycles, and connections
5. **Research-Backed**: Based on published academic research

## Citation

Vogel, G., Hirtreiter, E., Schulze Balhorn, L., & Schweidtmann, A. M. (2023).
SFILES 2.0: an extended text-based flowsheet representation.
Optimization and Engineering, 24(4), 2911-2933.

## Next Steps

The AI system can now:
1. Recognize SFILES notation patterns
2. Use standardized unit operation abbreviations
3. Understand flowsheet topology (branches, recycles, heat integration)
4. Apply research-backed process engineering knowledge
"""
    return doc


def main():
    """
    Main execution pipeline
    """
    print(f"\n{'#'*80}")
    print(f"#{'SFILES2 PATTERN EXTRACTION PIPELINE (HTTP METHOD)'.center(78)}#")
    print(f"{'#'*80}\n")
    
    # Step 1: Download repository
    repo_path = download_github_repo('process-intelligence-research', 'SFILES2', 'main')
    
    if not repo_path:
        print("\n❌ Failed to download repository. Exiting.")
        return
    
    # Step 2: Analyze structure
    inventory = analyze_sfiles2_structure(repo_path)
    
    # Step 3: Extract SFILES patterns
    sfiles_patterns = extract_sfiles_notation_patterns(repo_path)
    
    # Step 4: Extract unit operation mappings
    unit_mappings = extract_unit_operation_mappings(repo_path)
    
    # Step 5: Integrate with knowledge bases
    pfd_kb, pid_kb = integrate_with_knowledge_bases(sfiles_patterns, unit_mappings)
    
    # Step 6: Generate documentation
    documentation = generate_sfiles2_documentation()
    
    # Save documentation
    doc_path = '/app/SFILES2_INTEGRATION.md'
    with open(doc_path, 'w') as f:
        f.write(documentation)
    print(f"\n📝 Documentation saved to: {doc_path}")
    
    # Print summary
    print(f"\n{'='*80}")
    print(f"INTEGRATION COMPLETE")
    print(f"{'='*80}\n")
    print(f"✅ SFILES Patterns Extracted: {len(sfiles_patterns)}")
    print(f"✅ Unit Operation Mappings: {len(unit_mappings)}")
    
    # Get document count safely
    pfd_doc_count = len(pfd_kb.get('documents', [])) if isinstance(pfd_kb, dict) else len(pfd_kb)
    pid_doc_count = len(pid_kb.get('documents', [])) if isinstance(pid_kb, dict) else len(pid_kb)
    
    print(f"✅ PFD Knowledge Base Updated: {pfd_doc_count} documents + SFILES2")
    print(f"✅ P&ID Knowledge Base Updated: {pid_doc_count} documents + SFILES2")
    print(f"✅ Documentation Generated: {doc_path}")
    print(f"\n🎉 RAG system enhanced with SFILES2 research patterns!\n")


if __name__ == "__main__":
    main()
