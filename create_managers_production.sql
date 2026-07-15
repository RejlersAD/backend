-- ============================================================================
-- CREATE MISSING MANAGERS IN PRODUCTION DATABASE
-- ============================================================================
-- Run this SQL in Railway to create the three missing managers
-- 
-- Method 1 (Railway CLI):
--   railway run psql $DATABASE_URL -f create_managers_production.sql
-- 
-- Method 2 (Railway Web Console):
--   Copy and paste this SQL into Railway Data tab
-- ============================================================================

-- Transaction for safety
BEGIN;

-- ============================================================================
-- STEP 1: Create User Records
-- ============================================================================

-- Manager 1: Rafat S. M. Saqer
INSERT INTO auth_user (
    username, 
    email, 
    first_name, 
    last_name, 
    is_active, 
    is_staff, 
    is_superuser,
    password,
    date_joined
)
VALUES (
    'rafat_sm_saqer',
    'rafat.sm.saqer@rejlers.ae',
    'Rafat',
    'S. M. Saqer',
    true,
    false,
    false,
    '!',  -- Unusable password (user must reset)
    NOW()
)
ON CONFLICT (email) DO UPDATE SET
    first_name = EXCLUDED.first_name,
    last_name = EXCLUDED.last_name,
    is_active = true;

-- Manager 2: Anam Abbas
INSERT INTO auth_user (
    username, 
    email, 
    first_name, 
    last_name, 
    is_active, 
    is_staff, 
    is_superuser,
    password,
    date_joined
)
VALUES (
    'anam_abbas',
    'anam.abbas@rejlers.ae',
    'Anam',
    'Abbas',
    true,
    false,
    false,
    '!',  -- Unusable password
    NOW()
)
ON CONFLICT (email) DO UPDATE SET
    first_name = EXCLUDED.first_name,
    last_name = EXCLUDED.last_name,
    is_active = true;

-- Manager 3: Aleksi Murtomaki
INSERT INTO auth_user (
    username, 
    email, 
    first_name, 
    last_name, 
    is_active, 
    is_staff, 
    is_superuser,
    password,
    date_joined
)
VALUES (
    'aleksi_murtomaki',
    'aleksi.murtomaki@rejlers.ae',
    'Aleksi',
    'Murtomaki',
    true,
    false,
    false,
    '!',  -- Unusable password
    NOW()
)
ON CONFLICT (email) DO UPDATE SET
    first_name = EXCLUDED.first_name,
    last_name = EXCLUDED.last_name,
    is_active = true;

-- ============================================================================
-- STEP 2: Create UserProfile Records with RadAI Department
-- ============================================================================

-- Profile 1: Rafat S. M. Saqer
INSERT INTO rbac_userprofile (
    user_id,
    department,
    job_title,
    is_deleted,
    created_at,
    updated_at
)
SELECT 
    id,
    'radai',
    'Manager',
    false,
    NOW(),
    NOW()
FROM auth_user
WHERE email = 'rafat.sm.saqer@rejlers.ae'
ON CONFLICT (user_id) DO UPDATE SET
    department = 'radai',
    job_title = 'Manager',
    is_deleted = false,
    updated_at = NOW();

-- Profile 2: Anam Abbas
INSERT INTO rbac_userprofile (
    user_id,
    department,
    job_title,
    is_deleted,
    created_at,
    updated_at
)
SELECT 
    id,
    'radai',
    'Manager',
    false,
    NOW(),
    NOW()
FROM auth_user
WHERE email = 'anam.abbas@rejlers.ae'
ON CONFLICT (user_id) DO UPDATE SET
    department = 'radai',
    job_title = 'Manager',
    is_deleted = false,
    updated_at = NOW();

-- Profile 3: Aleksi Murtomaki
INSERT INTO rbac_userprofile (
    user_id,
    department,
    job_title,
    is_deleted,
    created_at,
    updated_at
)
SELECT 
    id,
    'radai',
    'Manager',
    false,
    NOW(),
    NOW()
FROM auth_user
WHERE email = 'aleksi.murtomaki@rejlers.ae'
ON CONFLICT (user_id) DO UPDATE SET
    department = 'radai',
    job_title = 'Manager',
    is_deleted = false,
    updated_at = NOW();

-- ============================================================================
-- STEP 3: Verification
-- ============================================================================

-- Show created/updated managers
SELECT 
    '✅ VERIFICATION - Managers Created' AS status;

SELECT 
    u.email,
    u.first_name || ' ' || u.last_name AS full_name,
    u.is_active AS active,
    up.department,
    up.job_title,
    CASE 
        WHEN u.is_active AND NOT up.is_deleted THEN '✅ VISIBLE IN DROPDOWN'
        ELSE '❌ WILL NOT SHOW'
    END AS dropdown_status
FROM auth_user u
INNER JOIN rbac_userprofile up ON u.id = up.user_id
WHERE u.email IN (
    'rafat.sm.saqer@rejlers.ae',
    'anam.abbas@rejlers.ae',
    'aleksi.murtomaki@rejlers.ae'
)
ORDER BY u.email;

-- Commit the transaction
COMMIT;

-- ============================================================================
-- SUCCESS MESSAGE
-- ============================================================================
SELECT '🎉 SUCCESS! Three managers created with RadAI department!' AS message;
SELECT 'Go to https://www.radai.ae/profile and check "Reporting Manager" dropdown' AS next_step;
SELECT 'You may need to clear browser cache (Ctrl+Shift+R)' AS tip;
