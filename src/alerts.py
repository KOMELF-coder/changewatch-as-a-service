"""Deterministic client-facing alert templates and threshold decisions."""

import logging

from .email_delivery import send_email, valid_email
from .email_presentation import display_time, event_count
from .html_email import render_html

log = logging.getLogger(__name__)


def validate_alert_options(data: dict) -> dict:
    threshold = data.get("alert_threshold", 60)
    language = data.get("language", "en")
    email = data.get("client_email", "")
    if type(threshold) is not int or not 0 <= threshold <= 100:
        raise ValueError("alert_threshold must be an integer from 0 to 100")
    if language not in ("en", "fr"):
        raise ValueError("language must be en or fr")
    if not isinstance(email, str) or (email and not valid_email(email)):
        raise ValueError("client_email must be a single email address or omitted")
    return {"alert_threshold": threshold, "language": language, "client_email": email}


# type: (English title, French title, English summary, French summary, EN action, FR action)
TEMPLATES = {
    "promotion": (
        "Discount/promotion change",
        "Modification d’une promotion",
        "updated its discounts or promotions",
        "a modifié ses remises ou promotions",
        "Review the offer terms and duration before adjusting your promotions.",
        "Vérifiez les conditions et la durée de l’offre avant d’adapter vos promotions.",
    ),
    "new offering": (
        "Product/service change",
        "Modification d’un produit ou service",
        "updated its products or services",
        "a modifié ses produits ou services",
        "Review the offering and assess how it compares with yours.",
        "Examinez l’offre et comparez-la à vos produits ou services.",
    ),
    "shipping": (
        "Shipping/delivery change",
        "Modification de la livraison",
        "updated its shipping or delivery information",
        "a modifié ses informations de livraison",
        "Check delivery charges and timelines and review your delivery proposition.",
        "Vérifiez les frais et délais de livraison et réévaluez votre offre de livraison.",
    ),
    "availability": (
        "Availability/stock change",
        "Modification de la disponibilité",
        "updated its availability or stock information",
        "a modifié ses informations de disponibilité ou de stock",
        "Verify current availability and consider the impact on customer demand.",
        "Vérifiez la disponibilité actuelle et évaluez les conséquences sur la demande.",
    ),
    "generic": (
        "Important content change",
        "Modification importante du contenu",
        "updated important content on its page",
        "a modifié un contenu important sur sa page",
        "Review the changed page and assess whether a business response is needed.",
        "Consultez la page modifiée et évaluez si une action commerciale est nécessaire.",
    ),
}


def number(value, french: bool) -> str:
    text = f"{value:g}"
    return text.replace(".", ",") if french else text


