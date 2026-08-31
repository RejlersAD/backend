# Canonical Project Relationship Migration Report

Generated: 2026-08-31  
Scope: Project Control, Procurement, and Finance project identity

## Decision

`apps.core.Project` is the authoritative enterprise Project. Procurement's
legacy Project registry remains temporarily available, but now has an explicit
one-to-one relationship to that canonical record. Purchase Requisitions and
Purchase Orders also have direct nullable canonical relationships.

Automatic migration uses only exact project-code matches after trimming,
case-folding, and collapsing repeated whitespace. It does not remove
punctuation, use substring matching, or infer a project from descriptive text.

## Pre-migration baseline

The read-only audit against the Railway preproduction database found:

| Record | Total | Existing structured linkage |
|---|---:|---:|
| Enterprise projects | 9 | — |
| Procurement projects | 3 | 1 safely resolvable; 2 unresolved |
| Purchase requisitions | 154 | 18 safely resolvable; 2 reference multiple enterprise projects; 134 have no exact match |
| Purchase orders | 127 | 16 safely resolvable; 0 conflicting references; 111 have no exact match |
| Purchase orders with budget allocation | 127 | 0 |
| Finance invoices | 74 | 0 linked to Vendor master records |
| Invoice-to-PO allocations | 0 | 0 verified |

This baseline predates migration `0035`. It was calculated with the same exact
matching policy but without changing Railway data. Run the report command after
deploying the schema to obtain authoritative record-level samples.

## Migration behavior

Migration `0035_canonical_enterprise_project_relationships`:

1. Adds `enterprise_project` to the Procurement Project master.
2. Adds `enterprise_project` to Purchase Requisitions.
3. Adds `enterprise_project` to Purchase Orders.
4. Links Procurement projects by exact project number/code.
5. Links a PR only when all recognized references resolve to one enterprise project.
6. Links a PO from its Procurement project, exact project number, or source PR
   only when those references resolve to one enterprise project.
7. Leaves missing, conflicting, and multi-project records unresolved.

Legacy fields and JSON payloads are preserved for audit and rollback.

Migration `0036_projectrelationshipresolution` adds an immutable audit ledger
for every manual and propagated canonical assignment. It does not modify any
budget, invoice, payroll, approval, or legacy reference value.

## Reconciliation workspace

Authorized Procurement users can open:

```text
/procurement/projects/reconciliation
```

The workspace provides a read-only unresolved queue, canonical Project choices,
search, record-type filtering, pagination, and recent resolution history. A
confirmed assignment is written atomically. Assigning a Procurement Project
also fills null links on explicitly related POs and PR project-detail records;
assigning a PR fills null links on its POs. Existing explicit canonical links
are not overwritten by propagation.

API endpoints:

- `GET /api/v1/procurement/projects/relationship-report/`
- `POST /api/v1/procurement/projects/resolve-relationship/`

## Report commands

Read-only summary:

```powershell
python manage.py report_project_relationships
```

Read-only JSON report:

```powershell
python manage.py report_project_relationships --format json --output project-relationships.json
```

Apply safe exact matches again after correcting legacy codes:

```powershell
python manage.py report_project_relationships --apply
```

The default mode never changes data. `--apply` only fills null canonical
relationships and does not replace an existing explicit relationship.

## Unresolved record policy

- `no_exact_match`: correct the legacy project number or select an enterprise project manually.
- `multiple_projects`: retain the PR unresolved until normalized multi-project allocation records are introduced.
- Conflicting PO references: reconcile the Procurement project, PO project number,
  and originating PR before assigning a canonical project.

Multi-project PR and PO records are retained in the manual queue and can now be
split across canonical projects and WBS nodes without forcing a false single-project link.

## WBS allocations and canonical cost ledger

Migration `project_control.0003_cost_ledger_and_allocations` introduces:

- approved enterprise Project/WBS budget allocations;
- controlled multi-project/WBS splits for PRs, POs, and verified invoice allocations;
- an idempotent posted/reversed cost ledger;
- immutable approved allocations with correcting/rejection workflow.

Cost KPIs no longer read the editable `Project.budget`, `Project.spent`,
approved-estimate totals, or invoice text. The ledger rebuild posts:

1. approved WBS budgets as `budget` entries;
2. linked or approved allocated POs as `commitment` entries;
3. verified Finance invoice-to-PO allocations as `actual` entries.

The existing Finance Sync button now rebuilds this structured ledger. KPI
responses expose `calculation_source=posted_cost_ledger`, entry count,
available-to-commit, and remaining commitment for operational verification.
The first ledger release deliberately does not guess foreign-exchange rates:
sources whose currency differs from the enterprise Project currency are
excluded and reported as currency exceptions until Finance converts them.
# Commercial integration and historical reconciliation

The canonical Project relationship now drives a shared cost ledger and an
immutable commercial event stream across Project Control, Procurement and
Finance. Final PO approval, accepted/partial receipts, verified invoice/PO
matches, invoice approval, and supplier payment operations are captured with a
unique event key, so retries never duplicate an event or ledger posting.

Use the historical command in preview mode first:

```powershell
python manage.py reconcile_commercial_history --json
```

After ambiguous PRs, POs and invoices have been resolved in the Project
Reconciliation screen, apply exact links and backfill provable events:

```powershell
python manage.py reconcile_commercial_history --apply --json
```

The apply mode deliberately does not fuzzy-match project text or automatically
approve invoice/PO allocations. Ambiguous records remain in manual review.
