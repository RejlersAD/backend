-- ============================================================================
-- RBAC SECURITY AUDIT - Production Database
-- ============================================================================
-- Purpose: Identify users with access to sensitive modules (Payroll, HR, Finance, Procurement)
-- who should NOT have access (i.e., not Super Administrators or HR Admins)
--
-- SENSITIVE MODULE CODES (from rbac_config.py):
--   - payroll
--   - hr_management
--   - timesheet
--   - hr_onboarding  
--   - finance
--   - sales
--   - procurement
--   - procurement_vendors
--   - procurement_orders
--   - procurement_requisitions
--   - procurement_receipts
-- ============================================================================

-- Query 1: Users with access to PAYROLL module
-- Expected: Only super_admin and hr_admin roles
SELECT 
    u.email,
    u.first_name,
    u.last_name,
    u.is_active,
    u.is_staff,
    u.is_superuser,
    r.name as role_name,
    r.code as role_code,
    r.level as role_level,
    m.code as module_code,
    m.name as module_name,
    up.status as profile_status,
    up.department
FROM auth_user u
JOIN rbac_userprofile up ON u.id = up.user_id
JOIN rbac_userrole ur ON up.id = ur.user_profile_id
JOIN rbac_role r ON ur.role_id = r.id
JOIN rbac_rolemodule rm ON r.id = rm.role_id
JOIN rbac_module m ON rm.module_id = m.id
WHERE 
    m.code IN ('payroll', 'hr_management', 'timesheet', 'hr_onboarding')
    AND up.is_deleted = false
    AND r.is_active = true
    AND ur.is_primary = true
ORDER BY m.code, u.email;

-- Query 2: Users with access to FINANCE module
-- Expected: super_admin, admin, finance team members
SELECT 
    u.email,
    u.first_name,
    u.last_name,
    r.name as role_name,
    r.code as role_code,
    m.code as module_code,
    m.name as module_name,
    up.department
FROM auth_user u
JOIN rbac_userprofile up ON u.id = up.user_id
JOIN rbac_userrole ur ON up.id = ur.user_profile_id
JOIN rbac_role r ON ur.role_id = r.id
JOIN rbac_rolemodule rm ON r.id = rm.role_id
JOIN rbac_module m ON rm.module_id = m.id
WHERE 
    m.code = 'finance'
    AND up.is_deleted = false
    AND r.is_active = true
    AND ur.is_primary = true
ORDER BY u.email;

-- Query 3: Users with access to PROCUREMENT modules
-- Expected: super_admin, admin, procurement team members
SELECT 
    u.email,
    u.first_name,
    u.last_name,
    r.name as role_name,
    r.code as role_code,
    m.code as module_code,
    m.name as module_name,
    up.department
FROM auth_user u
JOIN rbac_userprofile up ON u.id = up.user_id
JOIN rbac_userrole ur ON up.id = ur.user_profile_id
JOIN rbac_role r ON ur.role_id = r.id
JOIN rbac_rolemodule rm ON r.id = rm.role_id
JOIN rbac_module m ON rm.module_id = m.id
WHERE 
    m.code IN ('procurement', 'procurement_vendors', 'procurement_orders', 'procurement_requisitions', 'procurement_receipts')
    AND up.is_deleted = false
    AND r.is_active = true
    AND ur.is_primary = true
ORDER BY m.code, u.email;

-- Query 4: Users with access to SALES module
-- Expected: super_admin, admin, sales team members
SELECT 
    u.email,
    u.first_name,
    u.last_name,
    r.name as role_name,
    r.code as role_code,
    m.code as module_code,
    m.name as module_name,
    up.department
FROM auth_user u
JOIN rbac_userprofile up ON u.id = up.user_id
JOIN rbac_userrole ur ON up.id = ur.user_profile_id
JOIN rbac_role r ON ur.role_id = r.id
JOIN rbac_rolemodule rm ON r.id = rm.role_id
JOIN rbac_module m ON rm.module_id = m.id
WHERE 
    m.code = 'sales'
    AND up.is_deleted = false
    AND r.is_active = true
    AND ur.is_primary = true
ORDER BY u.email;

