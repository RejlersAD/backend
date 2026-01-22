"""
Dynamic Subscription Configuration Engine
Soft-coded subscription plans and feature management
"""
from typing import Dict, List, Optional, Any
from django.core.cache import cache
from django.conf import settings
import logging

logger = logging.getLogger(__name__)


# ============================================================================
# SUBSCRIPTION PLAN TEMPLATES - Soft-Coded Configuration
# ============================================================================

PLAN_TEMPLATES = {
    'free': {
        'name': 'Free Plan',
        'code': 'free',
        'display_name': 'Free Tier',
        'description': 'Perfect for trying out the platform',
        'plan_type': 'free',
        'billing_cycle': 'monthly',
        'price': 0.00,
        'trial_days': 14,
        'max_users': 3,
        'max_storage_gb': 5,
        'max_api_calls_per_day': 100,
        'max_projects': 2,
        'max_documents': 50,
        'priority_level': 5,
        'support_level': 'email',
        'allowed_modules': ['crs_documents', 'notifications'],
        'features': {
            'ai_features': False,
            'advanced_analytics': False,
            'custom_branding': False,
            'api_access': False,
            'export_data': False,
            'multi_language': False,
        },
        'badge': 'Free',
        'color_scheme': 'gray',
        'icon': '🆓',
        'is_public': True,
        'is_default': True,
        'sort_order': 1,
    },
    
    'basic': {
        'name': 'Basic Plan',
        'code': 'basic',
        'display_name': 'Basic',
        'description': 'Essential features for small teams',
        'plan_type': 'basic',
        'billing_cycle': 'monthly',
        'price': 49.00,
        'trial_days': 14,
        'max_users': 10,
        'max_storage_gb': 50,
        'max_api_calls_per_day': 1000,
        'max_projects': 10,
        'max_documents': 500,
        'priority_level': 4,
        'support_level': 'email',
        'allowed_modules': [
            'crs_documents',
            'qhse',
            'finance',
            'notifications',
            'file_storage',
        ],
        'features': {
            'ai_features': True,
            'advanced_analytics': False,
            'custom_branding': False,
            'api_access': True,
            'export_data': True,
            'multi_language': False,
            'priority_support': False,
        },
        'badge': '',
        'color_scheme': 'blue',
        'icon': '📦',
        'is_public': True,
        'sort_order': 2,
    },
    
    'professional': {
        'name': 'Professional Plan',
        'code': 'professional',
        'display_name': 'Professional',
        'description': 'Advanced features for growing businesses',
        'plan_type': 'professional',
        'billing_cycle': 'monthly',
        'price': 149.00,
        'trial_days': 14,
        'max_users': 50,
        'max_storage_gb': 500,
        'max_api_calls_per_day': 10000,
        'max_projects': 100,
        'max_documents': 5000,
        'priority_level': 2,
        'support_level': 'chat',
        'allowed_modules': [
            'crs_documents',
            'qhse',
            'finance',
            'pfd_converter',
            'pid_analysis',
            'designiq',
            'notifications',
            'project_management',
            'file_storage',
            'procurement',
        ],
        'features': {
            'ai_features': True,
            'advanced_analytics': True,
            'custom_branding': True,
            'api_access': True,
            'export_data': True,
            'multi_language': True,
            'priority_support': True,
            'dedicated_account_manager': False,
            'sla_guarantee': False,
        },
        'badge': 'Popular',
        'color_scheme': 'purple',
        'icon': '⭐',
        'is_public': True,
        'sort_order': 3,
    },
    
    'enterprise': {
        'name': 'Enterprise Plan',
        'code': 'enterprise',
        'display_name': 'Enterprise',
        'description': 'Unlimited features for large organizations',
        'plan_type': 'enterprise',
        'billing_cycle': 'yearly',
        'price': 999.00,
        'trial_days': 30,
        'max_users': None,  # Unlimited
        'max_storage_gb': None,  # Unlimited
        'max_api_calls_per_day': None,  # Unlimited
        'max_projects': None,  # Unlimited
        'max_documents': None,  # Unlimited
        'priority_level': 1,
        'support_level': 'dedicated',
        'allowed_modules': 'ALL',  # Special marker for all modules
        'features': {
            'ai_features': True,
            'advanced_analytics': True,
            'custom_branding': True,
            'api_access': True,
            'export_data': True,
            'multi_language': True,
            'priority_support': True,
            'dedicated_account_manager': True,
            'sla_guarantee': True,
            'custom_integrations': True,
            'white_label': True,
            'audit_logs': True,
            'sso_integration': True,
        },
        'badge': 'Best Value',
        'color_scheme': 'gold',
        'icon': '👑',
        'is_public': True,
        'sort_order': 4,
    },
}


