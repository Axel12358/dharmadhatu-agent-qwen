#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Facebook Dorks v4.1 — Google SOLO a través de Playwright + Tor + stealth.
Sin APIs externas. Los demás motores (DDG, Mojeek, Bing, Startpage) están
desactivados del flujo automático; se conservan como funciones pero no se usan.

Estrategia:
  1. Google vía Playwright con proxy Tor (pool de múltiples circuitos).
     stealth activado si playwright-stealth está disponible.
  2. Si Google bloquea (CAPTCHA/consent) → rotar identidad Tor y reintentar
     el mismo dork (máx 3 intentos).
  3. Parsea div.g (resultado), a[href^="http"] (URL), h3 (título),
     span.st / div[data-sncf] (snippet).
  4. Clasifica: grupos → grupos_encontrados.json, eventos → evento con
     organizador, perfiles/páginas → organizadores_facebook.json.

Límites: timeout global 180s, máx 10 dorks, rotación cada 3 circuitos,
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
except ImportError:
    obtener_sesion_tor = None
    obtener_proxy_tor = None
    _tor_ok = lambda: False
    renovar_identidad_tor = None
    _tor_restart_subprocess = None

# --- Tor Utils integration (rotación de identidad centralizada) ---
try:
    from core.tor_utils import (
        rotar_tor as rotar_identidad_tor_actualizado,
        verificar_tor as verificar_tor_actualizado,
    )
except ImportError:
    rotar_identidad_tor_actualizado = None
    verificar_tor_actualizado = None

# --- Tor Pool integration (multi-circuit management) ---
try:
    from core.tor_pool import (
        iniciar_pool as _iniciar_pool,
        rotar_tor as _pool_rotar_tor,
        verificar_tor as _pool_verificar_tor,
        obtener_sesion_tor as _pool_obtener_sesion,
        obtener_siguiente_identidad as _pool_siguiente_identidad,
    )
    try:
        _iniciar_pool()
    except Exception:
        pass
except ImportError:
    _pool_rotar_tor = None
    _pool_verificar_tor = None
    _pool_obtener_sesion = None
    _pool_siguiente_identidad = None

# Cookie de consentimiento de Google
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
    from core.busqueda_fusionada import fusionar_resultados
    STEALTH_AVAILABLE = True
except Exception:
    STEALTH_AVAILABLE = False

# --- Motores alternativos (pool Tor) para romper rate-limits de DDG ---
try:
    from core.busqueda_fusionada import (
        buscar_en_bing as _be_bing,
        buscar_en_startpage as _be_startpage,
        buscar_en_mojeek as _be_mojeek,
        buscar_en_searxng as _be_searxng,
    )
    _MOTOR_FNS = {k: v for k, v in {
        "bing": _be_bing, "startpage": _be_startpage,
        "mojeek": _be_mojeek, "searxng": _be_searxng,
    }.items() if v is not None}
except Exception:
    _MOTOR_FNS = {}

try:
    from playwright.async_api import async_playwright
    PLAYWRIGHT_AVAILABLE = True
except Exception:
    PLAYWRIGHT_AVAILABLE = False

# --- Extracción de eventos desde el snippet del SERP (Tor, sin Playwright) ---
try:
    from scrapers.event_extractor import EventExtractor
    _EXTRACTOR = EventExtractor()
except Exception:
    _EXTRACTOR = None

# --- Cliente HTTP con curl_cffi + Tor (mismo patrón que facebook_mcp) ---
try:
    from core.http_client import get_html as _get_html_tor
except Exception:
    _get_html_tor = None

import logging
logger = logging.getLogger(__name__)

# --- Constants ---
TIMEOUT_TOTAL = 180        # 180s presupuesto global
TIMEOUT_ENGINE = 12
MAX_DORKS_POR_EJECUCION = 10
SLEEP_BETWEEN = (20.0, 40.0)
TOR_ROTATE_EVERY = 3
TOR_RESTART_PAUSE = 10
MAX_REINTENTOS_POR_DORK = 3

# Motor único: GOOGLE vía Playwright+Tor. Otros motores desactivados.
FACEBOOK_ENGINES = ["google"]

