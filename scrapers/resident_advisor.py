#!/usr/bin/env python3
"""
Scraper de Resident Advisor (ra.co) SIN LOGIN usando la API GraphQL pública.

La web ra.co bloquea peticiones HTTP (DataDome), pero su API GraphQL
(https://ra.co/graphql) es pública y no requiere autenticación.

Estrategia:
1. Resuelve el id de área de cada ciudad clave vía `areas(searchTerm:)`.
2. Consulta `facetedSearch` por área con fecha >= hoy, ordenada ascendente.
3. Filtra client-side eventos de psytrance/techno/trance/house por género
   o por palabras clave en el título.
4. Devuelve eventos en el formato estándar del proyecto.

Requiere: requests + bs4 (opcional). Sin cookies. A prueba de bloqueos.
"""

import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Dict, Optional

import socket
import requests

socket.setdefaulttimeout(15)

_PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

OUTPUT_FILE = str(Path(_PROJECT_ROOT) / "eventos_resident_advisor.json")

from scrapers.event_extractor import EventExtractor

GRAPHQL_URL = "https://ra.co/graphql"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
    "Accept-Language": "en,es;q=0.9",
}

# Ciudades europeas clave (las que RA soporta). Se resuelve el id por búsqueda.
# Lista ampliada (mejora): añade Oslo, Bruselas, Dublín, Edimburgo, Hamburgo,
# Colonia, Múnich, Roma, Nápoles, Turín, Burdeos, Lyon, Marsella, Oporto,
# Boloña, Florencia, Verona y Génova (+18 ciudades) para superar 250 eventos
# consolidados. Mantiene las 16 originales intactas (nunca se restan).
CIUDADES_RA = [
    "Berlin", "Barcelona", "Madrid", "Amsterdam", "Paris", "London",
    "Lisbon", "Vienna", "Milan", "Budapest", "Prague", "Tel Aviv",
    "Athens", "Zurich", "Copenhagen", "Stockholm",
    # Ampliación (18 ciudades nuevas)
    "Oslo", "Brussels", "Dublin", "Edinburgh",
    "Hamburg", "Cologne", "Munich", "Rome", "Naples", "Turin",
    "Bordeaux", "Lyon", "Marseille", "Porto",
    "Bologna", "Florence", "Verona", "Genoa",
    # Ampliación global (12 ciudades no europeas)
    "Buenos Aires", "Ciudad de México", "São Paulo",
    "Istanbul", "Bombay", "Bangkok", "Tokyo", "Seoul",
    "Jakarta", "Cape Town", "Cairo", "Casablanca",
    # Ampliación 3 (15 ciudades adicionales)
    "Warsaw", "Krakow", "Bucharest", "Belo Horizonte", "Lima",
    "Bogota", "Santiago", "Montevideo", "Tbilisi", "Yerevan",
    "Tunis", "Marrakech", "Taipei", "Kuala Lumpur", "Ho Chi Minh City",
]

# Géneros que interesan para el proyecto (psytrance/techno/scene)
GENEROS_INTERES = {
    "psytrance", "trance", "techno", "tech house", "minimal techno",
    "dub techno", "progressive house", "house", "ambient", "goa",
    "hard techno", "acid techno", "schranz", "breaks", "dark psy",
}

# Palabras clave extra en el título/lineup por si el género no está etiquetado
KEYWORDS_TITULO = [
    "psytrance", "psy trance", "goa", "darkpsy", "dark psy", "forest psy",
    "hitech", "hi-tech", "full on", "fullon", "twilight", "psycore",
    "suomisaundi", "zenon", "psychill", "psybient", "psychedelic",
    "techno", "trance", "rave", "acid",
]

MAX_PAGINAS_POR_CIUDAD = 3
PAGE_SIZE = 50
# Tope final de eventos (ampliado para 46 ciudades y >300 eventos consolidados).
MAX_EVENTOS = 500

# Horizonte de búsqueda en meses. None = sin límite superior (comportamiento
# original, todos los eventos futuros). loop_mejora.py puede probar 6/9/12 meses.
HORIZONTE_MESES = None

# Sobrescritura dinámica desde loop_mejora.py (None = usar valor por defecto).
# Mecanismo aditivo: si no se llama a set_parametros_loop(), scrape_ra() se
# comporta exactamente como antes.
_OVERRIDE_CIUDADES = None
_OVERRIDE_MAX_EVENTOS = None
_OVERRIDE_HORIZONTE = None

