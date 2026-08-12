# Onboarding Access Issue - Resolution Guide

## 🔍 Problem Statement

User `lira.viaga@rejlers.ae` cannot access **"4.3 Onboarding | Offboarding"** feature in preprod, even after creating an "Onboarding" role and assigning it.

**Frontend URL:** https://frontend-cyan-eta-q169h70uw0.vercel.app
**Module Code:** `hr_onboarding`
**Sidebar Path:** `/hr/onboarding`

---

## ✅ Configuration Fixed (Already Deployed)

### Issue #1: Frontend/Backend Config Mismatch ✓ FIXED

**Problem:**
- Backend: `SENSITIVE_MODULE_CODES = ['hr_management', 'payroll', 'timesheet', 'hr_onboarding']`
- Frontend: `SENSITIVE_MODULE_CODES = ['hr_management', 'payroll', 'timesheet']` ← Missing `hr_onboarding`

**Solution Applied:**
- ✓ Updated `frontend/src/config/rbacAccess.config.js` line 88
- ✓ Updated `frontend/src/pages/Admin/RoleManagement.jsx` line 47
- ✓ Added comment: "SOFT-CODED: Must match backend/apps/rbac/rbac_config.py"

---

## 🔧 Required Actions (PREPROD)

### Step 1: Verify Role Has Module Assigned

1. **Login to Preprod as Super Admin**
   - URL: https://frontend-cyan-eta-q169h70uw0.vercel.app/login
   - Must be `super_admin` role

2. **Open Role Management**
   - Navigate to: `/admin/roles`
   - Find the "Onboarding" role in the list
   - Click to select it

3. **Check Modules Tab**
   - Click the **"Modules"** tab
   - Look for: ☐ **hr_onboarding** (Onboarding | Offboarding)
   
4. **If Module NOT Checked:**
   ```
   ⚠️ THIS IS THE PROBLEM!
   
   → Toggle ON: hr_onboarding
   → Look for the checkbox next to "Onboarding | Offboarding"
   → Click to enable it
   → Module will be assigned immediately
   ```

5. **If Module IS Checked:**
   ```
   ✓ Module is assigned correctly
   → Problem is elsewhere (continue to Step 2)
   ```

### Step 2: Verify User Has Role Assigned

1. **Open User Management**
   - Navigate to: `/admin/users`
   - Search for: `lira.viaga@rejlers.ae`
   - Click "Edit" button

2. **Check Roles Section**
   - Look in the "Roles" dropdown
   - Verify "Onboarding" role is selected
   - If multiple roles, ensure "Onboarding" is checked

3. **If Role NOT Assigned:**
   ```
   ⚠️ THIS IS THE PROBLEM!
   
   → Select "Onboarding" from roles dropdown
   → Click "Save"
   → User must logout/login to see changes
   ```

### Step 3: Clear Cache & Refresh

1. **Instruct User to:**
   ```
   1. Logout from preprod
   2. Clear browser cache (Ctrl+Shift+Del)
   3. Login again
   4. Check if "4.3 Onboarding | Offboarding" appears in sidebar
   ```

2. **Backend Cache:**
   - User modules are cached for 60 seconds
   - Logout/login triggers fresh fetch
   - Or wait 1 minute and refresh page

---

## 🔬 Diagnostic Checks

### Frontend: Check User's Modules via Browser Console

```javascript
// Open browser console (F12) on preprod
// Run this code:

const API_BASE_URL = 'https://aiflowbackend-production.up.railway.app/api/v1';
const token = localStorage.getItem('radai_access_token');

fetch(`${API_BASE_URL}/rbac/users/me/`, {
  headers: {
    'Authorization': `Bearer ${token}`,
    'Content-Type': 'application/json'
  }
})
.then(r => r.json())
.then(data => {
  console.log('User Data:', data);
  console.log('User Modules:', data.modules);
  
  const hasOnboarding = data.modules?.some(m => m.code === 'hr_onboarding');
  console.log('Has hr_onboarding:', hasOnboarding);
  
  if (!hasOnboarding) {
    console.error('❌ User does NOT have hr_onboarding module!');
    console.log('User roles:', data.roles);
  } else {
    console.log('✓ User HAS hr_onboarding module');
  }
});
```

**Expected Output:**
```javascript
{
  "email": "lira.viaga@rejlers.ae",
  "roles": [
    {
      "code": "onboarding",  // or similar
      "name": "Onboarding"
    }
  ],
  "modules": [
    {
      "code": "hr_onboarding",  // ← MUST be present
      "name": "Onboarding | Offboarding"
    },
    // ... other modules
  ]
}
```

### Backend: Run Verification Script (Railway)

If you have access to Railway backend terminal:

```bash
# SSH into Railway backend container
railway run python verify_onboarding_access.py

# Or via Railway CLI
railway shell
python verify_onboarding_access.py
```

This script will:
- ✓ Check if user exists
- ✓ Check user's roles
- ✓ Check if hr_onboarding module exists
- ✓ Check if role has module assigned
- ✓ Check get_all_modules() result
- ✓ Provide specific fix instructions

---

## 🎯 Common Mistakes & Solutions

### Mistake 1: Wrong Module Code
**Problem:** Assigned `hr_management` instead of `hr_onboarding`

