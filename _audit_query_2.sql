-- Query 2: Specific User Check - Debasis.Sana@rejlers.ae
SELECT 
    u.email,
    u.first_name || ' ' || u.last_name as name,
    u.is_superuser,
    STRING_AGG(DISTINCT r.code, ', ') as role_codes,
    COUNT(DISTINCT m.id) as module_count,
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
    u.email = 'Debasis.Sana@rejlers.ae'
    AND up.is_deleted = false
    AND r.is_active = true
GROUP BY u.id, u.email, u.first_name, u.last_name, u.is_superuser;
