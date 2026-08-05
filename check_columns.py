import psycopg2

PREPROD_DB = "postgresql://postgres:thAEPEWfKHTGvCwRfaeeichfMNxwdnbD@tokaido.proxy.rlwy.net:59798/railway"
PROD_DB = "postgresql://postgres:iBEjCnCHbjwnnIhyJhoRXGiUtXNHMjpp@sakura.proxy.rlwy.net:31281/railway"

print("=" * 80)
print("CHECKING DATABASE COLUMNS")
print("=" * 80)

# Preprod users table
conn = psycopg2.connect(PREPROD_DB)
cur = conn.cursor()
cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name='users' ORDER BY ordinal_position")
print("\n📊 PREPROD 'users' columns:")
for row in cur.fetchall():
    print(f"  - {row[0]}")
conn.close()

# Production users table  
conn = psycopg2.connect(PROD_DB)
cur = conn.cursor()
cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name='users' ORDER BY ordinal_position")
print("\n📊 PRODUCTION 'users' columns:")
for row in cur.fetchall():
    print(f"  - {row[0]}")
conn.close()

# Preprod user profiles
conn = psycopg2.connect(PREPROD_DB)
cur = conn.cursor()
cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name='rbac_user_profiles' ORDER BY ordinal_position")
print("\n📊 PREPROD 'rbac_user_profiles' columns:")
for row in cur.fetchall():
    print(f"  - {row[0]}")
conn.close()

# Production user profiles
conn = psycopg2.connect(PROD_DB)
cur = conn.cursor()
cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name='rbac_user_profiles' ORDER BY ordinal_position")
print("\n📊 PRODUCTION 'rbac_user_profiles' columns:")
for row in cur.fetchall():
    print(f"  - {row[0]}")
conn.close()

print("\n" + "=" * 80)
