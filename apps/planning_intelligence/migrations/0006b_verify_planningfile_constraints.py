# Generated migration to fix PlanningFile table constraints before document intelligence
from django.db import migrations


def verify_planningfile_constraints(apps, schema_editor):
    """
    Soft-coded verification and repair of PlanningFile table constraints.
    Ensures the table has proper primary key before foreign keys are added.
    """
    if schema_editor.connection.vendor != 'postgresql':
        return  # Skip for non-PostgreSQL databases
    
    with schema_editor.connection.cursor() as cursor:
        # Check if planning_intelligence_planningfile table exists
        cursor.execute("""
            SELECT EXISTS (
                SELECT FROM information_schema.tables 
                WHERE table_schema = 'public' 
                AND table_name = 'planning_intelligence_planningfile'
            );
        """)
        table_exists = cursor.fetchone()[0]
        
        if not table_exists:
            print("⚠️  PlanningFile table does not exist. Skipping constraint verification.")
            return
        
        # Verify primary key constraint exists
        cursor.execute("""
            SELECT constraint_name 
            FROM information_schema.table_constraints 
            WHERE table_name = 'planning_intelligence_planningfile' 
            AND constraint_type = 'PRIMARY KEY';
        """)
        pk_result = cursor.fetchone()
        
        if not pk_result:
            print("❌ Primary key constraint missing on PlanningFile. Attempting to add...")
            # Add primary key constraint if missing
            cursor.execute("""
                ALTER TABLE planning_intelligence_planningfile 
                ADD CONSTRAINT planning_intelligence_planningfile_pkey 
                PRIMARY KEY (id);
            """)
            print("✅ Primary key constraint added to PlanningFile table.")
        else:
            print(f"✅ PlanningFile table has primary key: {pk_result[0]}")
        
        # Verify id column exists and is correct type
        cursor.execute("""
            SELECT column_name, data_type, is_nullable
            FROM information_schema.columns
            WHERE table_name = 'planning_intelligence_planningfile'
            AND column_name = 'id';
        """)
        id_column = cursor.fetchone()
        
        if not id_column:
            print("❌ CRITICAL: 'id' column missing from PlanningFile table!")
            raise Exception("PlanningFile table is missing 'id' column. Manual database repair required.")
        
        print(f"✅ PlanningFile 'id' column: {id_column[1]} (nullable: {id_column[2]})")


def reverse_verify(apps, schema_editor):
    """Reverse operation - no action needed for verification"""
    pass


class Migration(migrations.Migration):
    """
    SOFT-CODED MIGRATION: Ensures PlanningFile table has proper constraints
    before document intelligence foreign keys are created.
    
    This migration is idempotent and safe to run multiple times.
    """
    
    dependencies = [
        ('planning_intelligence', '0006_relational_scheduling_engine'),
    ]

    operations = [
        migrations.RunPython(
            verify_planningfile_constraints,
            reverse_verify,
        ),
    ]
