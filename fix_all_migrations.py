"""
Comprehensive migration history repair script.
Fixes all dependency inconsistencies in the django_migrations table.
"""
import os
import sys
import django

# Setup Django environment
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from django.db import connection
from django.db.migrations.loader import MigrationLoader


def fix_all_migration_dependencies():
    """
    Comprehensively fix migration history by reordering records to match dependencies.
    """
    print("\n" + "="*70)
    print("COMPREHENSIVE MIGRATION HISTORY REPAIR")
    print("="*70 + "\n")
    
    # Load all migrations and their dependencies
    loader = MigrationLoader(connection)
    
    # Get all applied migrations from database
    with connection.cursor() as cursor:
        cursor.execute("""
            SELECT app, name, id, applied 
            FROM django_migrations 
            ORDER BY id;
        """)
        applied_migrations = cursor.fetchall()
    
    print(f"📋 Found {len(applied_migrations)} applied migrations\n")
    
    # Build a map of migrations to their IDs
    migration_ids = {}
    for app, name, mid, applied in applied_migrations:
        migration_ids[(app, name)] = (mid, applied)
    
    # Check for dependency violations
    violations = []
    
    for app, name, mid, applied in applied_migrations:
        migration_key = (app, name)
        
        if migration_key not in loader.graph.nodes:
            print(f"⚠️  Migration {app}.{name} not found in code - skipping")
            continue
        
        # Get dependencies for this migration
        migration = loader.graph.nodes[migration_key]
        dependencies = migration.parents if hasattr(migration, 'parents') else []
        
        for dep_app, dep_name in dependencies:
            dep_key = (dep_app, dep_name)
            
            if dep_key in migration_ids:
                dep_id, _ = migration_ids[dep_key]
                
                # Check if dependency has lower ID (comes before)
                if dep_id >= mid:
                    violations.append({
                        'migration': (app, name, mid),
                        'dependency': (dep_app, dep_name, dep_id),
                        'issue': f"{app}.{name} (ID {mid}) depends on {dep_app}.{dep_name} (ID {dep_id}) but has lower ID"
                    })
    
    if not violations:
        print("✅ No migration dependency violations found!\n")
        return True
    
    print(f"❌ Found {len(violations)} dependency violations:\n")
    for v in violations:
        print(f"   {v['issue']}")
    
    print(f"\n🔧 Fixing violations by reordering migration records...\n")
    
    # Strategy: Rebuild migration IDs in correct dependency order
    try:
        with connection.cursor() as cursor:
            # Get correct topological order from loader
            # Build a proper ordering based on dependencies
            ordered_migrations = []
            processed = set()
            
            def add_migration_with_deps(app, name):
                key = (app, name)
                if key in processed or key not in loader.graph.nodes:
                    return
                
                # Add dependencies first
                migration = loader.graph.nodes[key]
                deps = migration.parents if hasattr(migration, 'parents') else []
                for dep_app, dep_name in deps:
                    add_migration_with_deps(dep_app, dep_name)
                
                # Then add this migration
                if key in migration_ids:
                    mid, applied = migration_ids[key]
                    ordered_migrations.append((app, name, mid, applied))
                    processed.add(key)
            
            # Process all applied migrations
            for app, name, mid, applied in applied_migrations:
                add_migration_with_deps(app, name)
            
            # Now reassign IDs in correct order
            print("📝 Reassigning migration IDs in dependency order...\n")
            
            new_id = 1
            id_mapping = {}
            
            for app, name, old_id, applied in ordered_migrations:
                id_mapping[old_id] = new_id
                new_id += 1
            
            # First, move all migrations to temporary high IDs
            cursor.execute("""
                UPDATE django_migrations 
                SET id = id + 100000;
            """)
            
            # Then update to final IDs
            for app, name, old_id, applied in ordered_migrations:
                new_id = id_mapping[old_id]
                cursor.execute("""
                    UPDATE django_migrations 
                    SET id = %s 
                    WHERE app = %s AND name = %s;
                """, [new_id, app, name])
            
            print("✅ Migration IDs reassigned successfully!\n")
            
            # Verify the fix
            cursor.execute("""
                SELECT app, name, id 
                FROM django_migrations 
                ORDER BY id 
                LIMIT 20;
            """)
            updated_migrations = cursor.fetchall()
            
            print("✅ First 20 migrations in new order:")
            for app, name, mid in updated_migrations:
                print(f"   ID {mid:3d}: {app}.{name}")
            
            return True
            
    except Exception as e:
        print(f"\n❌ Error during fix: {e}")
        import traceback
        traceback.print_exc()
        connection.rollback()
        return False
    
    print("\n" + "="*70)


if __name__ == '__main__':
    try:
        success = fix_all_migration_dependencies()
        sys.exit(0 if success else 1)
    except Exception as e:
        print(f"\n❌ FATAL ERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
