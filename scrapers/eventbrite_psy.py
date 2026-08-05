#!/usr/bin/env python3
"""
Scraper de Eventbrite para eventos psytrance SIN LOGIN usando Playwright.

Busca en Eventbrite por palabras clave psytrance en múltiples ciudades.
Filtra resultados para reducir ruido (solo títulos con keywords psytrance).
Requiere: pip install playwright && playwright install chromium
"""

import asyncio
import random
import re
import sys
from pathlib import Path
from typing import List, Dict

_PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from scrapers.event_extractor import EventExtractor

# Ciudades de búsqueda (solo las más relevantes para psytrance)
CIUDADES_EVENTBRITE = [
    ("Berlin", "germany--berlin"),
    ("Amsterdam", "netherlands--amsterdam"),
    ("London", "united-kingdom--london"),
    ("Paris", "france--paris"),
    ("Tel Aviv", "israel--tel-aviv"),
    ("Budapest", "hungary--budapest"),
    ("São Paulo", "brazil--sao-paulo"),
    ("Copenhagen", "denmark--copenhagen"),
    ("Vienna", "austria--vienna"),
    ("Prague", "czech-republic--prague"),
    ("Athens", "greece--athens"),
    ("Istanbul", "turkey--istanbul"),
    ("Cape Town", "south-africa--cape-town"),
    ("Bangkok", "thailand--bangkok"),
    ("Tokyo", "japan--tokyo"),
    ("Seoul", "south-korea--seoul"),
    ("Singapore", "singapore--singapore"),
    ("Hong Kong", "hong-kong--hong-kong"),
]

# Solo keywords que son específicas de psytrance (reduce ruido)
PSYTRANCE_KEYWORDS = [
    "psytrance", "psy trance", "goa trance", "goa", "darkpsy", "dark psy",
    "forest psy", "hitech", "hi-tech", "full on", "psychedelic trance",
    "psydub", "psychill", "psybient", "progressive psy", "progressive trance",
]

MAX_EVENTOS = 80
_extractor = EventExtractor()


async def _scrape_ciudad(page, ciudad_slug, ciudad_nombre) -> List[Dict]:
    """Scrapea una ciudad de Eventbrite buscando keywords psytrance."""
    eventos = []
    seen_urls = set()

    for kw in PSYTRANCE_KEYWORDS[:4]:
        url = f"https://www.eventbrite.com/d/{ciudad_slug}/{kw}/"
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=25000)
            await asyncio.sleep(random.uniform(2, 4))

            # Esperar a que carguen los resultados
            await page.wait_for_selector(
                'a[href*="/e/"]', timeout=8000
            )

            # Extraer enlaces a eventos
            links = await page.query_selector_all('a[href*="/e/"]')

            for a in links:
                href = await a.get_attribute("href")
                if not href or href in seen_urls:
                    continue
                if not href.startswith("http"):
                    href = f"https://www.eventbrite.com{href}"
                seen_urls.add(href)

                # Extraer nombre del enlace
                nombre = (await a.inner_text()).strip()
                if not nombre or len(nombre) < 5:
                    continue

                # Filtrar: solo eventos con keywords psytrance en el título
                nombre_lower = nombre.lower()
                if not any(kw in nombre_lower for kw in PSYTRANCE_KEYWORDS):
                    continue

                # Buscar fecha y lugar en elementos cercanos
                fecha = "N/A"
                lugar = ciudad_nombre
                try:
                    parent = await a.evaluate_handle("el => el.closest('[class*=\"event\"]') || el.parentElement")
                    parent_text = await parent.evaluate("el => el.innerText") if parent else ""
                    if parent_text:
                        # Buscar fecha
                        fecha_match = re.search(r'(\w{3}\s+\d{1,2},?\s*\d{4})', parent_text)
                        if fecha_match:
                            fecha = fecha_match.group(1)
                        # Buscar lugar (líneas que no son el nombre ni la fecha)
                        lines = [l.strip() for l in parent_text.split("\n") if l.strip()]
                        for line in lines:
                            if line != nombre and not re.search(r'\d{1,2}[\/\.\-]\d{1,2}', line) and len(line) < 80:
                                lugar = line
                                break
                except Exception:
                    pass

                texto = f"{nombre} {lugar} {ciudad_nombre}"
                subgenero = _extractor.clasificar_subgenero(texto)

                eventos.append({
                    "nombre": nombre,
                    "fecha": fecha,
                    "lugar": lugar,
                    "pais": ciudad_nombre,
                    "fuente": "Eventbrite",
                    "organizador": nombre.split(" - ")[0].split(" @ ")[0].strip()[:60],
                    "email": href,
                    "link": href,
                    "subgenero": subgenero,
                    "descripcion": f"{nombre} en {lugar}, {ciudad_nombre}",
                })

            await asyncio.sleep(random.uniform(1, 3))
        except Exception:
            continue

    return eventos


async def scrape_eventbrite_psy(ciudades=None, max_eventos=MAX_EVENTOS) -> List[Dict]:
    """Scrapea eventos psytrance de Eventbrite en múltiples ciudades."""
    from playwright.async_api import async_playwright

    if ciudades is None:
        ciudades = CIUDADES_EVENTBRITE

    print("🎫 Scraping Eventbrite (psytrance, Playwright)...")
    todos = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--no-sandbox"])
        page = await browser.new_page()
        page.set_default_timeout(25000)

        for slug, nombre in ciudades:
            try:
                evs = await _scrape_ciudad(page, slug, nombre)
                if evs:
                    print(f"  ✅ {nombre}: {len(evs)} eventos psytrance")
                    todos.extend(evs)
                else:
                    print(f"  ⚠️ {nombre}: 0 eventos psytrance")
            except Exception as e:
                print(f"  ❌ {nombre}: {e}")

        await browser.close()

    # Deduplicar por URL
    vistos = set()
    unicos = []
    for ev in todos:
        k = ev.get("link", "")
        if k and k not in vistos:
            vistos.add(k)
            unicos.append(ev)

    unicos = unicos[:max_eventos]
    print(f"✅ Eventbrite psytrance total: {len(unicos)} eventos")
    return unicos


if __name__ == "__main__":
    eventos = asyncio.run(scrape_eventbrite_psy())
    for ev in eventos[:10]:
        print(f"  - {ev['nombre'][:50]} | {ev['fecha'][:20]} | {ev['lugar'][:30]}")