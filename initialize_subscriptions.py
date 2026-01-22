"""
Subscription System Initialization Script
Run this after migrations to set up default plans
"""
import os
import sys
import django

# Setup Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from apps.rbac.subscription_models import SubscriptionPlan, SubscriptionFeature
from apps.rbac.subscription_config import PLAN_TEMPLATES, FEATURE_CATALOG
from django.db import transaction


def initialize_plans():
    """Create subscription plans from templates"""
    print("\n" + "="*70)
    print("  SUBSCRIPTION PLAN INITIALIZATION")
    print("="*70 + "\n")
    
    created_count = 0
    updated_count = 0
    
    with transaction.atomic():
        for code, template in PLAN_TEMPLATES.items():
            plan, created = SubscriptionPlan.objects.update_or_create(
                code=code,
                defaults=template
            )
            
            if created:
                print(f"✅ Created: {plan.display_name} (${plan.price}/{plan.billing_cycle})")
                created_count += 1
            else:
                print(f"📝 Updated: {plan.display_name} (${plan.price}/{plan.billing_cycle})")
                updated_count += 1
    
    print(f"\n📊 Summary:")
    print(f"   - Created: {created_count} plans")
    print(f"   - Updated: {updated_count} plans")
    print(f"   - Total: {created_count + updated_count} plans\n")


def initialize_features():
    """Create subscription features from catalog"""
    print("\n" + "="*70)
    print("  SUBSCRIPTION FEATURE INITIALIZATION")
    print("="*70 + "\n")
    
    created_count = 0
    updated_count = 0
    
    with transaction.atomic():
        for code, config in FEATURE_CATALOG.items():
            feature, created = SubscriptionFeature.objects.update_or_create(
                code=code,
                defaults={
                    'name': config['name'],
                    'description': config['description'],
                    'feature_type': config['feature_type'],
                    'category': config.get('category', 'general'),
                    'icon': config.get('icon', ''),
                    'is_highlighted': config.get('is_highlighted', False),
                    'default_value': config.get('default_value', {}),
                    'unit': config.get('unit', ''),
                }
            )
            
            if created:
                print(f"✅ Created: {feature.name} ({feature.category})")
                created_count += 1
            else:
                print(f"📝 Updated: {feature.name} ({feature.category})")
                updated_count += 1
    
    print(f"\n📊 Summary:")
    print(f"   - Created: {created_count} features")
    print(f"   - Updated: {updated_count} features")
    print(f"   - Total: {created_count + updated_count} features\n")


def display_plans():
    """Display all active plans"""
    print("\n" + "="*70)
    print("  ACTIVE SUBSCRIPTION PLANS")
    print("="*70 + "\n")
    
    plans = SubscriptionPlan.objects.filter(is_active=True).order_by('sort_order', 'price')
    
    for plan in plans:
        print(f"\n{plan.icon} {plan.display_name}")
        print(f"   Code: {plan.code}")
        print(f"   Price: ${plan.price}/{plan.billing_cycle}")
        print(f"   Type: {plan.plan_type}")
        print(f"   Badge: {plan.badge or 'N/A'}")
        
        print(f"\n   Limits:")
        print(f"      Users: {plan.max_users or 'Unlimited'}")
        print(f"      Storage: {plan.max_storage_gb or 'Unlimited'} GB")
        print(f"      API Calls: {plan.max_api_calls_per_day or 'Unlimited'}/day")
        print(f"      Projects: {plan.max_projects or 'Unlimited'}")
        print(f"      Documents: {plan.max_documents or 'Unlimited'}/month")
        
        print(f"\n   Features: {len(plan.features)} enabled")
        for feature_key, feature_value in (plan.features or {}).items():
            if feature_value:
                print(f"      ✓ {feature_key}")
        
        print(f"\n   Modules: ", end='')
        if plan.allowed_modules == 'ALL' or plan.allowed_modules == ['ALL']:
            print("ALL modules")
        else:
            print(f"{len(plan.allowed_modules)} modules")
            for module in plan.allowed_modules[:3]:  # Show first 3
                print(f"      - {module}")
            if len(plan.allowed_modules) > 3:
                print(f"      ... and {len(plan.allowed_modules) - 3} more")
    
    print("\n" + "="*70 + "\n")


def main():
    """Main initialization function"""
    print("\n")
    print("╔══════════════════════════════════════════════════════════════╗")
    print("║                                                              ║")
    print("║       SUBSCRIPTION MANAGEMENT SYSTEM INITIALIZATION          ║")
    print("║                      Version 1.0.0                           ║")
    print("║                                                              ║")
    print("╚══════════════════════════════════════════════════════════════╝")
    
    try:
        # Initialize plans
        initialize_plans()
        
        # Initialize features
        initialize_features()
        
        # Display active plans
        display_plans()
        
        print("✅ INITIALIZATION COMPLETE!")
        print("\n📚 Next Steps:")
        print("   1. Review plans in Django Admin: /admin/rbac/subscriptionplan/")
        print("   2. Assign plans to users")
        print("   3. Integrate subscription checks in ViewSets")
        print("   4. Build frontend pricing page")
        print("   5. Setup payment gateway integration")
        
        print("\n📖 Documentation:")
        print("   - Full Guide: backend/apps/rbac/SUBSCRIPTION_GUIDE.md")
        print("   - Quick Ref: backend/apps/rbac/SUBSCRIPTION_QUICK_REF.md")
        print("   - Summary: backend/apps/rbac/SUBSCRIPTION_SUMMARY.md")
        
        print("\n🔗 API Base URL: /api/subscriptions/")
        print("\n" + "="*70 + "\n")
        
        return 0
        
    except Exception as e:
        print(f"\n❌ ERROR: {str(e)}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == '__main__':
    sys.exit(main())