-- Query 5: Comprehensive view - ALL users with their roles and sensitive modules
-- This shows the complete picture of who has what access
SELECT 
    u.email,
    u.first_name,
    u.last_name,
    u.is_superuser,
    STRING_AGG(DISTINCT r.name, ', ') as roles,
    STRING_AGG(DISTINCT r.code, ', ') as role_codes,
    STRING_AGG(DISTINCT 
        CASE 
            WHEN m.code IN ('payroll', 'hr_management', 'timesheet', 'hr_onboarding') THEN m.code 
        END, ', ') as hr_modules,
    STRING_AGG(DISTINCT 
        CASE 
            WHEN m.code = 'finance' THEN m.code 
        END, ', ') as finance_modules,
    STRING_AGG(DISTINCT 
        CASE 
            WHEN m.code IN ('procurement', 'procurement_vendors', 'procurement_orders', 'procurement_requisitions', 'procurement_receipts') THEN m.code 
        END, ', ') as procurement_modules,
    STRING_AGG(DISTINCT 
        CASE 
            WHEN m.code = 'sales' THEN m.code 
        END, ', ') as sales_modules,
    up.department,
    up.status
FROM auth_user u
JOIN rbac_userprofile up ON u.id = up.user_id
JOIN rbac_userrole ur ON up.id = ur.user_profile_id
JOIN rbac_role r ON ur.role_id = r.id
LEFT JOIN rbac_rolemodule rm ON r.id = rm.role_id
LEFT JOIN rbac_module m ON rm.module_id = m.id
WHERE 
    up.is_deleted = false
    AND r.is_active = true
GROUP BY u.id, u.email, u.first_name, u.last_name, u.is_superuser, up.department, up.status
HAVING 
    -- Only show users who have at least one sensitive module
    STRING_AGG(DISTINCT 
        CASE 
            WHEN m.code IN ('payroll', 'hr_management', 'timesheet', 'hr_onboarding', 'finance', 'sales', 'procurement', 'procurement_vendors', 'procurement_orders', 'procurement_requisitions', 'procurement_receipts') 
            THEN m.code 
        END, ', ') IS NOT NULL
ORDER BY u.email;

-- Query 6: Specific check for "Debasis.Sana@rejlers.ae" and similar Default role users
-- Find all users with "Default" role who might have unintended module access
SELECT 
    u.email,
    u.first_name,
    u.last_name,
    r.name as role_name,
    r.code as role_code,
    COUNT(m.id) as total_modules,
    STRING_AGG(m.code, ', ' ORDER BY m.code) as module_codes,
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
    AND r.code = 'default'  -- Focus on Default role users
GROUP BY u.email, u.first_name, u.last_name, r.name, r.code
HAVING 
    -- Flag if they have ANY sensitive module (they shouldn't)
    STRING_AGG(
        CASE 
            WHEN m.code IN ('payroll', 'hr_management', 'timesheet', 'hr_onboarding', 'finance', 'sales', 'procurement', 'procurement_vendors', 'procurement_orders', 'procurement_requisitions', 'procurement_receipts') 
            THEN m.code 
        END, ', ') IS NOT NULL
ORDER BY u.email;

-- Query 7: Check all system roles and their module assignments
-- This verifies the role-to-module mappings are correct
SELECT 
    r.name as role_name,
    r.code as role_code,
    r.level,
    r.is_system_role,
    COUNT(DISTINCT m.id) as module_count,
    STRING_AGG(DISTINCT m.code, ', ' ORDER BY m.code) as modules,
    STRING_AGG(DISTINCT 
        CASE 
            WHEN m.code IN ('payroll', 'hr_management', 'timesheet', 'hr_onboarding') THEN m.code 
        END, ', ') as hr_modules,
    STRING_AGG(DISTINCT 
        CASE 
            WHEN m.code IN ('finance', 'sales', 'procurement', 'procurement_vendors', 'procurement_orders', 'procurement_requisitions', 'procurement_receipts') THEN m.code 
        END, ', ') as business_modules
FROM rbac_role r
LEFT JOIN rbac_rolemodule rm ON r.id = rm.role_id
LEFT JOIN rbac_module m ON rm.module_id = m.id
WHERE r.is_active = true
GROUP BY r.id, r.name, r.code, r.level, r.is_system_role
ORDER BY r.level, r.code;