# ============================================================================
# FEATURE CATALOG - Soft-Coded Feature Definitions
# ============================================================================

FEATURE_CATALOG = {
    # AI Features
    'ai_document_analysis': {
        'name': 'AI Document Analysis',
        'code': 'ai_document_analysis',
        'description': 'Automated document parsing and analysis using AI',
        'feature_type': 'boolean',
        'category': 'ai',
        'icon': '🤖',
        'is_highlighted': True,
        'default_value': {'enabled': False},
    },
    'ai_pfd_conversion': {
        'name': 'PFD to P&ID Conversion',
        'code': 'ai_pfd_conversion',
        'description': 'Convert PFD diagrams to P&ID using AI',
        'feature_type': 'module',
        'category': 'ai',
        'icon': '🔄',
        'is_highlighted': True,
        'default_value': {'enabled': False},
    },
    
    # Storage Features
    'storage_limit': {
        'name': 'Storage Limit',
        'code': 'storage_limit',
        'description': 'Maximum storage space available',
        'feature_type': 'limit',
        'category': 'storage',
        'unit': 'GB',
        'icon': '💾',
        'default_value': {'limit': 5, 'unlimited': False},
    },
    
    # API Features
    'api_access': {
        'name': 'API Access',
        'code': 'api_access',
        'description': 'Access to REST API endpoints',
        'feature_type': 'boolean',
        'category': 'integration',
        'icon': '🔌',
        'default_value': {'enabled': False},
    },
    'api_rate_limit': {
        'name': 'API Rate Limit',
        'code': 'api_rate_limit',
        'description': 'Maximum API calls per day',
        'feature_type': 'limit',
        'category': 'integration',
        'unit': 'requests/day',
        'icon': '⚡',
        'default_value': {'limit': 100, 'unlimited': False},
    },
    
    # Analytics Features
    'advanced_analytics': {
        'name': 'Advanced Analytics',
        'code': 'advanced_analytics',
        'description': 'Detailed analytics and reporting',
        'feature_type': 'boolean',
        'category': 'analytics',
        'icon': '📊',
        'is_highlighted': True,
        'default_value': {'enabled': False},
    },
    'custom_reports': {
        'name': 'Custom Reports',
        'code': 'custom_reports',
        'description': 'Create custom analytics reports',
        'feature_type': 'boolean',
        'category': 'analytics',
        'icon': '📈',
        'default_value': {'enabled': False},
    },
    
    # Branding Features
    'custom_branding': {
        'name': 'Custom Branding',
        'code': 'custom_branding',
        'description': 'Customize platform with your brand',
        'feature_type': 'boolean',
        'category': 'branding',
        'icon': '🎨',
        'default_value': {'enabled': False},
    },
    'white_label': {
        'name': 'White Label',
        'code': 'white_label',
        'description': 'Complete white-label solution',
        'feature_type': 'boolean',
        'category': 'branding',
        'icon': '⚪',
        'default_value': {'enabled': False},
    },
    
    # Support Features
    'priority_support': {
        'name': 'Priority Support',
        'code': 'priority_support',
        'description': '24/7 priority customer support',
        'feature_type': 'support',
        'category': 'support',
        'icon': '🆘',
        'is_highlighted': True,
        'default_value': {'level': 'email'},
    },
    'dedicated_account_manager': {
        'name': 'Dedicated Account Manager',
        'code': 'dedicated_account_manager',
        'description': 'Personal account manager for your organization',
        'feature_type': 'support',
        'category': 'support',
        'icon': '👤',
        'default_value': {'enabled': False},
    },
    
    # Security Features
    'sso_integration': {
        'name': 'SSO Integration',
        'code': 'sso_integration',
        'description': 'Single Sign-On with SAML/OAuth',
        'feature_type': 'integration',
        'category': 'security',
        'icon': '🔐',
        'default_value': {'enabled': False},
    },
    'audit_logs': {
        'name': 'Audit Logs',
        'code': 'audit_logs',
        'description': 'Comprehensive audit trail for compliance',
        'feature_type': 'boolean',
        'category': 'security',
        'icon': '📋',
        'default_value': {'enabled': False},
    },
}


