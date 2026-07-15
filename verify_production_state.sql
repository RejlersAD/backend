-- ============================================================
-- PRODUCTION DATABASE VERIFICATION SCRIPT
-- Run this on Railway to verify all index renames completed
-- ============================================================

-- Check dashboard indexes (should show NEW names)
SELECT indexname FROM pg_indexes 
WHERE tablename = 'dashboard_userdashboardinsight' 
ORDER BY indexname;
-- Expected: dashboard_u_user_id_c45649_idx, dashboard_u_user_id_2f4ce5_idx

-- Check invoice_tracker indexes
SELECT indexname FROM pg_indexes 
WHERE tablename = 'invoice_tra_customerinvoice' 
ORDER BY indexname;
-- Expected: invoice_tra_account_55b104_idx, invoice_tra_rad_pro_21a969_idx

-- Check process_datasheet indexes
SELECT indexname FROM pg_indexes 
WHERE tablename = 'pump_calculation_data' 
ORDER BY indexname;
-- Expected: pump_calcul_tag_no_1ac76f_idx, pump_calcul_documen_2fc886_idx, pump_calcul_status_8d3b11_idx

-- Check rbac indexes
SELECT indexname FROM pg_indexes 
WHERE tablename LIKE 'rbac_%' 
AND indexname LIKE 'activity_%' 
ORDER BY indexname;
-- Expected: activity_ev_user_id_f3fd17_idx, activity_ev_applica_be833b_idx, etc.

-- Verify ProfileDocument table exists (from our new migrations)
SELECT EXISTS (
    SELECT FROM information_schema.tables 
    WHERE table_name = 'rbac_profile_documents'
);
-- Expected: true

-- Check ProfileDocument columns
SELECT column_name, data_type 
FROM information_schema.columns 
WHERE table_name = 'rbac_profile_documents' 
ORDER BY ordinal_position;

-- ============================================================
-- If all indexes show NEW names, migrations were successful!
-- ============================================================
