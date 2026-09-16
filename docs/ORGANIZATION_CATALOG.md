# RAD organization catalog

Source: **Rejlers Abu Dhabi Corporate Organization Chart**, document
**RAD-HM-CHT-0001, Rev. 6**, dated **12 December 2025**. The supplied file is
`RAD Organization Chart (With Photo) Rev 6.pdf`.

`apps/rbac/organization_catalog.py` is the shared reference for department and
organizational-role choices. The API exposes it at
`GET /api/v1/rbac/users/organization-catalog/` to authenticated users.

The frontend displays the catalog under **Roles & Access Management →
Organization structure**. The searchable view shows the source revision,
department functions, positions, recorded incumbents, and reporting lines.
It is a reference view; access-role editing remains in the Roles tab.
Profile, admin user creation/editing, HR employee editing, and onboarding reuse
the catalog for department and job-title suggestions.

## Department and function groups

| Group | Parent | Chart head |
| --- | --- | --- |
| Management | — | Jarmo Suominen |
| Sales & Business Development | Management | Anam Abbas |
| Operations & Project Delivery | Management | Mohamad El-Ghawanmeh |
| HR & Administration | Management | Sanglin Samuel (Acting) |
| Finance & ICT | Management | Aleksi Murtomaki |
| Project Management | Operations & Project Delivery | Jamal Ayoub |
| Engineering | Operations & Project Delivery | Rafat Saqer |
| QHSE | Operations & Project Delivery | Shaju Chacko |
| Procurement | Operations & Project Delivery | Richa Thomas |
| Artificial Intelligence (AI) | Operations & Project Delivery | No individual head specified |
| Project Controls | Project Management | Timothy Dolan |
| Process Engineering | Engineering | Debasis Sana |
| Civil & Structural | Engineering | Sherwin Mapaye |
| Instrumentation & Control | Engineering | Sanu Jacob |
| Electrical | Engineering | Swapnil Linge |
| Piping / Mechanical / Pipeline | Engineering | Amit Thakur (Acting HOD) |

Management and Engineering are catalog grouping labels for the chart's CEO and
Manager of Engineering branches. The chart's functional bullet points remain
responsibilities within those groups. They do not create additional manager
positions. In particular, **AI Team / RIN Growth** belongs to Operations &
Project Delivery, while **Rejlers India (RIN) Manager / Engineering Systems
Administration** belongs to Engineering.

## Organizational roles

The catalog includes 21 positions: the 15 named positions, Engineering Manager,
Project Director, Proposal Engineer, Rejlers India (RIN) Manager, Engineer, and
Designer. Plural team labels are singularized for individual job-title choices.
Executive secondary titles and acting appointments are retained as source data.
The generic Engineer and Designer positions have no fixed individual reporting
manager in the document, so the catalog does not assign one.

Names and reporting lines in this catalog describe the supplied revision. They
do not automatically update live employee assignments. EmployeeMaster and
UserProfile retain their existing departments, titles, and managers.

## Access and compatibility

Organizational roles are job positions; `rbac_roles` continues to define RADAI
application access. This update does not create, delete, rename, or assign
access roles or modify module/permission grants. The new catalog endpoint is
read-only and never seeds or synchronizes data on access.

Department and job-title lookup endpoints include the chart choices alongside
existing stored values. Forms retain an existing value even when it is absent
from the chart. Loading metadata or saving another field must not rewrite that
value. Catalog aliases are solely for display and filtering; they must never
be used to infer permission grants or mass-normalize employee departments.

The existing discipline-based authorization configuration and biometric
department-to-access-role mapping are independent and unchanged. Future
employee reassignments require an explicit update through the employee forms;
the organization catalog itself makes no such changes.

No database migration or role-seeding command is required. Deploy backend and
frontend together to make the updated choices and organization reference view
available. Named incumbents are not resolved to application accounts by name.