def _event_content(result: dict, language: str) -> dict:
    french = language == "fr"
    name = " ".join(result["competitor"].split())
    details = []
    if result.get("change_type") == "price_change":
        decreasing = result["new_price"] < result["old_price"]
        currency = (
            {"EUR": "€", "USD": "$", "GBP": "£"}.get(result["currency"], result["currency"])
            if french
            else result["currency"]
        )
        old = f"{number(result['old_price'], french)} {currency}"
        new = f"{number(result['new_price'], french)} {currency}"
        difference = f"{number(result['price_change_absolute'], french)} {currency}"
        percent = result.get("price_change_percent")
        percent_text = (
            (number(percent, french) + (" %" if french else "%"))
            if percent is not None
            else (
                "non calculable (ancien prix nul)"
                if french
                else "not calculable (previous price was zero)"
            )
        )
        if french:
            title = "Baisse de prix" if decreasing else "Hausse de prix"
            summary = (
                f"{name} a {'baissé' if decreasing else 'augmenté'} son prix de {old} à {new}."
            )
            details = [
                f"Ancien prix : {old}",
                f"Nouveau prix : {new}",
                f"Variation : {difference} ({percent_text})",
            ]
            action = "Vérifiez si cette variation est temporaire ou permanente et réévaluez votre positionnement tarifaire."
        else:
            title = "Price decrease" if decreasing else "Price increase"
            summary = f"{name} {'decreased' if decreasing else 'increased'} its price from {old} to {new}."
            details = [
                f"Previous price: {old}",
                f"New price: {new}",
                f"Difference: {difference}",
                f"Change: {percent_text}",
            ]
            action = "Check whether this price change is temporary or permanent and review your pricing/positioning accordingly."
    elif result.get("change_type") in {"new_product", "product_removed", "unavailable"}:
        kind = result["change_type"]
        labels = {
            "new_product": ("New product detected", "Nouveau produit détecté"),
            "product_removed": ("Product removed from page", "Produit retiré de la page"),
            "unavailable": ("Product unavailable", "Produit indisponible"),
        }
        title = labels[kind][int(french)]
        entity = (
            result.get("entity_name")
            or result.get("entity_id")
            or result.get("entity_url")
            or ("Produit" if french else "Product")
        )
        summaries = {
            "new_product": ("was added to the page", "a été ajouté à la page"),
            "product_removed": ("was removed from the page", "a été retiré de la page"),
            "unavailable": ("is now unavailable", "est désormais indisponible"),
        }
        summary = f"{entity} {summaries[kind][int(french)]}."
        actions = {
            "new_product": (
                "Review the new product and compare it with your offering.",
                "Examinez le nouveau produit et comparez-le à votre offre.",
            ),
            "product_removed": (
                "Check whether the product removal reflects a stock shortage or a lasting withdrawal.",
                "Vérifiez si le retrait du produit correspond à une rupture de stock ou à une suppression durable.",
            ),
            "unavailable": (
                "Check the stock status and expected return to availability.",
                "Vérifiez le stock et la date prévue de retour à la disponibilité.",
            ),
        }
        action = actions[kind][int(french)]
    else:
        concept = next(
            (key for key in TEMPLATES if key in result.get("matched_concepts", [])), "generic"
        )
        en_title, fr_title, en_summary, fr_summary, en_action, fr_action = TEMPLATES[concept]
        title = fr_title if french else en_title
        summary = f"{name} {fr_summary if french else en_summary}."
        action = fr_action if french else en_action
        # Quote page text rather than imply a machine translation of it.
        for change in result.get("changes", [])[:3]:
            details.extend(
                [
                    f"{'Avant' if french else 'Before'}: {change['removed'] or '—'}",
                    f"{'Après' if french else 'After'}: {change['added'] or '—'}",
                ]
            )
    if result.get("entity_name") or result.get("entity_id"):
        entity = result.get("entity_name") or result["entity_id"]
        details.insert(0, f"{'Produit' if french else 'Product'}: {entity}")
        if result.get("change_type") == "price_change":
            summary = f"{entity} : {old} → {new}."
    if result.get("entity_url"):
        details.append(f"{'Page produit' if french else 'Product page'}: {result['entity_url']}")
    return {"title": title, "details": details, "summary": summary, "action": action}


