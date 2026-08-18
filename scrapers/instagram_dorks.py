#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Instagram Dorks — búsqueda de eventos de psytrance vía DuckDuckGo y Mojeek.

Estrategia:
1. Genera dorks (10 por ejecución) con consultas NATURALES (no site:)
   combinando subgéneros × ciudades × hashtags de eventos y organizadores.
2. Busca en 2 motores: DDG (html.duckduckgo.com) → Mojeek.
   Timeout 8s por motor. Si un motor falla o bloquea, saltar al siguiente dork.
3. Para cada URL de Instagram encontrada:
   - Si es post/reel/tv (/p/, /reel/, /tv/), visita la página pública (sin login)
     y extrae caption, fecha, ubicación.
   - Si es perfil → organizador. Se guarda como organizador, NO como evento.
4. Pasa el caption por EventExtractor para extraer eventos.
5. Filtra ruido (memes, patrocinios, contenido no-evento).
6. Dedup local por URL. Archivos de estado para loop de mejora.

Rotación de Tor: reinicio vía subprocess cada 5 dorks o al detectar bloqueo,
con pausa de 10s. Semáforo global de Tor (máx 1 petición a la vez).

Presupuesto de tiempo: 180s máximo. 60% para búsqueda (dorks), 40% para
fase de perfiles. La fase de perfiles SIEMPRE se ejecuta.
Aditivo: nunca rompe el bot.
"""

from __future__ import annotations

import functools
import json
import os
import random
import re
import sys
import threading
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import requests
from bs4 import BeautifulSoup

_PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

# --- Tor integration (optional) ---
try:
    from core.tor_manager import (
        crear_sesion_busqueda,
        tor_disponible as _tor_ok,
        renovar_identidad_tor,
    )
except ImportError:
    crear_sesion_busqueda = None
    _tor_ok = lambda: False
    renovar_identidad_tor = None

try:
    from core.tor_manager import _reiniciar_proceso_tor as _tor_restart_subprocess
except Exception:
    _tor_restart_subprocess = None

# --- Tor Utils integration ---
try:
    from core.tor_utils import (
        rotar_tor as rotar_identidad_tor_actualizado,
        verificar_tor as verificar_tor_actualizado,
    )
except ImportError:
    rotar_identidad_tor_actualizado = None
    verificar_tor_actualizado = None

# --- Tor Pool integration ---
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
    from playwright.sync_api import sync_playwright
    PLAYWRIGHT_SYNC_AVAILABLE = True
except Exception:
    PLAYWRIGHT_SYNC_AVAILABLE = False

try:
    from playwright.async_api import async_playwright
    PLAYWRIGHT_ASYNC_AVAILABLE = True
except Exception:
    PLAYWRIGHT_ASYNC_AVAILABLE = False

_STATE_DIR = Path(__file__).resolve().parent / "instagram_dorks"
_STATE_DIR.mkdir(parents=True, exist_ok=True)

PRODUCTIVOS_FILE = _STATE_DIR / "instagram_dorks_productivos.json"
RENDIMIENTO_FILE = _STATE_DIR / "instagram_dorks_rendimiento.json"

# --- Constantes ---
TIMEOUT_TOTAL = 180
TIMEOUT_REQUEST = 15
MAX_RESULTS_PER_DORK = 10
MAX_DORKS_PER_RUN = 10
MAX_POSTS_PER_DORK = 8
SLEEP_BETWEEN = (20.0, 40.0)  # Pausas largas entre dorks
TOR_ROTATE_EVERY = 1       # Rotar Tor ANTES de cada dork
TOR_RESTART_PAUSE = 10
PLAYWRIGHT_TIMEOUT = 15
MAX_REINTENTOS_POR_DORK = 5  # Más reintentos

# Output files
ORGANIZADORES_INSTAGRAM_FILE = _STATE_DIR / "organizadores_instagram.json"
EVENTOS_PERFILES_INSTAGRAM_FILE = _STATE_DIR / "eventos_instagram_perfiles.json"

# Motor único: GOOGLE vía Playwright+Tor. Otros motores desactivados.
IG_ENGINES = ["google"]
TIMEOUT_ENGINE = 8
MAX_DORKS_POR_EJECUCION = 10


def _verificar_tor_ig() -> bool:
    """Verifica Tor usando tor_pool si disponible, o caída a tor_utils/manager."""
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


# --- Tor semaphore: máximo 1 petición Tor a la vez ---
TOR_SEMAPHORE = threading.Semaphore(1)

HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/125.0.0.0 Safari/537.36"),
    "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
}

# Perfiles semilla: organizadores conocidos de la escena psyclub/electrónica.
SEED_ORGANIZADORES_INSTAGRAM = [
    "https://www.instagram.com/psychonaut_nl/",
    "https://www.instagram.com/goapsy/",
    "https://www.instagram.com/dreamstate/",
    "https://www.instagram.com/psybient/",
    "https://www.instagram.com/psytadelic/",
    "https://www.instagram.com/anjunabeats/",
    "https://www.instagram.com/ministryofsound/",
    "https://www.instagram.com/beatport/",
    "https://www.instagram.com/psytrancetribe/",
    "https://www.instagram.com/etnicollective/",
    "https://www.instagram.com/promotype/",
    "https://www.instagram.com/foreignkeymusic/",
    "https://www.instagram.com/matsuri/",
    "https://www.instagram.com/spinmasterrecords/",
    "https://www.instagram.com/twowizards/",
]

PSY_HASHTAGS = [
    "psytrance", "goa", "darkpsy", "forestpsy", "hitech",
    "psychill", "psybient", "fullon", "progressive", "twilight",
]

ORGANIZER_KEYWORDS = [
    "organizer", "organizador", "promoter", "promotora",
    "booking", "management", "agency", "crew", "collective",
    "label", "records", "festival", "events",
    "promotion", "productora", "community", "comunidad",
    "night", "gathering", "team", "familia", "media",
]

NOISE_PATTERNS = [
    "login", "signup", "accounts/login",
    "/stories/", "/explore/locations/",
    "/explore/tags/", "/reels/", "/tv/",
]

_SUBGENEROS_IG = [
    "psytrance", "goa", "darkpsy", "forest", "forestpsy", "psychill",
    "psybient", "fullon", "progressive", "hitech", "twilight", "psycore",
    "suomisaundi", "zenon", "psychedelic", "psytribe", "hi tech", "psydance",
]
_KEYWORDS_EVENTO_IG = [
    "rave", "festival", "open air", "party", "gathering", "event",
    "ceremony", "full moon", "tribal", "aftermovie", "psyparty",
    "trance party", "trance festival",
]

def _es_relevante_ig(texto: str) -> bool:
    t = (texto or "").lower()
    if any(kw in t for kw in _SUBGENEROS_IG):
        return True
    if any(kw in t for kw in _KEYWORDS_EVENTO_IG):
        return True
    return False

_extractor = None
_anti = None


def _get_extractor():
    global _extractor
    if _extractor is None:
        try:
            from scrapers.event_extractor import EventExtractor
            _extractor = EventExtractor()
        except Exception:
            _extractor = None
    return _extractor


def _get_anti():
    global _anti
    if _anti is None:
        try:
            from scrapers.anti_block import get_anti_block
            _anti = get_anti_block()
        except Exception:
            _anti = None
    return _anti


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


# ---------------------------------------------------------------------------
# Helpers: Tor, Sessions
# ---------------------------------------------------------------------------
_sesion_tor = None
_busquedas_exitosas = 0
_bloqueo_detectado_en_dork = False


def _rotar_identidad_tor():
    """Rota la IP de Tor (pool) y resetea la sesión cacheada.

    Reinicia Tor vía pool de circuitos o subprocess, pausa 10s.
    Se llama cada 3 dorks o al detectar bloqueo.
    """
    global _sesion_tor, _busquedas_exitosas
    _sesion_tor = None
    _busquedas_exitosas = 0
    # Usar pool si disponible
    if _pool_rotar_tor is not None and _pool_siguiente_identidad is not None:
        try:
            idx = _pool_siguiente_identidad()
            return _pool_rotar_tor(idx)
        except Exception:
            pass
    # Usar tor_utils si disponible
    if rotar_identidad_tor_actualizado is not None:
        try:
            return rotar_identidad_tor_actualizado()
        except Exception:
            pass
    if renovar_identidad_tor is None:
        return False
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


def _es_bloqueo_ddg(status_code, texto=""):
    if status_code in (202, 403, 429):
        return True
    t = (texto or "")[:1500].lower()
    if "captcha" in t or "anonymous" in t or "not available" in t:
        return True
    return False


def _es_bloqueo_mojeek(r) -> bool:
    if r is None:
        return False
    if r.status_code in (403, 429, 503):
        return True
    texto = (r.text or "")[:2000].lower()
    if "captcha" in texto or "blocked" in texto or "access denied" in texto:
        return True
    return False


def _get_sesion():
    global _sesion_tor
    if _sesion_tor is not None:
        return _sesion_tor
    if _pool_obtener_sesion is not None:
        try:
            _sesion_tor = _pool_obtener_sesion()
            return _sesion_tor
        except Exception:
            pass
    if crear_sesion_busqueda is not None:
        try:
            _sesion_tor = crear_sesion_busqueda(usar_tor=True)
            return _sesion_tor
        except Exception:
            pass
    _sesion_tor = requests.Session()
    _sesion_tor.headers.update(HEADERS)
    return _sesion_tor


def _random_sleep():
    """Pause entre dorks usando configuración dinámica si disponible."""
    if STEALTH_AVAILABLE:
        _cfg = load_loop_config()
        pausa = _cfg.get("pausa_entre_dorks", list(SLEEP_BETWEEN))
    else:
        pausa = list(SLEEP_BETWEEN)
    time.sleep(random.uniform(pausa[0], pausa[1]))


def _ejecutar_fuera_del_loop(func):
    """Decorador: ejecuta `func` en un hilo separado si hay un event loop
    asyncio corriendo en el hilo actual (para Playwright Sync API)."""
    @functools.wraps(func)
    def _wrapper(*args, **kwargs):
        try:
            asyncio.get_running_loop()
            en_loop = True
        except RuntimeError:
            en_loop = False
        if en_loop:
            with ThreadPoolExecutor(max_workers=1) as pool:
                futuro = pool.submit(func, *args, **kwargs)
                return futuro.result()
        return func(*args, **kwargs)
    return _wrapper


# Necesario para _ejecutar_fuera_del_loop
import asyncio


# ---------------------------------------------------------------------------
# URL Classification for Instagram
# ---------------------------------------------------------------------------
IG_POST_RE = re.compile(r"instagram\.com/(p|reel|tv)/([\w.\-]+)")
IG_PROFILE_RE = re.compile(r"instagram\.com/([A-Za-z0-9_.]+)/?$")

def _es_post_instagram(url: str) -> bool:
    return bool(IG_POST_RE.search(url))

def _es_perfil_instagram(url: str) -> bool:
    if IG_POST_RE.search(url):
        return False
    return "instagram.com" in url and not any(
        x in url for x in ["/stories/", "/explore/", "/reels/", "/tv/"])


def _es_organizador_instagram(url: str) -> bool:
    m = IG_PROFILE_RE.search(url)
    if not m:
        return False
    username = m.group(1)
    if len(username) > 30:
        return False
    return True


def _limpiar_titulo_post_ig(titulo: str, snippet: str = "") -> str:
    texto = (titulo or "").strip()

    texto = re.sub(
        r"^\d[\d.,]*\s+likes?,?\s*\d[\d.,]*\s*comments?\s+[-:]\s*",
        "", texto, count=1, flags=re.IGNORECASE,
    )
    texto = re.sub(r"^\d[\d.,]*\s+likes?\s+[-:]?\s*", "", texto, count=1, flags=re.IGNORECASE)
    texto = re.sub(
        r"^(?:.*?\bon\s+(?:\w+\s+\d{1,2}[,.]?\s*\d{4}:))\s*",
        "", texto, count=1, flags=re.IGNORECASE,
    )
    texto = texto.strip(" -\t\n\r\"'")
    if " - " in texto:
        partes = [p.strip() for p in texto.split("-") if p.strip()]
        if partes:
            texto = partes[-1]
    texto = texto.strip(" \t\n\r\"'.")
    if texto and " " not in texto:
        candidato = texto.replace("_", " ").replace(".", " ").strip()
        if candidato and not re.search(r"\d{3,}", candidato):
            texto = " ".join(w.capitalize() for w in candidato.split() if w)
    if not texto or len(texto) < 5:
        frag = (snippet or "").strip()
        frag = re.sub(
            r"^\d[\d.,]*\s+likes?,?\s*\d[\d.,]*\s*comments?\s+[-:]\s*",
            "", frag, count=1, flags=re.IGNORECASE,
        )
        frag = frag.split("\n")[0].strip(" \"'")
        texto = frag if len(frag) >= 5 else (titulo or "").strip()
    return texto[:200]


def _extraer_autor_de_post(url: str) -> Optional[str]:
    """Extrae URL del autor de un post de Instagram usando meta tags."""
    with TOR_SEMAPHORE:
        sesion = _get_sesion()
        try:
            r = sesion.get(url, timeout=TIMEOUT_ENGINE)
        except Exception:
            return None
        if r.status_code >= 400 or r.status_code != 200:
            return None
        html = getattr(r, "text", "")
        soup = BeautifulSoup(html, "lxml")
        for sel in [
            "meta[property=\"og:article:author\"]",
            "meta[property=\"profile:meta\"]",
            "meta[name=\"description\"]",
            "meta[property=\"og:description\"]",
        ]:
            tag = soup.select_one(sel)
            if tag:
                content = tag.get("content", "")
                m = IG_PROFILE_RE.search(content)
                if m:
                    return f"https://www.instagram.com/{m.group(1)}/"
                m = re.search(r"instagram\.com/([A-Za-z0-9_.]+)", content)
                if m:
                    return f"https://www.instagram.com/{m.group(1)}/"
        _EXCLUIR = {
            "p", "reel", "reels", "tv", "stories", "explore", "share",
            "login", "about", "help", "jobs", "rsrc.php", "api", "developer",
            "4m", "direct", "accounts", "email", "password",
            "tags", "reels", "guide",
        }
        matches = re.findall(r"instagram\.com/([A-Za-z0-9_.]{1,30})(?:/|\"|\s)", html)
        for u in matches:
            ul = u.lower()
            if ul not in _EXCLUIR and "." not in ul:
                return f"https://www.instagram.com/{u}/"
    return None


# ---------------------------------------------------------------------------
# 1) Generación de dorks — CONSULTAS NATURALES (no site:)
# ---------------------------------------------------------------------------
def _cargar_config() -> Dict:
    ruta = Path(_PROJECT_ROOT) / "config_grupos.json"
    if not ruta.exists():
        return {"subgeneros": ["psytrance"], "paises": []}
    try:
        with open(ruta, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return {"subgeneros": ["psytrance"], "paises": []}


def _generar_dorks(config: Dict) -> List[str]:
    """Genera dorks con consultas NATURALES para Instagram posts + perfiles.

    En lugar de site:, usa consultas naturales. Limita a MAX_DORKS_POR_EJECUCION.
    """
    subgeneros = config.get("subgeneros", ["psytrance"])
    paises = config.get("paises", [])

    todas_ciudades = []
    for pais in paises:
        ciudades = pais.get("ciudades", [])
        for ciudad in ciudades:
            todas_ciudades.append({"ciudad": ciudad, "pais": pais["nombre"]})

    ciudad_names = [c["ciudad"] for c in todas_ciudades[:25]]

    dorks: List[str] = []

    # 1. Posts: hashtag + keyword evento (consultas naturales)
    for tag in PSY_HASHTAGS[:10]:
        dorks.append(f'"{tag}" instagram event')
        dorks.append(f'"{tag}" instagram party')
        dorks.append(f'"{tag}" instagram festival')

    # 2. Posts: subgénero + ciudad + instagram (naturale)
    for sub in subgeneros[:12]:
        for c in ciudad_names[:25]:
            dorks.append(f'"{sub}" "{c}" instagram event')
            dorks.append(f'"{sub}" "{c}" instagram party')
            dorks.append(f'"{sub}" "{c}" instagram festival')

    # 3. Posts: subgénero + keyword evento
    event_kws = ["festival", "rave", "open air", "gathering", "2026"]
    for kw in event_kws[:5]:
        for sub in subgeneros[:8]:
            dorks.append(f'"{sub}" "{kw}" instagram')

    # 4. Perfiles: subgénero + keyword organizador + instagram
    for sub in subgeneros[:10]:
        for org_kw in ORGANIZER_KEYWORDS[:8]:
            dorks.append(f'"{sub}" {org_kw} instagram')

    # 5. Perfiles: hashtag + organizador
    for tag in PSY_HASHTAGS[:6]:
        for org_kw in ORGANIZER_KEYWORDS[:4]:
            dorks.append(f'"{tag}" {org_kw} instagram')

    # 6. Ubicaciones/venues
    for tag in PSY_HASHTAGS[:5]:
        dorks.append(f'"{tag}" location instagram')
        dorks.append(f'"{tag}" venue instagram')

    # Dedup y shuffle
    vistos: Set[str] = set()
    unicos: List[str] = []
    for d in dorks:
        if d not in vistos:
            vistos.add(d)
            unicos.append(d)
    random.shuffle(unicos)

    # Rotation: load state, skip recently used
    estado = _leer_json(ESTADO_FILE, {"dorks_ejecutados": []}) if 'ESTADO_FILE' in dir() else {"dorks_ejecutados": []}
    ejecutados = set(estado.get("dorks_ejecutados", []))
    frescos = [d for d in unicos if d not in ejecutados]
    if len(frescos) < MAX_DORKS_POR_EJECUCION:
        estado["dorks_ejecutados"] = []
        frescos = list(unicos)
    random.shuffle(frescos)
    seleccionados = frescos[:MAX_DORKS_POR_EJECUCION]
    estado["dorks_ejecutados"] = list(ejecutados | set(seleccionados))
    _escribir_json(ESTADO_FILE, estado)
    return seleccionados


ESTADO_FILE = _STATE_DIR / "instagram_dorks_estado.json"


# ---------------------------------------------------------------------------
# 2) Search engines — SOLO DDG y Mojeek
# ---------------------------------------------------------------------------
def _buscar_ddg(dork: str) -> List[Dict]:
    """Busca en DuckDuckGo HTML (html.duckduckgo.com/html/?q=<dork>).

    GET con Tor. Parsea .result__a (URL) y .result__snippet (snippet).
    Timeout: 8s. Si hay bloqueo, rota Tor y salta al siguiente dork.
    """
    global _bloqueo_detectado_en_dork
    _bloqueo_detectado_en_dork = False
    anti = _get_anti()
    if anti is not None:
        anti.wait_if_needed("duckduckgo.com")
        if anti.is_blocked("duckduckgo.com"):
            _bloqueo_detectado_en_dork = True
            return []
    with TOR_SEMAPHORE:
        sesion = _get_sesion()
        try:
            r = sesion.get(
                "https://html.duckduckgo.com/html/",
                params={"q": dork},
                timeout=TIMEOUT_ENGINE,
            )
        except Exception:
            _bloqueo_detectado_en_dork = True
            return []
        if _es_bloqueo_ddg(r.status_code, getattr(r, "text", "")):
            _bloqueo_detectado_en_dork = True
            if _tor_ok and _tor_ok():
                _rotar_identidad_tor()
            elif anti is not None:
                anti.mark_blocked("duckduckgo.com")
            return []
        if anti is not None:
            anti.mark_success("duckduckgo.com")
        if r.status_code != 200:
            _bloqueo_detectado_en_dork = True
            return []
        soup = BeautifulSoup(r.text, "lxml")
        resultados = []
        for a in soup.select("a.result__a"):
            href = a.get("href", "")
            url = href
            if "uddg=" in href:
                try:
                    url = urllib.parse.unquote(
                        re.search(r"uddg=([^&]+)", href).group(1)
                    )
                except Exception:
                    url = href
            if "instagram.com" not in url:
                continue
            if any(p in url.lower() for p in NOISE_PATTERNS):
                continue
            titulo = a.get_text(strip=True)
            snippet = ""
            padre = a.find_parent("div", class_="result")
            if padre:
                snippet_el = padre.select_one(".result__snippet")
                if snippet_el:
                    snippet = snippet_el.get_text(strip=True)
            if not IG_POST_RE.search(url):
                continue
            resultados.append({
                "titulo": titulo, "url": url.split("?")[0].rstrip("/"),
                "snippet": snippet, "motor": "duckduckgo",
            })
            if len(resultados) >= MAX_POSTS_PER_DORK:
                break
        if resultados and _tor_ok and _tor_ok():
            global _busquedas_exitosas
            _busquedas_exitosas += 1
            if _busquedas_exitosas >= TOR_ROTATE_EVERY:
                _rotar_identidad_tor()
        return resultados


def _buscar_mojeek(dork: str) -> List[Dict]:
    """Busca en Mojeek (www.mojeek.com/search?q=<dork>).

    Selector a.ob (URL) y p.s (snippet). Timeout: 8s.
    """
    global _bloqueo_detectado_en_dork
    _bloqueo_detectado_en_dork = False
    anti = _get_anti()
    if anti is not None:
        anti.wait_if_needed("mojeek.com")
        if anti.is_blocked("mojeek.com"):
            _bloqueo_detectado_en_dork = True
            return []
    with TOR_SEMAPHORE:
        sesion = _get_sesion()
        try:
            r = sesion.get(
                "https://www.mojeek.com/search",
                params={"q": dork},
                timeout=TIMEOUT_ENGINE,
            )
        except Exception:
            _bloqueo_detectado_en_dork = True
            return []
        if _es_bloqueo_mojeek(r):
            _bloqueo_detectado_en_dork = True
            if _tor_ok and _tor_ok():
                _rotar_identidad_tor()
            elif anti is not None:
                anti.mark_blocked("mojeek.com")
            return []
        if anti is not None:
            anti.mark_success("mojeek.com")
        if r.status_code != 200:
            _bloqueo_detectado_en_dork = True
            return []
        soup = BeautifulSoup(r.text, "lxml")
        resultados = []
        for a in soup.select("a.ob"):
            href = a.get("href", "")
            url = href
            if not url.startswith("http"):
                continue
            if "instagram.com" not in url:
                continue
            if any(p in url.lower() for p in NOISE_PATTERNS):
                continue
            titulo = a.get_text(strip=True)
            snippet = ""
            padre = a.find_parent("li")
            if padre:
                snippet_el = padre.select_one("p.s")
                if snippet_el:
                    snippet = snippet_el.get_text(strip=True)
            if not IG_POST_RE.search(url):
                continue
            resultados.append({
                "titulo": titulo, "url": url.split("?")[0].rstrip("/"),
                "snippet": snippet, "motor": "mojeek",
            })
            if len(resultados) >= MAX_POSTS_PER_DORK:
                break
        return resultados


def _buscar_bing(dork: str) -> List[Dict]:
    """Busca en Bing HTML (www.bing.com/search?q=<dork>).

    Selector: li.b_algo h2 a (URL) y p (snippet). Timeout: 8s.
    """
    global _bloqueo_detectado_en_dork
    _bloqueo_detectado_en_dork = False
    anti = _get_anti()
    if anti is not None:
        anti.wait_if_needed("bing.com")
        if anti.is_blocked("bing.com"):
            _bloqueo_detectado_en_dork = True
            return []
    with TOR_SEMAPHORE:
        sesion = _get_sesion()
        try:
            r = sesion.get(
                "https://www.bing.com/search",
                params={"q": dork},
                timeout=TIMEOUT_ENGINE,
            )
        except Exception:
            _bloqueo_detectado_en_dork = True
            return []
        if r.status_code >= 429 or r.status_code == 403:
            _bloqueo_detectado_en_dork = True
            if _tor_ok and _tor_ok():
                _rotar_identidad_tor()
            elif anti is not None:
                anti.mark_blocked("bing.com")
            return []
        if anti is not None:
            anti.mark_success("bing.com")
        if r.status_code != 200:
            _bloqueo_detectado_en_dork = True
            return []
        soup = BeautifulSoup(r.text, "lxml")
        resultados = []
        for a in soup.select("li.b_algo h2 a"):
            href = a.get("href", "")
            url = href
            if not url.startswith("http"):
                continue
            if "instagram.com" not in url:
                continue
            if any(p in url.lower() for p in NOISE_PATTERNS):
                continue
            titulo = a.get_text(strip=True)
            snippet = ""
            padre = a.find_parent("li", class_="b_algo")
            if padre:
                snippet_el = padre.select_one("p")
                if snippet_el:
                    snippet = snippet_el.get_text(strip=True)
            if not IG_POST_RE.search(url):
                continue
            resultados.append({
                "titulo": titulo, "url": url.split("?")[0].rstrip("/"),
                "snippet": snippet, "motor": "bing",
            })
            if len(resultados) >= MAX_POSTS_PER_DORK:
                break
        if resultados and _tor_ok and _tor_ok():
            global _busquedas_exitosas
            _busquedas_exitosas += 1
            if _busquedas_exitosas >= TOR_ROTATE_EVERY:
                _rotar_identidad_tor()
        return resultados


# ---------------------------------------------------------------------------
# 3) Google search via Playwright + Tor (persistent context + stealth + gbv=1)
# ---------------------------------------------------------------------------
def _google_search_playwright_ig(dork: str, indice_tor: Optional[int] = None,
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
            dork, "ig", max_retries=3, indice_tor=indice_tor, timeout=timeout)
    except Exception as e:
        print(f"    ⚠️ Google Playwright IG error: {e}")
        return None


def _parse_google_results_ig(html: str) -> List[Dict]:
    """Parsea resultados de Google (div.g) filtrando solo instagram.com."""
    resultados: List[Dict] = []
    if not html:
        return resultados
    try:
        soup = BeautifulSoup(html, "lxml")
    except Exception:
        soup = BeautifulSoup(html, "html.parser")
    for g in soup.find_all("div", class_="g"):
        a = g.find("a", href=True)
        if not a:
            continue
        href = a["href"]
        if href.startswith("/url?q="):
            m = re.search(r"/url\?q=([^&]+)", href)
            if m:
                href = urllib.parse.unquote(m.group(1))
        elif href.startswith("/url?"):
            m = re.search(r"url=([^&]+)", href)
            if m:
                href = urllib.parse.unquote(m.group(1))
        if not href.startswith("http"):
            continue
        if "instagram.com" not in href.lower():
            continue
        if any(p in href.lower() for p in NOISE_PATTERNS):
            continue
        titulo = a.get_text(strip=True)
        h3 = g.find("h3")
        if h3:
            titulo = h3.get_text(strip=True)
        snippet_el = (g.find("span", class_="st")
                      or g.find("div", attrs={"data-sncf": True})
                      or g.find("div", class_="VwiC3b"))
        snippet = snippet_el.get_text(strip=True) if snippet_el else ""
        if not IG_POST_RE.search(href) and not IG_PROFILE_RE.search(href):
            continue
        # Filter out noise URLs
        lower_url = href.lower()
        if any(p in lower_url for p in NOISE_PATTERNS):
            continue
        resultados.append({
            "titulo": titulo[:200],
            "url": href.split("?")[0].rstrip("/"),
            "snippet": snippet,
            "motor": "google",
        })
        if len(resultados) >= MAX_POSTS_PER_DORK:
            break
    return resultados


    def _buscar_google_con_reintentos_ig(dork: str) -> List[Dict]:

    """Busca en Google usando intentos priorizados.

    Orden: Firefox + Playwright -> SearxNG publica -> requests por Tor.
    Rotacion Tor + cookies. Pausas dinamicas.
    Max 3 intentos por dork.
    Returns resultados de Instagram (posts + perfiles).
    """
    global _bloqueo_detectado_en_dork

    # Load dynamic config
    _cfg = load_loop_config() if STEALTH_AVAILABLE else {}
    max_reintentos = _cfg.get("max_reintentos", MAX_REINTENTOS_POR_DORK)
    retry_pause = _cfg.get("pausa_entre_reintentos", [45, 90])

    # Rotate Tor + cookies BEFORE each dork
    if _verificar_tor_ig():
        print(f"    🔄 Rotando Tor + cookies antes del dork...")
        if STEALTH_AVAILABLE:
            _rotate_tor_and_cookies()
        else:
            _rotar_identidad_tor()

    resultados: List[Dict] = []
    for intento in range(max_reintentos):
        # Usar busqueda priorizada (Firefox -> SearxNG -> requests)
        html = google_search_prioritario(dork, "ig", indice_tor=None, timeout=20)
        if html:
            low = html.lower()
            if ("unusual traffic" in low or "captcha" in low
                    or "recaptcha" in low
                    or "our systems have detected" in low
                    or "consent.google.com" in low
                    or "/sorry/" in low):
            _bloqueo_detectado_en_dork = True
            resultados = _parse_google_results_ig(html)
            if resultados:
                _bloqueo_detectado_en_dork = False
                return resultados
            _bloqueo_detectado_en_dork = True
        else:
            _bloqueo_detectado_en_dork = True

        if intento < max_reintentos - 1:
            wait = random.uniform(retry_pause[0], retry_pause[1])
            print(f"    ⏳ Pausa {wait:.0f}s entre reintentos...")
            time.sleep(wait)
            if _verificar_tor_ig():
                print(f"    🔄 Rotando Tor + cookies antes de reintento {intento+2}...")
                if STEALTH_AVAILABLE:
                    _rotate_tor_and_cookies()
                else:
                    _rotar_identidad_tor()
                time.sleep(random.uniform(5, 10))
            else:
                break
    return resultados
def _extraer_evento_ig_post(url: str, titulo: str, snippet: str) -> Optional[Dict]:
    """Visita un post de Instagram (público, sin login) y extrae datos via requests."""
    extractor = _get_extractor()
    anti = _get_anti()
    if anti is not None:
        anti.wait_if_needed("instagram.com")
    with TOR_SEMAPHORE:
        sesion = _get_sesion()
        try:
            r = sesion.get(url, timeout=TIMEOUT_ENGINE)
            if anti is not None:
                if r.status_code >= 400:
                    anti.mark_blocked("instagram.com")
                    return None
                anti.mark_success("instagram.com")
            if r.status_code != 200:
                return None
        except Exception:
            return None

    html = getattr(r, "text", "")
    soup = BeautifulSoup(html, "lxml")

    caption = ""
    meta_desc = soup.select_one('meta[property="og:description"]')
    if meta_desc:
        caption = meta_desc.get("content", "")
    meta_title = soup.select_one('meta[property="og:title"]')
    titulo_meta = meta_title.get("content", "") if meta_title else ""

    json_ld = soup.select_one('script[type="application/ld+json"]')
    if json_ld:
        try:
            data = json.loads(json_ld.string)
            if isinstance(data, dict):
                caption = data.get("articleBody", caption)
                titulo_meta = data.get("headline", titulo_meta)
        except Exception:
            pass

    if not caption:
        caption = snippet or titulo

    if not caption or len(caption) < 10:
        return None

    if not _es_relevante_ig(f"{titulo}\n{caption}"):
        return None

    lugar = "N/A"
    loc_match = re.search(r"\u00F6\s*(.+?)(?:\n|$|@|#)", caption) or \
               re.search(r"📍\s*(.+?)(?:\n|$|@|#)", caption)
    if loc_match:
        lugar = loc_match.group(1).strip()
    else:
        loc_match = re.search(r"at\s+([A-Z][\w\s]+?)(?:\s*[-–|,.\n]|\s*$)", caption)
        if loc_match:
            lugar = loc_match.group(1).strip()

    fecha = "N/A"
    fecha_match = re.search(r"(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{2,4})", caption)
    if fecha_match:
        d, m, y = fecha_match.groups()
        if len(y) == 2:
            y = "20" + y
        fecha = f"{y}-{int(m):02d}-{int(d):02d}"

    if extractor is not None:
        eventos = extractor.extract_all(caption, "Instagram (Dorks)", url)
        if eventos:
            ev = eventos[0]
            nombre_ev = (ev.get("nombre") or "").strip()
            nombre_limpio = _limpiar_titulo_post_ig(
                nombre_ev if nombre_ev and len(nombre_ev) >= 5 else titulo,
                snippet,
            )
            return {
                "nombre": nombre_limpio,
                "fecha": ev.get("fecha") or fecha,
                "lugar": ev.get("lugar") or lugar,
                "ciudad": ev.get("ciudad") or "N/A",
                "pais": ev.get("pais") or "N/A",
                "continente": ev.get("continente") or "N/A",
                "subcontinente": ev.get("subcontinente") or "N/A",
                "tipo_lugar": ev.get("tipo_lugar") or "N/A",
                "fuente": "Instagram (Dorks)",
                "organizador": ev.get("organizador") or "N/A",
                "email": ev.get("email") or "N/A",
                "link": url,
                "subgenero": ev.get("subgenero") or "general",
                "descripcion": caption[:500],
            }

    if fecha != "N/A":
        return {
            "nombre": _limpiar_titulo_post_ig(titulo_meta or titulo, caption),
            "fecha": fecha,
            "lugar": lugar,
            "ciudad": "N/A",
            "pais": "N/A",
            "continente": "N/A",
            "subcontinente": "N/A",
            "tipo_lugar": "N/A",
            "fuente": "Instagram (Dorks)",
            "organizador": "N/A",
            "email": "N/A",
            "link": url,
            "subgenero": "general",
            "descripcion": caption[:500],
        }

    return None


# ---------------------------------------------------------------------------
# 4) Event extraction from Instagram profiles (Playwright)
# ---------------------------------------------------------------------------
@_ejecutar_fuera_del_loop
def _extraer_eventos_de_perfil_nuevo(url: str) -> List[Dict]:
    """Usa Playwright para visitar un perfil de Instagram y extraer eventos
    de sus últimas publicaciones."""
    extractor = _get_extractor()
    anti = _get_anti()

    if anti is not None:
        anti.wait_if_needed("instagram.com")

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return []

    eventos: List[Dict] = []

    try:
        with sync_playwright() as p:
            proxy = None
            if _tor_ok and _tor_ok():
                proxy = {"server": "socks5://127.0.0.1:9050"}

            browser = p.chromium.launch(headless=True, proxy=proxy)
            context = browser.new_context(
                user_agent=("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                           "AppleWebKit/537.36 (KHTML, like Gecko) "
                           "Chrome/125.0.0.0 Safari/537.36"),
                locale="es-ES",
            )
            page = context.new_page()
            page.set_default_timeout(PLAYWRIGHT_TIMEOUT * 1000)

            try:
                page.goto(url, wait_until="domcontentloaded",
                          timeout=PLAYWRIGHT_TIMEOUT * 1000)
            except Exception:
                browser.close()
                return []

            # Check for login wall / private account
            try:
                if page.locator('text="Log in"').count() > 0 or \
                   page.locator('text="Iniciar sesión"').count() > 0:
                    browser.close()
                    return []
            except Exception:
                pass

            # Extraer biografía del perfil
            bio_text = ""
            try:
                meta = page.locator('meta[name="description"]')
                if meta.count() > 0:
                    bio_text = meta.first.get_attribute("content") or ""
                if not bio_text:
                    bio_text = page.evaluate(
                        "() => document.querySelector('meta[property=\"og:description\"]')?.content || ''"
                    )
            except Exception:
                bio_text = ""
            email_bio = _extraer_email_organizador(bio_text, requerir_keywords=False) if bio_text else None

            # Registrar perfil como organizador potencial
            try:
                from urllib.parse import urlparse
                path_seg = [s for s in urlparse(url).path.split("/") if s]
                uname = path_seg[0] if path_seg else "unknown"
                _guardar_organizador_instagram(uname, url, "dorks_instagram", email_bio)
            except Exception:
                pass

            # Wait for posts to load
            try:
                page.wait_for_selector('article', timeout=15000)
            except Exception:
                pass

            # Scroll to load more posts
            for _ in range(2):
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                time.sleep(1.0)

            # Get post links from the profile - limit to 8 recent posts
            post_links = page.evaluate("""
                () => {
                    const links = [];
                    document.querySelectorAll('a[href*="/p/"], a[href*="/reel/"]').forEach(a => {
                        const href = a.href;
                        if (href && !links.includes(href)) links.push(href);
                    });
                    return links.slice(0, 8);
                }
            """)

            browser.close()

            posts_procesados = 0
            for post_url in post_links:
                if posts_procesados >= 5:
                    break
                if any(e.get("link") == post_url for e in eventos):
                    continue
                ev = _extraer_evento_ig_post(post_url, "", "")
                if ev:
                    ev["_fuente_perfil"] = url
                    eventos.append(ev)
                posts_procesados += 1

    except Exception as e:
        print(f"    ⚠️ Perfil IG profile phase error: {e}")

    if eventos:
        eventos_existentes = []
        if EVENTOS_PERFILES_INSTAGRAM_FILE.exists():
            try:
                with open(EVENTOS_PERFILES_INSTAGRAM_FILE, "r", encoding="utf-8") as f:
                    eventos_existentes = json.load(f)
            except (json.JSONDecodeError, IOError):
                eventos_existentes = []
        links_existentes = set(e.get("link") for e in eventos_existentes)
        for ev in eventos:
            if ev.get("link") not in links_existentes:
                eventos_existentes.append(ev)
        _escribir_json(EVENTOS_PERFILES_INSTAGRAM_FILE, eventos_existentes)

    return eventos


# ---------------------------------------------------------------------------
# Email extraction from organizer contexts
# ---------------------------------------------------------------------------
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")

def _extraer_email_organizador(texto: str, requerir_keywords: bool = True) -> Optional[str]:
    """Extrae email del contexto del organizador/host."""
    email_patterns = [
        r'"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}"',
        r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b",
    ]
    if requerir_keywords:
        organizer_kw = ["organizer", "organizador", "promoter", "booking", "management"]
        texto_lower = texto.lower()
        es_organizador = any(kw in texto_lower for kw in organizer_kw)
        if not es_organizador:
            return None
    for pattern in email_patterns:
        m = re.search(pattern, texto)
        if m:
            email = m.group(0).strip('"')
            if "@" in email and len(email.split("@")[1]) > 2:
                return email
    return None


# ---------------------------------------------------------------------------
# Organizer profile management
# ---------------------------------------------------------------------------
def _cargar_organizadores_instagram() -> Dict[str, Dict]:
    """Carga la lista de organizadores de Instagram ya descubiertos."""
    if ORGANIZADORES_INSTAGRAM_FILE.exists():
        try:
            with open(ORGANIZADORES_INSTAGRAM_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return {}
    return {}


def _guardar_organizador_instagram(username: str, url: str, fuente: str = "dorks_instagram",
                                   email: Optional[str] = None) -> None:
    """Añade un organizador a la lista (sin duplicados)."""
    organizadores = _cargar_organizadores_instagram()
    if username in organizadores:
        if url and url not in organizadores[username].get("urls", []):
            organizadores[username].setdefault("urls", []).append(url)
        if email and not organizadores[username].get("email"):
            organizadores[username]["email"] = email
        fuentes = set(organizadores[username].get("fuentes", []))
        fuentes.add(fuente)
        organizadores[username]["fuentes"] = list(fuentes)
        organizadores[username]["total_hallazgos"] = int(organizadores[username].get("total_hallazgos", 0)) + 1
    else:
        organizadores[username] = {
            "username": username,
            "url": url,
            "fuentes": [fuente],
            "email": email or "N/A",
            "total_hallazgos": 1,
        }
    urls = organizadores[username].get("urls", [])
    if len(urls) > 10:
        urls = urls[-10:]
    organizadores[username]["urls"] = urls
    _escribir_json(ORGANIZADORES_INSTAGRAM_FILE, organizadores)


def _marcar_perfil_visitado(username: str, url: str) -> None:
    organizadores = _cargar_organizadores_instagram()
    if username in organizadores:
        data = organizadores[username]
        data["ultima_visita"] = datetime.now(timezone.utc).isoformat()
        data["visitas"] = int(data.get("visitas", 0)) + 1
        if url and url not in data.get("urls", []):
            data.setdefault("urls", []).append(url)
        _escribir_json(ORGANIZADORES_INSTAGRAM_FILE, organizadores)


def _ejecutar_fase_perfiles(fin: Optional[float] = None,
                            rondas: int = 2,
                            max_perfiles: int = 5) -> List[Dict]:
    """Fase de perfiles con LOOP: visita perfiles descubiertos, rotando Tor
    entre rondas y reintentando los que no devuelven eventos."""
    organizadores = _cargar_organizadores_instagram()
    if not organizadores:
        print(f"  🌱 Fase perfiles: sin organizadores descubiertos; "
              f"sembrando {len(SEED_ORGANIZADORES_INSTAGRAM)} perfiles conocidos...")
        for perfil_url in SEED_ORGANIZADORES_INSTAGRAM:
            m = IG_PROFILE_RE.search(perfil_url)
            if m:
                _guardar_organizador_instagram(m.group(1), perfil_url, fuente="seed")
        organizadores = _cargar_organizadores_instagram()
    if not organizadores:
        return []

    candidatos: List[Tuple[str, str, str]] = []
    for username, data in organizadores.items():
        url = data.get("url")
        if not url:
            continue
        candidatos.append((url, username, data.get("ultima_visita", "")))
    candidatos.sort(key=lambda x: (1 if x[2] else 0, x[2]))
    candidatos = candidatos[:max_perfiles]

    if not candidatos:
        return []

    print(f"  👥 Fase de perfiles: {len(candidatos)} candidato(s), loop {rondas} ronda(s)...")
    eventos_perfiles: List[Dict] = []
    procesados: Set[str] = set()

    for ronda in range(rondas):
        if fin is not None and time.time() > fin:
            print("  ⏰ Instagram Dorks: timeout en fase de perfiles")
            break
        if ronda > 0 and _tor_ok and _tor_ok():
            print(f"  🔄 Fase perfiles ronda {ronda+1}/{rondas}: renovando identidad Tor...")
            _rotar_identidad_tor()

        for perfil_url, username, _u in candidatos:
            if fin is not None and time.time() > fin:
                break
            if perfil_url in procesados:
                continue

            evs = _extraer_eventos_de_perfil_nuevo(perfil_url)
            if not evs and _tor_ok and _tor_ok():
                print(f"  🔁 Reintento perfil {username} con nueva identidad Tor...")
                _rotar_identidad_tor()
                evs = _extraer_eventos_de_perfil_nuevo(perfil_url)

            procesados.add(perfil_url)
            _marcar_perfil_visitado(username, perfil_url)

            if evs:
                eventos_perfiles.extend(evs)
                print(f"  ✅ Perfil {username}: {len(evs)} eventos")

        if len(procesados) >= len(candidatos):
            break

    return eventos_perfiles


# ---------------------------------------------------------------------------
# 5) Loop de mejora
# ---------------------------------------------------------------------------
def _registrar_dork(dork: str, eventos: int, urls_visitadas: int, tiempo_s: float) -> None:
    rend = _leer_json(RENDIMIENTO_FILE, {})
    entry = rend.get(dork, {
        "dork": dork, "runs": 0, "eventos": 0,
        "urls_visitadas": 0, "tiempo_s": 0.0,
    })
    entry["runs"] = int(entry.get("runs", 0)) + 1
    entry["eventos"] = int(entry.get("eventos", 0)) + eventos
    entry["urls_visitadas"] = int(entry.get("urls_visitadas", 0)) + urls_visitadas
    entry["tiempo_s"] = round(float(entry.get("tiempo_s", 0)) + tiempo_s, 2)
    entry["ultimo_run"] = datetime.now(timezone.utc).isoformat()
    rend[dork] = entry
    _escribir_json(RENDIMIENTO_FILE, rend)

    prod = _leer_json(PRODUCTIVOS_FILE, [])
    prod_map = {p.get("dork"): p for p in prod if isinstance(p, dict)}
    if eventos >= 1:
        prod_map[dork] = {
            "dork": dork,
            "eventos": int(prod_map.get(dork, {}).get("eventos", 0)) + eventos,
            "ultima_visita": datetime.now(timezone.utc).isoformat(),
        }
    _escribir_json(PRODUCTIVOS_FILE, list(prod_map.values()))


def _dorks_con_prioridad(dorks: List[str]) -> List[str]:
    prod = _leer_json(PRODUCTIVOS_FILE, [])
    prod_dorks = set()
    for p in prod:
        if isinstance(p, dict) and p.get("dork"):
            prod_dorks.add(p["dork"])
    priorizados = [d for d in dorks if d in prod_dorks]
    resto = [d for d in dorks if d not in prod_dorks]
    random.shuffle(resto)
    return priorizados + resto


# ---------------------------------------------------------------------------
# 6) Entrada principal
# ---------------------------------------------------------------------------
def scrape_instagram_dorks(timeout: int = TIMEOUT_TOTAL, limite: Optional[int] = None,
                           rondas: int = 2) -> List[Dict]:
    """Búsqueda de eventos de Instagram vía Dorks (Google SOLO via Playwright + Tor).

    Presupuesto: timeout → 60% búsqueda, 40% fase de perfiles.
    Rotación Tor + cookies antes de cada dork.
    Aditivo: nunca lanza excepciones al caller.
    Carga configuración dinámica desde dorks_config_loop.json.
    """
    # Load dynamic configuration
    _cfg = load_loop_config() if STEALTH_AVAILABLE else {}
    _timeout = int(_cfg.get("timeout_total", timeout))
    _max_dorks = _cfg.get("max_dorks", MAX_DORKS_POR_EJECUCION)
    _limite = limite if limite and limite > 0 else _max_dorks

    fin = time.time() + _timeout
    tor_status = "Tor ✓" if _verificar_tor_ig() else "directo"
    print(f"\U0001F50D Instagram Dorks: Posts + Perfiles [{tor_status}] "
          f"— {rondas} rondas...")
    eventos: List[Dict] = []

    global _bloqueo_detectado_en_dork

    try:
        # Presupuesto de tiempo: 60% búsqueda, 40% fase de perfiles
        _TIEMPO_PERFILES = max(int(timeout * 0.4), 60)
        _TIEMPO_DORKS = max(int(timeout * 0.6), 20)
        ahora = time.time()
        fin_dorks = ahora + _TIEMPO_DORKS
        fin_perfiles = min(ahora + timeout, fin_dorks + _TIEMPO_PERFILES)

        config = _cargar_config()
        dorks_pool = _generar_dorks(config)
        dorks_pool = _dorks_con_prioridad(dorks_pool)

        dorks_pool = dorks_pool[:_limite]

        if not dorks_pool:
            print("  ⚠️ Instagram Dorks: sin dorks para procesar")
            return []

        print(f"  Dorks: {len(dorks_pool)} | Presupuesto búsqueda: {_TIEMPO_DORKS}s | perfiles: {_TIEMPO_PERFILES}s")

        urls_procesadas: Set[str] = set()
        total_dorks = 0

        for ronda in range(rondas):
            if time.time() > fin_dorks:
                print("  ⏰ Instagram Dorks: timeout en búsqueda (rindiera para fase de perfiles)")
                break

            if ronda > 0 and _tor_ok and _tor_ok():
                print(f"  🔄 Ronda {ronda+1}/{rondas}: renovando identidad Tor...")
                _rotar_identidad_tor()

            for dork in dorks_pool:
                if time.time() > fin_dorks:
                    print("  ⏰ Instagram Dorks: timeout en búsqueda (rindiera para fase de perfiles)")
                    break

                t0 = time.time()
                _bloqueo_detectado_en_dork = False

                # Rotate Tor identity every TOR_ROTATE_EVERY dorks
                if total_dorks > 0 and total_dorks % TOR_ROTATE_EVERY == 0:
                    if _tor_ok and _tor_ok():
                        print(f"  🔄 Dorks: rotando Tor cada {TOR_ROTATE_EVERY} dorks...")
                        _rotar_identidad_tor()

                # If previous dork detected bloqueo, rotate Tor before this one
                if _bloqueo_detectado_en_dork and _tor_ok and _tor_ok():
                    print("  🔄 Dorks: bloqueo detectado, rotando Tor...")
                    _rotar_identidad_tor()
                    _bloqueo_detectado_en_dork = False

                # Google SOLO: Playwright + Tor, reintentos con rotación
                resultados: List[Dict] = _buscar_google_con_reintentos_ig(dork)

                total_dorks += 1
                evs_dork = 0

                for res in resultados:
                    url = res["url"]
                    if url in urls_procesadas:
                        continue
                    urls_procesadas.add(url)

                    if _es_post_instagram(url):
                        ev = _extraer_evento_ig_post(url, res.get("titulo", ""),
                                                     res.get("snippet", ""))
                        if ev:
                            eventos.append(ev)
                            evs_dork += 1
                        autor_url = _extraer_autor_de_post(url)
                        if autor_url and _es_organizador_instagram(autor_url):
                            m_a = IG_PROFILE_RE.search(autor_url)
                            if m_a:
                                _guardar_organizador_instagram(
                                    m_a.group(1), autor_url, fuente="post_author")
                    elif _es_perfil_instagram(url):
                        m = IG_PROFILE_RE.search(url)
                        username = m.group(1) if m else "unknown"
                        _guardar_organizador_instagram(username, url, "dorks_instagram")

                    if time.time() > fin_dorks:
                        break

                _registrar_dork(dork, evs_dork, len(resultados),
                               round(time.time() - t0, 2))

                if evs_dork > 0:
                    print(f"  ✅ Dork [{total_dorks}] (r{ronda+1}): {len(resultados)} URLs, "
                          f"{evs_dork} eventos — {dork[:60]}")

                _random_sleep()

        # ---- FASE DE PERFILES (loop con reintento + rotación Tor) ----
        if time.time() >= fin_perfiles:
            print("  ⏳ Búsqueda consumió el presupuesto; forzando fase de perfiles...")
            fin_perfiles = time.time() + min(_TIEMPO_PERFILES, 90)

        eventos_perfiles = _ejecutar_fase_perfiles(fin=fin_perfiles, rondas=2, max_perfiles=5)
        if eventos_perfiles:
            eventos.extend(eventos_perfiles)

        # Dedup local por URL
        vistos: Set[str] = set()
        unicos: List[Dict] = []
        for ev in eventos:
            k = ev.get("link") or ev.get("nombre") or ""
            if k and k not in vistos:
                vistos.add(k)
                unicos.append(ev)
        eventos = unicos

    except Exception as e:
        print(f"  ⚠️ Instagram Dorks: {type(e).__name__}: {e}")
        return []

    print(f"  📊 Instagram Dorks: {len(eventos)} eventos de {total_dorks} dorks")
    return eventos


if __name__ == "__main__":
    t0 = time.time()
    evs = scrape_instagram_dorks()
    print(f"Tiempo: {time.time() - t0:.0f}s, Eventos IG Dorks: {len(evs)}")
    for e in evs[:5]:
        print(f"  {e['nombre'][:50]} | {e.get('fecha', '?')} | {e.get('lugar', '?')}")