**How to Check:**
- The feature is "4.3 Onboarding | Offboarding"
- Requires module code: `hr_onboarding` (NOT `hr_management`)
- `hr_management` = "4.0 HR Dashboard"

**Solution:**
- In Role Management → Modules tab
- Toggle OFF: `hr_management` (if accidentally selected)
- Toggle ON: `hr_onboarding`

### Mistake 2: Role Created but No Modules Assigned
**Problem:** Created role but didn't assign any modules

**How to Check:**
- Role badge shows "0 modules" warning
- Modules tab is empty

**Solution:**
- Select the role
- Go to Modules tab
- Assign at least `hr_onboarding`
- Recommended: Also assign `hr_self_service` (My Profile)

### Mistake 3: Case Sensitivity in Role Code
**Problem:** Role code mismatch (e.g., "Onboarding" vs "onboarding")

**How to Check:**
- Check role.code in database vs API response
- Case matters in code matching

**Solution:**
- Use consistent lowercase codes
- When creating role, set code: `onboarding` (lowercase)

### Mistake 4: User Has Old Cache
**Problem:** User sees old menu without new role's modules

**How to Check:**
- API returns correct modules, but UI doesn't update
- Browser console shows old data

**Solution:**
- Hard refresh: Ctrl+F5
- Clear cache: Ctrl+Shift+Del
- Logout and login
- Try incognito window

---

## 📋 Step-by-Step Resolution Checklist

### In Preprod Admin Panel:

- [ ] 1. Login as Super Admin
- [ ] 2. Navigate to `/admin/roles`
- [ ] 3. Select "Onboarding" role
- [ ] 4. Go to "Modules" tab
- [ ] 5. Verify `hr_onboarding` is toggled ON
  - [ ] If OFF, toggle it ON
  - [ ] Save changes
- [ ] 6. Navigate to `/admin/users`
- [ ] 7. Search for `lira.viaga@rejlers.ae`
- [ ] 8. Click "Edit"
- [ ] 9. Verify "Onboarding" role is selected
  - [ ] If not selected, select it
  - [ ] Save changes
- [ ] 10. Instruct user to logout/login

### User Actions:

- [ ] 1. Logout from preprod
- [ ] 2. Clear browser cache
- [ ] 3. Login again
- [ ] 4. Navigate to dashboard
- [ ] 5. Check sidebar for "4. Human Resource" section
- [ ] 6. Look for "4.3 Onboarding | Offboarding"
- [ ] 7. Click to verify access

---

## 🚀 Deployment Status

**Changes Deployed:**
- ✓ Frontend: Configuration mismatch fixed (SENSITIVE_MODULE_CODES)
- ✓ Backend: Verification script added
- ✓ Docs: This resolution guide

**Branches:**
- ✓ Development: Committed and pushed
- ✓ Preprod: Merged (deploying now)
- ⏱ Main: Not deployed yet (only after preprod verification)

**Deployment Timeline:**
- Vercel preprod: ~2-3 minutes (may be live now)
- Railway backend: ~5 minutes (may be live now)

---

## 🆘 If Issue Persists

### Additional Checks:

1. **Check Backend Logs (Railway):**
   ```bash
   railway logs
   # Look for:
   # - Module access denials
   # - RBAC errors
   # - Cache issues
   ```

2. **Check Frontend Errors (Browser Console):**
   ```javascript
   // Look for:
   // - 403 Forbidden errors
   // - Module fetch errors
   // - RBAC check failures
   ```

3. **Verify Database Directly:**
   ```sql
   -- Check role exists
   SELECT * FROM rbac_roles WHERE code ILIKE '%onboarding%';
   
   -- Check modules assigned to role
   SELECT r.name, m.code, m.name 
   FROM rbac_roles r
   JOIN rbac_role_modules rm ON r.id = rm.role_id
   JOIN rbac_modules m ON rm.module_id = m.id
   WHERE r.code ILIKE '%onboarding%';
   
   -- Check user role assignment
   SELECT u.email, r.name, r.code
   FROM auth_user u
   JOIN rbac_userprofiles p ON u.id = p.user_id
   JOIN rbac_user_roles ur ON p.id = ur.user_profile_id
   JOIN rbac_roles r ON ur.role_id = r.id
   WHERE u.email = 'lira.viaga@rejlers.ae';
   ```

4. **Run Full Diagnostic:**
   ```bash
   # On Railway backend
   python verify_onboarding_access.py
   ```

---

## 📞 Summary

**Most Likely Cause:** The "Onboarding" role exists but `hr_onboarding` module is not assigned to it.

**Quick Fix:**
1. Go to `/admin/roles` in preprod
2. Select "Onboarding" role
3. Click "Modules" tab
4. Toggle ON: `hr_onboarding`
5. User logout/login

**Verification:**
- User should see "4.3 Onboarding | Offboarding" in sidebar
- Clicking it should open `/hr/onboarding` page
- No 403 or access denied errors

---

## 🎓 Prevention (Already Implemented)

**Future role creations will have:**
- ✓ Auto-select role after creation
- ✓ Auto-switch to Modules tab
- ✓ Warning notification about module assignment
- ✓ "0 modules" badge on roles without modules
- ✓ Warning icon and banner for empty roles

This prevents the same issue from happening again! 🚀
