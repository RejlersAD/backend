import psycopg2

PREPROD_DB = "postgresql://postgres:thAEPEWfKHTGvCwRfaeeichfMNxwdnbD@tokaido.proxy.rlwy.net:59798/railway"
PROD_DB = "postgresql://postgres:iBEjCnCHbjwnnIhyJhoRXGiUtXNHMjpp@sakura.proxy.rlwy.net:31281/railway"

print("=" * 80)
print("PREPROD USERS TABLE COLUMNS")
print("=" * 80)

conn = psycopg2.connect(PREPROD_DB)
cur = conn.cursor()

cur.execute("""
    SELECT column_name, data_type 
    FROM information_schema.columns 
    WHERE table_name='users' 
    ORDER BY ordinal_position
""")

preprod_cols = cur.fetchall()
for col in preprod_cols:
    print(f"{col[0]:30} {col[1]}")

conn.close()

print("\n" + "=" * 80)
print("PRODUCTION USERS TABLE COLUMNS")
print("=" * 80)

conn = psycopg2.connect(PROD_DB)
cur = conn.cursor()

cur.execute("""
    SELECT column_name, data_type 
    FROM information_schema.columns 
    WHERE table_name='users' 
    ORDER BY ordinal_position
""")

prod_cols = cur.fetchall()
for col in prod_cols:
    print(f"{col[0]:30} {col[1]}")

conn.close()

print("\n" + "=" * 80)
print("COUNT USERS IN PREPROD")
print("=" * 80)

conn = psycopg2.connect(PREPROD_DB)
cur = conn.cursor()
cur.execute("SELECT COUNT(*) FROM users")
count = cur.fetchone()[0]
print(f"Total users in preprod: {count}")
conn.close()
