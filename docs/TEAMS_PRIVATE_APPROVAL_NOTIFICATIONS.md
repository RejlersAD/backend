# Private Teams approval notifications

RADAI sends approval assignments to the standard Microsoft Teams **When a Teams
webhook request is received** trigger. Configure it for `Anyone`; the callback
URL is a secret. The flow must use the Microsoft Teams **Post a message in a chat or channel** action
with these values:

- Post as: `Flow bot`
- Post in: `Chat with Flow bot`
- Recipient: `recipient_email` from the HTTP body
- Message: use the supplied `message` field. It includes the PO number, project
  name/code, service, description, vendor, recorded value with currency, current approval level,
  submitter, and `Open Request` link. Alternatively, post the supplied Adaptive
  Card from `attachments[0].content` to the same private recipient.

## Update an existing Power Automate flow

The flow is managed outside this repository. Deploying the backend alone does
not update a flow that builds its own message from individual fields.

1. In the private Flow-bot action, replace the old message mapping with the
   request body's `message` dynamic value. If using an Adaptive Card action,
   use the request body's `attachments[0].content` instead.
2. Remove the old Due Date row and any required `due_date` property from a custom
   Parse JSON schema. Due dates are no longer sent or rendered in Teams.
3. If retaining a custom layout, map `po_number`, `project_name`, `project_id`,
   `service`, `description`, `vendor`, `value`, `approval_level`, and `submitted_by`. Check
   `approval_level` for null, not truthiness: Level 0 is a valid approval level.
4. Keep the recipient mapped to `recipient_email` and the action URL mapped to
   `action_url`. Save the flow and verify a controlled approval through the
   configured levels after deploying the backend.

Configure the backend deployment with:

```text
TEAMS_APPROVAL_WEBHOOK_URL=<Power Automate HTTP trigger URL>
TEAMS_APPROVAL_WEBHOOK_TIMEOUT=10
FRONTEND_URL=https://radai.ae
```

Example request body sent by RADAI:

```json
{
  "type": "message",
  "event_type": "approval_assignment",
  "recipient_email": "approver@rejlers.ae",
  "recipient_name": "Approver Name",
  "title": "New approval request assigned",
  "request": "Purchase Order RAD-PRJ-PUR-0117_2026",
  "po_number": "RAD-PRJ-PUR-0117_2026",
  "project_name": "Engineering services project",
  "project_id": "RAD-PRJ-2026-0042",
  "service": "Design review and verification",
  "description": "Engineering design assurance services",
  "vendor": "Example Supplier LLC",
  "value": "AED 12,500.00",
  "currency": "AED",
  "approval_level": 0,
  "submitted_by": "Requester Name",
  "action_label": "Open Request",
  "action_url": "https://radai.ae/approvals?tab=procurement",
  "message": "New approval request assigned\nRequest: Purchase Order RAD-PRJ-PUR-0117_2026\nPO Number: RAD-PRJ-PUR-0117_2026\nProject Name: Engineering services project\nProject ID: RAD-PRJ-2026-0042\nService: Design review and verification\nDescription: Engineering design assurance services\nVendor: Example Supplier LLC\nValue: AED 12,500.00\nApproval Level: Level 0\nSubmitted By: Requester Name\nOpen Request: https://radai.ae/approvals?tab=procurement",
  "notification_id": "...",
  "attachments": [
    {
      "contentType": "application/vnd.microsoft.card.adaptive",
      "contentUrl": null,
      "content": {
        "type": "AdaptiveCard",
        "version": "1.4",
        "body": [],
        "actions": []
      }
    }
  ]
}
```

The actual Adaptive Card carries the same facts as `message`; its `body` and
`actions` above are abbreviated. Missing PO numbers on pre-PO requisitions show
`Not issued`. Unavailable details show `Not specified`. Value is the saved PR/PO
total, with no additional tax calculation or assumed VAT treatment.

## Approval notification sequence

- Only the lowest unresolved numeric level receives actionable notifications:
  Level 0, then Level 1, Level 2, and the remaining configured levels through CEO.
- All approvers at the same level must approve before the next level is notified.
- Draft PR edits and future-level assignment edits do not send early alerts.
- A rejected workflow stops progression; later pending levels are not notified.
- The CEO no longer receives an automatic PO-created FYI. Buyer-reference FYIs
  go only to users outside the configured approval chain.
- The existing conditional CEO rule is preserved: PRs with PO applicable omit
  the PR CEO stage; PRs without a PO retain it. PO notifications follow the PO's
  configured management stage. These changes do not add or reassign approvers.
- Legacy PR assignment-update notices do not suppress a later actionable alert.
- Opening the Teams message does not approve the request or mark its RADAI
  notification read. Decisions remain in RADAI.
- CEO and administrator access does not override the assigned approver or the
  active level. Approval and rejection endpoints, queues and notification
  previews use the same eligibility rules.
- After submission, a normal edit cannot remove or move approval levels, change
  a decided assignment, or toggle the PO choice to remove a required CEO stage.
  Pending assignees can be corrected within their existing positions. Concurrent
  edits preserve decisions committed before the edit acquires its database lock.
- Digital signatures are snapshots of the acting user's saved profile signature;
  client-supplied signature bytes are not accepted as approval evidence. Each
  decision records actor identity and signature ownership. Procurement Level 0
  does not populate the legacy VP signature fields.
- Known signer/assignee conflicts block further approvals and notifications and
  show a review warning instead of a misattributed signature. Stored historical
  evidence is retained for review; these checks do not automatically reset or
  reassign prior approvals. Reviewed source PDF evidence follows its existing
  separate verification path.

Teams HTTP failure is retried up to three times and written to the notification
audit log. `teams_sent` means the webhook accepted the request; check the Flow
run and recipient chat to verify final delivery. Queue failures are caught so
the in-app workflow can continue. With eager Celery enabled, delivery runs in
the request process and can add latency; normal asynchronous delivery requires
the configured broker and worker.

## Browser push: recipient, sequence, and action

1. The approval service saves the decision under a database lock. After commit,
   it reloads the request and creates notifications only for active employees
   assigned to the current unresolved level. Ambiguous email matches are not
   guessed. All approvers at that level must finish before the next level opens.
2. Each assignment has a server-owned identifier. Reassigning A to B and later
   back to A produces a new identifier, so an old queued alert cannot become
   current again just because the recipient matches.
3. The push worker rechecks the current recipient, level, assignment identifier,
   request state and user preferences before delivery. Completed, reassigned,
   expired and archived requests are skipped. The same approval checks protect
   queued Teams and email delivery.
4. Browser subscriptions belong to one authenticated account. Push payloads
   identify the intended recipient; the service worker checks its account binding
   before showing a notification. Logout and session expiry clear that binding,
   close existing notifications, and unsubscribe the browser.
5. Notification clicks open a same-origin RADAI record. Opening an alert does not
   approve it. The page reloads approval eligibility, and every approve/reject
   call checks the assigned employee and current level again on the backend.

Delivery logs distinguish successful subscriptions, obsolete jobs, expired
subscriptions, and transient failures. Retries target failed subscriptions and
do not resend to browsers already recorded as delivered. A delivery record means
the push endpoint accepted the message; browser/OS display still depends on the
recipient's notification permission and device availability. Notifications
already displayed on a device may become stale, but cannot authorize an action.
