# 🤖 Automated Parallel AI Model Training

## Overview

This system **automatically trains all AI models in parallel** using data from AWS S3, eliminating manual annotation work.

## Features

✅ **Fully Automated** - No manual annotation required
✅ **Parallel Training** - Train multiple models simultaneously  
✅ **Self-Supervised** - Uses GPT-4V for initial annotations
✅ **S3 Integration** - Automatically collects training data from S3
✅ **Incremental Learning** - Models improve over time
✅ **Resumable** - Checkpointing allows resume after interruption

## Quick Start

### 1. Setup AWS S3 (One-time)

Upload your P&ID drawings to S3 bucket:

```bash
aws s3 sync ./local_pids/ s3://radai-training-data/pid_drawings/
```

### 2. Configure Environment Variables

Add to your `.env` file:

```bash
# AWS S3 Configuration
AWS_ACCESS_KEY_ID=your_access_key
AWS_SECRET_ACCESS_KEY=your_secret_key
AWS_STORAGE_BUCKET_NAME=radai-training-data
AWS_S3_REGION_NAME=ap-south-1

# OpenAI for Auto-Annotation
OPENAI_API_KEY=your_openai_key

# Claude for Validation (Optional)
ANTHROPIC_API_KEY=your_anthropic_key

# Training Configuration
USE_CLAUDE_VALIDATION=true
TRAINING_DEVICE=cuda  # or 'cpu'
```

### 3. Start Automated Training

**Option A: Train All Models (Parallel)**
```bash
cd backend
python apps/pfd_converter/scripts/automated_training_orchestrator.py \
    --models yolov8,gnn,rl \
    --workers 2
```

**Option B: Train Specific Model**
```bash
# YOLOv8 only (fastest)
python apps/pfd_converter/scripts/automated_training_orchestrator.py --models yolov8

# SDXL only (requires 16GB+ VRAM)
python apps/pfd_converter/scripts/automated_training_orchestrator.py --models sdxl --workers 1

# GNN + RL (parallel)
python apps/pfd_converter/scripts/automated_training_orchestrator.py --models gnn,rl
```

### 4. Monitor Training Progress

Training logs are saved to:
- `./training_data_cache/yolov8/training.log`
- `./training_data_cache/sdxl/training.log`
- `./training_data_cache/gnn/training.log`
- `./training_data_cache/rl/training.log`

TensorBoard monitoring:
```bash
tensorboard --logdir=./training_data_cache/tensorboard/
```

## How It Works

### YOLOv8 Symbol Detector

1. **Data Collection**: Scans S3 bucket for P&ID images
2. **Auto-Annotation**: Uses GPT-4V to detect and locate symbols
3. **YOLO Format**: Converts annotations to YOLO format
4. **Training**: Trains YOLOv8 for 100 epochs
5. **Validation**: Tests on validation set

**Time**: ~8-12 hours for 500 images on GPU

### Stable Diffusion XL (SDXL)

1. **Data Collection**: Collects 5000+ professional P&IDs from S3
2. **Caption Generation**: Creates training captions
3. **LoRA Preparation**: Prepares dataset for LoRA fine-tuning
4. **Fine-Tuning**: Trains LoRA weights (100 epochs)
5. **Weight Upload**: Saves trained weights to S3

**Time**: ~24-48 hours on A100 GPU

### Graph Neural Network (GNN)

1. **Pair Extraction**: Extracts PFD→P&ID conversion pairs from database
2. **Graph Conversion**: Converts to PyTorch Geometric format
3. **Training**: Trains 3-layer GAT network
4. **Validation**: Tests prediction accuracy

**Time**: ~4-6 hours on GPU

### Reinforcement Learning (RL)

1. **Synthetic Generation**: Creates 10,000 random process graphs
2. **Environment Setup**: Initializes Gym environment
3. **PPO Training**: Trains layout optimizer (1M timesteps)
4. **Evaluation**: Tests on validation layouts

**Time**: ~12-24 hours on GPU

## Training Requirements