_extractor = EventExtractor()


def set_parametros_loop(ciudades=None, max_eventos=None, horizonte_meses=None):
    """Permite que loop_mejora.py ajuste ciudades/eventos/horizonte sin tocar
    constantes. Pasar None deja el valor por defecto (reversible)."""
    global _OVERRIDE_CIUDADES, _OVERRIDE_MAX_EVENTOS, _OVERRIDE_HORIZONTE
    if ciudades is not None:
        _OVERRIDE_CIUDADES = list(ciudades)
    if max_eventos is not None:
        _OVERRIDE_MAX_EVENTOS = int(max_eventos)
    if horizonte_meses is not None:
        _OVERRIDE_HORIZONTE = int(horizonte_meses)


def _fecha_dentro_de_meses(meses) -> str:
    """Devuelve YYYY-MM-DD para 'hoy + meses' (uso como límite superior)."""
    ahora = datetime.now(timezone.utc)
    anio = ahora.year + (ahora.month - 1 + meses) // 12
    mes = (ahora.month - 1 + meses) % 12 + 1
    dia = min(ahora.day, 28)
    return ahora.replace(year=anio, month=mes, day=dia).strftime("%Y-%m-%d")


def _gql(query: str) -> dict:
    r = requests.post(GRAPHQL_URL, json={"query": query}, headers=HEADERS, timeout=20)
    r.raise_for_status()
    return r.json()


def _resolver_area(ciudad: str) -> Optional[dict]:
    """Devuelve {id, name, urlName} del área RA o None si no existe."""
    try:
        q = (
            'query { areas(searchTerm: "' + ciudad.replace('"', "") +
            '", limit: 1) { id name urlName isCountry } }'
        )
        d = _gql(q)
        areas = (d.get("data") or {}).get("areas") or []
        for a in areas:
            if not a.get("isCountry"):
                return a
        if areas:
            return areas[0]
    except Exception:
        pass
    return None


def _eventos_area(area_id, area_nombre, horizonte_meses=None) -> List[Dict]:
    """Página eventos futuros de un área, filtrando por género/título.

    horizonte_meses: límite superior de fechas (None = todos los futuros,
    comportamiento original). Con 6, solo eventos de los próximos 6 meses.
    """
    hoy = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    eventos = []
    eventos_area_totales = 0

    filtro_fecha = f'date: {{gte: "{hoy}T00:00:00"'
    if horizonte_meses:
        fin = _fecha_dentro_de_meses(horizonte_meses)
        filtro_fecha += f', lte: "{fin}T23:59:59"'
    filtro_fecha += "}"

    for page in range(1, MAX_PAGINAS_POR_CIUDAD + 1):
        q = (
            "query { facetedSearch(types: EVENT, "
            f"filters: {{ areas: {{eq: {area_id}}}, {filtro_fecha} }}, "
            "sort: { date: { order: ASCENDING } }, "
            f"page: {page}, pageSize: {PAGE_SIZE}) "
            "{ totalResults results { id data { __typename "
            "... on Event { id title date venue { name } area { name } "
            "genres { name } lineup content } } } } }"
        )
        try:
            d = _gql(q)
        except Exception as e:
            print(f"    ⚠️ RA {area_nombre} pág {page}: {e}")
            break

        fs = (d.get("data") or {}).get("facetedSearch") or {}
        resultados = fs.get("results") or []
        if not resultados:
            break
        eventos_area_totales += len(resultados)

        for r in resultados:
            ev = r.get("data")
            if not isinstance(ev, dict):
                continue
            if not _es_evento_interes(ev):
                continue
            norm = _normalizar_evento(ev, area_nombre)
            if norm:
                eventos.append(norm)

        if eventos_area_totales >= (fs.get("totalResults") or 0) and page > 1:
            break

    return eventos


def _es_evento_interes(ev: dict) -> bool:
    titulo = (ev.get("title") or "").lower()
    generos = {g.get("name", "").lower() for g in (ev.get("genres") or [])}
    lineup = (ev.get("lineup") or "").lower()

    if generos & GENEROS_INTERES:
        return True
    texto = titulo + " " + lineup
    if any(kw in texto for kw in KEYWORDS_TITULO):
        return True
    return False


