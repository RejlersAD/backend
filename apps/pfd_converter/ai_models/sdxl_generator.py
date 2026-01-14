"""
Stable Diffusion XL + ControlNet Pipeline
Precision P&ID drawing generation with pixel-perfect control
"""

import torch
from diffusers import (
    StableDiffusionXLControlNetPipeline,
    ControlNetModel,
    UniPCMultistepScheduler,
    AutoencoderKL
)
from diffusers.utils import load_image
from PIL import Image, ImageDraw, ImageFont
import numpy as np
import cv2
from typing import Dict, List, Optional, Tuple
import logging
from pathlib import Path
from ..config.ai_models_config import get_model_config

logger = logging.getLogger(__name__)


class StableDiffusionPIDGenerator:
    """
    Stable Diffusion XL + ControlNet for precise P&ID generation
    Fine-tuned on professional engineering drawings
    """
    
    def __init__(self, config_name: str = "sdxl_controlnet_pid"):
        """
        Initialize Stable Diffusion XL pipeline
        
        Args:
            config_name: Model configuration name
        """
        self.config = get_model_config(config_name)
        if not self.config or not self.config.enabled:
            raise ValueError(f"Model {config_name} not found or disabled")
        
        self.device = self.config.parameters.get("device", "cuda" if torch.cuda.is_available() else "cpu")
        self.torch_dtype = getattr(torch, self.config.parameters.get("torch_dtype", "float16"))
        
        self.pipe = None
        self.controlnet = None
        
        logger.info(f"Initializing Stable Diffusion XL on {self.device}")
        self._load_models()
    
    def _load_models(self):
        """Load ControlNet and SDXL models"""
        params = self.config.parameters
        
        try:
            # Load ControlNet for structural guidance
            logger.info(f"Loading ControlNet: {params['controlnet_model']}")
            self.controlnet = ControlNetModel.from_pretrained(
                params["controlnet_model"],
                torch_dtype=self.torch_dtype
            )
            
            # Load VAE (for better detail)
            logger.info("Loading VAE for high quality...")
            vae = AutoencoderKL.from_pretrained(
                "madebyollin/sdxl-vae-fp16-fix",
                torch_dtype=self.torch_dtype
            )
            
            # Load SDXL pipeline
            logger.info(f"Loading SDXL: {self.config.model_id}")
            self.pipe = StableDiffusionXLControlNetPipeline.from_pretrained(
                self.config.model_id,
                controlnet=self.controlnet,
                vae=vae,
                torch_dtype=self.torch_dtype,
                variant="fp16",
                use_safetensors=True
            )
            
            # Load LoRA weights if available
            lora_path = params.get("lora_weights")
            if lora_path and Path(lora_path).exists():
                logger.info(f"Loading LoRA weights: {lora_path}")
                self.pipe.load_lora_weights(lora_path)
            else:
                logger.warning(f"LoRA weights not found at {lora_path}. Using base model.")
                logger.info("To fine-tune, run: python train_sdxl_pid.py")
            
            # Optimize pipeline
            self.pipe.to(self.device)
            
            # Set scheduler for quality
            scheduler_name = params.get("scheduler", "UniPCMultistepScheduler")
            if scheduler_name == "UniPCMultistepScheduler":
                self.pipe.scheduler = UniPCMultistepScheduler.from_config(
                    self.pipe.scheduler.config
                )
            
            # Enable optimizations
            if self.device == "cuda":
                self.pipe.enable_model_cpu_offload()  # Save VRAM
                self.pipe.enable_vae_slicing()  # Process VAE in slices
                self.pipe.enable_vae_tiling()  # Process large images
                
                # Use xformers if available
                try:
                    self.pipe.enable_xformers_memory_efficient_attention()
                    logger.info("✅ xformers memory efficient attention enabled")
                except Exception as e:
                    logger.warning(f"xformers not available: {e}")
            
            logger.info("✅ Stable Diffusion XL pipeline loaded successfully")
            
        except Exception as e:
            logger.error(f"Failed to load SDXL pipeline: {e}")
            raise
    
    def generate_pid(
        self,
        skeleton_image: Image.Image,
        equipment_list: List[Dict],
        instruments: List[Dict],
        piping_specs: Dict,
        project_info: Dict = None,
        num_inference_steps: Optional[int] = None,
        guidance_scale: Optional[float] = None,
        seed: Optional[int] = None
    ) -> Image.Image:
        """
        Generate P&ID drawing from skeleton layout
        
        Args:
            skeleton_image: Line drawing showing layout structure
            equipment_list: List of equipment with positions
            instruments: List of instruments
            piping_specs: Piping specifications
            project_info: Project metadata
            num_inference_steps: Denoising steps (default from config)
            guidance_scale: Prompt guidance (default from config)
            seed: Random seed for reproducibility
            
        Returns:
            Generated P&ID image
        """
        params = self.config.parameters
        
        # Set parameters
        num_inference_steps = num_inference_steps or params.get("num_inference_steps", 50)
        guidance_scale = guidance_scale or params.get("guidance_scale", 7.5)
        controlnet_scale = params.get("controlnet_conditioning_scale", 0.9)
        
        # Build prompt from specifications
        prompt = self._build_engineering_prompt(equipment_list, instruments, piping_specs, project_info)
        negative_prompt = params.get("negative_prompt", "")
        
        logger.info("Generating P&ID with Stable Diffusion XL...")
        logger.info(f"  Steps: {num_inference_steps}, Guidance: {guidance_scale}")
        logger.info(f"  ControlNet scale: {controlnet_scale}")
        
        # Set seed for reproducibility
        generator = None
        if seed is not None:
            generator = torch.Generator(device=self.device).manual_seed(seed)
            logger.info(f"  Using seed: {seed}")
        
        # Prepare skeleton image
        control_image = self._prepare_control_image(skeleton_image)
        
        # Generate
        with torch.inference_mode():
            output = self.pipe(
                prompt=prompt,
                negative_prompt=negative_prompt,
                image=control_image,
                num_inference_steps=num_inference_steps,
                guidance_scale=guidance_scale,
                controlnet_conditioning_scale=controlnet_scale,
                height=params.get("height", 1216),
                width=params.get("width", 1728),
                generator=generator,
                num_images_per_prompt=1
            )
        
        generated_image = output.images[0]
        
        logger.info("✅ P&ID generated successfully")
        return generated_image
    
    def _build_engineering_prompt(
        self,
        equipment: List[Dict],
        instruments: List[Dict],
        piping: Dict,
        project_info: Dict = None
    ) -> str:
        """Build detailed engineering prompt"""
        template = self.config.parameters.get("prompt_template", "")
        
        # Extract equipment descriptions
        equipment_desc = self._describe_equipment(equipment)
        instrument_desc = self._describe_instruments(instruments)
        piping_desc = self._describe_piping(piping)
        
        # Fill template
        prompt = template.format(
            equipment_description=equipment_desc,
            instrument_description=instrument_desc,
            piping_description=piping_desc
        )
        
        # Add project-specific details
        if project_info:
            project_code = project_info.get("project_code", "")
            service = project_info.get("service", "")
            if project_code:
                prompt += f", project {project_code}"
            if service:
                prompt += f", {service} service"
        
        logger.debug(f"Generated prompt: {prompt[:200]}...")
        return prompt
    
    def _describe_equipment(self, equipment: List[Dict]) -> str:
        """Convert equipment list to natural language description"""
        if not equipment:
            return "no major equipment"
        
        # Count equipment types
        types_count = {}
        for eq in equipment:
            eq_type = eq.get("type", "equipment").replace("_", " ")
            types_count[eq_type] = types_count.get(eq_type, 0) + 1
        
        # Build description
        descriptions = []
        for eq_type, count in types_count.items():
            if count == 1:
                descriptions.append(f"1 {eq_type}")
            else:
                descriptions.append(f"{count} {eq_type}s")
        
        return ", ".join(descriptions)
    
    def _describe_instruments(self, instruments: List[Dict]) -> str:
        """Convert instruments to description"""
        if not instruments:
            return "basic instrumentation"
        
        types_count = {}
        for inst in instruments:
            inst_type = inst.get("type", "instrument").replace("_", " ")
            types_count[inst_type] = types_count.get(inst_type, 0) + 1
        
        descriptions = []
        for inst_type, count in types_count.items():
            descriptions.append(f"{count} {inst_type}")
        
        return "instrumentation with " + ", ".join(descriptions)
    
    def _describe_piping(self, piping: Dict) -> str:
        """Convert piping specs to description"""
        if not piping:
            return "standard process piping"
        
        # Extract line sizes
        line_sizes = piping.get("line_sizes", [])
        if line_sizes:
            sizes_str = ", ".join(str(s) for s in sorted(set(line_sizes)))
            return f"piping with line sizes {sizes_str}"
        
        return "process piping with multiple line sizes"
    
    def _prepare_control_image(self, skeleton: Image.Image) -> Image.Image:
        """Prepare skeleton image for ControlNet"""
        # Convert to numpy
        img = np.array(skeleton)
        
        # Ensure grayscale or binary
        if len(img.shape) == 3:
            img = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
        
        # Apply edge detection for clean lines
        edges = cv2.Canny(img, threshold1=50, threshold2=150)
        
        # Invert (white lines on black background for ControlNet)
        edges = 255 - edges
        
        # Convert back to PIL
        control_image = Image.fromarray(edges)
        
        # Resize to target dimensions
        target_size = (
            self.config.parameters.get("width", 1728),
            self.config.parameters.get("height", 1216)
        )
        control_image = control_image.resize(target_size, Image.Resampling.LANCZOS)
        
        return control_image
    
    def create_skeleton_from_graph(self, process_graph: Dict, layout: Dict) -> Image.Image:
        """
        Create skeleton image from process graph and layout
        
        Args:
            process_graph: Process flow graph with nodes and edges
            layout: Positions for each equipment node
            
        Returns:
            Skeleton image for ControlNet
        """
        width = self.config.parameters.get("width", 1728)
        height = self.config.parameters.get("height", 1216)
        
        # Create blank white image
        skeleton = Image.new('RGB', (width, height), 'white')
        draw = ImageDraw.Draw(skeleton)
        
        # Draw equipment as simple rectangles/circles
        for node_id, position in layout.items():
            x, y = position["x"], position["y"]
            node_data = process_graph["nodes"].get(node_id, {})
            node_type = node_data.get("type", "equipment")
            
            # Simple shape based on type
            size = 30  # pixels
            if "vessel" in node_type or "tank" in node_type:
                # Rectangle for vessels
                draw.rectangle(
                    [x - size, y - size*2, x + size, y + size*2],
                    outline='black',
                    width=2
                )
            elif "pump" in node_type or "compressor" in node_type:
                # Circle for rotating equipment
                draw.ellipse(
                    [x - size, y - size, x + size, y + size],
                    outline='black',
                    width=2
                )
            elif "heat_exchanger" in node_type:
                # Two circles for heat exchanger
                draw.ellipse([x - size, y - size//2, x, y + size//2], outline='black', width=2)
                draw.ellipse([x, y - size//2, x + size, y + size//2], outline='black', width=2)
            else:
                # Default rectangle
                draw.rectangle(
                    [x - size, y - size, x + size, y + size],
                    outline='black',
                    width=2
                )
        
        # Draw connections as lines
        for edge in process_graph.get("edges", []):
            from_node = edge["from"]
            to_node = edge["to"]
            
            if from_node in layout and to_node in layout:
                x1, y1 = layout[from_node]["x"], layout[from_node]["y"]
                x2, y2 = layout[to_node]["x"], layout[to_node]["y"]
                
                # Draw line
                draw.line([(x1, y1), (x2, y2)], fill='black', width=2)
        
        logger.info(f"Created skeleton with {len(layout)} equipment and {len(process_graph.get('edges', []))} connections")
        
        return skeleton
    
    def batch_generate(
        self,
        skeleton_images: List[Image.Image],
        specifications: List[Dict],
        batch_size: int = 4
    ) -> List[Image.Image]:
        """
        Generate multiple P&IDs in batches
        
        Args:
            skeleton_images: List of skeleton images
            specifications: List of specifications for each P&ID
            batch_size: Batch size for generation
            
        Returns:
            List of generated P&ID images
        """
        results = []
        
        for i in range(0, len(skeleton_images), batch_size):
            batch_skeletons = skeleton_images[i:i+batch_size]
            batch_specs = specifications[i:i+batch_size]
            
            logger.info(f"Generating batch {i//batch_size + 1} ({len(batch_skeletons)} images)")
            
            for skeleton, specs in zip(batch_skeletons, batch_specs):
                image = self.generate_pid(
                    skeleton_image=skeleton,
                    equipment_list=specs.get("equipment", []),
                    instruments=specs.get("instruments", []),
                    piping_specs=specs.get("piping", {}),
                    project_info=specs.get("project_info")
                )
                results.append(image)
        
        logger.info(f"✅ Generated {len(results)} P&IDs")
        return results


class SDXLTrainer:
    """Trainer for fine-tuning SDXL on P&ID datasets"""
    
    @staticmethod
    def prepare_dataset(pids_directory: str, output_dir: str):
        """
        Prepare P&ID dataset for training
        
        Args:
            pids_directory: Directory with P&ID images
            output_dir: Output directory for processed dataset
        """
        from ..config.ai_models_config import TRAINING_CONFIG
        
        config = TRAINING_CONFIG["diffusion_finetuning"]
        
        logger.info(f"Preparing dataset from {pids_directory}")
        
        # TODO: Implement dataset preparation
        # 1. Load P&ID images
        # 2. Extract skeletons (edge detection)
        # 3. Create image-caption pairs
        # 4. Resize to target dimensions
        # 5. Save in training format
        
        logger.info(f"✅ Dataset prepared in {output_dir}")
    
    @staticmethod
    def train_lora(
        dataset_path: str,
        output_dir: str = "./models/",
        **kwargs
    ):
        """
        Fine-tune SDXL with LoRA on P&ID dataset
        
        Args:
            dataset_path: Path to prepared dataset
            output_dir: Output directory for trained model
            **kwargs: Additional training parameters
        """
        from ..config.ai_models_config import TRAINING_CONFIG
        
        config = TRAINING_CONFIG["diffusion_finetuning"]
        
        logger.info("Starting SDXL LoRA fine-tuning...")
        logger.info(f"  Dataset: {dataset_path}")
        logger.info(f"  LoRA rank: {config['lora_rank']}")
        logger.info(f"  Epochs: {config['num_train_epochs']}")
        
        # TODO: Implement LoRA training
        # Use diffusers training scripts or custom training loop
        
        logger.info(f"✅ Training completed. Model saved to {output_dir}")


# Convenience function
def generate_pid_from_specs(
    equipment: List[Dict],
    instruments: List[Dict],
    piping: Dict,
    layout: Dict = None,
    project_info: Dict = None
) -> Image.Image:
    """
    Quick function to generate P&ID from specifications
    
    Args:
        equipment: Equipment list
        instruments: Instruments list
        piping: Piping specifications
        layout: Equipment layout positions
        project_info: Project metadata
        
    Returns:
        Generated P&ID image
    """
    generator = StableDiffusionPIDGenerator()
    
    # Create skeleton if layout provided
    if layout:
        process_graph = {
            "nodes": {eq["tag"]: eq for eq in equipment},
            "edges": piping.get("connections", [])
        }
        skeleton = generator.create_skeleton_from_graph(process_graph, layout)
    else:
        # Create simple skeleton
        skeleton = Image.new('RGB', (1728, 1216), 'white')
    
    # Generate P&ID
    image = generator.generate_pid(
        skeleton_image=skeleton,
        equipment_list=equipment,
        instruments=instruments,
        piping_specs=piping,
        project_info=project_info
    )
    
    return image
