import psycopg2

PREPROD_DB = "postgresql://postgres:thAEPEWfKHTGvCwRfaeeichfMNxwdnbD@tokaido.proxy.rlwy.net:59798/railway"
PROD_DB = "postgresql://postgres:iBEjCnCHbjwnnIhyJhoRXGiUtXNHMjpp@sakura.proxy.rlwy.net:31281/railway"

print("=" * 80)
print("PREPROD PROFILE COLUMNS")
print("=" * 80)

conn = psycopg2.connect(PREPROD_DB)
cur = conn.cursor()

cur.execute("""
    SELECT column_name, data_type 
    FROM information_schema.columns 
    WHERE table_name='rbac_user_profiles' 
    ORDER BY ordinal_position
""")

preprod_cols = cur.fetchall()
preprod_col_names = [col[0] for col in preprod_cols]

print(f"\nTotal columns: {len(preprod_cols)}\n")
for col in preprod_cols:
    print(f"{col[0]:40} {col[1]}")

conn.close()

print("\n" + "=" * 80)
print("PRODUCTION PROFILE COLUMNS")
print("=" * 80)

conn = psycopg2.connect(PROD_DB)
cur = conn.cursor()

cur.execute("""
    SELECT column_name, data_type 
    FROM information_schema.columns 
    WHERE table_name='rbac_user_profiles' 
    ORDER BY ordinal_position
""")

prod_cols = cur.fetchall()
prod_col_names = [col[0] for col in prod_cols]

print(f"\nTotal columns: {len(prod_cols)}\n")
for col in prod_cols:
    print(f"{col[0]:40} {col[1]}")

conn.close()

print("\n" + "=" * 80)
print("COLUMN COMPARISON")
print("=" * 80)

# Find columns in preprod but not in production
missing_in_prod = set(preprod_col_names) - set(prod_col_names)
# Find columns in production but not in preprod
missing_in_preprod = set(prod_col_names) - set(preprod_col_names)
# Common columns
common = set(preprod_col_names) & set(prod_col_names)

print(f"\n✅ Common columns: {len(common)}")
print(f"⚠️  In preprod but NOT in production: {len(missing_in_prod)}")
if missing_in_prod:
    for col in missing_in_prod:
        print(f"   - {col}")

print(f"\n⚠️  In production but NOT in preprod: {len(missing_in_preprod)}")
if missing_in_preprod:
    for col in missing_in_preprod:
        print(f"   - {col}")

print("\n" + "=" * 80)
