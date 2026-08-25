"""
Fix duplicate IDs in planning_intelligence_planningfile table
This script identifies and fixes duplicate primary key values
"""
import os
import sys
import django

# Setup Django
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from django.db import connection

def fix_duplicates():
    """Fix duplicate IDs in planningfile table"""
    with connection.cursor() as cursor:
        # Find duplicate IDs
        cursor.execute("""
            SELECT id, COUNT(*) as count
            FROM planning_intelligence_planningfile
            GROUP BY id
            HAVING COUNT(*) > 1
            ORDER BY id
        """)
        duplicates = cursor.fetchall()
        
        if not duplicates:
            print("✅ No duplicates found!")
            return
        
        print(f"Found {len(duplicates)} duplicate ID(s): {[d[0] for d in duplicates]}")
        
        # Get the max ID to start reassigning
        cursor.execute("SELECT MAX(id) FROM planning_intelligence_planningfile")
        max_id = cursor.fetchone()[0] or 0
        next_id = max_id + 1
        
        print(f"Current max ID: {max_id}, will start reassigning from {next_id}")
        
        # Fix each duplicate
        for dup_id, count in duplicates:
            print(f"\nFixing ID {dup_id} ({count} duplicates)...")
            
            # Get all CTIDs (physical row identifiers) for this ID
            cursor.execute("""
                SELECT ctid, id, created_at
                FROM planning_intelligence_planningfile
                WHERE id = %s
                ORDER BY created_at
            """, [dup_id])
            rows = cursor.fetchall()
            
            # Keep the first one, reassign the rest
            for i, (ctid, old_id, created_at) in enumerate(rows):
                if i == 0:
                    print(f"  ✓ Keeping first record: ctid={ctid}, id={old_id}, created_at={created_at}")
                else:
                    new_id = next_id
                    cursor.execute("""
                        UPDATE planning_intelligence_planningfile
                        SET id = %s
                        WHERE ctid = %s
                    """, [new_id, ctid])
                    print(f"  → Reassigned ctid={ctid} from id={old_id} to id={new_id}")
                    next_id += 1
        
        print(f"\n✅ Fixed all duplicates! New max ID: {next_id - 1}")
        
        # Verify no duplicates remain
        cursor.execute("""
            SELECT id, COUNT(*)
            FROM planning_intelligence_planningfile
            GROUP BY id
            HAVING COUNT(*) > 1
        """)
        remaining = cursor.fetchall()
        
        if remaining:
            print(f"❌ ERROR: Still have duplicates: {remaining}")
        else:
            print("✅ Verification: No duplicates remain")
            
            # Update sequence to match new max ID
            cursor.execute("""
                SELECT setval('planning_intelligence_planningfile_id_seq', %s, true)
            """, [next_id - 1])
            print(f"✅ Updated sequence to {next_id - 1}")

if __name__ == '__main__':
    print("=" * 60)
    print("FIXING DUPLICATE IDs IN PLANNINGFILE TABLE")
    print("=" * 60)
    fix_duplicates()
    print("=" * 60)
    print("DONE")
    print("=" * 60)