# Fase 3: fallback multi-motor (Bing/Startpage/Mojeek/SearxNG) vía pool Tor
# para romper rate-limits de DDG-lite y sumar cobertura de resultados.
USAR_MULTI_MOTOR = True
_MOTORES_FALLBACK = ["bing", "startpage", "mojeek", "searxng"]

# --- Tor semaphore ---
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

# State files
GRUPOS_FB_FILE = Path(_PROJECT_ROOT) / "grupos_encontrados.json"
ORG_FB_FILE = Path(_PROJECT_ROOT) / "organizadores_facebook.json"
DORKS_EVENTOS_FILE = Path(_PROJECT_ROOT) / "facebook_dorks_eventos.json"


# ---------------------------------------------------------------------------
# Sesión Tor
# ---------------------------------------------------------------------------
def _get_sesion() -> requests.Session:
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
    s = requests.Session()
    proxy = obtener_proxy_tor() if obtener_proxy_tor else None
    if proxy:
        s.proxies.update(proxy)
    s.headers.update({
        "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/125.0.0.0 Safari/537.36"),
        "Accept-Language": "en-US,en;q=0.9",
    })
    _sesion_cache = s
    return s


def _rotar_identidad_tor() -> bool:
    """Rota la IP de Tor (pool) y resetea la sesión cacheada."""
    global _sesion_cache, _busquedas_exitosas
    _sesion_cache = None
    _busquedas_exitosas = 0
    if _pool_rotar_tor is not None and _pool_siguiente_identidad is not None:
        try:
            idx = _pool_siguiente_identidad()
            return _pool_rotar_tor(idx)
        except Exception:
            pass
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


# ---------------------------------------------------------------------------
# Google (requests + Tor)
# ---------------------------------------------------------------------------
def _google_search_requests(dork: str, timeout: int = TIMEOUT_ENGINE) -> Tuple[Optional[str], bool]:
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


def _startpage_search_requests(dork: str, timeout: int = TIMEOUT_ENGINE) -> Tuple[Optional[str], bool]:
    url = "https://www.startpage.com/sp/search"
    params = {"query": dork}
    html, _ = _http_get(url, params, GOOGLE_CONSENT_COOKIE, timeout)
    return html, False


def _mojeek_search_requests(dork: str, timeout: int = TIMEOUT_ENGINE) -> Tuple[Optional[str], bool]:
    url = "https://www.mojeek.com/search"
    params = {"q": dork, "hl": "en"}
    html, _ = _http_get(url, params, GOOGLE_CONSENT_COOKIE, timeout)
    return html, False


def _ddg_search_requests(dork: str, timeout: int = TIMEOUT_ENGINE) -> Tuple[Optional[str], bool]:
    url = "https://html.duckduckgo.com/html/"
    params = {"q": dork}
    html, _ = _http_get(url, params, None, timeout)
    return html, False


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
            dork, "fb", max_retries=3, indice_tor=indice_tor, timeout=timeout)
    except Exception as e:
        logger.warning(f"Google Playwright error: {e}")
        return None


# ---------------------------------------------------------------------------
# Búsqueda de dorks vía Tor (curl_cffi) — rápida y sin IP real.
# Parsea los snippets de DDG-lite; NO visita las páginas de Facebook
# (login-wall por Tor). Extrae el evento directamente del snippet.
# ---------------------------------------------------------------------------
def _buscar_dork_tor(dork: str, timeout: int = 15) -> List[Dict]:
    """Busca un dork en DDG-lite usando curl_cffi + Tor (socks5h).

    Devuelve lista de {url, titulo, snippet} filtrada a enlaces
    facebook.com. Si Tor/get_html no está disponible, devuelve [].
    """
    if _get_html_tor is None:
        return []
    from bs4 import BeautifulSoup
    import urllib.parse as _up
    html = _get_html_tor(
        "https://lite.duckduckgo.com/lite/?q=" + _up.quote(dork), timeout)
    if not html:
        return []
    soup = BeautifulSoup(html, "html.parser")
    salida = []
    for a, sn in zip(soup.select("a.result-link"),
                      soup.select(".result-snippet")):
        href = a.get("href", "")
        m = re.search(r"uddg=([^&]+)", href)
        url = _up.unquote(m.group(1)) if m else ""
        if "facebook.com" not in url:
            continue
        titulo = a.get_text(" ", strip=True)
        snippet = sn.get_text(" ", strip=True) if sn else ""
        salida.append({"url": url, "titulo": titulo, "snippet": snippet})
    return salida





