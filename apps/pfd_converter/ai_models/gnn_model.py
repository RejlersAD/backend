"""
Graph Neural Network for Process Flow Understanding
Learns patterns from PFD→P&ID conversions to predict required instrumentation
"""

import torch
import torch.nn as nn
import torch.nn.functional as F
from torch_geometric.nn import GATConv, global_mean_pool
from torch_geometric.data import Data, DataLoader
from typing import Dict, List, Tuple, Optional
import logging
import numpy as np
from pathlib import Path
from ..config.ai_models_config import get_model_config

logger = logging.getLogger(__name__)


class ProcessFlowGNN(nn.Module):
    """
    Graph Attention Network for process flow understanding
    Predicts required instruments, valves, and optimal positions
    """
    
    def __init__(self, config_dict: Dict):
        """
        Initialize GNN model
        
        Args:
            config_dict: Configuration from ai_models_config
        """
        super(ProcessFlowGNN, self).__init__()
        
        self.num_equipment_types = config_dict.get("num_equipment_types", 50)
        self.num_stream_types = config_dict.get("num_stream_types", 20)
        self.embedding_dim = config_dict.get("embedding_dim", 128)
        self.hidden_dim = config_dict.get("hidden_dim", 256)
        self.num_heads = config_dict.get("num_attention_heads", 4)
        self.dropout = config_dict.get("dropout", 0.2)
        
        # Node embeddings (equipment features)
        self.equipment_embedding = nn.Embedding(self.num_equipment_types, self.embedding_dim)
        
        # Edge embeddings (stream features)
        self.stream_embedding = nn.Embedding(self.num_stream_types, 64)
        
        # Graph Attention layers
        self.gat1 = GATConv(
            self.embedding_dim, 
            self.hidden_dim, 
            heads=self.num_heads, 
            edge_dim=64, 
            dropout=self.dropout
        )
        
        self.gat2 = GATConv(
            self.hidden_dim * self.num_heads,
            self.hidden_dim,
            heads=self.num_heads,
            edge_dim=64,
            dropout=self.dropout
        )
        
        self.gat3 = GATConv(
            self.hidden_dim * self.num_heads,
            self.embedding_dim,
            heads=1,
            edge_dim=64,
            dropout=self.dropout
        )
        
        # Prediction heads
        self.instrument_predictor = nn.Sequential(
            nn.Linear(self.embedding_dim, 128),
            nn.ReLU(),
            nn.Dropout(self.dropout),
            nn.Linear(128, 50)  # 50 instrument types
        )
        
        self.valve_predictor = nn.Sequential(
            nn.Linear(self.embedding_dim, 128),
            nn.ReLU(),
            nn.Dropout(self.dropout),
            nn.Linear(128, 20)  # 20 valve types
        )
        
        self.position_predictor = nn.Sequential(
            nn.Linear(self.embedding_dim, 64),
            nn.ReLU(),
            nn.Dropout(self.dropout),
            nn.Linear(64, 2)  # X, Y coordinates
        )
        
        logger.info(f"ProcessFlowGNN initialized: {self.embedding_dim}d embeddings, "
                   f"{self.num_heads} attention heads")
    
    def forward(self, data: Data) -> Dict[str, torch.Tensor]:
        """
        Forward pass
        
        Args:
            data: PyTorch Geometric Data object with:
                - x: node features [num_nodes, num_equipment_types]
                - edge_index: graph connectivity [2, num_edges]
                - edge_attr: edge features [num_edges, num_stream_types]
                - batch: batch assignment [num_nodes]
        
        Returns:
            Dictionary with predictions
        """
        x, edge_index, edge_attr = data.x, data.edge_index, data.edge_attr
        
        # Embed equipment nodes
        x = self.equipment_embedding(x.long())
        
        # Embed stream edges
        edge_attr = self.stream_embedding(edge_attr.long())
        
        # Graph attention layers
        x = F.elu(self.gat1(x, edge_index, edge_attr))
        x = F.elu(self.gat2(x, edge_index, edge_attr))
        x = self.gat3(x, edge_index, edge_attr)
        
        # Predictions
        instruments = self.instrument_predictor(x)
        valves = self.valve_predictor(x)
        positions = self.position_predictor(x)
        
        return {
            "instruments": instruments,  # [num_nodes, 50]
            "valves": valves,            # [num_nodes, 20]
            "positions": positions       # [num_nodes, 2]
        }


