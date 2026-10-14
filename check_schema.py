import os, django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()
from django.db import connection

with connection.cursor() as c:
    c.execute("SELECT app, name FROM django_migrations WHERE app='spec_customization' ORDER BY id")
    print("MIGRATIONS:")
    for r in c.fetchall():
        print("  ", r[0], r[1])
    c.execute("SELECT column_name FROM information_schema.columns WHERE table_name='spec_customization_paperspecextractionjob' ORDER BY ordinal_position")
    cols = [r[0] for r in c.fetchall()]
    print("COLUMNS:", cols)
    print("saved_at present:", 'saved_at' in cols)
    print("saved_by present:", 'saved_by' in cols)