# ChangeWatch-as-a-Service

A Python Apify Actor for competitor page monitoring: URL → normalized snapshot → comparison → deterministic importance score → structured Dataset result. No external AI service is required.

## Deploy on Apify

1. Create an Actor in Apify Console and choose **Git repository** in Source.
2. Use `https://github.com/KOMELF-coder/changewatch-as-a-service.git`, branch `main`, repository root. Authorize GitHub access if private.
3. Build. `.actor/actor.json` points to the Dockerfile and input schema. The container uses Python 3.12 and runs `python -m src`.
4. Start with 256–512 MB memory and a run timeout appropriate for the page count (up to approximately 185 seconds per page with all retries).
5. Enter input and run. Inspect the run Dataset and named `changewatch-snapshots-v1` Key-Value Store.

The input UI provides a Client ID and individually editable competitor name/URL entries. Replace the illustrative example URL with a real page; example.com/pricing may return 404.

## Input

```json
{
  "client_id": "demo-client",
  "alert_threshold": 60,
  "language": "en",
  "competitors": [
    {"name": "Competitor A", "url": "https://example.com/pricing"}
  ]
}
```

Supply 1–100 distinct HTTP(S) URLs. Client IDs and names must be nonempty and at most 200 characters; URLs at most 2,048 characters without credentials. Invalid input fails before processing. Fragments/default ports are removed and hostnames lowercased. Paths/query strings remain significant. Duplicate canonical URLs are rejected.

## Output

One result per page: `initialized`, `unchanged`, `changed`, or `error`. Fetch failures preserve the previous snapshot. An error's `changed: false` is not evidence of unchanged content. All fetches failing marks the run failed after writing error records; mixed runs complete, so inspect individual statuses.

Example comparing `Monthly plan price $10` with `Monthly plan price $20` (hashes abbreviated):

```json
{
  "client_id": "demo-client",
  "competitor": "Competitor A",
  "url": "https://example.com/pricing",
  "status": "changed",
  "changed": true,
  "importance_score": 90,
  "change_summary": "1 text change(s); concepts: pricing, subscription. Before: $10. After: $20.",
  "previous_hash": "sha256-of-previous-text",
  "current_hash": "sha256-of-current-text",
  "detected_at": "2026-09-11T12:00:00+00:00",
  "snapshot_key": "snapshot-sha256-of-client-and-url",
  "similarity_ratio": 0.75,
  "matched_concepts": ["pricing", "subscription"],
  "changes": [{"removed": "$10", "added": "$20"}],
  "diff_truncated": false
}
```

Actual hashes are SHA-256 hex strings. First runs have null previous hash/similarity, score 0 and no alert. Unchanged results have score 0 and similarity 1. Errors have a null current hash and an `error` field. Timestamps are UTC.

## Fetching, comparison and scoring

Requests use a browser-style user agent, TLS verification, 10-second connection / 30-second network-operation timeouts, a 60-second total attempt limit and five redirects. Transport failures, timeouts, HTTP 408/429 and selected 5xx responses receive up to three attempts with 1- and 2-second backoff. Fetches are sequential. Limits are 5 MB decompressed HTML and 200,000 extracted characters; exceeding either produces an error, not a truncated baseline.

BeautifulSoup removes scripts, styles, navigation, footers, hidden elements and common inline hiding styles. Unicode/whitespace normalization ignores formatting-only changes that preserve text. Word-level diffs include up to ten changed sections and 500 characters per side; `diff_truncated` indicates omissions. Snapshots retain full normalized text.

Base score = `min(100, 10 + round(20 * (1 - similarity)) + concept weights + monetary bonus)` for changed pages only. Each concept group counts once. Keywords support English and French:

| Group | Weight |
| --- | ---: |
| Price / pricing / cost / prix / tarif / coût | 30 |
| Discount / sale / promotion / promo / réduction / remise / soldes | 25 |
| Product / service / produit / nouveau produit / nouveau service | 25 |
| Shipping / delivery / livraison / frais de port | 15 |
| Subscription / monthly / annual / free trial / abonnement / mensuel / annuel | 25 |
| Feature / fonctionnalité | 15 |
| Launch / launched / lancement / nouveauté | 25 |
| Availability / stock / available / unavailable / disponibilité / disponible / indisponible / rupture | 20 |

