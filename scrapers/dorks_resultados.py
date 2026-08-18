#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Dorks Resultados v4.1 — Google SOLO a través de Playwright + Tor + stealth.
Sin APIs externas. Los demás motores (DDG, Mojeek, Bing, Startpage) están
desactivados del flujo automático; se conservan como funciones pero no se usan.

Estrategia:
  1. Google vía Playwright con proxy Tor (pool de múltiples circuitos).
     stealth activado si playwright-stealth está disponible.
  2. Si Google bloquea (CAPTCHA/403) → rotar identidad Tor y reintentar
     el mismo dork (máx 3 intentos).
  3. Parseo: div.g (resultado), a[href^="http"] (URL), h3 (título),
     span.st / div[data-sncf] (snippet). Limpiar redirecciones /url?q=.

Límites: timeout global 150s, máx 10 dorks, rotación cada 3 circuitos,
pausas de 20-40s entre dorks, semáforo global (máx 1 petición Tor a la vez).
"""
from __future__ import annotations

import asyncio
import json
import os
import random
import re
import sys
import time
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from threading import Semaphore
from typing import Any, Dict, List, Optional, Set, Tuple

import requests
from bs4 import BeautifulSoup

_PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

# --- Tor integration (optional, fails silently) ---
try:
    from core.tor_manager import (
        obtener_sesion_tor,
        obtener_proxy_tor,
        tor_disponible as _tor_ok,
        renovar_identidad_tor,
        _reiniciar_proceso_tor as _tor_restart_subprocess,
    )
    from core.tor_utils import (
        rotar_tor as rotar_identidad_tor_actualizado,
        verificar_tor as verificar_tor_actualizado,
    )
    from core.tor_pool import (
        iniciar_pool as _iniciar_pool,
        rotar_tor as _pool_rotar_tor,
        verificar_tor as _pool_verificar_tor,
        obtener_sesion_tor as _pool_obtener_sesion,
        obtener_siguiente_identidad as _pool_siguiente_identidad,
    )
    # Iniciar pool de Tor al importar
    try:
        _iniciar_pool()
    except Exception:
        pass
except ImportError:
    obtener_sesion_tor = None
    obtener_proxy_tor = None
    _tor_ok = lambda: False
    renovar_identidad_tor = None
    _tor_restart_subprocess = None
    _iniciar_pool = None
    _pool_rotar_tor = None
    _pool_verificar_tor = None
    _pool_obtener_sesion = None
    _pool_siguiente_identidad = None

# Cookie de consentimiento de Google (evita redirección a consent.google.com)
GOOGLE_CONSENT_COOKIE = {"CONSENT": "YES+cb.20220301-11-p0.en+FX+700", "SOCS": "1234567890"}

# --- Playwright + Google Stealth (core module) ---
try:
    from core.google_stealth import (
        google_search_prioritario,
        get_tor_port,
        get_ua,
        _rotate_tor_and_cookies,
        load_loop_config,
    )
    STEALTH_AVAILABLE = True
except Exception:
    STEALTH_AVAILABLE = False

try:
    from playwright.async_api import async_playwright
    PLAYWRIGHT_AVAILABLE = True
except Exception:
    PLAYWRIGHT_AVAILABLE = False

# --- State directory ---
_STATE_DIR = Path(__file__).resolve().parent / "dorks_resultados"
_STATE_DIR.mkdir(parents=True, exist_ok=True)

ESTADO_FILE = _STATE_DIR / "dorks_rotacion_estado.json"
EVENTOS_FILE = _STATE_DIR / "eventos_dorks.json"
RENDIMIENTO_FILE = _STATE_DIR / "dorks_rendimiento.json"
DORKS_GENERADOS_FILE = _STATE_DIR / "dorks_generados.json"

GRUPOS_FB_FILE = Path(_PROJECT_ROOT) / "grupos_encontrados.json"
ORG_FB_FILE = Path(_PROJECT_ROOT) / "organizadores_facebook.json"

# --- Constants ---
TIMEOUT_TOTAL = 150        # 150s presupuesto global
TIMEOUT_ENGINE = 12        # 12s máximo por motor por dork
MAX_DORKS_PER_RUN = 10     # 10 dorks por ejecución
SLEEP_BETWEEN = (20.0, 40.0)
MAX_EVENTS_PER_RUN = 50
TOR_ROTATE_EVERY = 1       # Rotar Tor ANTES de cada dork
TOR_RESTART_PAUSE = 10     # 10s pausa tras reinicio de Tor
MAX_REINTENTOS_POR_DORK = 5  # Más reintentos
PLAYWRIGHT_WAIT = 10       # Segundos de espera entre interacciones

# Motor único: GOOGLE vía Playwright+Tor. Otros motores desactivados.
ENGINE_CASCADE = ["google"]
MAX_REINTENTOS_POR_DORK = 3   # Reintentos de Google tras rotar Tor
TOR_ROTATE_EVERY = 3          # Rotar circuito cada 3 dorks
SLEEP_BETWEEN = (20.0, 40.0)  # Pausa larga entre dorks

# --- Tor semaphore: máximo 1 petición Tor a la vez ---
TOR_SEMAPHORE = Semaphore(1)


def _verificar_tor() -> bool:
    """Verifica Tor usando tor_pool si disponible, o caída al manager original."""
    if _pool_verificar_tor is not None:
        try:
            return _pool_verificar_tor()
        except Exception:
            pass
    if verificar_tor_actualizado is not None:
        try:
            return verificar_tor_actualizado()
        except Exception:
            pass
    return bool(_tor_ok) and _tor_ok()


# Lazy session cache (SIEMPRE a través de Tor — nunca IP real)
_sesion_cache: Optional[requests.Session] = None
_busquedas_exitosas = 0
_bloqueo_detectado_en_dork = False

# ---------------------------------------------------------------------------
# Sesión Tor (requests)
# ---------------------------------------------------------------------------
def _get_sesion() -> requests.Session:
    """Devuelve una requests.Session con proxy Tor y headers rotados.

    Cachea la sesión para reusarla (el proxy Tor se mantiene).
    """
    global _sesion_cache
    if _sesion_cache is not None:
        return _sesion_cache
    if _pool_obtener_sesion is not None:
        try:
            _sesion_cache = _pool_obtener_sesion()
            return _sesion_cache
        except Exception:
            pass
    if obtener_sesion_tor is not None:
        try:
            _sesion_cache = obtener_sesion_tor()
            return _sesion_cache
        except Exception:
            pass
    # Fallback manual
    s = requests.Session()
    proxy = obtener_proxy_tor() if obtener_proxy_tor else None
    if proxy:
        s.proxies.update(proxy)
    s.headers.update({
        "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/125.0.0.0 Safari/537.36"),
        "Accept-Language": "en-US,en;q=0.9",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    })
    _sesion_cache = s
    return s


def _rotar_identidad_tor() -> bool:
    """Rota la IP de Tor (pool) y resetea la sesión cacheada."""
    global _sesion_cache, _busquedas_exitosas
    _sesion_cache = None
    _busquedas_exitosas = 0
    # Usar pool de Tor si está disponible
    if _pool_rotar_tor is not None and _pool_siguiente_identidad is not None:
        try:
            idx = _pool_siguiente_identidad()
            return _pool_rotar_tor(idx)
        except Exception:
            pass
    # Fallback a manager original
    if renovar_identidad_tor is None:
        return False
    result = False
    try:
        result = renovar_identidad_tor()
    except Exception:
        result = False
    if not result and _tor_restart_subprocess is not None:
        try:
            result = _tor_restart_subprocess()
        except Exception:
            result = False
    if result:
        time.sleep(TOR_RESTART_PAUSE)
    return result


def _random_sleep():
    """Pause entre dorks usando configuración dinámica si disponible."""
    if STEALTH_AVAILABLE:
        _cfg = load_loop_config()
        pausa = _cfg.get("pausa_entre_dorks", list(SLEEP_BETWEEN))
    else:
        pausa = list(SLEEP_BETWEEN)
    time.sleep(random.uniform(pausa[0], pausa[1]))


def _http_get(url: str, params: Optional[dict] = None,
              cookies: Optional[dict] = None,
              timeout: int = TIMEOUT_ENGINE) -> Tuple[Optional[str], bool]:
    """GET OBLIGATORIO a través de Tor (proxy SOCKS5 127.0.0.1:9050).

    Nunca se usa la IP real. Si el motor bloquea (403/429/503) se reporta
    ``bloqueado`` para activar rotación de identidad Tor. Devuelve
    (html|None, bloqueado_por_google)."""
    bloqueado = False
    s = _get_sesion()  # sesión con proxy Tor
    try:
        with TOR_SEMAPHORE:
            r = s.get(url, params=params, cookies=cookies, timeout=timeout)
        if r.status_code == 200:
            return r.text, False
        if r.status_code in (403, 429, 503):
            bloqueado = (r.status_code == 429)
    except Exception:
        pass
    return None, bloqueado


# ---------------------------------------------------------------------------
# Google (requests + Tor, con fallback directo si Tor bloquea)
# ---------------------------------------------------------------------------
def _google_search_requests(dork: str, timeout: int = TIMEOUT_ENGINE) -> Tuple[Optional[str], bool]:
    """Busca en Google vía requests+Tor.

    Returns (html, bloqueado). Detecta CAPTCHA / redirección de consentimiento.
    """
    url = "https://www.google.com/search"
    params = {"q": dork, "hl": "en", "num": 20}
    html, bloqueado = _http_get(url, params, GOOGLE_CONSENT_COOKIE, timeout)
    if html:
        low = html.lower()
        if ("unusual traffic" in low or "captcha" in low
                or "our systems have detected" in low
                or "consent.google.com" in low):
            return None, True
        return html, False
    return None, bloqueado


# ---------------------------------------------------------------------------
# Startpage (requests + Tor) — fallback de Google
# ---------------------------------------------------------------------------
def _startpage_search_requests(dork: str, timeout: int = TIMEOUT_ENGINE) -> Tuple[Optional[str], bool]:
    url = "https://www.startpage.com/sp/search"
    params = {"query": dork}
    html, _ = _http_get(url, params, GOOGLE_CONSENT_COOKIE, timeout)
    return html, False


# ---------------------------------------------------------------------------
# Mojeek (requests + Tor) — fallback
# ---------------------------------------------------------------------------
def _mojeek_search_requests(dork: str, timeout: int = TIMEOUT_ENGINE) -> Tuple[Optional[str], bool]:
    url = "https://www.mojeek.com/search"
    params = {"q": dork, "hl": "en"}
    html, _ = _http_get(url, params, GOOGLE_CONSENT_COOKIE, timeout)
    return html, False


# ---------------------------------------------------------------------------
# DuckDuckGo (requests + Tor) — fallback final requests
# ---------------------------------------------------------------------------
def _ddg_search_requests(dork: str, timeout: int = TIMEOUT_ENGINE) -> Tuple[Optional[str], bool]:
    url = "https://html.duckduckgo.com/html/"
    params = {"q": dork}
    html, _ = _http_get(url, params, None, timeout)
    return html, False


# ---------------------------------------------------------------------------
# Bing (requests + Tor) — fallback antes de Playwright
# ---------------------------------------------------------------------------
def _bing_search_requests(dork: str, timeout: int = TIMEOUT_ENGINE) -> Tuple[Optional[str], bool]:
    url = "https://www.bing.com/search"
    params = {"q": dork, "hl": "en"}
    html, bloqueado = _http_get(url, params, None, timeout)
    if html:
        low = html.lower()
        if "captcha" in low or "blocked" in low or "denied" in low:
            return None, True
        return html, False
    return None, bloqueado


# ---------------------------------------------------------------------------
# Google vía Playwright (contexto persistente + stealth + gbv=1 + Tor pool)
# ---------------------------------------------------------------------------
def _google_search_playwright(dork: str, indice_tor: Optional[int] = None,
                              timeout: int = TIMEOUT_ENGINE) -> Optional[str]:
    """Google vía Playwright con humano real + stealth + gbv=1 + Tor pool.

    Usa core.google_stealth con typing caracter por caracter, mouse,
    cookies persistentes y detección de CAPTCHA con retry.
    Nunca usa IP real. Rotación Tor antes de cada intento.
    """
    if not STEALTH_AVAILABLE:
        return None
    try:
        return google_search_playwright_with_retry(
            dork, "dr", max_retries=3, indice_tor=indice_tor, timeout=timeout)
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"Google Playwright error: {e}")
        return None


# ---------------------------------------------------------------------------
# Parsers SERP
# ---------------------------------------------------------------------------
def _limpiar_url_google(href: str) -> str:
    """Limpia redirecciones /url?q= de Google."""
    if href.startswith("/url?q="):
        m = re.search(r"/url\?q=([^&]+)", href)
        if m:
            return urllib.parse.unquote(m.group(1))
    if href.startswith("/url?"):
        m = re.search(r"url=([^&]+)", href)
        if m:
            return urllib.parse.unquote(m.group(1))
    return href


def _parse_google_results(html: str) -> List[Dict]:
    """Parsea resultados orgánicos de Google.

    div.g → a[href^="http"], h3 (título), span.st / div[data-sncf] (snippet).
    """
    resultados = []
    if not html:
        return resultados
    soup = BeautifulSoup(html, "lxml")
    for g in soup.find_all("div", class_="g"):
        a = g.find("a", href=True)
        if not a:
            continue
        href = a["href"]
        href = _limpiar_url_google(href)
        if not href.startswith("http") or "google.com" in href.lower():
            continue
        h3 = g.find("h3")
        if not h3:
            continue
        titulo = h3.get_text(strip=True)
        snippet_el = (g.find("span", class_="st")
                      or g.find("div", attrs={"data-sncf": True})
                      or g.find("div", class_="VwiC3b"))
        snippet = snippet_el.get_text(strip=True) if snippet_el else ""
        resultados.append({
            "titulo": titulo,
            "url": href.split("?")[0].rstrip("/"),
            "snippet": snippet,
            "motor": "google",
        })
        if len(resultados) >= MAX_DORKS_PER_RUN:
            break
    return resultados


def _parse_bing_results(html: str) -> List[Dict]:
    """Parsea resultados de Bing: li.b_algo h2 a (URL) y p (snippet)."""
    resultados: List[Dict] = []
    try:
        soup = BeautifulSoup(html, "lxml")
    except Exception:
        try:
            soup = BeautifulSoup(html, "html.parser")
        except Exception:
            return resultados
    for a in soup.select("li.b_algo h2 a"):
        href = a.get("href", "")
        url = href if href.startswith("http") else href
        if not url.startswith("http"):
            continue
        if "facebook.com" not in url and "instagram.com" not in url:
            continue
        titulo = a.get_text(strip=True)
        snippet = ""
        padre = a.find_parent("li", class_="b_algo")
        if padre:
            snippet_el = padre.select_one("p")
            if snippet_el:
                snippet = snippet_el.get_text(strip=True)
        url_limpia = _limpiar_url(url, titulo, snippet)
        if url_limpia and url_limpia not in [r.get("url", "") for r in resultados]:
            resultados.append({
                "titulo": titulo[:200],
                "url": url_limpia,
                "snippet": snippet[:300],
                "motor": "bing",
            })
        if len(resultados) >= MAX_RESULTS_PER_DORK:
            break
    return resultados


def _parse_startpage_results(html: str) -> List[Dict]:
    """Parsea resultados de Startpage (estructura similar a Google)."""
    resultados = []
    if not html:
        return resultados
    soup = BeautifulSoup(html, "lxml")
    for g in soup.find_all("div", class_="result"):
        a = g.find("a", href=True)
        if not a:
            continue
        href = a["href"]
        if not href.startswith("http") or "startpage.com" in href.lower():
            continue
        titulo_el = g.find("h3") or g.find("a", class_="result-link")
        if not titulo_el:
            continue
        titulo = titulo_el.get_text(strip=True)
        snippet_el = (g.find("p", class_="result-snippet")
                      or g.find("span", class_="st")
                      or g.find("div", class_="result-description"))
        snippet = snippet_el.get_text(strip=True) if snippet_el else ""
        resultados.append({
            "titulo": titulo,
            "url": href.split("?")[0].rstrip("/"),
            "snippet": snippet,
            "motor": "startpage",
        })
        if len(resultados) >= MAX_DORKS_PER_RUN:
            break
    # Fallback genérico si no hubo .result
    if not resultados:
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if not href.startswith("http") or "startpage.com" in href.lower():
                continue
            titulo = a.get_text(strip=True)
            if not titulo or len(titulo) < 3:
                continue
            resultados.append({
                "titulo": titulo,
                "url": href.split("?")[0].rstrip("/"),
                "snippet": "",
                "motor": "startpage",
            })
            if len(resultados) >= MAX_DORKS_PER_RUN:
                break
    return resultados


def _parse_mojeek_results(html: str) -> List[Dict]:
    resultados = []
    if not html:
        return resultados
    soup = BeautifulSoup(html, "lxml")
    for a in soup.select("a.ob"):
        href = a.get("href", "")
        if not href.startswith("http"):
            continue
        titulo = a.get_text(strip=True)
        padre = a.find_parent("li")
        snippet = ""
        if padre:
            snippet_el = padre.select_one("p.s")
            if snippet_el:
                snippet = snippet_el.get_text(strip=True)
        resultados.append({
            "titulo": titulo,
            "url": href.split("?")[0].rstrip("/"),
            "snippet": snippet,
            "motor": "mojeek",
        })
        if len(resultados) >= MAX_DORKS_PER_RUN:
            break
    return resultados


def _parse_ddg_results(html: str) -> List[Dict]:
    resultados = []
    if not html:
        return resultados
    soup = BeautifulSoup(html, "lxml")
    for a in soup.select("a.result__a"):
        href = a.get("href", "")
        if "uddg=" in href:
            try:
                href = urllib.parse.unquote(
                    re.search(r"uddg=([^&]+)", href).group(1))
            except Exception:
                pass
        if not href.startswith("http"):
            continue
        titulo = a.get_text(strip=True)
        snippet = ""
        padre = a.find_parent("div", class_="result")
        if padre:
            snippet_el = padre.select_one(".result__snippet")
            if snippet_el:
                snippet = snippet_el.get_text(strip=True)
        resultados.append({
            "titulo": titulo,
            "url": href.split("?")[0].rstrip("/"),
            "snippet": snippet,
            "motor": "ddg",
        })
        if len(resultados) >= MAX_DORKS_PER_RUN:
            break
    return resultados


# ---------------------------------------------------------------------------
# Dispatcher: Google → Startpage → Mojeek → DDG (requests), Playwright last
# ---------------------------------------------------------------------------
def _buscar_google_con_reintentos(dork: str) -> Tuple[List[Dict], bool]:
    """Busca en Google usando intentos priorizados.

    Orden: Firefox + Playwright → SearxNG pública → requests por Tor.
    Rotación Tor + cookies. Pausas dinámicas.
    Máx 3 intentos por dork.
    Returns (resultados, bloqueado_por_último_intento).
    """
    global _bloqueo_detectado_en_dork

    # Load dynamic config
    _cfg = load_loop_config() if STEALTH_AVAILABLE else {}
    max_reintentos = _cfg.get("max_reintentos", MAX_REINTENTOS_POR_DORK)
    retry_pause = _cfg.get("pausa_entre_reintentos", [45, 90])

    # Rotate Tor + cookies BEFORE each dork
    if _verificar_tor():
        print(f"    🔄 Rotando Tor + cookies antes del dork...")
        if STEALTH_AVAILABLE:
            _rotate_tor_and_cookies()
        else:
            _rotar_identidad_tor()

    for intento in range(max_reintentos):
        # Usar búsqueda priorizada (Firefox → SearxNG → requests)
        html = google_search_prioritario(dork, "dr", indice_tor=None, timeout=20)
        if html:
            low = html.lower()
            if ("unusual traffic" in low or "captcha" in low
                    or "recaptcha" in low
                    or "our systems have detected" in low
                    or "consent.google.com" in low
                    or "/sorry/" in low):
                _bloqueo_detectado_en_dork = True
            res = _parse_google_results(html)
            if res:
                _bloqueo_detectado_en_dork = False
                return res, False
            _bloqueo_detectado_en_dork = True
        else:
            _bloqueo_detectado_en_dork = True

        if intento < max_reintentos - 1:
            wait = random.uniform(retry_pause[0], retry_pause[1])
            print(f"    ⏳ Pausa {wait:.0f}s entre reintentos...")
            time.sleep(wait)
            if _verificar_tor():
                print(f"    🔄 Rotando Tor + cookies antes de reintento {intento+2}...")
                if STEALTH_AVAILABLE:
                    _rotate_tor_and_cookies()
                else:
                    _rotar_identidad_tor()
                time.sleep(random.uniform(5, 10))
            else:
                break
    return [], True


def _buscar_cascada(dork: str) -> Tuple[List[Dict], Optional[str]]:
    """Ejecuta Google SOLO (Playwright + Tor). No usa otros motores.

    Retorna (resultados, motor_usado). Retorna ([], "google") si falla.
    """
    resultados, bloqueado = _buscar_google_con_reintentos(dork)
    if resultados:
        return resultados, "google"
    return [], "google"


# ---------------------------------------------------------------------------
# URL classification
# ---------------------------------------------------------------------------
FB_EVENT_RE = re.compile(r"facebook\.com/events/[\w.\-]+")
FB_GROUP_RE = re.compile(r"facebook\.com/groups/([\w.\-]+)")
IG_POST_RE = re.compile(r"instagram\.com/(p|reel|tv)/([\w.\-]+)")
IG_PROFILE_RE = re.compile(r"instagram\.com/([A-Za-z0-9_.]+)/?$")

NOISE_URL_PATTERNS = [
    "login", "signin", "signup", "register", "marketplace",
    "buy", "sell", "shop", "store", "ad", "sponsored",
    "watch", "video", "live", "streaming",
]

NOISE_TEXT_PATTERNS = [
    "iniciar sesión", "log in", "sign up", "crear cuenta",
    "privacy policy", "terms of service", "cookie",
    "advertisement", "publicidad", "patrocinado",
]


def _es_url_ruidosa(url: str) -> bool:
    return any(p in url.lower() for p in NOISE_URL_PATTERNS)


def _es_texto_ruidoso(texto: str) -> bool:
    return any(p in texto.lower() for p in NOISE_TEXT_PATTERNS)


def _clasificar_url(url: str) -> Optional[str]:
    if not url:
        return None
    if FB_EVENT_RE.search(url):
        return "evento_facebook"
    if FB_GROUP_RE.search(url):
        return "grupo_facebook"
    if IG_POST_RE.search(url):
        return "post_instagram"
    if IG_PROFILE_RE.search(url) and not IG_POST_RE.search(url):
        return "perfil_instagram"
    if "facebook.com/" in url.lower():
        if "/pages/" in url.lower() or "/profile.php" in url.lower():
            return "perfil_facebook"
        m = re.search(r"facebook\.com/([A-Za-z0-9_.]+)/?$", url)
        if m and m.group(1).lower() not in (
                "groups", "events", "share", "sharer", "login", "hashtag"):
            return "perfil_facebook"
    return None


def _es_relevante_evento(titulo: str, snippet: str) -> bool:
    texto = f"{titulo} {snippet}".lower()
    keywords = [
        "psytrance", "darkpsy", "forest", "hitech", "goa",
        "rave", "festival", "party", "event", "open air",
        "fullon", "psychill", "psybient", "psycore", "suomisaundi",
    ]
    return any(kw in texto for kw in keywords)


def _extraer_organizador_snippet(titulo: str, snippet: str) -> str:
    texto = f"{titulo} {snippet}".lower()
    patrones = [
        r"organizado por[:,\s]+(.+?)(?:\s*[,.\n]|$)",
        r"presenta[:,\s]+(.+?)(?:\s*[,.\n]|$)",
        r"hosted by[:,\s]+(.+?)(?:\s*[,.\n]|$)",
        r"presented by[:,\s]+(.+?)(?:\s*[,.\n]|$)",
        r"con la participación de[:,\s]+(.+?)(?:\s*[,.\n]|$)",
    ]
    for patron in patrones:
        m = re.search(patron, texto)
        if m:
            org = m.group(1).strip()
            org = re.sub(r"\s+", " ", org).strip(" ,.")
            if org and len(org) >= 3:
                return org
    return "N/A"


# ---------------------------------------------------------------------------
# Date extraction
# ---------------------------------------------------------------------------
DATE_PATTERNS = [
    re.compile(r"\b(20\d{2})-(\d{1,2})-(\d{1,2})\b"),
    re.compile(r"\b(\d{1,2})[/.\-](\d{1,2})[/.\-](20\d{2})\b"),
]

MONTHS_MAP = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6,
    "julio": 7, "agosto": 8, "septiembre": 9, "octubre": 10, "noviembre": 11,
    "diciembre": 12,
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11,
    "december": 12,
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6, "jul": 7,
    "aug": 8, "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12,
}


def _extraer_fecha_snippet(texto: str) -> Optional[str]:
    texto = re.sub(r"^\d+\s+likes?,?\s*\d+\s+comments?\s+[-:]\s*", "", texto,
                   count=1, flags=re.IGNORECASE)
    for pat in DATE_PATTERNS:
        m = pat.search(texto)
        if m:
            try:
                g = m.groups()
                if len(g) == 3:
                    y, mo, d = int(g[0]), int(g[1]), int(g[2])
                    if 1 <= mo <= 12 and 1 <= d <= 31:
                        return f"{y}-{mo:02d}-{d:02d}"
            except (ValueError, TypeError):
                continue
    return None


def _extraer_lugar_snippet(texto: str) -> Optional[str]:
    m = re.search(r"(?:en|at|in|@|📍)\s+([A-ZÀ-Ú][\w\sÀ-Ú]{2,40}?)(?:\s*[-–|,.\n]|\s*$)",
                  texto, re.IGNORECASE)
    if m:
        lugar = m.group(1).strip()[:100]
        falsos = ["the", "this", "that", "click", "see", "more", "join",
                  "follow", "like", "share", "comment", "post", "event",
                  "facebook", "instagram", "group", "page", "profile"]
        if lugar.lower().split()[0] not in falsos:
            return lugar
    return None


# ---------------------------------------------------------------------------
# Dork generation (autocontenido): 60% grupos, 40% eventos
# ---------------------------------------------------------------------------
_SUBGENEROS = ["psytrance", "darkpsy", "forest", "goa", "hitech",
              "psychill", "fullon", "twilight", "psycore", "suomisaundi"]
_CIUDADES = ["Berlin", "Barcelona", "Mexico", "Amsterdam", "Paris",
            "Lisbon", "Madrid", "London", "Rome", "Vienna"]


def _generar_dorks() -> List[str]:
    """Genera 10 dorks: 6 grupos (60%) + 4 eventos (40%), rotando sub/ciudades."""
    dorks: List[str] = []
    # 60% grupos (6)
    for i in range(6):
        sub = _SUBGENEROS[i % len(_SUBGENEROS)]
        ciudad = _CIUDADES[i % len(_CIUDADES)]
        dorks.append(f'site:facebook.com/groups "{sub}" "{ciudad}"')
    # 40% eventos (4)
    event_specs = [
        'site:facebook.com/events "psytrance" "Berlin" "2026"',
        'site:facebook.com/events "darkpsy" "rave" "2026"',
        'site:facebook.com/events "goa trance" "festival"',
        'site:facebook.com/events "forest psy" "comunidad" "2026"',
    ]
    dorks.extend(event_specs)
    return dorks[:MAX_DORKS_PER_RUN]


# ---------------------------------------------------------------------------
# Persistence helpers
# ---------------------------------------------------------------------------
def _leer_json(ruta: Path, default: Any = None) -> Any:
    if not ruta.exists():
        return default
    try:
        with open(ruta, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError, ValueError):
        return default


def _escribir_json(ruta: Path, datos: Any) -> None:
    try:
        tmp = str(ruta) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(datos, f, ensure_ascii=False, indent=2)
        os.replace(tmp, str(ruta))
    except (IOError, OSError):
        pass


def _extraer_fb_perfil_username(url: str) -> Optional[str]:
    m = re.search(r"facebook\.com/([A-Za-z0-9_.]+)/?$", url)
    if m:
        u = m.group(1)
        if u.lower() not in ("groups", "events", "pages", "people",
                              "hashtag", "share", "sharer", "login"):
            return u
    m2 = re.search(r"facebook\.com/pages/([A-Za-z0-9_.]+)", url)
    if m2:
        return m2.group(1)
    return None


# ---------------------------------------------------------------------------
# Main scraping function
# ---------------------------------------------------------------------------
def scrape_dorks_resultados(
    dorks: Optional[List[str]] = None,
    motores: Optional[List[str]] = None,
    timeout: int = TIMEOUT_TOTAL,
    limite: Optional[int] = None,
) -> List[Dict]:
    """Punto de entrada principal.

    Google SOLO a través de Playwright + Tor + stealth.
    Clasifica resultados (eventos FB, grupos FB, posts IG) y extrae organizador.
    """
    global _bloqueo_detectado_en_dork, _busquedas_exitosas

    # Load dynamic configuration
    _cfg = load_loop_config() if STEALTH_AVAILABLE else {}
    _timeout = _cfg.get("timeout_total", timeout)
    _max_dorks = _cfg.get("max_dorks", MAX_DORKS_PER_RUN)

    if dorks is None:
        dorks = _generar_dorks()
    _limite = limite if limite and limite > 0 else _max_dorks
    if dorks:
        dorks = dorks[:_limite]
    # motores param: Google-only. Ignorar motores que no sean "google".
    # (conservado por compatibilidad, pero solo se usa Google)
    if motores is not None and "google" not in motores:
        print("  ⚠️  Dorks: motor solicitado no es Google, usando Google.")

    if not dorks:
        return []

    start_time = time.time()
    fin = start_time + _timeout
    eventos: List[Dict] = []

    tor_status = "Tor ✓" if (_verificar_tor()) else "directo"
    print(f"  🔍 Dorks: {len(dorks)} dorks [Google-only, {tor_status}, budget {_timeout}s]")

    urls_procesadas: Set[str] = set()
    dorks_procesados = 0
    motores_bloqueados: Set[str] = set()

    for idx, dork in enumerate(dorks):
        if time.time() > fin:
            print("  ⏰ Dorks: timeout global alcanzado")
            break

        # Rotar Tor cada TOR_ROTATE_EVERY dorks
        if idx > 0 and idx % TOR_ROTATE_EVERY == 0:
            if _tor_ok and _tor_ok():
                print(f"  🔄 Tor: rotando cada {TOR_ROTATE_EVERY} dorks...")
                _rotar_identidad_tor()

        if _bloqueo_detectado_en_dork:
            print("  🔄 Tor: bloqueo detectado, rotando...")
            _rotar_identidad_tor()
            _bloqueo_detectado_en_dork = False

        resultados, motor = _buscar_cascada(dork)
        if not resultados:
            continue

        for hit in resultados:
            url = hit.get("url", "")
            if url in urls_procesadas:
                continue
            urls_procesadas.add(url)

            titulo = hit.get("titulo", "")
            snippet = hit.get("snippet", "")

            if _es_texto_ruidoso(titulo) or _es_texto_ruidoso(snippet):
                continue
            if _es_url_ruidosa(url):
                continue

            tipo = _clasificar_url(url)
            if tipo is None:
                continue

            # Videos y pre-2024 se descartan
            if "/videos/" in url.lower() or "watch?" in url.lower():
                continue

            if tipo == "evento_facebook":
                if not _es_relevante_evento(titulo, snippet):
                    continue
                fecha = _extraer_fecha_snippet(f"{titulo} {snippet}")
                if fecha and int(fecha[:4]) < 2024:
                    continue
                lugar = _extraer_lugar_snippet(f"{titulo} {snippet}")
                organizador = _extraer_organizador_snippet(titulo, snippet)
                eventos.append({
                    "nombre": titulo[:200],
                    "fecha": fecha or "N/A",
                    "lugar": lugar or "N/A",
                    "ciudad": "N/A", "pais": "N/A",
                    "continente": "N/A", "subcontinente": "N/A",
                    "tipo_lugar": "N/A",
                    "fuente": f"Dorks ({motor})",
                    "organizador": organizador,
                    "email": "N/A", "link": url,
                    "subgenero": "general",
                    "descripcion": snippet[:500] or titulo[:500],
                    "_tipo_dork": tipo, "_motor": motor,
                })

            elif tipo == "grupo_facebook":
                # Guardar en grupos_encontrados.json (additive)
                _guardar_grupo_fb(url, titulo)

            elif tipo in ("perfil_instagram",):
                # Perfiles IG → no evento (se maneja en otro módulo)
                pass

        dorks_procesados += 1
        _busquedas_exitosas += 1
        if _busquedas_exitosas >= TOR_ROTATE_EVERY:
            print(f"  🔄 Tor: {TOR_ROTATE_EVERY} búsquedas, rotando identidad...")
            _rotar_identidad_tor()
            _busquedas_exitosas = 0

        _random_sleep()
        if len(eventos) >= MAX_EVENTS_PER_RUN:
            break

    # Dedup por URL
    vistos: Set[str] = set()
    unicos: List[Dict] = []
    for ev in eventos:
        k = ev.get("link") or ""
        if k and k not in vistos:
            vistos.add(k)
            unicos.append(ev)
    eventos = unicos

    print(f"  📊 Dorks: {len(eventos)} eventos de {dorks_procesados} dorks "
          f"({time.time() - start_time:.1f}s)")

    if eventos:
        _guardar_eventos(eventos)
    return eventos


def _guardar_grupo_fb(url: str, nombre: str) -> None:
    """Guarda un grupo FB en grupos_encontrados.json sin duplicar (additive)."""
    m = FB_GROUP_RE.search(url)
    if not m:
        return
    gid = m.group(1)
    data = _leer_json(GRUPOS_FB_FILE, {"groups": []})
    if not isinstance(data, dict):
        data = {"groups": []}
    grupos = data.get("groups", [])
    if any(g.get("id") == gid for g in grupos):
        return
    grupos.append({
        "id": gid,
        "nombre": nombre[:200] or gid,
        "url": f"https://facebook.com/groups/{gid}",
        "tipo_grupo": [],
    })
    data["groups"] = grupos
    data["total"] = len(grupos)
    data["timestamp"] = datetime.now(timezone.utc).isoformat()
    _escribir_json(GRUPOS_FB_FILE, data)


def _guardar_eventos(eventos: List[Dict]) -> None:
    existente = _leer_json(EVENTOS_FILE, [])
    if not isinstance(existente, list):
        existente = []
    urls = {e.get("link") for e in existente if isinstance(e, dict)}
    nuevos = [e for e in eventos if e.get("link") not in urls]
    _escribir_json(EVENTOS_FILE, existente + nuevos)


if __name__ == "__main__":
    evs = scrape_dorks_resultados(limite=10)
    print(f"\nEventos: {len(evs)}")
    for e in evs[:5]:
        print(f"  {e['nombre'][:50]} | {e.get('fecha','?')} | {e.get('lugar','?')} | org: {e.get('organizador','?')}")
