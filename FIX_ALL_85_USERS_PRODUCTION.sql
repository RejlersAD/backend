-- ============================================================
-- COMPREHENSIVE PRODUCTION RBAC AUDIT & FIX
-- Find and Fix ALL 85+ Users with Permission Issues
-- ============================================================

-- ============================================================
-- STEP 1: COUNT TOTAL AFFECTED USERS
-- ============================================================

SELECT 
    COUNT(*) as total_affected_users,
    COUNT(*) FILTER (WHERE u.is_superuser = true) as superuser_count,
    COUNT(*) FILTER (WHERE u.is_staff = true AND u.is_superuser = false) as staff_only_count
FROM auth_user u
INNER JOIN rbac_userprofile up ON u.id = up.user_id
INNER JOIN rbac_userrole ur ON up.id = ur.user_profile_id
INNER JOIN rbac_role r ON ur.role_id = r.id
WHERE u.is_active = true  -- Include active users (you deactivated them)
  AND up.is_deleted = false
  AND r.is_active = true
  AND (u.is_superuser = true OR u.is_staff = true)
  AND r.code NOT IN ('super_admin', 'admin', 'ict_admin')
  AND u.email NOT IN ('mohammed.agra@rejlers.ae', 'fahad.hussein@rejlers.ae', 'tanzeem.agra@rejlers.ae');

-- Expected: ~85 users


-- ============================================================
-- STEP 2: LIST ALL AFFECTED USERS (DETAILED)
-- ============================================================

SELECT 
    u.email,
    u.first_name || ' ' || u.last_name AS full_name,
    u.is_superuser,
    u.is_staff,
    u.is_active,
    r.code AS rbac_role,
    r.name AS rbac_role_name,
    ur.is_primary,
    CASE 
        WHEN u.is_superuser = true THEN '🔴 CRITICAL: is_superuser bypass'
        WHEN u.is_staff = true THEN '🟡 WARNING: is_staff set'
    END AS issue_type,
    u.last_login::date as last_login,
    u.date_joined::date as date_joined
FROM auth_user u
INNER JOIN rbac_userprofile up ON u.id = up.user_id
INNER JOIN rbac_userrole ur ON up.id = ur.user_profile_id
INNER JOIN rbac_role r ON ur.role_id = r.id
WHERE u.is_active = true  -- Change to false to see deactivated users
  AND up.is_deleted = false
  AND r.is_active = true
  AND (u.is_superuser = true OR u.is_staff = true)
  AND r.code NOT IN ('super_admin', 'admin', 'ict_admin')
  AND u.email NOT IN ('mohammed.agra@rejlers.ae', 'fahad.hussein@rejlers.ae', 'tanzeem.agra@rejlers.ae')
ORDER BY 
    u.is_superuser DESC,
    u.is_staff DESC,
    u.last_login DESC NULLS LAST,
    u.email;

-- Copy the email list from results for the fix


-- ============================================================
-- STEP 3: CHECK DEACTIVATED USERS (The 85+ you deactivated)
-- ============================================================

SELECT 
    u.email,
    u.first_name || ' ' || u.last_name AS full_name,
    u.is_superuser,
    u.is_staff,
    u.is_active,
    r.code AS rbac_role,
    u.last_login::date as last_login
FROM auth_user u
INNER JOIN rbac_userprofile up ON u.id = up.user_id
INNER JOIN rbac_userrole ur ON up.id = ur.user_profile_id
INNER JOIN rbac_role r ON ur.role_id = r.id
WHERE u.is_active = false  -- These are the ones you deactivated
  AND up.is_deleted = false
  AND r.is_active = true
  AND (u.is_superuser = true OR u.is_staff = true)
  AND r.code NOT IN ('super_admin', 'admin', 'ict_admin')
  AND u.email NOT IN ('mohammed.agra@rejlers.ae', 'fahad.hussein@rejlers.ae', 'tanzeem.agra@rejlers.ae')