class GNNTrainer:
    """Trainer for Process Flow GNN"""
    
    def __init__(self, model: ProcessFlowGNN, config_name: str = "process_flow_gnn"):
        """
        Initialize trainer
        
        Args:
            model: GNN model instance
            config_name: Model configuration name
        """
        self.model = model
        self.config = get_model_config(config_name)
        self.device = self.config.parameters.get("device", "cuda" if torch.cuda.is_available() else "cpu")
        
        self.model.to(self.device)
        
        # Optimizer
        lr = self.config.parameters.get("learning_rate", 0.0003)
        weight_decay = self.config.parameters.get("weight_decay", 1e-5)
        self.optimizer = torch.optim.Adam(
            self.model.parameters(),
            lr=lr,
            weight_decay=weight_decay
        )
        
        # Loss functions
        self.instrument_criterion = nn.BCEWithLogitsLoss()  # Multi-label classification
        self.valve_criterion = nn.BCEWithLogitsLoss()
        self.position_criterion = nn.MSELoss()
        
        logger.info(f"GNN Trainer initialized on {self.device}")
    
    def train_epoch(self, train_loader: DataLoader) -> float:
        """Train for one epoch"""
        self.model.train()
        total_loss = 0
        
        for batch in train_loader:
            batch = batch.to(self.device)
            
            # Forward pass
            predictions = self.model(batch)
            
            # Compute losses
            instrument_loss = self.instrument_criterion(
                predictions["instruments"],
                batch.y_instruments
            )
            valve_loss = self.valve_criterion(
                predictions["valves"],
                batch.y_valves
            )
            position_loss = self.position_criterion(
                predictions["positions"],
                batch.y_positions
            )
            
            # Combined loss
            loss = instrument_loss + valve_loss + 0.1 * position_loss
            
            # Backward pass
            self.optimizer.zero_grad()
            loss.backward()
            torch.nn.utils.clip_grad_norm_(self.model.parameters(), 1.0)
            self.optimizer.step()
            
            total_loss += loss.item()
        
        avg_loss = total_loss / len(train_loader)
        return avg_loss
    
    def validate(self, val_loader: DataLoader) -> Dict[str, float]:
        """Validate model"""
        self.model.eval()
        
        total_instrument_loss = 0
        total_valve_loss = 0
        total_position_loss = 0
        
        with torch.no_grad():
            for batch in val_loader:
                batch = batch.to(self.device)
                
                predictions = self.model(batch)
                
                instrument_loss = self.instrument_criterion(
                    predictions["instruments"],
                    batch.y_instruments
                )
                valve_loss = self.valve_criterion(
                    predictions["valves"],
                    batch.y_valves
                )
                position_loss = self.position_criterion(
                    predictions["positions"],
                    batch.y_positions
                )
                
                total_instrument_loss += instrument_loss.item()
                total_valve_loss += valve_loss.item()
                total_position_loss += position_loss.item()
        
        num_batches = len(val_loader)
        return {
            "instrument_loss": total_instrument_loss / num_batches,
            "valve_loss": total_valve_loss / num_batches,
            "position_loss": total_position_loss / num_batches,
            "total_loss": (total_instrument_loss + total_valve_loss + 0.1 * total_position_loss) / num_batches
        }
    
    def train(
        self,
        train_loader: DataLoader,
        val_loader: DataLoader,
        epochs: int = 100,
        early_stopping_patience: int = 15
    ):
        """
        Full training loop
        
        Args:
            train_loader: Training data loader
            val_loader: Validation data loader
            epochs: Number of epochs
            early_stopping_patience: Patience for early stopping
        """
        logger.info(f"Starting GNN training for {epochs} epochs...")
        
        best_val_loss = float('inf')
        patience_counter = 0
        
        for epoch in range(epochs):
            # Train
            train_loss = self.train_epoch(train_loader)
            
            # Validate
            val_metrics = self.validate(val_loader)
            val_loss = val_metrics["total_loss"]
            
            logger.info(f"Epoch {epoch+1}/{epochs}: "
                       f"Train Loss: {train_loss:.4f}, "
                       f"Val Loss: {val_loss:.4f}, "
                       f"Instrument: {val_metrics['instrument_loss']:.4f}, "
                       f"Valve: {val_metrics['valve_loss']:.4f}, "
                       f"Position: {val_metrics['position_loss']:.4f}")
            
            # Early stopping
            if val_loss < best_val_loss:
                best_val_loss = val_loss
                patience_counter = 0
                self.save_model("best_model.pth")
            else:
                patience_counter += 1
                
                if patience_counter >= early_stopping_patience:
                    logger.info(f"Early stopping at epoch {epoch+1}")
                    break
        
        logger.info(f"✅ Training complete. Best val loss: {best_val_loss:.4f}")
    
    def save_model(self, path: str):
        """Save model checkpoint"""
        torch.save({
            'model_state_dict': self.model.state_dict(),
            'optimizer_state_dict': self.optimizer.state_dict(),
        }, path)
        logger.info(f"Model saved to {path}")
    
    def load_model(self, path: str):
        """Load model checkpoint"""
        checkpoint = torch.load(path, map_location=self.device)
        self.model.load_state_dict(checkpoint['model_state_dict'])
        self.optimizer.load_state_dict(checkpoint['optimizer_state_dict'])
        logger.info(f"Model loaded from {path}")


