-- ============================================================
-- EXECUTE THIS IN RAILWAY DATA TAB
-- Find ALL users with permission issues
-- ============================================================

SELECT 
    u.email,
    u.first_name || ' ' || u.last_name AS name,
    u.is_superuser,
    u.is_staff,
    r.code AS rbac_role,
    CASE 
        WHEN u.is_superuser = true THEN '🔴 CRITICAL'
        WHEN u.is_staff = true THEN '🟡 WARNING'
    END AS severity,
    u.last_login::date as last_login
FROM auth_user u
INNER JOIN rbac_userprofile up ON u.id = up.user_id
INNER JOIN rbac_userrole ur ON up.id = ur.user_profile_id
INNER JOIN rbac_role r ON ur.role_id = r.id
WHERE u.is_active = true
  AND up.is_deleted = false
  AND r.is_active = true
  AND (u.is_superuser = true OR u.is_staff = true)
  AND r.code NOT IN ('super_admin', 'admin', 'ict_admin')
  AND u.email NOT IN ('mohammed.agra@rejlers.ae', 'fahad.hussein@rejlers.ae', 'tanzeem.agra@rejlers.ae')
ORDER BY u.is_superuser DESC, u.is_staff DESC, u.email;


-- ============================================================
-- IF ISSUES FOUND, RUN THIS TO FIX ALL AT ONCE:
-- ============================================================

UPDATE auth_user u
SET is_superuser = false, is_staff = false
FROM rbac_userprofile up, rbac_userrole ur, rbac_role r
WHERE u.id = up.user_id
  AND up.id = ur.user_profile_id
  AND ur.role_id = r.id
  AND u.is_active = true
  AND up.is_deleted = false
  AND r.is_active = true
  AND (u.is_superuser = true OR u.is_staff = true)
  AND r.code NOT IN ('super_admin', 'admin', 'ict_admin')
  AND u.email NOT IN ('mohammed.agra@rejlers.ae', 'fahad.hussein@rejlers.ae', 'tanzeem.agra@rejlers.ae');


-- ============================================================
-- VERIFY FIX (should return 0 rows):
-- ============================================================

SELECT COUNT(*) as remaining_issues
FROM auth_user u
INNER JOIN rbac_userprofile up ON u.id = up.user_id
INNER JOIN rbac_userrole ur ON up.id = ur.user_profile_id
INNER JOIN rbac_role r ON ur.role_id = r.id
WHERE u.is_active = true
  AND (u.is_superuser = true OR u.is_staff = true)
  AND r.code NOT IN ('super_admin', 'admin', 'ict_admin')
  AND u.email NOT IN ('mohammed.agra@rejlers.ae', 'fahad.hussein@rejlers.ae', 'tanzeem.agra@rejlers.ae');
