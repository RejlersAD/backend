-- ============================================================
-- FIX USER PERMISSIONS FOR DEBASIS.SANA@REJLERS.AE
-- Issue: User has super admin access despite Default role
-- Root Cause: is_superuser or is_staff Django flags set to True
-- ============================================================

-- STEP 1: CHECK CURRENT STATE
-- Run this first to see what flags are set
SELECT 
    email,
    first_name,
    last_name,
    is_superuser,
    is_staff,
    is_active,
    date_joined,
    last_login
FROM auth_user
WHERE email = 'Debasis.Sana@rejlers.ae';

-- Expected problem: is_superuser = true OR is_staff = true
-- This bypasses ALL RBAC permission checks!


-- STEP 2: CHECK RBAC ROLE ASSIGNMENT
-- Verify user's RBAC roles
SELECT 
    u.email,
    r.name AS role_name,
    r.code AS role_code,
    ur.is_primary,
    ur.is_active,
    ur.created_at
FROM auth_user u
INNER JOIN rbac_userprofile up ON u.id = up.user_id
INNER JOIN rbac_userrole ur ON up.id = ur.user_profile_id
INNER JOIN rbac_role r ON ur.role_id = r.id
WHERE u.email = 'Debasis.Sana@rejlers.ae'
  AND ur.is_active = true
  AND up.is_deleted = false;

-- Expected result: role_code = 'default' (NOT super_admin or admin)


-- STEP 3: FIX THE ISSUE (REMOVE DJANGO FLAGS)
-- This removes the superuser and staff flags
-- IMPORTANT: Run this ONLY after confirming Step 1 shows the problem
UPDATE auth_user
SET 
    is_superuser = false,
    is_staff = false
WHERE email = 'Debasis.Sana@rejlers.ae';

-- Verify the fix was applied:
SELECT 
    email,
    is_superuser,
    is_staff,
    is_active
FROM auth_user
WHERE email = 'Debasis.Sana@rejlers.ae';

-- Expected after fix:
-- is_superuser = false
-- is_staff = false
-- is_active = true


-- ============================================================
-- COMPREHENSIVE AUDIT: FIND OTHER USERS WITH SIMILAR ISSUES
-- ============================================================

-- Find all users with Django superuser/staff flags
-- who have non-admin RBAC roles (potential security issues)
SELECT 
    u.email,
    u.first_name,
    u.last_name,
    u.is_superuser AS django_superuser,
    u.is_staff AS django_staff,
    r.code AS rbac_role,
    ur.is_primary,
    CASE 
        WHEN u.is_superuser = true THEN 'CRITICAL: Has superuser flag'
        WHEN u.is_staff = true AND r.code NOT IN ('super_admin', 'admin', 'ict_admin') THEN 'WARNING: Has staff flag but not admin role'
        ELSE 'OK'
    END AS issue
FROM auth_user u
INNER JOIN rbac_userprofile up ON u.id = up.user_id
INNER JOIN rbac_userrole ur ON up.id = ur.user_profile_id
INNER JOIN rbac_role r ON ur.role_id = r.id
WHERE u.is_active = true
  AND up.is_deleted = false
  AND ur.is_active = true
  AND (u.is_superuser = true OR u.is_staff = true)
ORDER BY u.is_superuser DESC, u.is_staff DESC, u.email;


-- ============================================================
-- EXPECTED USERS WITH DJANGO FLAGS (AUTHORIZED)
-- ============================================================
-- These users SHOULD have is_superuser=true or is_staff=true:
-- 1. tanzeem.agra@rejlers.ae (super_admin role)
-- 2. mohammed.agra@rejlers.ae (super_admin or ict_admin role)
-- 3. Any user with super_admin, admin, or ict_admin RBAC role
--
-- All others (including Default role users) should have:
-- is_superuser = false
-- is_staff = false
-- ============================================================