ORDER BY u.email;


-- ============================================================
-- STEP 4: FIX ALL USERS AT ONCE (Bulk Fix)
-- ============================================================

-- This fixes ALL active AND inactive users with the issue
-- They will have proper RBAC-only permissions after this

UPDATE auth_user u
SET 
    is_superuser = false,
    is_staff = false
FROM rbac_userprofile up, rbac_userrole ur, rbac_role r
WHERE u.id = up.user_id
  AND up.id = ur.user_profile_id
  AND ur.role_id = r.id
  -- No is_active filter, so it fixes both active AND deactivated users
  AND up.is_deleted = false
  AND r.is_active = true
  AND (u.is_superuser = true OR u.is_staff = true)
  AND r.code NOT IN ('super_admin', 'admin', 'ict_admin')
  AND u.email NOT IN ('mohammed.agra@rejlers.ae', 'fahad.hussein@rejlers.ae', 'tanzeem.agra@rejlers.ae');

-- Check how many rows were updated
-- Expected: ~85+ users


-- ============================================================
-- STEP 5: REACTIVATE USERS (After flags are fixed)
-- ============================================================

-- Now that Django flags are removed, users will only have RBAC permissions
-- It's safe to reactivate them

UPDATE auth_user u
SET is_active = true
FROM rbac_userprofile up, rbac_userrole ur, rbac_role r
WHERE u.id = up.user_id
  AND up.id = ur.user_profile_id
  AND ur.role_id = r.id
  AND u.is_active = false  -- Currently deactivated
  AND up.is_deleted = false
  AND r.is_active = true
  AND u.is_superuser = false  -- Verified flags are removed
  AND u.is_staff = false
  AND r.code NOT IN ('super_admin', 'admin', 'ict_admin')
  AND u.email NOT IN ('mohammed.agra@rejlers.ae', 'fahad.hussein@rejlers.ae', 'tanzeem.agra@rejlers.ae');

-- Expected: ~85+ users reactivated


-- ============================================================
-- STEP 6: VERIFY FIX - Should Return 0 Rows
-- ============================================================

SELECT 
    COUNT(*) as remaining_issues,
    COUNT(*) FILTER (WHERE u.is_superuser = true) as superuser_issues,
    COUNT(*) FILTER (WHERE u.is_staff = true) as staff_issues
FROM auth_user u
INNER JOIN rbac_userprofile up ON u.id = up.user_id
INNER JOIN rbac_userrole ur ON up.id = ur.user_profile_id
INNER JOIN rbac_role r ON ur.role_id = r.id
WHERE u.is_active = true
  AND up.is_deleted = false
  AND r.is_active = true
  AND (u.is_superuser = true OR u.is_staff = true)
  AND r.code NOT IN ('super_admin', 'admin', 'ict_admin')
  AND u.email NOT IN ('mohammed.agra@rejlers.ae', 'fahad.hussein@rejlers.ae', 'tanzeem.agra@rejlers.ae');

-- Expected Result:
-- remaining_issues: 0
-- superuser_issues: 0  
-- staff_issues: 0


-- ============================================================
-- STEP 7: VALIDATE SPECIFIC USER (ravikumar.naickar@rejlers.ae)
-- ============================================================

SELECT 
    u.email,
    u.first_name,
    u.last_name,
    u.is_superuser AS django_superuser_flag,
    u.is_staff AS django_staff_flag,
    u.is_active,
    r.code AS rbac_role,
    r.name AS rbac_role_name,
    ur.is_primary,
    CASE 
        WHEN u.is_superuser = false AND u.is_staff = false THEN '✅ FIXED - RBAC only'
        ELSE '❌ STILL HAS DJANGO FLAGS'
    END AS status
