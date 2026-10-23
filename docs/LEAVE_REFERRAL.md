# HR-LEAVE-REFER-01 — Leave approval referral

User-authorized 23 October 2026. Payroll remains the leave owner; the existing
HR workflow owns task assignment and immutable events. No migration is needed.

`GET /api/v1/payroll/leave-requests/{id}/referral-options/` returns
`{results: [{id: <user ID or null>, employee_id, name, employee_code,
department, eligible}]}`. Requires the current review assignment and positive
payroll approve access. Following the user's employee-search correction, results
include every canonical employee and RADAI accounts awaiting canonical linkage;
no role/designation filter or arbitrary result cap applies. Only identity and
department metadata is returned. The picker searches names, codes and departments
locally across the complete response. Employee rows without a login remain
visible with `id: null` and cannot receive an approval task. `eligible` describes
existing approval eligibility; POST still revalidates authority. Browsing the
directory does not grant payroll or final-HR decision permission.

`POST /api/v1/payroll/leave-requests/{id}/refer-to/` accepts integer `approver_id`
and optional `note` (max 4,000 characters), returning the leave representation.
Authorization is repeated server-side under leave/workflow/task locks. Active
payroll approval access is required for the target; self referral and employee
self approval are forbidden. HR-stage targets also need current HR eligibility.
The original stage definition and remaining route do not change. Repeated/stale
commands from the old reviewer fail after responsibility transfers. Further
referrals by the current reviewer are independently audited.

The task stays pending with no decision timestamp/actor. A `referred` event
records actor, stage, task ID, previous assignee/role, new assignee, display-name
snapshots, timestamp and note in the same transaction. Audit write failure rolls
back the command. The existing notification service resolves the new recipient
after commit and handles in-app delivery. Reminder/escalation notifications
respect the referred assignment. Other workflow domains retain their behavior.

Leave responses add read-only `can_refer` and `referral_history` (id, stage,
actor_id, actor, approver_id, approver, at, note). Existing approval_steps retain
stage decisions; participants retain read access to that leave's history.
`PENDING` and `RM_APPROVED` retain their wire meanings and both use status_display
`Pending Approval`. Referral changes neither status nor balances/dates. Earlier
manager approval remains visible when HR refers. Final approval/rejection still
uses the original stage decision endpoints and checks.

Legacy requests without a workflow/task cannot refer; their existing decisions
remain supported. No historical workflow or missing approval is fabricated.

Verification uses `config.settings_leave_test`, synthetic data, explicit
approval grants and the production HTTP guard. SQLite does not verify concurrent
PostgreSQL locking. Browser fixtures use the actual React review component;
no real leave request or business notification is needed for checks.
