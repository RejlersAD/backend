-- ============================================================================
-- VERIFY MISSING MANAGERS IN PRODUCTION DATABASE
-- ============================================================================
-- Run this in Railway Data tab to check if the three managers exist
-- URL: https://railway.app/dashboard → Select Project → Postgres → Data tab
-- ============================================================================

-- QUERY 1: Check if managers exist
-- Expected: 0 rows before script, 3 rows after script
SELECT 
    u.email,
    u.first_name,
    u.last_name,
    u.is_active,
    u.date_joined,
    CASE 
        WHEN up.id IS NULL THEN '❌ NO PROFILE'
        ELSE '✅ HAS PROFILE'
    END AS profile_status
FROM auth_user u
LEFT JOIN rbac_userprofile up ON u.id = up.user_id
WHERE u.email IN (
    'rafat.sm.saqer@rejlers.ae',
    'anam.abbas@rejlers.ae',
    'aleksi.murtomaki@rejlers.ae'
)
ORDER BY u.email;

-- ============================================================================
-- QUERY 2: Check full profile details (if users exist)
-- Expected: Shows department and job_title
SELECT 
    u.email,
    u.first_name || ' ' || u.last_name AS full_name,
    u.is_active,
    up.department,
    up.job_title,
    up.is_deleted,
    up.created_at
FROM auth_user u
INNER JOIN rbac_userprofile up ON u.id = up.user_id
WHERE u.email IN (
    'rafat.sm.saqer@rejlers.ae',
    'anam.abbas@rejlers.ae',
    'aleksi.murtomaki@rejlers.ae'
)
ORDER BY u.email;

-- ============================================================================
-- QUERY 3: Check ALL RadAI department users
-- Expected: Shows all users with department='radai'
SELECT 
    u.email,
    u.first_name || ' ' || u.last_name AS full_name,
    up.department,
    up.job_title,
    u.is_active,
    CASE 
        WHEN u.is_active = true AND up.is_deleted = false THEN '✅ VISIBLE IN DROPDOWN'
        WHEN u.is_active = false THEN '❌ INACTIVE USER'
        WHEN up.is_deleted = true THEN '❌ DELETED PROFILE'
        ELSE '⚠️ UNKNOWN STATUS'
    END AS dropdown_status
FROM auth_user u
INNER JOIN rbac_userprofile up ON u.id = up.user_id
WHERE up.department = 'radai'
ORDER BY u.email;

-- ============================================================================
-- QUERY 4: Check all available managers (same as API)
-- This simulates what the Profile dropdown should show
-- Expected: All active users with profiles
SELECT 
    u.email,
    u.first_name || ' ' || u.last_name AS display_name,
    up.job_title,
    up.department,
    u.is_active
FROM auth_user u
INNER JOIN rbac_userprofile up ON u.id = up.user_id
WHERE 
    u.is_active = true 
    AND up.is_deleted = false
    AND up.department IS NOT NULL
ORDER BY u.email;

-- ============================================================================
-- EXPECTED RESULTS INTERPRETATION
-- ============================================================================
-- 
-- BEFORE running railway_add_radai_department_managers.py:
-- ---------------------------------------------------------
-- Query 1: 0 rows (managers don't exist)
-- Query 2: 0 rows (no profiles)
-- Query 3: 0 rows (no RadAI users yet)
-- Query 4: Should show existing managers (but not the 3 new ones)
--
-- AFTER running railway_add_radai_department_managers.py:
-- --------------------------------------------------------
-- Query 1: 3 rows with ✅ HAS PROFILE
-- Query 2: 3 rows with department='radai', job_title='Manager'
-- Query 3: At least 3 rows (the new managers + any existing RadAI users)
-- Query 4: Should include the 3 new managers in the list
--
-- ============================================================================
-- QUICK FIX IF USERS EXIST BUT NOT SHOWING IN DROPDOWN
-- ============================================================================
-- 
-- If users exist but are not showing, check:
--
-- 1. Are they active?
UPDATE auth_user 
SET is_active = true 
WHERE email IN (
    'rafat.sm.saqer@rejlers.ae',
    'anam.abbas@rejlers.ae',
    'aleksi.murtomaki@rejlers.ae'
);

-- 2. Are profiles not deleted?
UPDATE rbac_userprofile 
SET is_deleted = false 
WHERE user_id IN (
    SELECT id FROM auth_user WHERE email IN (
        'rafat.sm.saqer@rejlers.ae',
        'anam.abbas@rejlers.ae',
        'aleksi.murtomaki@rejlers.ae'
    )
);

-- 3. Do they have department and job_title?
UPDATE rbac_userprofile 
SET 
    department = 'radai',
    job_title = 'Manager'
WHERE user_id IN (
    SELECT id FROM auth_user WHERE email IN (
        'rafat.sm.saqer@rejlers.ae',
        'anam.abbas@rejlers.ae',
        'aleksi.murtomaki@rejlers.ae'
    )
);

-- ============================================================================
-- VERIFY FIX
-- ============================================================================
-- After running fixes, re-run Query 2 to verify all fields are correct