def render_alert(result: dict, language: str, *, html: bool = False) -> tuple[str, str]:
    french = language == "fr"
    name = " ".join(result["competitor"].split())
    events = result.get("entity_changes") or [result]
    cards = []
    concept_map = {
        "promotion": "promotion",
        "shipping": "shipping",
        "availability": "availability",
        "generic_content_change": "generic",
    }
    for event in events:
        # Entity events are authoritative: never inherit another event's identity.
        item = {**result, **event}
        if event is not result:
            for key in ("entity_name", "entity_url", "entity_id"):
                item[key] = event.get(key)
            item["changes"] = event.get("changes", [])
            if "before" in event or "after" in event:
                item["changes"] = [
                    {"removed": event.get("before", ""), "added": event.get("after", "")}
                ]
            item["matched_concepts"] = [concept_map.get(event.get("change_type"), "generic")]
        content = _event_content(item, language)
        entity = item.get("entity_name") or item.get("entity_id") or ""
        kind = item.get("change_type")
        if kind == "new_product":
            content["title"] = "Nouveau produit" if french else "New product"
            if item.get("price") is not None:
                content["details"].append(
                    f"{'Prix' if french else 'Price'}: {item['price']} {item.get('currency') or ''}"
                )
        elif kind == "product_removed":
            content["title"] = "Produit retiré" if french else "Product removed"
        elif kind in {"availability", "unavailable"}:
            content["title"] = "Disponibilité" if french else "Availability"
        content["entity"] = entity
        content["value"] = ""
        content["delta"] = ""
        if kind == "price_change":
            currency = {"EUR": "€", "USD": "$", "GBP": "£"}.get(item["currency"], item["currency"])
            content["value"] = (
                f"{number(item['old_price'], french)} {currency} → {number(item['new_price'], french)} {currency}"
            )
            percent = item.get("price_change_percent")
            percent_text = (
                number(percent, french) + (" %" if french else "%")
                if percent is not None
                else ("pourcentage non calculable" if french else "percentage unavailable")
            )
            content["delta"] = (
                f"{number(item['price_change_absolute'], french)} {currency} ({percent_text})"
            )
            if entity:
                verb = (
                    ("a baissé" if item["new_price"] < item["old_price"] else "a augmenté")
                    if french
                    else ("decreased" if item["new_price"] < item["old_price"] else "increased")
                )
                content["summary"] = (
                    f"{entity} {verb} {'de' if french else 'from'} {number(item['old_price'], french)} {currency} {'à' if french else 'to'} {number(item['new_price'], french)} {currency}."
                )
        elif entity and kind not in {"new_product", "product_removed", "unavailable"}:
            content["summary"] = f"{entity} : {content['summary']}"
        cards.append(content)
    count = event_count(len(cards), french)
    subject = f"[ChangeWatch] {name} - {count if len(cards) > 1 else cards[0]['title']}"
    summary = " ".join(card["summary"] for card in cards)
    action = " ".join(dict.fromkeys(card["action"] for card in cards))
    if html:
        return subject, render_html(
            result, language, cards[0]["title"], [], summary, action, cards=cards
        )
    lines = [
        "ChangeWatch",
        "Veille concurrentielle automatisée" if french else "Automated competitor monitoring",
        "",
        name,
        count,
        "",
    ]
    for card in cards:
        lines.extend([f"[{card['title']}]", *card["details"], ""])
    lines.extend(
        [
            f"{'Importance globale' if french else 'Overall importance'}: {result['importance_score']}/100",
            "",
            "Résumé :" if french else "Summary:",
            summary,
            "",
            "Action recommandée :" if french else "Recommended action:",
            action,
            "",
            "Voir la page surveillée" if french else "View monitored page",
            result["url"],
            "",
            display_time(result["detected_at"], french),
            "Notification automatique générée par ChangeWatch"
            if french
            else "Automated notification generated by ChangeWatch",
        ]
    )
    return subject, "\n".join(lines)


async def apply_alert(result: dict, options: dict | None = None, sender=None) -> None:
    options = validate_alert_options(options or {})
    threshold = options["alert_threshold"]
    triggered = result["changed"] is True and result["importance_score"] >= threshold
    result.update(
        alert_triggered=triggered,
        alert_sent=False,
        alert_subject="",
        alert_body="",
        alert_html="",
        alert_threshold=threshold,
        email_error=None,
    )
    log.info(
        "Alert decision: changed=%s score=%s threshold=%s generated=%s",
        result["changed"],
        result["importance_score"],
        threshold,
        triggered,
    )
    if not triggered:
        log.info("Email not sent: alert threshold conditions not met.")
        return
    result["alert_subject"], result["alert_body"] = render_alert(result, options["language"])
    _, result["alert_html"] = render_alert(result, options["language"], html=True)
    if not options["client_email"]:
        log.info("Email delivery disabled: client_email is missing; alert text retained.")
        return
    try:
        sent, error = await (sender or send_email)(
            options["client_email"],
            result["alert_subject"],
            result["alert_body"],
            result["alert_html"],
        )
    except Exception:
        # Isolate delivery failures, including provider configuration errors.
        sent, error = False, "Email delivery failed unexpectedly; delivery is unconfirmed."
    result.update(alert_sent=sent, email_error=error)
    if error:
        log.warning("Email not sent: %s", error)
    else:
        log.info("Email sent: accepted by Resend.")
