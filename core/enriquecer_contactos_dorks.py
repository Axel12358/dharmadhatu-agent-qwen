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
    # Fast path: SearxNG local (Docker) — sin Tor, ~2s, respeta comillas
    # Si searxng está caído/suspendido → devolver [] de inmediato (no
    # quemar 45s en fallback Tor que apenas rinde para dorks de organizador).
    try:
        from core.serp_tor import _buscar_searxng_local
        res = _buscar_searxng_local(dork, timeout=timeout)
        return res if res else []
    except Exception:
        return []


def _na(v: str) -> bool:
    return (v or "").strip().lower() in ("", "n/a", "na", "none", "null", "-", "?")


def _fetch_contenido(url: str, timeout: int = 12) -> str:
    """Obtiene el texto de una página resultado (curl_cffi directo, sin Tor)."""
    try:
        from curl_cffi import requests as rr
        resp = rr.get(url, impersonate="chrome120", timeout=timeout, allow_redirects=True)
        if resp.status_code != 200:
            return ""
        # Página principal: emails/datos suelen estar en el HTML crudo
        return resp.text[:400_000]
    except Exception:
        return ""


def _extraer_emails_paginas(res: list, top=2, timeout=12) -> dict:
    """Fetchea las top N páginas resultado y extrae emails/redes de su HTML."""
    contactos = {"email": [], "telegram": [], "instagram": [], "soundcloud": []}
    vistos = set()
    for hit in res[:top]:
        url = hit.get("url", "") or hit.get("link", "")
        if not url or url in vistos:
            continue
        vistos.add(url)
        try:
            html = _fetch_contenido(url, timeout)
        except Exception:
            html = ""
        if not html or len(html) < 50:
            continue
        c = _extraer_contactos(html)
        for k in contactos:
            vals = c.get(k) or []
            for v in vals:
                if v and str(v) not in vistos:
                    try:
                        vistos.add(str(v))
                    except TypeError:
                        pass
                    contactos[k].append(v)
        if contactos["email"]:
            break
    return contactos


def _aplicar_actualizacion(link: str, clave_aux: tuple, email: str,
                           contactos_json: str, timeout=180) -> bool:
    """Actualiza SOLO email/contactos de la fila matcheada, bajo lock y con
    lectura fresca. NUNCA pisa el CSV con snapshots viejos."""
    try:
        from core.csv_lock import csv_locked_rows
    except Exception:
        from csv_lock import csv_locked_rows
    if not email and not contactos_json:
        return False
    with csv_locked_rows(CSV, timeout=timeout) as (frescas, _fn):
        for r in frescas:
            k = (r.get("link") or "").strip().lower()
            if link and k and k == link:
                pass
            elif link:
                continue
            else:
                if (r.get("nombre") or "").lower() != clave_aux[0]:
                    continue
                if (r.get("fecha") or "").lower() != clave_aux[1]:
                    continue
            changed = False
            if _na(r.get("email", "")) and email:
                r["email"] = email
                changed = True
            if _na(r.get("contactos", "")) and contactos_json:
                r["contactos"] = contactos_json
                changed = True
            return changed
    return False


def enriquecer_contactos_dorks(max_n: int = 10, timeout_dork: int = 20) -> dict:
    rows = list(csv.DictReader(open(CSV, encoding="utf-8")))
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
        if not contactos.get("email"):
            # emails casi nunca están en snippets: fetchea la página del result
            contactos_pag = _extraer_emails_paginas(res, top=2, timeout=timeout_dork)
            for k in ("email", "telegram", "instagram", "soundcloud"):
                if not contactos.get(k):
                    contactos[k] = contactos_pag.get(k, [])
        emails = contactos.get("email", [])
        hay_otro = any(contactos.get(k) for k in ("telegram", "instagram", "soundcloud"))
        cont_json = ""
        if (emails or hay_otro) and not _na(r.get("contactos", "")):
            cont_json = json.dumps(contactos, ensure_ascii=False)
        email_nuevo = emails[0] if emails else ""
        # guardado aditivo bajo lock: re-lee fresco y actualiza solo la fila
        link = (r.get("link") or "").strip().lower()
        clave_aux = ((r.get("nombre") or "").lower(), (r.get("fecha") or "").lower())
        if _aplicar_actualizacion(link, clave_aux, email_nuevo, cont_json):
            stats["con_email"] += 1 if (email_nuevo and _na(r.get("email", ""))) else 0
            stats["con_contacto"] += 1 if cont_json else 0
    return stats


if __name__ == "__main__":
    print("STATS contactos-dorks:", enriquecer_contactos_dorks())
