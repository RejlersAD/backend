"""
AI Cost Recommendation System
Provides intelligent recommendations for cost optimization and usage tracking
"""

import logging
from typing import Dict, List
from django.core.cache import cache
from datetime import datetime, timedelta
from .ai_provider_config import AIProviderConfig

logger = logging.getLogger(__name__)


class CostRecommendationSystem:
    """
    Intelligent cost recommendation and tracking system
    """
    
    @staticmethod
    def get_recommendations(num_pages: int = 100) -> Dict:
        """
        Get cost recommendations for different strategies
        
        Args:
            num_pages: Estimated number of pages to process
        
        Returns:
            Dict with recommendations for each strategy
        """
        config = AIProviderConfig
        
        recommendations = []
        
        # Strategy 1: Local Only (FREE)
        local_cost = config.estimate_cost(num_pages, 'local_ocr')
        recommendations.append({
            'strategy': 'local_only',
            'name': '🆓 Free - Local OCR Only',
            'method': 'PaddleOCR/Tesseract (Local)',
            'cost_total': local_cost['total_cost'],
            'cost_per_page': local_cost['cost_per_page'],
            'quality': 'Medium',
            'speed': 'Fast',
            'pros': [
                '✅ Zero cost',
                '✅ Fast processing',
                '✅ Complete offline operation',
                '✅ No API dependency'
            ],
            'cons': [
                '⚠️ Lower accuracy on complex diagrams',
                '⚠️ Requires manual validation'
            ],
            'recommended_for': [
                'Simple SLDs with clear text',
                'Budget-constrained projects',
                'High-volume processing',
                'Offline environments'
            ],
            'priority': 1
        })
        
        # Strategy 2: Cost Optimized (RECOMMENDED)
        gpt35_cost = config.estimate_cost(num_pages, 'gpt_3.5_turbo')
        recommendations.append({
            'strategy': 'cost_optimized',
            'name': '⭐ Recommended - Hybrid Low Cost',
            'method': 'PaddleOCR + GPT-3.5-turbo',
            'cost_total': gpt35_cost['total_cost'],
            'cost_per_page': gpt35_cost['cost_per_page'],
            'quality': 'High',
            'speed': 'Medium',
            'pros': [
                '✅ Excellent accuracy',
                '✅ Very low cost (~$0.50 per 1000 pages)',
                '✅ Best cost/quality balance',
                '✅ Handles complex diagrams well'
            ],
            'cons': [
                '⚠️ Requires OpenAI API key',
                '⚠️ Slight network latency'
            ],
            'recommended_for': [
                'Most production use cases',
                'Cost-conscious teams',
                'Medium to complex SLDs',
                'Automated workflows'
            ],
            'priority': 1,
            'badge': 'RECOMMENDED'
        })
        
        # Strategy 3: Quality First (EXPENSIVE)
        gpt4o_cost = config.estimate_cost(num_pages, 'gpt_4o_vision')
        recommendations.append({
            'strategy': 'quality_first',
            'name': '💎 Premium - Vision AI',
            'method': 'GPT-4o Vision (High Quality)',
            'cost_total': gpt4o_cost['total_cost'],
            'cost_per_page': gpt4o_cost['cost_per_page'],
            'quality': 'Very High',
            'speed': 'Slow',
            'pros': [
                '✅ Highest accuracy',
                '✅ Best for complex/poor quality SLDs',
                '✅ Direct image understanding'
            ],
            'cons': [
                '❌ High cost (~$75 per 1000 pages)',
                '❌ 150x more expensive than hybrid',
                '❌ Slower processing'
            ],
            'recommended_for': [
                'Critical/safety-critical projects only',
                'Very poor quality scanned documents',
                'When budget is not a constraint'
            ],
            'priority': 3,
            'badge': 'EXPENSIVE'
        })
        
        # Add comparison
        savings_vs_vision = ((gpt4o_cost['total_cost'] - gpt35_cost['total_cost']) / gpt4o_cost['total_cost'] * 100) if gpt4o_cost['total_cost'] > 0 else 0
        
        return {
            'for_pages': num_pages,
            'recommendations': recommendations,
            'comparison': {
                'cheapest': 'local_only',
                'recommended': 'cost_optimized',
                'highest_quality': 'quality_first',
                'savings_hybrid_vs_vision': f"{savings_vs_vision:.1f}%",
                'savings_amount': gpt4o_cost['total_cost'] - gpt35_cost['total_cost']
            },
            'current_strategy': config.get_active_strategy()['name']
        }
    
    @staticmethod
    def estimate_project_cost(num_drawings: int, pages_per_drawing: int = 5) -> Dict:
        """
        Estimate cost for an entire project
        
        Args:
            num_drawings: Number of SLD drawings
            pages_per_drawing: Average pages per drawing
        
        Returns:
            Dict with project cost breakdown
        """
        total_pages = num_drawings * pages_per_drawing
        config = AIProviderConfig
        
        # Get costs for each strategy
        local_cost = config.estimate_cost(total_pages, 'local_ocr')
        hybrid_cost = config.estimate_cost(total_pages, 'gpt_3.5_turbo')
        vision_cost = config.estimate_cost(total_pages, 'gpt_4o_vision')
        
        return {
            'project_scope': {
                'num_drawings': num_drawings,
                'pages_per_drawing': pages_per_drawing,
                'total_pages': total_pages
            },
            'cost_breakdown': {
                'local_only': {
                    'total': local_cost['total_cost'],
                    'per_drawing': local_cost['total_cost'] / num_drawings if num_drawings > 0 else 0,
                    'description': 'Free - Local OCR only'
                },
                'cost_optimized': {
                    'total': hybrid_cost['total_cost'],
                    'per_drawing': hybrid_cost['total_cost'] / num_drawings if num_drawings > 0 else 0,
                    'description': 'Recommended - Hybrid approach'
                },
                'quality_first': {
                    'total': vision_cost['total_cost'],
                    'per_drawing': vision_cost['total_cost'] / num_drawings if num_drawings > 0 else 0,
                    'description': 'Premium - Vision AI'
                }
            },
            'savings_using_hybrid': {
                'amount': vision_cost['total_cost'] - hybrid_cost['total_cost'],
                'percentage': ((vision_cost['total_cost'] - hybrid_cost['total_cost']) / vision_cost['total_cost'] * 100) if vision_cost['total_cost'] > 0 else 0
            },
            'recommendation': 'Use cost_optimized strategy for best cost/quality balance'
        }
    
    @staticmethod
    def track_usage(job_id: str, method: str, pages_processed: int, cost: float):
        """
        Track usage and costs in cache
        
        Args:
            job_id: Unique job identifier
            method: Extraction method used
            pages_processed: Number of pages processed
            cost: Actual cost incurred
        """
        try:
            # Get today's date as key
            today = datetime.now().strftime('%Y-%m-%d')
            cache_key = f'cost_tracking_{today}'
            
            # Get existing tracking data
            tracking_data = cache.get(cache_key, {
                'date': today,
                'total_pages': 0,
                'total_cost': 0.0,
                'jobs': [],
                'by_method': {}
            })
            
            # Update totals
            tracking_data['total_pages'] += pages_processed
            tracking_data['total_cost'] += cost
            
            # Add job record
            tracking_data['jobs'].append({
                'job_id': job_id,
                'method': method,
                'pages': pages_processed,
                'cost': cost,
                'timestamp': datetime.now().isoformat()
            })
            
            # Update method breakdown
            if method not in tracking_data['by_method']:
                tracking_data['by_method'][method] = {
                    'pages': 0,
                    'cost': 0.0,
                    'count': 0
                }
            
            tracking_data['by_method'][method]['pages'] += pages_processed
            tracking_data['by_method'][method]['cost'] += cost
            tracking_data['by_method'][method]['count'] += 1
            
            # Save to cache (24 hours)
            cache.set(cache_key, tracking_data, 86400)
            
            # Check thresholds
            config = AIProviderConfig.COST_TRACKING
            if config['enabled']:
                if tracking_data['total_cost'] > config['warn_threshold']:
                    logger.warning(f"[CostTracking] Daily cost ${tracking_data['total_cost']:.2f} exceeds warning threshold ${config['warn_threshold']}")
                
                if tracking_data['total_cost'] > config['block_threshold']:
                    logger.error(f"[CostTracking] Daily cost ${tracking_data['total_cost']:.2f} exceeds block threshold ${config['block_threshold']}!")
            
        except Exception as e:
            logger.error(f"[CostTracking] Error tracking usage: {e}")
    
    @staticmethod
    def get_daily_usage() -> Dict:
        """
        Get today's usage statistics
        
        Returns:
            Dict with daily usage data
        """
        today = datetime.now().strftime('%Y-%m-%d')
        cache_key = f'cost_tracking_{today}'
        
        tracking_data = cache.get(cache_key, {
            'date': today,
            'total_pages': 0,
            'total_cost': 0.0,
            'jobs': [],
            'by_method': {}
        })
        
        # Add cost thresholds
        config = AIProviderConfig.COST_TRACKING
        tracking_data['thresholds'] = {
            'warn_threshold': config['warn_threshold'],
            'block_threshold': config['block_threshold'],
            'warn_exceeded': tracking_data['total_cost'] > config['warn_threshold'],
            'block_exceeded': tracking_data['total_cost'] > config['block_threshold']
        }
        
        return tracking_data
    
    @staticmethod
    def get_cost_comparison_chart() -> Dict:
        """
        Get data for cost comparison visualization
        """
        sample_sizes = [10, 100, 1000, 5000, 10000]  # pages
        config = AIProviderConfig
        
        data = {
            'page_volumes': sample_sizes,
            'strategies': {}
        }
        
        for pages in sample_sizes:
            local = config.estimate_cost(pages, 'local_ocr')
            hybrid = config.estimate_cost(pages, 'gpt_3.5_turbo')
            vision = config.estimate_cost(pages, 'gpt_4o_vision')
            
            if 'local_only' not in data['strategies']:
                data['strategies']['local_only'] = []
            if 'cost_optimized' not in data['strategies']:
                data['strategies']['cost_optimized'] = []
            if 'quality_first' not in data['strategies']:
                data['strategies']['quality_first'] = []
            
            data['strategies']['local_only'].append(local['total_cost'])
            data['strategies']['cost_optimized'].append(hybrid['total_cost'])
            data['strategies']['quality_first'].append(vision['total_cost'])
        
        return data


# ========================================================================
# USAGE EXAMPLES
# ========================================================================
"""
# Get recommendations
recommendations = CostRecommendationSystem.get_recommendations(num_pages=500)
print(f"Recommended strategy: {recommendations['comparison']['recommended']}")

# Estimate project cost
project = CostRecommendationSystem.estimate_project_cost(num_drawings=50, pages_per_drawing=5)
print(f"Project cost: ${project['cost_breakdown']['cost_optimized']['total']:.2f}")
print(f"Savings: ${project['savings_using_hybrid']['amount']:.2f}")

# Track usage
CostRecommendationSystem.track_usage(
    job_id='test-123',
    method='gpt_3.5_turbo',
    pages_processed=10,
    cost=0.005
)

# Get daily usage
usage = CostRecommendationSystem.get_daily_usage()
print(f"Today's cost: ${usage['total_cost']:.2f}")
"""
