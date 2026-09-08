"""Fase 5-B — Enriquecer email/contactos vía dorks multi-motor (Tor, pool).

FB OpenGraph vía Tor está fuertemente throttled (se cuelga tras pocos fetches).
Alternativa fiable: buscar el organizador/nombre del evento en Bing/Startpage/
Mojeek/SearxNG (que SÍ responden por Tor) y extraer emails/telegram/instagram
de los snippets y de la página principal del resultado. Reusa el buscador
multi-motor de Fase 3, que ya es robusto.

Se ejecuta en lotes acotados (subprocess con kill por wall-clock) porque el
fetch de la página resultado puede colgar; el CSV se guarda cada iteración.
"""
from __future__ import annotations

import csv
import json
import re
import sys
import threading
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
CSV = PROJECT_ROOT / "eventos_encontrados.csv"

from core.enriquecer_fb_og import _extraer_contactos  # regex de contactos

_RE_EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")


def _buscar():
    from scrapers.facebook_dorks import _buscar_dork_multimotor_tor
    return _buscar_dork_multimotor_tor


def _buscar_segura(dork, timeout, tope=45):
    box = {}

    def _w():
        try:
            box["r"] = _buscar()(dork, timeout)
        except Exception:
            box["r"] = []
    t = threading.Thread(target=_w, daemon=True)
    t.start()
    t.join(tope)
    return box.get("r", [])


def _na(v: str) -> bool:
    return (v or "").strip().lower() in ("", "n/a", "na", "none", "null", "-", "?")


def enriquecer_contactos_dorks(max_n: int = 10, timeout_dork: int = 20) -> dict:
    rows = list(csv.DictReader(open(CSV, encoding="utf-8")))
    campos = list(rows[0].keys())
    objetivos = [
        r for r in rows
        if _na(r.get("email", "")) or _na(r.get("contactos", ""))
        and (r.get("organizador") or r.get("nombre") or "").strip()
    ]
    objetivos = [r for r in objetivos if (r.get("organizador") or r.get("nombre", "")).strip()][:max_n]

    stats = {"revisadas": len(objetivos), "con_email": 0, "con_contacto": 0}
    for r in objetivos:
        term = (r.get("organizador") or r.get("nombre", "")).strip()
        dork = f'"{term}" psytrance contact email'
        res = _buscar_segura(dork, timeout_dork)
        texto = " ".join(
            (hit.get("titulo", "") + " " + hit.get("snippet", "")) for hit in res
        )
        contactos = _extraer_contactos(texto)
        emails = contactos.get("email", [])
        if _na(r.get("email", "")) and emails:
            r["email"] = emails[0]
            stats["con_email"] += 1
        hay_otro = any(contactos.get(k) for k in ("telegram", "instagram", "soundcloud"))
        if _na(r.get("contactos", "")) and (emails or hay_otro):
            r["contactos"] = json.dumps(contactos, ensure_ascii=False)
            stats["con_contacto"] += 1
        # guardado incremental
        with open(CSV, "w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=campos)
            w.writeheader()
            w.writerows(rows)
    return stats


if __name__ == "__main__":
    print("STATS contactos-dorks:", enriquecer_contactos_dorks())
