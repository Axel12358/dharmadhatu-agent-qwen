#!/usr/bin/env python3
"""
Scraper de IsraTrance (isratrance.com) — portal israelí de trance.

Situación: El sitio está actualmente caído (HTTP 0 / timeout).
La última vez que estuvo accesible era un sitio legacy (HTML 4.01)
con framesets y sin sección de eventos funcional.
Estrategia: Intentar conexiones alternativas; si fallan, retornar lista vacía.
Requiere: requests
"""

import sys
from pathlib import Path
from typing import List, Dict

import requests

_PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from scrapers.event_extractor import EventExtractor

BASE_URLS = [
    "https://www.isratrance.com/",
    "http://www.isratrance.com/",
    "https://isratrance.com/",
]
MAX_EVENTOS = 10
_extractor = EventExtractor()


def scrape_isratrance(max_eventos=MAX_EVENTOS) -> List[Dict]:
    """Intenta extraer eventos de IsraTrance. Retorna lista vacía si el sitio está caído."""
    print("🇮🇱 Scraping IsraTrance...")

    for base_url in BASE_URLS:
        try:
            r = requests.get(base_url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
            if r.status_code == 200 and len(r.text) > 100:
                return _parse_isratrance(r.text, base_url)
        except Exception:
            continue

    print(f"  ⚠️ IsraTrance: sitio no accesible (probablemente caído)")
    return []


def _parse_isratrance(html: str, base_url: str) -> List[Dict]:
    """Parsea el HTML de IsraTrance buscando eventos."""
    from bs4 import BeautifulSoup
    eventos = []
    soup = BeautifulSoup(html, "html.parser")

    # Buscar enlaces a eventos/parties
    for a in soup.find_all("a", href=True):
        href = a["href"]
        txt = a.get_text(strip=True).lower()
        if any(kw in href.lower() + txt for kw in ["event", "party", "gig", "calendar"]):
            url = href if href.startswith("http") else f"{base_url}{href.lstrip('/')}"
            nombre = a.get_text(strip=True) or "Evento IsraTrance"
            if len(nombre) < 3:
                continue
            eventos.append({
                "nombre": nombre[:120],
                "fecha": "N/A",
                "lugar": "Israel",
                "pais": "Israel",
                "fuente": "IsraTrance",
                "organizador": "IsraTrance",
                "email": url,
                "link": url,
                "subgenero": _extractor.clasificar_subgenero(f"{nombre} trance Israel"),
                "descripcion": f"Evento desde IsraTrance: {nombre}",
            })

    eventos = eventos[:max_eventos]
    print(f"  ✅ IsraTrance: {len(eventos)} eventos")
    return eventos


if __name__ == "__main__":
    eventos = scrape_isratrance()
    for ev in eventos[:10]:
        print(f"  - {ev['nombre'][:50]} | {ev['link']}")