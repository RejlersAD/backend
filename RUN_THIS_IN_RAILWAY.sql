-- ============================================================
-- RUN THESE QUERIES IN RAILWAY WEB DASHBOARD
-- https://railway.app → aiflow_backend → Postgres → Data tab
-- ============================================================

-- STEP 1: Check current state (COPY AND RUN THIS FIRST)
SELECT email, is_superuser, is_staff, is_active 
FROM auth_user 
WHERE email = 'Debasis.Sana@rejlers.ae';


-- STEP 2: Fix the issue (RUN THIS TO FIX)
UPDATE auth_user 
SET is_superuser = false, is_staff = false 
WHERE email = 'Debasis.Sana@rejlers.ae';


-- STEP 3: Verify fix applied (RUN THIS TO CONFIRM)
SELECT email, is_superuser, is_staff, is_active 
FROM auth_user 
WHERE email = 'Debasis.Sana@rejlers.ae';


-- Expected result after fix:
-- email: Debasis.Sana@rejlers.ae
-- is_superuser: false
-- is_staff: false
-- is_active: true
