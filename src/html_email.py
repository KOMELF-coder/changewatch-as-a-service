"""Inline-only, fluid transactional email layout with escaped dynamic content."""

from html import escape
from urllib.parse import urlsplit


def render_html(
    result: dict, language: str, title: str, details: list[str], summary: str, action: str
) -> str:
    french = language == "fr"
    name = escape(" ".join(result["competitor"].split()))
    url = result["url"]
    # Escaping attributes is necessary but does not make unsafe schemes safe.
    parsed = urlsplit(url)
    safe_url = parsed.scheme in {"http", "https"} and bool(parsed.hostname)
    subtitle = "Alerte de veille concurrentielle" if french else "Competitor intelligence alert"
    cta = "Voir la page concurrente" if french else "View competitor page"
    footer = (
        "Surveillance automatisée par ChangeWatch"
        if french
        else "Automated monitoring by ChangeWatch"
    )
    rows = ""
    for detail in details:
        label, _, value = detail.partition(":")
        emphasis = label.strip() in {"New price", "Nouveau prix"}
        rows += (
            '<tr><td style="padding:10px 0;border-bottom:1px solid #e5e7eb;">'
            f'<div style="font-size:12px;color:#64748b;">{escape(label.strip())}</div>'
            f'<div style="margin-top:3px;font-size:{24 if emphasis else 16}px;'
            f'font-weight:{700 if emphasis else 400};color:#0f172a;overflow-wrap:anywhere;">'
            f"{escape(value.strip())}</div></td></tr>"
        )
    button = (
        f'<a href="{escape(url, quote=True)}" style="display:inline-block;padding:14px 20px;'
        "background-color:#0f172a;color:#ffffff;border-radius:6px;font-weight:700;"
        f'text-decoration:none;text-align:center;">{cta}</a>'
        if safe_url
        else ""
    )
    return f'''<!doctype html>
<html lang="{"fr" if french else "en"}">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>ChangeWatch</title></head>
<body style="margin:0;padding:0;background-color:#ffffff;color:#0f172a;font-family:Arial,Helvetica,sans-serif;font-size:16px;line-height:1.6;">
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="width:100%;background-color:#ffffff;"><tr><td align="center" style="padding:24px 12px;">
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="width:100%;max-width:600px;text-align:left;">
<tr><td style="padding:0 12px 24px;border-bottom:1px solid #e5e7eb;">
<div style="font-size:24px;font-weight:700;letter-spacing:-0.5px;">ChangeWatch</div>
<div style="font-size:14px;color:#64748b;">{subtitle}</div></td></tr>
<tr><td style="padding:24px 12px;">
<span style="display:inline-block;padding:5px 10px;background-color:#eef2f6;color:#334155;border-radius:4px;font-size:13px;font-weight:700;">{escape(title)}</span>
<h1 style="margin:16px 0 4px;font-size:26px;line-height:1.3;overflow-wrap:anywhere;">{name}</h1>
<div style="font-size:13px;color:#64748b;">{"Concurrent surveillé" if french else "Monitored competitor"}</div>
</td></tr>
<tr><td style="padding:0 12px;">
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="width:100%;border:1px solid #e5e7eb;border-radius:8px;background-color:#f8fafc;"><tr><td style="padding:8px 20px 20px;">
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="width:100%;">{rows}</table>
<div style="margin-top:16px;font-size:13px;color:#64748b;">Importance</div>
<div style="font-size:24px;font-weight:700;">{escape(str(result["importance_score"]))}<span style="font-size:16px;font-weight:400;color:#64748b;">/100</span></div>
</td></tr></table></td></tr>
<tr><td style="padding:24px 12px 0;">
<h2 style="margin:0 0 8px;font-size:16px;">{"Résumé" if french else "Summary"}</h2>
<p style="margin:0;overflow-wrap:anywhere;">{escape(summary)}</p></td></tr>
<tr><td style="padding:24px 12px;">
<h2 style="margin:0 0 8px;font-size:16px;">{"Action recommandée" if french else "Recommended action"}</h2>
<p style="margin:0;overflow-wrap:anywhere;">{escape(action)}</p></td></tr>
<tr><td style="padding:0 12px 24px;">{button}
<p style="margin:12px 0 0;font-size:12px;color:#64748b;overflow-wrap:anywhere;word-break:break-all;">{escape(url)}</p></td></tr>
<tr><td style="padding:20px 12px;border-top:1px solid #e5e7eb;font-size:12px;color:#64748b;">
<div>{"Détecté le" if french else "Detected at"}: {escape(str(result["detected_at"]))}</div>
<div style="margin-top:8px;">{footer}</div>
</td></tr></table></td></tr></table>
</body></html>'''