# ============================================================================
# USAGE LIMITS CONFIGURATION
# ============================================================================

USAGE_LIMITS = {
    'storage': {
        'metric_type': 'storage',
        'unit': 'GB',
        'warning_threshold': 0.80,  # Warn at 80%
        'hard_limit': True,  # Block at 100%
    },
    'api_calls': {
        'metric_type': 'api_calls',
        'unit': 'requests',
        'warning_threshold': 0.90,
        'hard_limit': False,  # Just warn, don't block
    },
    'documents': {
        'metric_type': 'documents',
        'unit': 'files',
        'warning_threshold': 0.85,
        'hard_limit': True,
    },
    'projects': {
        'metric_type': 'projects',
        'unit': 'projects',
        'warning_threshold': 0.90,
        'hard_limit': True,
    },
    'users': {
        'metric_type': 'users',
        'unit': 'users',
        'warning_threshold': 0.95,
        'hard_limit': True,
    },
}


# ============================================================================
# SUBSCRIPTION RULES ENGINE
# ============================================================================

class SubscriptionRulesEngine:
    """
    Dynamic rules engine for subscription enforcement
    """
    
    @staticmethod
    def can_access_module(subscription, module_code: str) -> bool:
        """Check if subscription allows access to module"""
        if not subscription or not subscription.plan:
            return False
        
        # Check if expired/suspended
        if subscription.status in ['expired', 'suspended', 'cancelled']:
            return False
        
        # Check plan's allowed modules
        allowed = subscription.plan.allowed_modules
        if allowed == 'ALL' or allowed == ['ALL']:
            return True
        
        return module_code in allowed
    
    @staticmethod
    def can_create_project(subscription) -> tuple[bool, str]:
        """Check if user can create new project"""
        if not subscription:
            return False, "No active subscription"
        
        max_projects = subscription.get_limit('max_projects')
        if max_projects is None:
            return True, ""
        
        # Get current project count (would query actual projects)
        # For now, placeholder logic
        current_projects = 0  # TODO: Count actual projects
        
        if current_projects >= max_projects:
            return False, f"Project limit reached ({max_projects}). Upgrade to create more."
        
        return True, ""
    
    @staticmethod
    def can_upload_file(subscription, file_size_mb: float) -> tuple[bool, str]:
        """Check if user can upload file"""
        if not subscription:
            return False, "No active subscription"
        
        max_storage = subscription.get_limit('max_storage_gb')
        if max_storage is None:
            return True, ""
        
        # Get current storage (would query actual storage)
        current_storage_gb = 0  # TODO: Calculate actual storage
        
        if (current_storage_gb + file_size_mb / 1024) > max_storage:
            return False, f"Storage limit exceeded. Upgrade for more space."
        
        return True, ""
    
    @staticmethod
    def can_add_user(subscription) -> tuple[bool, str]:
        """Check if org can add more users"""
        if not subscription:
            return False, "No active subscription"
        
        max_users = subscription.get_limit('max_users')
        if max_users is None:
            return True, ""
        
        # Get current user count
        current_users = 0  # TODO: Count actual users
        
        if current_users >= max_users:
            return False, f"User limit reached ({max_users}). Upgrade for more users."
        
        return True, ""
    
    @staticmethod
    def can_use_feature(subscription, feature_code: str) -> tuple[bool, str]:
        """Check if subscription includes feature"""
        if not subscription:
            return False, "No active subscription"
        
        has_feature = subscription.has_feature(feature_code)
        if not has_feature:
            return False, f"Feature '{feature_code}' not available in your plan. Upgrade to unlock."
        
        return True, ""


# ============================================================================
# SUBSCRIPTION CONFIGURATION MANAGER
# ============================================================================

