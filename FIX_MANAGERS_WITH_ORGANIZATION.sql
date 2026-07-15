-- ============================================================================
-- FIXED: Create Managers with Organization
-- ============================================================================
-- This version includes organization_id which is REQUIRED for UserProfile
-- ============================================================================

-- Step 1: Find the organization ID (most production setups have one organization)
-- Run this first to see what organization exists:
SELECT id, name, code FROM rbac_organizations ORDER BY created_at LIMIT 5;

-- ============================================================================
-- Step 2: Create Managers (UPDATE THE organization_id BELOW)
-- ============================================================================
-- IMPORTANT: Replace 'YOUR_ORG_ID_HERE' with the actual organization UUID from Step 1

DO $$
DECLARE
    org_id UUID;
    user1_id INTEGER;
    user2_id INTEGER;
    user3_id INTEGER;
BEGIN
    -- Get the organization ID (assumes there's only one, or pick the first active one)
    SELECT id INTO org_id FROM rbac_organizations WHERE is_active = true ORDER BY created_at LIMIT 1;
    
    IF org_id IS NULL THEN
        RAISE EXCEPTION 'No active organization found. Create an organization first.';
    END IF;
    
    RAISE NOTICE 'Using organization ID: %', org_id;
    
    -- ================================================================
    -- Manager 1: Rafat S. M. Saqer
    -- ================================================================
    INSERT INTO auth_user (username, email, first_name, last_name, is_active, is_staff, is_superuser, password, date_joined)
    VALUES ('rafat_sm_saqer', 'rafat.sm.saqer@rejlers.ae', 'Rafat', 'S. M. Saqer', true, false, false, '!', NOW())
    ON CONFLICT (email) DO UPDATE SET first_name = EXCLUDED.first_name, last_name = EXCLUDED.last_name, is_active = true
    RETURNING id INTO user1_id;
    
    -- Get user ID if conflict occurred
    IF user1_id IS NULL THEN
        SELECT id INTO user1_id FROM auth_user WHERE email = 'rafat.sm.saqer@rejlers.ae';
    END IF;
    
    INSERT INTO rbac_userprofile (user_id, organization_id, department, job_title, status, is_deleted, created_at, updated_at)
    VALUES (user1_id, org_id, 'radai', 'Manager', 'active', false, NOW(), NOW())
    ON CONFLICT (user_id) DO UPDATE SET 
        organization_id = org_id,
        department = 'radai', 
        job_title = 'Manager', 
        status = 'active',
        is_deleted = false, 
        updated_at = NOW();
    
    RAISE NOTICE '✅ Created/Updated: Rafat S. M. Saqer';
    
    -- ================================================================
    -- Manager 2: Anam Abbas
    -- ================================================================
    INSERT INTO auth_user (username, email, first_name, last_name, is_active, is_staff, is_superuser, password, date_joined)
    VALUES ('anam_abbas', 'anam.abbas@rejlers.ae', 'Anam', 'Abbas', true, false, false, '!', NOW())
    ON CONFLICT (email) DO UPDATE SET first_name = EXCLUDED.first_name, last_name = EXCLUDED.last_name, is_active = true
    RETURNING id INTO user2_id;
    
    IF user2_id IS NULL THEN
        SELECT id INTO user2_id FROM auth_user WHERE email = 'anam.abbas@rejlers.ae';
    END IF;
    
    INSERT INTO rbac_userprofile (user_id, organization_id, department, job_title, status, is_deleted, created_at, updated_at)
    VALUES (user2_id, org_id, 'radai', 'Manager', 'active', false, NOW(), NOW())
    ON CONFLICT (user_id) DO UPDATE SET 
        organization_id = org_id,
        department = 'radai', 
        job_title = 'Manager', 
        status = 'active',
        is_deleted = false, 
        updated_at = NOW();
    
    RAISE NOTICE '✅ Created/Updated: Anam Abbas';
    
    -- ================================================================
    -- Manager 3: Aleksi Murtomaki
    -- ================================================================
    INSERT INTO auth_user (username, email, first_name, last_name, is_active, is_staff, is_superuser, password, date_joined)
    VALUES ('aleksi_murtomaki', 'aleksi.murtomaki@rejlers.ae', 'Aleksi', 'Murtomaki', true, false, false, '!', NOW())
    ON CONFLICT (email) DO UPDATE SET first_name = EXCLUDED.first_name, last_name = EXCLUDED.last_name, is_active = true
    RETURNING id INTO user3_id;
    
    IF user3_id IS NULL THEN
        SELECT id INTO user3_id FROM auth_user WHERE email = 'aleksi.murtomaki@rejlers.ae';
    END IF;
    
    INSERT INTO rbac_userprofile (user_id, organization_id, department, job_title, status, is_deleted, created_at, updated_at)
    VALUES (user3_id, org_id, 'radai', 'Manager', 'active', false, NOW(), NOW())
    ON CONFLICT (user_id) DO UPDATE SET 
        organization_id = org_id,
        department = 'radai', 
        job_title = 'Manager', 
        status = 'active',
        is_deleted = false, 
        updated_at = NOW();
    
    RAISE NOTICE '✅ Created/Updated: Aleksi Murtomaki';
    
END $$;

-- ============================================================================
-- Step 3: Verify All Three Managers
-- ============================================================================
SELECT 
    u.email,
    u.first_name || ' ' || u.last_name AS full_name,
    u.is_active AS user_active,
    up.status AS profile_status,
    up.department,
    up.job_title,
    up.is_deleted,
    o.name AS organization,
    CASE 
        WHEN u.is_active AND up.status = 'active' AND NOT up.is_deleted THEN '✅ WILL SHOW IN DROPDOWN'
        WHEN NOT u.is_active THEN '❌ User inactive'
        WHEN up.status != 'active' THEN '❌ Profile not active'
        WHEN up.is_deleted THEN '❌ Profile deleted'
        ELSE '⚠️ Unknown issue'
    END AS visibility_status
FROM auth_user u
INNER JOIN rbac_userprofile up ON u.id = up.user_id
INNER JOIN rbac_organizations o ON up.organization_id = o.id
WHERE u.email IN (
    'rafat.sm.saqer@rejlers.ae',
    'anam.abbas@rejlers.ae',
    'aleksi.murtomaki@rejlers.ae'
)
ORDER BY u.email;
