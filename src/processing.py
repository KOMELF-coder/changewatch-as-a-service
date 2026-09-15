"""Coordinate one URL without mixing reliability state into content snapshots."""

from copy import deepcopy
from datetime import datetime, timezone

import httpx
from apify import Actor

from .alerts import apply_alert, validate_alert_options
from .collection import fetch_collection, protect_coverage
from .health import notify_operator, update_health
from .monitor import compare, fetch_text, snapshot_key, text_hash
from .prices import tokens
from .reliability import digest, prepare_change, record_sent, state_hash


def validate_snapshot(previous, key):
    if previous is not None and (
        not isinstance(previous, dict)
        or previous.get("schema_version") not in {1, 2}
        or not isinstance(previous.get("text"), str)
        or previous.get("hash") != text_hash(previous["text"])
        or snapshot_key(previous.get("client_id"), previous.get("url")) != key
        or (previous.get("schema_version") == 2 and not isinstance(previous.get("entities"), list))
    ):
        raise ValueError("Invalid stored snapshot; inspect before resetting")


async def process_competitor(
    client_id: str,
    competitor: dict,
    store,
    client,
    push_data,
    alert_options=None,
    sender=None,
    operator_sender=None,
) -> bool:
    options = validate_alert_options(alert_options or {})
    key = snapshot_key(client_id, competitor["url"])
    state_key = key.replace("snapshot-", "monitor-")
    previous = None
    state = {"state_version": 1}
    now = datetime.now(timezone.utc).isoformat()
    result = {
        "client_id": client_id,
        "competitor": competitor["name"],
        "url": competitor["url"],
        "detected_at": now,
        "snapshot_key": key,
    }
    try:
        stored_state = await store.get_value(state_key)
        if isinstance(stored_state, dict) and stored_state.get("state_version") == 1:
            state = stored_state
        previous = await store.get_value(key)
        validate_snapshot(previous, key)
        signature = digest(
            [competitor.get("ignore_selectors", []), competitor.get("ignore_text_patterns", [])]
        )
        if previous and previous.get("ignore_signature", digest([[], []])) != signature:
            # Filter changes establish a fresh baseline rather than synthetic deletions.
            previous = None
            for field in ("baseline", "pending", "recent_states"):
                state.pop(field, None)
        if "baseline" not in state and previous:
            state["baseline"] = previous
            state["recent_states"] = [state_hash(previous)]
        baseline = state.get("baseline") or previous
        page = await fetch_collection(
            client,
            competitor["url"],
            fetch_text,
            ignore_selectors=competitor.get("ignore_selectors", []),
            ignore_text_patterns=competitor.get("ignore_text_patterns", []),
        )
        metadata = {k: v for k, v in page.items() if k.startswith("collection_")}
        # First aggregation is a scope migration, not hundreds of new products.
        if (
            page["collection_detected"]
            and baseline
            and (
                not baseline.get("collection_detected")
                or baseline.get("collection_expansion_status") != "complete"
                and page["collection_expansion_status"] == "complete"
            )
        ):
            baseline = None
            for field in ("baseline", "pending", "recent_states"):
                state.pop(field, None)
            result["collection_migrated"] = True
        page, preserve = protect_coverage(page, baseline)
        Actor.log.info(
            "Collection coverage: pages=%s entities=%s status=%s reason=%s",
            page["collection_pages_fetched"],
            page["collection_entities_found"],
            page["collection_expansion_status"],
            page["collection_expansion_reason"],
        )
        result.update(metadata)
        result.update({k: v for k, v in page.items() if k.startswith("collection_")})
        result.update(
            compare(
                baseline["text"] if baseline else None,
                page["text"],
                previous_entities=baseline.get("entities") if baseline else None,
                current_entities=page["entities"],
                require_entity_match=page["requires_entities"]
                or bool(
                    baseline
                    and (
                        baseline.get("requires_entities")
                        or sum(isinstance(t, tuple) for t in tokens(baseline["text"])) > 1
                    )
                ),
            )
        )
        snapshot = {
            "schema_version": 2,
            "client_id": client_id,
            "url": competitor["url"],
            "text": page["text"],
            "hash": text_hash(page["text"]),
            "updated_at": now,
            "entities": page["entities"],
            "requires_entities": page["requires_entities"],
            "ignore_signature": signature,
            **metadata,
        }
        accepted = deepcopy(state.get("baseline"))
        epoch = state.get("epoch", 0)
        prepare_change(result, state, snapshot, options)
        if preserve:
            state["baseline"] = accepted
            state["epoch"] = epoch
    except Exception as exc:
        expected = isinstance(exc, (httpx.HTTPError, TimeoutError))
        # ValueErrors from unusable pages are operational failures too.
        unexpected = not expected and not isinstance(exc, ValueError)
        error = (
            f"HTTP {exc.response.status_code}"
            if isinstance(exc, httpx.HTTPStatusError)
            else type(exc).__name__
        )
        result.update(
            status="error",
            changed=False,
            importance_score=0,
            previous_hash=previous.get("hash") if isinstance(previous, dict) else None,
            current_hash=None,
            similarity_ratio=None,
            matched_concepts=[],
            changes=[],
            entity_changes=[],
            diff_truncated=False,
            error=error,
            change_summary="Page could not be processed; previous snapshot preserved.",
            change_confidence=0,
            confirmation_required=options.get("confirmation_runs") or 2,
            confirmation_count=0,
            confirmation_status="pending" if state.get("pending") else "immediate",
            dynamic_content_detected=False,
            duplicate_alert_suppressed=False,
            alert_fingerprint=None,
            collection_detected=bool(previous and previous.get("collection_detected")),
            collection_pages_fetched=0,
            collection_entities_found=0,
            collection_expansion_status="error",
            collection_expansion_reason="Initial page unavailable; previous snapshot preserved",
        )
        result.update(update_health(state, now, error))
        await notify_operator(
            state, client_id, competitor["url"], now, unexpected=unexpected, sender=operator_sender
        )
        await apply_alert(result, options, sender)
        result["alert_decision_reason"] = "monitoring_error"
        await store.set_value(state_key, state)
        await push_data(result)
        Actor.log.warning(
            "Monitoring failure: key=%s status=%s error=%s", key, result["monitoring_status"], error
        )
        return False
    result.update(update_health(state, now))
    await notify_operator(state, client_id, competitor["url"], now, sender=operator_sender)
    await apply_alert(result, options, sender)
    record_sent(result, state)
    # Save accepted-email fingerprints before Dataset writes, reducing duplicate sends
    # when output storage fails. Cross-storage/email transactions remain impossible.
    if result["alert_sent"]:
        await store.set_value(state_key, state)
    try:
        await push_data(result)
        await store.set_value(state_key, state)
        if not preserve:
            await store.set_value(key, snapshot)
    except Exception:
        update_health(state, now, "Unexpected storage/output failure")
        await notify_operator(
            state, client_id, competitor["url"], now, unexpected=True, sender=operator_sender
        )
        try:
            await store.set_value(state_key, state)
        except Exception:
            Actor.log.error("Unable to persist monitoring health after storage failure: %s", key)
        raise
    Actor.log.info(
        "Processed %s: %s score=%s confidence=%s confirmation=%s decision=%s",
        key,
        result["status"],
        result["importance_score"],
        result["change_confidence"],
        result["confirmation_status"],
        result["alert_decision_reason"],
    )
    return True
