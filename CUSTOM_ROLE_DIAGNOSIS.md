# Custom Role "Onboarding" - Features Not Showing Issue

## 🔍 Root Cause Analysis

Based on code investigation, the issue occurs because:

**1. Backend Module Resolution (`apps/rbac/models.py`, line 389-431):**
```python
def get_all_modules(self):
    """Get all accessible modules from all assigned roles (with caching)"""
    # Gets modules from active roles through RoleModule junction table
    user_role_ids = UserRole.objects.filter(
        user_profile=self,
        role__is_active=True
    ).values_list('role_id', flat=True)
    
    modules = list(Module.objects.filter(
        rolemodule__role_id__in=user_role_ids,  # ← This is the KEY query
        is_active=True
    ).distinct())
```

**2. Frontend Feature Display (`components/Layout/Sidebar.jsx`, line 152-258):**
```javascript
const fetchUserModules = async () => {
  const response = await fetch(`${API_BASE_URL}/rbac/users/me/`);
  const moduleCodes = userData.modules?.map(m => m.code) || [];
  setUserModules(moduleCodes);
};

const hasModuleAccess = (item) => {
  return userModules.includes(item.moduleCode);
};
```

**3. The Problem:**
When you created the custom role "Onboarding" via `/admin/roles`, you provided:
- ✓ Role name
- ✓ Role code
- ✓ Role level
- ✗ **BUT NO MODULES** were assigned

Without modules in the `RoleModule` junction table, the backend returns an empty modules array, and the frontend hides all features.

---

## ✅ Solution: Assign Modules to the "Onboarding" Role

### Method 1: Via UI (Recommended)

1. **Open Role Management**
   - Navigate to: `https://frontend-cyan-eta-q169h70uw0.vercel.app/admin/roles`
   - Login as Super Admin

2. **Select the "Onboarding" Role**
   - Find "Onboarding" in the roles list
   - Click to select it

3. **Assign Modules**
   - Click the **"Modules"** tab
   - Toggle ON the modules this role should access:
     - `hr_self_service` (My Profile - Employee Self-Service)
     - `hr_onboarding` (Onboarding | Offboarding)
     - Add any other modules needed for onboarding users

4. **Save Changes**
   - Changes take effect immediately
   - **User must refresh browser or logout/login**

### Method 2: Backend Code Inspection

Check what happened in preprod database by running this query:

```sql
-- Check if "Onboarding" role exists
SELECT id, code, name, level, is_active 
FROM rbac_roles 
WHERE code = 'onboarding' OR LOWER(name) = 'onboarding';

-- Check if any modules are assigned to this role
SELECT r.name as role_name, m.code as module_code, m.name as module_name
FROM rbac_roles r
LEFT JOIN rbac_role_modules rm ON r.id = rm.role_id
LEFT JOIN rbac_modules m ON rm.module_id = m.id
WHERE r.code = 'onboarding' OR LOWER(r.name) = 'onboarding';

-- Check user's role assignment
SELECT u.email, r.name as role_name, ur.is_primary
FROM auth_user u
JOIN rbac_userprofiles p ON u.id = p.user_id
JOIN rbac_user_roles ur ON p.id = ur.user_profile_id
JOIN rbac_roles r ON ur.role_id = r.id
WHERE u.email = 'lira.viaga@rejlers.ae';
```

Expected result if properly configured:
- Role exists: ✓
- Modules assigned: ✓ (should show hr_self_service, hr_onboarding, etc.)
- User assigned: ✓

If `rbac_role_modules` has 0 rows for "Onboarding" → **That's the problem!**

---

## 🔧 Prevention: Improve Role Creation UX

### Backend Enhancement (Soft-Coded)

**File:** `backend/apps/rbac/views.py` (RoleViewSet)

Add validation to warn when creating roles without modules:

```python
def create(self, request, *args, **kwargs):
    """Create a new role (SOFT-CODED: validation from rbac_config.py)"""
    response = super().create(request, *args, **kwargs)
    
    # SOFT-CODED: Warn if custom role created without modules
    role_code = request.data.get('code', '')
    if not role_code.startswith('super_admin') and not role_code in SYSTEM_ROLES_CONFIG:
        # This is a custom role
        module_ids = request.data.get('module_ids', [])
        if not module_ids:
            response.data['warning'] = (
                'Role created without modules. '
                'Users assigned to this role will not see any features. '
                'Please assign modules via the Modules tab.'
            )
    
    return response
```

### Frontend Enhancement (Soft-Coded)

**File:** `frontend/src/pages/Admin/RoleManagement.jsx`

Add a warning banner after role creation:

