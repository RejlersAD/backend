# Private Teams approval notifications

RADAI sends approval assignments to a Power Automate HTTP-triggered flow. The
flow must use the Microsoft Teams **Post a message in a chat or channel** action
with these values:

- Post as: `Flow bot`
- Post in: `Chat with Flow bot`
- Recipient: `recipient_email` from the HTTP body
- Message: build from `title`, `request`, `submitted_by`, `due_date`, and add an
  `Open Request` link using `action_url`

Configure the backend deployment with:

```text
TEAMS_APPROVAL_WEBHOOK_URL=<Power Automate HTTP trigger URL>
TEAMS_APPROVAL_WEBHOOK_TIMEOUT=10
FRONTEND_URL=https://radai.ae
```

Example request body sent by RADAI:

```json
{
  "event_type": "approval_assignment",
  "recipient_email": "approver@rejlers.ae",
  "recipient_name": "Approver Name",
  "title": "New approval request assigned",
  "request": "Purchase Requisition RAD-PRJ-PR-0001_2026",
  "submitted_by": "Requester Name",
  "due_date": "05-Sep-2026",
  "action_label": "Open Request",
  "action_url": "https://radai.ae/approvals?tab=procurement",
  "message": "New approval request assigned\nRequest: Purchase Requisition RAD-PRJ-PR-0001_2026\nSubmitted By: Requester Name\nDue Date: 05-Sep-2026\nOpen Request: https://radai.ae/approvals?tab=procurement",
  "notification_id": "..."
}
```

Teams failure is retried three times and written to the notification audit log.
It never blocks the in-app assignment or the approval workflow.