### Hardware

**Minimum (CPU only)**:
- 16GB RAM
- 100GB disk space
- Training time: 5-7 days

**Recommended (GPU)**:
- NVIDIA GPU with 8GB+ VRAM (RTX 3070 or better)
- 32GB RAM
- 200GB disk space
- Training time: 2-3 days

**Optimal (Cloud GPU)**:
- NVIDIA A100 (40GB VRAM)
- 64GB RAM
- 500GB SSD
- Training time: 12-24 hours

### Data Requirements

**Minimum**:
- 500 P&ID drawings for YOLOv8
- 1000 PFD→P&ID conversion pairs for GNN
- Auto-generated synthetic data for RL

**Recommended**:
- 1000+ P&IDs for YOLOv8 (better accuracy)
- 5000+ professional P&IDs for SDXL
- 2000+ conversion pairs for GNN

## Cost Estimation

### AWS Costs

**S3 Storage** (100GB P&IDs): ~$2.30/month

**EC2 GPU Instance** (g4dn.xlarge):
- $0.526/hour
- 48 hours training = ~$25

**Total**: ~$27 for complete training

### API Costs

**GPT-4V Auto-Annotation** (500 images):
- ~$0.10 per image = $50 total
- One-time cost

**Claude Validation** (optional):
- ~$0.006 per validation
- Negligible cost

## Monitoring & Debugging

### Check Training Status

```bash
# View real-time logs
tail -f training_data_cache/yolov8/training.log

# Check training status JSON
cat training_status.json
```

### Common Issues

**Issue**: "No GPU detected"
```bash
# Verify CUDA installation
python -c "import torch; print(torch.cuda.is_available())"

# Install CUDA toolkit
# See: https://developer.nvidia.com/cuda-downloads
```

**Issue**: "S3 connection failed"
```bash
# Verify AWS credentials
aws s3 ls s3://your-bucket-name/

# Check .env file
cat .env | grep AWS
```

**Issue**: "Out of memory (OOM)"
```bash
# Reduce batch size in config
# YOLOv8: batch_size=16 → 8
# SDXL: Use gradient checkpointing
# GNN: Reduce graph size
```

## Advanced Configuration

### Custom S3 Paths

```python
orchestrator = TrainingOrchestrator(
    s3_bucket='my-custom-bucket',
    local_cache_dir='./my_cache'
)

# Override search paths
orchestrator.yolov8_annotator.search_prefixes = [
    'my_custom_path/pids/',
    'another_path/'
]
```

### Resume Training

```bash
# Training automatically resumes from last checkpoint
python automated_training_orchestrator.py --models yolov8 --resume
```

### Export Trained Models

```bash
# Models are automatically saved to:
./training_data_cache/yolov8/models/best.pt
./training_data_cache/sdxl/lora_weights/pid_specialist_lora_v2.safetensors
./training_data_cache/gnn/models/process_flow_gnn_v1.pth
./training_data_cache/rl/models/layout_optimizer_ppo_v1.zip
```

## Performance Benchmarks

After training, expected performance:

| Model | Accuracy | Speed | Cost |
|-------|----------|-------|------|
| **YOLOv8** | 95%+ | 30ms | $0 |
| **SDXL** | 9.2/10 quality | 8s | $0.02 |
| **GNN** | 85%+ recall | 50ms | $0 |
| **RL** | 30% improvement | 100ms | $0 |

**Total per conversion**: ~$0.02 (vs $0.50 with GPT-4V + DALL-E)

## Next Steps

1. ✅ **Start Training**: Run orchestrator script
2. ⏳ **Wait 2-3 days**: Let models train
3. 🧪 **Test Models**: Validate on test set
4. 🚀 **Deploy**: Integrate into production pipeline
5. 📊 **Monitor**: Track accuracy and cost savings

## Support

For issues or questions:
- Check logs: `./training_data_cache/*/training.log`
- GitHub Issues: [your-repo]
- Email: support@radai.ae

---

**Built with ❤️ for process engineers by process engineers**
