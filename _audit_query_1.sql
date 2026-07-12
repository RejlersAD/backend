-- Query 1: Default Role Users with SENSITIVE Modules (SHOULD BE EMPTY)
SELECT 
    u.email,
    u.first_name || ' ' || u.last_name as name,
    STRING_AGG(DISTINCT m.code, ', ' ORDER BY m.code) as all_modules,
    STRING_AGG(
        CASE 
            WHEN m.code IN ('payroll', 'hr_management', 'timesheet', 'hr_onboarding', 'finance', 'sales', 'procurement', 'procurement_vendors', 'procurement_orders', 'procurement_requisitions', 'procurement_receipts') 
            THEN m.code 
        END, ', ') as sensitive_modules
FROM auth_user u
JOIN rbac_userprofile up ON u.id = up.user_id
JOIN rbac_userrole ur ON up.id = ur.user_profile_id
JOIN rbac_role r ON ur.role_id = r.id
LEFT JOIN rbac_rolemodule rm ON r.id = rm.role_id
LEFT JOIN rbac_module m ON rm.module_id = m.id
WHERE 
    up.is_deleted = false
    AND r.is_active = true
    AND r.code = 'default'
    AND ur.is_primary = true
GROUP BY u.email, u.first_name, u.last_name
HAVING 
    STRING_AGG(
        CASE 
            WHEN m.code IN ('payroll', 'hr_management', 'timesheet', 'hr_onboarding', 'finance', 'sales', 'procurement', 'procurement_vendors', 'procurement_orders', 'procurement_requisitions', 'procurement_receipts') 
            THEN m.code 
        END, ', ') IS NOT NULL
ORDER BY u.email;