def _buscar_dork_multimotor_tor(dork: str, timeout: int = 15) -> List[Dict]:
    """Busca un dork por Tor usando varios motores para romper rate-limits.

    DDG-lite (curl_cffi, rápido) primero; si viene vacío (bloqueo), cae en
    Bing -> Startpage -> Mojeek -> SearxNG, cada uno por un exit distinto del
    pool Tor. Devuelve solo resultados facebook.com.
    """
    res = _buscar_dork_tor(dork, timeout)
    if res:
        return [x for x in res if "facebook.com" in (x.get("url", "").lower())]
    if not USAR_MULTI_MOTOR or not _MOTOR_FNS:
        return []
    # DDG bloqueó: rotamos exit y probamos los otros motores.
    if _rotar_identidad_tor is not None:
        try:
            _rotar_identidad_tor()
        except Exception:
            pass
    for nombre in _MOTORES_FALLBACK:
        fn = _MOTOR_FNS.get(nombre)
        if fn is None:
            continue
        try:
            r = fn(dork, timeout)
        except Exception:
            r = []
        if isinstance(r, list):
            fb = [x for x in r if "facebook.com" in (x.get("url", "").lower())]
            if fb:
                return fb
    return []


# ---------------------------------------------------------------------------
# Bing (requests + Tor) — respaldo adicional
# ---------------------------------------------------------------------------
def _bing_search_requests(dork: str, timeout: int = TIMEOUT_ENGINE) -> Tuple[Optional[str], bool]:
    """Bing via Tor fallback."""
    return _http_get(
        "https://www.bing.com/search", {"q": dork}, GOOGLE_CONSENT_COOKIE, timeout)
# ---------------------------------------------------------------------------
# Parsers (genéricos, filtramos solo facebook.com)
# ---------------------------------------------------------------------------
def _limpiar_url_google(href: str) -> str:
    if href.startswith("/url?q="):
        m = re.search(r"/url\?q=([^&]+)", href)
        if m:
            return urllib.parse.unquote(m.group(1))
    if href.startswith("/url?"):
        m = re.search(r"url=([^&]+)", href)
        if m:
            return urllib.parse.unquote(m.group(1))
    return href


def _parse_serp_facebook(html: str, motor: str) -> List[Dict]:
    """Parsea resultados de cualquier SERP, filtrando solo enlaces facebook.com."""
    resultados = []
    if not html:
        return resultados
    soup = BeautifulSoup(html, "lxml")

    # Google / Startpage: div.g
    contenedores = soup.find_all("div", class_="g")
    # Mojeek: a.ob
    if not contenedores:
        contenedores = soup.select("a.ob")
    # DDG: a.result__a
    if not contenedores:
        contenedores = soup.select("a.result__a")

    for el in contenedores:
        # Extraer href
        if el.name == "a":
            a = el
        else:
            a = el.find("a", href=True)
        if not a:
            continue
        href = a["href"]
        href = _limpiar_url_google(href)
        if not href.startswith("http"):
            continue
        # Solo Facebook
        if not re.search(r"facebook\.com", href, re.IGNORECASE):
            continue
        # Título
        if el.name == "a":
            titulo = el.get_text(strip=True)
        else:
            h3 = el.find("h3")
            titulo = h3.get_text(strip=True) if h3 else a.get_text(strip=True)
        if not titulo:
            continue
        # Snippet
        snippet_el = None
        if el.name != "a":
            snippet_el = (el.find("span", class_="st")
                          or el.find("div", attrs={"data-sncf": True})
                          or el.find("p", class_="s")
                          or el.find("div", class_="result-snippet"))
        snippet = snippet_el.get_text(strip=True) if snippet_el else ""
        resultados.append({
            "titulo": titulo,
            "url": href.split("?")[0].rstrip("/"),
            "snippet": snippet,
            "motor": motor,
        })
        if len(resultados) >= MAX_DORKS_POR_EJECUCION:
            break
    return resultados


