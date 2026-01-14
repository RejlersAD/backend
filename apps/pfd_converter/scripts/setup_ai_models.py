"""
Setup Script for Advanced AI Models
Downloads and installs all required models and dependencies
"""

import os
import sys
import subprocess
import logging
from pathlib import Path
import urllib.request
import tarfile
import zipfile

logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger(__name__)


class ModelSetup:
    """Setup manager for AI models"""
    
    def __init__(self):
        self.base_dir = Path(__file__).parent
        self.models_dir = self.base_dir / "models"
        self.datasets_dir = self.base_dir / "datasets"
        
        # Create directories
        self.models_dir.mkdir(exist_ok=True)
        self.datasets_dir.mkdir(exist_ok=True)
    
    def setup_all(self):
        """Run complete setup"""
        logger.info("=" * 80)
        logger.info("ADVANCED AI MODELS SETUP")
        logger.info("=" * 80)
        
        steps = [
            ("Check Python version", self.check_python_version),
            ("Install PyTorch", self.install_pytorch),
            ("Install requirements", self.install_requirements),
            ("Download YOLOv8 base model", self.download_yolov8),
            ("Download ControlNet weights", self.download_controlnet),
            ("Setup dataset directories", self.setup_datasets),
            ("Verify installation", self.verify_installation),
        ]
        
        for step_name, step_func in steps:
            logger.info(f"\n[{step_name}]")
            logger.info("-" * 60)
            try:
                step_func()
                logger.info(f"✅ {step_name} complete")
            except Exception as e:
                logger.error(f"❌ {step_name} failed: {e}")
                return False
        
        logger.info("\n" + "=" * 80)
        logger.info("✅ SETUP COMPLETE!")
        logger.info("=" * 80)
        logger.info("\nNext steps:")
        logger.info("1. Prepare training datasets:")
        logger.info("   python scripts/prepare_yolov8_dataset.py")
        logger.info("2. Train YOLOv8 symbol detector:")
        logger.info("   python scripts/train_yolov8_symbols.py")
        logger.info("3. Fine-tune Stable Diffusion:")
        logger.info("   python scripts/train_sdxl_pid.py")
        logger.info("4. Train GNN and RL models:")
        logger.info("   python scripts/train_gnn.py")
        logger.info("   python scripts/train_rl_optimizer.py")
        
        return True
    
    def check_python_version(self):
        """Check Python version >= 3.10"""
        version = sys.version_info
        if version.major < 3 or (version.major == 3 and version.minor < 10):
            raise RuntimeError(f"Python 3.10+ required, found {version.major}.{version.minor}")
        logger.info(f"Python version: {version.major}.{version.minor}.{version.micro} ✓")
    
    def install_pytorch(self):
        """Install PyTorch with CUDA support"""
        logger.info("Installing PyTorch...")
        
        # Check if CUDA is available
        try:
            import torch
            cuda_available = torch.cuda.is_available()
            logger.info(f"PyTorch already installed. CUDA available: {cuda_available}")
            
            if cuda_available:
                logger.info(f"CUDA version: {torch.version.cuda}")
                logger.info(f"GPU: {torch.cuda.get_device_name(0)}")
            return
        except ImportError:
            pass
        
        # Install PyTorch (CUDA 12.1)
        logger.info("Installing PyTorch with CUDA 12.1...")
        subprocess.check_call([
            sys.executable, "-m", "pip", "install",
            "torch", "torchvision", "torchaudio",
            "--index-url", "https://download.pytorch.org/whl/cu121"
        ])
    
    def install_requirements(self):
        """Install all requirements"""
        logger.info("Installing requirements...")
        
        requirements_file = self.base_dir / "requirements.txt"
        if not requirements_file.exists():
            logger.warning("requirements.txt not found")
            return
        
        subprocess.check_call([
            sys.executable, "-m", "pip", "install", "-r", str(requirements_file)
        ])
    
    def download_yolov8(self):
        """Download YOLOv8x base model"""
        model_path = self.models_dir / "yolov8x.pt"
        
        if model_path.exists():
            logger.info(f"YOLOv8x already exists at {model_path}")
            return
        
        logger.info("Downloading YOLOv8x base model...")
        
        # Will be downloaded automatically by ultralytics on first use
        from ultralytics import YOLO
        model = YOLO('yolov8x.pt')
        logger.info(f"YOLOv8x downloaded successfully")
    
    def download_controlnet(self):
        """Download ControlNet weights"""
        logger.info("ControlNet will be downloaded automatically by diffusers")
        logger.info("Weights will be cached in ~/.cache/huggingface/")
        
        # Pre-download to verify
        try:
            from diffusers import ControlNetModel
            logger.info("Testing ControlNet download...")
            controlnet = ControlNetModel.from_pretrained(
                "lllyasviel/control_v11p_sd15_lineart",
                torch_dtype=torch.float16
            )
            logger.info("ControlNet downloaded successfully")
        except Exception as e:
            logger.warning(f"ControlNet download test failed: {e}")
            logger.info("It will be downloaded on first use")
    
    def setup_datasets(self):
        """Create dataset directory structure"""
        logger.info("Setting up dataset directories...")
        
        # YOLOv8 dataset
        yolo_dir = self.datasets_dir / "pid_symbols"
        (yolo_dir / "images" / "train").mkdir(parents=True, exist_ok=True)
        (yolo_dir / "images" / "val").mkdir(parents=True, exist_ok=True)
        (yolo_dir / "labels" / "train").mkdir(parents=True, exist_ok=True)
        (yolo_dir / "labels" / "val").mkdir(parents=True, exist_ok=True)
        
        # SDXL dataset
        sdxl_dir = self.datasets_dir / "professional_pids"
        sdxl_dir.mkdir(parents=True, exist_ok=True)
        
        # GNN dataset
        gnn_dir = self.datasets_dir / "process_graphs"
        gnn_dir.mkdir(parents=True, exist_ok=True)
        
        logger.info(f"Dataset directories created:")
        logger.info(f"  - YOLOv8: {yolo_dir}")
        logger.info(f"  - SDXL: {sdxl_dir}")
        logger.info(f"  - GNN: {gnn_dir}")
        
        # Create data.yaml for YOLOv8
        data_yaml = yolo_dir / "data.yaml"
        if not data_yaml.exists():
            with open(data_yaml, 'w') as f:
                f.write(f"""# YOLOv8 P&ID Symbols Dataset
path: {yolo_dir.absolute()}
train: images/train
val: images/val

# Classes (50 equipment/instrument types)
names:
  0: vessel_vertical
  1: vessel_horizontal
  2: pump_centrifugal
  3: pump_positive_displacement
  4: heat_exchanger_shell_tube
  5: heat_exchanger_plate
  6: compressor
  7: turbine
  8: tank_atmospheric
  9: tank_pressure
  10: valve_gate
  11: valve_globe
  12: valve_ball
  13: valve_butterfly
  14: valve_check
  15: valve_control
  16: valve_relief
  17: instrument_flow
  18: instrument_pressure
  19: instrument_temperature
  20: instrument_level
  21: instrument_analyzer
  22: filter
  23: strainer
  24: mixer
  25: separator
  # Add more classes as needed up to 50
""")
            logger.info(f"Created data.yaml template")
    
    def verify_installation(self):
        """Verify all components are installed"""
        logger.info("Verifying installation...")
        
        checks = [
            ("PyTorch", "import torch; torch.cuda.is_available()"),
            ("Ultralytics (YOLOv8)", "from ultralytics import YOLO"),
            ("Diffusers (SDXL)", "from diffusers import StableDiffusionXLPipeline"),
            ("Anthropic (Claude)", "import anthropic"),
            ("PyTorch Geometric (GNN)", "import torch_geometric"),
            ("Stable Baselines3 (RL)", "from stable_baselines3 import PPO"),
        ]
        
        all_passed = True
        for name, code in checks:
            try:
                exec(code)
                logger.info(f"  ✅ {name}")
            except Exception as e:
                logger.error(f"  ❌ {name}: {e}")
                all_passed = False
        
        if not all_passed:
            raise RuntimeError("Some components failed verification")


def main():
    """Main setup function"""
    setup = ModelSetup()
    success = setup.setup_all()
    
    if not success:
        logger.error("\n❌ Setup failed. Please review errors above.")
        sys.exit(1)
    
    logger.info("\n✅ Setup successful! AI models are ready to use.")
    sys.exit(0)


if __name__ == "__main__":
    main()