class GNNInference:
    """Inference engine for trained GNN"""
    
    def __init__(self, model_path: str):
        """
        Initialize inference engine
        
        Args:
            model_path: Path to trained model
        """
        config = get_model_config("process_flow_gnn")
        self.model = ProcessFlowGNN(config.parameters)
        self.device = config.parameters.get("device", "cuda" if torch.cuda.is_available() else "cpu")
        
        # Load trained weights
        checkpoint = torch.load(model_path, map_location=self.device)
        self.model.load_state_dict(checkpoint['model_state_dict'])
        self.model.to(self.device)
        self.model.eval()
        
        logger.info(f"GNN model loaded for inference from {model_path}")
    
    def predict(self, process_graph: Dict) -> Dict:
        """
        Predict required P&ID elements from PFD process graph
        
        Args:
            process_graph: Process graph with equipment nodes and stream edges
            
        Returns:
            Predictions for instruments, valves, and positions
        """
        # Convert process graph to PyTorch Geometric Data
        data = self._graph_to_data(process_graph)
        data = data.to(self.device)
        
        # Predict
        with torch.no_grad():
            predictions = self.model(data)
        
        # Convert predictions to readable format
        results = self._parse_predictions(predictions, process_graph)
        
        return results
    
    def _graph_to_data(self, process_graph: Dict) -> Data:
        """Convert process graph to PyTorch Geometric Data"""
        # Extract nodes and edges
        nodes = process_graph.get("nodes", {})
        edges = process_graph.get("edges", [])
        
        # Create node feature matrix (equipment type IDs)
        node_list = list(nodes.keys())
        node_features = []
        for node_id in node_list:
            node_data = nodes[node_id]
            eq_type = node_data.get("type", "equipment")
            type_id = self._equipment_type_to_id(eq_type)
            node_features.append(type_id)
        
        x = torch.tensor(node_features, dtype=torch.long)
        
        # Create edge index and edge features
        edge_index = []
        edge_features = []
        node_to_idx = {node: idx for idx, node in enumerate(node_list)}
        
        for edge in edges:
            from_node = edge.get("from")
            to_node = edge.get("to")
            
            if from_node in node_to_idx and to_node in node_to_idx:
                from_idx = node_to_idx[from_node]
                to_idx = node_to_idx[to_node]
                
                edge_index.append([from_idx, to_idx])
                
                # Edge feature (stream type)
                stream_type = edge.get("phase", "liquid")
                type_id = self._stream_type_to_id(stream_type)
                edge_features.append(type_id)
        
        edge_index = torch.tensor(edge_index, dtype=torch.long).t().contiguous()
        edge_attr = torch.tensor(edge_features, dtype=torch.long)
        
        return Data(x=x, edge_index=edge_index, edge_attr=edge_attr)
    
    def _equipment_type_to_id(self, eq_type: str) -> int:
        """Map equipment type to ID"""
        type_map = {
            "vessel": 0, "pump": 1, "compressor": 2, "heat_exchanger": 3,
            "tank": 4, "turbine": 5, "mixer": 6, "separator": 7,
            # Add more mappings...
        }
        return type_map.get(eq_type.lower(), 0)
    
    def _stream_type_to_id(self, stream_type: str) -> int:
        """Map stream type to ID"""
        type_map = {
            "liquid": 0, "gas": 1, "steam": 2, "two_phase": 3,
            # Add more mappings...
        }
        return type_map.get(stream_type.lower(), 0)
    
    def _parse_predictions(self, predictions: Dict, process_graph: Dict) -> Dict:
        """Parse model predictions into readable format"""
        # Apply sigmoid to get probabilities
        instrument_probs = torch.sigmoid(predictions["instruments"]).cpu().numpy()
        valve_probs = torch.sigmoid(predictions["valves"]).cpu().numpy()
        positions = predictions["positions"].cpu().numpy()
        
        # Extract high-confidence predictions
        results = {
            "required_instruments": [],
            "required_valves": [],
            "optimal_positions": {}
        }
        
        node_list = list(process_graph.get("nodes", {}).keys())
        
        for idx, node_id in enumerate(node_list):
            # Instruments (threshold at 0.5)
            inst_indices = np.where(instrument_probs[idx] > 0.5)[0]
            for inst_idx in inst_indices:
                inst_type = self._id_to_instrument_type(inst_idx)
                results["required_instruments"].append({
                    "equipment": node_id,
                    "type": inst_type,
                    "confidence": float(instrument_probs[idx][inst_idx])
                })
            
            # Valves
            valve_indices = np.where(valve_probs[idx] > 0.5)[0]
            for valve_idx in valve_indices:
                valve_type = self._id_to_valve_type(valve_idx)
                results["required_valves"].append({
                    "equipment": node_id,
                    "type": valve_type,
                    "confidence": float(valve_probs[idx][valve_idx])
                })
            
            # Positions
            results["optimal_positions"][node_id] = {
                "x": float(positions[idx][0]),
                "y": float(positions[idx][1])
            }
        
        return results
    
    def _id_to_instrument_type(self, inst_id: int) -> str:
        """Map instrument ID to type"""
        types = ["FT", "PT", "TT", "LT", "AT", "FIC", "PIC", "TIC", "LIC"]
        return types[inst_id] if inst_id < len(types) else f"INST_{inst_id}"
    
    def _id_to_valve_type(self, valve_id: int) -> str:
        """Map valve ID to type"""
        types = ["gate", "globe", "ball", "butterfly", "check", "control", "relief"]
        return types[valve_id] if valve_id < len(types) else f"VALVE_{valve_id}"


# Convenience function
def predict_pid_requirements(process_graph: Dict, model_path: str = "./models/process_flow_gnn_v1.pth") -> Dict:
    """
    Quick prediction function
    
    Args:
        process_graph: Process graph from PFD
        model_path: Path to trained model
        
    Returns:
        Predicted P&ID requirements
    """
    inference = GNNInference(model_path)
    return inference.predict(process_graph)
