import psycopg2

PREPROD_DB = "postgresql://postgres:thAEPEWfKHTGvCwRfaeeichfMNxwdnbD@tokaido.proxy.rlwy.net:59798/railway"
PROD_DB = "postgresql://postgres:iBEjCnCHbjwnnIhyJhoRXGiUtXNHMjpp@sakura.proxy.rlwy.net:31281/railway"

print("PREPROD rbac_organizations columns:")
conn = psycopg2.connect(PREPROD_DB)
cur = conn.cursor()
cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name='rbac_organizations' ORDER BY ordinal_position")
preprod_cols = [c[0] for c in cur.fetchall()]
print(preprod_cols)
conn.close()

print("\nPROD rbac_organizations columns:")
conn = psycopg2.connect(PROD_DB)
cur = conn.cursor()
cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name='rbac_organizations' ORDER BY ordinal_position")
prod_cols = [c[0] for c in cur.fetchall()]
print(prod_cols)
conn.close()

print("\n common:", set(preprod_cols) & set(prod_cols))
print("In preprod only:", set(preprod_cols) - set(prod_cols))
print("In prod only:", set(prod_cols) - set(preprod_cols))
