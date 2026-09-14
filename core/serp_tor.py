#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SERP Tor — Motor de búsqueda centralizado con rotación de circuito.

Unifica Google, Bing, Startpage, DDG y SearxNG a través de Tor
con rotación automática de circuitos y delays adaptativos.

Uso:
    from core.serp_tor import buscar_serp
    resultados = buscar_serp("psytrance festival 2026 site:facebook.com")
"""
from __future__ import annotations

import base64
import random
import re
import time
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote_plus, unquote

import requests

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

# --- Config ---
_TIMEOUT_DEFAULT = 15
_PAUSA_ENTRE_MOTORES = (1.0, 3.0)
_PAUSA_ENTRE_QUERIES = (2.0, 5.0)
_MAX_RESULTADOS = 20

# --- User agents ---
_UAS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86; rv:133.0) Gecko/20100101 Firefox/133.0",
]


# ---------------------------------------------------------------------------
# Sesión Tor
# ---------------------------------------------------------------------------
def _get_sesion() -> requests.Session:
    """Obtiene sesión con proxy Tor."""
    if _TOR_POOL:
        try:
            return _pool_sesion()
        except Exception:
            pass
    s = requests.Session()
    s.proxies = {
        "http": "socks5h://127.0.0.1:9050",
        "https": "socks5h://127.0.0.1:9050",
    }
    s.headers.update({"User-Agent": random.choice(_UAS)})
    return s


def _rotar_circuito() -> None:
    """Rota el circuito Tor."""
    if _TOR_POOL:
        try:
            idx = _pool_siguiente()
            _pool_rotar(idx)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Parsers por motor
# ---------------------------------------------------------------------------
def _parse_ddg(html: str) -> List[Dict]:
    """Parsea resultados de DuckDuckGo Lite."""
    resultados = []
    # URLs en duckduckgo.com/l/?uddg=
    links = re.findall(
        r'class="result__a"[^>]*href="([^"]*)"', html
    )
    snippets = re.findall(
        r'class="result__snippet"[^>]*>(.*?)</a>', html, re.DOTALL
    )
    for i, href in enumerate(links):
        m = re.search(r"uddg=([^&]+)", href)
        url = unquote(m.group(1)) if m else href
        if not url.startswith("http"):
            continue
        snippet = ""
        if i < len(snippets):
            snippet = re.sub(r"<[^>]+>", "", snippets[i]).strip()[:300]
        resultados.append({"url": url, "snippet": snippet, "motor": "ddg"})
    return resultados


def _parse_bing(html: str) -> List[Dict]:
    """Parsea resultados de Bing."""
    resultados = []
    # Bing usa li.b_algo > h2 > a
    links = re.findall(
        r'<li class="b_algo"[^>]*>.*?<h2><a href="([^"]*)"[^>]*>(.*?)</a>',
        html, re.DOTALL
    )
    snippets = re.findall(
        r'<li class="b_algo"[^>]*>.*?<p[^>]*>(.*?)</p>',
        html, re.DOTALL
    )
    for i, (url, titulo) in enumerate(links):
        if not url.startswith("http"):
            continue
        titulo = re.sub(r"<[^>]+>", "", titulo).strip()
        snippet = ""
        if i < len(snippets):
            snippet = re.sub(r"<[^>]+>", "", snippets[i]).strip()[:300]
        resultados.append({
            "url": url, "titulo": titulo,
            "snippet": snippet, "motor": "bing",
        })
    return resultados


def _parse_startpage(html: str) -> List[Dict]:
    """Parsea resultados de Startpage."""
    resultados = []
    # Startpage usa .w-gl__result > a
    links = re.findall(
        r'<a[^>]*class="w-gl__result-title[^"]*"[^>]*href="([^"]*)"[^>]*>(.*?)</a>',
        html, re.DOTALL
    )
    snippets = re.findall(
        r'<p class="w-gl__description[^"]*"[^>]*>(.*?)</p>',
        html, re.DOTALL
    )
    for i, (url, titulo) in enumerate(links):
        if not url.startswith("http"):
            continue
        titulo = re.sub(r"<[^>]+>", "", titulo).strip()
        snippet = ""
        if i < len(snippets):
            snippet = re.sub(r"<[^>]+>", "", snippets[i]).strip()[:300]
        resultados.append({
            "url": url, "titulo": titulo,
            "snippet": snippet, "motor": "startpage",
        })
    return resultados


def _parse_google(html: str) -> List[Dict]:
    """Parsea resultados de Google."""
    resultados = []
    # Google usa div.g > div > a
    bloques = re.findall(
        r'<div class="g"[^>]*>(.*?)</div>\s*</div>\s*</div>',
        html, re.DOTALL
    )
    for bloque in bloques[:_MAX_RESULTADOS]:
        m_url = re.search(r'<a href="/url\?q=([^&"]+)', bloque)
        if not m_url:
            continue
        url = unquote(m_url.group(1))
        if not url.startswith("http"):
            continue
        m_titulo = re.search(r'<h3[^>]*>(.*?)</h3>', bloque, re.DOTALL)
        titulo = re.sub(r"<[^>]+>", "", m_titulo.group(1)).strip() if m_titulo else ""
        m_snippet = re.search(
            r'<span class="st"[^>]*>(.*?)</span>', bloque, re.DOTALL
        )
        snippet = re.sub(r"<[^>]+>", "", m_snippet.group(1)).strip()[:300] if m_snippet else ""
        resultados.append({
            "url": url, "titulo": titulo,
            "snippet": snippet, "motor": "google",
        })
    return resultados


def _parse_generic(html: str, motor: str) -> List[Dict]:
    """Parser genérico: extrae todos los enlaces http de resultados."""
    resultados = []
    links = re.findall(r'href="(https?://[^"]+)"', html)
    vistos = set()
    for url in links:
        if url in vistos:
            continue
        vistos.add(url)
        resultados.append({
            "url": url, "titulo": "", "snippet": "", "motor": motor,
        })
        if len(resultados) >= _MAX_RESULTADOS:
            break
    return resultados


# ---------------------------------------------------------------------------
# Motores de búsqueda
# ---------------------------------------------------------------------------
def _buscar_ddg(query: str, sesion: requests.Session,
                timeout: int = _TIMEOUT_DEFAULT) -> List[Dict]:
    """DuckDuckGo Lite HTML."""
    url = "https://html.duckduckgo.com/html/"
    try:
        r = sesion.get(url, params={"q": query}, timeout=timeout)
        if r.status_code == 200:
            return _parse_ddg(r.text)
    except Exception:
        pass
    return []


def _buscar_bing(query: str, sesion: requests.Session,
                 timeout: int = _TIMEOUT_DEFAULT) -> List[Dict]:
    """Bing."""
    url = "https://www.bing.com/search"
    try:
        r = sesion.get(url, params={"q": query, "count": 20}, timeout=timeout)
        if r.status_code == 200:
            return _parse_bing(r.text)
    except Exception:
        pass
    return []


# --- Motores DIRECTOS (curl_cffi, sin Tor) ---
# Bing responde bien por curl_cffi con impersonate sin bloqueo; multiplica el
# throughput de dorks porque no depende de Tor. Parsea redirects ck/a con la URL
# real en base64 (u=a1<base64url>).
def _parse_bing_directo(html: str) -> List[Dict]:
    """Parsea resultados de Bing (HTML directo, redirects ck/a en base64)."""
    resultados = []
    patron = (
        r'<h2[^>]*>\s*<a[^>]+href="(https://www\.bing\.com/ck/a\?[^"]+)"'
        r'[^>]*>(.*?)</a>'
    )
    for m in re.finditer(patron, html, re.DOTALL):
        href, titulo = m.group(1), re.sub(r"<[^>]+>", "", m.group(2)).strip()
        qs = dict(re.findall(r"[?&]([^=&]+)=([^&]+)", href.replace("&amp;", "&")))
        u = qs.get("u", "")
        url = ""
        if u.startswith("a1"):
            b = u[2:]
            b += "=" * ((4 - len(b) % 4) % 4)
            try:
                url = base64.urlsafe_b64decode(b).decode("utf-8", "ignore")
            except Exception:
                url = ""
        if not url.startswith("http"):
            continue
        resultados.append({
            "url": url, "titulo": titulo,
            "snippet": "", "motor": "bing_directo",
        })
    return resultados[:_MAX_RESULTADOS]


def _buscar_bing_directo(query: str, sesion=None,
                         timeout: int = _TIMEOUT_DEFAULT) -> List[Dict]:
    """Bing vía curl_cffi (sin Tor), impersonando Chrome."""
    try:
        from curl_cffi import requests as _cffi
    except Exception:
        return []
    try:
        r = _cffi.get(
            "https://www.bing.com/search",
            params={"q": query, "count": 20},
            impersonate="chrome120",
            timeout=timeout,
        )
        if r.status_code == 200:
            low = r.text.lower()
            if any(x in low for x in ["unusual traffic", "captcha",
                                       "verify you are human"]):
                return []
            return _parse_bing_directo(r.text)
    except Exception:
        pass
    return []


def _buscar_bing_directo_pag(query: str, sesion=None,
                             timeout: int = _TIMEOUT_DEFAULT) -> List[Dict]:
    """Bing directo con paginación (first=1,11) para más cobertura."""
    try:
        from curl_cffi import requests as _cffi
    except Exception:
        return []
    vistos, out = set(), []
    for first in (1, 11):
        try:
            r = _cffi.get(
                "https://www.bing.com/search",
                params={"q": query, "count": 20, "first": first},
                impersonate="chrome120",
                timeout=timeout,
            )
            if r.status_code != 200:
                continue
            for x in _parse_bing_directo(r.text):
                if x["url"] in vistos:
                    continue
                vistos.add(x["url"])
                out.append(x)
        except Exception:
            continue
    return out[:_MAX_RESULTADOS]


def _buscar_startpage(query: str, sesion: requests.Session,
                      timeout: int = _TIMEOUT_DEFAULT) -> List[Dict]:
    """Startpage."""
    url = "https://www.startpage.com/do/search"
    try:
        r = sesion.get(url, params={"query": query}, timeout=timeout)
        if r.status_code == 200:
            return _parse_startpage(r.text)
    except Exception:
        pass
    return []


def _buscar_google(query: str, sesion: requests.Session,
                   timeout: int = _TIMEOUT_DEFAULT) -> List[Dict]:
    """Google (con cookies de consentimiento)."""
    url = "https://www.google.com/search"
    cookies = {"CONSENT": "YES+cb.20220301-11-p0.en+FX+700"}
    try:
        r = sesion.get(
            url, params={"q": query, "hl": "en", "num": 20},
            cookies=cookies, timeout=timeout,
        )
        if r.status_code == 200:
            # Verificar CAPTCHA
            low = r.text.lower()
            if any(x in low for x in ["unusual traffic", "captcha",
                                       "consent.google.com"]):
                return []  # Bloqueado
            return _parse_google(r.text)
        if r.status_code == 429:
            return []  # Rate limited
    except Exception:
        pass
    return []


def _buscar_searxng(query: str, sesion: requests.Session,
                    timeout: int = _TIMEOUT_DEFAULT) -> List[Dict]:
    """SearxNG pública (JSON API)."""
    instancias = [
        "https://searx.be/search",
        "https://paulgo.io/search",
        "https://search.rhscz.eu/search",
        "https://priv.au/search",
        "https://searx.work/search",
    ]
    for inst in instancias:
        try:
            r = sesion.get(
                inst,
                params={"q": query, "format": "json", "categories": "general"},
                timeout=timeout,
            )
            if r.status_code == 200:
                data = r.json()
                resultados = []
                for item in data.get("results", [])[:_MAX_RESULTADOS]:
                    resultados.append({
                        "url": item.get("url", ""),
                        "titulo": item.get("title", ""),
                        "snippet": item.get("content", "")[:300],
                        "motor": "searxng",
                    })
                if resultados:
                    return resultados
        except Exception:
            continue
    return []


# ---------------------------------------------------------------------------
# API pública
# ---------------------------------------------------------------------------
MOTORES = {
    "ddg": _buscar_ddg,
    "bing": _buscar_bing,
    "startpage": _buscar_startpage,
    "google": _buscar_google,
    "searxng": _buscar_searxng,
    "bing_directo": _buscar_bing_directo,
    "bing_directo_pag": _buscar_bing_directo_pag,
}

# Orden de prioridad: DDG (único que funciona vía Tor) → SearxNG (pública)
# Bing/Startpage dan basura o challenge vía Tor — desactivados por defecto
ORDEN_MOTORES = ["ddg", "searxng"]

# Motores directos (curl_cffi, sin Tor): Bing rinde sin bloqueo y es rápido.
ORDEN_MOTORES_DIRECTO = ["bing_directo_pag"]


def buscar_serp(
    query: str,
    motores: Optional[List[str]] = None,
    solo_facebook: bool = False,
    timeout: int = _TIMEOUT_DEFAULT,
    rotar_circuito: bool = False,
    directo: bool = False,
) -> List[Dict]:
    """
    Busca en múltiples motores.

    `directo=True` usa motores curl_cffi sin Tor (Bing), mucho más rápido y
    con mayor throughput; `directo=False` usa la sesión Tor (DDG/SearxNG).

    Usa la MISMA sesión para todos los motores (rápido).
    La rotación de circuito es entre queries, no entre motores.

    Args:
        query: Consulta de búsqueda
        motores: Lista de motores a usar (default: todos en orden)
        solo_facebook: Si True, filtra solo resultados facebook.com
        timeout: Timeout por motor
        rotar_circuito: Si True, rota circuito Tor al inicio

    Returns:
        Lista de {url, titulo, snippet, motor}
    """
    if motores is None:
        motores = ORDEN_MOTORES_DIRECTO if directo else ORDEN_MOTORES

    if rotar_circuito and not directo:
        _rotar_circuito()

    sesion = None if directo else _get_sesion()
    todos: List[Dict] = []
    urls_vistas: set = set()

    for motor in motores:
        fn = MOTORES.get(motor)
        if fn is None:
            continue

        try:
            resultados = fn(query, sesion, timeout)
        except Exception:
            resultados = []

        for r in resultados:
            url = r.get("url", "")
            if not url or url in urls_vistas:
                continue
            urls_vistas.add(url)
            todos.append(r)

        # Si encontramos resultados suficientes, no seguir
        if len(todos) >= _MAX_RESULTADOS:
            break

    if solo_facebook:
        todos = [r for r in todos if "facebook.com" in r.get("url", "").lower()]

    return todos[:_MAX_RESULTADOS]


def buscar_serp_fb(
    subgenero: str = "psytrance",
    localidad: str = "",
    anio: str = "2026",
    tipo: str = "festival",
    motores: Optional[List[str]] = None,
) -> List[Dict]:
    """
    Atajo para buscar eventos FB con un dork compuesto.

    Genera: site:facebook.com/events {subgenero} {tipo} {localidad} {anio}
    """
    partes = ["site:facebook.com/events", subgenero]
    if tipo:
        partes.append(tipo)
    if localidad:
        partes.append(localidad)
    if anio:
        partes.append(anio)
    query = " ".join(partes)
    return buscar_serp(query, motores=motores, solo_facebook=True)


if __name__ == "__main__":
    print("=== SERP Tor — Test ===")
    resultados = buscar_serp(
        "psytrance festival 2026 site:facebook.com",
        motores=["ddg", "bing"],
        solo_facebook=True,
    )
    print(f"Resultados: {len(resultados)}")
    for r in resultados[:5]:
        print(f"  [{r['motor']}] {r['url'][:80]}")
        if r.get("titulo"):
            print(f"    {r['titulo'][:60]}")