Concepts come from removed/added diff fragments, not a scan of the full page. An immediately preceding commercial label (optionally ending in a colon) can supply context: editing `gratuite` in `Livraison gratuite` matches shipping, and editing the number in `Prix : 99 €` matches pricing. Only the nearest such label is used, within four preceding tokens; trailing unchanged text and other nearby sections are excluded. Concepts from separate edits are combined in stable order. Detected monetary price changes explicitly add `pricing`, even without a price keyword. This conservative rule can omit concepts whose relationship to the edit requires longer-range interpretation.

The existing monetary bonus remains unchanged: currency amounts or percentages within six neighboring words of an edit add 20. Price-change score floors also remain unchanged, so removing irrelevant concept matches does not remove the high score guaranteed by a material price change. `change_summary` uses the same refined concept list as `matched_concepts`.

For example, `Prix : 99 €` → `Prix : 79 €` produces only `pricing`, even with unchanged product or delivery text nearby. Changing `Livraison gratuite` to `Livraison 4,99 €` in the same run also adds `shipping`. This is a prioritization heuristic, not a probability. Every normalized text change is reported; consumers can filter by `changed` and a chosen score threshold. Similarity uses word-level Python `SequenceMatcher` with its frequent-token heuristic enabled for large repetitive pages.

### Structured price changes

Monetary parsing supports symbols before or after amounts, EUR/USD/GBP codes, comma/dot decimals and conventional thousands separators: `€99`, `99 €`, `99,99 €`, `$99`, `99 USD`, `£99`, `1 299,99 €`. Bare `$` means USD; no currency conversion is performed.

Prices are compared within aligned text edits. A replacement must contain exactly one old and one new amount in the same currency. Additions, deletions, currency changes and ambiguous multi-price replacements do not produce structured price changes. Separately aligned price edits can produce multiple entries in `price_changes`; the top-level fields describe the first entry with the highest scoring tier. Existing snapshot records need no migration.

The final score is the larger of the base score and the price-change floor, capped at 100:

| Absolute percentage change (increase or decrease) | Minimum score |
| --- | ---: |
| Any detected price change | 60 |
| At least 5% | 70 |
| At least 10% | 80 |
| At least 20% | 90 |

For `Prix : 99 €` → `Prix : 79 €`, additional fields are:

```json
{
  "change_type": "price_change",
  "old_price": 99,
  "new_price": 79,
  "currency": "EUR",
  "price_change_absolute": -20,
  "price_change_percent": -20.2,
  "importance_score": 90
}
```

`price_change_absolute` is the signed new-minus-old difference. Percentage is relative to the old amount and rounded to two decimal places; a zero old price produces a null percentage and a minimum score of 60. Tier comparisons use unrounded percentage magnitudes. Arithmetic uses Python Decimal, serialized as JSON numbers. The extra price fields are omitted when no price change is identified; all existing output fields remain intact. Changes to currency formatting alone do not count as a price change, though they can still be textual changes.

## Snapshot persistence and reliability

The **named** store `changewatch-snapshots-v1` persists across runs. Keys hash client ID and canonical URL. Renaming a competitor preserves history; changing client ID or URL initializes a new baseline. Clients are logically isolated within the store, not separate access-control boundaries. Do not reuse this store name for another implementation.

Records contain schema version, client ID, URL, text, hash and last-success timestamp. Fetch errors never overwrite them. Corrupt/incompatible records fail the run for inspection. To deliberately reset a baseline, delete its `snapshot_key` record; the next successful fetch initializes it.

Optional email delivery is attempted before the Dataset result is written, then the snapshot advances. Storage failures fail the run. These operations are not transactional: interruption between email acceptance and storage writes can repeat an email on a rerun. Consumers can deduplicate Dataset alerts by client ID, URL, previous hash and current hash. Run only one instance at a time for the same client/URL; overlapping runs can race because updates are not locked.

The MVP reads server-rendered HTML. It does not execute JavaScript, render CSS, solve CAPTCHAs, or recognize every HTTP-200 bot/error page. Dynamic timestamps, cookie banners and personalization can trigger changes. Choose stable public pages and review response quality before relying on alerts.

## Test first run versus second run

