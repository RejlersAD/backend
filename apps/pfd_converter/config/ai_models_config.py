"""
AI Models Configuration
Soft-coded configuration for all AI/ML models used in PFD to P&ID conversion
Allows easy switching between models, providers, and parameters
"""

from dataclasses import dataclass
from typing import Dict, List, Optional
from enum import Enum


class ModelProvider(Enum):
    """AI model providers"""
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    HUGGINGFACE = "huggingface"
    STABILITY_AI = "stability_ai"
    LOCAL = "local"


class ModelType(Enum):
    """Types of AI models"""
    VISION = "vision"
    LLM = "llm"
    DIFFUSION = "diffusion"
    DETECTION = "detection"
    GNN = "gnn"
    RL = "rl"


@dataclass
class ModelConfig:
    """Configuration for a single AI model"""
    name: str
    provider: ModelProvider
    model_type: ModelType
    model_id: str
    enabled: bool = True
    priority: int = 1  # Lower = higher priority
    cost_per_call: float = 0.0  # USD
    max_retries: int = 3
    timeout: int = 60  # seconds
    parameters: Dict = None
    
    def __post_init__(self):
        if self.parameters is None:
            self.parameters = {}


# ==========================================
# VISION MODELS CONFIGURATION
# ==========================================

VISION_MODELS = {
    # Primary: YOLOv8 for symbol detection (highest accuracy)
    # DISABLED: Heavy lifting ML model removed for performance
    "yolov8_symbol_detector": ModelConfig(
        name="YOLOv8 Symbol Detector",
        provider=ModelProvider.LOCAL,
        model_type=ModelType.DETECTION,
        model_id="yolov8x",  # Extra large for technical drawings
        enabled=False,  # Disabled to reduce heavy ML processing
        priority=1,
        cost_per_call=0.0,
        parameters={
            "conf_threshold": 0.25,  # Confidence threshold
            "iou_threshold": 0.45,  # IoU for NMS
            "imgsz": 1280,  # Image size for inference
            "max_det": 300,  # Maximum detections
            "classes": None,  # None = all classes
            "device": "cuda",  # or "cpu"
            "half": True,  # Use FP16 for speed
            "weights_path": "./models/yolov8_pid_symbols.pt",
            "classes_map": {
                0: "vessel_vertical",
                1: "vessel_horizontal",
                2: "pump_centrifugal",
                3: "pump_positive_displacement",
                4: "heat_exchanger_shell_tube",
                5: "heat_exchanger_plate",
                6: "compressor",
                7: "turbine",
                8: "tank_atmospheric",
                9: "tank_pressure",
                10: "valve_gate",
                11: "valve_globe",
                12: "valve_ball",
                13: "valve_butterfly",
                14: "valve_check",
                15: "valve_control",
                16: "valve_relief",
                17: "instrument_flow",
                18: "instrument_pressure",
                19: "instrument_temperature",
                20: "instrument_level",
                21: "instrument_analyzer",
                22: "filter",
                23: "strainer",
                24: "mixer",
                25: "separator",
                # Add more as needed (target: 50 classes)
            }
        }
    ),
    
    # Secondary: Florence-2 for spatial understanding
    "florence2_technical": ModelConfig(
        name="Florence-2 Technical Drawings",
        provider=ModelProvider.HUGGINGFACE,
        model_type=ModelType.VISION,
        model_id="microsoft/Florence-2-large",
        enabled=True,
        priority=2,
        cost_per_call=0.0,
        parameters={
            "task": "<DETAILED_CAPTION>",  # or <OCR_WITH_REGION>
            "device": "cuda",
            "torch_dtype": "float16",
            "trust_remote_code": True,
            "max_new_tokens": 1024,
            "num_beams": 3,
            "do_sample": False,
        }
    ),
    
    # Tertiary: GPT-4V for high-level process understanding
    "gpt4v_process_understanding": ModelConfig(
        name="GPT-4 Vision Process Understanding",
        provider=ModelProvider.OPENAI,
        model_type=ModelType.VISION,
        model_id="gpt-4o",  # Latest vision model
        enabled=True,
        priority=3,
        cost_per_call=0.10,
        parameters={
            "max_tokens": 4096,
            "temperature": 0.1,  # Low for technical accuracy
            "detail": "high",  # High resolution analysis
        }
    ),
    
    # OCR: Donut for engineering text
    "donut_ocr": ModelConfig(
        name="Donut OCR for Engineering",
        provider=ModelProvider.HUGGINGFACE,
        model_type=ModelType.VISION,
        model_id="naver-clova-ix/donut-base",
        enabled=False,  # Enable after fine-tuning
        priority=4,
        cost_per_call=0.0,
        parameters={
            "device": "cuda",
            "task_prompt": "<s_engineering_tag>",
        }
    )
}


