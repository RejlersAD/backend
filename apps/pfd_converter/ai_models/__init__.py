"""
AI Models Package
Advanced AI/ML models for PFD to P&ID conversion

Note: YOLOv8, GNN, and RL models are disabled for performance optimization.
They can be re-enabled by setting enabled=True in ai_models_config.py
"""

# Always available models
from .sdxl_generator import StableDiffusionPIDGenerator, generate_pid_from_specs
from .claude_reasoner import ClaudeEngineeringReasoner, validate_pid

# Heavy ML models - conditionally imported only if enabled in config
def _lazy_import_heavy_models():
    """Lazy import heavy models only when needed and enabled"""
    from ..config.ai_models_config import VISION_MODELS, GNN_MODELS, RL_MODELS
    
    models = {}
    
    # YOLOv8 - only import if enabled
    if VISION_MODELS.get("yolov8_symbol_detector", {}).enabled:
        try:
            from .yolov8_detector import YOLOv8SymbolDetector, detect_symbols
            models['YOLOv8SymbolDetector'] = YOLOv8SymbolDetector
            models['detect_symbols'] = detect_symbols
        except ImportError:
            pass
    
    # GNN - only import if enabled
    if GNN_MODELS.get("process_flow_gnn", {}).enabled:
        try:
            from .gnn_model import ProcessFlowGNN, GNNInference, predict_pid_requirements
            models['ProcessFlowGNN'] = ProcessFlowGNN
            models['GNNInference'] = GNNInference
            models['predict_pid_requirements'] = predict_pid_requirements
        except ImportError:
            pass
    
    # RL - only import if enabled
    if RL_MODELS.get("layout_optimizer_ppo", {}).enabled:
        try:
            from .rl_optimizer import RLLayoutOptimizer, optimize_pid_layout
            models['RLLayoutOptimizer'] = RLLayoutOptimizer
            models['optimize_pid_layout'] = optimize_pid_layout
        except ImportError:
            pass
    
    return models

# Core exports (always available)
__all__ = [
    # Stable Diffusion XL Generation
    "StableDiffusionPIDGenerator",
    "generate_pid_from_specs",
    
    # Claude Engineering Reasoner
    "ClaudeEngineeringReasoner",
    "validate_pid",
    
    # Heavy models available via lazy loading
    "_lazy_import_heavy_models",
]
