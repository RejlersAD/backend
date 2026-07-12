-- ============================================================
-- CHECK PRODUCTION DATABASE FOR CUSTOM ROLE ISSUES
-- Users: kiran.ingale@rejlers.ae, ravikumar.naickar@rejlers.ae
-- ============================================================

-- QUICK CHECK: Django Flags Status
SELECT 
    email,
    is_superuser,
    is_staff,
    is_active,
    CASE 
        WHEN is_superuser = true THEN '🔴 HAS SUPERUSER FLAG'
        WHEN is_staff = true THEN '🟡 HAS STAFF FLAG'
        ELSE '✅ NO FLAGS'
    END AS flag_status,
    date_joined,
    last_login
FROM auth_user
WHERE email IN (
    'kiran.ingale@rejlers.ae',
    'ravikumar.naickar@rejlers.ae'
);

-- Expected: is_superuser = false, is_staff = false


-- RBAC ROLE CHECK
SELECT 
    u.email,
    r.name AS role_name,
    r.code AS role_code,
    ur.is_primary,
    CASE 
        WHEN u.is_superuser = true AND r.code NOT IN ('super_admin', 'admin', 'ict_admin') 
            THEN '🔴 CRITICAL: Has superuser without admin role'
        WHEN u.is_staff = true AND r.code NOT IN ('super_admin', 'admin', 'ict_admin') 
            THEN '🟡 WARNING: Has staff without admin role'
        ELSE '✅ OK'
    END AS issue_status
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
ORDER BY u.email;


-- COMPREHENSIVE AUDIT: Find ALL users with similar issues in production
SELECT 
    u.email,
    u.first_name,
    u.last_name,
    u.is_superuser,
    u.is_staff,
    r.code AS rbac_role,
    ur.is_primary,
    CASE 
        WHEN u.is_superuser = true AND r.code NOT IN ('super_admin', 'admin', 'ict_admin') 
            THEN '🔴 CRITICAL'
        WHEN u.is_staff = true AND r.code NOT IN ('super_admin', 'admin', 'ict_admin') 
            THEN '🟡 WARNING'
        ELSE '✅ OK'
    END AS status,
    u.last_login
FROM auth_user u
INNER JOIN rbac_userprofile up ON u.id = up.user_id
INNER JOIN rbac_userrole ur ON up.id = ur.user_profile_id
INNER JOIN rbac_role r ON ur.role_id = r.id
WHERE u.is_active = true
  AND up.is_deleted = false
  AND r.is_active = true
  AND (u.is_superuser = true OR u.is_staff = true)
  AND r.code NOT IN ('super_admin', 'admin', 'ict_admin')
ORDER BY 
    u.is_superuser DESC, 
    u.is_staff DESC, 
    u.email;
