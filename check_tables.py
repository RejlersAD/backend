import os
import psycopg2

db_url = os.environ.get('DATABASE_PUBLIC_URL') or os.environ.get('DATABASE_URL')
conn = psycopg2.connect(db_url)
cur = conn.cursor()

# Find organization table
cur.execute("SELECT tablename FROM pg_tables WHERE schemaname = 'public' AND tablename LIKE '%organization%' ORDER BY tablename")
print("\nOrganization tables:")
for row in cur.fetchall():
    print(f"  {row[0]}")

# Find user tables
cur.execute("SELECT tablename FROM pg_tables WHERE schemaname = 'public' AND tablename LIKE '%user%' LIMIT 20")
print("\nUser tables:")
for row in cur.fetchall():
    print(f"  {row[0]}")

cur.close()
conn.close()
