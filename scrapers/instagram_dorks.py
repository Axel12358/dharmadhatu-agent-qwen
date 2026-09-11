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

        google_search_playwright_with_retry,

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
# ─────────────────────────────────────────────────────────────────────────────
# FIX: _buscar_google_con_reintentos_ig como función de NIVEL MÓDULO
# (antes estaba anidada dentro de _parse_google_results_ig — des-indentada aquí)
# ─────────────────────────────────────────────────────────────────────────────

def _buscar_google_con_reintentos_ig(dork: str, max_reintentos: int = 2) -> list:
    """Busca en Google vía Playwright stealth (si disponible).

    Retorna lista de resultados crudos [{titulo,url,snippet,motor}] o [].
    """
    if not STEALTH_AVAILABLE:
        return []
    for intento in range(max_reintentos):
        try:
            html = _google_search_playwright_ig(dork)
            if html:
                return _parse_google_results_ig(html)
        except Exception as e:
            print(f"  ⚠️  Google IG intento {intento+1}/{max_reintentos} falló: {e}")
        _random_sleep()
    return []


# ─────────────────────────────────────────────────────────────────────────────
# FUNCIÓN PÚBLICA — descubierta por core/plugin_loader.py
# ─────────────────────────────────────────────────────────────────────────────

def scrape_instagram_dorks(
    timeout: int = 180,
    rotar_tor: bool = True,
    max_dorks: int = 10,
    **kwargs,
) -> list:
    """Scraper público de Instagram vía dorks (DDG → Mojeek → Google stealth).

    timeouts
    --------
    timeout   : presupuesto de tiempo total en segundos (default 180).
    rotar_tor : rotar identidad Tor entre motores (default True).
    max_dorks : cuántos dorks procesar por ejecución (default 10).

    Retorna lista de dicts con las 13 columnas del CSV. Siempre aditivo:
    nunca elimina filas existentes de eventos_encontrados.csv.
    """
    import os
    import time
    from pathlib import Path
    from core.csv_lock import csv_locked_rows

    t0 = time.time()
    eventos_nuevos: list = []

    ESTADO_DIR = os.path.join(os.path.dirname(__file__), "instagram_dorks")
    os.makedirs(ESTADO_DIR, exist_ok=True)
    URLS_VISTAS_PATH = Path(ESTADO_DIR) / "urls_vistas.json"
    urls_vistas: set = set(_leer_json(URLS_VISTAS_PATH, default=[]))

    try:
        config = _cargar_config()
    except Exception as e:
        print(f"⚠️  instagram_dorks: no se pudo cargar config: {e}")
        config = {}

    try:
        _get_sesion()          # valida que Tor/proxy responda (adapta al flag)
        _get_anti()
    except Exception as e:
        print(f"❌ instagram_dorks: sesión/anti-block falló: {e}")
        return []

    try:
        dorks = _generar_dorks(config)[:max_dorks]
    except Exception as e:
        print(f"❌ instagram_dorks: _generar_dorks falló: {e}")
        return []

    if not dorks:
        print("ℹ️  instagram_dorks: sin dorks generados (bloqueo anterior ?).")
        return []

    print(f"🔍 instagram_dorks: {len(dorks)} dorks — presupuesto {timeout}s")

    for dork in dorks:
        if time.time() - t0 > timeout:
            print("⏱️  instagram_dorks: presupuesto agotado, saliendo.")
            break

        print(f"  🔎 dork: {dork[:80]}")
        items: list = []

        # DDG
        try:
            r = _buscar_ddg(dork)
            if r:
                items.extend(r)
            else:
                print("  🚫 DDG no devolvió resultados (bloqueo o sin hits)")
        except Exception as e:
            print(f"  ⚠️  DDG error: {e}")

        _random_sleep()

        # Mojeek (si DDG no dio suficiente)
        if len(items) < 3:
            try:
                r = _buscar_mojeek(dork)
                if r:
                    items.extend(r)
            except Exception as e:
                print(f"  ⚠️  Mojeek error: {e}")

        _random_sleep()

        # Google Playwright (solo si stealth disponible y aún hay tiempo)
        if STEALTH_AVAILABLE and time.time() - t0 < timeout - 20:
            try:
                items.extend(_buscar_google_con_reintentos_ig(dork))
            except Exception as e:
                print(f"  ⚠️  Google stealth error: {e}")

        # Dedup local por url dentro del mismo dork
        vistos_dork = set()
        unicos = []
        for it in items:
            u = (it.get("url") or "").rstrip("/")
            if u and u not in vistos_dork:
                vistos_dork.add(u)
                unicos.append(it)
        items = unicos

        for item in items:
            if time.time() - t0 > timeout:
                break
            url = (item.get("url") or "").rstrip("/")
            titulo = item.get("titulo", "")
            snippet = item.get("snippet", "")

            if not url or url in urls_vistas:
                continue

            texto_rapido = f"{titulo} {snippet}"
            if not _es_relevante_ig(texto_rapido):
                urls_vistas.add(url)
                continue

            if _es_post_instagram(url):
                # Post/reel: se registra como hallazgo con título limpio.
                nombre = _limpiar_titulo_post_ig(titulo, snippet)
                try:
                    autor = _extraer_autor_de_post(url)
                except Exception:
                    autor = None
                if not nombre or not _es_relevante_ig(nombre):
                    urls_vistas.add(url)
                    continue
                fila = {
                    "nombre": nombre[:120],
                    "fecha": "", "lugar": "", "pais": "", "continente": "",
                    "subcontinente": "",
                    "fuente": "instagram_dorks",
                    "organizador": (author_to_organizer(autor) if autor else ""),
                    "email": "", "link": url,
                    "subgenero": "", "tipo_lugar": "", "contactos": "",
                }
            elif _es_perfil_instagram(url) or _es_organizador_instagram(url):
                # Perfil/organizador: registrar pero no como evento
                print(f"  👤 perfil/organizador: {url[:60]}")
                urls_vistas.add(url)
                continue
            else:
                urls_vistas.add(url)
                continue

            try:
                with csv_locked_rows("eventos_encontrados.csv") as (filas, _fn):
                    links = {f.get("link", "") for f in filas}
                    if fila["link"] not in links:
                        filas.append(fila)
                        eventos_nuevos.append(fila)
                        print(f"  ✅ nuevo evento IG: {fila['nombre'][:50]}")
            except Exception as e:
                print(f"  ❌ CSV lock error: {e}")

            urls_vistas.add(url)

        # Rotar identidad Tor entre dorks
        if rotar_tor:
            try:
                _rotar_identidad_tor()
            except Exception:
                pass

    try:
        _escribir_json(URLS_VISTAS_PATH, sorted(urls_vistas))
    except Exception as e:
        print(f"⚠️  instagram_dorks: no se guardó urls_vistas: {e}")

    elapsed = round(time.time() - t0, 1)
    print(f"🏁 instagram_dorks: {len(eventos_nuevos)} nuevos en {elapsed}s")
    return eventos_nuevos


def author_to_organizer(autor_url: str) -> str:
    """Convierte una URL de perfil de Instagram en nombre legible de organizador."""
    a = (autor_url or "").rstrip("/")
    if a and "/" in a:
        return a.rstrip("/").split("/")[-1].replace("_", " ").strip().title()
    return a or ""


# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import json
    print("🧪 Corriendo instagram_dorks directamente…")
    resultados = scrape_instagram_dorks(timeout=180)
    print(f"\n📦 Eventos encontrados: {len(resultados)}")
    for ev in resultados[:5]:
        print(json.dumps(ev, ensure_ascii=False, indent=2))