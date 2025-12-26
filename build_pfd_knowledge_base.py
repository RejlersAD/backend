"""
Build a comprehensive PFD knowledge base from S3 bucket for RAG system
This script analyzes existing PFD documents to extract patterns and create training examples
"""
import os
import boto3
import django
import json
import fitz  # PyMuPDF
from pathlib import Path
from PIL import Image
from io import BytesIO
import base64

# Setup Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from apps.pfd_converter.models import PFDDocument

class PFDKnowledgeBaseBuilder:
    def __init__(self):
        self.s3_client = boto3.client(
            's3',
            region_name='ap-south-1',
            aws_access_key_id=os.getenv('AWS_ACCESS_KEY_ID'),
            aws_secret_access_key=os.getenv('AWS_SECRET_ACCESS_KEY')
        )
        self.knowledge_base = []
        self.samples_dir = Path('pfd_training_samples')
        self.samples_dir.mkdir(exist_ok=True)
        
    def download_pfd_samples(self, bucket_name, max_samples=10):
        """Download diverse PFD samples from S3"""
        print(f"\n{'='*80}")
        print(f"DOWNLOADING PFD SAMPLES FROM: {bucket_name}")
        print(f"{'='*80}\n")
        
        downloaded_files = []
        
        # Check PFD_to_PID folder in rejlers-edrs-project
        if bucket_name == 'rejlers-edrs-project':
            try:
                response = self.s3_client.list_objects_v2(
                    Bucket=bucket_name,
                    Prefix='PFD_to_PID/',
                    MaxKeys=max_samples
                )
                
                if 'Contents' in response:
                    for obj in response['Contents']:
                        if obj['Key'].lower().endswith('.pdf') and obj['Size'] > 1000:
                            filename = obj['Key'].split('/')[-1]
                            local_path = self.samples_dir / filename
                            
                            print(f"  📥 Downloading: {filename} ({obj['Size']/1024:.1f} KB)")
                            self.s3_client.download_file(bucket_name, obj['Key'], str(local_path))
                            downloaded_files.append(local_path)
                            
                            if len(downloaded_files) >= max_samples:
                                break
            except Exception as e:
                print(f"  ❌ Error: {str(e)}")
        
        # Also download from rejlers-pfd-and-pid (get PFD files from project folders)
        elif bucket_name == 'rejlers-pfd-and-pid':
            try:
                # Get all folders
                response = self.s3_client.list_objects_v2(Bucket=bucket_name, Delimiter='/')
                
                if 'CommonPrefixes' in response:
                    for prefix in response['CommonPrefixes'][:5]:  # First 5 projects
                        folder = prefix['Prefix']
                        
                        # List files in folder
                        folder_response = self.s3_client.list_objects_v2(
                            Bucket=bucket_name,
                            Prefix=folder,
                            MaxKeys=20
                        )
                        
                        if 'Contents' in folder_response:
                            for obj in folder_response['Contents']:
                                # Look for PFD files specifically
                                if ('pfd' in obj['Key'].lower() and 
                                    obj['Key'].lower().endswith('.pdf') and 
                                    obj['Size'] > 1000):
                                    
                                    filename = f"{folder.split('/')[0]}_{obj['Key'].split('/')[-1]}"
                                    local_path = self.samples_dir / filename
                                    
                                    print(f"  📥 Downloading: {filename} ({obj['Size']/1024:.1f} KB)")
                                    self.s3_client.download_file(bucket_name, obj['Key'], str(local_path))
                                    downloaded_files.append(local_path)
                                    
                                    if len(downloaded_files) >= max_samples:
                                        break
                        
                        if len(downloaded_files) >= max_samples:
                            break
                            
            except Exception as e:
                print(f"  ❌ Error: {str(e)}")
        
        print(f"\n✅ Downloaded {len(downloaded_files)} PFD samples")
        return downloaded_files
    
    def extract_pfd_patterns(self, pdf_path):
        """Extract common patterns from a PFD document"""
        print(f"\n  🔍 Analyzing: {pdf_path.name}")
        
        patterns = {
            'filename': pdf_path.name,
            'text_content': '',
            'has_equipment': False,
            'has_streams': False,
            'has_flow_info': False,
            'equipment_types': [],
            'stream_info': []
        }
        
        try:
            # Extract text from PDF
            doc = fitz.open(str(pdf_path))
            full_text = ''
            
            for page in doc:
                full_text += page.get_text()
            
            patterns['text_content'] = full_text[:1000]  # First 1000 chars
            
            # Identify equipment patterns
            equipment_keywords = ['PUMP', 'COMPRESSOR', 'HEAT EXCHANGER', 'VESSEL', 
                                'TANK', 'REACTOR', 'COLUMN', 'SEPARATOR', 'HEATER', 
                                'COOLER', 'DRUM', 'TOWER']
            
            for keyword in equipment_keywords:
                if keyword in full_text.upper():
                    patterns['has_equipment'] = True
                    patterns['equipment_types'].append(keyword)
            
            # Identify stream information
            stream_keywords = ['STREAM', 'FLOW', 'RATE', 'TEMPERATURE', 'PRESSURE', 
                             'kg/h', 'm³/h', '°C', 'bar', 'psig']
            
            for keyword in stream_keywords:
                if keyword in full_text:
                    patterns['has_streams'] = True
                    patterns['has_flow_info'] = True
            
            print(f"     ✓ Found: {len(patterns['equipment_types'])} equipment types")
            print(f"     ✓ Has flow info: {patterns['has_flow_info']}")
            
            doc.close()
            
        except Exception as e:
            print(f"     ❌ Error: {str(e)}")
        
        return patterns
    
    def build_knowledge_base(self):
        """Build comprehensive knowledge base from all samples"""
        print(f"\n{'='*80}")
        print("BUILDING PFD KNOWLEDGE BASE")
        print(f"{'='*80}\n")
        
        # Download samples from both buckets
        files1 = self.download_pfd_samples('rejlers-edrs-project', max_samples=5)
        files2 = self.download_pfd_samples('rejlers-pfd-and-pid', max_samples=5)
        
        all_files = files1 + files2
        
        if not all_files:
            print("❌ No PFD samples found")
            return
        
        # Analyze each sample
        print(f"\n{'='*80}")
        print("ANALYZING PFD PATTERNS")
        print(f"{'='*80}")
        
        for pdf_file in all_files:
            patterns = self.extract_pfd_patterns(pdf_file)
            self.knowledge_base.append(patterns)
        
        # Save knowledge base
        kb_file = Path('pfd_knowledge_base.json')
        with open(kb_file, 'w') as f:
            json.dump(self.knowledge_base, f, indent=2)
        
        print(f"\n✅ Knowledge base saved to: {kb_file}")
        print(f"   Total patterns: {len(self.knowledge_base)}")
        
        # Generate training examples
        self.generate_training_examples()
    
    def generate_training_examples(self):
        """Generate few-shot learning examples from patterns"""
        print(f"\n{'='*80}")
        print("GENERATING TRAINING EXAMPLES")
        print(f"{'='*80}\n")
        
        examples = []
        
        for pattern in self.knowledge_base:
            if pattern['has_equipment'] and pattern['has_flow_info']:
                example = {
                    'document': pattern['filename'],
                    'equipment_found': pattern['equipment_types'][:3],
                    'has_process_data': True,
                    'extraction_approach': 'Focus on equipment tags, stream numbers, and process conditions'
                }
                examples.append(example)
                print(f"  ✓ Generated example from: {pattern['filename']}")
        
        # Save training examples
        examples_file = Path('pfd_training_examples.json')
        with open(examples_file, 'w') as f:
            json.dump(examples, f, indent=2)
        
        print(f"\n✅ Generated {len(examples)} training examples")
        print(f"   Saved to: {examples_file}")
        
        return examples

def main():
    print("\n" + "="*80)
    print(" PFD KNOWLEDGE BASE BUILDER")
    print("="*80)
    
    builder = PFDKnowledgeBaseBuilder()
    builder.build_knowledge_base()
    
    print("\n" + "="*80)
    print("BUILD COMPLETE")
    print("="*80)
    print("\nNext Steps:")
    print("1. Review pfd_knowledge_base.json for patterns")
    print("2. Check pfd_training_examples.json for training data")
    print("3. Use these to enhance the AI prompt in services.py")
    print("="*80 + "\n")

if __name__ == '__main__':
    main()
