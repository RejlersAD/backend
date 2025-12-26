"""
Extract patterns from SFILES2 GitHub repository and integrate into knowledge base
Repository: https://github.com/process-intelligence-research/SFILES2.git
"""
import os
import django
import json
import subprocess
from pathlib import Path
import shutil
import fitz  # PyMuPDF
import re
from collections import Counter

# Setup Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

class SFILESPatternExtractor:
    """
    Extract PFD/P&ID patterns from SFILES2 repository
    """
    
    def __init__(self):
        self.repo_url = "https://github.com/process-intelligence-research/SFILES2.git"
        self.repo_dir = Path('sfiles2_repo')
        self.patterns = {
            'pfd_patterns': [],
            'pid_patterns': [],
            'symbols': [],
            'equipment': [],
            'instrumentation': []
        }
        
    def clone_repository(self):
        """Clone the SFILES2 repository"""
        print(f"\n{'='*80}")
        print("CLONING SFILES2 REPOSITORY")
        print(f"{'='*80}\n")
        
        # Remove existing directory if present
        if self.repo_dir.exists():
            print(f"  🗑️  Removing existing repository...")
            shutil.rmtree(self.repo_dir)
        
        # Clone repository
        print(f"  📥 Cloning from: {self.repo_url}")
        try:
            result = subprocess.run(
                ['git', 'clone', '--depth', '1', self.repo_url, str(self.repo_dir)],
                capture_output=True,
                text=True,
                timeout=300
            )
            
            if result.returncode == 0:
                print(f"  ✅ Repository cloned successfully")
                return True
            else:
                print(f"  ❌ Clone failed: {result.stderr}")
                return False
                
        except Exception as e:
            print(f"  ❌ Error: {str(e)}")
            return False
    
    def analyze_repository_structure(self):
        """Analyze the repository structure"""
        print(f"\n{'='*80}")
        print("ANALYZING REPOSITORY STRUCTURE")
        print(f"{'='*80}\n")
        
        if not self.repo_dir.exists():
            print("❌ Repository directory not found")
            return
        
        # Find all relevant files
        pdf_files = list(self.repo_dir.rglob('*.pdf'))
        png_files = list(self.repo_dir.rglob('*.png'))
        jpg_files = list(self.repo_dir.rglob('*.jpg'))
        json_files = list(self.repo_dir.rglob('*.json'))
        xml_files = list(self.repo_dir.rglob('*.xml'))
        txt_files = list(self.repo_dir.rglob('*.txt'))
        md_files = list(self.repo_dir.rglob('*.md'))
        
        print(f"Repository Contents:")
        print(f"  📄 PDF files: {len(pdf_files)}")
        print(f"  🖼️  Image files (PNG/JPG): {len(png_files) + len(jpg_files)}")
        print(f"  📋 JSON files: {len(json_files)}")
        print(f"  📋 XML files: {len(xml_files)}")
        print(f"  📝 Text files: {len(txt_files)}")
        print(f"  📝 Markdown files: {len(md_files)}")
        
        # Show directory structure
        print(f"\n  Directory Structure:")
        for item in sorted(self.repo_dir.iterdir())[:20]:
            if item.is_dir():
                file_count = len(list(item.rglob('*')))
                print(f"    📁 {item.name}/ ({file_count} files)")
            else:
                print(f"    📄 {item.name}")
        
        return {
            'pdf_files': pdf_files,
            'image_files': png_files + jpg_files,
            'json_files': json_files,
            'xml_files': xml_files,
            'txt_files': txt_files,
            'md_files': md_files
        }
    
    def extract_pdf_patterns(self, pdf_files, max_files=10):
        """Extract patterns from PDF files"""
        print(f"\n{'='*80}")
        print(f"EXTRACTING PATTERNS FROM PDF FILES")
        print(f"{'='*80}\n")
        
        patterns = []
        
        for i, pdf_file in enumerate(pdf_files[:max_files], 1):
            print(f"  {i}/{min(len(pdf_files), max_files)}: {pdf_file.name}")
            
            try:
                pattern = self._analyze_pdf(pdf_file)
                if pattern:
                    patterns.append(pattern)
                    print(f"     ✓ Extracted: {len(pattern.get('equipment', []))} equipment, "
                          f"{len(pattern.get('instruments', []))} instruments")
            except Exception as e:
                print(f"     ❌ Error: {str(e)}")
        
        print(f"\n✅ Extracted {len(patterns)} patterns from PDFs")
        return patterns
    
    def _analyze_pdf(self, pdf_path):
        """Analyze a single PDF file"""
        pattern = {
            'filename': pdf_path.name,
            'path': str(pdf_path.relative_to(self.repo_dir)),
            'equipment': [],
            'instruments': [],
            'symbols': [],
            'text_content': ''
        }
        
        try:
            doc = fitz.open(str(pdf_path))
            full_text = ''
            
            for page in doc:
                full_text += page.get_text()
            
            pattern['text_content'] = full_text[:1500]
            
            # Extract equipment tags
            equipment_patterns = [
                r'\b[A-Z]{1,2}[-\s]?\d{3,4}[A-Z]?\b',  # P-101, V-201A
                r'\b\d{3,4}[-\s]?[A-Z][-\s]?\d{3,4}\b',  # 562-V-203
            ]
            
            for eq_pattern in equipment_patterns:
                equipment = re.findall(eq_pattern, full_text)
                pattern['equipment'].extend(equipment)
            
            pattern['equipment'] = list(set(pattern['equipment']))[:15]
            
            # Extract instrument tags (ISA 5.1)
            instrument_codes = [
                'FT', 'PT', 'TT', 'LT', 'AT',
                'FIC', 'PIC', 'TIC', 'LIC', 'AIC',
                'FCV', 'PCV', 'TCV', 'LCV',
                'PSV', 'FE', 'PE', 'TE', 'LE',
                'FSL', 'FSH', 'PSL', 'PSH', 'TSL', 'TSH', 'LSL', 'LSH',
                'PAH', 'PAL', 'TAH', 'TAL', 'LAH', 'LAL',
                'PSHH', 'PSLL', 'TSHH', 'TSLL', 'LSHH', 'LSLL'
            ]
            
            for code in instrument_codes:
                inst_pattern = rf'\b{code}[-\s]?\d+[A-Z]?\b'
                instruments = re.findall(inst_pattern, full_text, re.IGNORECASE)
                if instruments:
                    pattern['instruments'].extend([code] * len(instruments))
            
            pattern['instruments'] = list(set(pattern['instruments']))
            
            doc.close()
            
        except Exception as e:
            pass
        
        return pattern
    
    def extract_json_patterns(self, json_files):
        """Extract patterns from JSON annotation files"""
        print(f"\n{'='*80}")
        print(f"EXTRACTING PATTERNS FROM JSON FILES")
        print(f"{'='*80}\n")
        
        patterns = []
        
        for json_file in json_files[:20]:  # First 20 JSON files
            print(f"  📋 {json_file.name}")
            
            try:
                with open(json_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                
                pattern = {
                    'filename': json_file.name,
                    'path': str(json_file.relative_to(self.repo_dir)),
                    'data_type': self._identify_json_type(data),
                    'content': data
                }
                
                patterns.append(pattern)
                print(f"     ✓ Type: {pattern['data_type']}")
                
            except Exception as e:
                print(f"     ❌ Error: {str(e)}")
        
        print(f"\n✅ Extracted {len(patterns)} JSON patterns")
        return patterns
    
    def _identify_json_type(self, data):
        """Identify the type of JSON data"""
        if isinstance(data, dict):
            keys = set(data.keys())
            
            if 'annotations' in keys or 'shapes' in keys:
                return 'annotations'
            elif 'equipment' in keys or 'instruments' in keys:
                return 'equipment_data'
            elif 'symbols' in keys:
                return 'symbol_library'
            else:
                return 'general'
        return 'unknown'
    
    def analyze_documentation(self, md_files):
        """Analyze markdown documentation"""
        print(f"\n{'='*80}")
        print(f"ANALYZING DOCUMENTATION")
        print(f"{'='*80}\n")
        
        insights = {
            'dataset_info': '',
            'standards': [],
            'methodology': ''
        }
        
        for md_file in md_files[:10]:
            print(f"  📝 {md_file.name}")
            
            try:
                with open(md_file, 'r', encoding='utf-8') as f:
                    content = f.read()
                
                # Extract key information
                if 'README' in md_file.name.upper():
                    insights['dataset_info'] = content[:2000]
                
                # Look for standards mentioned
                standard_keywords = ['ISO', 'ISA', 'API', 'ANSI', 'DIN', 'P&ID', 'PFD']
                for keyword in standard_keywords:
                    if keyword in content:
                        insights['standards'].append(keyword)
                
                print(f"     ✓ Analyzed")
                
            except Exception as e:
                print(f"     ❌ Error: {str(e)}")
        
        insights['standards'] = list(set(insights['standards']))
        return insights
    
    def generate_comprehensive_report(self, all_patterns):
        """Generate comprehensive analysis report"""
        print(f"\n{'='*80}")
        print("COMPREHENSIVE ANALYSIS REPORT")
        print(f"{'='*80}\n")
        
        # Aggregate statistics
        all_equipment = []
        all_instruments = []
        
        for pattern in all_patterns.get('pdf_patterns', []):
            all_equipment.extend(pattern.get('equipment', []))
            all_instruments.extend(pattern.get('instruments', []))
        
        equipment_counter = Counter(all_equipment)
        instrument_counter = Counter(all_instruments)
        
        print("Most Common Equipment Tags:")
        for tag, count in equipment_counter.most_common(15):
            print(f"  • {tag}: {count} occurrences")
        
        print("\nMost Common Instrumentation:")
        for instr, count in instrument_counter.most_common(15):
            print(f"  • {instr}: {count} occurrences")
        
        # Save comprehensive report
        report = {
            'source': 'SFILES2 GitHub Repository',
            'repository': self.repo_url,
            'total_pdf_patterns': len(all_patterns.get('pdf_patterns', [])),
            'total_json_patterns': len(all_patterns.get('json_patterns', [])),
            'equipment_statistics': dict(equipment_counter.most_common(20)),
            'instrument_statistics': dict(instrument_counter.most_common(20)),
            'standards_found': all_patterns.get('documentation', {}).get('standards', []),
            'patterns': all_patterns
        }
        
        report_file = Path('sfiles2_analysis_report.json')
        with open(report_file, 'w') as f:
            json.dump(report, f, indent=2)
        
        print(f"\n✅ Comprehensive report saved: {report_file}")
        return report
    
    def integrate_with_existing_kb(self, new_patterns):
        """Integrate SFILES2 patterns with existing knowledge bases"""
        print(f"\n{'='*80}")
        print("INTEGRATING WITH EXISTING KNOWLEDGE BASES")
        print(f"{'='*80}\n")
        
        # Load existing PFD knowledge base
        pfd_kb_file = Path('pfd_knowledge_base.json')
        if pfd_kb_file.exists():
            with open(pfd_kb_file, 'r') as f:
                pfd_kb = json.load(f)
            print(f"  📚 Loaded existing PFD KB: {len(pfd_kb)} documents")
        else:
            pfd_kb = []
            print(f"  📚 No existing PFD KB found, creating new")
        
        # Load existing P&ID knowledge base
        pid_kb_file = Path('pid_knowledge_base.json')
        if pid_kb_file.exists():
            with open(pid_kb_file, 'r') as f:
                pid_kb = json.load(f)
            print(f"  📚 Loaded existing P&ID KB: {len(pid_kb)} documents")
        else:
            pid_kb = []
            print(f"  📚 No existing P&ID KB found, creating new")
        
        # Add SFILES2 patterns to PFD KB
        added_pfd = 0
        for pattern in new_patterns.get('pdf_patterns', []):
            if pattern.get('equipment'):
                pfd_entry = {
                    'filename': f"SFILES2_{pattern['filename']}",
                    'source': 'SFILES2 Repository',
                    'path': pattern['path'],
                    'text_content': pattern['text_content'],
                    'has_equipment': len(pattern['equipment']) > 0,
                    'equipment_types': self._classify_equipment_types(pattern['equipment']),
                    'equipment_tags': pattern['equipment'][:10]
                }
                pfd_kb.append(pfd_entry)
                added_pfd += 1
        
        # Add SFILES2 patterns to P&ID KB
        added_pid = 0
        for pattern in new_patterns.get('pdf_patterns', []):
            if pattern.get('instruments'):
                pid_entry = {
                    'filename': f"SFILES2_{pattern['filename']}",
                    'source': 'SFILES2 Repository',
                    'path': pattern['path'],
                    'instrumentation': {
                        'has_instruments': True,
                        'instrument_types': pattern['instruments']
                    },
                    'equipment': {
                        'tags': pattern['equipment'][:10]
                    }
                }
                pid_kb.append(pid_entry)
                added_pid += 1
        
        # Save updated knowledge bases
        with open(pfd_kb_file, 'w') as f:
            json.dump(pfd_kb, f, indent=2)
        print(f"\n  ✅ Updated PFD KB: Added {added_pfd} new patterns (Total: {len(pfd_kb)})")
        
        with open(pid_kb_file, 'w') as f:
            json.dump(pid_kb, f, indent=2)
        print(f"  ✅ Updated P&ID KB: Added {added_pid} new patterns (Total: {len(pid_kb)})")
        
        return {
            'pfd_kb_size': len(pfd_kb),
            'pid_kb_size': len(pid_kb),
            'added_pfd': added_pfd,
            'added_pid': added_pid
        }
    
    def _classify_equipment_types(self, equipment_tags):
        """Classify equipment based on tags"""
        types = []
        for tag in equipment_tags:
            if 'P-' in tag or '-P-' in tag:
                types.append('PUMP')
            elif 'V-' in tag or '-V-' in tag:
                types.append('VESSEL')
            elif 'E-' in tag or '-E-' in tag:
                types.append('EXCHANGER')
            elif 'C-' in tag or '-C-' in tag:
                types.append('COMPRESSOR')
            elif 'T-' in tag or '-T-' in tag:
                types.append('TOWER')
        return list(set(types))
    
    def run_complete_analysis(self):
        """Run complete analysis pipeline"""
        print("\n" + "="*80)
        print(" SFILES2 PATTERN EXTRACTION PIPELINE")
        print("="*80)
        
        # Step 1: Clone repository
        if not self.clone_repository():
            print("\n❌ Failed to clone repository")
            return False
        
        # Step 2: Analyze structure
        file_info = self.analyze_repository_structure()
        
        # Step 3: Extract patterns from PDFs
        pdf_patterns = self.extract_pdf_patterns(file_info['pdf_files'])
        
        # Step 4: Extract patterns from JSON
        json_patterns = self.extract_json_patterns(file_info['json_files'])
        
        # Step 5: Analyze documentation
        documentation = self.analyze_documentation(file_info['md_files'])
        
        # Step 6: Generate comprehensive report
        all_patterns = {
            'pdf_patterns': pdf_patterns,
            'json_patterns': json_patterns,
            'documentation': documentation
        }
        report = self.generate_comprehensive_report(all_patterns)
        
        # Step 7: Integrate with existing knowledge bases
        integration_result = self.integrate_with_existing_kb(all_patterns)
        
        # Final summary
        print("\n" + "="*80)
        print("ANALYSIS COMPLETE")
        print("="*80)
        print(f"\nResults:")
        print(f"  • PDF patterns extracted: {len(pdf_patterns)}")
        print(f"  • JSON patterns extracted: {len(json_patterns)}")
        print(f"  • Standards found: {', '.join(documentation['standards'])}")
        print(f"  • Added to PFD KB: {integration_result['added_pfd']} patterns")
        print(f"  • Added to P&ID KB: {integration_result['added_pid']} patterns")
        print(f"  • Total PFD KB size: {integration_result['pfd_kb_size']} documents")
        print(f"  • Total P&ID KB size: {integration_result['pid_kb_size']} documents")
        print("\n" + "="*80 + "\n")
        
        return True

def main():
    extractor = SFILESPatternExtractor()
    success = extractor.run_complete_analysis()
    
    if success:
        print("✅ SFILES2 patterns successfully extracted and integrated!")
        print("\nNext steps:")
        print("1. Restart backend services to load new patterns")
        print("2. Test the enhanced system")
        print("3. Upload PFD at http://localhost:3000/pfd/upload")
    else:
        print("❌ Pattern extraction failed")

if __name__ == '__main__':
    main()
