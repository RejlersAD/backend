"""
Smart PFD-P&ID Database Builder
Scans AWS S3 "Assembly" folder and creates organized database in "pfd_database" folder
Uses intelligent pattern matching to pair PFDs with their corresponding P&IDs
"""

import os
import boto3
import json
import re
from pathlib import Path
from datetime import datetime
from collections import defaultdict
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class PFDDatabaseBuilder:
    """
    Intelligent PFD-P&ID database builder
    - Scans Assembly folder for all PFD and P&ID files
    - Uses smart matching to pair related documents
    - Organizes into structured database with metadata
    - Creates searchable index
    """
    
    def __init__(self):
        """Initialize AWS S3 client"""
        self.bucket_name = os.environ.get('AWS_STORAGE_BUCKET_NAME', 'rejlers-engineering-data')
        self.region = os.environ.get('AWS_S3_REGION_NAME', 'me-central-1')
        self.s3_client = boto3.client(
            's3',
            aws_access_key_id=os.environ.get('AWS_ACCESS_KEY_ID'),
            aws_secret_access_key=os.environ.get('AWS_SECRET_ACCESS_KEY'),
            region_name=self.region
        )
        
        # Database structure
        self.pfd_files = []
        self.pid_files = []
        self.paired_documents = []
        self.database_metadata = {
            'created_at': datetime.now().isoformat(),
            'total_pfds': 0,
            'total_pids': 0,
            'total_pairs': 0,
            'categories': {},
            'index': {}
        }
    
    def scan_assembly_folder(self):
        """
        Scan the Assembly folder in S3 bucket
        Structure: Assembly/input/ (PFDs) and Assembly/output/ (P&IDs)
        Returns list of all files with metadata
        """
        logger.info(f"🔍 Scanning S3 bucket: {self.bucket_name}/Assembly/")
        
        try:
            # List all objects in Assembly folder recursively
            paginator = self.s3_client.get_paginator('list_objects_v2')
            pages = paginator.paginate(Bucket=self.bucket_name, Prefix='Assembly/')
            
            all_files = []
            for page in pages:
                if 'Contents' in page:
                    for obj in page['Contents']:
                        # Skip the folder markers
                        if obj['Key'].endswith('/'):
                            continue
                            
                        file_info = {
                            'key': obj['Key'],
                            'size': obj['Size'],
                            'last_modified': obj['LastModified'].isoformat(),
                            'filename': os.path.basename(obj['Key']),
                            'folder_path': os.path.dirname(obj['Key'])
                        }
                        all_files.append(file_info)
            
            logger.info(f"✅ Found {len(all_files)} files in Assembly folder")
            
            # Log structure
            input_files = [f for f in all_files if '/input/' in f['key']]
            output_files = [f for f in all_files if '/output/' in f['key']]
            logger.info(f"   📂 Input (PFDs): {len(input_files)} files")
            logger.info(f"   📂 Output (P&IDs): {len(output_files)} files")
            
            return all_files
            
        except Exception as e:
            logger.error(f"❌ Error scanning Assembly folder: {str(e)}")
            return []
    
    def classify_documents(self, files):
        """
        Intelligently classify files as PFD or P&ID
        The Assembly folder structure:
        - Assembly/input/ contains PFDs
        - Assembly/output/ contains P&IDs
        """
        logger.info("🤖 Classifying documents using folder structure...")
        
        for file_info in files:
            # Skip non-PDF files
            if not file_info['filename'].lower().endswith('.pdf'):
                continue
            
            # Classify by folder structure (most reliable)
            if '/input/' in file_info['key']:
                file_info['type'] = 'PFD'
                file_info['category'] = self._extract_category(file_info['folder_path'])
                self.pfd_files.append(file_info)
            elif '/output/' in file_info['key']:
                file_info['type'] = 'PID'
                file_info['category'] = self._extract_category(file_info['folder_path'])
                self.pid_files.append(file_info)
            else:
                # Root level files - try pattern matching
                filename_lower = file_info['filename'].lower()
                if 'pfd' in filename_lower:
                    file_info['type'] = 'PFD'
                    file_info['category'] = 'misc'
                    self.pfd_files.append(file_info)
                elif 'p&id' in filename_lower or 'pid' in filename_lower:
                    file_info['type'] = 'PID'
                    file_info['category'] = 'misc'
                    self.pid_files.append(file_info)
                else:
                    file_info['type'] = 'REFERENCE'
                    file_info['category'] = 'misc'
        
        logger.info(f"✅ Classification complete:")
        logger.info(f"   📊 PFDs: {len(self.pfd_files)}")
        logger.info(f"   📐 P&IDs: {len(self.pid_files)}")
        
        # Log categories
        pfd_categories = {}
        for pfd in self.pfd_files:
            cat = pfd.get('category', 'unknown')
            pfd_categories[cat] = pfd_categories.get(cat, 0) + 1
        
        logger.info("   📂 PFD Categories:")
        for cat, count in pfd_categories.items():
            logger.info(f"      - {cat}: {count}")
    
    def _extract_category(self, folder_path):
        """
        Extract equipment/system category from folder path
        Example: Assembly/input/Control Valve/BDV → BDV
        """
        parts = folder_path.split('/')
        # Get the last meaningful part
        if len(parts) >= 4:
            return f"{parts[-2]}/{parts[-1]}" if parts[-1] else parts[-2]
        elif len(parts) >= 3:
            return parts[-1] if parts[-1] else parts[-2]
        return 'misc'
    
    def _extract_drawing_number(self, filename):
        """
        Extract standard drawing number from filename
        Format: P16093-XX-XX-XX-XXXX-X
        """
        pattern = r'P\d{5}-\d{2}-\d{2}-\d{2}-\d{4}-\d'
        match = re.search(pattern, filename.upper())
        return match.group(0) if match else None
    
    def pair_documents(self):
        """
        Intelligently pair PFDs with their corresponding P&IDs
        Uses folder structure and naming patterns
        The Assembly structure already pairs them by category/equipment type
        """
        logger.info("🔗 Pairing PFDs with corresponding P&IDs...")
        
        # Group by category
        pfd_by_category = {}
        for pfd in self.pfd_files:
            category = pfd.get('category', 'misc')
            if category not in pfd_by_category:
                pfd_by_category[category] = []
            pfd_by_category[category].append(pfd)
        
        pid_by_category = {}
        for pid in self.pid_files:
            category = pid.get('category', 'misc')
            if category not in pid_by_category:
                pid_by_category[category] = []
            pid_by_category[category].append(pid)
        
        # Match PFDs with P&IDs by category
        for category, pfds in pfd_by_category.items():
            pids = pid_by_category.get(category, [])
            
            if pids:
                pair = {
                    'pfds': pfds,
                    'pids': pids,
                    'category': category,
                    'confidence': 'high',
                    'relationship': f"{len(pfds)} PFD(s) → {len(pids)} P&ID(s)"
                }
                self.paired_documents.append(pair)
                logger.info(f"✅ Paired category '{category}': {len(pfds)} PFD(s) ↔ {len(pids)} P&ID(s)")
            else:
                # PFD with no matching P&ID
                pair = {
                    'pfds': pfds,
                    'pids': [],
                    'category': category,
                    'confidence': 'none',
                    'relationship': 'unpaired'
                }
                self.paired_documents.append(pair)
                logger.warning(f"⚠️ No P&ID found for category: {category}")
        
        # Check for P&IDs without PFDs
        for category, pids in pid_by_category.items():
            if category not in pfd_by_category:
                pair = {
                    'pfds': [],
                    'pids': pids,
                    'category': category,
                    'confidence': 'none',
                    'relationship': 'orphaned-pids'
                }
                self.paired_documents.append(pair)
                logger.warning(f"⚠️ P&IDs without PFD in category: {category}")
        
        logger.info(f"✅ Pairing complete: {len(self.paired_documents)} category groups created")
    
    def create_database_structure(self):
        """
        Create organized database structure in S3
        Structure:
        pfd_database/
            ├── index.json (searchable index)
            ├── metadata.json (database metadata)
            ├── categories/
            │   ├── Control_Valve_BDV/
            │   │   ├── pfd/
            │   │   │   └── BDV_PFD.pdf
            │   │   └── pid/
            │   │       └── BDV_P&ID.pdf
            │   ├── PUMP/
            │   └── PSV/
            └── reference/
                └── Legends Combine file.pdf
        """
        logger.info("📁 Creating database structure in S3...")
        
        # Copy files to organized structure by category
        for pair in self.paired_documents:
            category = pair['category'].replace('/', '_').replace(' ', '_')
            logger.info(f"📂 Processing category: {category}")
            
            # Copy PFDs
            for pfd in pair['pfds']:
                pfd_dest = f"pfd_database/categories/{category}/pfd/{pfd['filename']}"
                self._copy_s3_object(pfd['key'], pfd_dest)
                
                # Update index
                if pfd['filename'] not in self.database_metadata['index']:
                    self.database_metadata['index'][pfd['filename']] = {
                        'type': 'PFD',
                        'category': pair['category'],
                        'pfd_path': pfd_dest,
                        'pid_paths': [],
                        'confidence': pair['confidence']
                    }
            
            # Copy P&IDs
            for pid in pair['pids']:
                pid_dest = f"pfd_database/categories/{category}/pid/{pid['filename']}"
                self._copy_s3_object(pid['key'], pid_dest)
                
                # Link P&ID to PFD in index
                for pfd in pair['pfds']:
                    if pfd['filename'] in self.database_metadata['index']:
                        self.database_metadata['index'][pfd['filename']]['pid_paths'].append(pid_dest)
                
                # Also create P&ID entry in index
                if pid['filename'] not in self.database_metadata['index']:
                    self.database_metadata['index'][pid['filename']] = {
                        'type': 'PID',
                        'category': pair['category'],
                        'pid_path': pid_dest,
                        'related_pfds': [pfd['filename'] for pfd in pair['pfds']]
                    }
        
        # Update metadata
        self.database_metadata['total_pfds'] = len(self.pfd_files)
        self.database_metadata['total_pids'] = len(self.pid_files)
        self.database_metadata['total_pairs'] = len(self.paired_documents)
        
        # Category summary
        for pair in self.paired_documents:
            category = pair['category']
            self.database_metadata['categories'][category] = {
                'pfds': len(pair['pfds']),
                'pids': len(pair['pids']),
                'confidence': pair['confidence']
            }
        
        # Upload metadata and index
        self._upload_json('pfd_database/metadata.json', self.database_metadata)
        self._upload_json('pfd_database/index.json', self.database_metadata['index'])
        
        logger.info("✅ Database structure created successfully!")
    
    def _copy_s3_object(self, source_key, dest_key):
        """Copy object within S3 bucket"""
        try:
            copy_source = {'Bucket': self.bucket_name, 'Key': source_key}
            self.s3_client.copy_object(
                CopySource=copy_source,
                Bucket=self.bucket_name,
                Key=dest_key
            )
            logger.info(f"   ✅ Copied: {source_key} → {dest_key}")
        except Exception as e:
            logger.error(f"   ❌ Failed to copy {source_key}: {str(e)}")
    
    def _upload_json(self, key, data):
        """Upload JSON data to S3"""
        try:
            self.s3_client.put_object(
                Bucket=self.bucket_name,
                Key=key,
                Body=json.dumps(data, indent=2),
                ContentType='application/json'
            )
            logger.info(f"✅ Uploaded: {key}")
        except Exception as e:
            logger.error(f"❌ Failed to upload {key}: {str(e)}")
    
    def generate_report(self):
        """Generate comprehensive database report"""
        report = f"""
═══════════════════════════════════════════════════════════════════
                    PFD-P&ID DATABASE REPORT
═══════════════════════════════════════════════════════════════════

📊 Database Statistics:
   • Total PFDs: {self.database_metadata['total_pfds']}
   • Total P&IDs: {self.database_metadata['total_pids']}
   • Total Pairs: {self.database_metadata['total_pairs']}
   • Created: {self.database_metadata['created_at']}

📂 Categories:
"""
        for category, count in self.database_metadata['categories'].items():
            report += f"   • {category}: {count} pairs\n"
        
        report += f"""
🔗 Pairing Summary:
   • High Confidence: {sum(1 for p in self.paired_documents if p['confidence'] == 'high')}
   • Low Confidence: {sum(1 for p in self.paired_documents if p['confidence'] == 'low')}
   • Unpaired: {sum(1 for p in self.paired_documents if p['confidence'] == 'none')}

📍 Database Location:
   • S3 Bucket: {self.bucket_name}
   • Path: pfd_database/
   • Index: pfd_database/index.json
   • Metadata: pfd_database/metadata.json

═══════════════════════════════════════════════════════════════════
"""
        return report
    
    def build(self):
        """Execute complete database build process"""
        logger.info("🚀 Starting PFD-P&ID Database Builder...")
        
        # Step 1: Scan Assembly folder
        files = self.scan_assembly_folder()
        if not files:
            logger.error("❌ No files found in Assembly folder")
            return
        
        # Step 2: Classify documents
        self.classify_documents(files)
        
        # Step 3: Pair documents
        self.pair_documents()
        
        # Step 4: Create database structure
        self.create_database_structure()
        
        # Step 5: Generate report
        report = self.generate_report()
        print(report)
        
        # Save report locally
        report_path = Path(__file__).parent / 'pfd_database_report.txt'
        report_path.write_text(report)
        logger.info(f"✅ Report saved: {report_path}")
        
        logger.info("🎉 Database build complete!")


if __name__ == '__main__':
    # Load environment variables
    from decouple import config
    
    os.environ['AWS_ACCESS_KEY_ID'] = config('AWS_ACCESS_KEY_ID')
    os.environ['AWS_SECRET_ACCESS_KEY'] = config('AWS_SECRET_ACCESS_KEY')
    os.environ['AWS_STORAGE_BUCKET_NAME'] = config('AWS_STORAGE_BUCKET_NAME')
    os.environ['AWS_S3_REGION_NAME'] = config('AWS_S3_REGION_NAME')
    
    # Build database
    builder = PFDDatabaseBuilder()
    builder.build()
