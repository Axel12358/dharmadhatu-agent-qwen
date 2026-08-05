#!/usr/bin/env python3
"""
Scraper de Ektoplazm (ektoplazm.com) — netlabel de psytrance.

Situación: WordPress con releases, no eventos. Algunas news posts mencionan
           festivales o parties. Estrategia: parsear posts recientes y detectar
           menciones de eventos.
Requiere: requests + bs4
"""

import re
import sys
from pathlib import Path
from typing import List, Dict

from bs4 import BeautifulSoup
import requests

_PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from scrapers.event_extractor import EventExtractor

BASE_URL = "https://ektoplazm.com"
NEWS_URL = f"{BASE_URL}/blog"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}
MAX_EVENTOS = 20
_extractor = EventExtractor()

EVENT_KEYWORDS = [
    "festival", "party", "parties", "event", "gig", "rave", "live set",
    "openair", "open air", "club night", "lineup", "tickets", "venue",
]


def _es_post_evento(title: str, content: str) -> bool:
    texto = (title + " " + content).lower()
    return any(kw in texto for kw in EVENT_KEYWORDS)


def scrape_ektoplazm(max_eventos=MAX_EVENTOS) -> List[Dict]:
    """Scrapea menciones de eventos de Ektoplazm (news posts)."""
    print("🎵 Scraping Ektoplazm...")
    eventos = []

    try:
        r = requests.get(NEWS_URL, headers=HEADERS, timeout=20)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
    except Exception as e:
        print(f"  ❌ Ektoplazm: {e}")
        return []

    # Buscar artículos/posts
    articles = soup.find_all("article") or soup.find_all("div", class_=re.compile(r"post|entry|article"))

    for art in articles[:20]:
        title_el = art.find(["h2", "h3", "h1"])
        title = title_el.get_text(strip=True) if title_el else ""
        content_el = art.find(["p", "div"], class_=re.compile(r"content|entry|excerpt"))
        content = content_el.get_text(strip=True) if content_el else ""

        if not _es_post_evento(title, content):
            continue

        link_el = art.find("a", href=True)
        link = link_el["href"] if link_el else BASE_URL
        if not link.startswith("http"):
            link = f"{BASE_URL}/{link.lstrip('/')}"

        texto = f"{title} {content[:200]}"
        subgenero = _extractor.clasificar_subgenero(texto)

        eventos.append({
            "nombre": title[:120],
            "fecha": "N/A",
            "lugar": "N/A",
            "pais": "N/A",
            "fuente": "Ektoplazm",
            "organizador": "Ektoplazm",
            "email": link,
            "link": link,
            "subgenero": subgenero,
            "descripcion": content[:300],
        })

    eventos = eventos[:max_eventos]
    print(f"  ✅ Ektoplazm: {len(eventos)} menciones de eventos")
    return eventos


if __name__ == "__main__":
    eventos = scrape_ektoplazm()
    for ev in eventos[:10]:
        print(f"  - {ev['nombre'][:50]} | {ev['link']}")
