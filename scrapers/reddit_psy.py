#!/usr/bin/env python3
"""
Scraper de Reddit (r/psytrance, r/aves, etc.) vía feed RSS/Atom SIN LOGIN.

Extrae posts de eventos/fiestas/festivales de múltiples subreddits.
Requiere: requests
"""

import re
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Dict
from urllib.parse import unquote

import requests

_PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from scrapers.event_extractor import EventExtractor

SUBREDDITS = [
    "psytrance",
    "aves",
    "Psyculture",
    "GoaGigs",
    "psychedelictrance",
    "goatrance",
    "electronicmusic",
    "festivals",
    "psytranceproduction",
    "raves",
]
RSS_URL = "https://www.reddit.com/r/{sub}/.rss?limit=100"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

# Palabras clave que indican que un post es sobre un evento
EVENT_KEYWORDS = [
    "festival", "party", "parties", "event", "gig", "rave", "live",
    "openair", "open air", "outdoor", "indoor", "club night", "clubnight",
    "lineup", "line-up", "tickets", "tickets on sale", "presale",
    "venue", "location announced", "dates announced", "aftermovie",
    "sunset", "sunrise", "dancefloor",
    "this weekend", "next weekend", "coming up", "announced",
    "going to", "attending", "who's going", "wanna go",
    "jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec",
    "2026", "2027", "2025",
]

# Palabras clave psytrance para filtrar títulos
PSYTRANCE_KEYWORDS = [
    "psytrance", "psy trance", "goa trance", "goa", "darkpsy", "dark psy",
    "forest psy", "hitech", "hi-tech", "full on", "psychedelic trance",
    "psydub", "psychill", "psybient", "progressive psy", "progressive trance",
]

MAX_EVENTOS = 100
_extractor = EventExtractor()
ATOM_NS = {"atom": "http://www.w3.org/2005/Atom"}


def _es_post_evento(title: str, content: str) -> bool:
    """Determina si un post de Reddit es sobre un evento."""
    texto = (title + " " + content).lower()
    return any(kw in texto for kw in EVENT_KEYWORDS)


def _es_psytrance(title: str, content: str) -> bool:
    """Determina si un post es relevante para psytrance."""
    texto = (title + " " + content).lower()
    return any(kw in texto for kw in PSYTRANCE_KEYWORDS)


def _parse_fecha(fecha_str: str) -> str:
    """Convierte fecha Atom a YYYY-MM-DD."""
    try:
        dt = datetime.fromisoformat(fecha_str.replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%d")
    except Exception:
        return "N/A"


def _extraer_fecha_titulo(title: str) -> str:
    """Intenta extraer fecha del título del post."""
    # Patrones de fecha
    patrones = [
        r'(\d{1,2})[\/\.\-](\d{1,2})[\/\.\-](\d{2,4})',  # DD/MM/YYYY
        r'(\d{1,2})\s+(?:de|of)?\s*(\w{3})\s*(?:de|of)?\s*(\d{4})',  # 15 Aug 2026
        r'(?:on|el)\s+(\d{1,2}\s+\w{3}\s+\d{4})',  # on 15 Aug 2026
        r'(?:this|next)\s+(weekend|saturday|sunday|monday|tuesday|wednesday|thursday|friday)',
    ]
    for pat in patrones:
        m = re.search(pat, title, re.I)
        if m:
            return m.group(1) if m.lastindex == 1 else m.group(0)
    return "N/A"


def _extraer_lugar_titulo(title: str) -> str:
    """Intenta extraer lugar/ciudad del título del post."""
    # Buscar patrones comunes: "en Madrid", "in Berlin", "@ Club Name"
    m = re.search(r'(?:en|in|@|at|—)\s*([A-ZÁÉÍÓÚÑ][a-záéíóúñ]+(?:\s+[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+){0,3})', title)
    if m:
        lugar = m.group(1).strip()
        # Filtrar palabras comunes que no son lugares
        filtro = ["this", "that", "the", "a", "an", "for", "with", "and", "or", "but"]
        if lugar.lower() not in filtro and len(lugar) > 2:
            return lugar
    return "N/A"


def _extraer_info_evento(title: str, content: str) -> Dict:
    """Intenta extraer nombre del evento, fecha y lugar."""
    nombre = title.strip()
    if len(nombre) > 150:
        nombre = nombre[:147] + "..."

    fecha = _extraer_fecha_titulo(title)
    lugar = _extraer_lugar_titulo(title)

    return {"nombre": nombre, "fecha": fecha, "lugar": lugar}


def scrape_reddit_psy(max_eventos=MAX_EVENTOS) -> List[Dict]:
    """Scrapea posts de eventos psytrance de múltiples subreddits vía RSS."""
    print("📱 Scraping Reddit (psytrance events, RSS)...")
    todos = []

    for sub in SUBREDDITS:
        url = RSS_URL.format(sub=sub)
        try:
            r = requests.get(url, headers=HEADERS, timeout=30)
            if r.status_code == 429:
                print(f"  ⚠️ r/{sub}: rate limited (429), saltando")
                time.sleep(10)
                continue
            r.raise_for_status()
            root = ET.fromstring(r.text)

            entries = root.findall("atom:entry", ATOM_NS)
            eventos_sub = 0

            for entry in entries:
                title_el = entry.find("atom:title", ATOM_NS)
                content_el = entry.find("atom:content", ATOM_NS)
                updated_el = entry.find("atom:updated", ATOM_NS)
                link_el = entry.find("atom:link", ATOM_NS)

                title = title_el.text if title_el is not None else ""
                content = content_el.text if content_el is not None else ""
                updated = updated_el.text if updated_el is not None else ""
                link = link_el.get("href", "") if link_el is not None else ""

                if not _es_post_evento(title, content):
                    continue
                if not _es_psytrance(title, content):
                    continue

                info = _extraer_info_evento(title, content)
                if info["fecha"] in ("N/A", ""):
                    continue
                texto_completo = f"{title} {content[:300]}"
                subgenero = _extractor.clasificar_subgenero(texto_completo)

                # Limpiar HTML del contenido
                content_clean = re.sub(r'<[^>]+>', ' ', content)[:300]

                todos.append({
                    "nombre": info["nombre"],
                    "fecha": info["fecha"] or _parse_fecha(updated),
                    "lugar": info["lugar"],
                    "pais": "N/A",
                    "fuente": "Reddit",
                    "organizador": f"r/{sub}",
                    "email": link,
                    "link": link,
                    "subgenero": subgenero,
                    "descripcion": content_clean.strip(),
                })
                eventos_sub += 1

            print(f"  ✅ r/{sub}: {eventos_sub} posts de eventos")
            time.sleep(3)  # Respetar rate limit de Reddit
        except Exception as e:
            print(f"  ❌ r/{sub}: {e}")

    # Deduplicar por URL
    vistos = set()
    unicos = []
    for ev in todos:
        k = ev.get("link", "")
        if k and k not in vistos:
            vistos.add(k)
            unicos.append(ev)

    unicos = unicos[:max_eventos]
    print(f"✅ Reddit psytrance total: {len(unicos)} posts de eventos")
    return unicos


if __name__ == "__main__":
    eventos = scrape_reddit_psy()
    for ev in eventos[:10]:
        print(f"  - {ev['nombre'][:55]} | {ev['fecha']} | {ev['lugar']}")