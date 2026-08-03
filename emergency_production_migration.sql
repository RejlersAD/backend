-- Emergency Production Migration Script
-- Run this if Django migrations fail
-- Adds missing columns from migrations 0013 and 0014

BEGIN;

-- Migration 0013: Add vendor integration fields
DO $$
BEGIN
    -- Add vendor_id column if it doesn't exist
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'procurement_purchaserequisition' 
        AND column_name = 'vendor_id'
    ) THEN
        ALTER TABLE procurement_purchaserequisition 
        ADD COLUMN vendor_id uuid NULL;
        
        ALTER TABLE procurement_purchaserequisition 
        ADD CONSTRAINT procurement_purchas_vendor_id_fk 
        FOREIGN KEY (vendor_id) REFERENCES procurement_vendor(id) 
        ON DELETE SET NULL;
        
        CREATE INDEX procurement_purchas_vendor_id_idx 
        ON procurement_purchaserequisition(vendor_id);
        
        RAISE NOTICE 'Added vendor_id column';
    ELSE
        RAISE NOTICE 'vendor_id column already exists';
    END IF;
    
    -- Add vendor_selection_reason if doesn't exist
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'procurement_purchaserequisition' 
        AND column_name = 'vendor_selection_reason'
    ) THEN
        ALTER TABLE procurement_purchaserequisition 
        ADD COLUMN vendor_selection_reason text NOT NULL DEFAULT '';
        
        RAISE NOTICE 'Added vendor_selection_reason column';
    END IF;
    
    -- Add ai_vendor_recommendations if doesn't exist
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'procurement_purchaserequisition' 
        AND column_name = 'ai_vendor_recommendations'
    ) THEN
        ALTER TABLE procurement_purchaserequisition 
        ADD COLUMN ai_vendor_recommendations jsonb NOT NULL DEFAULT '[]'::jsonb;
        
        RAISE NOTICE 'Added ai_vendor_recommendations column';
    END IF;
    
END $$;

-- Migration 0014: Alter ai_vendor_recommendations default
DO $$
BEGIN
    -- Change default if needed
    ALTER TABLE procurement_purchaserequisition 
    ALTER COLUMN ai_vendor_recommendations SET DEFAULT '[]'::jsonb;
    
    RAISE NOTICE 'Updated ai_vendor_recommendations default';
END $$;

-- Record migrations in django_migrations table
INSERT INTO django_migrations (app, name, applied)
SELECT 'procurement', '0013_enhance_pr_workflow_and_vendor_integration', NOW()
WHERE NOT EXISTS (
    SELECT 1 FROM django_migrations 
    WHERE app = 'procurement' 
    AND name = '0013_enhance_pr_workflow_and_vendor_integration'
);

INSERT INTO django_migrations (app, name, applied)
SELECT 'procurement', '0014_alter_purchaserequisition_ai_vendor_recommendations_and_more', NOW()
WHERE NOT EXISTS (
    SELECT 1 FROM django_migrations 
    WHERE app = 'procurement' 
    AND name = '0014_alter_purchaserequisition_ai_vendor_recommendations_and_more'
);

COMMIT;

-- Verify
SELECT 
    column_name, 
    data_type, 
    is_nullable,
    column_default
FROM information_schema.columns 
WHERE table_name = 'procurement_purchaserequisition'
AND column_name IN ('vendor_id', 'vendor_selection_reason', 'ai_vendor_recommendations')
ORDER BY column_name;

SELECT 
    'Migration Status' as check_type,
    COUNT(*) as count 
FROM django_migrations 
WHERE app = 'procurement' 
AND (name LIKE '%0013%' OR name LIKE '%0014%');
