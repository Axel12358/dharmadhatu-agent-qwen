#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FB Playwright Tor — Scraping directo de páginas de eventos Facebook
a través de Playwright + Tor SOCKS5.

Facebook retorna 200 vía Tor pero necesita JavaScript rendering.
Playwright con proxy SOCKS5 resuelve esto.

Flujo:
  1. Recibe URLs de eventos FB (de dorks, cache, etc.)
  2. Visita cada URL con Playwright + Tor SOCKS5
  3. Extrae: nombre, fecha, lugar, organizador, descripción
  4. Devuelve eventos normalizados

Regla innegociable: SIEMPRE vía Tor, nunca IP real.
"""
from __future__ import annotations

import json
import os
import random
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

_PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

# --- Tor pool ---
try:
    from core.tor_pool import (
        IDENTIDADES_TOR,
        obtener_sesion_tor as _pool_sesion,
        verificar_tor as _pool_verificar,
        rotar_tor as _pool_rotar,
        obtener_siguiente_identidad as _pool_siguiente,
    )
    _TOR_POOL = True
except ImportError:
    _TOR_POOL = False

# --- Playwright ---
try:
    from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
    PW_DISPONIBLE = True
except ImportError:
    PW_DISPONIBLE = False

# --- Stealth (si disponible) ---
try:
    from playwright_stealth import stealth_sync
    STEALTH_DISPONIBLE = True
except ImportError:
    STEALTH_DISPONIBLE = False

# --- User agents ---
_UAS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86; rv:133.0) Gecko/20100101 Firefox/133.0",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
]

# --- Meses para parseo de fechas ---
MESES_EN = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
    "january": 1, "february": 2, "march": 3, "april": 4,
    "june": 6, "july": 7, "august": 8, "september": 9,
    "october": 10, "november": 11, "december": 12,
}
MESES_ES = {
    "ene": 1, "feb": 2, "mar": 3, "abr": 4, "may": 5, "jun": 6,
    "jul": 7, "ago": 8, "sep": 9, "oct": 10, "nov": 11, "dic": 12,
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4,
    "junio": 6, "julio": 7, "agosto": 8, "septiembre": 9,
    "octubre": 10, "noviembre": 11, "diciembre": 12,
}

# --- Config ---
TIMEOUT_PAGINA = 25000  # ms
MAX_URLS_POR_RUN = 30
PAUSA_ENTRE_URLS = (3.0, 7.0)
TOR_ROTATE_CADA = 5  # rotar circuito cada N páginas


def _obtener_proxy_tor() -> Optional[str]:
    """Devuelve URL de proxy SOCKS5 de Tor."""
    if _TOR_POOL:
        idx = _pool_siguiente()
        socks_port = IDENTIDADES_TOR[idx][0]
        return f"socks5://127.0.0.1:{socks_port}"
    return "socks5://127.0.0.1:9050"


def _normalizar_fecha(texto: str) -> Optional[str]:
    """Intenta parsear fecha de texto libre a YYYY-MM-DD."""
    if not texto:
        return None
    texto = texto.strip()
    # ISO directo
    m = re.search(r"(20\d{2})-(\d{1,2})-(\d{1,2})", texto)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if 1 <= mo <= 12 and 1 <= d <= 31:
            return f"{y}-{mo:02d}-{d:02d}"
    # "Aug 15, 2026" / "15 Aug 2026" / "August 15, 2026"
    todos_meses = {**MESES_EN, **MESES_ES}
    for mes_name, mes_num in todos_meses.items():
        # "Aug 15, 2026"
        pat = re.compile(
            rf"\b{mes_name}\b\s+(\d{{1,2}}),?\s*(20\d{{2}})", re.IGNORECASE
        )
        m = pat.search(texto)
        if m:
            d, y = int(m.group(1)), int(m.group(2))
            if 1 <= d <= 31:
                return f"{y}-{mes_num:02d}-{d:02d}"
        # "15 Aug 2026"
        pat2 = re.compile(
            rf"\b(\d{{1,2}})\s+{mes_name}\b\s*(20\d{{2}})?", re.IGNORECASE
        )
        m2 = pat2.search(texto)
        if m2:
            d = int(m2.group(1))
            y = int(m2.group(2)) if m2.group(2) else 2026
            if 1 <= d <= 31:
                return f"{y}-{mes_num:02d}-{d:02d}"
    # "Friday, August 15 at 10:00 PM" → extraer solo la parte de fecha
    pat3 = re.compile(
        r"(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)"
        r",?\s+(\w+)\s+(\d{1,2})(?:\s+at\s+[\d:]+(?:\s*[ap]m)?)?",
        re.IGNORECASE,
    )
    m3 = pat3.search(texto)
    if m3:
        mes_str = m3.group(1).lower()
        if mes_str in todos_meses:
            d = int(m3.group(2))
            if 1 <= d <= 31:
                return f"2026-{todos_meses[mes_str]:02d}-{d:02d}"
    return None


def _extraer_organizador_fb(texto: str) -> str:
    """Extrae organizador de texto de Facebook."""
    patrones = [
        r"(?:organized by|hosted by|presented by|organized por|organizado por|presenta|organiza)[:\s]+(.+?)(?:\s*[·•\-\n]|$)",
        r"(?:Organizer|Organizador)[:\s]+(.+?)(?:\s*[·•\-\n]|$)",
    ]
    for pat in patrones:
        m = re.search(pat, texto, re.IGNORECASE)
        if m:
            org = m.group(1).strip().strip(" ,.")
            if org and 3 <= len(org) <= 100:
                return org
    return "N/A"


def _scrapear_url_fb(page, url: str) -> Optional[Dict]:
    """Visita una URL de Facebook y extrae datos del evento."""
    try:
        page.goto(url, timeout=TIMEOUT_PAGINA, wait_until="domcontentloaded")
        # Espera a que cargue el contenido principal
        time.sleep(2.0)

        # Extraer texto visible
        try:
            body_text = page.inner_text("body", timeout=5000)
        except Exception:
            body_text = ""

        # Extraer título (h1)
        titulo = ""
        try:
            h1 = page.query_selector("h1")
            if h1:
                titulo = h1.inner_text().strip()
        except Exception:
            pass

        if not titulo:
            # Fallback: primer título grande
            try:
                titulo = page.title().strip()
                # Limpiar " | Facebook" del final
                titulo = re.sub(r"\s*[|\-–]\s*Facebook\s*$", "", titulo)
            except Exception:
                return None

        if not titulo or len(titulo) < 5:
            return None

        # Filtrar páginas que no son eventos
        skip_patterns = [
            "log in", "sign up", "create new account",
            "content not found", "page not available",
            "no hay nada", "error",
        ]
        if any(p in titulo.lower() for p in skip_patterns):
            return None

        # Extraer fecha
        fecha = None
        # Buscar en elementos de fecha comunes de FB
        for sel in [
            '[data-testid="event_date_time"]',
            '[data-testid="event-permalink-details"]',
            "._43-1",  # Clase FB clásica para fecha
        ]:
            try:
                el = page.query_selector(sel)
                if el:
                    fecha = _normalizar_fecha(el.inner_text())
                    if fecha:
                        break
            except Exception:
                pass

        # Fallback: buscar fecha en body_text
        if not fecha:
            fecha = _normalizar_fecha(body_text[:2000])

        # Extraer lugar
        lugar = "N/A"
        for sel in [
            '[data-testid="event_location_link"]',
            '[data-testid="event venue"]',
            "._43-3",  # Clase FB para ubicación
        ]:
            try:
                el = page.query_selector(sel)
                if el:
                    lugar = el.inner_text().strip()
                    if lugar and lugar != "N/A":
                        break
            except Exception:
                pass

        if lugar == "N/A":
            # Buscar en body_text patrones de ubicación
            loc_patterns = [
                r"(?:Location|Venue|Lugar|Ubicación|Dirección)[:\s]+(.+?)(?:\n|$)",
                r"(?:at|en)\s+([A-Z][A-Za-z\s]+(?:Hall|Center|Club|Bar|Venue|Space|Room))",
            ]
            for pat in loc_patterns:
                m = re.search(pat, body_text[:3000])
                if m:
                    lugar = m.group(1).strip()[:200]
                    break

        # Extraer organizador
        organizador = _extraer_organizador_fb(body_text[:3000])

        # Extraer descripción
        descripcion = ""
        try:
            desc_el = page.query_selector('[data-testid="event_description"]')
            if desc_el:
                descripcion = desc_el.inner_text().strip()[:500]
        except Exception:
            pass

        if not descripcion:
            # Primeros 500 chars del body como descripción
            descripcion = body_text[:500].strip()

        return {
            "nombre": titulo[:300],
            "fecha": fecha or "N/A",
            "lugar": lugar[:200],
            "pais": "N/A",  # Se completa después con geocode
            "continente": "N/A",
            "subcontinente": "N/A",
            "fuente": "Facebook (directo)",
            "organizador": organizador[:200],
            "email": "N/A",
            "link": url,
            "subgenero": "general",  # Se clasifica después
            "tipo_lugar": "N/A",
            "descripcion": descripcion[:500],
        }
    except PWTimeout:
        return None
    except Exception as e:
        return None


def scrape_fb_playwright_tor(
    urls: Optional[List[str]] = None,
    max_urls: int = MAX_URLS_POR_RUN,
) -> List[Dict]:
    """
    Scraping directo de páginas de eventos FB vía Playwright + Tor SOCKS5.

    Args:
        urls: Lista de URLs a visitar. Si es None, busca en caché de dorks.
        max_urls: Máximo de URLs a visitar.

    Returns:
        Lista de eventos normalizados.
    """
    if not PW_DISPONIBLE:
        print("  ⚠️ FB Playwright Tor: playwright no instalado")
        return []

    # Cargar URLs de dorks si no se proporcionan
    if urls is None:
        urls = _cargar_urls_dorks()

    if not urls:
        print("  ⚠️ FB Playwright Tor: sin URLs para visitar")
        return []

    urls = urls[:max_urls]

    # Cargar URLs ya procesadas (dedup)
    vistas = _cargar_urls_vistas()

    # Filtrar URLs nuevas
    urls_nuevas = [u for u in urls if u not in vistas]
    if not urls_nuevas:
        print("  ⚠️ FB Playwright Tor: todas las URLs ya procesadas")
        return []

    print(f"  🌐 FB Playwright Tor: {len(urls_nuevas)} URLs nuevas "
          f"(de {len(urls)} total)")

    eventos: List[Dict] = []
    proxy = _obtener_proxy_tor()
    paginas_visitadas = 0

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            proxy={"server": proxy} if proxy else None,
        )
        context = browser.new_context(
            user_agent=random.choice(_UAS),
            viewport={"width": 1366, "height": 768},
            locale="en-US",
        )

        # Aplicar stealth si disponible
        if STEALTH_DISPONIBLE:
            try:
                page = context.new_page()
                stealth_sync(page)
            except Exception:
                page = context.new_page()
        else:
            page = context.new_page()

        for url in urls_nuevas:
            if paginas_visitadas >= max_urls:
                break

            # Rotar circuito Tor cada N páginas
            if paginas_visitadas > 0 and paginas_visitadas % TOR_ROTATE_CADA == 0:
                _rotar_tor_silencioso()

            evento = _scrapear_url_fb(page, url)
            if evento:
                eventos.append(evento)
                vistas.add(url)
                print(f"    ✅ [{paginas_visitadas+1}] {evento['nombre'][:50]}")
            else:
                print(f"    ⚠️ [{paginas_visitadas+1}] {url[:60]}... (sin datos)")

            paginas_visitadas += 1

            # Pausa humana
            time.sleep(random.uniform(*PAUSA_ENTRE_URLS))

        browser.close()

    # Guardar URLs vistas
    _guardar_urls_vistas(vistas)

    print(f"  📊 FB Playwright Tor: {len(eventos)} eventos de "
          f"{paginas_visitadas} páginas visitadas")

    return eventos


def _cargar_urls_dorks() -> List[str]:
    """Carga URLs de FB de la caché de dorks."""
    dorks_file = Path(_PROJECT_ROOT) / "facebook_dorks_eventos.json"
    if not dorks_file.exists():
        return []
    try:
        with open(dorks_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        eventos = data.get("eventos", [])
        urls = []
        for ev in eventos:
            url = ev.get("link") or ev.get("url") or ""
            if "facebook.com" in url and url not in urls:
                urls.append(url)
        return urls
    except Exception:
        return []


def _cargar_urls_vistas() -> Set[str]:
    """Carga URLs ya procesadas."""
    vistas_file = Path(_PROJECT_ROOT) / "fb_playwright_vistas.json"
    if not vistas_file.exists():
        return set()
    try:
        with open(vistas_file, "r", encoding="utf-8") as f:
            return set(json.load(f))
    except Exception:
        return set()


def _guardar_urls_vistas(vistas: Set[str]) -> None:
    """Guarda URLs procesadas."""
    vistas_file = Path(_PROJECT_ROOT) / "fb_playwright_vistas.json"
    try:
        # Mantener solo las últimas 5000
        vista_list = sorted(vistas)[-5000:]
        tmp = str(vistas_file) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(vista_list, f)
        os.replace(tmp, str(vistas_file))
    except Exception:
        pass


def _rotar_tor_silencioso() -> None:
    """Rota el circuito Tor sin output."""
    if _TOR_POOL:
        try:
            idx = _pool_siguiente()
            _pool_rotar(idx)
        except Exception:
            pass


if __name__ == "__main__":
    eventos = scrape_fb_playwright_tor(max_urls=5)
    print(f"\nEventos extraídos: {len(eventos)}")
    for ev in eventos[:5]:
        print(f"  {ev['nombre'][:50]} | {ev['fecha']} | {ev['lugar'][:30]}")
