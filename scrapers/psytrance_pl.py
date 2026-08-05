#!/usr/bin/env python3
"""
Scraper de psytrance.pl — portal polaco de psytrance.

Estrategia:
1. Parsear la lista de eventos del homepage (/event/ paths).
2. Visitar cada página de evento individual para extraer
   fecha, lugar, organizador y descripción.
3. También extraer el enlace a Facebook Events para eventos actuales.
Requiere: requests + bs4
"""

import re
import sys
import time
from pathlib import Path
from typing import List, Dict
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

_PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from scrapers.event_extractor import EventExtractor

BASE_URL = "https://www.psytrance.pl"
HOME_URL = BASE_URL + "/"
EVENTS_URL = BASE_URL + "/events/"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}
MAX_EVENTOS = 30
_extractor = EventExtractor()


def _parse_event_page(url: str) -> Dict:
    """Visita una página de evento individual y extrae detalles."""
    try:
        r = requests.get(url, headers=HEADERS, timeout=20)
        if r.status_code != 200:
            return None
        soup = BeautifulSoup(r.text, "html.parser")

        # Título del evento (h1 o title)
        h1 = soup.find("h1")
        nombre = h1.get_text(strip=True) if h1 else ""

        # Buscar fecha en el contenido
        texto_completo = soup.get_text()
        fecha = "N/A"
        lugar = "Poland"
        organizador = "Psytrance.pl"

        # Patrones de fecha
        fecha_match = re.search(r'(\d{2}\.\d{2}\.\d{4})', texto_completo)
        if fecha_match:
            fecha = fecha_match.group(1)

        # Buscar lugar en el contenido
        lugar_match = re.search(r'(?:Miejscowość|Miejsce|Lugar|Location|Venue)[:\s]+(.+?)(?:\n|$)', texto_completo, re.I)
        if lugar_match:
            lugar = lugar_match.group(1).strip()[:80]

        # Buscar organizador
        org_match = re.search(r'(?:Organizator|Organizer|Zorganizowane przez)[:\s]+(.+?)(?:\n|$)', texto_completo, re.I)
        if org_match:
            organizador = org_match.group(1).strip()[:80]

        # Buscar enlace a Facebook
        fb_link = ""
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if "facebook.com" in href.lower():
                fb_link = href
                break

        return {
            "nombre": nombre or "Evento psytrance (Polonia)",
            "fecha": fecha,
            "lugar": lugar,
            "pais": "Poland",
            "fuente": "Psytrance.pl",
            "organizador": organizador,
            "email": fb_link or url,
            "link": fb_link or url,
            "subgenero": "",
            "descripcion": texto_completo[:500],
        }
    except Exception:
        return None


def scrape_psytrance_pl(max_eventos=MAX_EVENTOS) -> List[Dict]:
    """Scrapea eventos de psytrance.pl desde el homepage y páginas individuales."""
    print("🇵🇱 Scraping psytrance.pl...")
    eventos = []

    # 1. Parsear homepage para encontrar enlaces a eventos
    try:
        r = requests.get(HOME_URL, headers=HEADERS, timeout=20)
        r.raise_for_status()
    except Exception as e:
        print(f"  ❌ psytrance.pl homepage: {e}")
        return []

    soup = BeautifulSoup(r.text, "html.parser")

    # Buscar enlaces a /event/ en el homepage
    event_links = set()
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "/event/" in href:
            full_url = urljoin(BASE_URL, href)
            event_links.add(full_url)

    # 2. También buscar enlaces a Facebook Events
    fb_links = set()
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "facebook.com" in href.lower():
            fb_links.add(href)

    # 3. Visitar cada página de evento individual
    count = 0
    for event_url in list(event_links)[:max_eventos]:
        if count >= max_eventos:
            break
        ev = _parse_event_page(event_url)
        if ev:
            texto = f"{ev['nombre']} psytrance Poland"
            ev["subgenero"] = _extractor.clasificar_subgenero(texto)
            eventos.append(ev)
            count += 1
            time.sleep(1)  # Rate limiting

    # 4. Añadir enlace a Facebook Events si existe
    for fb_link in fb_links:
        if count >= max_eventos:
            break
        # Verificar que no sea duplicado
        if any(e.get("link") == fb_link for e in eventos):
            continue
        slug = fb_link.rstrip("/").split("/")[-1]
        nombre = slug.replace("-", " ").replace("_", " ").title()
        if len(nombre) < 3:
            nombre = "Evento psytrance (Polonia)"
        eventos.append({
            "nombre": nombre,
            "fecha": "N/A",
            "lugar": "Poland",
            "pais": "Poland",
            "fuente": "Psytrance.pl",
            "organizador": "Psytrance.pl",
            "email": fb_link,
            "link": fb_link,
            "subgenero": _extractor.clasificar_subgenero(f"{nombre} psytrance Poland"),
            "descripcion": f"Evento psytrance desde psytrance.pl. Enlace Facebook: {fb_link}",
        })
        count += 1

    eventos = eventos[:max_eventos]
    print(f"  ✅ psytrance.pl: {len(eventos)} eventos ({len(event_links)} páginas individuales)")
    return eventos


if __name__ == "__main__":
    eventos = scrape_psytrance_pl()
    for ev in eventos[:10]:
        print(f"  - {ev['nombre'][:50]} | {ev['fecha']} | {ev['lugar']}")