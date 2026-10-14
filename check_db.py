import os, django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()
from django.db import connection

with connection.cursor() as c:
    c.execute("SELECT setting FROM pg_settings WHERE name='server_version'")
    print("PG VERSION:", c.fetchone())
    c.execute("SELECT current_database()")
    print("DB:", c.fetchone())
    c.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='public' AND table_name LIKE 'spec_customization%' ORDER BY 1")
    print("TABLES:", [r[0] for r in c.fetchall()])