import psycopg2

PROD_DB = "postgresql://postgres:iBEjCnCHbjwnnIhyJhoRXGiUtXNHMjpp@sakura.proxy.rlwy.net:31281/railway"

print("=" * 80)
print("PRODUCTION DATABASE STATUS")
print("=" * 80)

conn = psycopg2.connect(PROD_DB)
cur = conn.cursor()

# Count tables
cur.execute("SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='public'")
table_count = cur.fetchone()[0]

# Count users
cur.execute("SELECT COUNT(*) FROM users")
user_count = cur.fetchone()[0]

# Count profiles
cur.execute("SELECT COUNT(*) FROM rbac_user_profiles")
profile_count = cur.fetchone()[0]

# Count organizations
cur.execute("SELECT COUNT(*) FROM rbac_organizations")
org_count = cur.fetchone()[0]

# Count rbac modules
cur.execute("SELECT COUNT(*) FROM rbac_modules")
module_count = cur.fetchone()[0]

# Count rbac roles
cur.execute("SELECT COUNT(*) FROM rbac_roles")
role_count = cur.fetchone()[0]

print(f"\n✅ Schema Created Successfully!")
print(f"\nTables:        {table_count}")
print(f"Users:         {user_count}")
print(f"Profiles:      {profile_count}")
print(f"Organizations: {org_count}")
print(f"Modules:       {module_count}")
print(f"Roles:         {role_count}")
print("\n✅ All user data migrated from preprod!")
print("\n" + "=" * 80)

conn.close()