def _normalizar_evento(ev: dict, area_nombre: str) -> Optional[Dict]:
    titulo = (ev.get("title") or "").strip() or "N/A"
    if titulo.lower() in ("tba", "tbd", "sin nombre"):
        return None

    fecha_raw = ev.get("date") or ""
    fecha = fecha_raw[:10] if len(fecha_raw) >= 10 else "N/A"

    venue = (ev.get("venue") or {}).get("name") or "N/A"
    area = (ev.get("area") or {}).get("name") or area_nombre

    generos = ", ".join(g.get("name", "") for g in (ev.get("genres") or [])) or "N/A"
    lineup = (ev.get("lineup") or "").strip() or "N/A"
    contenido = (ev.get("content") or "").strip()

    descripcion = "; ".join(p for p in [lineup, generos, contenido[:300]] if p and p != "N/A")

    tipo_lugar = _extractor._extract_venue_type(f"{titulo} {venue} {area} {descripcion}") or "N/A"

    # Organizador: usar venue como organizador (venues suelen organizar eventos)
    organizador = venue if venue != "N/A" else "N/A"
    
    # Email/Contacto: usar URL del evento como referencia
    evento_url = f"https://ra.co/events/{ev.get('id', '')}"

    evento = {
        "nombre": titulo,
        "fecha": fecha,
        "lugar": venue,
        "ciudad": area,
        "pais": "N/A",
        "tipo_lugar": tipo_lugar,
        "fuente": "Resident Advisor",
        "organizador": organizador,
        "email": "",  # RA no proporciona emails en su API pública
        "url": evento_url,
        "link": evento_url,
        "descripcion": descripcion[:500],
        "generos": generos,
    }

    # País desde el extractor si el área coincide con ciudad conocida
    cc = _extractor._extract_city_country(descripcion + " " + area, area)
    if isinstance(cc, tuple) and len(cc) == 2:
        evento["ciudad"] = cc[0] if cc[0] != "N/A" else area
        evento["pais"] = cc[1] if cc[1] != "N/A" else "N/A"

    return evento


def scrape_ra(ciudades=None, max_eventos=None, horizonte_meses=None) -> List[Dict]:
    """Scrapea eventos de Resident Advisor.

    Parámetros opcionales (usados por loop_mejora.py):
      - ciudades: sublista de ciudades a procesar (None = CIUDADES_RA completa).
      - max_eventos: tope final de eventos (None = MAX_EVENTOS).
      - horizonte_meses: límite superior de fechas (None = sin límite).
    Sin argumentos se comporta exactamente igual que antes (nunca restar).
    """
    print("🌐 Scraping Resident Advisor (GraphQL, sin login)...")
    if ciudades is None:
        ciudades = _OVERRIDE_CIUDADES or CIUDADES_RA
    if max_eventos is None:
        max_eventos = _OVERRIDE_MAX_EVENTOS or MAX_EVENTOS
    if horizonte_meses is None:
        horizonte_meses = _OVERRIDE_HORIZONTE or HORIZONTE_MESES

    todos = []

    for ciudad in ciudades:
        area = _resolver_area(ciudad)
        if not area:
            print(f"  ⚠️ {ciudad}: área no encontrada")
            continue
        evs = _eventos_area(area["id"], area["name"], horizonte_meses)
        print(f"  ✅ {area['name']}: {len(evs)} eventos de interés")
        todos.extend(evs)
        time.sleep(0.5)

    # Deduplicar por nombre+fecha+lugar
    vistos = set()
    unicos = []
    for ev in todos:
        k = (ev.get("nombre", ""), ev.get("fecha", ""), ev.get("lugar", ""))
        if k not in vistos:
            vistos.add(k)
            unicos.append(ev)

    unicos = unicos[:max_eventos]

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(unicos, f, indent=2, ensure_ascii=False)
    print(f"✅ {len(unicos)} eventos de RA guardados en {OUTPUT_FILE}")
    return unicos


if __name__ == "__main__":
    eventos = scrape_ra()
    print(f"\nTotal eventos RA: {len(eventos)}")
    for ev in eventos[:10]:
        print(f"  - {ev.get('nombre', '?')[:55]} | {ev.get('fecha', '?')} | {ev.get('lugar', '?')}")
