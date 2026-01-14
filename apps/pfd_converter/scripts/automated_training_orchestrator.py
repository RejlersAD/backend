"""
Automated Parallel Training Orchestrator
=========================================

Orchestrates parallel training of all AI models using AWS S3 data:
1. YOLOv8 Symbol Detector - Auto-annotates using GPT-4V, trains incrementally
2. SDXL P&ID Generator - Collects from S3, fine-tunes with LoRA
3. GNN Process Model - Extracts PFD→P&ID pairs, trains on graph patterns
4. RL Layout Optimizer - Generates synthetic layouts, trains with PPO

Features:
- AWS S3 integration for data storage/retrieval
- Self-supervised learning (uses GPT-4V for initial annotations)
- Parallel training with multiprocessing
- Checkpointing and resumable training
- Real-time monitoring with TensorBoard
- Automatic dataset expansion
"""

import os
import sys
import logging
from pathlib import Path
from typing import Dict, List, Optional
import multiprocessing as mp
from datetime import datetime
import json
import boto3
from botocore.exceptions import ClientError
import torch
from decouple import config

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class TrainingOrchestrator:
    """
    Orchestrates parallel training of all AI models
    """
    
    def __init__(self, s3_bucket: str = None, local_cache_dir: str = None):
        """
        Initialize training orchestrator
        
        Args:
            s3_bucket: AWS S3 bucket name for data storage
            local_cache_dir: Local directory for caching data
        """
        self.s3_bucket = s3_bucket or config('AWS_STORAGE_BUCKET_NAME', default='radai-training-data')
        self.local_cache_dir = local_cache_dir or './training_data_cache'
        
        # Store S3 credentials (picklable) - don't create client here
        self.s3_config = {
            'bucket': self.s3_bucket,
            'access_key': config('AWS_ACCESS_KEY_ID', default=''),
            'secret_key': config('AWS_SECRET_ACCESS_KEY', default=''),
            'region': config('AWS_S3_REGION_NAME', default='ap-south-1')
        }
        
        # Test S3 connection
        try:
            s3_test = boto3.client(
                's3',
                aws_access_key_id=self.s3_config['access_key'],
                aws_secret_access_key=self.s3_config['secret_key'],
                region_name=self.s3_config['region']
            )
            s3_test.head_bucket(Bucket=self.s3_bucket)
            logger.info(f"✅ Connected to S3 bucket: {self.s3_bucket}")
        except Exception as e:
            logger.warning(f"⚠️  S3 connection issue: {e}. Will use local files only.")
        
        # Create cache directory
        os.makedirs(self.local_cache_dir, exist_ok=True)
        
        # Training status
        self.training_status = {
            'yolov8': {'status': 'pending', 'progress': 0, 'last_checkpoint': None},
            'sdxl': {'status': 'pending', 'progress': 0, 'last_checkpoint': None},
            'gnn': {'status': 'pending', 'progress': 0, 'last_checkpoint': None},
            'rl': {'status': 'pending', 'progress': 0, 'last_checkpoint': None}
        }
        
        # Check GPU availability
        self.device = 'cuda' if torch.cuda.is_available() else 'cpu'
        if self.device == 'cuda':
            logger.info(f"✅ GPU available: {torch.cuda.get_device_name(0)}")
            logger.info(f"   VRAM: {torch.cuda.get_device_properties(0).total_memory / 1024**3:.1f} GB")
        else:
            logger.warning("⚠️  No GPU detected. Training will be SLOW on CPU!")
    
    def start_parallel_training(self, models: List[str] = None, max_workers: int = 2):
        """
        Start parallel training of multiple models
        
        Args:
            models: List of models to train ['yolov8', 'sdxl', 'gnn', 'rl']
            max_workers: Maximum number of parallel training processes
        """
        if models is None:
            models = ['yolov8', 'gnn', 'rl']  # SDXL requires more VRAM, train separately
        
        logger.info("="*80)
        logger.info("🚀 STARTING AUTOMATED PARALLEL TRAINING")
        logger.info("="*80)
        logger.info(f"Models to train: {', '.join(models)}")
        logger.info(f"Max parallel workers: {max_workers}")
        logger.info(f"Device: {self.device}")
        logger.info("="*80)
        
        # Create training processes
        processes = []
        
        for model_name in models:
            if model_name == 'yolov8':
                p = mp.Process(target=self._train_yolov8_parallel, name='YOLOv8-Trainer')
            elif model_name == 'sdxl':
                p = mp.Process(target=self._train_sdxl_parallel, name='SDXL-Trainer')
            elif model_name == 'gnn':
                p = mp.Process(target=self._train_gnn_parallel, name='GNN-Trainer')
            elif model_name == 'rl':
                p = mp.Process(target=self._train_rl_parallel, name='RL-Trainer')
            else:
                logger.warning(f"Unknown model: {model_name}")
                continue
            
            processes.append((model_name, p))
        
        # Start processes in batches
        running = []
        completed = []
        
        while processes or running:
            # Start new processes if slots available
            while len(running) < max_workers and processes:
                model_name, process = processes.pop(0)
                process.start()
                running.append((model_name, process))
                logger.info(f"▶️  Started training: {model_name} (PID: {process.pid})")
            
            # Check for completed processes
            for model_name, process in running[:]:
                if not process.is_alive():
                    process.join()
                    running.remove((model_name, process))
                    completed.append(model_name)
                    logger.info(f"✅ Completed training: {model_name}")
            
            # Wait a bit before checking again
            if running:
                import time
                time.sleep(5)
        
        logger.info("="*80)
        logger.info(f"🎉 ALL TRAINING COMPLETED")
        logger.info(f"   Trained models: {', '.join(completed)}")
        logger.info("="*80)
    
    def _train_yolov8_parallel(self):
        """
        Train YOLOv8 using auto-annotation from GPT-4V + incremental learning
        """
        # Use absolute imports for multiprocessing
        import sys
        import os
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', '..'))
        
        from apps.pfd_converter.scripts.local_data_collector import collect_local_pids
        from apps.pfd_converter.scripts.auto_annotate_yolov8 import AutoYOLOv8Annotator
        
        logger.info("\n[YOLOv8] 🎯 Starting automated training...")
        
        try:
            # Phase 1: Collect P&IDs from local filesystem (S3 access restricted)
            logger.info("[YOLOv8] Phase 1: Collecting P&IDs from local filesystem...")
            pids = collect_local_pids(min_count=10)  # Lower for testing
            
            if len(pids) == 0:
                raise Exception("No P&ID files found in local filesystem")
            
            logger.info(f"[YOLOv8] ✅ Collected {len(pids)} P&ID drawings")
            
            # Initialize auto-annotator
            annotator = AutoYOLOv8Annotator(
                s3_bucket=self.s3_bucket,
                cache_dir=os.path.join(self.local_cache_dir, 'yolov8')
            )
            
            # Phase 2: Auto-annotate using GPT-4V
            logger.info("[YOLOv8] Phase 2: Auto-annotating with GPT-4V...")
            dataset = annotator.auto_annotate_batch(pids, batch_size=5)
            logger.info(f"[YOLOv8] ✅ Annotated {len(dataset.get('images', []))} images")
            
            # Phase 3: Train YOLOv8
            logger.info("[YOLOv8] Phase 3: Training YOLOv8 detector...")
            from apps.pfd_converter.ai_models import YOLOv8SymbolDetector
            
            detector = YOLOv8SymbolDetector()
            detector.train(
                dataset_path=dataset['data_yaml'],
                epochs=50,  # Reduced for faster testing
                batch_size=8,  # Reduced for CPU
                imgsz=640,  # Reduced for CPU
                device=self.device
            )
            
            logger.info("[YOLOv8] ✅ Training completed!")
            self.training_status['yolov8']['status'] = 'completed'
            
        except Exception as e:
            logger.error(f"[YOLOv8] ❌ Training failed: {e}")
            import traceback
            traceback.print_exc()
            self.training_status['yolov8']['status'] = 'failed'
    
    def _train_sdxl_parallel(self):
        """
        Train SDXL using P&IDs from S3 + LoRA fine-tuning
        """
        from .auto_train_sdxl import AutoSDXLTrainer
        
        logger.info("\n[SDXL] 🎨 Starting automated training...")
        
        try:
            # Initialize SDXL trainer
            trainer = AutoSDXLTrainer(
                s3_bucket=self.s3_bucket,
                cache_dir=os.path.join(self.local_cache_dir, 'sdxl')
            )
            
            # Phase 1: Collect professional P&IDs
            logger.info("[SDXL] Phase 1: Collecting professional P&IDs from S3...")
            pids = trainer.collect_professional_pids(min_count=5000)
            logger.info(f"[SDXL] ✅ Collected {len(pids)} professional P&IDs")
            
            # Phase 2: Prepare training dataset
            logger.info("[SDXL] Phase 2: Preparing LoRA training dataset...")
            dataset = trainer.prepare_lora_dataset(pids)
            
            # Phase 3: Fine-tune with LoRA
            logger.info("[SDXL] Phase 3: Fine-tuning SDXL with LoRA...")
            trainer.train_lora(
                dataset_path=dataset,
                num_epochs=100,
                learning_rate=1e-4,
                device=self.device
            )
            
            logger.info("[SDXL] ✅ Training completed!")
            self.training_status['sdxl']['status'] = 'completed'
            
        except Exception as e:
            logger.error(f"[SDXL] ❌ Training failed: {e}")
            self.training_status['sdxl']['status'] = 'failed'
    
    def _train_gnn_parallel(self):
        """
        Train GNN using PFD→P&ID pairs from database + S3
        """
        # Use absolute imports for multiprocessing
        import sys
        import os
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', '..'))        
        from apps.pfd_converter.scripts.local_data_collector import collect_local_pfd_pid_pairs
        
        logger.info("\n[GNN] 📊 Starting automated training...")
        
        try:
            # Phase 1: Extract PFD→P&ID pairs from database
            logger.info("[GNN] Phase 1: Extracting PFD→P&ID pairs from database...")
            pairs = collect_local_pfd_pid_pairs()
            
            if len(pairs) < 10:
                logger.warning(f"[GNN] ⚠️  Only {len(pairs)} pairs found (recommended: 100+)")
                logger.info("[GNN] 💡 Skipping GNN training due to insufficient data")
                self.training_status['gnn']['status'] = 'skipped'
                return
            
            logger.info(f"[GNN] ✅ Extracted {len(pairs)} conversion pairs")
            
            # Phase 2: Convert to graph format and train
            logger.info("[GNN] Phase 2: Training Graph Neural Network...")
            from apps.pfd_converter.scripts.auto_train_gnn import AutoGNNTrainer
            
            trainer = AutoGNNTrainer(
                s3_bucket=self.s3_bucket,
                cache_dir=os.path.join(self.local_cache_dir, 'gnn')
            )
            
            # Convert pairs to graph representations
            graphs = trainer.convert_to_graphs(pairs)
            
            # Train GNN model
            from apps.pfd_converter.ai_models.gnn_model import ProcessFlowGNN, GNNTrainer
            
            model = ProcessFlowGNN()
            gnn_trainer = GNNTrainer(model)
            gnn_trainer.train(
                train_loader=graphs['train'],
                val_loader=graphs['val'],
                epochs=50,  # Reduced for testing
                device=self.device
            )
            
            logger.info("[GNN] ✅ Training completed!")
            self.training_status['gnn']['status'] = 'completed'
            
        except Exception as e:
            logger.error(f"[GNN] ❌ Training failed: {e}")
            import traceback
            traceback.print_exc()
            self.training_status['gnn']['status'] = 'failed'
    
    def _train_rl_parallel(self):
        """
        Train RL optimizer using synthetic layouts + existing P&IDs
        """
        # Use absolute imports for multiprocessing
        import sys
        import os
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', '..'))        
        from apps.pfd_converter.scripts.auto_train_rl import AutoRLTrainer
        
        logger.info("\n[RL] 🎮 Starting automated training...")
        
        try:
            # Initialize RL trainer
            trainer = AutoRLTrainer(
                s3_bucket=self.s3_bucket,
                cache_dir=os.path.join(self.local_cache_dir, 'rl')
            )
            
            # Phase 1: Generate synthetic layouts
            logger.info("[RL] Phase 1: Generating synthetic training layouts...")
            layouts = trainer.generate_synthetic_layouts(count=1000)  # Reduced for testing
            logger.info(f"[RL] ✅ Generated {len(layouts)} synthetic layouts")
            
            # Phase 2: Train PPO agent
            logger.info("[RL] Phase 2: Training PPO layout optimizer...")
            
            # Import with headless mode for CV2
            os.environ['MPLBACKEND'] = 'Agg'  # Matplotlib headless
            try:
                from apps.pfd_converter.ai_models.rl_optimizer import RLLayoutOptimizer
            except ImportError as e:
                if 'libGL' in str(e):
                    logger.warning("[RL] ⚠️  OpenGL not available in Docker - skipping RL training")
                    logger.info("[RL] 💡 RL training requires GUI libraries. Deploy to GPU instance for full training.")
                    self.training_status['rl']['status'] = 'skipped'
                    return
                else:
                    raise
            
            optimizer = RLLayoutOptimizer()
            optimizer.train(
                training_graphs=layouts,
                total_timesteps=100_000,  # Reduced for testing
                n_envs=2,  # Reduced for CPU
                device=self.device
            )
            
            logger.info("[RL] ✅ Training completed!")
            self.training_status['rl']['status'] = 'completed'
            
        except Exception as e:
            logger.error(f"[RL] ❌ Training failed: {e}")
            import traceback
            traceback.print_exc()
            self.training_status['rl']['status'] = 'failed'
    
    def get_training_status(self) -> Dict:
        """Get current training status for all models"""
        return self.training_status
    
    def save_status(self, filepath: str = './training_status.json'):
        """Save training status to file"""
        with open(filepath, 'w') as f:
            json.dump(self.training_status, f, indent=2)
        logger.info(f"💾 Training status saved to {filepath}")


