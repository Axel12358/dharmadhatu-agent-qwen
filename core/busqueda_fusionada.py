#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Búsqueda fusionada multi-motor a través de Tor.

Regla innegociable: todo el scraping usa Tor (socks5h://127.0.0.1:<puerto>).
IP real jamás. No se usa Chromium.

Motores (en orden de prioridad definido en fusionar_resultados):
  1. DuckDuckGo  (HTML, selectores .result__a / .result__snippet)
  2. Bing        (li.b_algo, h2 a, p)
  3. Startpage   (.w-gl__result, a, .w-gl__description)
  4. Mojeek      (a.ob, p.s)
  5. SearxNG     (JSON, lista de instancias públicas)
  6. Google fallback (requests por Tor)
"""
from __future__ import annotations

import random
import time
from typing import Any, Dict, List, Optional
from urllib.parse import quote_plus, unquote

import requests

try:
    from core.tor_pool import (
        IDENTIDADES_TOR,
        get_tor_port as _get_tor_port,
        obtener_sesion_tor as _obtener_sesion_tor,
    )
except Exception:
    from core.google_stealth import get_tor_port as _get_tor_port  # type: ignore
    _obtener_sesion_tor = None
    IDENTIDADES_TOR = []

try:
    from core.google_stealth import buscar_en_requests_fallback
except Exception:
    buscar_en_requests_fallback = None  # type: ignore

_PAUSA_ENTRE_MOTORES = (1.0, 2.5)
_TIMEOUT_DEFAULT = 20
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

_INSTANCIAS_SEARXNG = [
    "https://searx.be",
    "https://paulgo.io",
    "https://search.rhscz.eu",
    "https://priv.au",
    "https://searx.work",
    "https://baresearch.org",
    "https://search.inetol.net",
]


def _sesion_tor(indice_tor: Optional[int] = None) -> Optional[requests.Session]:
    """Devuelve una requests.Session con proxy Tor del pool (socks5h).

    Si obtener_sesion_tor() falla, construye una sesión manual con el puerto
    del pool. Nunca usa la IP real.
    """
    if _obtener_sesion_tor is not None:
        try:
            s = _obtener_sesion_tor(indice=indice_tor)
            if s is not None:
                s.headers.update(_HEADERS)
                return s
        except Exception:
            pass
    try:
        socks_port = _get_tor_port(indice_tor)
        s = requests.Session()
        proxy_url = f"socks5h://127.0.0.1:{socks_port}"
        s.proxies.update({"http": proxy_url, "https": proxy_url})
        s.headers.update(_HEADERS)
        return s
    except Exception:
        return None


def _get(url: str, session: requests.Session, timeout: int) -> Optional[str]:
    """GET protegido con detección de bloqueo. Devuelve html o None."""
    try:
        r = session.get(url, timeout=timeout)
        if r.status_code == 200:
            low = r.text[:3000].lower()
            if ("unusual traffic" in low or "captcha" in low
                    or "recaptcha" in low or "verify you are human" in low
                    or "/sorry/" in low or "checking your browser" in low
                    or "bot" in low and "not a bot" not in low
                    and "detected" in low):
                return None
            return r.text
        return None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# DuckDuckGo
# ---------------------------------------------------------------------------
def buscar_en_duckduckgo(dork: str, timeout: int = _TIMEOUT_DEFAULT) -> List[Dict]:
    """DuckDuckGo HTML sobre Tor. Selectores reales: .result__a / .result__snippet."""
    from bs4 import BeautifulSoup

    session = _sesion_tor()
    if session is None:
        return []
    url = f"https://html.duckduckgo.com/html/?q={quote_plus(dork)}"
    html = _get(url, session, timeout)
    if not html:
        return []
    soup = BeautifulSoup(html, "lxml")
    resultados: List[Dict] = []
    for a in soup.select("a.result__a"):
        href = a.get("href", "")
        titulo = a.get_text(strip=True)
        destino = ""
        if href:
            # DDG redirige vía /l/?uddg=<url_encoded>&rut=...
            if "duckduckgo.com/l" in href:
                try:
                    from urllib.parse import urlparse as _up, parse_qs as _qs
                    full = href if href.startswith("http") else ("https:" + href)
                    params = _qs(_up(full).query)
                    if params.get("uddg"):
                        destino = unquote(params["uddg"][0])
                    elif params.get("url"):
                        destino = unquote(params["url"][0])
                except Exception:
                    destino = ""
            elif href.startswith("http"):
                destino = unquote(href)
            else:
                destino = unquote(href)
        if not destino or not destino.startswith("http"):
            continue
        snippet_el = a.find_parent("div", class_="result")
        snippet = ""
        if snippet_el:
            snip = snippet_el.select_one(".result__snippet")
            if snip:
                snippet = snip.get_text(strip=True)
        resultados.append({
            "url": destino.split("?")[0].rstrip("/"),
            "titulo": titulo,
            "snippet": snippet,
            "motor": "duckduckgo",
        })
    return resultados


# ---------------------------------------------------------------------------
# Bing
# ---------------------------------------------------------------------------
def buscar_en_bing(dork: str, timeout: int = _TIMEOUT_DEFAULT) -> List[Dict]:
    """Bing sobre Tor. Selectores reales: li.b_algo, h2 a, p."""
    from bs4 import BeautifulSoup

    session = _sesion_tor()
    if session is None:
        return []
    url = f"https://www.bing.com/search?q={quote_plus(dork)}&count=10&setlang=en-US"
    html = _get(url, session, timeout)
    if not html:
        return []
    soup = BeautifulSoup(html, "lxml")
    resultados: List[Dict] = []
    for li in soup.select("li.b_algo"):
        a = li.select_one("h2 a")
        if not a:
            a = li.find("a", href=True)
        if not a:
            continue
        href = a.get("href", "")
        titulo = a.get_text(strip=True)
        # Bing redirige vía /ck/a?...&ru=<url_encoded>
        destino = ""
        if href:
            if "bing.com/ck/a" in href:
                try:
                    from urllib.parse import urlparse as _up, parse_qs as _qs
                    params = _qs(_up(href).query)
                    if params.get("ru"):
                        destino = unquote(params["ru"][0])
                except Exception:
                    destino = ""
            elif href.startswith("http"):
                destino = unquote(href)
            else:
                destino = unquote(href)
        if not destino or not destino.startswith("http"):
            continue
        p = li.select_one("p")
        snippet = p.get_text(strip=True) if p else ""
        resultados.append({
            "url": destino.split("?")[0].rstrip("/"),
            "titulo": titulo,
            "snippet": snippet,
            "motor": "bing",
        })
    return resultados


# ---------------------------------------------------------------------------
# Startpage
# ---------------------------------------------------------------------------
def buscar_en_startpage(dork: str, timeout: int = _TIMEOUT_DEFAULT) -> List[Dict]:
    """Startpage sobre Tor. Selectores reales: .w-gl__result, a, .w-gl__description."""
    from bs4 import BeautifulSoup

    session = _sesion_tor()
    if session is None:
        return []
    url = f"https://www.startpage.com/sp/search?query={quote_plus(dork)}"
    html = _get(url, session, timeout)
    if not html:
        return []
    soup = BeautifulSoup(html, "lxml")
    resultados: List[Dict] = []
    for item in soup.select(".w-gl__result"):
        a = item.select_one("a")
        if not a or not a.get("href"):
            continue
        href = a["href"]
        titulo = item.select_one(".w-gl__description")
        titulo_txt = titulo.get_text(strip=True) if titulo else a.get_text(strip=True)
        snippet_el = item.select_one(".w-gl__description")
        snippet = snippet_el.get_text(strip=True) if snippet_el else ""
        resultados.append({
            "url": unquote(href).split("?")[0].rstrip("/"),
            "titulo": titulo_txt,
            "snippet": snippet,
            "motor": "startpage",
        })
    return resultados


# ---------------------------------------------------------------------------
# Mojeek
# ---------------------------------------------------------------------------
def buscar_en_mojeek(dork: str, timeout: int = _TIMEOUT_DEFAULT) -> List[Dict]:
    """Mojeek sobre Tor. Selectores reales: a.ob, p.s."""
    from bs4 import BeautifulSoup

    session = _sesion_tor()
    if session is None:
        return []
    url = f"https://www.mojeek.com/search?q={quote_plus(dork)}&hl=en"
    html = _get(url, session, timeout)
    if not html:
        return []
    soup = BeautifulSoup(html, "lxml")
    resultados: List[Dict] = []
    for a in soup.select("a.ob"):
        href = a.get("href", "")
        if not href or not href.startswith("http"):
            continue
        titulo = a.get_text(strip=True)
        # snippet: buscar p.s dentro del mismo bloque de resultado
        bloque = a.find_parent("div", class_="results")
        snippet = ""
        if bloque:
            ps = bloque.select_one("p.s")
            snippet = ps.get_text(strip=True) if ps else ""
        resultados.append({
            "url": unquote(href).split("?")[0].rstrip("/"),
            "titulo": titulo,
            "snippet": snippet,
            "motor": "mojeek",
        })
    return resultados


# ---------------------------------------------------------------------------
# SearxNG (varias instancias públicas)
# ---------------------------------------------------------------------------
def buscar_en_searxng(dork: str, timeout: int = _TIMEOUT_DEFAULT) -> List[Dict]:
    """SearxNG pública sobre Tor (varias instancias). JSON `results`.

    Devuelve lista de dicts {url, titulo, snippet, motor}. No devuelve HTML.
    """
    session = _sesion_tor()
    if session is None:
        return []

    resultados: List[Dict] = []
    for instancia in _INSTANCIAS_SEARXNG:
        url = f"{instancia}/search?q={quote_plus(dork)}&format=json&language=en-US"
        try:
            r = session.get(url, timeout=timeout)
        except Exception:
            continue
        if r.status_code != 200:
            continue
        ct = r.headers.get("Content-Type", "")
        if "json" not in ct:
            continue
        try:
            data = r.json()
        except Exception:
            continue
        items = data.get("results", []) if isinstance(data, dict) else []
        for item in items:
            url_item = (item.get("url", "") or "").strip()
            titulo = (item.get("title", "") or "").strip()
            if url_item and titulo:
                resultados.append({
                    "url": url_item.split("?")[0].rstrip("/"),
                    "titulo": titulo,
                    "snippet": item.get("content", "") or "",
                    "motor": "searxng",
                })
        if resultados:
            return resultados
    return resultados


# ---------------------------------------------------------------------------
# Google fallback (requests por Tor)
# ---------------------------------------------------------------------------
def buscar_en_google_fallback(
    dork: str,
    indice_tor: Optional[int] = None,
    timeout: int = _TIMEOUT_DEFAULT,
) -> List[Dict]:
    """Google fallback usando requests por Tor. Usa google_stealth si existe."""
    if buscar_en_requests_fallback is not None:
        try:
            return buscar_en_requests_fallback(dork, "fusion", indice_tor, timeout)
        except Exception:
            pass
    session = _sesion_tor(indice_tor)
    if session is None:
        return []
    from bs4 import BeautifulSoup
    url = (
        f"https://www.google.com/search?q={quote_plus(dork)}"
        f"&hl=en&num=10&gbv=1&filter=0&pws=0&tbs=qdr:w"
    )
    html = _get(url, session, timeout)
    if not html:
        return []
    soup = BeautifulSoup(html, "lxml")
    resultados: List[Dict] = []
    for div in soup.select("div.g"):
        a = div.find("a", href=True)
        if not a:
            continue
        href = a["href"]
        if href.startswith("/url?q="):
            m = __import__("re").search(r"/url\?q=([^&]+)", href)
            if m:
                href = unquote(m.group(1))
        if not href.startswith("http"):
            continue
        h3 = div.find("h3")
        titulo = h3.get_text(strip=True) if h3 else ""
        if not titulo:
            continue
        snippet_el = div.find("span", class_="st") or div.find("div", attrs={"data-sncf": True})
        snippet = snippet_el.get_text(strip=True) if snippet_el else ""
        resultados.append({
            "url": href.split("?")[0].rstrip("/"),
            "titulo": titulo,
            "snippet": snippet,
            "motor": "google",
        })
    return resultados


# ---------------------------------------------------------------------------
# Fusión multi-motor
# ---------------------------------------------------------------------------
def fusionar_resultados(
    dork: str,
    indice_tor: Optional[int] = None,
    timeout: int = _TIMEOUT_DEFAULT,
) -> List[Dict]:
    """Ejecuta todos los motores en orden y fusiona resultados.

    Prioridad: DuckDuckGo → Bing → Startpage → Mojeek → SearxNG → Google fallback.
    Elimina duplicados por URL. Devuelve lista de dicts
    {url, titulo, snippet, motor}.
    """
    motores = [
        ("duckduckgo", lambda: buscar_en_duckduckgo(dork, timeout)),
        ("bing", lambda: buscar_en_bing(dork, timeout)),
        ("startpage", lambda: buscar_en_startpage(dork, timeout)),
        ("mojeek", lambda: buscar_en_mojeek(dork, timeout)),
        ("searxng", lambda: buscar_en_searxng(dork, timeout)),
        ("google", lambda: buscar_en_google_fallback(dork, indice_tor, timeout)),
    ]

    fusionado: List[Dict] = []
    urls_vistas = set()

    for nombre, fn in motores:
        try:
            resultados = fn()
        except Exception as e:
            print(f"    ⚠️ Motor {nombre} error: {e}")
            resultados = []

        if not isinstance(resultados, list):
            resultados = []

        for r in resultados:
            if not isinstance(r, dict):
                continue
            url = (r.get("url", "") or "").strip()
            if not url or url in urls_vistas:
                continue
            urls_vistas.add(url)
            fusionado.append({
                "url": url,
                "titulo": r.get("titulo", "") or r.get("title", ""),
                "snippet": r.get("snippet", "") or r.get("content", ""),
                "motor": r.get("motor") or nombre,
            })

        # Pausa breve entre motores (behaviour humano sobre Tor)
        time.sleep(random.uniform(*_PAUSA_ENTRE_MOTORES))

    return fusionado
