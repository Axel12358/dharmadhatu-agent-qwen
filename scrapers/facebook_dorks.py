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

try:
    from playwright.async_api import async_playwright
    PLAYWRIGHT_AVAILABLE = True
except Exception:
    PLAYWRIGHT_AVAILABLE = False

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
              "psychill", "fullon", "twilight", "psycore", "suomisaundi"]
_CIUDADES = ["Berlin", "Barcelona", "Mexico", "Amsterdam", "Paris",
            "Lisbon", "Madrid", "London", "Rome", "Vienna"]


def _generar_dorks_facebook() -> List[str]:
    dorks: List[str] = []
    # 60% grupos (6)
    specs_grupos = [
        'site:facebook.com/groups "psytrance" "Berlin"',
        'site:facebook.com/groups "darkpsy" "Mexico"',
        'site:facebook.com/groups "forest psy" "comunidad"',
        'site:facebook.com/groups "goa trance" "festival"',
        'site:facebook.com/groups "hitech" "rave"',
        'site:facebook.com/groups "twilight" "party"',
    ]
    dorks.extend(specs_grupos)
    # 40% eventos (4)
    specs_eventos = [
        'site:facebook.com/events "psytrance" "Berlin" "2026"',
        'site:facebook.com/events "darkpsy" "rave" "2026"',
        'site:facebook.com/events "goa trance" "festival"',
        'site:facebook.com/events "forest psy" "comunidad" "2026"',
    ]
    dorks.extend(specs_eventos)
    return dorks[:MAX_DORKS_POR_EJECUCION]


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
            json.dump(datos, f, ensure_ascii=False, indent=2)
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

    # Load dynamic configuration
    _cfg = load_loop_config() if STEALTH_AVAILABLE else {}
    _timeout = _cfg.get("timeout_total", TIMEOUT_TOTAL)
    _max_dorks = _cfg.get("max_dorks", MAX_DORKS_POR_EJECUCION)

    if dorks is None:
        dorks = _generar_dorks_facebook()
    _limite = limite if limite and limite > 0 else _max_dorks
    if dorks:
        dorks = dorks[:_limite]

    if not dorks:
        return {"eventos": [], "grupos": [], "organizadores": set()}

    start_time = time.time()
    fin = start_time + _timeout
    eventos: List[Dict] = []
    grupos: List[Dict] = []
    organizadores: Set[str] = set()

    tor_status = "Tor ✓" if _verificar_tor() else "directo"
    print(f"  🔍 Facebook Dorks: {len(dorks)} dorks [Google-only, {tor_status}, budget {_timeout}s]")

    urls_procesadas: Set[str] = set()
    dorks_procesados = 0
    motores_bloqueados: Set[str] = set()

    for idx, dork in enumerate(dorks):
        if time.time() > fin:
            print("  ⏰ Facebook Dorks: timeout global alcanzado")
            break

        if dorks_procesados > 0 and dorks_procesados % TOR_ROTATE_EVERY == 0:
            print(f"  🔄 Tor: rotando cada {TOR_ROTATE_EVERY} dorks...")
            _rotar_identidad_tor()

        if _bloqueo_detectado_en_dork:
            print("  🔄 Tor: bloqueo detectado, rotando...")
            _rotar_identidad_tor()
            _bloqueo_detectado_en_dork = False

        resultados, motor = _buscar_cascada_fb(dork)
        if not resultados:
            continue

        for hit in resultados:
            url = hit.get("url", "")
            titulo = hit.get("titulo", "")
            snippet = hit.get("snippet", "")
            if not url or not titulo:
                continue
            if url in urls_procesadas:
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
                fecha = _extraer_fecha_desde_snippet(f"{titulo} {snippet}")
                if fecha and int(fecha[:4]) < 2024:
                    continue
                if not _es_relevante_evento_simple(titulo, snippet):
                    continue
                organizador = _extraer_organizador_desde_snippet(titulo, snippet)
                if organizador:
                    organizadores.add(organizador)
                eventos.append({
                    "nombre": titulo[:200],
                    "fecha": fecha or "N/A",
                    "lugar": "N/A", "ciudad": "N/A", "pais": "N/A",
                    "continente": "N/A", "subcontinente": "N/A",
                    "tipo_lugar": "N/A",
                    "fuente": f"Facebook Dorks ({motor})",
                    "organizador": organizador or "N/A",
                    "email": "N/A", "link": url,
                    "subgenero": "general",
                    "descripcion": snippet[:500] or titulo[:500],
                    "_tipo_dork": "google_dork", "_motor": motor,
                })

            elif tipo == "grupo":
                _guardar_grupo_fb(url, titulo)
                grupos.append({
                    "nombre": titulo[:200],
                    "url": url, "tipo": "grupo_facebook",
                    "_motor": motor or "unknown",
                })

            elif tipo == "perfil":
                username = _extraer_fb_username(url)
                if username:
                    _guardar_organizador_fb(username, url, titulo)
                    organizadores.add(username)

        dorks_procesados += 1
        _busquedas_exitosas += 1
        if _busquedas_exitosas >= TOR_ROTATE_EVERY:
            print(f"  🔄 Tor: {TOR_ROTATE_EVERY} búsquedas, rotando identidad...")
            _rotar_identidad_tor()
            _busquedas_exitosas = 0

        # Between dorks: 30-60s pause (human behavior)
        _random_sleep()
        if len(eventos) + len(grupos) >= _limite:
            break

    # Dedup eventos por URL
    vistos: Set[str] = set()
    eventos_unicos: List[Dict] = []
    for ev in eventos:
        k = ev.get("link", "")
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


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    res = scrape_facebook_dorks(limite=10)
    print(f"\nEventos: {len(res['eventos'])}")
    for e in res["eventos"][:5]:
        print(f"  {e['nombre'][:50]} | {e.get('fecha','?')} | org: {e.get('organizador','?')}")
    print(f"\nGrupos: {len(res['grupos'])}")
    print(f"Organizadores: {res['organizadores']}")
