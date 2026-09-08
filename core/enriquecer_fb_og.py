#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Enriquecimiento de eventos Facebook vía OpenGraph (sin login, vía Tor).

Técnica validada en GitHub (ChocoData-com/facebook-event-scraper, domini-67/
facebook-event-scraper): las páginas de evento públicas de Facebook renderizan
meta etiquetas og:title / og:description / og:image con la info incrustada en
el texto del description, p.ej.:
  "Event in Vienna by Debattierklub Wien and 2 others on Friday, April 17 2026
   with 191 people interested and 40 people going."

Este módulo:
  1. Lee eventos_encontrados.csv.
  2. Para cada fila con link de facebook.com/events/ y campos incompletos
     (fecha / lugar / organizador / email / contactos), fetch del link vía Tor
     (curl_cffi, igual que el resto del bot) y parsea las etiquetas OG.
  3. Rellena fecha, lugar, organizador y un dict de contactos (email, telegram,
     instagram, soundcloud) en una columna 'contactos'.
  4. Rota la identidad Tor cada N fetches para no ser bloqueado.
  5. Escribe el CSV periódicamente (cada lote) y al final, preservando TODAS
     las columnas existentes. Es incremental: las filas ya completas se excluyen.

No usa IP real: todo sale por socks5h://127.0.0.1:9050.
"""

from __future__ import annotations

import csv
import html as _html
import json
import re
import sys
import threading
import time
from pathlib import Path
from typing import Dict, List

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from core.http_client import get_html  # curl_cffi + Tor
from core.tor_manager import renovar_identidad_tor, tor_disponible

CSV_PATH = _PROJECT_ROOT / "eventos_encontrados.csv"

_RE_EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_RE_TG = re.compile(r"(?:https?://)?(?:t\.me/|telegram\.me/|@)([A-Za-z0-9_]{4,})")
_RE_IG = re.compile(r"(?:https?://)?instagram\.com/([A-Za-z0-9_.]{3,})")
_RE_SC = re.compile(r"(?:https?://)?soundcloud\.com/([A-Za-z0-9_\-]{3,})")

_META_RE = re.compile(
    r'<meta[^>]+property=["\']([^"\']+)["\'][^>]+content=["\'](.*?)["\']',
    re.S | re.I,
)
_META_RE2 = re.compile(
    r'<meta[^>]+content=["\'](.*?)["\'][^>]+property=["\']([^"\']+)["\']',
    re.S | re.I,
)


def _og_tags(html_text: str) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for m in _META_RE.finditer(html_text):
        out[m.group(1).lower()] = _html.unescape(m.group(2))
    for m in _META_RE2.finditer(html_text):
        out.setdefault(m.group(2).lower(), _html.unescape(m.group(1)))
    return out


def _parse_description(desc: str) -> Dict[str, str]:
    res: Dict[str, str] = {}
    m = re.search(r"Event in (.+?)(?:\s+by\s+|\s+on\s+|$)", desc, re.I)
    if m:
        res["lugar"] = m.group(1).strip()
    m = re.search(r"\s+by\s+(.+?)(?:\s+on\s+|$)", desc, re.I)
    if m:
        res["organizador"] = m.group(1).strip()
    m = re.search(r"\s+on\s+(.+?)(?:\s+with\s+|$)", desc, re.I)
    if m:
        res["fecha"] = m.group(1).strip()
    return res


def _extraer_contactos(texto: str) -> Dict[str, List[str]]:
    contactos: Dict[str, List[str]] = {
        "email": [], "telegram": [], "instagram": [], "soundcloud": [],
        "facebook": [], "grupo": [],
    }
    for e in _RE_EMAIL.findall(texto):
        if e.lower().endswith((".png", ".jpg", ".gif", ".webp")):
            continue
        if e not in contactos["email"]:
            contactos["email"].append(e)
    for m in _RE_TG.finditer(texto):
        h = m.group(1)
        if h and h not in contactos["telegram"]:
            contactos["telegram"].append(h)
    for m in _RE_IG.finditer(texto):
        h = m.group(1)
        if h and h.lower() not in ("p", "explore", "reel") and h not in contactos["instagram"]:
            contactos["instagram"].append(h)
    for m in _RE_SC.finditer(texto):
        h = m.group(1)
        if h and h not in contactos["soundcloud"]:
            contactos["soundcloud"].append(h)
    # Perfil/page del organizador en Facebook (lo que de verdad sirve para booking;
    # el email rara vez aparece). Excluye rutas reservadas.
    _RES = {"events", "groups", "pages", "profile.php", "gif", "photo",
            "watch", "notes", "messages", "help", "settings", "login",
            "people", "hashtag", "search", "marketplace", "gaming", "jobs",
            "fundraisers", "community", "privacy", "terms", "about", "reels",
            "explore", "ads", "business", "notifications"}
    for m in re.finditer(r"facebook\.com/([A-Za-z0-9._]{3,})", texto):
        slug = m.group(1)
        if slug.lower() in _RES:
            continue
        url = f"https://www.facebook.com/{slug}"
        if url not in contactos["facebook"]:
            contactos["facebook"].append(url)
    # Grupo donde se publicó el evento (muchos eventos viven en grupos)
    for m in re.finditer(r"facebook\.com/groups/(\d{6,})", texto):
        g = f"https://www.facebook.com/groups/{m.group(1)}"
        if g not in contactos["grupo"]:
            contactos["grupo"].append(g)
    return contactos


def _run_thread(fn, timeout, default=None):
    """Ejecuta fn en hilo daemon con tope de wall-clock.

    Clave: los hilos son daemon, así que si fn se pasa del tope (p.ej.
    curl_cffi ignorando el timeout de conexión en Tor throttled), el hilo se
    abandona al terminar el join y NO bloquea el proceso (el error anterior
    usaba ThreadPoolExecutor cuyo __exit__ hace shutdown(wait=True) y colgaba).
    """
    box = {}

    def _w():
        try:
            box["r"] = fn()
        except Exception:
            box["r"] = default
    t = threading.Thread(target=_w, daemon=True)
    t.start()
    t.join(timeout)
    return box.get("r", default)


def _rotar_safe():
    """Rotación de Tor con tope: si el reinicio se traba, no bloquea el run."""
    _run_thread(renovar_identidad_tor, 10)


def _get_html_safe(url: str, timeout: int):
    """get_html con tope de wall-clock real (hilo daemon)."""
    return _run_thread(lambda: get_html(url, timeout), timeout + 6)


def _guardar(rows, campos):
    with open(CSV_PATH, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=campos, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)


def enriquecer(
    max_fetches: int = 200,
    rotar_cada: int = 8,
    timeout_por_url: int = 12,
    tiempo_max_seg: int = 240,
) -> Dict[str, int]:
    """Enriquece las filas FB del CSV con datos de OpenGraph. Devuelve stats."""
    if not CSV_PATH.exists():
        return {"error": 1}
    rows = list(csv.DictReader(open(CSV_PATH, encoding="utf-8")))
    if not rows:
        return {"vacio": 1}
    if "contactos" not in rows[0].keys():
        for r in rows:
            r["contactos"] = ""
    campos = list(rows[0].keys())

    vacios = {"", "N/A", "None", "nan"}
    objetivos = [
        r for r in rows
        if (r.get("link") or "").startswith("http") and "facebook.com/events/" in r["link"]
        and (
            (r.get("fecha") or "").strip() in vacios
            or (r.get("lugar") or "").strip() in vacios
            or (r.get("organizador") or "").strip() in vacios
            or (r.get("email") or "").strip() in vacios
            or (r.get("contactos") or "").strip() in vacios
        )
    ]
    objetivos = objetivos[:max_fetches]

    stats = {"revisadas": len(objetivos), "actualizadas": 0, "con_contacto": 0, "fallos": 0}
    if not tor_disponible():
        return {"tor_offline": 1}

    inicio = time.time()
    for i, r in enumerate(objetivos):
        try:
            html_text = _get_html_safe(r["link"], timeout_por_url)
            if not html_text:
                stats["fallos"] += 1
            else:
                og = _og_tags(html_text)
                cambios = False
                if (r.get("nombre") or "").strip() in vacios and og.get("og:title"):
                    r["nombre"] = og["og:title"]
                    cambios = True
                parsed = _parse_description(og.get("og:description", ""))
                if (r.get("fecha") or "").strip() in vacios and parsed.get("fecha"):
                    r["fecha"] = parsed["fecha"]; cambios = True
                if (r.get("lugar") or "").strip() in vacios and parsed.get("lugar"):
                    r["lugar"] = parsed["lugar"]; cambios = True
                if (r.get("organizador") or "").strip() in vacios and parsed.get("organizador"):
                    r["organizador"] = parsed["organizador"]; cambios = True
                contactos = _extraer_contactos(html_text)
                emails = contactos.get("email", [])
                if (r.get("email") or "").strip() in vacios and emails:
                    r["email"] = emails[0]; cambios = True
                if emails or any(contactos.get(k) for k in ("telegram", "instagram", "soundcloud")):
                    r["contactos"] = json.dumps(contactos, ensure_ascii=False)
                    cambios = True
                    stats["con_contacto"] += 1
                if cambios:
                    stats["actualizadas"] += 1
        except Exception:
            stats["fallos"] += 1

        # escritura cada iteración (no perder avances si se traba)
        _guardar(rows, campos)
        if (i + 1) % rotar_cada == 0:
            print(f"  … {i+1}/{len(objetivos)} | +{stats['actualizadas']} | "
                  f"contactos:{stats['con_contacto']} | fallos:{stats['fallos']}", flush=True)
            try:
                _rotar_safe()
            except Exception:
                pass
        time.sleep(0.3)
        if time.time() - inicio > tiempo_max_seg:
            print(f"  ⏱ tope de tiempo ({tiempo_max_seg}s) alcanzado en {i+1}/{len(objetivos)}",
                  flush=True)
            break

    _guardar(rows, campos)
    return stats


if __name__ == "__main__":
    print("Stats enriquecimiento FB OG:", enriquecer())