def _buscar_google_con_reintentos_fb(dork: str) -> Tuple[List[Dict], bool]:
    """Busca en Google usando búsqueda fusionada de motores.

    Orden: Firefox + Playwright → SearxNG pública → requests por Tor
    (fusión de resultados sin duplicados por URL).
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
        # Búsqueda fusionada multi-motor (DDG → Bing → Startpage → Mojeek →
        # SearxNG → Google fallback), todo a través de Tor.
        resultados = fusionar_resultados(dork, indice_tor=None, timeout=20)
        if resultados:
            res = [r for r in resultados if "facebook.com" in r.get("url", "").lower()]
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


def _buscar_cascada_fb(dork: str) -> Tuple[List[Dict], Optional[str]]:
    """Google SOLO (Playwright + Tor). No usa otros motores.

    Returns (resultados, motor_usado). Retorna ([], "google") si falla.
    """
    resultados, bloqueado = _buscar_google_con_reintentos_fb(dork)
    if resultados:
        return resultados, "google"
    return [], "google"


# ---------------------------------------------------------------------------
# URL classification (Facebook)
# ---------------------------------------------------------------------------
FB_EVENT_RE = re.compile(r"facebook\.com/events/[\w.\-]+")
FB_GROUP_RE = re.compile(r"facebook\.com/groups/([\w.\-]+)")


def _clasificar_url_facebook(url: str) -> Optional[str]:
    if not url:
        return None
    if FB_EVENT_RE.search(url):
        return "evento"
    if FB_GROUP_RE.search(url):
        return "grupo"
    if "facebook.com/" in url.lower():
        if "/pages/" in url.lower() or "/profile.php" in url.lower():
            return "perfil"
        m = re.search(r"facebook\.com/([A-Za-z0-9_.]+)/?$", url)
        if m and m.group(1).lower() not in (
                "groups", "events", "share", "sharer", "login", "hashtag"):
            return "perfil"
    return None


def _es_url_facebook_valida(url: str) -> bool:
    if not url:
        return False
    u = url.lower()
    if not any(d in u for d in ["facebook.com", "m.facebook.com"]):
        return False
    patrones_basura = ["login", "signin", "marketplace", "watch", "messages"]
    return not any(p in u for p in patrones_basura)


def _extraer_organizador_desde_snippet(titulo: str, snippet: str) -> str:
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
    return ""


def _extraer_fecha_desde_snippet(texto: str) -> Optional[str]:
    for pat in [re.compile(r"\b(20\d{2})-(\d{1,2})-(\d{1,2})\b"),
                re.compile(r"\b(\d{1,2})[/.\-](\d{1,2})[/.\-](20\d{2})\b")]:
        m = pat.search(texto)
        if m:
            try:
                g = m.groups()
                y, mo, d = int(g[0]), int(g[1]), int(g[2])
                if 1 <= mo <= 12 and 1 <= d <= 31:
                    return f"{y}-{mo:02d}-{d:02d}"
            except (ValueError, TypeError):
                continue
    return None


def _es_relevante_evento_simple(titulo: str, snippet: str) -> bool:
    texto = f"{titulo} {snippet}".lower()
    keywords = ["psytrance", "darkpsy", "forest", "hitech", "goa",
                "rave", "festival", "party", "event", "open air",
                "fullon", "psychill", "psybient"]
    return any(kw in texto for kw in keywords)


def _extraer_fb_username(url: str) -> Optional[str]:
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
# Dork generation: 60% grupos, 40% eventos
# ---------------------------------------------------------------------------
_SUBGENEROS = ["psytrance", "darkpsy", "forest", "goa", "hitech",
               "psychill", "fullon", "twilight", "psycore", "suomisaundi",
               "zenon", "progressive", "psybient", "goa trance"]
_CIUDADES = ["Berlin", "Barcelona", "Mexico", "Amsterdam", "Paris",
            "Lisbon", "Madrid", "London", "Rome", "Vienna", "Buenos Aires",
            "Sao Paulo", "Tel Aviv", "Mumbai", "Bangkok", "Sydney", "Prague",
            "Ibiza", "Moscow", "Hamburg", "Athens", "Copenhagen"]
_PAISES = ["Germany", "Spain", "Mexico", "Brazil", "France", "Portugal",
          "United Kingdom", "Italy", "Argentina", "Israel", "India",
          "Thailand", "Australia", "Japan", "Russia", "Netherlands"]
_AÑOS = ["2025", "2026", "2027"]
_TIPOS_EVENTO = ["festival", "party", "rave", "event", "open air",
                "fiesta", "night", "gathering"]
_TIPOS_GRUPO = ["comunidad", "crew", "familia", "tribe", "group"]


def _generar_dorks_facebook(n: int = None, semilla: int = None) -> List[str]:
    """Genera dorks combinando subgéneros × localidades × años × tipos,
    con un generador aleatorio (barajado) para que cada corrida explore
    combinaciones distintas de la escena psytrance.

    El espacio completo de combinaciones se construye y luego se muestrean
    ``n`` dorks al azar. Los dorks de evento usan formato libre (sin
    comillas) porque DDG-lite solo devuelve resultados así.
    """
    if semilla is not None:
        random.seed(semilla)
    if n is None:
        n = MAX_DORKS_POR_EJECUCION

    localidades = _CIUDADES + _PAISES
    base: List[str] = []
    # --- Dorks de EVENTOS (aportan filas al CSV) ---
    for sub in _SUBGENEROS:
        for lug in localidades:
            base.append(f"site:facebook.com/events {sub} {lug}")
            for tipo in _TIPOS_EVENTO:
                base.append(f"site:facebook.com/events {sub} {tipo} {lug}")
                for anio in _AÑOS:
                    base.append(f"site:facebook.com/events {sub} {tipo} {lug} {anio}")
    # --- Dorks de GRUPOS (descubrimiento de fuentes) ---
    for sub in _SUBGENEROS:
        for lug in localidades:
            base.append(f"site:facebook.com/groups {sub} {lug}")
            for tg in _TIPOS_GRUPO:
                base.append(f"site:facebook.com/groups {sub} {tg} {lug}")

    random.shuffle(base)
    return base[:n]


# ---------------------------------------------------------------------------
# Persistence (additive)
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
            # default=str: nunca corrompe el JSON si algún valor no serializa.
            json.dump(datos, f, ensure_ascii=False, indent=2, default=str)
        os.replace(tmp, str(ruta))
    except (IOError, OSError):
        pass


def _guardar_grupo_fb(url: str, nombre: str) -> None:
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
    from datetime import datetime, timezone
    data["timestamp"] = datetime.now(timezone.utc).isoformat()
    _escribir_json(GRUPOS_FB_FILE, data)


def _guardar_organizador_fb(username: str, url: str, nombre: str,
                            fuente: str = "facebook_dorks") -> None:
    data = _leer_json(ORG_FB_FILE, {})
    if not isinstance(data, dict):
        data = {}
    if username in data:
        d = data[username]
        fuentes = set(d.get("fuentes", []))
        fuentes.add(fuente)
        d["fuentes"] = list(fuentes)
        d["total_hallazgos"] = int(d.get("total_hallazgos", 0)) + 1
    else:
        data[username] = {
            "nombre": nombre[:200] or username,
            "url": url,
            "tipo": "perfil_facebook",
            "email": "N/A",
            "fuentes": [fuente],
            "total_hallazgos": 1,
            "agregado": True,
        }
    _escribir_json(ORG_FB_FILE, data)


# ---------------------------------------------------------------------------
# Main function
# ---------------------------------------------------------------------------
def scrape_facebook_dorks(
    temas: Optional[List[str]] = None,
    ciudades: Optional[List[str]] = None,
    limite: int = 10,
    dorks: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Punto de entrada principal para Facebook Dorks.

    Google SOLO a través de Playwright + Tor + stealth.
    Clasifica grupos / eventos / perfiles de Facebook.
    """
    global _bloqueo_detectado_en_dork

    # --- Configuración ligera y acotada (rápido, sin congelarse) ---
    start_time = time.time()
    _timeout = 180  # tope de seguridad global
    if dorks is None:
        dorks = _generar_dorks_facebook()
    if limite and limite > 0:
        dorks = dorks[:limite]
    else:
        dorks = dorks[:MAX_DORKS_POR_EJECUCION]

    if not dorks:
        return {"eventos": [], "grupos": [], "organizadores": set()}

    tor_status = "Tor ✓" if _verificar_tor() else "directo"
    print(f"  🔍 Facebook Dorks: {len(dorks)} dorks [curl_cffi+Tor, {tor_status}]")

    eventos: List[Dict] = []
    grupos: List[Dict] = []
    organizadores: Set[str] = set()
    urls_procesadas: Set[str] = set()

    for idx, dork in enumerate(dorks):
        if time.time() - start_time > _timeout:
            print("  ⏰ Facebook Dorks: timeout global alcanzado")
            break

        # Subgénero inferido del dork (para etiquetar el evento)
        sub = "general"
        for sg in ("darkpsy", "forest", "psychill", "psybient", "fullon",
                   "progressive", "goa", "hitech", "twilight", "psycore",
                   "suomisaundi", "zenon", "psychedelic", "psytrance"):
            if sg in dork.lower():
                sub = sg
                break

        resultados = _buscar_dork_multimotor_tor(dork, timeout=15)
        if not resultados:
            continue

        for hit in resultados:
            url = hit.get("url", "")
            titulo = hit.get("titulo", "")
            snippet = hit.get("snippet", "")
            if not url or not titulo or url in urls_procesadas:
                continue
            urls_procesadas.add(url)

            if not _es_url_facebook_valida(url):
                continue
            if "/videos/" in url.lower() or "watch?" in url.lower():
                continue

            tipo = _clasificar_url_facebook(url)
            if tipo is None:
                continue

            if tipo == "evento":
                if _EXTRACTOR is None:
                    continue
                blob = f"{titulo} {snippet}"
                evs = _EXTRACTOR.extract_all(blob[:1500], "Facebook (dorks)", url)
                for ev in evs:
                    if ev.get("fecha") in (None, "", "N/A"):
                        continue
                    ev["subgenero"] = sub if sub != "general" else (
                        ev.get("subgenero") or "general")
                    ev["fuente"] = "Facebook (dorks)"
                    ev["organizador"] = ev.get("organizador") or "N/A"
                    ev["descripcion"] = snippet[:500] or titulo[:500]
                    eventos.append(ev)

            elif tipo == "grupo":
                _guardar_grupo_fb(url, titulo)
                grupos.append({
                    "nombre": titulo[:200],
                    "url": url, "tipo": "grupo_facebook",
                    "_motor": "ddg_tor",
                })

            elif tipo == "perfil":
                username = _extraer_fb_username(url)
                if username:
                    _guardar_organizador_fb(username, url, titulo)
                    organizadores.add(username)

        # Pausa breve y humana entre dorks (sin los 30-60s del diseño previo)
        time.sleep(random.uniform(1.5, 3.0))

    # Dedup eventos por URL
    vistos: Set[str] = set()
    eventos_unicos: List[Dict] = []
    for ev in eventos:
        k = ev.get("link") or ev.get("url") or ""
        if k and k not in vistos:
            vistos.add(k)
            eventos_unicos.append(ev)

    vistos_g: Set[str] = set()
    grupos_unicos: List[Dict] = []
    for g in grupos:
        k = g.get("url", "")
        if k and k not in vistos_g:
            vistos_g.add(k)
            grupos_unicos.append(g)

    print(f"  📊 Facebook Dorks: {len(eventos_unicos)} eventos + "
          f"{len(grupos_unicos)} grupos, {len(organizadores)} organizadores "
          f"en {time.time() - start_time:.1f}s")

    return {
        "eventos": eventos_unicos,
        "grupos": grupos_unicos,
        "organizadores": organizadores,
        "tiempo_ejecucion": round(time.time() - start_time, 1),
    }