# ==========================================
# LLM MODELS CONFIGURATION
# ==========================================

LLM_MODELS = {
    # Primary: Claude 3.5 Sonnet for engineering reasoning
    "claude_sonnet_engineer": ModelConfig(
        name="Claude 3.5 Sonnet Engineering Reasoner",
        provider=ModelProvider.ANTHROPIC,
        model_type=ModelType.LLM,
        model_id="claude-3-5-sonnet-20241022",
        enabled=True,
        priority=1,
        cost_per_call=0.003,  # per 1K input tokens
        parameters={
            "max_tokens": 8192,
            "temperature": 0.2,  # Low for technical accuracy
            "top_p": 0.9,
            "system_prompt": """You are a senior process engineer with 25+ years experience 
in oil & gas P&ID development. You follow ISA-5.1, ADNOC DEP, API, and ASME standards. 
You have deep expertise in:
- Safety instrumented systems (SIS)
- Process control loops (PID, cascade, ratio)
- Material selection for corrosive/high temp services
- Pressure relief and flare systems
- Utility systems (steam, cooling water, instrument air)
- Operability, maintainability, and safety reviews

Provide specific, actionable engineering recommendations with standard references."""
        }
    ),
    
    # Secondary: GPT-4 Turbo for complex reasoning
    "gpt4_turbo_engineer": ModelConfig(
        name="GPT-4 Turbo Engineering Assistant",
        provider=ModelProvider.OPENAI,
        model_type=ModelType.LLM,
        model_id="gpt-4-turbo-preview",
        enabled=True,
        priority=2,
        cost_per_call=0.01,
        parameters={
            "max_tokens": 4096,
            "temperature": 0.1,
            "response_format": {"type": "json_object"}
        }
    ),
    
    # Tertiary: Gemini 1.5 Pro for large context
    "gemini_pro_context": ModelConfig(
        name="Gemini 1.5 Pro Large Context",
        provider=ModelProvider.LOCAL,  # Via Google AI API
        model_type=ModelType.LLM,
        model_id="gemini-1.5-pro",
        enabled=False,  # Enable for multi-P&ID projects
        priority=3,
        cost_per_call=0.0035,
        parameters={
            "max_output_tokens": 8192,
            "temperature": 0.2,
            "context_window": 1000000,  # 1M tokens!
        }
    )
}


# ==========================================
# DIFFUSION MODELS CONFIGURATION
# ==========================================

DIFFUSION_MODELS = {
    # Primary: Stable Diffusion XL with ControlNet
    "sdxl_controlnet_pid": ModelConfig(
        name="Stable Diffusion XL ControlNet P&ID",
        provider=ModelProvider.LOCAL,
        model_type=ModelType.DIFFUSION,
        model_id="stabilityai/stable-diffusion-xl-base-1.0",
        enabled=True,
        priority=1,
        cost_per_call=0.02,  # Amortized GPU cost
        parameters={
            "controlnet_model": "lllyasviel/control_v11p_sd15_lineart",
            "lora_weights": "./models/pid_specialist_lora_v2.safetensors",
            "num_inference_steps": 50,
            "guidance_scale": 7.5,
            "controlnet_conditioning_scale": 0.9,
            "height": 1216,  # A1 aspect ratio
            "width": 1728,
            "device": "cuda",
            "torch_dtype": "float16",
            "scheduler": "UniPCMultistepScheduler",
            "negative_prompt": """blurry, artistic, watercolor, hand-drawn, sketch, 
            3d render, cartoon, anime, photograph, colorful, decorative, 
            non-technical, imprecise, ambiguous""",
            "prompt_template": """Professional process and instrumentation diagram (P&ID), 
technical drawing, ISA-5.1 standard symbols, {equipment_description}, 
{instrument_description}, {piping_description}, clean black lines on white background, 
CAD quality, orthogonal routing, industry standard, oil and gas process, 
ADNOC DEP compliant, professional engineering drawing style, precise line weights, 
clear labels, detailed instrumentation, control loops visible, no artistic interpretation, 
technical accuracy, high contrast, crisp lines"""
        }
    ),
    
    # Fallback: DALL-E 3 for quick prototyping
    "dalle3_fallback": ModelConfig(
        name="DALL-E 3 Fallback",
        provider=ModelProvider.OPENAI,
        model_type=ModelType.DIFFUSION,
        model_id="dall-e-3",
        enabled=True,
        priority=2,
        cost_per_call=0.40,
        parameters={
            "size": "1792x1024",
            "quality": "hd",
            "style": "natural",
        }
    )
}


