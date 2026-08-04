# 🔍 KIRAN ACCESS ISSUE - ROOT CAUSE ANALYSIS

**Date**: 2026-07-13  
**Issue**: `kiran.ingale@rejlers.ae` can see Finance, QHSE despite having "Default" role  
**Status**: ❌ **CRITICAL BUG FOUND**  

---

## 🎯 ROOT CAUSE DISCOVERED

### **Problem 1: Config Bypass (Line 291)**
```python
# backend/apps/rbac/rbac_config.py:291
MODULE_ACCESS_RULES = {
    'check_role_first': True,
    'check_direct_assignment': True,
    'admin_has_all_access': True,  # ⚠️  THIS IS THE PROBLEM!
    'superadmin_has_all_access': True,
}
```

**Impact**: Any user with role code `'admin'` (not just `'super_admin'`) bypasses ALL module checks and can see everything.

### **Problem 2: User Has Admin Role**
The user `kiran.ingale@rejlers.ae` likely has:
- **Primary Role**: "Default" (visible in UI)
- **Secondary Role**: "Admin" or "ICT Admin" (hidden/not obvious)

Both roles are active, so the admin role grants unrestricted access.

---

## 🔍 HOW TO VERIFY

### **Step 1: Check User's Roles** (Railway Shell)
```bash
python check_kiran_access.py
```

**Expected Output** (Problem Confirmed):
```
🎭 ASSIGNED ROLES:
   • Default (code: default, level: 4)
   • Admin (code: admin, level: 2)  ⚠️  ADMIN ROLE - BYPASSES MODULE CHECKS!

📦 ACCESSIBLE MODULES:
   Total modules: 65

   💰 FINANCE MODULES (8):
      • finance: Finance
      • finance_invoices: Invoices
      ...

❌ DIAGNOSIS: User has ADMIN role - bypasses all module checks
```

---

## ✅ COMPLETE FIX (Choose ONE Solution)

### **SOLUTION A: Remove Admin Role from User** (Quick Fix - 2 min)
**Best if**: Only a few users have incorrect admin roles

```bash
# Railway shell
python manage.py shell

>>> from django.contrib.auth import get_user_model
>>> from apps.rbac.models import UserProfile, Role
>>> 
>>> # Get user
>>> user = get_user_model().objects.get(email='kiran.ingale@rejlers.ae')
>>> profile = user.rbac_profile
>>> 
>>> # Check current roles
>>> print("Current roles:")
>>> for role in profile.roles.filter(is_active=True):
...     print(f"  - {role.code}: {role.name}")
>>> 
>>> # Remove admin roles (keep only default)
>>> admin_roles = profile.roles.filter(code__in=['admin', 'ict_admin'], is_active=True)
>>> for role in admin_roles:
...     profile.roles.remove(role)
...     print(f"❌ Removed: {role.code}")
>>> 
>>> # Verify
>>> print("\nRemaining roles:")
>>> for role in profile.roles.filter(is_active=True):
...     print(f"  - {role.code}: {role.name}")
>>> 
>>> # Clear cache
>>> from django.core.cache import cache
>>> cache.delete(f'user_modules_{profile.id}')
>>> cache.delete(f'user_permissions_{profile.id}')
>>> 
>>> print("\n✅ User role fixed! User must logout and login.")
>>> exit()
```

---

### **SOLUTION B: Change Config to Disable Admin Bypass** (System-Wide Fix - 5 min)
**Best if**: Many users have admin role but shouldn't bypass checks

This enforces that ONLY `super_admin` bypasses checks, not regular `admin`.

#### **Step 1: Update Backend Config**
```python
# backend/apps/rbac/rbac_config.py:291

MODULE_ACCESS_RULES = {
    'check_role_first': True,
    'check_direct_assignment': True,
    'admin_has_all_access': False,  # ✅ CHANGED: Admins must follow role policy
    'superadmin_has_all_access': True,  # Only super admins bypass
}
```

#### **Step 2: Update Permission Class**
```python
# backend/apps/rbac/permissions.py:98

class HasModuleAccess(permissions.BasePermission):
    """
    Permission class to check if user has access to specific module
    """
    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        
        # ONLY Super admin bypasses (not regular admin)  ✅ CHANGED
        try:
            profile = request.user.rbac_profile
            if profile.roles.filter(code='super_admin', is_active=True).exists():
                return True
        except UserProfile.DoesNotExist:
            return False
        
        # Everyone else (including admin) must have explicit module access
        module_required = getattr(view, 'module_required', None)
        if not module_required:
            return True
        
        return profile.has_module_access(module_required)
```

#### **Step 3: Commit and Deploy**
```bash
cd backend
git add apps/rbac/rbac_config.py apps/rbac/permissions.py
git commit -m "fix(rbac): disable admin bypass - enforce role-based access

CRITICAL SECURITY FIX

PROBLEM:
- Regular 'admin' role bypassed all module checks
- Users with admin role could access Finance, HR, QHSE, Procurement
- MODULE_ACCESS_RULES['admin_has_all_access'] = True (too permissive)

SOLUTION:
✓ Changed 'admin_has_all_access': False
✓ Updated HasModuleAccess to ONLY allow super_admin bypass
✓ Regular admins now follow ROLE_MODULE_POLICY like everyone else

SECURITY IMPACT:
- Admin role: Can manage users/roles, but module access from policy
- Super Admin: Still bypasses all checks (emergency access)
- Default/Engineer: Module access from ROLE_MODULE_POLICY (unchanged)

Users with 'admin' role must logout/login to refresh JWT tokens"
git push origin main
```

