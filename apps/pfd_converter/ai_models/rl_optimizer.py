"""
Reinforcement Learning Layout Optimizer
PPO agent that learns optimal P&ID equipment layout
Minimizes line crossings, maximizes readability
"""

import gymnasium as gym
from gymnasium import spaces
import numpy as np
from typing import Dict, List, Tuple, Optional
import logging
from stable_baselines3 import PPO
from stable_baselines3.common.env_util import make_vec_env
from stable_baselines3.common.callbacks import EvalCallback, CheckpointCallback
from stable_baselines3.common.vec_env import VecMonitor
from ..config.ai_models_config import get_model_config
import networkx as nx

logger = logging.getLogger(__name__)


class PIDLayoutEnv(gym.Env):
    """
    Gymnasium environment for P&ID layout optimization
    State: Equipment positions and connections
    Action: Move/rotate equipment
    Reward: Based on layout quality metrics
    """
    
    metadata = {"render_modes": []}
    
    def __init__(self, process_graph: Dict, canvas_size: Tuple[int, int] = (1728, 1216)):
        """
        Initialize environment
        
        Args:
            process_graph: Process graph with equipment nodes and connections
            canvas_size: Drawing canvas size (width, height) in pixels
        """
        super(PIDLayoutEnv, self).__init__()
        
        self.process_graph = process_graph
        self.nodes = list(process_graph.get("nodes", {}).keys())
        self.edges = process_graph.get("edges", [])
        self.canvas_width, self.canvas_height = canvas_size
        
        self.num_equipment = len(self.nodes)
        
        # Action space: [dx, dy, dangle] for each equipment
        # Each action is normalized to [-1, 1]
        self.action_space = spaces.Box(
            low=-1.0,
            high=1.0,
            shape=(self.num_equipment * 3,),  # x, y, angle for each
            dtype=np.float32
        )
        
        # Observation space: [x, y, angle, type] for each equipment
        self.observation_space = spaces.Box(
            low=0.0,
            high=1.0,
            shape=(self.num_equipment * 4,),
            dtype=np.float32
        )
        
        # State: current equipment positions and angles
        self.equipment_positions = {}  # {node_id: (x, y, angle)}
        self.step_count = 0
        self.max_steps = 1000
        
        # Reward weights (configurable)
        config = get_model_config("layout_optimizer_ppo")
        self.reward_weights = config.parameters.get("reward_weights", {
            "crossing_penalty": -10.0,
            "length_penalty": -0.1,
            "spacing_reward": 5.0,
            "flow_direction_reward": 10.0,
            "grouping_reward": 5.0
        })
        
        logger.info(f"PIDLayoutEnv initialized: {self.num_equipment} equipment, "
                   f"{len(self.edges)} connections")
        
        self.reset()
    
    def reset(self, seed: Optional[int] = None, options: Optional[dict] = None):
        """Reset environment to initial state"""
        super().reset(seed=seed)
        
        # Initialize random positions
        for i, node_id in enumerate(self.nodes):
            x = np.random.uniform(0.1, 0.9)  # Normalized [0, 1]
            y = np.random.uniform(0.1, 0.9)
            angle = np.random.uniform(0, 2 * np.pi)
            self.equipment_positions[node_id] = (x, y, angle)
        
        self.step_count = 0
        
        observation = self._get_observation()
        info = {}
        
        return observation, info
    
    def step(self, action: np.ndarray):
        """
        Execute action and return new state
        
        Args:
            action: Equipment movements [dx, dy, dangle] for each equipment
        
        Returns:
            observation, reward, terminated, truncated, info
        """
        # Apply actions (movements)
        action = action.reshape(self.num_equipment, 3)
        
        for i, node_id in enumerate(self.nodes):
            x, y, angle = self.equipment_positions[node_id]
            
            # Update position (scaled movements)
            dx, dy, dangle = action[i]
            x += dx * 0.05  # Small movements
            y += dy * 0.05
            angle += dangle * 0.2
            
            # Clip to canvas bounds
            x = np.clip(x, 0.05, 0.95)
            y = np.clip(y, 0.05, 0.95)
            angle = angle % (2 * np.pi)
            
            self.equipment_positions[node_id] = (x, y, angle)
        
        # Calculate reward
        reward = self._calculate_reward()
        
        # Check termination
        self.step_count += 1
        terminated = False  # Never terminate early (fixed horizon)
        truncated = self.step_count >= self.max_steps
        
        observation = self._get_observation()
        info = self._get_info()
        
        return observation, reward, terminated, truncated, info
    
    def _get_observation(self) -> np.ndarray:
        """Get current state observation"""
        obs = []
        for node_id in self.nodes:
            x, y, angle = self.equipment_positions[node_id]
            node_type = self.process_graph["nodes"][node_id].get("type", "equipment")
            type_id = self._equipment_type_to_id(node_type)
            
            obs.extend([x, y, angle / (2 * np.pi), type_id / 50.0])  # Normalize
        
        return np.array(obs, dtype=np.float32)
    
    def _calculate_reward(self) -> float:
        """
        Calculate layout quality reward
        Multi-objective: minimize crossings, length; maximize spacing, flow direction
        """
        reward = 0.0
        
        # 1. Line crossing penalty
        num_crossings = self._count_line_crossings()
        reward += num_crossings * self.reward_weights["crossing_penalty"]
        
        # 2. Total line length penalty
        total_length = self._calculate_total_line_length()
        reward += total_length * self.reward_weights["length_penalty"]
        
        # 3. Proper spacing reward
        proper_spacing_count = self._count_proper_spacing()
        reward += proper_spacing_count * self.reward_weights["spacing_reward"]
        
        # 4. Flow direction reward (left to right preferred)
        flow_score = self._calculate_flow_direction_score()
        reward += flow_score * self.reward_weights["flow_direction_reward"]
        
        # 5. Equipment grouping reward (similar equipment grouped)
        grouping_score = self._calculate_grouping_score()
        reward += grouping_score * self.reward_weights["grouping_reward"]
        
        return reward
    
    def _count_line_crossings(self) -> int:
        """Count number of line crossings in current layout"""
        crossings = 0
        
        # Get all line segments
        segments = []
        for edge in self.edges:
            from_node = edge.get("from")
            to_node = edge.get("to")
            
            if from_node in self.equipment_positions and to_node in self.equipment_positions:
                x1, y1, _ = self.equipment_positions[from_node]
                x2, y2, _ = self.equipment_positions[to_node]
                segments.append(((x1, y1), (x2, y2)))
        
        # Check all pairs for intersections
        for i in range(len(segments)):
            for j in range(i + 1, len(segments)):
                if self._segments_intersect(segments[i], segments[j]):
                    crossings += 1
        
        return crossings
    
    def _segments_intersect(self, seg1: Tuple, seg2: Tuple) -> bool:
        """Check if two line segments intersect"""
        (x1, y1), (x2, y2) = seg1
        (x3, y3), (x4, y4) = seg2
        
        def ccw(A, B, C):
            return (C[1] - A[1]) * (B[0] - A[0]) > (B[1] - A[1]) * (C[0] - A[0])
        
        return ccw((x1, y1), (x3, y3), (x4, y4)) != ccw((x2, y2), (x3, y3), (x4, y4)) and \
               ccw((x1, y1), (x2, y2), (x3, y3)) != ccw((x1, y1), (x2, y2), (x4, y4))
    
    def _calculate_total_line_length(self) -> float:
        """Calculate total length of all connection lines"""
        total_length = 0.0
        
        for edge in self.edges:
            from_node = edge.get("from")
            to_node = edge.get("to")
            
            if from_node in self.equipment_positions and to_node in self.equipment_positions:
                x1, y1, _ = self.equipment_positions[from_node]
                x2, y2, _ = self.equipment_positions[to_node]
                
                length = np.sqrt((x2 - x1)**2 + (y2 - y1)**2)
                total_length += length
        
        return total_length
    
    def _count_proper_spacing(self) -> int:
        """Count equipment pairs with proper spacing"""
        proper_count = 0
        min_spacing = 0.1  # Minimum normalized distance
        max_spacing = 0.4  # Maximum normalized distance
        
        positions = list(self.equipment_positions.values())
        
        for i in range(len(positions)):
            for j in range(i + 1, len(positions)):
                x1, y1, _ = positions[i]
                x2, y2, _ = positions[j]
                
                dist = np.sqrt((x2 - x1)**2 + (y2 - y1)**2)
                
                if min_spacing <= dist <= max_spacing:
                    proper_count += 1
        
        return proper_count
    
    def _calculate_flow_direction_score(self) -> float:
        """Calculate score for maintaining left-to-right flow"""
        score = 0.0
        
        for edge in self.edges:
            from_node = edge.get("from")
            to_node = edge.get("to")
            
            if from_node in self.equipment_positions and to_node in self.equipment_positions:
                x1, _, _ = self.equipment_positions[from_node]
                x2, _, _ = self.equipment_positions[to_node]
                
                # Reward if destination is to the right of source
                if x2 > x1:
                    score += 1.0
        
        return score / len(self.edges) if self.edges else 0.0
    
    def _calculate_grouping_score(self) -> float:
        """Calculate score for grouping similar equipment"""
        score = 0.0
        
        # Group equipment by type
        type_groups = {}
        for node_id in self.nodes:
            node_type = self.process_graph["nodes"][node_id].get("type", "equipment")
            if node_type not in type_groups:
                type_groups[node_type] = []
            type_groups[node_type].append(node_id)
        
        # For each group, reward if equipment is clustered
        for eq_type, group_nodes in type_groups.items():
            if len(group_nodes) < 2:
                continue
            
            # Calculate average distance within group
            distances = []
            for i in range(len(group_nodes)):
                for j in range(i + 1, len(group_nodes)):
                    x1, y1, _ = self.equipment_positions[group_nodes[i]]
                    x2, y2, _ = self.equipment_positions[group_nodes[j]]
                    dist = np.sqrt((x2 - x1)**2 + (y2 - y1)**2)
                    distances.append(dist)
            
            avg_dist = np.mean(distances)
            # Reward smaller average distances (tighter grouping)
            score += 1.0 / (1.0 + avg_dist)
        
        return score
    
    def _get_info(self) -> Dict:
        """Get additional info about current state"""
        return {
            "step": self.step_count,
            "num_crossings": self._count_line_crossings(),
            "total_length": self._calculate_total_line_length(),
            "proper_spacing": self._count_proper_spacing(),
            "flow_score": self._calculate_flow_direction_score(),
            "grouping_score": self._calculate_grouping_score()
        }
    
    def _equipment_type_to_id(self, eq_type: str) -> int:
        """Map equipment type to numeric ID"""
        type_map = {
            "vessel": 0, "pump": 1, "compressor": 2, "heat_exchanger": 3,
            "tank": 4, "turbine": 5
        }
        return type_map.get(eq_type.lower(), 0)
    
    def get_layout(self) -> Dict[str, Dict]:
        """Get current layout in absolute coordinates"""
        layout = {}
        for node_id, (x, y, angle) in self.equipment_positions.items():
            layout[node_id] = {
                "x": x * self.canvas_width,
                "y": y * self.canvas_height,
                "angle": angle
            }
        return layout


