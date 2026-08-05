# Subscription Management System (7.3)
## Complete Implementation Guide

**Version:** 1.0.0  
**Created:** January 22, 2026  
**Feature:** Enterprise SaaS Subscription Management

---

## 📋 Table of Contents

1. [Overview](#overview)
2. [Architecture](#architecture)
3. [Features](#features)
4. [Installation](#installation)
5. [Configuration](#configuration)
6. [API Endpoints](#api-endpoints)
7. [Usage Examples](#usage-examples)
8. [Soft-Coded Customization](#soft-coded-customization)
9. [Integration Guide](#integration-guide)
10. [Best Practices](#best-practices)

---

## 🎯 Overview

The Subscription Management System (7.3) is a **fully soft-coded, enterprise-grade subscription platform** designed for the AIFlow application. It provides dynamic plan management, usage tracking, and automatic enforcement of subscription limits.

### Key Benefits

✅ **Soft-Coded Configuration** - No code changes needed for new plans  
✅ **Dynamic Feature Management** - Enable/disable features per plan  
✅ **Usage Tracking** - Monitor and enforce subscription limits  
✅ **Flexible Billing** - Monthly, quarterly, yearly, lifetime  
✅ **Enterprise Ready** - Multi-tier plans with custom overrides  
✅ **Auto-Enforcement** - Middleware automatically checks access  
✅ **Audit Trail** - Complete history of subscription changes  

---

## 🏗️ Architecture

### Database Models

```
┌─────────────────────┐
│ SubscriptionPlan    │ ← Soft-coded plan templates
├─────────────────────┤
│ - id, name, code    │
│ - price, billing    │
│ - features (JSON)   │
│ - allowed_modules   │
│ - max_users, etc.   │
└─────────────────────┘
         │
         │ (many)
         ▼
┌─────────────────────┐
│ UserSubscription    │ ← User's active subscription
├─────────────────────┤
│ - user, plan        │
│ - status, dates     │
│ - custom_limits     │
│ - custom_features   │
└─────────────────────┘
         │
         │ (many)
         ▼
┌─────────────────────┐
│ UsageTracking       │ ← Daily/monthly usage logs
├─────────────────────┤
│ - metric_type       │
│ - usage_count       │
│ - limit_value       │
│ - is_over_limit     │
└─────────────────────┘
```

### Component Layers

```
┌──────────────────────────────────────────────┐
│           Frontend (React/Vue)                │
│  Pricing Page | Subscription Dashboard       │
└──────────────────────────────────────────────┘
                     │
                     ▼
┌──────────────────────────────────────────────┐
│              REST API Layer                   │
│  /api/subscriptions/* endpoints              │
└──────────────────────────────────────────────┘
                     │
                     ▼
┌──────────────────────────────────────────────┐
│         Subscription ViewSets                 │
│  PlanViewSet | SubscriptionViewSet           │
└──────────────────────────────────────────────┘
                     │
                     ▼
┌──────────────────────────────────────────────┐
│        Permission & Middleware                │
│  HasActiveSubscription | UsageLimit          │
└──────────────────────────────────────────────┘
                     │
                     ▼
┌──────────────────────────────────────────────┐
│       Configuration Engine (Soft-Coded)       │
│  PLAN_TEMPLATES | FEATURE_CATALOG            │
└──────────────────────────────────────────────┘
                     │
                     ▼
┌──────────────────────────────────────────────┐
│          Database Models                      │
│  PostgreSQL with Django ORM                  │
└──────────────────────────────────────────────┘
```

---

## 🎨 Features

### 1. Soft-Coded Subscription Plans

Define plans in Python configuration without database changes:

```python
# backend/apps/rbac/subscription_config.py

PLAN_TEMPLATES = {
    'professional': {
        'name': 'Professional Plan',
        'price': 149.00,
        'max_users': 50,
        'max_storage_gb': 500,
        'allowed_modules': ['crs', 'qhse', 'finance', 'pfd'],
        'features': {
            'ai_features': True,
            'advanced_analytics': True,
            'priority_support': True,
        },
    }
}
```

### 2. Dynamic Feature Management

```python
FEATURE_CATALOG = {
    'ai_document_analysis': {
        'name': 'AI Document Analysis',
        'feature_type': 'boolean',
        'category': 'ai',
        'default_value': {'enabled': False},
    }
}
```

### 3. Usage Tracking & Enforcement

Automatically track and enforce limits:

- **Storage** - GB used vs limit
- **API Calls** - Daily request count
- **Projects** - Active project count
- **Users** - Team member count
- **Documents** - Monthly upload count

### 4. Subscription Actions

- **Upgrade** - Move to higher-tier plan
- **Downgrade** - Move to lower-tier plan
- **Cancel** - Cancel with reason tracking
- **Renew** - Automatic or manual renewal
- **Suspend** - Admin suspension for non-payment

### 5. Invoicing & Billing

- Auto-generate invoices
- Track payment status
- Support multiple gateways (Stripe, PayPal)
- Line item details

---

## 📦 Installation

### Step 1: Run Migrations

```bash
# Create migration files
docker-compose -f docker-compose.local.yml exec backend python manage.py makemigrations rbac

# Apply migrations
docker-compose -f docker-compose.local.yml exec backend python manage.py migrate
```

### Step 2: Initialize Default Plans

```python
# Create management command or admin action
docker-compose -f docker-compose.local.yml exec backend python manage.py shell

from apps.rbac.subscription_config import PLAN_TEMPLATES, SubscriptionConfigManager
from apps.rbac.subscription_models import SubscriptionPlan

# Create plans from templates
for code, template in PLAN_TEMPLATES.items():
    plan, created = SubscriptionPlan.objects.get_or_create(
        code=code,
        defaults=template
    )
    print(f"{'Created' if created else 'Updated'}: {plan.display_name}")
```

### Step 3: Add Middleware (Optional)

```python
# config/settings.py

MIDDLEWARE = [
    ...
    'apps.rbac.subscription_permissions.SubscriptionCheckMiddleware',
]
```

---

## ⚙️ Configuration

### Environment Variables

```bash
# .env.local

# Subscription Settings
SUBSCRIPTION_TRIAL_DAYS=14
SUBSCRIPTION_AUTO_RENEW=True
SUBSCRIPTION_GRACE_PERIOD_DAYS=7

# Payment Gateways
STRIPE_SECRET_KEY=sk_test_...
STRIPE_PUBLISHABLE_KEY=pk_test_...
PAYPAL_CLIENT_ID=...
PAYPAL_SECRET=...

# Usage Limits
STORAGE_WARNING_THRESHOLD=0.80  # Warn at 80%
API_RATE_LIMIT_WARNING=0.90     # Warn at 90%
```

### Plan Customization

Edit `subscription_config.py` to add/modify plans:

```python
PLAN_TEMPLATES = {
    'my_custom_plan': {
        'name': 'Custom Enterprise',
        'code': 'custom_enterprise',
        'display_name': 'Custom Enterprise Solution',
        'description': 'Fully customized for your needs',
        'plan_type': 'custom',
        'billing_cycle': 'yearly',
        'price': 2999.00,
        'trial_days': 30,
        'max_users': None,  # Unlimited
        'max_storage_gb': None,  # Unlimited
        'allowed_modules': 'ALL',  # All modules
        'features': {
            'ai_features': True,
            'white_label': True,
            'dedicated_support': True,
            'custom_integrations': True,
        },
        'badge': 'Premium',
        'color_scheme': 'platinum',
        'icon': '💎',
        'sort_order': 5,
    }
}
```

---

## 🔌 API Endpoints

### Base URL: `/api/subscriptions/`

#### Subscription Plans

```http
# List all public plans
GET /api/subscriptions/plans/

# Get specific plan
GET /api/subscriptions/plans/{id}/

# Compare plans
POST /api/subscriptions/plans/compare/
Body: {"plan_ids": ["uuid1", "uuid2"]}

# Create plan from template (Admin only)
POST /api/subscriptions/plans/create_from_template/
Body: {"template_code": "professional"}
```

#### User Subscriptions

```http
# List user subscriptions (Admin: all, User: own)
GET /api/subscriptions/subscriptions/

# Get my active subscription
GET /api/subscriptions/subscriptions/my_subscription/

# Create subscription (Admin only)
POST /api/subscriptions/subscriptions/
Body: {
  "user": "user_id",
  "plan": "plan_id",
  "start_date": "2026-01-22",
  "auto_renew": true
}

# Upgrade subscription
POST /api/subscriptions/subscriptions/{id}/upgrade/
Body: {"new_plan_id": "uuid", "reason": "User upgrade"}

# Downgrade subscription
POST /api/subscriptions/subscriptions/{id}/downgrade/
Body: {"new_plan_id": "uuid"}

# Cancel subscription
POST /api/subscriptions/subscriptions/{id}/cancel/
Body: {"reason": "Too expensive"}

# Check limit before action
POST /api/subscriptions/subscriptions/{id}/check_limit/
Body: {"action": "create_project"}
```

#### Usage Tracking

```http
# Get usage summary
GET /api/subscriptions/usage/summary/

# List usage logs
GET /api/subscriptions/usage/?metric_type=storage&period=monthly
```

#### Subscription History

```http
# Get audit trail
GET /api/subscriptions/history/?subscription={id}

# Get user's history
GET /api/subscriptions/history/?subscription__user__email=user@example.com
```

#### Invoices

```http
# List invoices
GET /api/subscriptions/invoices/

# Mark invoice as paid (Admin only)
POST /api/subscriptions/invoices/{id}/mark_paid/
Body: {
  "transaction_id": "stripe_ch_123",
  "payment_method": "stripe"
}
```

#### Dashboard (Admin Only)

```http
# Get subscription statistics
GET /api/subscriptions/dashboard/stats/

# Get revenue trends
GET /api/subscriptions/dashboard/revenue_trends/?months=6
```

---

## 💻 Usage Examples

### Example 1: Check User Subscription in View

```python
from rest_framework.views import APIView
from rest_framework.response import Response
from apps.rbac.subscription_config import get_active_subscription

class MyProtectedView(APIView):
    def get(self, request):
        subscription = get_active_subscription(request.user)
        
        if not subscription:
            return Response({
                'error': 'No active subscription'
            }, status=403)
        
        # Check if plan includes feature
        if not subscription.has_feature('ai_features'):
            return Response({
                'error': 'Upgrade required for AI features'
            }, status=403)
        
        # Proceed with logic
        return Response({'data': '...'})
```

### Example 2: Use Permission Classes

```python
from rest_framework import viewsets
from apps.rbac.subscription_permissions import (
    HasActiveSubscription,
    HasSubscriptionFeature,
    HasModuleAccess
)

class PFDConverterViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated, HasModuleAccess]
    required_module = 'pfd_converter'  # Enforced automatically
    
    # AI features require professional+ plan
    @action(detail=True, methods=['post'])
    def ai_convert(self, request, pk=None):
        # Check feature requirement
        if not request.has_feature('ai_features'):
            return Response({'error': 'AI features not available'}, status=403)
        
        # Proceed with AI conversion
        ...
```

### Example 3: Enforce Usage Limits

```python
from rest_framework.decorators import api_view
from apps.rbac.subscription_permissions import check_usage_limit

@api_view(['POST'])
@check_usage_limit('create_project')
def create_project(request):
    # This decorator automatically checks project limit
    # and returns 403 if exceeded
    
    # Create project logic
    project = Project.objects.create(...)
    return Response({'project_id': project.id})
```

### Example 4: Frontend - Display Subscription Info

```javascript
// React component
import { useEffect, useState } from 'react';
import axios from 'axios';

function SubscriptionBadge() {
  const [subscription, setSubscription] = useState(null);
  
  useEffect(() => {
    axios.get('/api/subscriptions/subscriptions/my_subscription/')
      .then(res => setSubscription(res.data))
      .catch(err => console.error(err));
  }, []);
  
  if (!subscription) return <div>No subscription</div>;
  
  return (
    <div className="subscription-badge">
      <span className={`badge badge-${subscription.plan_details.color_scheme}`}>
        {subscription.plan_details.icon} {subscription.plan_name}
      </span>
      {subscription.is_trial && (
        <span className="trial-indicator">
          Trial - {subscription.days_remaining} days left
        </span>
      )}
    </div>
  );
}
```

### Example 5: Upgrade Flow

```javascript
// Upgrade button handler
async function handleUpgrade(newPlanId) {
  try {
    const response = await axios.post(
      `/api/subscriptions/subscriptions/${currentSubscriptionId}/upgrade/`,
      {
        new_plan_id: newPlanId,
        reason: 'User initiated upgrade'
      }
    );
    
    alert('Successfully upgraded to ' + response.data.plan_details.display_name);
    window.location.reload();
  } catch (error) {
    alert('Upgrade failed: ' + error.response.data.error);
  }
}
```

---

## 🔧 Soft-Coded Customization

### Add New Feature

1. Add to `FEATURE_CATALOG`:

```python
FEATURE_CATALOG = {
    ...
    'custom_reports': {
        'name': 'Custom Reports',
        'code': 'custom_reports',
        'description': 'Create custom analytics reports',
        'feature_type': 'boolean',
        'category': 'analytics',
        'icon': '📊',
        'default_value': {'enabled': False},
    }
}
```

2. Add to plan templates:

```python
PLAN_TEMPLATES = {
    'professional': {
        ...
        'features': {
            ...
            'custom_reports': True,  # Enable for professional
        }
    }
}
```

3. Use in code:

```python
if subscription.has_feature('custom_reports'):
    # Show custom report builder
    ...
```

### Add New Usage Metric

1. Add to `USAGE_LIMITS`:

```python
USAGE_LIMITS = {
    ...
    'exports': {
        'metric_type': 'exports',
        'unit': 'files',
        'warning_threshold': 0.85,
        'hard_limit': True,
    }
}
```

2. Track usage:

```python
from apps.rbac.subscription_models import UsageTracking

# When user exports data
usage = UsageTracking.objects.get_or_create(
    subscription=user_subscription,
    metric_type='exports',
    period='monthly',
    period_start=date.today().replace(day=1),
    period_end=...
)
usage.increment(count=1)
```

---

## 🔗 Integration Guide

### Integrate with Existing Modules

#### CRS Module Integration

```python
# apps/crs/views.py

from apps.rbac.subscription_permissions import HasModuleAccess

class CRSDocumentViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated, HasModuleAccess]
    required_module = 'crs_documents'
    
    # Automatically checks if user's plan includes CRS
```

#### PFD Converter Integration

```python
# apps/pfd_converter/views.py

from apps.rbac.subscription_permissions import (
    HasModuleAccess, SubscriptionUsageLimit
)

class PFDDocumentViewSet(viewsets.ModelViewSet):
    permission_classes = [
        IsAuthenticated,
        HasModuleAccess,
        SubscriptionUsageLimit
    ]
    required_module = 'pfd_converter'
    limit_check_action = 'upload_file'  # Check file limit on upload
```

### Payment Gateway Integration

#### Stripe Integration Example

```python
# apps/rbac/payment_integrations.py

import stripe
from django.conf import settings

stripe.api_key = settings.STRIPE_SECRET_KEY

def create_stripe_subscription(user_subscription):
    """Create Stripe subscription for user"""
    
    # Create customer
    customer = stripe.Customer.create(
        email=user_subscription.user.email,
        metadata={'subscription_id': str(user_subscription.id)}
    )
    
    # Create subscription
    stripe_sub = stripe.Subscription.create(
        customer=customer.id,
        items=[{
            'price': get_stripe_price_id(user_subscription.plan),
        }],
        trial_period_days=user_subscription.plan.trial_days,
    )
    
    # Store Stripe ID
    user_subscription.metadata['stripe_subscription_id'] = stripe_sub.id
    user_subscription.metadata['stripe_customer_id'] = customer.id
    user_subscription.save()
    
    return stripe_sub
```

---

## 📊 Best Practices

### 1. Always Use Soft-Coded Configuration

✅ **DO:** Define plans in `subscription_config.py`  
❌ **DON'T:** Hardcode plan logic in views

```python
# ✅ GOOD
if subscription.has_feature('ai_features'):
    enable_ai()

# ❌ BAD
if subscription.plan.code == 'professional':
    enable_ai()
```

### 2. Cache Subscription Checks

```python
from django.core.cache import cache

def get_user_subscription_cached(user):
    cache_key = f'subscription_{user.id}'
    subscription = cache.get(cache_key)
    
    if not subscription:
        subscription = get_active_subscription(user)
        cache.set(cache_key, subscription, 300)  # 5 min
    
    return subscription
```

### 3. Handle Gracefully

```python
# Always provide upgrade path
if not allowed:
    return Response({
        'error': message,
        'upgrade_required': True,
        'current_plan': subscription.plan.code,
        'suggested_plans': get_upgrade_suggestions(subscription.plan.code)
    }, status=403)
```

### 4. Audit Everything

```python
# Log all subscription changes
SubscriptionHistory.objects.create(
    subscription=subscription,
    action='upgraded',
    old_plan=old_plan,
    new_plan=new_plan,
    performed_by=request.user,
    reason=reason,
    ip_address=request.META.get('REMOTE_ADDR')
)
```

### 5. Monitor Usage

```python
# Set up Celery task to check usage daily
@shared_task
def check_subscription_usage():
    for subscription in UserSubscription.objects.filter(status='active'):
        usage_logs = subscription.usage_logs.filter(is_over_limit=True)
        
        if usage_logs.exists():
            # Send warning email
            send_usage_warning_email(subscription)
```

---

## 🎯 Summary

The Subscription Management System (7.3) provides:

1. ✅ **Soft-coded plan management** - No DB changes for new plans
2. ✅ **Dynamic feature toggles** - Enable/disable per plan
3. ✅ **Automatic enforcement** - Middleware checks access
4. ✅ **Usage tracking** - Monitor limits in real-time
5. ✅ **Complete audit trail** - Track all changes
6. ✅ **Flexible billing** - Multiple cycles supported
7. ✅ **Enterprise ready** - Custom overrides for large clients

**Next Steps:**
1. Run migrations
2. Create default plans from templates
3. Integrate with existing modules
4. Setup payment gateway
5. Build frontend pricing page

---

**Documentation Version:** 1.0.0  
**Last Updated:** January 22, 2026  
**Support:** For questions, contact the development team