def main():
    """
    Main entry point for automated training orchestrator
    
    Usage:
        python automated_training_orchestrator.py --models yolov8,gnn,rl --workers 2
    """
    import argparse
    
    # Fix for CUDA multiprocessing - must use 'spawn' instead of 'fork'
    import multiprocessing
    try:
        multiprocessing.set_start_method('spawn', force=True)
    except RuntimeError:
        # Already set, ignore
        pass
    
    parser = argparse.ArgumentParser(description='Automated AI Model Training Orchestrator')
    parser.add_argument('--models', type=str, default='yolov8,gnn,rl',
                        help='Comma-separated list of models to train')
    parser.add_argument('--workers', type=int, default=2,
                        help='Maximum number of parallel training workers')
    parser.add_argument('--s3-bucket', type=str, default=None,
                        help='AWS S3 bucket name')
    parser.add_argument('--cache-dir', type=str, default='./training_data_cache',
                        help='Local cache directory')
    
    args = parser.parse_args()
    
    # Parse models list
    models = [m.strip() for m in args.models.split(',')]
    
    # Initialize orchestrator
    orchestrator = TrainingOrchestrator(
        s3_bucket=args.s3_bucket,
        local_cache_dir=args.cache_dir
    )
    
    # Start training
    orchestrator.start_parallel_training(
        models=models,
        max_workers=args.workers
    )
    
    # Save final status
    orchestrator.save_status()
    
    print("\n" + "="*80)
    print("✅ TRAINING ORCHESTRATION COMPLETED")
    print("="*80)
    print("\nFinal Status:")
    for model, status in orchestrator.get_training_status().items():
        status_emoji = "✅" if status['status'] == 'completed' else "❌" if status['status'] == 'failed' else "⏳"
        print(f"  {status_emoji} {model.upper()}: {status['status']}")
    print("="*80)


if __name__ == '__main__':
    main()