class RLLayoutOptimizer:
    """Reinforcement Learning optimizer for P&ID layouts"""
    
    def __init__(self, config_name: str = "layout_optimizer_ppo"):
        """
        Initialize RL optimizer
        
        Args:
            config_name: Model configuration name
        """
        self.config = get_model_config(config_name)
        self.device = self.config.parameters.get("device", "cpu")
        self.model_path = self.config.parameters.get("model_path", "./models/layout_optimizer_ppo_v1.zip")
        
        self.model = None
        
        logger.info(f"RL Layout Optimizer initialized (device: {self.device})")
    
    def train(
        self,
        training_graphs: List[Dict],
        total_timesteps: int = 1000000,
        n_envs: int = 8
    ):
        """
        Train PPO agent on multiple process graphs
        
        Args:
            training_graphs: List of process graphs for training
            total_timesteps: Total training timesteps
            n_envs: Number of parallel environments
        """
        logger.info(f"Training RL layout optimizer on {len(training_graphs)} graphs...")
        logger.info(f"Total timesteps: {total_timesteps}, Parallel envs: {n_envs}")
        
        # Create vectorized environment
        def make_env(graph):
            def _init():
                return PIDLayoutEnv(graph)
            return _init
        
        # Use first graph for all envs (or cycle through graphs)
        env = make_vec_env(make_env(training_graphs[0]), n_envs=n_envs)
        env = VecMonitor(env)
        
        # Create PPO model
        params = self.config.parameters
        self.model = PPO(
            policy=params.get("policy", "MlpPolicy"),
            env=env,
            learning_rate=params.get("learning_rate", 0.0003),
            n_steps=params.get("n_steps", 2048),
            batch_size=params.get("batch_size", 64),
            n_epochs=params.get("n_epochs", 10),
            gamma=params.get("gamma", 0.99),
            gae_lambda=params.get("gae_lambda", 0.95),
            clip_range=params.get("clip_range", 0.2),
            ent_coef=params.get("ent_coef", 0.01),
            vf_coef=params.get("vf_coef", 0.5),
            max_grad_norm=params.get("max_grad_norm", 0.5),
            tensorboard_log="./logs/rl_training/",
            device=self.device,
            verbose=1
        )
        
        # Callbacks
        eval_env = make_vec_env(make_env(training_graphs[0]), n_envs=1)
        eval_callback = EvalCallback(
            eval_env,
            eval_freq=10000,
            n_eval_episodes=20,
            best_model_save_path="./models/rl_best/",
            log_path="./logs/rl_eval/",
            deterministic=True
        )
        
        checkpoint_callback = CheckpointCallback(
            save_freq=50000,
            save_path="./models/rl_checkpoints/",
            name_prefix="ppo_layout"
        )
        
        # Train
        self.model.learn(
            total_timesteps=total_timesteps,
            callback=[eval_callback, checkpoint_callback],
            progress_bar=True
        )
        
        # Save final model
        self.model.save(self.model_path)
        logger.info(f"✅ Training complete. Model saved to {self.model_path}")
    
    def optimize_layout(self, process_graph: Dict, max_iterations: int = 1000) -> Dict:
        """
        Optimize layout for a process graph
        
        Args:
            process_graph: Process graph with equipment and connections
            max_iterations: Maximum optimization iterations
            
        Returns:
            Optimized equipment layout
        """
        # Load trained model
        if self.model is None:
            try:
                self.model = PPO.load(self.model_path, device=self.device)
                logger.info(f"Loaded trained model from {self.model_path}")
            except Exception as e:
                logger.warning(f"Could not load trained model: {e}")
                logger.warning("Using random initialization instead")
                return self._random_layout(process_graph)
        
        # Create environment
        env = PIDLayoutEnv(process_graph)
        
        # Run optimization
        obs, _ = env.reset()
        logger.info(f"Optimizing layout for {env.num_equipment} equipment...")
        
        best_reward = float('-inf')
        best_layout = None
        
        for step in range(max_iterations):
            action, _ = self.model.predict(obs, deterministic=True)
            obs, reward, terminated, truncated, info = env.step(action)
            
            if reward > best_reward:
                best_reward = reward
                best_layout = env.get_layout()
            
            if terminated or truncated:
                obs, _ = env.reset()
            
            if step % 100 == 0:
                logger.info(f"  Step {step}/{max_iterations}: Reward {reward:.2f}, "
                           f"Crossings {info['num_crossings']}")
        
        logger.info(f"✅ Optimization complete. Best reward: {best_reward:.2f}")
        return best_layout
    
    def _random_layout(self, process_graph: Dict) -> Dict:
        """Fallback: random layout"""
        env = PIDLayoutEnv(process_graph)
        env.reset()
        return env.get_layout()


# Convenience function
def optimize_pid_layout(process_graph: Dict) -> Dict:
    """
    Quick function to optimize P&ID layout
    
    Args:
        process_graph: Process graph
        
    Returns:
        Optimized layout
    """
    optimizer = RLLayoutOptimizer()
    return optimizer.optimize_layout(process_graph)
