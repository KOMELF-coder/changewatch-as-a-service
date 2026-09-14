"""Persisted confirmation, bounded state history, deduplication and alert policy."""

import hashlib
import json
from datetime import datetime


def digest(value) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()


def state_hash(snapshot: dict) -> str:
    entities = sorted(snapshot.get("entities", []), key=lambda e: json.dumps(e, sort_keys=True))
    return digest([snapshot["text"], entities])


def fingerprint(result: dict) -> str:
    events = result.get("entity_changes") or result.get("price_changes")
    if events:
        fields = (
            "entity_id",
            "entity_url",
            "entity_name",
            "change_type",
            "old_price",
            "new_price",
            "currency",
        )
        meaningful = sorted(
            [{k: e.get(k) for k in fields} for e in events],
            key=lambda e: json.dumps(e, sort_keys=True),
        )
    else:
        meaningful = [
            result.get("change_type"),
            result.get("previous_hash"),
            result.get("current_hash"),
        ]
    return digest([result["client_id"], result["url"], meaningful])


def confidence(result: dict) -> int:
    if not result["changed"]:
        return 0
    events = result.get("entity_changes", [])
    if events:
        return min(event.get("change_confidence", 60) for event in events)
    if result.get("change_type") == "price_change":
        return 80  # Conservative single-product text fallback, not stable DOM identity.
    return 35 if result.get("ambiguous_numeric_change") else 60


def prepare_change(result: dict, state: dict, snapshot: dict, options: dict) -> None:
    current = state_hash(snapshot)
    history = state.get("recent_states", [])[-7:]
    seen = current in history
    history.append(current)
    state["recent_states"] = history
    transitions = [value for i, value in enumerate(history) if i == 0 or value != history[i - 1]]
    high = bool(result.get("entity_changes")) and all(
        e.get("change_type") in {"price_change", "new_product", "product_removed"}
        and e.get("change_confidence", 0) >= 95
        for e in result["entity_changes"]
    )
    dynamic = (
        not high
        and len(transitions) >= 4
        and transitions[-4] == transitions[-2]
        and transitions[-3] == transitions[-1]
    )
    required = options.get("confirmation_runs") or (1 if high else 2)
    pending = state.get("pending")
    result.update(
        change_confidence=confidence(result),
        confirmation_required=required,
        confirmation_count=0,
        confirmation_status="immediate",
        dynamic_content_detected=dynamic,
        state_seen_before=seen,
        alert_suppressed_reason="repeated_dynamic_state" if dynamic else None,
        duplicate_alert_suppressed=False,
        alert_fingerprint=fingerprint(result),
    )
    if dynamic:
        result["content_stability"] = "flapping_content"
    if not result["changed"]:
        result["confirmation_status"] = "discarded" if pending else "immediate"
        state["pending"] = None
        state["baseline"] = snapshot
        return
    count = pending["count"] + 1 if pending and pending["target"] == current else 1
    result["confirmation_count"] = count
    result["confirmation_status"] = (
        "immediate" if required == 1 else ("confirmed" if count >= required else "pending")
    )
    if pending and pending["target"] != current:
        result["pending_change_status"] = "superseded"
    state["pending"] = {"target": current, "count": count}
    sent = state.get("sent_fingerprints", {}).get(result["alert_fingerprint"])
    now = datetime.fromisoformat(result["detected_at"]).timestamp()
    if (
        sent
        and sent["epoch"] == state.get("epoch", 0)
        and now - sent["timestamp"] < options.get("alert_cooldown_hours", 24) * 3600
    ):
        result["duplicate_alert_suppressed"] = True
    if count >= required:
        if state.get("baseline") and state_hash(state["baseline"]) != current:
            state["epoch"] = state.get("epoch", 0) + 1
        state["baseline"] = snapshot
        state["pending"] = None


def record_sent(result: dict, state: dict) -> None:
    if not result.get("alert_sent"):
        return
    sent = state.setdefault("sent_fingerprints", {})
    sent[result["alert_fingerprint"]] = {
        "timestamp": datetime.fromisoformat(result["detected_at"]).timestamp(),
        "epoch": state.get("epoch", 0),
    }
    # Bound per-URL storage; cooldown timestamps are retained for recent alerts.
    state["sent_fingerprints"] = dict(
        sorted(sent.items(), key=lambda item: item[1]["timestamp"])[-100:]
    )


def alert_decision(result: dict, options: dict) -> str:
    if not result["changed"]:
        return "unchanged"
    if result["importance_score"] < options["alert_threshold"]:
        return "below_threshold"
    minimum = 80 if result.get("change_type") == "price_change" else 50
    if result.get("change_confidence", confidence(result)) < minimum:
        return "low_confidence"
    if result.get("dynamic_content_detected"):
        return "dynamic_content"
    if result.get("duplicate_alert_suppressed"):
        return "duplicate"
    if result.get("confirmation_status") in {"pending", "discarded", "superseded"}:
        return "awaiting_confirmation"
    if not options["client_email"]:
        return "no_client_email"
    return "eligible"