# ==========================================
# GNN MODELS CONFIGURATION
# ==========================================

GNN_MODELS = {
    # Process Flow GNN
    # DISABLED: Heavy lifting ML model removed for performance
    "process_flow_gnn": ModelConfig(
        name="Process Flow Graph Neural Network",
        provider=ModelProvider.LOCAL,
        model_type=ModelType.GNN,
        model_id="custom_gnn_v1",
        enabled=False,  # Disabled to reduce heavy ML processing
        priority=1,
        cost_per_call=0.0,
        parameters={
            "num_equipment_types": 50,
            "num_stream_types": 20,
            "embedding_dim": 128,
            "hidden_dim": 256,
            "num_gat_layers": 3,
            "num_attention_heads": 4,
            "dropout": 0.2,
            "learning_rate": 0.0003,
            "weight_decay": 1e-5,
            "device": "cuda",
            "model_path": "./models/process_flow_gnn_v1.pth",
            "training": {
                "batch_size": 32,
                "epochs": 100,
                "early_stopping_patience": 15,
                "val_split": 0.2
            }
        }
    )
}


# ==========================================
# RL MODELS CONFIGURATION
# ==========================================

RL_MODELS = {
    # Layout Optimizer RL Agent
    # DISABLED: Heavy lifting ML model removed for performance
    "layout_optimizer_ppo": ModelConfig(
        name="PPO Layout Optimizer",
        provider=ModelProvider.LOCAL,
        model_type=ModelType.RL,
        model_id="ppo_layout_v1",
        enabled=False,  # Disabled to reduce heavy ML processing
        priority=1,
        cost_per_call=0.0,
        parameters={
            "algorithm": "PPO",
            "policy": "MlpPolicy",
            "learning_rate": 0.0003,
            "n_steps": 2048,
            "batch_size": 64,
            "n_epochs": 10,
            "gamma": 0.99,
            "gae_lambda": 0.95,
            "clip_range": 0.2,
            "ent_coef": 0.01,
            "vf_coef": 0.5,
            "max_grad_norm": 0.5,
            "device": "cuda",
            "model_path": "./models/layout_optimizer_ppo_v1.zip",
            "reward_weights": {
                "crossing_penalty": -10.0,
                "length_penalty": -0.1,
                "spacing_reward": 5.0,
                "flow_direction_reward": 10.0,
                "grouping_reward": 5.0
            },
            "training": {
                "total_timesteps": 1000000,
                "eval_freq": 10000,
                "n_eval_episodes": 20
            }
        }
    )
}


# ==========================================
# PIPELINE CONFIGURATION
# ==========================================

PIPELINE_CONFIG = {
    "mode": "production",  # or "development", "testing"
    
    "enable_multi_model_voting": True,  # Use ensemble of models
    "voting_weights": {
        "yolov8_symbol_detector": 0.5,
        "florence2_technical": 0.3,
        "gpt4v_process_understanding": 0.2
    },
    
    "fallback_strategy": "cascade",  # "cascade" or "parallel"
    "max_pipeline_time": 300,  # seconds
    
    "caching": {
        "enabled": True,
        "backend": "redis",
        "ttl": 3600,  # 1 hour
        "cache_vision_results": True,
        "cache_llm_results": True
    },
    
    "quality_thresholds": {
        "min_symbol_confidence": 0.25,
        "min_equipment_match": 0.80,  # 80% of equipment must be detected
        "min_validation_score": 0.85
    },
    
    "cost_limits": {
        "max_cost_per_conversion": 0.50,  # USD
        "prefer_local_models": True,
        "use_api_models_only_if_necessary": True
    },
    
    "performance": {
        "parallel_processing": True,
        "max_workers": 4,
        "gpu_batch_size": 8,
        "use_mixed_precision": True
    }
}


# ==========================================
# TRAINING CONFIGURATION
# ==========================================