```javascript
const handleCreateRole = useCallback(async () => {
  if (!createForm.name.trim() || !createForm.code.trim()) { 
    setCreateError('Name and Code are required.'); 
    return; 
  }
  
  setCreating(true); setCreateError(null);
  try {
    const res = await rbacService.createRole({
      name: createForm.name.trim(), 
      code: createForm.code.trim().toLowerCase().replace(/\s+/g,'_'),
      level: createForm.level, 
      description: createForm.description.trim(), 
      is_system_role: false, 
      is_active: true,
    });
    
    const role = res?.data ?? res;
    setRoles((prev) => [...prev, role]); 
    setShowCreate(false); 
    setCreateForm(EMPTY_FORM);
    
    // SOFT-CODED: Auto-select the new role and show modules tab
    setSelectedRole(role);
    setDetailTab('modules');
    
    // SOFT-CODED: Show warning about module assignment
    notify('success', `Role "${role.name}" created. IMPORTANT: Assign modules in the Modules tab below.`);
    notify('warning', 'Without modules, users assigned to this role will not see any features.', 8000);
    
  } catch (err) {
    setCreateError(err?.response?.data?.detail || 'Failed.');
  } finally { 
    setCreating(false); 
  }
}, [createForm, notify]);
```

---

## 🎯 Soft-Coded Configuration

All role and module behavior is centralized in:

**Backend:** `backend/apps/rbac/rbac_config.py`
```python
# Default modules for new roles (suggestion)
DEFAULT_CUSTOM_ROLE_MODULES = [
    'hr_self_service',  # Always give users access to their profile
]

# Minimum modules warning threshold
ROLE_MIN_MODULES_WARNING = 1  # Warn if role has < 1 module
```

**Frontend:** `frontend/src/config/rbacAccess.config.js`
```javascript
// Module access control mode
export const ACCESS_CONTROL_MODE = 'role_based';  // ← Enforces role-based access

// Custom role creation hints
export const CUSTOM_ROLE_CREATION_HINTS = {
  minModules: 1,
  warningMessage: 'Roles without modules cannot access any features',
  suggestedModules: ['hr_self_service'],  // Always recommend this
};
```

---

## 📋 Checklist for "Onboarding" Role Fix

### Immediate Actions (Preprod)

- [ ] 1. Login to preprod as super admin
- [ ] 2. Open `/admin/roles`
- [ ] 3. Select "Onboarding" role
- [ ] 4. Go to "Modules" tab
- [ ] 5. Assign these modules:
  - [ ] `hr_self_service` (Employee Self-Service Portal)
  - [ ] `hr_onboarding` (Onboarding/Offboarding)
  - [ ] Any other modules onboarding users need
- [ ] 6. Inform `lira.viaga@rejlers.ae` to:
  - [ ] Refresh browser (F5)
  - [ ] Or logout and login again
- [ ] 7. Test: Login as lira.viaga and verify features appear

### Long-term Improvements

- [ ] Add module count badge to role cards in UI
- [ ] Show warning icon for roles with 0 modules
- [ ] Auto-select "Modules" tab after role creation
- [ ] Add backend validation warning
- [ ] Update role creation modal with module hints
- [ ] Add role health check to admin dashboard

---

## 🔐 Cache Considerations

**Backend Cache (60 seconds):**
```python
# File: apps/rbac/models.py, line 429
cache.set(cache_key, modules, 60)  # ← Modules cached for 1 minute
```

**After assigning modules:**
- Backend cache clears automatically after 60 seconds
- OR user logout/login triggers cache refresh
- OR user refreshes browser → frontend refetches `/rbac/users/me/`

**Frontend Cache:**
```javascript
// File: components/Layout/Sidebar.jsx, line 152
const fetchUserModules = async () => {
  // Fetches fresh data on component mount
  const response = await fetch(`${API_BASE_URL}/rbac/users/me/`);
};
```

**To force immediate cache clear:**
```python
from django.core.cache import cache
profile_id = 'UUID-HERE'
cache.delete(f'user_modules_{profile_id}')
cache.delete(f'user_permissions_{profile_id}')
```

---

## 🎓 Summary

**Why features don't show:**
1. Custom role created ✓
2. Role assigned to user ✓  
3. **BUT: Role has ZERO modules** ✗
4. Backend returns empty modules array
5. Frontend hides all menu items

**Fix:** Assign modules to the role via `/admin/roles`

**Prevention:** Add UI warnings and validation to role creation flow

**Soft-coded:** All configuration in `rbac_config.py` and `rbacAccess.config.js`

---

## 📞 Need Help?

If the issue persists after assigning modules:

1. Check browser console (F12) for errors
2. Check backend logs: `docker logs radai_backend_local -f`
3. Verify user has the role: `SELECT * FROM rbac_user_roles WHERE user_profile_id = ...`
4. Verify role has modules: `SELECT * FROM rbac_role_modules WHERE role_id = ...`
5. Clear browser cache and cookies
6. Test in incognito/private window