FROM auth_user u
INNER JOIN rbac_userprofile up ON u.id = up.user_id
INNER JOIN rbac_userrole ur ON up.id = ur.user_profile_id
INNER JOIN rbac_role r ON ur.role_id = r.id
WHERE u.email = 'ravikumar.naickar@rejlers.ae'
  AND up.is_deleted = false;

-- Expected After Fix:
-- django_superuser_flag: false
-- django_staff_flag: false
-- rbac_role: default (or whatever role they should have)
-- status: ✅ FIXED - RBAC only


-- ============================================================
-- STEP 8: VERIFY ALL ROLE DEFINITIONS
-- ============================================================

-- Check what roles exist in the system
SELECT 
    code AS role_code,
    name AS role_name,
    description,
    level,
    is_active,
    created_at::date
FROM rbac_role
WHERE is_active = true
ORDER BY level DESC, code;

-- Expected roles from https://www.radai.ae/admin/roles:
-- super_admin, admin, ict_admin, manager, engineer, finance, hr_admin, default, etc.


-- ============================================================
-- STEP 9: USER COUNT PER ROLE (Sanity Check)
-- ============================================================

SELECT 
    r.code AS role_code,
    r.name AS role_name,
    COUNT(DISTINCT u.id) as user_count,
    COUNT(DISTINCT u.id) FILTER (WHERE u.is_active = true) as active_users,
    COUNT(DISTINCT u.id) FILTER (WHERE u.is_superuser = true OR u.is_staff = true) as users_with_django_flags
FROM rbac_role r
LEFT JOIN rbac_userrole ur ON r.id = ur.role_id
LEFT JOIN rbac_userprofile up ON ur.user_profile_id = up.id
LEFT JOIN auth_user u ON up.user_id = u.id
WHERE r.is_active = true
  AND (up.is_deleted = false OR up.is_deleted IS NULL)
GROUP BY r.code, r.name, r.level
ORDER BY r.level DESC, user_count DESC;

-- This shows distribution of users across roles
-- Users with Django flags should ONLY be in super_admin/admin/ict_admin roles


-- ============================================================
-- STEP 10: FINAL AUDIT - Who Should Have Django Flags
-- ============================================================

-- These users SHOULD have is_staff or is_superuser
SELECT 
    u.email,
    u.is_superuser,
    u.is_staff,
    r.code AS rbac_role,
    'Authorized' AS status
FROM auth_user u
INNER JOIN rbac_userprofile up ON u.id = up.user_id
INNER JOIN rbac_userrole ur ON up.id = ur.user_profile_id
INNER JOIN rbac_role r ON ur.role_id = r.id
WHERE u.is_active = true
  AND up.is_deleted = false
  AND r.is_active = true
  AND r.code IN ('super_admin', 'admin', 'ict_admin')
ORDER BY r.code, u.email;

-- Expected: Only 2-5 users (system administrators)
-- mohammed.agra@rejlers.ae, fahad.hussein@rejlers.ae, etc.


-- ============================================================
-- NOTES
-- ============================================================

-- WHY THIS FIXES THE PROBLEM:
-- 1. Removes is_superuser and is_staff flags from 85+ users
-- 2. Now they only have RBAC permissions based on their assigned role
-- 3. Even if you change their RBAC role, Django flags won't bypass it
-- 4. Safe to reactivate users after fix

-- EXECUTION ORDER:
-- 1. Run STEP 1 & 2 to identify all affected users
-- 2. Run STEP 4 to fix Django flags (bulk update)
-- 3. Run STEP 5 to reactivate users (optional, after verification)
-- 4. Run STEP 6 to verify fix worked
-- 5. Run STEP 7 to test specific user
-- 6. Run STEP 8-10 for final validation

-- AFTER THIS FIX:
-- ✅ Users will only have access based on their RBAC role
-- ✅ Changing RBAC role will immediately affect their permissions
-- ✅ No more Django flag bypass
-- ✅ System administrators (super_admin, admin, ict_admin) remain unchanged