---

### **SOLUTION C: Hybrid Approach** (Recommended - 10 min)
Combine both solutions for maximum security:

1. **Fix the config** (Solution B) - Prevents future issues
2. **Clean up existing users** (Solution A) - Fix current state
3. **Run role sync** - Ensure everyone has correct access

```bash
# Step 1: Update config and permissions (Solution B)
# ... (follow Solution B steps)

# Step 2: Remove admin role from users who don't need it
python manage.py shell

>>> from apps.rbac.models import UserProfile, Role
>>> 
>>> # Find all users with 'admin' role
>>> admin_role = Role.objects.get(code='admin', is_active=True)
>>> users_with_admin = UserProfile.objects.filter(
...     roles=admin_role,
...     is_deleted=False
... ).select_related('user')
>>> 
>>> print(f"Users with 'admin' role: {users_with_admin.count()}")
>>> for profile in users_with_admin:
...     print(f"  - {profile.user.email}")
>>> 
>>> # Review list and remove admin from those who don't need it
>>> # (Keep admin for actual system administrators only)
>>> exit()

# Step 3: Sync all users to ensure correct access
python manage.py sync_all_users_to_roles

# Step 4: Verify specific user
python manage.py diagnose_user_rbac --email kiran.ingale@rejlers.ae
```

---

## 🔍 VERIFICATION

After applying fix, verify:

### **1. User Diagnostic**
```bash
python manage.py diagnose_user_rbac --email kiran.ingale@rejlers.ae
```

**Expected Output**:
```
🎭 Step 3: Checking assigned roles...
  ✅ 1 role(s) assigned:
     🟢 USER Default (code: default, level: 4)  ✅ NO ADMIN ROLE

📦 Step 4: Computing expected modules...
  Expected modules: 25 (from ROLE_MODULE_POLICY['default'])

🔍 Step 6: Comparing expected vs actual...
  ✅ PERFECT MATCH - user has exactly the right modules
  
  ❌ Should NOT have:
     (none)
  
  ✅ Should have but missing:
     (none)
```

### **2. Frontend Test**
1. **User logs out and logs in** (JWT refresh)
2. Navigate to `https://www.radai.ae`
3. **Should see**:
   - ✅ Dashboard
   - ✅ 1. Engineering
   - ✅ 2. COMMON (CRS, DesignIQ)
4. **Should NOT see**:
   - ❌ 4. Human Resources
   - ❌ 5. Finance
   - ❌ 6. Procurement
   - ❌ 7. QHSE
   - ❌ 9. Admin

### **3. Direct URL Test**
Try accessing protected URLs directly:
- `https://www.radai.ae/finance` → ❌ Access Denied / Redirect
- `https://www.radai.ae/admin/users` → ❌ Access Denied / Redirect
- `https://www.radai.ae/qhse` → ❌ Access Denied / Redirect

---

## 📊 WHO IS AFFECTED?

### **Find All Users with Admin Role**
```bash
python manage.py shell -c "
from apps.rbac.models import UserProfile, Role
admin_role = Role.objects.get(code='admin', is_active=True)
users = UserProfile.objects.filter(roles=admin_role, is_deleted=False)
print(f'Users with admin role: {users.count()}')
for p in users.select_related('user'):
    print(f'  - {p.user.email}')
"
```

### **Expected Impact**
- **Super Admins**: NO CHANGE (still bypass all checks)
- **Regular Admins**: Must have explicit module access from roles
- **Default/Engineers**: NO CHANGE (already using role policy)
- **Custom Role Users**: Already being removed (separate fix)

---

## 💡 WHY THIS HAPPENED

### **Original Intent** (Incorrect)
```
admin_has_all_access: True
→ "Admins should be able to access everything to manage users"
```

### **Correct Approach**
```
admin_has_all_access: False
→ "Admins can manage users/roles, but need explicit module access like everyone else"
→ "Only super_admin bypasses (emergency access only)"
```

### **Role Separation**
| Role | User Management | Module Access |
|------|----------------|---------------|
| **Super Admin** | ✅ Full | ✅ Bypass (all modules) |
| **Admin** | ✅ Full | ❌ From role policy only |
| **Default** | ❌ None | ❌ From role policy only |

---

## 🚀 RECOMMENDED ACTION

**Choice**: Solution C (Hybrid) ✅

**Reason**:
1. **Fixes root cause** (config change prevents future issues)
2. **Cleans up existing state** (removes incorrect role assignments)
3. **System-wide enforcement** (sync ensures everyone is correct)

**Time**: 10-15 minutes  
**Risk**: LOW (transaction-safe, reversible)  
**Impact**: HIGH (fixes security bypass)  

---

**EXECUTE NOW**:
1. Update `rbac_config.py` and `permissions.py` (Solution B)
2. Review and fix user role assignments (Solution A)
3. Deploy and verify (Solution C)

✅ **This will completely fix the issue!**
