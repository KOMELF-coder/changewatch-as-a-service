# Operator runbook

Use the client's saved Task, run history, Dataset and the shared named KVS `changewatch-snapshots-v1`. Keep an access-controlled operator record of client IDs, Task/schedule links and changes. No dashboard is required.

## Normal daily operation

A scheduled Task run produces one Dataset result per URL. Healthy unchanged pages send no email. Pending generic changes are normal until confirmed; stable ID/URL entity changes may alert immediately. Check per-URL `monitoring_status`, not just the overall run status: partial fetch failures can occur in a successful run. Technical notifications require `OPERATOR_EMAIL` and working provider configuration; they are not a substitute for reviewing missed runs.

## Investigation guide

| Symptom | Action |
| --- | --- |
| `degraded` / `failing` | Inspect URL, `consecutive_failures`, `last_success_at`, `last_failure_at`, `last_error` and safe run logs. One/two failures are degraded; three are failing. Confirm the URL still exists and is public. Fix the cause and rerun the Task without overlap; healthy resets the count. Failed fetches retain the last valid content snapshot. |
| HTTP 403 | Check public access and whether the site blocks automated fetching. Do not treat the last snapshot as fresh data. Ask the customer for a permitted alternative page or pause/remove that URL if unavailable. Do not bypass access controls. Repeated failures produce an operator notice when configured. |
| Timeout | Check network/site availability, affected page size and run timeout versus URL count. The Actor already retries selected transient failures. Avoid launching overlapping retries; rerun after recovery. Raising the total run limit does not solve a permanently blocked site. |
| False positive | Inspect changed fragments, entity identity, old/new values and source page. Classify the actual error before editing rules. Use narrow per-URL ignore rules for known irrelevant regions, or review the threshold with the customer. Preserve evidence; do not globally reset storage or weaken entity matching. |
| Repeated dynamic content | Inspect `dynamic_content_detected`, `state_seen_before`, confirmation fields and `alert_suppressed_reason`. A → B → A → B generic rotation is suppressed. Confirm it is banner/carousel noise; optionally ignore that selector. A changed ignore configuration reinitializes this URL. Do not ignore genuine product changes merely because they recur. |
| Duplicate suppression | Inspect `alert_fingerprint`, `duplicate_alert_suppressed` and `alert_decision_reason`. Compare prior accepted-send timestamps in the paired monitor record. Default cooldown is 24 hours; changed prices have different fingerprints. Check for duplicate schedules/tasks or input overrides. Do not remove dedup records to force a resend. |
| Expected email absent | First inspect `alert_decision_reason`, threshold, confidence and confirmation status. `no_client_email` permits a preview but no send. `alert_sent: true` means provider acceptance, not inbox delivery. Verify the recipient and use Resend delivery events to investigate accepted mail. |
| `email_error` / Resend failure | Verify secret presence, sender-domain configuration and provider status/logs. Never paste API keys into Dataset, chat or commits. Check whether a timed-out request was actually accepted before manually resending. Client delivery is not automatically retried on the next unchanged run; use the retained alert text for a deliberate manual follow-up. A provider outage may also prevent operator email. |
| Schedule failure / no recent run | Check schedule enabled state, next run, timezone, selected Task, input overrides, build availability, account usage/balance and run limits. Look for aborted/failed runs, not just missing alerts. Use Apify's run-failure notifications as a separate safeguard; this Actor cannot email if it never starts. After repair, run once and check every URL, then verify the next scheduled execution. |

An accepted operator failure notice is deduplicated until recovery; failed notification attempts are limited to once per hour per type. Recovery sends one notice. Do not assume silence proves health.

## Reinitialize one URL safely

Use only for a deliberate fresh baseline, corrupt state after investigation, or an approved restart of a previously removed URL. A reset deletes pending changes, history, health and deduplication for that URL and loses the comparison gap. It does not fix a 403, timeout or provider failure.

1. Pause every schedule/trigger using the exact client ID and wait until no matching run is active. Record why the reset is needed and export the two records if policy permits.
2. Find the URL's Dataset item and copy its `snapshot_key`. In `changewatch-snapshots-v1`, open that exact `snapshot-<hash>` record and verify its stored `client_id` and canonical `url` match the intended customer/page. If the Dataset is unavailable, inspect snapshot records for both fields; never guess from competitor display name. For missing/corrupt records, derive the key locally using the existing `snapshot_key(client_id, canonical_url(url))` helpers and verify against run evidence.
3. The paired state key is **the same hash**, with `monitor-` instead of `snapshot-`. Verify the pair and delete only these two records. Deleting just one is insufficient because the monitor record also contains the accepted baseline. Do not delete `INPUT`, unrelated keys or the entire shared KVS.
4. Save the original Task input, temporarily run it with only the target competitor and the same client ID, and inspect `initialized`, `changed: false`, `alert_sent: false`, `monitoring_status: healthy`. Other URLs' state remains untouched. If initialization fails, keep the schedule paused while investigating.
5. Restore the complete saved competitor list before resuming the schedule. Verify URL count and next run. Record the reset time and baseline run ID; tell the customer about any material monitoring gap.

