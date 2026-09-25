# Private Teams approval notifications

RADAI sends approval assignments to the standard Microsoft Teams **When a Teams
webhook request is received** trigger. Configure it for `Anyone`; the callback
URL is a secret. The flow uses the Microsoft Teams **Post a message in a chat or
channel** action with these values:

- Post as: `Flow bot`
- Post in: `Chat with Flow bot`
- Recipient: `recipient_email` from the HTTP body
- Message: use backend-generated `message_html` as described below. Alternatively,
  post the supplied Adaptive Card from `attachments[0].content` to the same
  private recipient.

## Compact procurement approval message

For `approval_assignment` events, the heading is **🚨 NEW PO – APPROVAL REQUIRED**
or **🚨 NEW PR – APPROVAL REQUIRED**, matching the source document type. The
visible fields appear in this order:

| Label | Source and display |
| --- | --- |
| PR/PO | The source document's `request_number`; a PO may fall back to `po_number`. A recommendation always keeps its own PR identity. |
| Project | `project_id` (the displayed project code) and `project_name`, joined with ` – ` when both are available. |
| Service | A short, readable summary of `service`. |
| Vendor | The saved vendor display name. |
| Value | The saved PR/PO total with its currency, without another tax calculation. |
| ⌛ Due for approval | The recorded approval timestamp, or `Not specified`. |

The heading and labels are bold, and the entire Value line is bold. One `<br>`
separates consecutive rows inside a single paragraph; no blank source lines or
extra paragraphs are inserted. Procurement summaries flatten embedded line
breaks and limit PR/PO to 300 characters, Project to 220, Service and Value to
180, and Vendor to 160. Shortened summaries end with `…`; the canonical
**Open Request** action opens the full record. Long descriptions, duplicate
request rows, linked PO rows, submitter and approval level are omitted from
this visible summary. Text can still wrap naturally in a narrow Teams window.

Buyer-created PO FYIs use the same compact detail rows but retain their existing
informational title and omit the approval-deadline row. Non-procurement messages
retain their existing template and detail fields.

### Approval deadline and compatibility

The optional top-level `approval_due_at` field contains an ISO timestamp or
`null`. For a PR, the procurement context uses its saved `review_due_at`; for a
PO it is `null`, because the PO model has no canonical approval deadline. Aware
timestamps display their supplied offset, for example
`25 Sep 2026, 16:30 UTC+0400`. Missing, invalid or timezone-naive values display
`Not specified`. Delivery dates, required dates, linked PR deadlines and assumed
SLAs are never substituted for a PO approval deadline. The removed legacy
`due_date` field remains unsupported.

Existing top-level detail fields remain available for consumers: `request`,
`description`, `project_name`, `project_id`, `po_number`, `service`, `vendor`,
`value`, `approval_level`, and `submitted_by` retain their names and types.
Description previews retain their 1,500-character limit, service 700, and other
detail display fields 300, with `… (Open Request for full text)` on truncation.
These limits are distinct from the shorter visible procurement summary. Level 0
remains valid in the payload even though the compact summary omits that row.
The `title`, `message`, `message_html` and Adaptive Card reflect the compact
layout. `message` uses one newline between rows; Adaptive Card facts use text
runs with explicit bold weights and no inter-row spacing.

The backend converts saved rich text to readable text before building webhook
fields. Pasted editor tags, attributes, comments, scripts and hidden elements
are removed. Empty editor content falls back to the next available procurement
description. Saved PR/PO content is unchanged. The backend escapes field text
and the action URL in `message_html`; use that fragment directly without escaping
the whole fragment again. Individual top-level fields remain plain text, not
trusted HTML.

## Update an existing Power Automate flow

The flow is managed outside this repository. Deploying the backend updates its
incoming field values, but does not change a custom message layout in the flow.

1. In the private Flow-bot action, replace the complete old Message layout with
   `triggerBody()?['message_html']` through the expression editor. Enter the bare
   expression there, without `@{...}` around it.
2. Alternatively, select the `</>` button in **Parameters > Message** and paste
   the entire contents of [`teams-approval-message.html`](teams-approval-message.html):

   ```text
   @{triggerBody()?['message_html']}
   ```

   This inline expression belongs in the message editor's HTML view, not the
   action's separate Code view. It consumes the same centralized backend
   template. Do not add another paragraph, individual field rows or blank lines
   around it. An Adaptive Card action instead uses `attachments[0].content`.
3. Remove any required legacy `due_date` property from a custom Parse JSON
   schema. If the schema lists `approval_due_at`, allow `string` and `null` and
   keep it optional for compatibility. `approval_level` accepts `integer` and
   `null`. The direct `triggerBody()` expression does not require new fields to
   appear in the dynamic-content picker.
4. Keep the recipient mapped to `recipient_email`. The generated message already
   contains the canonical `action_url` link. Save the flow after the backend
   version supplying this template is deployed.

During a separately authorized delivery check, inspect the flow run and recipient
chat: approval headings should identify PR or PO, the six compact fields should
have no blank rows, saved PR deadlines should include their UTC offset, and POs
without a deadline should show `Not specified`. Buyer FYIs must remain
informational. **Open Request** must open the intended source record.