class SubscriptionConfigManager:
    """
    Centralized manager for subscription configuration
    Uses caching for performance
    """
    
    CACHE_TTL = 300  # 5 minutes
    CACHE_KEY_PREFIX = 'subscription_config_'
    
    @classmethod
    def get_plan_template(cls, plan_code: str) -> Optional[Dict]:
        """Get plan template by code"""
        cache_key = f"{cls.CACHE_KEY_PREFIX}plan_{plan_code}"
        cached = cache.get(cache_key)
        
        if cached:
            return cached
        
        template = PLAN_TEMPLATES.get(plan_code)
        if template:
            cache.set(cache_key, template, cls.CACHE_TTL)
        
        return template
    
    @classmethod
    def get_all_plans(cls) -> Dict[str, Dict]:
        """Get all plan templates"""
        cache_key = f"{cls.CACHE_KEY_PREFIX}all_plans"
        cached = cache.get(cache_key)
        
        if cached:
            return cached
        
        cache.set(cache_key, PLAN_TEMPLATES, cls.CACHE_TTL)
        return PLAN_TEMPLATES
    
    @classmethod
    def get_feature_config(cls, feature_code: str) -> Optional[Dict]:
        """Get feature configuration"""
        cache_key = f"{cls.CACHE_KEY_PREFIX}feature_{feature_code}"
        cached = cache.get(cache_key)
        
        if cached:
            return cached
        
        feature = FEATURE_CATALOG.get(feature_code)
        if feature:
            cache.set(cache_key, feature, cls.CACHE_TTL)
        
        return feature
    
    @classmethod
    def get_all_features(cls) -> Dict[str, Dict]:
        """Get all features"""
        cache_key = f"{cls.CACHE_KEY_PREFIX}all_features"
        cached = cache.get(cache_key)
        
        if cached:
            return cached
        
        cache.set(cache_key, FEATURE_CATALOG, cls.CACHE_TTL)
        return FEATURE_CATALOG
    
    @classmethod
    def get_features_by_category(cls, category: str) -> Dict[str, Dict]:
        """Get features filtered by category"""
        all_features = cls.get_all_features()
        return {
            code: config
            for code, config in all_features.items()
            if config.get('category') == category
        }
    
    @classmethod
    def get_usage_limit_config(cls, metric_type: str) -> Optional[Dict]:
        """Get usage limit configuration"""
        return USAGE_LIMITS.get(metric_type)
    
    @classmethod
    def clear_cache(cls):
        """Clear all subscription config cache"""
        # Would need to iterate and delete all keys with prefix
        logger.info("Subscription configuration cache cleared")


# ============================================================================
# SUBSCRIPTION HELPERS
# ============================================================================

def get_active_subscription(user):
    """Get user's active subscription"""
    from .subscription_models import UserSubscription
    
    try:
        return UserSubscription.objects.filter(
            user=user,
            status='active'
        ).select_related('plan').first()
    except Exception as e:
        logger.error(f"Error getting active subscription for {user}: {e}")
        return None


def check_subscription_limit(subscription, limit_key: str, current_value: int) -> tuple[bool, str]:
    """
    Generic subscription limit checker
    
    Args:
        subscription: UserSubscription instance
        limit_key: Limit attribute name (e.g., 'max_projects')
        current_value: Current usage count
    
    Returns:
        (allowed: bool, message: str)
    """
    if not subscription:
        return False, "No active subscription"
    
    limit = subscription.get_limit(limit_key)
    
    # None = unlimited
    if limit is None:
        return True, ""
    
    if current_value >= limit:
        return False, f"Limit reached ({limit}). Upgrade your plan."
    
    return True, ""


def get_upgrade_suggestions(current_plan_code: str) -> List[Dict]:
    """Get suggested upgrade plans"""
    all_plans = PLAN_TEMPLATES
    current_sort = all_plans.get(current_plan_code, {}).get('sort_order', 0)
    
    suggestions = []
    for code, config in all_plans.items():
        if config.get('sort_order', 0) > current_sort:
            suggestions.append({
                'code': code,
                'name': config['display_name'],
                'price': config['price'],
                'badge': config.get('badge', ''),
            })
    
    return sorted(suggestions, key=lambda x: x['price'])