# ---------------------------------------------------------------------------
# Bucle iterativo (aplicación efectiva de los dorks)
# ---------------------------------------------------------------------------
def _cargar_eventos_dorks() -> List[Dict]:
    data = _leer_json(DORKS_EVENTOS_FILE, {"eventos": []})
    if isinstance(data, dict):
        evs = data.get("eventos", [])
        return evs if isinstance(evs, list) else []
    return []


def _guardar_eventos_dorks(eventos: List[Dict]) -> List[Dict]:
    """Mezcla con lo ya persistido (dedup por URL) — aditivo y sobrevive a
    caídas del pipeline."""
    exist = _cargar_eventos_dorks()
    vistos = {e.get("link") or e.get("url") for e in exist}
    for e in eventos:
        k = e.get("link") or e.get("url")
        if k and k not in vistos:
            vistos.add(k)
            exist.append(e)
    _escribir_json(DORKS_EVENTOS_FILE, {"eventos": exist, "total": len(exist)})
    return exist


def scrape_facebook_dorks_loop(
    max_rondas: int = 3,
    limite: int = 6,
    objetivo_eventos: int = 12,
    pausa_base: float = 4.0,
    verbose: bool = True,
) -> Dict[str, Any]:
    """Aplica Facebook Dorks de forma iterativa y efectiva.

    Cada ronda genera combinaciones NUEVAS y aleatorias
    (subgénero × localidad × año × tipo) vía ``_generar_dorks_facebook``,
    acumula eventos únicos y aplica *backoff* si DDG empieza a rate-limitear
    (rondas vacías). Persiste incrementalmente en
    ``facebook_dorks_eventos.json`` (aditivo). Se detiene al alcanzar el
    objetivo, agotar rondas, o tras un backoff excesivo.
    """
    # Semilla con lo ya persistido para recuperación entre corridas
    eventos = _cargar_eventos_dorks()
    vistos_ev: Set[str] = {e.get("link") or e.get("url") for e in eventos}
    grupos: List[Dict] = []
    vistos_g: Set[str] = set()
    orgs: Set[str] = set()
    perfiles: List[str] = []

    ronda = 0
    vacias = 0
    pausa = pausa_base

    while ronda < max_rondas:
        ronda += 1
        semilla = (int(time.time() * 1000) % 100000) + ronda * 104729
        dorks = _generar_dorks_facebook(n=limite, semilla=semilla)
        # Timeout de seguridad por ronda: si DDG/Tor se traba, abortamos la
        # ronda y seguimos (nunca colgar la terminal).
        try:
            from concurrent.futures import ThreadPoolExecutor as _TPE, TimeoutError as _TOE
            with _TPE(max_workers=1) as _ex:
                _f = _ex.submit(scrape_facebook_dorks, dorks=dorks, limite=limite)
                res = _f.result(timeout=200)
        except Exception as _e:
            if verbose:
                print(f"  ⏱️ Dorks ronda {ronda}: timeout/error ({type(_e).__name__}) — "
                      f"se aborta el loop para no colgar")
            break
        evs = res.get("eventos", []) or []
        for e in evs:
            k = e.get("link") or e.get("url") or ""
            if k and k not in vistos_ev:
                vistos_ev.add(k)
                eventos.append(e)
        for g in res.get("grupos", []) or []:
            k = g.get("url", "")
            if k and k not in vistos_g:
                vistos_g.add(k)
                grupos.append(g)
        for o in res.get("organizadores", set()) or set():
            orgs.add(o)
        for p in res.get("perfiles", []) or []:
            perfiles.append(p)

        if verbose:
            print(f"  🔁 Dorks ronda {ronda}/{max_rondas}: +{len(evs)} "
                  f"eventos (acumulado {len(eventos)})")

        if len(eventos) >= objetivo_eventos:
            if verbose:
                print(f"  🎯 Objetivo alcanzado ({len(eventos)} eventos)")
            break

        if len(evs) == 0:
            vacias += 1
            if vacias >= 2:
                pausa = min(pausa * 2, 120)
                if verbose:
                    print(f"  ⏳ Posible rate-limit de DDG, backoff {pausa:.0f}s")
                if pausa >= 120:
                    break
        else:
            vacias = 0
            pausa = pausa_base

        if ronda < max_rondas:
            time.sleep(pausa)

    # Persistencia aditiva (recuperable en futuras corridas)
    eventos = _guardar_eventos_dorks(eventos)
    if verbose:
        print(f"  💾 Dorks persistidos: {len(eventos)} eventos únicos en "
              f"{DORKS_EVENTOS_FILE.name}")
    return {
        "eventos": eventos,
        "grupos": grupos,
        "organizadores": orgs,
        "perfiles": perfiles,
    }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    res = scrape_facebook_dorks(limite=10)
    print(f"\nEventos: {len(res['eventos'])}")
    for e in res["eventos"][:5]:
        print(f"  {e['nombre'][:50]} | {e.get('fecha','?')} | org: {e.get('organizador','?')}")
    print(f"\nGrupos: {len(res['grupos'])}")
    print(f"Organizadores: {res['organizadores']}")
