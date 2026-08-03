"""
Sales AI Service
Generative AI and Machine Learning for Sales Intelligence
"""

from typing import Dict, List, Any, Optional
from decimal import Decimal
from datetime import datetime, timedelta
from django.utils import timezone
from django.db.models import Avg, Sum, Count, Q, F
import random
import logging

logger = logging.getLogger(__name__)


class SalesAIService:
    """
    Comprehensive AI service for sales intelligence
    Features: Forecasting, Lead Scoring, Churn Prediction, Recommendations
    """
    
    # ==============================================================================
    # CONFIGURATION
    # ==============================================================================
    
    AI_MODEL_VERSION = "v2.1.0"
    CONFIDENCE_THRESHOLD = 0.75
    
    # Soft-coded scoring weights
    LEAD_SCORE_WEIGHTS = {
        'company_size': 0.25,
        'industry_fit': 0.20,
        'budget_match': 0.25,
        'urgency': 0.15,
        'decision_authority': 0.15,
    }
    
    CHURN_RISK_FACTORS = {
        'days_since_contact': {'threshold': 90, 'weight': 0.30},
        'health_score': {'threshold': 40, 'weight': 0.25},
        'deal_activity': {'threshold': 0, 'weight': 0.20},
        'sentiment': {'threshold': 0.3, 'weight': 0.15},
        'support_tickets': {'threshold': 5, 'weight': 0.10},
    }
    
    # ==============================================================================
    # SALES FORECASTING
    # ==============================================================================
    
    @classmethod
    def generate_sales_forecast(cls, period: str, historical_months: int = 6) -> Dict[str, Any]:
        """
        Generate AI-powered sales forecast
        
        Args:
            period: Forecast period (e.g., "2026-Q2", "2026-03")
            historical_months: Months of historical data to use
        
        Returns:
            Comprehensive forecast with confidence intervals
        """
        from .models import Deal, SalesForecast
        
        logger.info(f"Generating sales forecast for {period}")
        
        # Gather historical data
        end_date = timezone.now().date()
        start_date = end_date - timedelta(days=historical_months * 30)
        
        historical_deals = Deal.objects.filter(
            actual_close_date__gte=start_date,
            stage='closed_won'
        )
        
        # Calculate historical metrics
        historical_revenue = historical_deals.aggregate(
            total=Sum('actual_value')
        )['total'] or Decimal('0')
        
        avg_monthly_revenue = historical_revenue / historical_months if historical_months > 0 else Decimal('0')
        
        # Get current pipeline
        pipeline_deals = Deal.objects.exclude(stage__in=['closed_won', 'closed_lost'])
        
        total_pipeline_value = pipeline_deals.aggregate(
            total=Sum('weighted_value')
        )['total'] or Decimal('0')
        
        # AI Prediction Algorithm (simplified - in production, use ML models)
        # Formula: (Historical Average * 0.3) + (Pipeline Weighted * 0.7)
        predicted_revenue = (avg_monthly_revenue * Decimal('0.3')) + (total_pipeline_value * Decimal('0.7'))
        
        # Confidence calculation
        data_quality_score = min(historical_deals.count() / 20, 1.0)  # More data = higher confidence
        pipeline_quality_score = min(pipeline_deals.count() / 10, 1.0)
        confidence = (data_quality_score * 0.6 + pipeline_quality_score * 0.4)
        
        # Best/Worst case scenarios
        variance = predicted_revenue * Decimal('0.25')  # ±25% variance
        best_case = predicted_revenue + variance
        worst_case = max(predicted_revenue - variance, Decimal('0'))
        
        # Stage breakdown
        deals_by_stage = {}
        for stage in ['qualified', 'proposal', 'negotiation']:
            stage_deals = pipeline_deals.filter(stage=stage)
            stage_value = stage_deals.aggregate(Sum('weighted_value'))['weighted_value__sum'] or Decimal('0')
            deals_by_stage[stage] = float(stage_value)
        
        # Service breakdown
        forecast_by_service = cls._calculate_service_breakdown(pipeline_deals)
        
        # Top contributing deals
        top_deals = list(pipeline_deals.order_by('-weighted_value')[:5].values(
            'deal_code', 'deal_name', 'weighted_value', 'probability'
        ))
        
        forecast_data = {
            'forecast_period': period,
            'forecast_date': timezone.now().date(),
            'predicted_revenue': float(predicted_revenue),
            'confidence_level': round(confidence, 2),
            'best_case': float(best_case),
            'worst_case': float(worst_case),
            'model_version': cls.AI_MODEL_VERSION,
            'training_data_points': historical_deals.count(),
            'features_used': [
                'historical_revenue',
                'pipeline_weighted_value',
                'deal_count',
                'avg_deal_size',
                'win_rate'
            ],
            'forecast_by_stage': deals_by_stage,
            'forecast_by_service': forecast_by_service,
            'top_deals_considered': top_deals,
            'insights': cls._generate_forecast_insights(
                predicted_revenue, 
                avg_monthly_revenue,
                pipeline_deals.count()
            )
        }
        
        return forecast_data
    
    @classmethod
    def _calculate_service_breakdown(cls, deals_queryset) -> Dict[str, float]:
        """Calculate revenue breakdown by service category"""
        breakdown = {}
        for deal in deals_queryset:
            for service in deal.service_categories:
                breakdown[service] = breakdown.get(service, 0) + float(deal.weighted_value)
        return breakdown
    
    @classmethod
    def _generate_forecast_insights(cls, predicted: Decimal, historical_avg: Decimal, deal_count: int) -> List[str]:
        """Generate AI insights about the forecast"""
        insights = []
        
        if predicted > historical_avg * Decimal('1.2'):
            insights.append("📈 Projected growth of 20%+ above historical average - Strong pipeline momentum!")
        elif predicted < historical_avg * Decimal('0.8'):
            insights.append("⚠️ Forecast below historical average - Focus on pipeline building and conversion")
        else:
            insights.append("✅ Stable forecast consistent with historical performance")
        
        if deal_count < 5:
            insights.append("⚡ Limited pipeline - Prioritize lead generation activities")
        elif deal_count > 15:
            insights.append("🎯 Healthy pipeline volume - Focus on deal acceleration and conversion")
        
        return insights
    
    # ==============================================================================
    # LEAD SCORING & QUALIFICATION
    # ==============================================================================
    
    @classmethod
    def calculate_lead_score(cls, deal_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        AI-powered lead scoring
        
        Args:
            deal_data: Dictionary with deal attributes
        
        Returns:
            Score (0-100) with breakdown
        """
        scores = {}
        
        # Company Size Score (based on employee count or revenue)
        company_size = deal_data.get('client_employee_count', 0)
        if company_size > 1000:
            scores['company_size'] = 100
        elif company_size > 500:
            scores['company_size'] = 80
        elif company_size > 100:
            scores['company_size'] = 60
        else:
            scores['company_size'] = 40
        
        # Industry Fit Score (soft-coded target industries)
        target_industries = ['oil_gas', 'petrochemical', 'power_generation']
        industry = deal_data.get('industry_type', '')
        scores['industry_fit'] = 100 if industry in target_industries else 60
        
        # Budget Match Score
        deal_value = deal_data.get('estimated_value', 0)
        if deal_value > 1000000:
            scores['budget_match'] = 100
        elif deal_value > 500000:
            scores['budget_match'] = 80
        elif deal_value > 100000:
            scores['budget_match'] = 60
        else:
            scores['budget_match'] = 40
        
        # Urgency Score (based on expected close date)
        expected_close = deal_data.get('expected_close_date')
        if expected_close:
            days_until_close = (expected_close - timezone.now().date()).days
            if days_until_close < 30:
                scores['urgency'] = 100
            elif days_until_close < 90:
                scores['urgency'] = 75
            else:
                scores['urgency'] = 50
        else:
            scores['urgency'] = 30
        
        # Decision Authority (based on contact role)
        contact_role = deal_data.get('primary_contact_role', '')
        if contact_role == 'decision_maker':
            scores['decision_authority'] = 100
        elif contact_role == 'influencer':
            scores['decision_authority'] = 75
        else:
            scores['decision_authority'] = 50
        
        # Calculate weighted total
        total_score = sum(
            scores[factor] * cls.LEAD_SCORE_WEIGHTS[factor]
            for factor in scores
        )
        
        # Determine grade
        if total_score >= 80:
            grade = 'A - Hot Lead'
        elif total_score >= 60:
            grade = 'B - Warm Lead'
        elif total_score >= 40:
            grade = 'C - Cold Lead'
        else:
            grade = 'D - Low Priority'
        
        return {
            'total_score': round(total_score, 1),
            'grade': grade,
            'score_breakdown': scores,
            'recommendations': cls._generate_lead_recommendations(total_score, scores)
        }
    
    @classmethod
    def _generate_lead_recommendations(cls, total_score: float, scores: Dict) -> List[str]:
        """Generate actionable recommendations based on lead score"""
        recommendations = []
        
        if total_score >= 80:
            recommendations.append("🔥 Priority lead! Schedule demo within 48 hours")
            recommendations.append("💼 Involve senior sales leadership")
        elif total_score >= 60:
            recommendations.append("📞 Schedule discovery call this week")
            recommendations.append("📧 Send personalized case study")
        else:
            recommendations.append("🎯 Add to nurture campaign")
            recommendations.append("📚 Share educational content")
        
        # Specific weak areas
        if scores.get('budget_match', 0) < 60:
            recommendations.append("💰 Discuss flexible pricing options")
        if scores.get('urgency', 0) < 60:
            recommendations.append("⏰ Create sense of urgency with limited-time offers")
        
        return recommendations
    
    # ==============================================================================
    # CHURN PREDICTION
    # ==============================================================================
    
    @classmethod
    def predict_churn_risk(cls, client) -> Dict[str, Any]:
        """
        Predict client churn risk using AI
        
        Args:
            client: Client model instance
        
        Returns:
            Churn prediction with risk factors
        """
        risk_factors = {}
        risk_score = 0
        
        # Factor 1: Days since last contact
        if client.last_contact_date:
            days_since_contact = (timezone.now().date() - client.last_contact_date).days
            factor_config = cls.CHURN_RISK_FACTORS['days_since_contact']
            if days_since_contact > factor_config['threshold']:
                risk_factors['inactivity'] = {
                    'value': days_since_contact,
                    'threshold': factor_config['threshold'],
                    'severity': 'high' if days_since_contact > 180 else 'medium'
                }
                risk_score += factor_config['weight'] * min(days_since_contact / 180, 1)
        
        # Factor 2: Health score
        factor_config = cls.CHURN_RISK_FACTORS['health_score']
        if client.health_score < factor_config['threshold']:
            risk_factors['low_health_score'] = {
                'value': client.health_score,
                'threshold': factor_config['threshold'],
                'severity': 'high' if client.health_score < 30 else 'medium'
            }
            risk_score += factor_config['weight'] * (1 - client.health_score / 100)
        
        # Factor 3: Deal activity
        from .models import Deal
        active_deals = Deal.objects.filter(
            client=client,
            stage__in=['qualified', 'proposal', 'negotiation']
        ).count()
        
        factor_config = cls.CHURN_RISK_FACTORS['deal_activity']
        if active_deals == 0:
            risk_factors['no_active_deals'] = {
                'value': 0,
                'threshold': 1,
                'severity': 'high'
            }
            risk_score += factor_config['weight']
        
        # Normalize risk score to 0-100
        risk_score = min(risk_score * 100, 100)
        
        # Determine risk level
        if risk_score > 70:
            risk_level = 'high'
            priority = 'critical'
        elif risk_score > 40:
            risk_level = 'medium'
            priority = 'high'
        else:
            risk_level = 'low'
            priority = 'normal'
        
        return {
            'risk_score': round(risk_score, 1),
            'risk_level': risk_level,
            'priority': priority,
            'risk_factors': risk_factors,
            'recommendations': cls._generate_retention_recommendations(risk_level, risk_factors),
            'confidence': 0.85,
            'model_version': cls.AI_MODEL_VERSION
        }
    
    @classmethod
    def _generate_retention_recommendations(cls, risk_level: str, risk_factors: Dict) -> List[str]:
        """Generate retention strategies"""
        recommendations = []
        
        if risk_level == 'high':
            recommendations.append("🚨 URGENT: Schedule executive-level check-in call this week")
            recommendations.append("🎁 Consider offering loyalty discount or exclusive benefits")
            recommendations.append("📊 Conduct satisfaction survey to identify pain points")
        
        if 'inactivity' in risk_factors:
            recommendations.append("📞 Re-engage with personalized outreach")
            recommendations.append("💡 Share new product features or success stories")
        
        if 'low_health_score' in risk_factors:
            recommendations.append("🔍 Review account performance and address issues")
            recommendations.append("🤝 Assign dedicated success manager")
        
        if 'no_active_deals' in risk_factors:
            recommendations.append("🎯 Identify new opportunities or upsell potential")
            recommendations.append("📅 Schedule quarterly business review")
        
        return recommendations
    
    # ==============================================================================
    # DEAL WIN PROBABILITY
    # ==============================================================================
    
    @classmethod
    def calculate_win_probability(cls, deal) -> Dict[str, Any]:
        """
        AI-calculated win probability (beyond stage-based probability)
        
        Args:
            deal: Deal model instance
        
        Returns:
            Win probability with factors
        """
        from .models import DEAL_STAGES
        
        # Start with stage-based probability
        base_probability = DEAL_STAGES.get(deal.stage, {}).get('probability', 50)
        
        # Adjustment factors
        adjustments = {}
        final_probability = base_probability
        
        # Factor 1: Deal age (older deals less likely to close)
        days_in_pipeline = (timezone.now().date() - deal.created_at.date()).days
        if days_in_pipeline > 180:
            adjustments['age_penalty'] = -20
            final_probability -= 20
        elif days_in_pipeline > 90:
            adjustments['age_penalty'] = -10
            final_probability -= 10
        
        # Factor 2: Client health score
        if deal.client.health_score > 70:
            adjustments['client_health_boost'] = +10
            final_probability += 10
        elif deal.client.health_score < 40:
            adjustments['client_health_penalty'] = -15
            final_probability -= 15
        
        # Factor 3: Recent activity
        recent_activities = deal.activities.filter(
            activity_date__gte=timezone.now() - timedelta(days=14)
        ).count()
        
        if recent_activities > 3:
            adjustments['activity_boost'] = +15
            final_probability += 15
        elif recent_activities == 0:
            adjustments['inactivity_penalty'] = -20
            final_probability -= 20
        
        # Factor 4: Quote status
        recent_quote = deal.quotes.filter(status='sent').order_by('-sent_date').first()
        if recent_quote:
            days_since_quote = (timezone.now().date() - recent_quote.sent_date.date()).days
            if days_since_quote < 7:
                adjustments['recent_quote_boost'] = +10
                final_probability += 10
        
        # Clamp probability
        final_probability = max(0, min(100, final_probability))
        
        # Confidence based on data availability
        confidence = 0.70 + (len(adjustments) * 0.05)
        
        return {
            'base_probability': base_probability,
            'ai_probability': round(final_probability, 1),
            'adjustments': adjustments,
            'confidence': round(confidence, 2),
            'recommendation': cls._generate_deal_recommendations(final_probability, deal.stage, adjustments)
        }
    
    @classmethod
    def _generate_deal_recommendations(cls, probability: float, stage: str, adjustments: Dict) -> List[str]:
        """Generate recommendations to improve win probability"""
        recommendations = []
        
        if probability > 75:
            recommendations.append("✅ Strong deal! Focus on closing tactics")
            recommendations.append("📝 Prepare contract and legal review")
        elif probability > 50:
            recommendations.append("🎯 Address objections and competitive threats")
            recommendations.append("💼 Bring in executive sponsor")
        else:
            recommendations.append("⚠️ Deal at risk - reassess qualification")
            recommendations.append("🔄 Consider re-positioning or walking away")
        
        # Specific adjustments
        if 'inactivity_penalty' in adjustments:
            recommendations.append("📞 URGENT: Re-engage immediately with multi-threaded outreach")
        
        if 'age_penalty' in adjustments:
            recommendations.append("⏰ Deal aging - create urgency or close out")
        
        return recommendations
    
    # ==============================================================================
    # NEXT BEST ACTION
    # ==============================================================================
    
    @classmethod
    def recommend_next_action(cls, deal) -> Dict[str, Any]:
        """
        AI-powered next best action recommendation
        
        Args:
            deal: Deal model instance
        
        Returns:
            Recommended action with reasoning
        """
        from .models import DEAL_STAGES
        
        stage = deal.stage
        days_in_stage = (timezone.now().date() - deal.updated_at.date()).days
        
        # Get last activity
        last_activity = deal.activities.order_by('-activity_date').first()
        days_since_activity = (timezone.now() - last_activity.activity_date).days if last_activity else 999
        
        actions = []
        
        # Stage-specific recommendations
        if stage == 'lead':
            actions.append({
                'action': 'Schedule Discovery Call',
                'priority': 'high',
                'reason': 'Qualify lead and understand requirements',
                'timeline': 'Within 48 hours',
                'owner': 'Sales Rep'
            })
        
        elif stage == 'qualified':
            if days_since_activity > 7:
                actions.append({
                    'action': 'Follow-up Call',
                    'priority': 'high',
                    'reason': 'No activity in 7+ days',
                    'timeline': 'Today',
                    'owner': 'Sales Rep'
                })
            else:
                actions.append({
                    'action': 'Prepare Proposal',
                    'priority': 'medium',
                    'reason': 'Lead is qualified, move to proposal stage',
                    'timeline': 'Within 5 days',
                    'owner': 'Sales Engineer'
                })
        
        elif stage == 'proposal':
            actions.append({
                'action': 'Proposal Follow-up',
                'priority': 'high',
                'reason': 'Check if client has questions about proposal',
                'timeline': 'Within 3 days of sending',
                'owner': 'Sales Rep'
            })
        
        elif stage == 'negotiation':
            actions.append({
                'action': 'Address Objections',
                'priority': 'critical',
                'reason': 'Deal in negotiation - close gaps',
                'timeline': 'Daily follow-up',
                'owner': 'Sales Manager'
            })
        
        # Time-based urgency
        if days_in_stage > 30:
            actions.insert(0, {
                'action': 'Deal Review',
                'priority': 'critical',
                'reason': f'Deal stuck in {stage} for {days_in_stage} days',
                'timeline': 'Immediate',
                'owner': 'Sales Manager'
            })
        
        # Close date approaching
        days_to_close = (deal.expected_close_date - timezone.now().date()).days
        if 0 < days_to_close < 7:
            actions.insert(0, {
                'action': 'Accelerate Close',
                'priority': 'critical',
                'reason': f'Expected close in {days_to_close} days',
                'timeline': 'Daily',
                'owner': 'Sales Director'
            })
        
        return {
            'primary_action': actions[0] if actions else None,
            'alternative_actions': actions[1:3] if len(actions) > 1 else [],
            'deal_health': cls._assess_deal_health(deal, days_in_stage, days_since_activity),
            'confidence': 0.82
        }
    
    @classmethod
    def _assess_deal_health(cls, deal, days_in_stage: int, days_since_activity: int) -> str:
        """Assess overall deal health"""
        if days_in_stage > 60 or days_since_activity > 14:
            return 'poor'
        elif days_in_stage > 30 or days_since_activity > 7:
            return 'fair'
        else:
            return 'good'
    
    # ==============================================================================
    # GENERATIVE AI - CONTENT GENERATION
    # ==============================================================================
    
    @classmethod
    def generate_email_template(cls, context: str, client_name: str, purpose: str) -> Dict[str, str]:
        """
        Generate personalized email templates using AI patterns
        
        Args:
            context: Deal/client context
            client_name: Client company name
            purpose: Email purpose (follow_up, proposal, etc.)
        
        Returns:
            Subject and body templates
        """
        templates = {
            'follow_up': {
                'subject': f"Following up on our conversation - {client_name}",
                'body': f"""Hi there,

I wanted to follow up on our recent discussion about how we can help {client_name} achieve your engineering and project management goals.

Based on our conversation, I believe our solutions could deliver significant value in:
• [Key benefit 1]
• [Key benefit 2]
• [Key benefit 3]

Would you be available for a brief call this week to discuss next steps?

Best regards,
[Your Name]"""
            },
            'proposal': {
                'subject': f"Proposal for {client_name} - [Project Name]",
                'body': f"""Dear [Client Name],

Thank you for the opportunity to present our proposal for [Project Name]. We're excited about the possibility of partnering with {client_name}.

Our proposal includes:
✅ Comprehensive engineering design services
✅ Project management and coordination
✅ Quality assurance and compliance
✅ Ongoing support and maintenance

I've attached the detailed proposal for your review. I'm confident we can deliver exceptional results within your timeline and budget.

Looking forward to discussing this further!

Best regards,
[Your Name]"""
            },
            'negotiation': {
                'subject': f"Addressing your questions - {client_name}",
                'body': f"""Hi [Client Name],

Thank you for your feedback on our proposal. I appreciate the opportunity to address your questions and concerns.

Regarding [specific concern], here's what we can offer:
[Solution/adjustment]

I'm committed to finding a solution that works for {client_name}. Can we schedule a call to discuss this in detail?

Best regards,
[Your Name]"""
            }
        }
        
        return templates.get(purpose, templates['follow_up'])
    
    @classmethod
    def generate_insights_summary(cls, client) -> List[Dict[str, Any]]:
        """
        Generate AI insights summary for a client
        
        Args:
            client: Client model instance
        
        Returns:
            List of insights with recommendations
        """
        insights = []
        
        # Insight 1: Client Health
        health_insight = {
            'type': 'health_analysis',
            'title': 'Client Health Assessment',
            'score': client.health_score,
            'status': 'healthy' if client.health_score > 70 else 'at_risk',
            'description': f"Client health score is {client.health_score}/100",
            'recommendation': "Continue regular engagement" if client.health_score > 70 else "Increase touchpoints and address concerns"
        }
        insights.append(health_insight)
        
        # Insight 2: Revenue Potential
        from .models import Deal
        pipeline_value = Deal.objects.filter(
            client=client,
            stage__in=['qualified', 'proposal', 'negotiation']
        ).aggregate(Sum('weighted_value'))['weighted_value__sum'] or Decimal('0')
        
        revenue_insight = {
            'type': 'revenue_potential',
            'title': 'Revenue Opportunity',
            'value': float(pipeline_value),
            'description': f"${pipeline_value:,.2f} in active pipeline",
            'recommendation': "Focus on accelerating deals in negotiation stage" if pipeline_value > 100000 else "Identify new opportunities"
        }
        insights.append(revenue_insight)
        
        # Insight 3: Engagement
        from .models import SalesActivity
        recent_activities = SalesActivity.objects.filter(
            client=client,
            activity_date__gte=timezone.now() - timedelta(days=30)
        ).count()
        
        engagement_insight = {
            'type': 'engagement_level',
            'title': 'Engagement Frequency',
            'value': recent_activities,
            'description': f"{recent_activities} activities in last 30 days",
            'status': 'good' if recent_activities > 4 else 'low',
            'recommendation': "Maintain cadence" if recent_activities > 4 else "Increase touchpoints to 2+ per week"
        }
        insights.append(engagement_insight)
        
        return insights
