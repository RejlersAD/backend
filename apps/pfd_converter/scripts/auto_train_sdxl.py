"""
Auto-Training System for Stable Diffusion XL
=============================================

Automatically collects professional P&IDs from S3 and fine-tunes SDXL with LoRA
for company-specific P&ID generation style.

Process:
1. Collect 5000+ professional P&IDs from S3
2. Extract metadata and create captions
3. Prepare LoRA training dataset
4. Fine-tune SDXL with company style
5. Save LoRA weights to S3
"""

import os
import logging
from typing import List, Dict
from pathlib import Path
import boto3
from PIL import Image
import io
from tqdm import tqdm
import torch
from diffusers import StableDiffusionXLPipeline, AutoencoderKL
from transformers import CLIPTextModel, CLIPTokenizer
from decouple import config

logger = logging.getLogger(__name__)


class AutoSDXLTrainer:
    """
    Automatically trains SDXL for P&ID generation using S3 data
    """
    
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
        
    def collect_professional_pids(self, min_count: int = 5000) -> List[Dict]:
        """
        Collect professional P&IDs from S3 bucket
        
        Filters for high-quality professional drawings
        """
        logger.info(f"🔍 Collecting professional P&IDs from S3...")
        
        pids = []
        paginator = self.s3_client.get_paginator('list_objects_v2')
        
        # Search professional P&ID folders
        search_prefixes = [
            'pid_drawings/',
            'professional_pids/',
            'project_data/',
            'completed_pids/',
            'pid_library/'
        ]
        
        for prefix in search_prefixes:
            try:
                pages = paginator.paginate(Bucket=self.s3_bucket, Prefix=prefix)
                
                for page in pages:
                    if 'Contents' not in page:
                        continue
                    
                    for obj in page['Contents']:
                        key = obj['Key']
                        
                        # Filter for professional P&IDs (larger files are usually higher quality)
                        if any(key.lower().endswith(ext) for ext in ['.pdf', '.png', '.jpg']) and obj['Size'] > 100000:
                            pids.append({
                                'key': key,
                                'size': obj['Size'],
                                'last_modified': obj['LastModified']
                            })
                
            except Exception as e:
                logger.warning(f"⚠️  Error scanning {prefix}: {e}")
                continue
        
        logger.info(f"✅ Collected {len(pids)} professional P&IDs")
        
        if len(pids) < min_count:
            logger.warning(f"⚠️  Only found {len(pids)} files (minimum: {min_count})")
            logger.warning("    SDXL requires 5000+ images for good quality")
            logger.info("💡 Consider:")
            logger.info("   - Uploading more P&IDs to S3")
            logger.info("   - Using data augmentation")
            logger.info("   - Starting with smaller dataset (results may vary)")
        
        return pids
    
    def prepare_lora_dataset(self, pids: List[Dict]) -> str:
        """
        Prepare LoRA training dataset from P&IDs
        
        Returns:
            Path to prepared dataset directory
        """
        dataset_dir = os.path.join(self.cache_dir, 'sdxl_lora_dataset')
        images_dir = os.path.join(dataset_dir, 'images')
        os.makedirs(images_dir, exist_ok=True)
        
        logger.info(f"📦 Preparing LoRA dataset from {len(pids)} P&IDs...")
        
        metadata = []
        
        for i, pid_info in enumerate(tqdm(pids, desc="Processing")):
            try:
                # Download image
                image_data = self._download_from_s3(pid_info['key'])
                image = Image.open(io.BytesIO(image_data))
                
                # Resize to SDXL training size (1024x1024 or 1728x1216)
                target_size = (1728, 1216)  # A1 landscape format
                image = image.resize(target_size, Image.Resampling.LANCZOS)
                
                # Save image
                image_filename = f"pid_{i:06d}.jpg"
                image_path = os.path.join(images_dir, image_filename)
                image.convert('RGB').save(image_path, quality=95)
                
                # Create caption (for LoRA training)
                caption = self._generate_caption_from_filename(pid_info['key'])
                caption_path = os.path.join(images_dir, f"pid_{i:06d}.txt")
                with open(caption_path, 'w') as f:
                    f.write(caption)
                
                metadata.append({
                    'image': image_filename,
                    'caption': caption,
                    'source': pid_info['key']
                })
                
            except Exception as e:
                logger.error(f"  ❌ Failed to process {pid_info['key']}: {e}")
                continue
        
        logger.info(f"✅ Dataset prepared: {len(metadata)} images")
        
        # Save metadata
        import json
        metadata_path = os.path.join(dataset_dir, 'metadata.json')
        with open(metadata_path, 'w') as f:
            json.dump(metadata, f, indent=2)
        
        return dataset_dir
    
    def train_lora(self, dataset_path: str, num_epochs: int = 100, learning_rate: float = 1e-4, device: str = 'cuda'):
        """
        Fine-tune SDXL with LoRA on P&ID dataset
        """
        logger.info(f"🚀 Starting SDXL LoRA fine-tuning...")
        logger.info(f"   Dataset: {dataset_path}")
        logger.info(f"   Epochs: {num_epochs}")
        logger.info(f"   Learning rate: {learning_rate}")
        logger.info(f"   Device: {device}")
        
        try:
            from ..ai_models.sdxl_generator import SDXLTrainer
            
            SDXLTrainer.train_lora(
                dataset_path=dataset_path,
                output_dir=os.path.join(self.cache_dir, 'lora_weights'),
                num_epochs=num_epochs,
                learning_rate=learning_rate
            )
            
            logger.info("✅ SDXL LoRA training completed!")
            
            # Upload weights to S3
            self._upload_weights_to_s3()
            
        except Exception as e:
            logger.error(f"❌ SDXL training failed: {e}")
            raise
    
    def _download_from_s3(self, key: str) -> bytes:
        """Download file from S3"""
        response = self.s3_client.get_object(Bucket=self.s3_bucket, Key=key)
        return response['Body'].read()
    
    def _generate_caption_from_filename(self, key: str) -> str:
        """
        Generate training caption from filename
        """
        filename = os.path.basename(key)
        
        # Base caption
        caption = "professional engineering piping and instrumentation diagram, "
        caption += "technical drawing, ISA 5.1 compliant symbols, "
        caption += "equipment vessels pumps valves instruments, "
        caption += "process flow lines, control loops, "
        caption += "title block with drawing information, "
        caption += "clean white background, high detail, engineering standard"
        
        # Add specific details if extractable from filename
        if 'oil' in filename.lower() or 'gas' in filename.lower():
            caption += ", oil and gas industry"
        if 'water' in filename.lower():
            caption += ", water treatment process"
        if 'offshore' in filename.lower():
            caption += ", offshore platform"
        
        return caption
    
    def _upload_weights_to_s3(self):
        """Upload trained LoRA weights to S3 for backup"""
        weights_dir = os.path.join(self.cache_dir, 'lora_weights')
        
        if not os.path.exists(weights_dir):
            logger.warning("⚠️  No weights found to upload")
            return
        
        logger.info("📤 Uploading LoRA weights to S3...")
        
        for filename in os.listdir(weights_dir):
            if filename.endswith('.safetensors') or filename.endswith('.pt'):
                local_path = os.path.join(weights_dir, filename)
                s3_key = f"ai_models/sdxl_lora/{filename}"
                
                try:
                    self.s3_client.upload_file(local_path, self.s3_bucket, s3_key)
                    logger.info(f"  ✅ Uploaded: {s3_key}")
                except Exception as e:
                    logger.error(f"  ❌ Upload failed: {e}")
        
        logger.info("✅ Weights uploaded to S3")
