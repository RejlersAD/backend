"""
AI-Powered CRS Revision Analysis Service
Provides intelligent insights, predictions, and recommendations for revision management
"""

import re
import hashlib
from typing import List, Dict, Tuple, Optional
from datetime import datetime, timedelta
from django.db.models import Q, Count, Avg
from django.utils import timezone
import logging

logger = logging.getLogger(__name__)


class CRSRevisionAIService:
    """
    AI service for analyzing CRS revisions, detecting patterns, and providing insights
    Uses both rule-based logic and AI models (extensible to GPT-4/Claude)
    """
    
    @staticmethod
    def calculate_text_similarity(text1: str, text2: str) -> float:
        """
        Calculate similarity between two text strings
        Uses Levenshtein distance ratio (can be replaced with embeddings)
        Returns: similarity score 0-100
        """
        if not text1 or not text2:
            return 0.0
        
        # Normalize texts
        text1 = text1.lower().strip()
        text2 = text2.lower().strip()
        
        if text1 == text2:
            return 100.0
        
        # Simple word-based similarity (can be enhanced with NLP)
        words1 = set(text1.split())
        words2 = set(text2.split())
        
        if not words1 or not words2:
            return 0.0
        
        intersection = words1.intersection(words2)
        union = words1.union(words2)
        
        jaccard_similarity = len(intersection) / len(union) if union else 0.0
        
        # Check for substring matches (one comment might be expansion of another)
        if text1 in text2 or text2 in text1:
            jaccard_similarity = max(jaccard_similarity, 0.7)
        
        return round(jaccard_similarity * 100, 2)
    
    @staticmethod
    def detect_comment_links(source_comments, target_comments, threshold: float = 60.0) -> List[Dict]:
        """
        Detect potential links between comments from different revisions
        Returns list of potential links with similarity scores
        """
        links = []
        
        for source_comment in source_comments:
            for target_comment in target_comments:
                similarity = CRSRevisionAIService.calculate_text_similarity(
                    source_comment.comment_text,
                    target_comment.comment_text
                )
                
                if similarity >= threshold:
                    link_type = 'identical' if similarity > 95 else 'modified' if similarity > 80 else 'related'
                    
                    links.append({
                        'source_comment_id': source_comment.id,
                        'target_comment_id': target_comment.id,
                        'similarity_score': similarity,
                        'link_type': link_type,
                        'ai_confidence': similarity,
                    })
        
        return sorted(links, key=lambda x: x['similarity_score'], reverse=True)
    
    @staticmethod
    def calculate_risk_score(chain) -> float:
        """
        Calculate comprehensive risk score for a revision chain
        Considers: revision count, unresolved comments, time factors
        Returns: risk score 0-100
        """
        risk_factors = []
        
        # Factor 1: Revision count proximity to limit
        if chain.max_allowed_revisions > 0:
            revision_factor = (chain.current_revision_number / chain.max_allowed_revisions) * 40
            risk_factors.append(revision_factor)
        
        # Factor 2: Unresolved comments across all revisions
        revisions = chain.revisions.all()
        if revisions.exists():
            total_comments = sum(r.total_comments for r in revisions)
            total_resolved = sum(r.total_resolved_comments for r in revisions)
            
            if total_comments > 0:
                unresolved_ratio = (total_comments - total_resolved) / total_comments
                risk_factors.append(unresolved_ratio * 30)
        
        # Factor 3: Time since last revision (stagnation risk)
        if revisions.exists():
            latest_revision = revisions.order_by('-revision_number').first()
            if latest_revision.updated_at:
                days_since_update = (timezone.now() - latest_revision.updated_at).days
                if days_since_update > 30:
                    time_factor = min((days_since_update / 30) * 15, 20)
                    risk_factors.append(time_factor)
        
        # Factor 4: Carryover comment ratio (comments that keep coming back)
        if revisions.count() > 1:
            recent_revisions = revisions.order_by('-revision_number')[:3]
            avg_carryover = sum(r.total_carryover_comments for r in recent_revisions) / len(recent_revisions)
            avg_total = sum(r.total_comments for r in recent_revisions) / len(recent_revisions)
            
            if avg_total > 0:
                carryover_ratio = avg_carryover / avg_total
                risk_factors.append(carryover_ratio * 10)
        
        total_risk = sum(risk_factors)
        return round(min(total_risk, 100), 2)
    
    @staticmethod
    def determine_risk_level(risk_score: float) -> str:
        """Convert numeric risk score to risk level category"""
        if risk_score >= 75:
            return 'critical'
        elif risk_score >= 50:
            return 'high'
        elif risk_score >= 25:
            return 'medium'
        else:
            return 'low'
    
    @staticmethod
    def generate_risk_recommendation(chain) -> str:
        """Generate AI recommendation based on risk analysis"""
        risk_score = chain.ai_risk_score or 0
        current_rev = chain.current_revision_number
        max_rev = chain.max_allowed_revisions
        
        recommendations = []
        
        if current_rev >= max_rev:
            recommendations.append(
                f"⚠️ CRITICAL: Maximum revisions reached ({current_rev}/{max_rev}). "
                "Immediate action required to prevent project rejection."
            )
        elif current_rev >= max_rev - 1:
            recommendations.append(
                f"🔴 HIGH PRIORITY: Only {max_rev - current_rev} revision(s) remaining. "
                "Recommend comprehensive review before next submission."
            )
        
        # Analyze revision history
        revisions = chain.revisions.all().order_by('revision_number')
        if revisions.count() >= 2:
            # Check for increasing comment trend
            comment_counts = [r.total_new_comments for r in revisions]
            if len(comment_counts) >= 2 and comment_counts[-1] > comment_counts[-2]:
                recommendations.append(
                    "📈 TREND ALERT: New comments increased in latest revision. "
                    "Consider identifying root cause areas."
                )
        
        if risk_score >= 75:
            recommendations.append(
                "🎯 RECOMMENDED ACTIONS:\n"
                "  • Schedule urgent stakeholder meeting\n"
                "  • Prioritize critical/high-priority comments\n"
                "  • Consider parallel resolution teams\n"
                "  • Implement daily progress tracking"
            )
        elif risk_score >= 50:
            recommendations.append(
                "📋 SUGGESTED ACTIONS:\n"
                "  • Review unresolved comment patterns\n"
                "  • Allocate additional resources if needed\n"
                "  • Set aggressive resolution deadlines\n"
                "  • Weekly progress reviews"
            )
        elif risk_score >= 25:
            recommendations.append(
                "✅ MAINTAIN MOMENTUM:\n"
                "  • Continue current resolution pace\n"
                "  • Monitor for new comment trends\n"
                "  • Regular status updates"
            )
        else:
            recommendations.append(
                "🌟 ON TRACK: Project progressing well. "
                "Maintain current processes and communication."
            )
        
        return "\n\n".join(recommendations) if recommendations else "No specific recommendations at this time."
    
    @staticmethod
    def predict_completion_date(chain) -> Optional[datetime]:
        """
        Predict likely completion date based on historical resolution rates
        Returns predicted date or None if insufficient data
        """
        revisions = chain.revisions.all().order_by('revision_number')
        
        if revisions.count() < 2:
            return None
        
        # Calculate average time between revisions
        time_deltas = []
        for i in range(len(revisions) - 1):
            if revisions[i].completed_date and revisions[i + 1].submitted_date:
                delta = revisions[i + 1].submitted_date - revisions[i].completed_date
                time_deltas.append(delta.days)
        
        if not time_deltas:
            return None
        
        avg_days_per_revision = sum(time_deltas) / len(time_deltas)
        
        # Calculate remaining revisions needed
        latest_revision = revisions.last()
        if latest_revision.total_comments > 0:
            resolution_rate = latest_revision.total_resolved_comments / latest_revision.total_comments
            if resolution_rate < 1.0:
                # Estimate revisions needed to complete
                estimated_remaining_revisions = max(1, int((1 - resolution_rate) * 3))
            else:
                estimated_remaining_revisions = 1
        else:
            estimated_remaining_revisions = 1
        
        # Predict completion date
        predicted_days = int(avg_days_per_revision * estimated_remaining_revisions)
        predicted_date = timezone.now() + timedelta(days=predicted_days)
        
        return predicted_date.date()
    
    @staticmethod
    def analyze_comment_patterns(comments) -> Dict:
        """
        Analyze patterns in comments (frequent keywords, categories, issues)
        Returns dictionary with pattern analysis
        """
        if not comments:
            return {}
        
        # Extract keywords (simplified - can use NLP for better results)
        keyword_freq = {}
        category_freq = {}
        priority_dist = {}
        
        for comment in comments:
            # Keyword extraction (basic)
            text = comment.comment_text.lower()
            words = re.findall(r'\b\w{4,}\b', text)  # Words with 4+ chars
            for word in words:
                keyword_freq[word] = keyword_freq.get(word, 0) + 1
            
            # Category distribution
            category = comment.comment_type
            category_freq[category] = category_freq.get(category, 0) + 1
            
            # Priority distribution
            priority = comment.priority
            priority_dist[priority] = priority_dist.get(priority, 0) + 1
        
        # Sort and get top keywords
        top_keywords = sorted(keyword_freq.items(), key=lambda x: x[1], reverse=True)[:10]
        
        return {
            'total_comments': len(comments),
            'top_keywords': [{'word': k, 'count': v} for k, v in top_keywords],
            'category_distribution': category_freq,
            'priority_distribution': priority_dist,
        }
    
    @staticmethod
    def suggest_response_template(comment) -> str:
        """
        Generate AI-suggested response template based on comment analysis
        This is a simplified version - can be enhanced with GPT-4
        """
        comment_lower = comment.comment_text.lower()
        
        # Rule-based response templates (can be replaced with AI)
        if any(word in comment_lower for word in ['clarify', 'explain', 'unclear']):
            return (
                "Thank you for your comment. We understand your request for clarification. "
                "[Provide detailed explanation here with specific references to relevant sections/drawings]. "
                "Please let us know if additional information is required."
            )
        
        elif any(word in comment_lower for word in ['correct', 'error', 'mistake', 'wrong']):
            return (
                "Thank you for identifying this issue. We acknowledge the [error/discrepancy] and will: "
                "[1. Provide corrected information/drawing] "
                "[2. Update all affected documents] "
                "[3. Verify no similar issues exist]. "
                "Revised [document/drawing] will be submitted in next revision."
            )
        
        elif any(word in comment_lower for word in ['add', 'include', 'missing']):
            return (
                "Thank you for this comment. We will incorporate the requested [information/detail] in the next revision. "
                "Specifically, we will: [List specific actions]. "
                "This will be reflected in [specific section/drawing]."
            )
        
        elif any(word in comment_lower for word in ['comply', 'requirement', 'standard']):
            return (
                "We acknowledge the requirement to comply with [specific standard/code]. "
                "Our approach: [Explain compliance strategy]. "
                "Supporting documentation: [List references]. "
                "Please confirm if this approach is acceptable."
            )
        
        else:
            return (
                "Thank you for your comment. We have reviewed this and our response is as follows: "
                "[Provide specific, technical response addressing the comment]. "
                "[Include any actions taken or to be taken]. "
                "Please advise if this addresses your concern."
            )
    
    @staticmethod
    def calculate_complexity_score(revision) -> float:
        """
        Calculate complexity score for a revision based on various factors
        Returns: complexity score 0-100
        """
        complexity_factors = []
        
        # Factor 1: Number of comments
        total_comments = revision.total_comments
        if total_comments > 50:
            complexity_factors.append(30)
        elif total_comments > 20:
            complexity_factors.append(20)
        elif total_comments > 10:
            complexity_factors.append(10)
        
        # Factor 2: Carryover ratio (persistent issues)
        if total_comments > 0:
            carryover_ratio = revision.total_carryover_comments / total_comments
            complexity_factors.append(carryover_ratio * 25)
        
        # Factor 3: Document complexity from comment analysis
        comments = revision.document.comments.all()
        if comments.exists():
            # High priority comments add complexity
            high_priority_count = comments.filter(priority__in=['high', 'critical']).count()
            if comments.count() > 0:
                high_priority_ratio = high_priority_count / comments.count()
                complexity_factors.append(high_priority_ratio * 25)
            
            # Technical complexity (comments with long text)
            avg_comment_length = sum(len(c.comment_text) for c in comments) / comments.count()
            if avg_comment_length > 200:
                complexity_factors.append(20)
            elif avg_comment_length > 100:
                complexity_factors.append(10)
        
        total_complexity = sum(complexity_factors)
        return round(min(total_complexity, 100), 2)
    
    @staticmethod
    def estimate_resolution_time(revision) -> float:
        """
        Estimate resolution time in hours based on complexity and comment count
        Returns: estimated hours
        """
        base_hours_per_comment = 2.0  # Base assumption
        
        complexity_multiplier = 1.0
        if revision.ai_complexity_score:
            if revision.ai_complexity_score > 75:
                complexity_multiplier = 2.0
            elif revision.ai_complexity_score > 50:
                complexity_multiplier = 1.5
            elif revision.ai_complexity_score > 25:
                complexity_multiplier = 1.2
        
        total_hours = revision.total_comments * base_hours_per_comment * complexity_multiplier
        
        # Add overhead for carryover comments (they're often more complex)
        carryover_overhead = revision.total_carryover_comments * 0.5 * base_hours_per_comment
        
        return round(total_hours + carryover_overhead, 1)
