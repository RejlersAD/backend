# Subscription System - Quick Reference

## 🚀 Quick Start

### 1. Check User Subscription

```python
from apps.rbac.subscription_config import get_active_subscription

subscription = get_active_subscription(request.user)
if subscription:
    print(f"Plan: {subscription.plan.name}")
    print(f"Status: {subscription.status}")
    print(f"Days remaining: {subscription.days_remaining}")
```

### 2. Check Feature Access

```python
if subscription.has_feature('ai_features'):
    # User has AI features
    enable_ai_conversion()
```

### 3. Check Module Access

```python
from apps.rbac.subscription_config import SubscriptionRulesEngine

allowed = SubscriptionRulesEngine.can_access_module(subscription, 'pfd_converter')
if allowed:
    # User can access PFD converter
    ...
```

### 4. Check Usage Limits

```python
allowed, message = SubscriptionRulesEngine.can_create_project(subscription)
if not allowed:
    return Response({'error': message}, status=403)
```

---

## 🎨 Add New Plan (Soft-Coded)

Edit `backend/apps/rbac/subscription_config.py`:

```python
PLAN_TEMPLATES = {
    'startup': {  # New plan
        'name': 'Startup Plan',
        'code': 'startup',
        'display_name': 'Startup',
        'price': 29.00,
        'billing_cycle': 'monthly',
        'max_users': 5,
        'max_storage_gb': 25,
        'allowed_modules': ['crs_documents', 'qhse', 'finance'],
        'features': {
            'ai_features': False,
            'api_access': True,
            'export_data': True,
        },
        'badge': '',
        'color_scheme': 'green',
        'icon': '🚀',
        'sort_order': 1.5,
    }
}
```

Then create plan in database:

```bash
docker-compose exec backend python manage.py shell

from apps.rbac.subscription_models import SubscriptionPlan
from apps.rbac.subscription_config import PLAN_TEMPLATES

plan = SubscriptionPlan.objects.create(**PLAN_TEMPLATES['startup'])
```

---

## 🔒 Protect ViewSet with Subscription

```python
from apps.rbac.subscription_permissions import HasModuleAccess

class MyViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated, HasModuleAccess]
    required_module = 'my_module'  # Auto-checked
```

---

## 📊 Track Usage

```python
from apps.rbac.subscription_models import UsageTracking
from datetime import date

# Get or create usage log
usage, _ = UsageTracking.objects.get_or_create(
    subscription=user_subscription,
    metric_type='documents',
    period='monthly',
    period_start=date.today().replace(day=1),
    defaults={
        'period_end': (date.today() + timedelta(days=30)),
        'limit_value': user_subscription.get_limit('max_documents')
    }
)

# Increment usage
usage.increment(count=1)

# Check if over limit
if usage.is_over_limit:
    # Warn user or block action
    ...
```

---

## 🔧 Custom Limits (Enterprise Clients)

```python
# Override limits for specific customer
subscription = UserSubscription.objects.get(user=user)
subscription.custom_limits = {
    'max_users': 200,  # Override from plan's 50
    'max_storage_gb': 2000,  # Custom amount
}
subscription.custom_features = {
    'white_label': True,  # Add extra feature
}
subscription.save()
```

---

## 📈 Upgrade/Downgrade

```python
# Upgrade
new_plan = SubscriptionPlan.objects.get(code='professional')
subscription.plan = new_plan
subscription.save()

# Record history
SubscriptionHistory.objects.create(
    subscription=subscription,
    action='upgraded',
    old_plan=old_plan,
    new_plan=new_plan,
    performed_by=request.user
)
```

---

## 🌐 API Endpoints Cheat Sheet

```bash
# Get my subscription
GET /api/subscriptions/subscriptions/my_subscription/

# List all plans
GET /api/subscriptions/plans/

# Compare plans
POST /api/subscriptions/plans/compare/
{"plan_ids": ["uuid1", "uuid2"]}

# Upgrade
POST /api/subscriptions/subscriptions/{id}/upgrade/
{"new_plan_id": "uuid"}

# Check limit
POST /api/subscriptions/subscriptions/{id}/check_limit/
{"action": "create_project"}

# Usage summary
GET /api/subscriptions/usage/summary/

# Admin stats
GET /api/subscriptions/dashboard/stats/
```

---

## 🎯 Common Patterns

### Pattern 1: Feature Gate

```python
@require_subscription(feature_code='ai_features')
def ai_feature_view(request):
    # This view requires AI features
    ...
```

### Pattern 2: Usage Limit

```python
@check_usage_limit('upload_file')
def upload_document(request):
    # Automatically checks document limit
    ...
```

### Pattern 3: Middleware Access

```python
# In any view with middleware enabled
if request.has_feature('custom_reports'):
    show_report_builder()

if request.has_module('pfd_converter'):
    enable_pfd_converter()

allowed, message = request.check_limit('create_project')
```

---

## 🐛 Debugging

```python
# Get subscription details
subscription = get_active_subscription(user)
print(f"Plan: {subscription.plan.code}")
print(f"Status: {subscription.status}")
print(f"Expired: {subscription.is_expired}")
print(f"Trial: {subscription.is_trial}")
print(f"Modules: {subscription.plan.allowed_modules}")
print(f"Features: {subscription.plan.features}")
print(f"Max users: {subscription.get_limit('max_users')}")

# Check specific feature
has_feature = subscription.has_feature('ai_features')
print(f"Has AI: {has_feature}")

# Check module access
has_module = SubscriptionRulesEngine.can_access_module(subscription, 'qhse')
print(f"Can access QHSE: {has_module}")
```

---

## ✅ Checklist for New Feature

- [ ] Add feature to `FEATURE_CATALOG`
- [ ] Add to relevant plan templates
- [ ] Update frontend pricing table
- [ ] Add permission check in ViewSet
- [ ] Test upgrade/downgrade scenarios
- [ ] Document in API docs

---

## 📞 Support

**Files:**
- Models: `subscription_models.py`
- Config: `subscription_config.py`
- Views: `subscription_views.py`
- Permissions: `subscription_permissions.py`
- Serializers: `subscription_serializers.py`

**Documentation:**
- Full Guide: `SUBSCRIPTION_GUIDE.md`
- This Reference: `SUBSCRIPTION_QUICK_REF.md`
