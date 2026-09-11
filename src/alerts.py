"""Deterministic client-facing alert templates and threshold decisions."""

import logging

from .email_delivery import send_email, valid_email
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


def render_alert(result: dict, language: str, *, html: bool = False) -> tuple[str, str]:
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
        subject_type = "Variation de prix" if french else "Price change"
    else:
        concept = next(
            (key for key in TEMPLATES if key in result.get("matched_concepts", [])), "generic"
        )
        en_title, fr_title, en_summary, fr_summary, en_action, fr_action = TEMPLATES[concept]
        title = fr_title if french else en_title
        summary = f"{name} {fr_summary if french else en_summary}."
        action = fr_action if french else en_action
        subject_type = (
            ("Changement important" if french else "Important change")
            if concept == "generic"
            else ("Changement détecté" if french else "Change detected")
        )
        # Quote page text rather than imply a machine translation of it.
        for change in result.get("changes", [])[:3]:
            details.extend(
                [
                    f"{'Avant' if french else 'Before'}: {change['removed'] or '—'}",
                    f"{'Après' if french else 'After'}: {change['added'] or '—'}",
                ]
            )
    subject = f"[ChangeWatch] {name} - {subject_type}"
    if html:
        return subject, render_html(result, language, title, details, summary, action)
    lines = [
        "🚨 Changement concurrent important détecté"
        if french
        else "🚨 Important competitor change detected",
        "",
        f"{'Concurrent :' if french else 'Competitor:'} {name}",
        f"URL: {result['url']}",
        "",
        f"{'Type de changement :' if french else 'Change type:'} {title}",
        *details,
        "",
        f"Importance: {result['importance_score']}/100",
        "",
        "Résumé :" if french else "Summary:",
        summary,
        "",
        "Action recommandée :" if french else "Recommended action:",
        action,
        "",
        "Détecté le :" if french else "Detected at:",
        result["detected_at"],
    ]
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
