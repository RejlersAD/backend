#!/bin/bash
# Run this in Railway shell to fix user permissions

echo "============================================================"
echo "FIX USER PERMISSIONS FOR DEBASIS.SANA@REJLERS.AE"
echo "============================================================"

# Access PostgreSQL and run the fix
python manage.py dbshell << 'EOF'
-- Step 1: Check current state
SELECT 
    email, 
    is_superuser, 
    is_staff, 
    is_active 
FROM auth_user 
WHERE email = 'Debasis.Sana@rejlers.ae';

-- Step 2: Apply fix
UPDATE auth_user 
SET 
    is_superuser = false, 
    is_staff = false 
WHERE email = 'Debasis.Sana@rejlers.ae';

-- Step 3: Verify fix
SELECT 
    email, 
    is_superuser, 
    is_staff, 
    is_active 
FROM auth_user 
WHERE email = 'Debasis.Sana@rejlers.ae';
EOF

echo ""
echo "============================================================"
echo "Fix applied! User must logout and login again."
echo "============================================================"