1. Monitor a page you control with a fixed client ID: `initialized`, `changed: false`, score 0.
2. Run without editing it: `unchanged`, equal hashes, score 0.
3. Edit visible pricing text from `$10` to `$20`: `changed`, a diff and higher score.
4. Run again without edits: `unchanged` against the new snapshot.
5. Return HTTP 404: `error`; restoring the page compares against the last successful baseline.

## Local development

Use Python 3.12:

```sh
python -m venv .venv
# Linux/macOS: source .venv/bin/activate
# PowerShell: .\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-dev.txt
python -m pytest -q
python -m ruff check src tests
python -m ruff format --check src tests
```

Put input at `storage/key_value_stores/default/INPUT.json`, then run `python -m src`. Retain `storage/` between runs; avoid purge options when testing persistence. Local filesystem storage needs no Apify token. Do not set `APIFY_IS_AT_HOME` locally. Tests use controlled HTTP responses, without credentials or competitor requests.

## Daily schedule

Save the input as an Actor task. In Apify Console **Schedules**, create a daily schedule, select that task and your built Actor version, and set the intended time/timezone (for example 08:00 Europe/Paris). Keep client ID and URLs stable. Ensure runs finish before the next start and avoid manual overlapping runs. Inspect run failures and Dataset errors after deployment.