TRAINING_CONFIG = {
    "yolov8_training": {
        "dataset_path": "./datasets/pid_symbols/",
        "data_yaml": "./datasets/pid_symbols/data.yaml",
        "epochs": 100,
        "imgsz": 1280,
        "batch": 16,
        "device": "cuda",
        "workers": 8,
        "patience": 20,
        "optimizer": "AdamW",
        "lr0": 0.001,
        "lrf": 0.01,
        "momentum": 0.937,
        "weight_decay": 0.0005,
        "augmentation": {
            "hsv_h": 0.015,
            "hsv_s": 0.7,
            "hsv_v": 0.4,
            "degrees": 5.0,
            "translate": 0.1,
            "scale": 0.5,
            "shear": 2.0,
            "perspective": 0.0,
            "flipud": 0.0,
            "fliplr": 0.5,
            "mosaic": 1.0,
            "mixup": 0.1
        }
    },
    
    "gnn_training": {
        "dataset_path": "./datasets/process_graphs/",
        "train_split": 0.8,
        "val_split": 0.1,
        "test_split": 0.1,
        "num_workers": 4
    },
    
    "rl_training": {
        "env_name": "PIDLayoutEnv-v1",
        "n_envs": 8,  # Parallel environments
        "tensorboard_log": "./logs/rl_training/"
    },
    
    "diffusion_finetuning": {
        "dataset_path": "./datasets/professional_pids/",
        "method": "LoRA",  # Low-Rank Adaptation
        "lora_rank": 8,
        "lora_alpha": 16,
        "learning_rate": 1e-4,
        "batch_size": 4,
        "gradient_accumulation_steps": 4,
        "num_train_epochs": 100,
        "mixed_precision": "fp16",
        "validation_prompts": [
            "P&ID of a crude oil distillation unit",
            "P&ID of a gas compression train",
            "P&ID of a heat exchanger network"
        ]
    }
}


# ==========================================
# HELPER FUNCTIONS
# ==========================================

def get_model_config(model_name: str) -> Optional[ModelConfig]:
    """Get configuration for a specific model"""
    all_models = {
        **VISION_MODELS,
        **LLM_MODELS,
        **DIFFUSION_MODELS,
        **GNN_MODELS,
        **RL_MODELS
    }
    return all_models.get(model_name)


def get_enabled_models(model_type: Optional[ModelType] = None) -> List[ModelConfig]:
    """Get all enabled models, optionally filtered by type"""
    all_models = {
        **VISION_MODELS,
        **LLM_MODELS,
        **DIFFUSION_MODELS,
        **GNN_MODELS,
        **RL_MODELS
    }
    
    enabled = [m for m in all_models.values() if m.enabled]
    
    if model_type:
        enabled = [m for m in enabled if m.model_type == model_type]
    
    return sorted(enabled, key=lambda x: x.priority)


def get_primary_model(model_type: ModelType) -> Optional[ModelConfig]:
    """Get the primary (highest priority) enabled model of a given type"""
    models = get_enabled_models(model_type)
    return models[0] if models else None


def estimate_conversion_cost(use_api_models: bool = False) -> float:
    """Estimate cost per conversion based on enabled models"""
    cost = 0.0
    
    if use_api_models:
        # Vision analysis
        if VISION_MODELS["gpt4v_process_understanding"].enabled:
            cost += VISION_MODELS["gpt4v_process_understanding"].cost_per_call
        
        # LLM reasoning
        if LLM_MODELS["claude_sonnet_engineer"].enabled:
            cost += LLM_MODELS["claude_sonnet_engineer"].cost_per_call * 2  # 2 calls avg
        
        # Drawing generation
        if DIFFUSION_MODELS["dalle3_fallback"].enabled:
            cost += DIFFUSION_MODELS["dalle3_fallback"].cost_per_call
    else:
        # Local models (GPU amortized cost)
        cost = DIFFUSION_MODELS["sdxl_controlnet_pid"].cost_per_call
    
    return cost


def validate_config():
    """Validate configuration for consistency"""
    errors = []
    
    # Check at least one vision model is enabled
    vision_models = get_enabled_models(ModelType.VISION)
    if not vision_models:
        errors.append("No vision models enabled")
    
    # Check at least one LLM is enabled
    llm_models = get_enabled_models(ModelType.LLM)
    if not llm_models:
        errors.append("No LLM models enabled")
    
    # Check diffusion model is enabled
    diffusion_models = get_enabled_models(ModelType.DIFFUSION)
    if not diffusion_models:
        errors.append("No diffusion models enabled")
    
    # Check cost limit
    estimated_cost = estimate_conversion_cost(use_api_models=True)
    max_cost = PIPELINE_CONFIG["cost_limits"]["max_cost_per_conversion"]
    if estimated_cost > max_cost:
        errors.append(f"Estimated cost ${estimated_cost:.2f} exceeds limit ${max_cost:.2f}")
    
    return {"valid": len(errors) == 0, "errors": errors}


# Run validation on import
_validation = validate_config()
if not _validation["valid"]:
    import warnings
    warnings.warn(f"AI Models Configuration Issues: {', '.join(_validation['errors'])}")
