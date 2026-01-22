# 🚀 Data Visibility Quick Reference Card

## 📌 Problem
Users can see other users' data despite RBAC ❌

## ✅ Solution  
Smart row-level security with team collaboration

---

## 🎯 Quick Implementation (3 Steps)

### Step 1: Add to imports
```python
from apps.rbac.data_visibility_mixin import TeamCollaborationMixin
```

### Step 2: Update ViewSet
```python
class YourViewSet(TeamCollaborationMixin, viewsets.ModelViewSet):
    visibility_module_code = 'your_module'  # Required
    visibility_owner_field = 'created_by'   # Optional
```

### Step 3: Done!
Automatic filtering applied ✅

---

## 📦 Available Mixins

| Mixin | Use Case | Example |
|-------|----------|---------|
| `TeamCollaborationMixin` | Team collaboration | QHSE, CRS, Finance |
| `PersonalDataMixin` | Personal data only | Notifications |
| `ProjectBasedMixin` | Project owner + members | Project Management |
| `CustomVisibilityMixin` | Complex scenarios | Custom logic |

---

## 🎨 Common Patterns

### Pattern 1: Team Sees Everything (QHSE)
```python
class QHSEViewSet(TeamCollaborationMixin, viewsets.ModelViewSet):
    visibility_module_code = 'qhse'
    # No owner_field - entire team sees all
```

### Pattern 2: Team + Personal Fallback (CRS)
```python
class CRSViewSet(TeamCollaborationMixin, viewsets.ModelViewSet):
    visibility_module_code = 'crs_documents'
    visibility_owner_field = 'uploaded_by'  # Non-members see own only
```

### Pattern 3: Personal Only (Notifications)
```python
class NotificationViewSet(PersonalDataMixin, viewsets.ModelViewSet):
    visibility_owner_field = 'recipient'
```

---

## 🔧 Helper Functions

```python
from apps.rbac.data_visibility_config import (
    is_admin_user,              # Check if admin
    user_has_module_access,     # Check module access
    get_users_with_module_access,  # Get team IDs
)

# Usage
if is_admin_user(request.user):
    # Admin access
    
if user_has_module_access(request.user, 'qhse'):
    # User has QHSE module

team_ids = get_users_with_module_access('qhse')  # List[int]
```

---

## 📊 Behavior Matrix

| User Type | Has Module? | Result |
|-----------|-------------|--------|
| Regular User | ✅ Yes | Sees ALL team data |
| Regular User | ❌ No | Sees only own data (if owner_field set) |
| Super Admin | N/A | Sees EVERYTHING |

---

## 🧪 Quick Test

```bash
# Test different user types
curl -H "Authorization: Bearer <token>" http://localhost:8000/api/v1/qhse/projects/

# Expected results:
# - QHSE user: Returns all projects
# - Non-QHSE user: Returns empty or own projects
# - Admin: Returns all projects
```

---

## 🎓 Key Attributes

```python
visibility_module_code = 'module_code'     # Required: RBAC module
visibility_owner_field = 'created_by'      # Optional: ownership field
visibility_logging = True                  # Optional: audit logs
visibility_bypass = False                  # Optional: disable (emergency)
```

---

## 🚨 Common Issues

### Issue: Users can't see anything
```python
# Check module assignment
user_has_module_access(user, 'qhse')  # Should return True
```

### Issue: Everyone sees everything
```python
# Check configuration
visibility_module_code = 'qhse'  # Must be set!
```

### Issue: Admins can't see all
```python
# Check admin role
is_admin_user(user)  # Should return True for admins
```

---

## 📁 Files Created

1. `data_visibility_config.py` - Configuration (350 lines)
2. `data_visibility_mixin.py` - Reusable mixins (400 lines)  
3. `DATA_VISIBILITY_GUIDE.md` - Full guide (800 lines)
4. `DATA_VISIBILITY_SUMMARY.md` - Summary (500 lines)

---

## ✅ Already Implemented

- ✅ CRS Documents (`apps/crs/views.py`)
- ✅ QHSE Projects (`apps/qhse/views.py`)

---

## 📝 To Implement

Remaining modules (copy-paste pattern):
- Finance Records
- PFD Converter
- PID Analysis
- Procurement
- File Storage
- DesignIQ
- Notifications

---

## 💡 Pro Tips

1. **Always set module code** - Required for proper filtering
2. **Use TeamCollaborationMixin** - For most department-based modules
3. **Enable logging** - For audit compliance
4. **Test with 3 user types** - Module user, non-module user, admin
5. **Optimize queries** - Use select_related/prefetch_related

---

## 📞 Get Help

- Full Guide: `DATA_VISIBILITY_GUIDE.md`
- Code: `data_visibility_config.py` and `data_visibility_mixin.py`
- Examples: See CRS and QHSE implementations

---

**Status:** ✅ Ready to Use  
**Time to Implement:** 2 minutes per ViewSet  
**Rollout:** 2-3 weeks for all modules
