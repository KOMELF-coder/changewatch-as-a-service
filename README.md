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

Base score = `min(100, 10 + round(20 * (1 - similarity)) + concept weights + monetary bonus)` for changed pages only. Each group counts once, using changed text plus six neighboring words on each side. Keywords support English and French:

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

Currency amounts or percentages in that context add 20. This is a prioritization heuristic, not a probability. Every normalized text change is reported; consumers can filter by `changed` and a chosen score threshold. Similarity uses word-level Python `SequenceMatcher` with its frequent-token heuristic enabled for large repetitive pages.

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

The Dataset result is written before the snapshot advances. Storage failures fail the run. These writes are not transactional: interruption between writes can repeat an alert on retry. Consumers can deduplicate by client ID, URL, previous hash and current hash. Run only one instance at a time for the same client/URL; overlapping runs can race because updates are not locked.

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

No payments, authentication, dashboard, email, external AI analysis or marketplace integration is included.
