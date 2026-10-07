"""Einfache DNS-Prüfung der Absenderdomain (SPF, DMARC) für die Zustellbarkeit."""
from __future__ import annotations

import dns.resolver


def _txt(name: str) -> list[str]:
    try:
        answers = dns.resolver.resolve(name, "TXT", lifetime=5)
    except Exception:  # noqa: BLE001 - NXDOMAIN, Timeout, ...
        return []
    return ["".join(part.decode() for part in r.strings) for r in answers]


def check_domain(domain: str) -> list[dict]:
    """Liefert Prüfpunkte: [{name, ok, detail}]."""
    out = []
    spf = [t for t in _txt(domain) if t.lower().startswith("v=spf1")]
    if len(spf) == 1:
        out.append({"name": "SPF", "ok": True, "detail": spf[0]})
    elif not spf:
        out.append({"name": "SPF", "ok": False, "detail": "Kein SPF-Eintrag (TXT „v=spf1 …“) gefunden."})
    else:
        out.append({"name": "SPF", "ok": False, "detail": "Mehrere SPF-Einträge – es darf nur einen geben."})
    dmarc = [t for t in _txt(f"_dmarc.{domain}") if t.lower().startswith("v=dmarc1")]
    if dmarc:
        out.append({"name": "DMARC", "ok": True, "detail": dmarc[0]})
    else:
        out.append({"name": "DMARC", "ok": False, "detail": "Kein DMARC-Eintrag unter _dmarc gefunden."})
    return out