**When not to delete KVS:** routine URL/name/email/threshold/timezone edits, pending confirmations, suppressed duplicates, network failures and offboarding one client never justify deleting the shared store. See [onboarding edits](CLIENT_ONBOARDING.md#changes-to-an-existing-customer) and [offboarding](CLIENT_OFFBOARDING.md).

## Weekly review (every active customer)

- [ ] Task is present and operational; input matches the agreed customer and recipient.
- [ ] Exactly the intended schedule is enabled, with correct next run and no stale input overrides.
- [ ] Last successful run is recent relative to the agreed frequency; inspect partial failures too.
- [ ] Review failing/degraded URLs and last successful observation per URL.
- [ ] Review `email_error`, provider delivery issues and operator notification failures.
- [ ] Review noisy URLs, repeated suppression and unsuitable ignore rules.
- [ ] Review cost/run in Apify run statistics and unexpected increases in runtime/usage.
- [ ] Compare configured URL count with the agreed count, including additions/removals.
- [ ] Record review date, findings, owner and next action in the operator record.

Platform references: [Task configuration](https://docs.apify.com/actors/running/tasks), [schedule behavior](https://docs.apify.com/actors/running/schedules), [record-level KVS operations](https://docs.apify.com/api/v2/storage-key-value-stores).

## Collection coverage

Check `collection_pages_fetched`, `collection_entities_found`, `collection_expansion_status` and reason on each root URL. A healthy fetch does not guarantee full collection coverage.

| Status | Interpretation / action |
| --- | --- |
| `not_applicable` | Single-page content without collection signals; ordinary monitoring continues. |
| `complete` | All discovered HTML pagination exhausted. Compare observed count with the site's advertised count manually; hidden APIs are not inferred. |
| `unsupported` | Visible load-more cannot be followed as a public HTML URL, or entities are not identifiable. Ask for a usable public paginated collection/product URL; do not reverse-engineer private APIs or bypass controls. |
| `limited` | 10 pages / 500 entities / 25 MB / 90 seconds bounded the scan. Review the reason. Prefer narrower category URLs instead of claiming the whole catalog is covered. |
| `partial` | Extra-page failure, repetition, no new identities or a suspicious coverage drop. Inspect the source and safe logs. Missing known products are protected from removal alerts; confident observed price changes may still alert. |
| `error` | Initial root fetch failed; diagnose access/timeouts as above. Last valid snapshot is preserved. |

JS-only infinite scroll is not supported unless the HTML exposes a directly usable same-origin additional HTML URL. Configuring explicit separate public URLs is possible but each then has its own baseline and contributes one monitored page to activity reports. Removing pagination controls with ignore selectors can reduce coverage; inspect ignore rules when counts suddenly fall.

On `collection_baseline_preserved`, do not delete the old good baseline merely to silence the diagnostic. Restore expansion and run again. If a real catalog shrink eliminates an entire pagination page, review evidence and decide whether a deliberate single-URL reset is appropriate; the conservative guard may otherwise retain those removals. First upgrade to aggregated coverage and first complete scan after an initial partial baseline intentionally initialize once without a scope-change alert.

## Weekly activity reports

No second scheduler is required. The first upgraded observation starts a seven-day period. Inspect the last configured URL's Dataset item for `weekly_report_*` fields and the client-scoped `weekly-*` KVS record (its stored `client_id` identifies the owner). Check these in the weekly operator review too.

- No report yet: verify seven elapsed days, a running daily schedule, configured `client_email`, and no accepted significant alert in the closed period. Weeks with accepted alerts intentionally skip the activity report. A newly upgraded client has no historical report backlog.
- Delivery failure: inspect `weekly_report_error`, credentials/provider events and the frozen `pending` period. Failed attempts retain it for a later run; only one pending period is kept. Do not delete the weekly state to force a resend.
- Uncertain sends are automatically held once 23 hours have passed since the first attempt, leaving a margin inside Resend's 24-hour deduplication window. Verify provider records; only after confirming no acceptance may you remove `pending.attempted_at` to permit another attempt. Never clear it merely because no email arrived in the inbox.
- Uncertain acceptance or persisted-send-state failure: check Resend first. Period idempotency protects identical requests within the provider's 24-hour window, not indefinitely. If already accepted, record that pending period as delivered (`last_weekly_report_at` with the actual UTC acceptance time and `pending: null`) only after pausing the Task and exporting/verifying the exact client record. Keep active-period counters unchanged. Resume once resolved.
- Recipient/language changed during an uncertain retry: inspect any HTTP 409 and prior provider acceptance. Do not rotate the idempotency key to force delivery. Resolve the old pending period deliberately before retrying with different content.
- Count mismatch: checks include failed attempts, each configured root URL counts once regardless of collection expansion, and health is the last observation per URL within the period. Mid-week URL edits can change the reported unique page count. Long schedule gaps must not be presented as successful checks.
- Offboarding: stop schedules and handle the client's `weekly-*` record as well as its snapshots/monitor state under the agreed policy. A single-URL reset should not erase client-wide activity history.

Report previews use the same compact transactional styling and contain no tracking, marketing or internal confidence/confirmation diagnostics. Full live acceptance instructions are in the [README](../README.md#pre-launch-rebuild-and-live-acceptance).