References: [Apify scheduling](https://docs.apify.com/platform/schedules), [named storage](https://docs.apify.com/sdk/python/docs/concepts/storages), [input UI schema](https://docs.apify.com/actors/development/actor-definition/input-schema/specification/v1).

## Client alerts and optional email

Optional input fields (existing inputs still work):

| Field | Default | Behavior |
| --- | --- | --- |
| `alert_threshold` | `60` | Integer 0–100; a changed page must score at least this value. |
| `client_email` | omitted | One recipient address. Omit or use an empty string for preview-only operation. |
| `language` | `en` | `en` or `fr`; controls alert templates, not scraping or scoring. |

An alert is generated only when `changed == true` and `importance_score >= alert_threshold`. Equality qualifies. Initialization, unchanged pages, errors, and changes below the threshold remain in the Dataset but do not generate or send alerts, even if the threshold is zero.

Every Dataset item retains all previous fields and adds:

```json
{
  "alert_triggered": true,
  "alert_sent": false,
  "alert_subject": "[ChangeWatch] Test Shop - Price change",
  "alert_body": "Human-readable plain-text message...",
  "alert_html": "<!doctype html>...",
  "alert_threshold": 60,
  "email_error": null
}
```

For non-triggered items, subject/body/HTML are empty strings and both flags are false. Missing email still produces qualifying alert text and HTML, with `alert_sent: false` and `email_error: null`. Missing provider configuration produces a safe explanatory `email_error` and logs that delivery is disabled. Provider rejection or request failure is recorded without failing monitoring. `alert_sent: true` means Resend acknowledged the request with a message ID; it does not confirm inbox delivery, and later bounces are not tracked.

Deterministic templates cover price increases/decreases, discounts/promotions, products/services, shipping/delivery, availability/stock and generic important content. Price templates take priority; otherwise the first matching category in the preceding list is used. Other categories use the generic template. French wording and numeric decimal separators are localized; quoted page excerpts remain in their original language. For multiple prices, the message describes the primary price in the structured result.

### Configure Resend

1. Create a Resend account, add a sending domain and complete its DNS verification. Choose a sender address on that verified domain. Resend's testing sender is restricted; use your verified domain for client delivery.
2. Create an API key with sending permission for the intended domain.
3. In the Apify Actor's environment-variable configuration, securely set these values. Store the API key as a secret value, not in input, Git, Dockerfile or logs:

| Environment variable | Meaning |
| --- | --- |
| `EMAIL_PROVIDER_API_KEY` | Resend API key; required to send. |
| `EMAIL_FROM_ADDRESS` | Verified sender email address; required to send. |
| `EMAIL_FROM_NAME` | Optional sender display name; defaults to `ChangeWatch`. |

4. Rebuild the Actor from `main`, supply `client_email`, select `language` and the threshold, then run against a controlled page with a meaningful change.

Delivery uses a dedicated HTTPS client calling Resend's `POST /emails` endpoint with both `text = alert_body` and `html = alert_html`, redirects disabled, and a bounded timeout. Plain text remains available to email clients that do not display HTML. No additional SDK or AI API is needed. Provider response bodies, recipient addresses and credentials are not included in delivery logs or error strings. The recipient remains part of your Apify input, so restrict access appropriately.

See [Resend sending API](https://resend.com/docs/api-reference/emails/send-email) and [domain verification](https://resend.com/docs/dashboard/domains/introduction). `.env.example` lists the variables; the Actor does not automatically load a local `.env` file.

### Delivery failure and retry behavior

This MVP makes one delivery attempt per qualifying result and does not automatically retry or queue failed messages. A timeout can mean delivery is unconfirmed rather than definitely rejected. Failed delivery still writes the Dataset result and advances the successful page snapshot; an unchanged next run will not retry that email. Use `email_error` and the retained alert text for manual follow-up. Exactly-once delivery is not guaranteed across interruptions or overlapping runs.

### French example

Subject: `[ChangeWatch] Test Shop - Variation de prix`

```text
🚨 Changement concurrent important détecté

Concurrent : Test Shop
URL: https://example.com

Type de changement : Baisse de prix
Ancien prix : 99 €
Nouveau prix : 79 €
Variation : -20 € (-20,2 %)

Importance: 90/100

Résumé :
Test Shop a baissé son prix de 99 € à 79 €.

Action recommandée :
Vérifiez si cette variation est temporaire ou permanente et réévaluez votre positionnement tarifaire.

Détecté le :
2026-09-11T12:00:00+00:00
```

### English example

Subject: `[ChangeWatch] Test Shop - Price change`

```text
🚨 Important competitor change detected

Competitor: Test Shop
URL: https://example.com

Change type: Price increase
Previous price: 79 USD
New price: 99 USD
Difference: 20 USD
Change: 25.32%

Importance: 90/100

Summary:
Test Shop increased its price from 79 USD to 99 USD.

Recommended action:
Check whether this price change is temporary or permanent and review your pricing/positioning accordingly.

Detected at:
2026-09-11T12:00:00+00:00
```

Scores reflect the actual detected context and may be higher than these examples.

### HTML email preview

Open [the complete French HTML example](examples/alert-fr.html) in a browser to preview the actual generated template. It shows a sample score of 100; production scores remain determined by the existing scoring engine. Narrow the browser window to inspect the fluid single-column layout.

```text
ChangeWatch
Notification de veille concurrentielle
────────────────────────────────────────
Concurrent: Test Shop
Changement détecté: Baisse de prix

Ancien prix
99 €
Nouveau prix
79 €
Variation
-20 € (-20,2 %)
Importance: 100/100

Résumé
Test Shop a baissé son prix de 99 € à 79 €.

Action recommandée
Vérifiez si cette variation est temporaire
ou permanente et réévaluez votre
positionnement tarifaire.

Voir la page surveillée (lien)
────────────────────────────────────────
Heure de détection: 2026-09-11T12:00:00+00:00
Notification automatique générée par ChangeWatch
```

Subjects use `[ChangeWatch] <competitor> - <notification type>`: `Variation de prix` / `Price change`, `Changement détecté` / `Change detected` for commercial categories, and `Changement important` / `Important change` for generic updates. The plain-text body remains unchanged.

The white-background template uses neutral typography, inline styles, fluid presentation tables and a 600px maximum content width. It has a compact header, plain direction labels and an underlined monitored-page link. There are no hero sections, decorative cards, banners, tracking pixels, external assets, social icons or analytics. Dynamic text and URL attributes remain escaped. Sender configuration and delivery behavior are unchanged. Rendering varies between email clients; plain text is always sent alongside HTML.

### Test without sending email

Omit `client_email` (the safest preview mode even if credentials are configured). Set `language: "fr"` or `"en"`, initialize a controlled page, edit its price, and run again. Read `alert_subject` and `alert_body` in the Dataset. Existing identical snapshots do not retrigger an alert merely because you change the threshold or language.

Run `python -m pytest -q` for mocked provider success, rejection and failure tests alongside the real local SDK persistence test. Automated tests send no real email and require no provider credentials.

No payments, authentication, dashboard, external AI analysis or marketplace integration is included.
