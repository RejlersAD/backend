"""
Auto-Training System for Graph Neural Network (GNN)
====================================================

Automatically extracts PFD→P&ID conversion pairs from database
and trains GNN to predict required instruments and optimal positions.
"""

import os
import logging
from typing import List, Dict
import boto3
import torch
from torch_geometric.data import Data, DataLoader
from decouple import config
import django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

logger = logging.getLogger(__name__)


class AutoGNNTrainer:
    """
    Automatically trains GNN using database conversion pairs
    """
    
    def __init__(self, s3_bucket: str, cache_dir: str):
        self.s3_bucket = s3_bucket
        self.cache_dir = cache_dir
        os.makedirs(cache_dir, exist_ok=True)
    
    def extract_pfd_pid_pairs_from_db(self, min_count: int = 1000) -> List[Dict]:
        """
        Extract PFD→P&ID conversion pairs from database
        """
        from apps.pfd_converter.models import PFDDocument, PIDConversion
        
        logger.info("🔍 Extracting PFD→P&ID pairs from database...")
        
        pairs = []
        
        # Get all successful conversions
        conversions = PIDConversion.objects.filter(
            status='completed'
        ).select_related('pfd_document')
        
        logger.info(f"   Found {conversions.count()} completed conversions")
        
        for conversion in conversions:
            try:
                pfd_doc = conversion.pfd_document
                
                # Extract PFD data
                pfd_data = {
                    'equipment': pfd_doc.extracted_data.get('equipment', []) if pfd_doc.extracted_data else [],
                    'streams': pfd_doc.extracted_data.get('process_streams', []) if pfd_doc.extracted_data else []
                }
                
                # Extract P&ID data
                pid_data = {
                    'equipment': conversion.pid_specifications.get('equipment_list', []) if conversion.pid_specifications else [],
                    'instruments': conversion.pid_specifications.get('instrument_list', []) if conversion.pid_specifications else []
                }
                
                if pfd_data['equipment'] and pid_data['instruments']:
                    pairs.append({
                        'pfd': pfd_data,
                        'pid': pid_data,
                        'project': pfd_doc.project_code
                    })
                
            except Exception as e:
                logger.warning(f"  ⚠️  Failed to extract pair: {e}")
                continue
        
        logger.info(f"✅ Extracted {len(pairs)} valid PFD→P&ID pairs")
        
        if len(pairs) < min_count:
            logger.warning(f"⚠️  Only {len(pairs)} pairs (minimum: {min_count})")
            logger.info("💡 Tip: Generate more P&IDs to increase training data")
        
        return pairs
    
    def convert_to_graphs(self, pairs: List[Dict]) -> Dict:
        """
        Convert PFD→P&ID pairs to graph format for GNN training
        """
        logger.info(f"📊 Converting {len(pairs)} pairs to graph format...")
        
        train_graphs = []
        val_graphs = []
        
        for i, pair in enumerate(pairs):
            try:
                graph = self._create_graph_from_pair(pair)
                
                # 80/20 train/val split
                if i < len(pairs) * 0.8:
                    train_graphs.append(graph)
                else:
                    val_graphs.append(graph)
                    
            except Exception as e:
                logger.error(f"  ❌ Failed to create graph: {e}")
                continue
        
        # Create DataLoaders
        train_loader = DataLoader(train_graphs, batch_size=32, shuffle=True)
        val_loader = DataLoader(val_graphs, batch_size=32, shuffle=False)
        
        logger.info(f"✅ Created {len(train_graphs)} training graphs, {len(val_graphs)} validation graphs")
        
        return {
            'train': train_loader,
            'val': val_loader
        }
    
    def _create_graph_from_pair(self, pair: Dict) -> Data:
        """
        Create PyTorch Geometric graph from PFD→P&ID pair
        """
        pfd = pair['pfd']
        pid = pair['pid']
        
        # Create nodes (equipment from PFD)
        nodes = []
        node_features = []
        
        for equip in pfd['equipment']:
            nodes.append(equip.get('tag', ''))
            # Node features: [type_id, capacity, pressure, temperature]
            node_features.append([
                self._encode_equipment_type(equip.get('type', '')),
                float(equip.get('capacity', 0)),
                float(equip.get('design_pressure', 0)),
                float(equip.get('design_temperature', 0))
            ])
        
        # Create edges (streams connecting equipment)
        edges = []
        for stream in pfd['streams']:
            src = stream.get('source', '')
            dst = stream.get('destination', '')
            if src in nodes and dst in nodes:
                src_idx = nodes.index(src)
                dst_idx = nodes.index(dst)
                edges.append([src_idx, dst_idx])
        
        # Target: instruments to add (from P&ID)
        target_instruments = []
        for inst in pid['instruments']:
            target_instruments.append([
                self._encode_instrument_type(inst.get('type', '')),
                float(inst.get('position', {}).get('x', 0.5)),
                float(inst.get('position', {}).get('y', 0.5))
            ])
        
        # Convert to tensors
        x = torch.tensor(node_features, dtype=torch.float)
        edge_index = torch.tensor(edges, dtype=torch.long).t().contiguous() if edges else torch.empty((2, 0), dtype=torch.long)
        y = torch.tensor(target_instruments, dtype=torch.float) if target_instruments else torch.zeros((1, 3))
        
        return Data(x=x, edge_index=edge_index, y=y)
    
    def _encode_equipment_type(self, type_str: str) -> int:
        """Encode equipment type to integer"""
        types = ['vessel', 'tank', 'pump', 'heat_exchanger', 'compressor', 'separator', 'filter']
        return types.index(type_str) if type_str in types else 0
    
    def _encode_instrument_type(self, type_str: str) -> int:
        """Encode instrument type to integer"""
        types = ['flow_transmitter', 'pressure_transmitter', 'temperature_transmitter', 'level_transmitter',
                 'control_valve', 'safety_valve']
        return types.index(type_str) if type_str in types else 0
