"""
Auto-Annotation System for YOLOv8
==================================

Uses GPT-4V to automatically annotate P&ID symbols for YOLOv8 training.
This eliminates manual annotation work by leveraging AI to bootstrap the training data.

Process:
1. Collect P&IDs from S3 bucket
2. Use GPT-4V to detect and locate symbols
3. Convert to YOLO annotation format
4. Validate annotations
5. Incrementally train YOLOv8

Self-improving loop:
- Train initial model with GPT-4V annotations
- Use trained model to annotate new images (faster)
- Human validation on subset
- Retrain with corrected data
"""

import os
import json
import logging
from typing import List, Dict, Tuple
from pathlib import Path
import boto3
from PIL import Image
import io
import base64
from openai import OpenAI
from decouple import config
import yaml
from tqdm import tqdm
try:
    from pdf2image import convert_from_path
    PDF2IMAGE_AVAILABLE = True
except ImportError:
    PDF2IMAGE_AVAILABLE = False

logger = logging.getLogger(__name__)


class AutoYOLOv8Annotator:
    """
    Automatically annotates P&ID images for YOLOv8 training using GPT-4V
    """
    
    # P&ID symbol classes (50 types)
    SYMBOL_CLASSES = [
        # Equipment (0-19)
        'vessel', 'tank', 'drum', 'column', 'reactor',
        'pump', 'compressor', 'turbine', 'fan', 'blower',
        'heat_exchanger', 'cooler', 'heater', 'condenser', 'reboiler',
        'separator', 'filter', 'cyclone', 'mixer', 'agitator',
        
        # Instruments (20-39)
        'flow_transmitter', 'pressure_transmitter', 'temperature_transmitter', 'level_transmitter',
        'flow_indicator', 'pressure_indicator', 'temperature_indicator', 'level_indicator',
        'flow_controller', 'pressure_controller', 'temperature_controller', 'level_controller',
        'flow_element', 'pressure_gauge', 'temperature_well', 'level_glass',
        'analyzer', 'switch', 'alarm', 'recorder',
        
        # Valves (40-49)
        'control_valve', 'isolation_valve', 'check_valve', 'safety_valve',
        'ball_valve', 'gate_valve', 'globe_valve', 'butterfly_valve',
        'three_way_valve', 'pressure_relief_valve'
    ]
    
    def __init__(self, s3_bucket: str, cache_dir: str):
        self.s3_bucket = s3_bucket
        self.cache_dir = cache_dir
        os.makedirs(cache_dir, exist_ok=True)
        
        # Initialize S3
        self.s3_client = boto3.client(
            's3',
            aws_access_key_id=config('AWS_ACCESS_KEY_ID', default=''),
            aws_secret_access_key=config('AWS_SECRET_ACCESS_KEY', default='')
        )
        
        # Initialize OpenAI
        self.openai_client = OpenAI(api_key=config('OPENAI_API_KEY', default=''))
        
    def collect_pids_from_s3(self, min_count: int = 500, max_count: int = 1000) -> List[Dict]:
        """
        Collect P&ID drawings from S3 bucket
        
        Returns:
            List of dicts with 'key', 'url', 'local_path'
        """
        logger.info(f"🔍 Scanning S3 bucket: s3://{self.s3_bucket}/")
        
        pids = []
        paginator = self.s3_client.get_paginator('list_objects_v2')
        
        # Search for P&ID files in relevant folders
        search_prefixes = [
            'pid_documents/',
            'pid_drawings/',
            'pfd_documents/',  # Some projects mix PFD/P&ID
            'project_data/',
            'media/pid_drawings/'
        ]
        
        for prefix in search_prefixes:
            try:
                pages = paginator.paginate(Bucket=self.s3_bucket, Prefix=prefix)
                
                for page in pages:
                    if 'Contents' not in page:
                        continue
                    
                    for obj in page['Contents']:
                        key = obj['Key']
                        
                        # Filter for image/PDF files
                        if any(key.lower().endswith(ext) for ext in ['.pdf', '.png', '.jpg', '.jpeg', '.tif', '.tiff']):
                            pids.append({
                                'key': key,
                                'size': obj['Size'],
                                'last_modified': obj['LastModified']
                            })
                            
                            if len(pids) >= max_count:
                                break
                
                if len(pids) >= max_count:
                    break
                    
            except Exception as e:
                logger.warning(f"⚠️  Error scanning {prefix}: {e}")
                continue
        
        logger.info(f"✅ Found {len(pids)} P&ID files in S3")
        
        if len(pids) < min_count:
            logger.warning(f"⚠️  Only found {len(pids)} files (minimum: {min_count})")
            logger.info("💡 Tip: Upload more P&ID drawings to your S3 bucket")
        
        return pids[:max_count]
    
    def auto_annotate_batch(self, pids: List, batch_size: int = 10) -> Dict:
        """
        Auto-annotate a batch of P&IDs using GPT-4V
        
        Args:
            pids: List of file paths (str) or dicts with 'key' field
            batch_size: Number of images to process before logging
        
        Returns:
            Dataset dict with YOLO format annotations
        """
        dataset_dir = os.path.join(self.cache_dir, 'yolo_dataset')
        images_dir = os.path.join(dataset_dir, 'images', 'train')
        labels_dir = os.path.join(dataset_dir, 'labels', 'train')
        
        os.makedirs(images_dir, exist_ok=True)
        os.makedirs(labels_dir, exist_ok=True)
        
        annotated_count = 0
        
        logger.info(f"📝 Starting auto-annotation of {len(pids)} images...")
        
        for i, pid_info in enumerate(tqdm(pids, desc="Annotating")):
            try:
                # Handle both file paths and S3 dicts
                if isinstance(pid_info, str):
                    # Local file path
                    image_path_src = pid_info
                    
                    # Convert PDF to image if needed
                    if image_path_src.lower().endswith('.pdf'):
                        if not PDF2IMAGE_AVAILABLE:
                            raise ImportError("pdf2image not installed. Run: pip install pdf2image")
                        # Convert first page of PDF to image
                        images = convert_from_path(image_path_src, first_page=1, last_page=1, dpi=200)
                        image = images[0]
                    else:
                        image = Image.open(image_path_src)
                else:
                    # S3 dict with 'key'
                    image_data = self._download_from_s3(pid_info['key'])
                    
                    # Check if PDF from S3
                    if pid_info.get('key', '').lower().endswith('.pdf'):
                        if not PDF2IMAGE_AVAILABLE:
                            raise ImportError("pdf2image not installed. Run: pip install pdf2image")
                        # Save temporarily and convert
                        temp_path = '/tmp/temp_pdf.pdf'
                        with open(temp_path, 'wb') as f:
                            f.write(image_data)
                        images = convert_from_path(temp_path, first_page=1, last_page=1, dpi=200)
                        image = images[0]
                        os.remove(temp_path)
                    else:
                        image = Image.open(io.BytesIO(image_data))
                
                # Annotate using GPT-4V
                annotations = self._annotate_with_gpt4v(image)
                
                # Save image
                image_filename = f"pid_{i:05d}.jpg"
                image_path = os.path.join(images_dir, image_filename)
                image.convert('RGB').save(image_path, quality=95)
                
                # Save annotations in YOLO format
                label_path = os.path.join(labels_dir, f"pid_{i:05d}.txt")
                self._save_yolo_annotations(annotations, label_path)
                
                annotated_count += 1
                
                # Batch processing (save memory)
                if (i + 1) % batch_size == 0:
                    logger.info(f"  ✅ Annotated {i+1}/{len(pids)} images")
                
            except Exception as e:
                pid_name = pid_info if isinstance(pid_info, str) else pid_info.get('key', 'unknown')
                logger.error(f"  ❌ Failed to annotate {pid_name}: {e}")
                continue
        
        logger.info(f"✅ Auto-annotation complete: {annotated_count}/{len(pids)} images")
        
        # Create data.yaml
        data_yaml_path = os.path.join(dataset_dir, 'data.yaml')
        self._create_data_yaml(data_yaml_path, dataset_dir)
        
        return {
            'data_yaml': data_yaml_path,
            'images_count': annotated_count,
            'classes': len(self.SYMBOL_CLASSES)
        }
    
    def _download_from_s3(self, key: str) -> bytes:
        """Download file from S3"""
        response = self.s3_client.get_object(Bucket=self.s3_bucket, Key=key)
        return response['Body'].read()
    
    def _annotate_with_gpt4v(self, image: Image.Image) -> List[Dict]:
        """
        Use GPT-4V to detect and locate symbols in P&ID
        
        Returns:
            List of annotations: [{'class': 'pump', 'bbox': [x_center, y_center, width, height]}, ...]
        """
        # Resize if too large (GPT-4V has size limits)
        max_size = 2048
        if max(image.size) > max_size:
            ratio = max_size / max(image.size)
            new_size = tuple(int(dim * ratio) for dim in image.size)
            image = image.resize(new_size, Image.Resampling.LANCZOS)
        
        # Encode image to base64
        buffered = io.BytesIO()
        image.save(buffered, format="JPEG", quality=95)
        image_base64 = base64.b64encode(buffered.getvalue()).decode('utf-8')
        
        # GPT-4V prompt for symbol detection
        prompt = f"""Analyze this P&ID engineering drawing and detect ALL equipment, instruments, and valves.

For EACH symbol you detect, provide:
1. Symbol type (from this list: {', '.join(self.SYMBOL_CLASSES[:20])})
2. Bounding box coordinates as percentages (0.0-1.0): [x_center, y_center, width, height]

Return ONLY a JSON array of detections:
[
  {{"class": "pump", "bbox": [0.25, 0.60, 0.08, 0.12]}},
  {{"class": "vessel", "bbox": [0.50, 0.45, 0.15, 0.30]}},
  ...
]

Be thorough - detect ALL visible symbols including small instruments."""
        
        # Try multiple models in order of preference
        models_to_try = ["gpt-4o", "gpt-4-turbo", "gpt-4-vision-preview"]
        
        for model in models_to_try:
            try:
                logger.info(f"Trying model: {model}")
                
                response = self.openai_client.chat.completions.create(
                    model=model,
                    messages=[
                        {
                            "role": "user",
                            "content": [
                                {"type": "text", "text": prompt},
                                {
                                    "type": "image_url",
                                    "image_url": {
                                        "url": f"data:image/jpeg;base64,{image_base64}",
                                        "detail": "high"
                                    }
                                }
                            ]
                        }
                    ],
                    max_tokens=4096,
                    temperature=0.2
                )
                
                # Successfully got response, break out of model loop
                break
                
            except Exception as model_error:
                logger.warning(f"Model {model} failed: {model_error}")
                if model == models_to_try[-1]:
                    # Last model failed, re-raise
                    raise
                # Try next model
                continue
        
        try:
            
            # Parse JSON response
            content = response.choices[0].message.content
            
            if not content or not content.strip():
                logger.warning("Empty response from GPT-4V")
                return []
            
            # Extract JSON (might be wrapped in markdown)
            if '```json' in content:
                content = content.split('```json')[1].split('```')[0]
            elif '```' in content:
                content = content.split('```')[1].split('```')[0]
            
            content = content.strip()
            
            # Sometimes GPT returns explanatory text before JSON - extract only the JSON array
            if not content.startswith('['):
                # Try to find JSON array in the response
                import re
                json_match = re.search(r'\[[\s\S]*\]', content)
                if json_match:
                    content = json_match.group(0)
                else:
                    logger.warning(f"No JSON array found in response: {content[:200]}")
                    return []
            
            annotations = json.loads(content)
            
            # Validate and convert class names to indices
            validated = []
            for ann in annotations:
                class_name = ann.get('class', '').lower()
                if class_name in self.SYMBOL_CLASSES:
                    ann['class_id'] = self.SYMBOL_CLASSES.index(class_name)
                    validated.append(ann)
            
            return validated
            
        except Exception as e:
            logger.error(f"GPT-4V annotation failed: {e}")
            return []
    
    def _save_yolo_annotations(self, annotations: List[Dict], label_path: str):
        """
        Save annotations in YOLO format
        
        Format: <class_id> <x_center> <y_center> <width> <height>
        All values normalized to 0.0-1.0
        """
        with open(label_path, 'w') as f:
            for ann in annotations:
                class_id = ann['class_id']
                bbox = ann['bbox']
                # YOLO format: class x_center y_center width height
                line = f"{class_id} {bbox[0]:.6f} {bbox[1]:.6f} {bbox[2]:.6f} {bbox[3]:.6f}\n"
                f.write(line)
    
    def _create_data_yaml(self, yaml_path: str, dataset_dir: str):
        """Create YOLO data.yaml configuration"""
        data = {
            'path': dataset_dir,
            'train': 'images/train',
            'val': 'images/train',  # Use same for now, split later
            'nc': len(self.SYMBOL_CLASSES),
            'names': self.SYMBOL_CLASSES
        }
        
        with open(yaml_path, 'w') as f:
            yaml.dump(data, f, default_flow_style=False)
        
        logger.info(f"✅ Created data.yaml: {yaml_path}")
