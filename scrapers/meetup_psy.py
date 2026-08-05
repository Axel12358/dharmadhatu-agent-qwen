#!/usr/bin/env python3
"""
Scraper de Meetup para eventos psytrance SIN LOGIN.

Busca en Meetup por palabras clave psytrance en múltiples ciudades.
Usa JSON-LD estructurado cuando está disponible.
Requiere: requests
"""

import json
import re
import sys
from pathlib import Path
from typing import List, Dict

import requests

_PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from scrapers.event_extractor import EventExtractor

# Ciudades de búsqueda (ampliadas)
CIUDADES_MEETUP = [
    "Berlin", "London", "Amsterdam", "Paris", "Madrid", "Barcelona",
    "Lisbon", "Vienna", "Budapest", "Prague", "Warsaw", "Copenhagen",
    "Stockholm", "Oslo", "Helsinki", "Dublin", "Edinburgh", "Brussels",
    "Zurich", "Milan", "Rome", "Munich", "Hamburg", "Cologne",
    "Tel Aviv", "Istanbul", "Athens", "Bucharest", "Sofia",
    "São Paulo", "Buenos Aires", "Mexico City", "Montreal",
    "Tokyo", "Seoul", "Bangkok", "Singapore", "Hong Kong",
    "Cape Town", "Johannesburg", "Nairobi", "Casablanca",
    "Helsingborg", "Malmö", "Gothenburg", "Uppsala", "Linköping",
    "Bristol", "Manchester", "Glasgow", "Birmingham", "Leeds",
    "Leipzig", "Dresden", "Frankfurt", "Stuttgart", "Düsseldorf",
    "Bologna", "Florence", "Venice", "Naples", "Turin", "Genoa",
    "Verona", "Padua", "Trieste",
    "Lyon", "Marseille", "Bordeaux", "Toulouse", "Nice", "Nantes",
    "Porto", "Coimbra", "Braga", "Aveiro",
    "Barcelona", "Valencia", "Seville", "Bilbao", "Málaga",
    "Wrocław", "Kraków", "Gdańsk", "Poznań",
]

PSYTRANCE_KEYWORDS = [
    "psytrance", "psy trance", "goa trance", "goa", "darkpsy", "dark psy",
    "forest psy", "hitech", "hi-tech", "full on", "psychedelic trance",
    "psydub", "psychill", "psybient", "progressive psy", "progressive trance",
    "psycore", "suomisaundi", "zenon", "twilight",
    "trance", "festival", "rave", "party", "open air", "outdoor",
    "psychedelic", "goa", "fullon", "full-on",
]

MAX_EVENTOS = 60
_extractor = EventExtractor()


def _es_psytrance(texto: str) -> bool:
    """Determina si un texto es relevante para psytrance."""
    t = texto.lower()
    return any(kw in t for kw in PSYTRANCE_KEYWORDS)


def _parse_jsonld_events(html: str) -> List[Dict]:
    """Extrae eventos de JSON-LD estructurado en el HTML."""
    import xml.etree.ElementTree as ET
    events = []
    ld_matches = re.findall(
        r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>',
        html, re.S
    )
    for ld_text in ld_matches:
        try:
            ld = json.loads(ld_text)
            if isinstance(ld, list):
                for item in ld:
                    if isinstance(item, dict) and item.get("@type") == "Event":
                        events.append(item)
            elif isinstance(ld, dict) and ld.get("@type") == "Event":
                events.append(ld)
        except Exception:
            continue
    return events


def scrape_meetup_psy(ciudades=None, max_eventos=MAX_EVENTOS) -> List[Dict]:
    """Scrapea eventos psytrance de Meetup en múltiples ciudades."""
    if ciudades is None:
        ciudades = CIUDADES_MEETUP

    print("🌍 Scraping Meetup (psytrance)...")
    todos = []

    for ciudad in ciudades:
        try:
            # Buscar eventos psytrance en la ciudad
            params = {"keywords": "psytrance", "source": "EVENTS", "location": ciudad}
            r = requests.get("https://www.meetup.com/find/", params=params,
                             headers={"User-Agent": "Mozilla/5.0"}, timeout=8)
            if r.status_code != 200:
                continue

            # Extraer JSON-LD
            ld_events = _parse_jsonld_events(r.text)

            for ld in ld_events:
                nombre = ld.get("name", "") or "N/A"
                if not _es_psytrance(nombre):
                    continue

                fecha_raw = ld.get("startDate", "") or "N/A"
                fecha = fecha_raw[:10] if fecha_raw else "N/A"

                lugar_data = ld.get("location", {})
                if isinstance(lugar_data, dict):
                    lugar = lugar_data.get("name") or lugar_data.get("address", "")
                    if isinstance(lugar, dict):
                        lugar = lugar.get("addressLocality", "N/A")
                else:
                    lugar = str(lugar_data)

                url = ld.get("url", "")
                descripcion = ld.get("description", "") or ""
                # Limpiar HTML de la descripción
                descripcion = re.sub(r'<[^>]+>', ' ', descripcion)[:300]

                subgenero = _extractor.clasificar_subgenero(f"{nombre} {ciudad} psytrance")

                todos.append({
                    "nombre": nombre[:120],
                    "fecha": fecha,
                    "lugar": str(lugar)[:100] if lugar else "N/A",
                    "pais": ciudad,
                    "fuente": "Meetup",
                    "organizador": nombre.split(" - ")[0][:60],
                    "email": url,
                    "link": url,
                    "subgenero": subgenero,
                    "descripcion": descripcion,
                })

            time.sleep(2)  # Rate limiting
        except Exception:
            continue

    # Deduplicar por URL
    vistos = set()
    unicos = []
    for ev in todos:
        k = ev.get("link", "")
        if k and k not in vistos:
            vistos.add(k)
            unicos.append(ev)

    unicos = unicos[:max_eventos]
    print(f"✅ Meetup psytrance total: {len(unicos)} eventos")
    return unicos


if __name__ == "__main__":
    eventos = scrape_meetup_psy()
    for ev in eventos[:10]:
        print(f"  - {ev['nombre'][:50]} | {ev['fecha']} | {ev['lugar']}")