-- ============================================================
-- FIX CUSTOM ROLE PERMISSIONS ISSUE (MULTIPLE USERS)
-- ============================================================
-- Issue: Users have is_superuser or is_staff flags set to True
--        which bypasses all RBAC permission checks
-- Solution: Set flags to False for users without admin RBAC roles
-- ============================================================

-- CONFIGURATION: Users to fix
-- Add or remove email addresses as needed
-- These users should have is_superuser=False and is_staff=False

-- Target users for this fix:
--   - kiran.ingale@rejlers.ae
--   - ravikumar.naickar@rejlers.ae

-- ============================================================
-- STEP 1: CHECK CURRENT STATE OF ALL TARGET USERS
-- ============================================================

SELECT 
    u.email,
    u.first_name,
    u.last_name,
    u.is_superuser AS django_superuser,
    u.is_staff AS django_staff,
    u.is_active,
    r.code AS rbac_role,
    ur.is_primary,
    CASE 
        WHEN u.is_superuser = true AND r.code NOT IN ('super_admin', 'admin', 'ict_admin') 
            THEN '🔴 CRITICAL: Has superuser flag without admin role'
        WHEN u.is_staff = true AND r.code NOT IN ('super_admin', 'admin', 'ict_admin') 
            THEN '🟡 WARNING: Has staff flag without admin role'
        WHEN u.is_superuser = false AND u.is_staff = false 
            THEN '✅ OK: No Django flags'
        ELSE '✅ OK: Has admin role'
    END AS status
FROM auth_user u
INNER JOIN rbac_userprofile up ON u.id = up.user_id
INNER JOIN rbac_userrole ur ON up.id = ur.user_profile_id
INNER JOIN rbac_role r ON ur.role_id = r.id
WHERE u.email IN (
    'kiran.ingale@rejlers.ae',
    'ravikumar.naickar@rejlers.ae'
)
AND u.is_active = true
AND up.is_deleted = false
AND r.is_active = true
ORDER BY u.email;

-- Expected problem: Users with 🔴 or 🟡 status need fixing


-- ============================================================
-- STEP 2: DETAILED CHECK OF EACH USER'S RBAC ROLES
-- ============================================================

-- This shows all roles assigned to target users
SELECT 
    u.email,
    r.name AS role_name,
    r.code AS role_code,
    ur.is_primary,
    r.is_active AS role_is_active,
    ur.created_at
FROM auth_user u
INNER JOIN rbac_userprofile up ON u.id = up.user_id
INNER JOIN rbac_userrole ur ON up.id = ur.user_profile_id
INNER JOIN rbac_role r ON ur.role_id = r.id
WHERE u.email IN (
    'kiran.ingale@rejlers.ae',
    'ravikumar.naickar@rejlers.ae'
)
AND up.is_deleted = false
AND r.is_active = true
ORDER BY u.email, ur.is_primary DESC, r.code;

-- Expected: Users should have non-admin roles (e.g., 'default', 'engineer', etc.)
-- If they have 'super_admin', 'admin', or 'ict_admin', they SHOULD have Django flags


-- ============================================================
-- STEP 3: APPLY FIX (REMOVE DJANGO FLAGS)
-- ============================================================

-- ⚠️  WARNING: This will modify the database!
-- ⚠️  Only run this after confirming Step 1 shows users need fixing
-- ⚠️  Users must logout and login again after this change

UPDATE auth_user
SET 
    is_superuser = false,
    is_staff = false
WHERE email IN (
    'kiran.ingale@rejlers.ae',
    'ravikumar.naickar@rejlers.ae'
)
-- Safety check: only update if flags are currently set
AND (is_superuser = true OR is_staff = true);

-- Check how many rows were updated
-- Expected: 1-2 rows (one per user that had flags set)


-- ============================================================
-- STEP 4: VERIFY FIX WAS APPLIED
-- ============================================================

SELECT 
    email,
    is_superuser,
    is_staff,
    is_active,
    CASE 
        WHEN is_superuser = false AND is_staff = false THEN '✅ FIXED'
        ELSE '❌ STILL HAS FLAGS'
    END AS verification_status
FROM auth_user
WHERE email IN (
    'kiran.ingale@rejlers.ae',
    'ravikumar.naickar@rejlers.ae'
);

-- Expected after fix:
--   is_superuser = false
--   is_staff = false
--   is_active = true
--   verification_status = '✅ FIXED'


-- ============================================================
-- STEP 5: COMPREHENSIVE AUDIT (FIND OTHER SIMILAR ISSUES)
-- ============================================================

-- This query finds ALL users who might have the same problem
-- Use this to identify if more users need fixing

SELECT 
    u.email,
    u.first_name,
    u.last_name,
    u.is_superuser AS django_superuser,
    u.is_staff AS django_staff,
    r.code AS rbac_role,
    ur.is_primary,
    u.date_joined,
    u.last_login,
    CASE 
        WHEN u.is_superuser = true AND r.code NOT IN ('super_admin', 'admin', 'ict_admin') 
            THEN '🔴 CRITICAL: Superuser without admin role'
        WHEN u.is_staff = true AND r.code NOT IN ('super_admin', 'admin', 'ict_admin') 
            THEN '🟡 WARNING: Staff without admin role'
        ELSE '✅ OK'
    END AS issue_status
FROM auth_user u
INNER JOIN rbac_userprofile up ON u.id = up.user_id
INNER JOIN rbac_userrole ur ON up.id = ur.user_profile_id
INNER JOIN rbac_role r ON ur.role_id = r.id
WHERE u.is_active = true
  AND up.is_deleted = false
  AND r.is_active = true
  AND (u.is_superuser = true OR u.is_staff = true)
  -- Exclude authorized admin users
  AND r.code NOT IN ('super_admin', 'admin', 'ict_admin')
ORDER BY 
    u.is_superuser DESC, 
    u.is_staff DESC, 
    u.email;

-- This will show any other users who have the same issue
-- Add their emails to the target list in Step 3 if needed


-- ============================================================
-- NOTES
-- ============================================================

-- AUTHORIZED USERS WITH DJANGO FLAGS:
-- These users SHOULD have is_staff=True or is_superuser=True:
--   - mohammed.agra@rejlers.ae (super_admin)
--   - fahad.hussein@rejlers.ae (ict_admin)
--   - Any user with rbac_role = 'super_admin', 'admin', or 'ict_admin'

-- WHY THIS ISSUE HAPPENS:
-- Django User model has TWO permission systems:
--   1. Django Flags (is_superuser, is_staff) - built-in
--   2. RBAC Roles (custom role-based access control)
-- 
-- The permission classes check Django flags FIRST:
--   if request.user.is_superuser:
--       return True  # BYPASSES ALL RBAC CHECKS
--
-- So even if a user has role='default', if is_superuser=True,
-- they get full admin access!

-- HOW TO USE THIS FILE:
-- Option 1: Railway Dashboard
--   1. Go to railway.app > your project > Postgres > Data tab
--   2. Copy and paste each section (Step 1, Step 2, etc.)
--   3. Run queries one at a time
--
-- Option 2: pgAdmin or DBeaver
--   1. Connect to Railway Postgres database
--   2. Open new query window
--   3. Copy and paste, run section by section
--
-- Option 3: Django dbshell
--   1. SSH into Railway or run locally
--   2. python manage.py dbshell
--   3. Copy and paste queries

-- AFTER APPLYING FIX:
-- 1. Users MUST logout from https://www.radai.ae
-- 2. Users MUST clear browser cache/cookies (or use incognito)
-- 3. Users login again - they will now have correct permissions
-- 4. Test that they can only access features allowed by their RBAC role
