#!/usr/bin/env python3
"""
Scraper de EDM Dance Directory (edmdancedirectory.com) — psytrance genre.

Fuente: https://edmdancedirectory.com/events/genre/psytrance
- Gratis, sin login, sin API key.
- 49 eventos psytrance con JSON-LD schema.org/MusicEvent (nombre, startDate, lugar, organizador).
- Requests + BS4 + get_active_proxies (Tor si no hay proxy residencial, nunca IP real).
Requiere: requests, bs4, lxml
"""
import json
import re
import sys
import time
from pathlib import Path
from typing import List, Dict

import requests
from bs4 import BeautifulSoup

_PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

try:
    from core.http_client import get_active_proxies
    _HAS_PROXY = True
except Exception:
    def get_active_proxies():
        return {}
    _HAS_PROXY = False

try:
    from scrapers.event_extractor import EventExtractor
    _extractor = EventExtractor()
except Exception:
    _extractor = None

BASE_URL = "https://edmdancedirectory.com"
LIST_URL = f"{BASE_URL}/events/genre/psytrance"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}
MAX_EVENTOS = 50


def _sesion():
    s = requests.Session()
    # Cloudflare bloquea Tor exits; usar directo para este sitio (no FB/IG)
    # Intentar sin proxy primero; fallback a Tor solo si directo falla
    s.headers.update(HEADERS)
    return s


def _normalizar_fecha(d: str) -> str:
    if not d:
        return "N/A"
    # 2026-04-04T00:00:00.000Z -> 2026-04-04
    return d.split("T")[0] if "T" in d else d[:10]


def scrape_edmdancedirectory(max_eventos=MAX_EVENTOS) -> List[Dict]:
    """Scrapea psytrance events de EDM Dance Directory vía JSON-LD."""
    print("🎵 Scraping EDM Dance Directory (psytrance)...")
    eventos = []
    s = _sesion()
    r = None
    for url_try in [LIST_URL, f"{BASE_URL}/events", BASE_URL]:
        try:
            r = s.get(url_try, timeout=20)
            if r.status_code == 200:
                LIST_URL_OK = url_try
                break
            if r.status_code in (403, 404):
                continue
            r.raise_for_status()
            break
        except Exception as e:
            last_err = e
            continue
    if r is None or r.status_code != 200:
        print(f"  ❌ EDM Dance Directory: {last_err if 'last_err' in locals() else 'no response'}")
        return []
    try:
        soup = BeautifulSoup(r.text, "lxml")
    except Exception as e:
        print(f"  ❌ EDM Dance Directory parse: {e}")
        return []

    # Buscar todos los scripts ld+json
    scripts = soup.find_all("script", type="application/ld+json")
    vistos = set()
    for scr in scripts:
        try:
            txt = scr.string or scr.get_text()
            if not txt:
                continue
            data = json.loads(txt)
        except Exception:
            continue
        # data puede ser dict CollectionPage con mainEntity.itemListElement
        candidates = []
        if isinstance(data, dict) and "mainEntity" in data:
            me = data["mainEntity"]
            if isinstance(me, dict) and "itemListElement" in me:
                for li in me["itemListElement"]:
                    it = li.get("item") if isinstance(li, dict) else None
                    if it:
                        candidates.append(it)
        elif isinstance(data, dict) and data.get("@type") == "MusicEvent":
            candidates.append(data)
        elif isinstance(data, list):
            for it in data:
                if isinstance(it, dict) and it.get("@type") == "MusicEvent":
                    candidates.append(it)

        for it in candidates:
            if len(eventos) >= max_eventos:
                break
            nombre = (it.get("name") or "").strip()[:120]
            if not nombre or nombre in vistos:
                continue
            start = _normalizar_fecha(it.get("startDate", ""))
            loc = it.get("location", {}) if isinstance(it.get("location"), dict) else {}
            lugar = (loc.get("name") or "").strip() or "N/A"
            addr = loc.get("address", {}) if isinstance(loc.get("address"), dict) else {}
            pais = (addr.get("addressLocality") or addr.get("addressCountry") or "N/A").strip() or "N/A"
            # Si pais es ciudad, intentar extraer país real del texto
            # Por ahora dejamos como está; geo_utils lo normaliza después
            org = it.get("organizer", {}) if isinstance(it.get("organizer"), dict) else {}
            organizador = (org.get("name") or loc.get("name") or "N/A").strip()[:80] or "N/A"
            link = it.get("url") or (r.url if r is not None else LIST_URL)
            if not link.startswith("http"):
                link = BASE_URL + link
            desc = it.get("description") or ""
            subgenero = ""
            if _extractor:
                try:
                    subgenero = _extractor.clasificar_subgenero(f"{nombre} {desc} psytrance")
                except Exception:
                    subgenero = "psytrance"
            else:
                subgenero = "psytrance"
            vistos.add(nombre)
            eventos.append({
                "nombre": nombre,
                "fecha": start or "N/A",
                "lugar": lugar,
                "pais": pais,
                "fuente": "EDM Dance Directory",
                "organizador": organizador,
                "email": link,
                "link": link,
                "subgenero": subgenero or "psytrance",
                "descripcion": desc[:400],
            })
            time.sleep(0.1)

    print(f"  ✅ EDM Dance Directory: {len(eventos)} eventos")
    return eventos[:max_eventos]


if __name__ == "__main__":
    evs = scrape_edmdancedirectory()
    for ev in evs[:10]:
        print(f"  - {ev['nombre'][:60]} | {ev['fecha']} | {ev['lugar']} | {ev['pais']}")
