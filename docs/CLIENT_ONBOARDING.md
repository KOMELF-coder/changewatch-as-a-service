# Client onboarding

Target: less than five minutes of trained operator setup with URLs and contact details ready. Initial crawling can take longer; never declare monitoring active until every agreed page has a successful baseline. No dashboard is required.

## Canonical customer contract

```json
{
  "client_id": "client-acme",
  "client_email": "alerts@client.com",
  "language": "fr",
  "timezone": "Europe/Paris",
  "alert_threshold": 60,
  "competitors": [
    {"name": "Example competitor", "url": "https://example.com/pricing"}
  ]
}
```

Replace example URLs before running. This is the standard production input; advanced reliability controls remain optional. Start from [Starter: five pages](../examples/client-starter.json) or [Business: fifteen pages](../examples/client-business.json). These contain reserved example domains and are configuration templates, not monitored production targets.

## Identity and task convention

Use one Apify saved Task per customer, named `changewatch-<client_id>`, for example `changewatch-client-acme`. Schedules must target that Task, with no input overrides, so edits to the saved customer input apply on future runs. Do not create duplicate tasks/schedules for the same customer.

`client_id` is the permanent storage namespace, not the customer's display name. Its canonical form is 1–200 ASCII lowercase letters/digits in groups separated by single hyphens: `client-acme`, `maison-dupont`, `agency-client-001`. Uppercase, accents, underscores, repeated/leading/trailing hyphens, slashes and whitespace are rejected. Normalization is deliberately strict: submit the canonical form; the Actor does not lowercase, trim or slugify IDs. Before assigning one, check existing tasks and the operator's access-controlled client record for uniqueness, including former customers. The Actor cannot establish business ownership of an ID.

Existing valid IDs and snapshot keys are unchanged. A legacy ID outside this format now fails validation; do not simply rename it in a live task. Stop its schedule, record the old namespace and choose a verified unused canonical ID. Using the new ID starts a fresh baseline; archive the old records under the agreed storage policy. Moving history requires a separately reviewed migration of both snapshot and monitor records, not an automatic conversion.

## Five-minute checklist

- [ ] **0:00–1:00 — Collect:** confirm the recipient address, 3–15 publicly accessible competitor URLs, a clear name for each, language (`fr`/`en`), timezone and threshold. Confirm the agreed page count; remove duplicate URLs.
- [ ] **1:00–2:00 — Configure:** reserve the unique client ID; open ChangeWatch in Apify Console, create a saved Task, name it `changewatch-<client_id>`, paste the contract and save. Check run memory/timeout are appropriate for the URL count. Keep the approved Actor build and existing Resend sender configuration.
- [ ] **2:00–3:00 — Initialize:** run the Task once. In its Dataset verify one result per URL, all `initialized`, `changed: false`, `alert_triggered: false`, `alert_sent: false`, and `monitoring_status: healthy`. A previously monitored URL can instead be `unchanged`; investigate unexpected prior state or any alert before proceeding. A successful overall run with some failed URLs is not complete onboarding.
- [ ] **3:00–4:00 — Activate:** after the baseline succeeds, create/enable one daily schedule targeting the saved Task. Choose a non-overlapping time and the schedule's intended timezone explicitly; the input's `timezone` controls email display, not scheduling. Verify next run time and absence of input overrides. If crawling is still running, leave activation pending and finish once checked.
- [ ] **4:00–5:00 — Confirm and record:** manually send the [welcome](templates/welcome-fr.txt) or [English welcome](templates/welcome-en.txt) after activation, using the actual successful page count and frequency. Record client ID, task name/ID/link, schedule ID/link, recipient, URL count, language/timezone, threshold, baseline run ID/time and support contact. Use [monitoring-active](templates/monitoring-active-fr.txt) / [English](templates/monitoring-active-en.txt) for a separate baseline confirmation when needed; do not send redundant messages automatically.

## MVP defaults

| Setting | Default |
| --- | --- |
| Monitoring | Daily, one schedule per customer |
| Alert threshold | 60 |
| French customer timezone | Europe/Paris |
| Confirmation | Automatic: stable ID/URL entity changes 1 run; other changes 2 |
| Identical-alert cooldown | 24 hours |
| Technical notifications | Enabled when `OPERATOR_EMAIL` and working Resend configuration exist |

Leave `confirmation_runs` unset. Two daily observations can delay a generic alert by a day. Before onboarding clients, verify shared Resend configuration and an internal delivery test once; do not change shared credentials for each client. Welcome and activation templates are sent manually; ChangeWatch sends only change alerts and optional operator technical notices.

## Changes to an existing customer

Pause the customer's schedule and wait for any active run to finish before editing saved Task input. Record the edit and resume the same schedule after checking the next run. Do not change `client_id` for routine edits.

| Edit | Procedure and snapshot effect |
| --- | --- |
| Add URL | Append `{name, url}` to `competitors`, save and run. A never-seen URL initializes without an alert. Other URLs can still produce legitimate alerts on that run. A previously removed URL with retained records resumes comparison with its old baseline; use the runbook's single-URL reset if a fresh start is intended. |
| Remove URL | Remove only its entry and save. Other URLs and their snapshots are unaffected. Old snapshot/monitor records remain until deliberately removed under policy. Remove its pending alerts from any manual follow-up list. At least one URL is required; use offboarding if removing all pages. |
| Rename competitor | Change only `name`; it affects future display text, not the client/URL snapshot key. Existing Dataset items and sent emails are unchanged. |
| Change email | Verify the new address with the customer, replace `client_email` and save. Future eligible alerts use it. No baseline reset, replay or automatic test email occurs. Remove outdated contact details from the operator record under policy. |
| Change threshold | Change `alert_threshold` (0–100), save and record rationale. Snapshots stay intact. Lowering it does not resend previously accepted changes from an unchanged page. |
| Change timezone | Set a valid IANA zone and save; future client email timestamps change. Dataset UTC and snapshots remain unchanged. Adjust the schedule separately if its execution time should also change. |
| Replace URL | Treat as removal plus addition. Host case, fragments and default ports are canonicalized; path/query changes can create a new identity. Review whether the normalized URL is actually new. |
| Add ignore rules | Use narrow selectors/patterns only after examining noise. Changing this URL's ignore configuration establishes a fresh baseline; other URLs are unaffected. |

Tasks and schedules are documented by Apify: [saved Tasks](https://docs.apify.com/actors/running/tasks), [schedules and input overrides](https://docs.apify.com/actors/running/schedules).

## Product boundaries

ChangeWatch monitors publicly accessible URLs supplied by the customer/operator. It cannot guarantee continued scrapeability. Product disappearance does not necessarily mean discontinuation. Alerts are monitoring signals, not guaranteed business facts; customers should verify material decisions at the source. Store client contacts, inputs, snapshots and run exports only in access-controlled locations. This checklist is not a legal or retention policy.
