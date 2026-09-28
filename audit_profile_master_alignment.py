"""
READ-ONLY audit: compare UserProfile (rbac) vs EmployeeMaster (hr_core)
for department / job_title / manager alignment.

Usage (inside container):
    python /app/audit_profile_master_alignment.py
"""
import os
import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from apps.rbac.models import UserProfile
from apps.hr_core.models import EmployeeMaster

rows = []
profiles = UserProfile.objects.filter(is_deleted=False).select_related("user", "canonical_employee", "manager")
total = profiles.count()
linked = 0
mismatch = 0
unlinked = 0

for p in profiles.iterator():
    emp = p.canonical_employee or EmployeeMaster.objects.filter(user_id=p.user_id).first()
    if not emp:
        unlinked += 1
        continue
    linked += 1
    expected_title = emp.designation or emp.job_title_uae or emp.job_title_finland or ""
    expected_dept = emp.department or ""
    expected_mgr_id = emp.manager.user_id if emp.manager else None

    diffs = {}
    if (p.department or "") != expected_dept:
        diffs["department"] = (p.department, expected_dept)
    if (p.job_title or "") != expected_title:
        diffs["job_title"] = (p.job_title, expected_title)
    mgr_user_id = p.manager.user_id if p.manager else None
    if mgr_user_id != expected_mgr_id:
        diffs["manager"] = (str(mgr_user_id), str(expected_mgr_id))

    if diffs:
        mismatch += 1
        rows.append((p.user.email, diffs))

print(f"\n=== PROFILE <-> MASTER ALIGNMENT AUDIT ===")
print(f"Profiles scanned : {total}")
print(f"Linked to master : {linked}")
print(f"Unlinked         : {unlinked}")
print(f"Misaligned       : {mismatch}")
print()
for email, diffs in rows[:40]:
    print(f"- {email}")
    for field, (old, new) in diffs.items():
        print(f"    {field}: profile={old!r}  master={new!r}")
if len(rows) > 40:
    print(f"  ... and {len(rows) - 40} more")
print("\nREAD-ONLY — no changes were made.")