Editing these repository files does not update, deploy or test the hosted flow.
Previously delivered Teams messages are unchanged. Local regression checks use
mocked delivery and send no messages to recipient chats.

Microsoft references: [Flow bot messages to a user](https://learn.microsoft.com/en-us/power-automate/teams/send-a-message-in-teams#post-a-message-as-the-flow-bot-directly-to-a-user)
and [workflow expression functions](https://learn.microsoft.com/en-us/azure/logic-apps/expression-functions-reference).

Configure the backend deployment with:

```text
TEAMS_APPROVAL_WEBHOOK_URL=<Power Automate HTTP trigger URL>
TEAMS_APPROVAL_WEBHOOK_TIMEOUT=10
FRONTEND_URL=https://radai.ae
```

Illustrative PO approval request body (example values, not a production record):

```json
{
  "type": "message",
  "event_type": "approval_assignment",
  "entity_type": "purchase_order",
  "entity_id": "example-po-id",
  "request_number": "RAD-PRJ-PUR-0117_2026",
  "recipient_email": "approver@example.com",
  "recipient_name": "Approver Name",
  "title": "🚨 NEW PO – APPROVAL REQUIRED",
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
  "approval_due_at": null,
  "submitted_by": "Requester Name",
  "action_label": "Open Request",
  "action_url": "https://radai.ae/approvals?tab=procurement",
  "message": "🚨 NEW PO – APPROVAL REQUIRED\nPR/PO: RAD-PRJ-PUR-0117_2026\nProject: RAD-PRJ-2026-0042 – Engineering services project\nService: Design review and verification\nVendor: Example Supplier LLC\nValue: AED 12,500.00\n⌛ Due for approval: Not specified\nOpen Request: https://radai.ae/approvals?tab=procurement",
  "message_html": "<p><b>🚨 NEW PO – APPROVAL REQUIRED</b><br><b>PR/PO:</b> RAD-PRJ-PUR-0117_2026<br><b>Project:</b> RAD-PRJ-2026-0042 – Engineering services project<br><b>Service:</b> Design review and verification<br><b>Vendor:</b> Example Supplier LLC<br><b>Value: AED 12,500.00</b><br><b>⌛ Due for approval:</b> Not specified<br><a href=\"https://radai.ae/approvals?tab=procurement\">Open Request</a></p>",
  "notification_id": "example-notification-id",
  "attachments": [
    {
      "contentType": "application/vnd.microsoft.card.adaptive",
      "contentUrl": null,
      "content": {
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
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
`actions` above are abbreviated. Unavailable details show `Not specified`. A PR
summary shows its PR number; its legacy top-level `po_number` can still be
`Not issued` when no PO is linked. Value is the saved PR/PO total, with no
additional tax calculation or assumed VAT treatment.

## Approval notification sequence

New native Purchase Orders must contain an eligible assigned PO approval stage.
Linking a PR no longer removes the PO's Final Management Sign-off. The standard
form exposes that existing signatory selection and retains the configured CEO;
the backend continues to validate the employee's official position and PO
approval permission. Mohamad's operations title is eligible for VP Delivery
only. Existing unassigned native drafts can be configured through that same
form, and saving the assignment queues the PO request once.

PR-to-PO conversion validates the existing default final signatory before
creating the PO. It retains source PR decisions as explicitly external history
and opens a separate pending PO sign-off, with notifications queued after
commit. It does not turn source PR decisions into recorded PO signatures.
Historical source-document imports retain their evidence-only behavior.

Native PO association preserves unfinished PR approvals. The PR becomes
converted only after its own final approval completes. An unassigned draft's
PDF/DOCX now says "Approval not requested / No approver assigned"; a genuinely
assigned pending stage retains "Approval pending / Not yet approved".

- Purchase Recommendations and Purchase Orders send independent RADAI and Teams
  notifications for their own active approval levels. Linking a PO to a PR does
  not combine their notification rows, delivery tracking, or read/delete state.
- Procurement payloads identify their own `entity_type` (`purchase_recommendation`
  or `purchase_order`), `entity_id`, and `request_number`. Existing `pr_id` and
  `po_id` metadata remains supported. Teams approval headings identify PR or PO;
  a recommendation shows its own PR number. Linked PO details remain available
  in the payload and source record, outside the compact Teams summary.
- Preview navigation and delivery revalidation use the explicit document type.
  A linked reference cannot redirect a recommendation alert to its PO. Older
  notifications without a type retain the backend's PO-first interpretation.
- Only the lowest unresolved numeric level receives actionable notifications:
  Level 0, then Level 1, Level 2, and the remaining configured levels through CEO.
- All approvers at the same level must approve before the next level is notified.
- Draft PR edits and future-level assignment edits do not send early alerts.
- A rejected workflow stops progression; later pending levels are not notified.
- The CEO no longer receives an automatic PO-created FYI. Buyer-reference FYIs
  go only to users outside the configured approval chain.
- The existing conditional CEO rule is preserved: PRs with PO applicable omit
  the PR CEO stage; PRs without a PO retain it. PO notifications follow the PO's
  configured management stage. Existing PR approvers are not reassigned.
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
