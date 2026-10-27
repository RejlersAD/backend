# EXEC-ACCESS-01 — Executive tab access

## EXEC-ACCESS-02 - assignment repair, 27 October 2026

Effective tab grants now supply Executive workspace read entry without separate
role/module/read grants. Explicit read denies, inactive/locked/deleted identities
and disabled modules still block access. Source/record restrictions and separate
write/upload/export permissions remain. Current-user modules and action metadata
use the same decision; Executive module cache cannot retain revoked tab grants.
All six tabs are assignable. rbac.0070 adds view_commercial, view_workforce and
view_risk without assigning users or roles. Broad readers retain those tabs unless
explicitly denied; tab-only users see only granted tabs. Denied objects are withheld.
Permission review uses effective role windows. This supersedes the entry prerequisite
in the historical sections below. 27 backend and 9 browser checks passed. Local
rbac.0070 was applied after verifying postgres_local/radai_dev and its one-migration
plan; workers reloaded. Production and live assignments are unchanged.
Shared workspace scope: ../../docs/features/executive-assignment-repair.md.


## Local activation and Super Administrator verification — 23 October 2026

The local running workers were returning the older report without allowed_tabs,
and rbac.0065 was unapplied. Applied only that migration to the verified
postgres_local/radai_dev database and reloaded local Gunicorn using HUP.
Authenticated backend and frontend-proxied report reads now return HTTP 200 and
all six tabs for the user's active Super Administrator membership. The catalogue
returns all three new permission definitions. No role memberships or individual
overrides were changed; the user's Executive deny count is zero. Production is
unchanged. The earlier non-activation statements below describe the original
implementation stage, before this explicitly requested local runtime repair.

Authorized 23 October 2026: keep existing Roles & Access Management and reuse
reviewed role grants and individual Allow/Deny/Inherit overrides.

| Permission | Action | Tab |
| --- | --- | --- |
| executive_dashboard.view_overview | view_overview | Overview |
| executive_dashboard.view_financial | view_financial | Financial performance |
| executive_dashboard.view_portfolio | view_portfolio | Project portfolio |

Executive module/read and existing source/record restrictions remain required.
Distinct tab actions prevent one tab denial from denying the module read action.
Explicit individual denies apply to Super Administrators. Existing review,
stale-snapshot, audit and self-edit protections remain unchanged.

Migration 0065_executive_tab_permissions adds choices and definitions only,
after 0064_userprofile_digital_stamp. It assigns no roles or individual grants.
Ordinary existing readers need explicit new grants. Super Administrator implicit
access remains. Reversal leaves definitions without rewriting assignments.

GET /api/v1/dashboard/executive/ adds allowed_tabs; optional tab rejects unknown
values with 400 and denied tabs with 403. With no tab, return the permitted
projection for client navigation. Without Overview, clear common headline,
department, action, portfolio, workforce and coverage records. Without Financial,
omit financial_performance. Without Portfolio, omit portfolio_performance,
except that authorized Overview retains its operational summary without the
workbook/revenue_dashboard/source fields. Overview independently authorizes
its cross-department summaries even when a dedicated tab is denied.

Executive invoice readers require Overview or Financial. Portfolio workbook,
revenue and invoice readers, upload preview and import require Portfolio.
Existing upload authority and source/record restrictions remain required.
Commercial, Workforce and Risk retain their existing access policy. Responses
remain private/no-store; exports use filtered reports.

Deploy the matching frontend/backend together, migrate the intended environment,
then assign permissions using the existing reviews. This task does not apply
live migrations, change live role assignments or deploy.

Verification command (isolated SQLite):

    ..\.venv\Scripts\python.exe -X utf8 manage.py test apps.dashboard.tests_executive_access apps.dashboard.tests_executive apps.dashboard.tests_executive_finance --settings=config.settings_portfolio_test --noinput

All 15 new access tests and 7 existing individual-override checks passed (22/22).
The earlier combined Executive run passed 78/80. The two Finance
expectation failures reproduce with HEAD's unchanged Executive views: outdated
balance definition text and additional sharepoint_sync response metadata.
See workspace artifacts/executive-tab-access-backend.log and
artifacts/executive-tab-access-baseline.log.

The extended Portfolio run reports 8 failures and 1 error; all reproduce with
HEAD's unchanged Portfolio/Executive views (baseline-expanded log). These concern
existing workbook parsing/validation expectations, outside tab authorization.
All 7 browser tests passed, including actual role/user review payloads, tab
revocation/deep links and Finance/Portfolio source reporting.
