"""
Build comprehensive P&ID (Piping & Instrumentation Diagram) knowledge base from S3
P&IDs are more detailed than PFDs and include instrumentation, valve specs, piping classes
"""
import os
import boto3
import django
import json
import fitz  # PyMuPDF
from pathlib import Path
from collections import Counter
import re

# Setup Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

class PIDKnowledgeBaseBuilder:
    def __init__(self):
        self.s3_client = boto3.client(
            's3',
            region_name='ap-south-1',
            aws_access_key_id=os.getenv('AWS_ACCESS_KEY_ID'),
            aws_secret_access_key=os.getenv('AWS_SECRET_ACCESS_KEY')
        )
        self.knowledge_base = []
        self.samples_dir = Path('pid_training_samples')
        self.samples_dir.mkdir(exist_ok=True)
        
    def download_pid_samples(self, bucket_name, max_samples=10):
        """Download diverse P&ID samples from S3"""
        print(f"\n{'='*80}")
        print(f"DOWNLOADING P&ID SAMPLES FROM: {bucket_name}")
        print(f"{'='*80}\n")
        
        downloaded_files = []
        
        if bucket_name == 'rejlers-edrs-project':
            # Download from data_preprocessing folder (999 files including P&IDs)
            try:
                response = self.s3_client.list_objects_v2(
                    Bucket=bucket_name,
                    Prefix='data_preprocessing/',
                    MaxKeys=100
                )
                
                if 'Contents' in response:
                    for obj in response['Contents']:
                        # Look for P&ID files
                        if ('pid' in obj['Key'].lower() or 'p&id' in obj['Key'].lower()) and \
                           obj['Key'].lower().endswith('.pdf') and obj['Size'] > 10000:
                            
                            filename = obj['Key'].split('/')[-1]
                            local_path = self.samples_dir / filename
                            
                            print(f"  📥 Downloading: {filename} ({obj['Size']/1024:.1f} KB)")
                            self.s3_client.download_file(bucket_name, obj['Key'], str(local_path))
                            downloaded_files.append(local_path)
                            
                            if len(downloaded_files) >= max_samples:
                                break
            except Exception as e:
                print(f"  ❌ Error: {str(e)}")
        
        elif bucket_name == 'rejlers-pfd-and-pid':
            # Download P&IDs from project folders
            try:
                response = self.s3_client.list_objects_v2(Bucket=bucket_name, Delimiter='/')
                
                if 'CommonPrefixes' in response:
                    for prefix in response['CommonPrefixes'][:8]:  # First 8 projects
                        folder = prefix['Prefix']
                        
                        folder_response = self.s3_client.list_objects_v2(
                            Bucket=bucket_name,
                            Prefix=folder,
                            MaxKeys=30
                        )
                        
                        if 'Contents' in folder_response:
                            for obj in folder_response['Contents']:
                                # Look for P&ID files (PID but not PFD)
                                key_lower = obj['Key'].lower()
                                if ('pid' in key_lower or 'p&id' in key_lower) and \
                                   'pfd' not in key_lower and \
                                   key_lower.endswith('.pdf') and \
                                   obj['Size'] > 10000:
                                    
                                    filename = f"{folder.split('/')[0]}_{obj['Key'].split('/')[-1]}"
                                    filename = filename[:100]  # Limit filename length
                                    local_path = self.samples_dir / filename
                                    
                                    print(f"  📥 Downloading: {filename[:60]}... ({obj['Size']/1024:.1f} KB)")
                                    self.s3_client.download_file(bucket_name, obj['Key'], str(local_path))
                                    downloaded_files.append(local_path)
                                    
                                    if len(downloaded_files) >= max_samples:
                                        break
                        
                        if len(downloaded_files) >= max_samples:
                            break
                            
            except Exception as e:
                print(f"  ❌ Error: {str(e)}")
        
        print(f"\n✅ Downloaded {len(downloaded_files)} P&ID samples")
        return downloaded_files
    
    def extract_pid_patterns(self, pdf_path):
        """Extract instrumentation and piping patterns from P&ID"""
        print(f"\n  🔍 Analyzing: {pdf_path.name[:60]}...")
        
        patterns = {
            'filename': pdf_path.name,
            'text_content': '',
            'instrumentation': {
                'has_instruments': False,
                'instrument_types': [],
                'control_loops': []
            },
            'piping': {
                'has_piping_specs': False,
                'line_sizes': [],
                'piping_classes': []
            },
            'valves': {
                'has_valves': False,
                'valve_types': []
            },
            'equipment': {
                'tags': [],
                'types': []
            },
            'symbols': []
        }
        
        try:
            doc = fitz.open(str(pdf_path))
            full_text = ''
            
            for page in doc:
                full_text += page.get_text()
            
            patterns['text_content'] = full_text[:2000]  # First 2000 chars
            
            # Instrumentation patterns (ISA 5.1 standard)
            instrument_codes = {
                'FT': 'Flow Transmitter',
                'PT': 'Pressure Transmitter',
                'TT': 'Temperature Transmitter',
                'LT': 'Level Transmitter',
                'FIC': 'Flow Indicating Controller',
                'PIC': 'Pressure Indicating Controller',
                'TIC': 'Temperature Indicating Controller',
                'LIC': 'Level Indicating Controller',
                'FCV': 'Flow Control Valve',
                'PCV': 'Pressure Control Valve',
                'TCV': 'Temperature Control Valve',
                'LCV': 'Level Control Valve',
                'PSV': 'Pressure Safety Valve',
                'PSHH': 'Pressure Switch High-High',
                'PSLL': 'Pressure Switch Low-Low',
                'FSL': 'Flow Switch Low',
                'TSH': 'Temperature Switch High',
                'LAH': 'Level Alarm High',
                'PAH': 'Pressure Alarm High'
            }
            
            for code, description in instrument_codes.items():
                # Look for instrument tags like FT-101, PT-201A
                pattern = rf'\b{code}[-\s]?\d+[A-Z]?\b'
                if re.search(pattern, full_text, re.IGNORECASE):
                    patterns['instrumentation']['has_instruments'] = True
                    patterns['instrumentation']['instrument_types'].append(code)
            
            # Remove duplicates
            patterns['instrumentation']['instrument_types'] = list(set(
                patterns['instrumentation']['instrument_types']
            ))
            
            # Valve types
            valve_keywords = [
                'GATE VALVE', 'GLOBE VALVE', 'BALL VALVE', 'BUTTERFLY VALVE',
                'CHECK VALVE', 'CONTROL VALVE', 'SAFETY VALVE', 'RELIEF VALVE',
                'ISOLATION VALVE', 'BLOCK VALVE', 'NEEDLE VALVE'
            ]
            
            for valve_type in valve_keywords:
                if valve_type in full_text.upper():
                    patterns['valves']['has_valves'] = True
                    patterns['valves']['valve_types'].append(valve_type)
            
            # Piping specifications
            # Look for line sizes (e.g., 2", 4", 6" or DN50, DN100)
            line_size_pattern = r'(\d+["″]|DN\s?\d+)'
            line_sizes = re.findall(line_size_pattern, full_text)
            patterns['piping']['line_sizes'] = list(set(line_sizes))[:10]  # Top 10
            
            if line_sizes:
                patterns['piping']['has_piping_specs'] = True
            
            # Piping class patterns (e.g., 150#, 300#, CL150)
            piping_class_pattern = r'(\d{3,4}#|CL\s?\d{3,4}|CLASS\s?\d{3,4})'
            piping_classes = re.findall(piping_class_pattern, full_text, re.IGNORECASE)
            patterns['piping']['piping_classes'] = list(set(piping_classes))[:5]
            
            # Equipment tags (similar to PFD but more detailed)
            equipment_pattern = r'\b\d{3,4}[-\s]?[A-Z][-\s]?\d{3,4}[A-Z]?\b'
            equipment_tags = re.findall(equipment_pattern, full_text)
            patterns['equipment']['tags'] = list(set(equipment_tags))[:15]
            
            print(f"     ✓ Instruments: {len(patterns['instrumentation']['instrument_types'])} types")
            print(f"     ✓ Valves: {len(patterns['valves']['valve_types'])} types")
            print(f"     ✓ Line sizes: {len(patterns['piping']['line_sizes'])} found")
            print(f"     ✓ Equipment tags: {len(patterns['equipment']['tags'])} found")
            
            doc.close()
            
        except Exception as e:
            print(f"     ❌ Error: {str(e)}")
        
        return patterns
    
    def build_knowledge_base(self):
        """Build comprehensive P&ID knowledge base"""
        print(f"\n{'='*80}")
        print("BUILDING P&ID KNOWLEDGE BASE")
        print(f"{'='*80}\n")
        
        # Download samples from both buckets
        files1 = self.download_pid_samples('rejlers-edrs-project', max_samples=5)
        files2 = self.download_pid_samples('rejlers-pfd-and-pid', max_samples=8)
        
        all_files = files1 + files2
        
        if not all_files:
            print("❌ No P&ID samples found")
            return
        
        # Analyze each sample
        print(f"\n{'='*80}")
        print("ANALYZING P&ID PATTERNS")
        print(f"{'='*80}")
        
        for pdf_file in all_files:
            patterns = self.extract_pid_patterns(pdf_file)
            self.knowledge_base.append(patterns)
        
        # Save knowledge base
        kb_file = Path('pid_knowledge_base.json')
        with open(kb_file, 'w') as f:
            json.dump(self.knowledge_base, f, indent=2)
        
        print(f"\n✅ P&ID Knowledge base saved to: {kb_file}")
        print(f"   Total P&ID patterns: {len(self.knowledge_base)}")
        
        # Generate insights
        self.generate_insights()
        
        # Generate training examples
        self.generate_training_examples()
    
    def generate_insights(self):
        """Generate insights from collected patterns"""
        print(f"\n{'='*80}")
        print("P&ID PATTERN INSIGHTS")
        print(f"{'='*80}\n")
        
        # Collect all instrument types
        all_instruments = []
        for kb in self.knowledge_base:
            all_instruments.extend(kb['instrumentation']['instrument_types'])
        
        instrument_counts = Counter(all_instruments)
        
        print("Most Common Instrumentation:")
        for instrument, count in instrument_counts.most_common(10):
            print(f"  • {instrument}: {count} occurrences")
        
        # Collect valve types
        all_valves = []
        for kb in self.knowledge_base:
            all_valves.extend(kb['valves']['valve_types'])
        
        valve_counts = Counter(all_valves)
        
        print("\nMost Common Valve Types:")
        for valve, count in valve_counts.most_common(5):
            print(f"  • {valve}: {count} occurrences")
        
        # Statistics
        with_instruments = sum(1 for kb in self.knowledge_base 
                              if kb['instrumentation']['has_instruments'])
        with_valves = sum(1 for kb in self.knowledge_base 
                         if kb['valves']['has_valves'])
        with_piping = sum(1 for kb in self.knowledge_base 
                         if kb['piping']['has_piping_specs'])
        
        total = len(self.knowledge_base)
        print(f"\nP&ID Coverage:")
        print(f"  • Documents with instrumentation: {with_instruments}/{total}")
        print(f"  • Documents with valve specifications: {with_valves}/{total}")
        print(f"  • Documents with piping specs: {with_piping}/{total}")
    
    def generate_training_examples(self):
        """Generate P&ID training examples"""
        print(f"\n{'='*80}")
        print("GENERATING P&ID TRAINING EXAMPLES")
        print(f"{'='*80}\n")
        
        examples = []
        
        for pattern in self.knowledge_base:
            if pattern['instrumentation']['has_instruments']:
                example = {
                    'document': pattern['filename'][:60],
                    'instrument_types': pattern['instrumentation']['instrument_types'][:5],
                    'valve_types': pattern['valves']['valve_types'][:3],
                    'has_piping_specs': pattern['piping']['has_piping_specs'],
                    'line_sizes': pattern['piping']['line_sizes'][:5],
                    'extraction_approach': 'Focus on ISA 5.1 instrument tags, valve types, line sizes, and piping classes'
                }
                examples.append(example)
                print(f"  ✓ Generated example from: {pattern['filename'][:60]}")
        
        # Save training examples
        examples_file = Path('pid_training_examples.json')
        with open(examples_file, 'w') as f:
            json.dump(examples, f, indent=2)
        
        print(f"\n✅ Generated {len(examples)} P&ID training examples")
        print(f"   Saved to: {examples_file}")
        
        return examples

def main():
    print("\n" + "="*80)
    print(" P&ID KNOWLEDGE BASE BUILDER")
    print("="*80)
    
    builder = PIDKnowledgeBaseBuilder()
    builder.build_knowledge_base()
    
    print("\n" + "="*80)
    print("BUILD COMPLETE")
    print("="*80)
    print("\nNext Steps:")
    print("1. Review pid_knowledge_base.json for P&ID patterns")
    print("2. Check pid_training_examples.json for training data")
    print("3. Use these to enhance P&ID generation prompts")
    print("="*80 + "\n")

if __name__ == '__main__':
    main()
