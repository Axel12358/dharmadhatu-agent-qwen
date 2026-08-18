#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Scraper de canales públicos de Telegram (Dharmadhatu Bot v5) — SIN API token.

Estrategia validada con herramientas open-source:
- Búsqueda de canales: DuckDuckGo (endpoint HTML) con `site:t.me/s/` +
  combinaciones de subgénero+ciudad de `config_grupos.json`.
  Fuentes de referencia:
    • ulinycoin/shadow-tg  (https://github.com/ulinycoin/shadow-tg) — MIT,
      usa el mismo patrón: t.me/s/{channel} + búsqueda DDG site:t.me/s/.
    • Kisspeace/accless-tg-scraper (https://github.com/kisspeace/accless-tg-scraper)
    • PythonicCafe/tchan (https://github.com/PythonicCafe/tchan)
- Scraping de mensajes: `requests` + `BeautifulSoup` sobre la vista web
  pública `https://t.me/s/<canal>` (HTML estático, sin JavaScript). Selectores
  verificados en vivo: `.tgme_widget_message` / `.tgme_widget_message_text` /
  `.tgme_widget_message_date time`. Paginación con `?before=<id>` para llegar
  hasta 50 mensajes (20 por página).
- Extracción de eventos: `EventExtractor.extract_all` (fechas, lugares,
  organizadores, emails, subgénero). URL del evento: t.me/s/<canal>/<id>.

Todo gratuito/open-source, solo dependencias ya presentes (requests, bs4,
lxml). Archivos de estado dentro de `scrapers/telegram/`.

Timeout global: 180 segundos (3 min). Rate limiting con `AntiBlock`.
Siempre aditivo: si algo falla, devuelve [] sin romper el bot.
"""

from __future__ import annotations

import json
import os
import random
import re
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple
from urllib.parse import unquote

import requests
from bs4 import BeautifulSoup

_PROJECT_ROOT = str(Path(__file__).resolve().parent.parent.parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

# Directorio de estado dentro del proyecto (requisito: subcarpeta local)
_DIR = Path(__file__).resolve().parent
_STATE_DIR = _DIR
_STATE_DIR.mkdir(parents=True, exist_ok=True)

CANALES_ESTADO = _STATE_DIR / "telegram_canales_estado.json"
CANALES_PRODUCTIVOS = _STATE_DIR / "telegram_canales_productivos.json"
RENDIMIENTO = _STATE_DIR / "telegram_rendimiento.json"

# Límites y control de tiempo
TIMEOUT_FASE = 180  # 3 minutos máximo para toda la fase Telegram
TIMEOUT_REQUEST = 25
MAX_MENSAJES_POR_CANAL = 50      # objetivo
PAGINAS_POR_CANAL = 3            # 20 msgs/página → hasta 60 (cap 50)
MAX_CANALES_POR_RUN = 8
MAX_BUSQUEDAS_POR_RUN = 6
MIN_MENSAJES_CANAL = 5           # canal con <5 mensajes → no productivo

HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/125.0.0.0 Safari/537.36"),
    "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
}

# Subgéneros a combinar con ciudades (coincide con config_grupos.json)
SUBGENEROS = [
    "psytrance", "darkpsy", "forest psy", "goa trance", "hitech",
    "psychedelic trance", "fullon", "psychill", "progressive psy",
]

# Canales semilla validados manualmente (funcionan siempre, incluso si DDG
# rate-limita con 202). TheMysticRose: festival/parties psytrance real.
CANALES_SEMILLA = ["TheMysticRose"]

# Keywords específicas de psytrance (reducción de ruido; mismas que Eventbrite).
PSYTRANCE_KEYWORDS = [
    "psytrance", "psy trance", "goa trance", "goa", "darkpsy", "dark psy",
    "forest psy", "hitech", "hi-tech", "full on", "psychedelic trance",
    "psydub", "psychill", "psybient", "progressive psy", "progressive trance",
    "psycore", "suomisaundi", "zenon", "twilight", "mystic rose",
]

# Señales claras de que el canal NO es de psytrance (evitan ruido en el CSV)
NO_PSY_SENALES = [
    "tv", "pelicula", "film", "movie", "series", "netflix", "kodi",
    "iptv", "telegram channel for", "crypto", "forex", "bitcoin",
    "programas de tv", "fútbol", "futbol", "f1", "partido",
]

# Señales de que un mensaje es un RECAP (galería de fotos/vídeo) y NO un
# anuncio de evento → nunca debe generar evento.
RECAP_SENALES = [
    "bilder der letzten", "fotos vom", "fotos del", "photos from",
    "video vom", "aftermovie", "after movie", "aftermovie",
    "teil 1", "teil 2", "teil 3", "part 1", "part 2", "part 3",
    "galería de fotos", "galeria de fotos", "photo gallery",
    "impressions", "recap", "recap video", "thank you for the",
    "gracias a todos", "danksagung",
]

# Señales de que un mensaje es la DESCRIPCIÓN/BIO del canal (historia del
# canal, fechas de fundación, bienvenida) → no es un evento.
BIO_SENALES = [
    "willkommen zum offiziellen", "welcome to the official",
    "bienvenido al canal", "bienvenid@ al canal", "este es el canal",
    "this is the channel", "official telegram channel",
    "offizieller kanal", "descripción del canal", "descripcion del canal",
    "channel description", "pinned «", "pinned <<",
    "herzlich willkommen", "offiziellen",
]

# ---- Parámetros de mejora semántica (álgebra lineal) ----
# Umbral de similitud coseno para aceptar un canal candidato comparado con
# canales productivos conocidos. Bajo este valor, el canal se descarta.
THRESH_CANAL = 0.4
# Umbral de similitud para filtrar mensajes: si un mensaje es muy distinto a
# los "mensajes semilla" (que anuncian eventos) se descarta sin extraer.
THRESH_MENSAJE = 0.3
# Mensajes a inspeccionar como preview al validar un canal candidato.
MAX_MSGS_PREVIEW = 8
# Cada cuántas ejecuciones se reentrena el modelo de canales.
RERENTRENAR_CADA = 5
# Confianza mínima para aceptar la predicción de subgénero/tipo_lugar. Baja
# porque los mensajes ya pasaron el filtro keyword+semilla de psytrance, por
# lo que un subgénero plausible (aunque poco confiable) es útil rellenar.
CONF_MIN_PREDICCION = 0.15

# Archivos de estado semántica (dentro de scrapers/telegram/)
MODELO_CANALES = _STATE_DIR / "modelo_canales.pkl"
SEMILLA_MENSAJES = _STATE_DIR / "semilla_mensajes.json"

_algebra = None  # core.algebra_lineal (None si no disponible)
_modelo_canales = None  # {"vectorizador": V, "canales": {ch: vector_np}}

# Señales claras de que el canal NO es de psytrance (evitan ruido en el CSV)
NO_PSY_SENALES = [
    "tv", "pelicula", "film", "movie", "series", "netflix", "kodi",
    "iptv", "telegram channel for", "crypto", "forex", "bitcoin",
    "programas de tv", "fútbol", "futbol", "f1", "partido",
]

# Señales de que un mensaje es un RECAP (galería de fotos/vídeo) y NO un
# anuncio de evento → nunca debe generar evento.
RECAP_SENALES = [
    "bilder der letzten", "fotos vom", "fotos del", "photos from",
    "video vom", "aftermovie", "after movie", "aftermovie",
    "teil 1", "teil 2", "teil 3", "part 1", "part 2", "part 3",
    "galería de fotos", "galeria de fotos", "photo gallery",
    "impressions", "recap", "recap video", "thank you for the",
    "gracias a todos", "danksagung",
]

# Señales de que un mensaje es la DESCRIPCIÓN/BIO del canal (historia del
# canal, fechas de fundación, bienvenida) → no es un evento.
BIO_SENALES = [
    "willkommen zum offiziellen", "welcome to the official",
    "bienvenido al canal", "bienvenid@ al canal", "este es el canal",
    "this is the channel", "official telegram channel",
    "offizieller kanal", "descripción del canal", "descripcion del canal",
    "channel description", "pinned «", "pinned <<",
    "herzlich willkommen", "offiziellen",
]

_extractor = None
_anti = None
_algebra = None  # core.algebra_lineal (None si no disponible)
_modelo_canales = None  # {"vectorizador": V, "canales": {ch: vector_np}}


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


def _get_algebra():
    """Carga core.algebra_lineal de forma opcional (None si no está)."""
    global _algebra
    if _algebra is None:
        try:
            import core.algebra_lineal as alg
            _algebra = alg
        except Exception:
            _algebra = False  # cache negativo: no volver a intentar
    return _algebra if _algebra is not False else None


def _vec_texto_a_vector(vec, textos):
    """Transforma textos a vectores usando un vectorizador (numpy L2-normalizado)."""
    try:
        import numpy as np
        X = vec.transform(textos)
        if X is None:
            return None
        return X
    except Exception:
        return None


def _cargar_modelo_canales():
    """Carga (o inicializa) el modelo de vectores de canales productivos."""
    global _modelo_canales
    if _modelo_canales is not None:
        return _modelo_canales
    try:
        import pickle
        if MODELO_CANALES.exists():
            with open(MODELO_CANALES, "rb") as f:
                _modelo_canales = pickle.load(f)
            return _modelo_canales
    except Exception:
        pass
    _modelo_canales = {"vectorizador": None, "canales": {}}
    return _modelo_canales


def _guardar_modelo_canales():
    """Persiste el modelo de canales (solo con álgebra lineal disponible)."""
    try:
        import pickle
        with open(str(MODELO_CANALES) + ".tmp", "wb") as f:
            pickle.dump(_modelo_canales, f)
        os.replace(str(MODELO_CANALES) + ".tmp", str(MODELO_CANALES))
    except Exception:
        pass


def _entrenar_vectorizador_canales(textos_canales: Dict[str, List[str]]):
    """Ajusta un VectorizadorTFIDF sobre todos los textos de los canales
    productivos y calcula el vector promedio por canal.

    `textos_canales`: {canal: [texto_msg, ...]}.
    Devuelve dict {vectorizador, canales: {canal: vector_np}} o None.
    """
    alg = _get_algebra()
    if not alg or not textos_canales:
        return None
    try:
        todos: List[str] = []
        for c, msgs in textos_canales.items():
            for m in msgs:
                if m and len(m) >= 5:
                    todos.append(m)
        if not todos:
            return None
        vec = alg.VectorizadorTFIDF()
        vec.fit(todos)
        if not vec.vocab:
            return None
        import numpy as np
        canales_vec = {}
        for c, msgs in textos_canales.items():
            filt = [m for m in msgs if m and len(m) >= 5]
            if not filt:
                continue
            X = vec.transform(filt)
            if X.shape[0] == 0:
                continue
            prom = np.asarray(X.mean(axis=0)).flatten() if X.ndim > 1 else X
            canales_vec[c] = prom
        return {"vectorizador": vec, "canales": canales_vec}
    except Exception:
        return None


def _similitud_canal(mensajes_preview: List[str]) -> float:
    """Similitud coseno máxima entre el preview de un canal candidato y los
    canales productivos indexados. Devuelve 0.0 si está caótico."""
    model = _cargar_modelo_canales()
    vec = model.get("vectorizador")
    canales_vec = model.get("canales", {})
    if vec is None or not canales_vec or not mensajes_preview:
        return 0.0
    try:
        import numpy as np
        textos = [m for m in mensajes_preview if m and len(m) >= 5]
        if not textos:
            return 0.0
        X = vec.transform(textos)
        if X.shape[0] == 0:
            return 0.0
        prom = np.asarray(X.mean(axis=0)).flatten() if X.ndim > 1 else X
        best = 0.0
        for c, cv in canales_vec.items():
            if cv is None or cv.size == 0:
                continue
            sim = float(np.dot(cv, prom)) if cv.shape == prom.shape else 0.0
            if sim > best:
                best = sim
        return best
    except Exception:
        return 0.0


def _leer_json(ruta: Path, por_defecto: Any) -> Any:
    if not ruta.exists():
        return por_defecto
    try:
        with open(ruta, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError, ValueError):
        return por_defecto


def _escribir_json(ruta: Path, datos: Any) -> None:
    try:
        tmp = str(ruta) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(datos, f, ensure_ascii=False, indent=2)
        os.replace(tmp, str(ruta))
    except (IOError, OSError):
        pass


# ---------------------------------------------------------------------------
# 1) Búsqueda de canales con DuckDuckGo (site:t.me/s/)
# ---------------------------------------------------------------------------
def _combinaciones_busqueda() -> List[str]:
    """Genera combinaciones subgénero+ciudad desde config_grupos.json."""
    combos: List[str] = []
    try:
        cfg = _leer_json(Path(_PROJECT_ROOT) / "config_grupos.json", {})
        subgen = cfg.get("subgeneros") or SUBGENEROS
        paises = cfg.get("paises") or []
        # Recoger ciudades (rotación limitada a las primeras de cada país)
        ciudades: List[str] = []
        for p in paises[:12]:
            c = (p.get("ciudades") or [])[:4]
            ciudades.extend(c)
        # subgenero + ciudad
        for sg in subgen[:8]:
            for city in random.sample(ciudades, min(3, len(ciudades))):
                combos.append(f'site:t.me/s "{sg}" "{city}"')
        # solo subgenero (global)
        for sg in subgen[:6]:
            combos.append(f'site:t.me/s "{sg}"')
    except Exception:
        pass
    random.shuffle(combos)
    return combos[:MAX_BUSQUEDAS_POR_RUN]


def _es_canal_irrelevante(canal: str) -> bool:
    """Descartar canales claramente no-psytrance (TV, pelis, crypto, deportes)."""
    c = canal.lower()
    return any(s in c for s in NO_PSY_SENALES)


def _es_canal_relevante(canal: str) -> bool:
    """True si el canal candidato es temáticamente similar a los productivos.

    Usa similitud coseno contra el modelo de canales. Si no hay modelo
    todavía, acepta el canal (el modelado se entrena en ejecuciones
    posteriores). Siempre se combina con el filtro keyword de respaldo.
    """
    if _es_canal_irrelevante(canal):
        return False
    model = _cargar_modelo_canales()
    if model.get("vectorizador") is None or not model.get("canales"):
        # Sin modelo: aceptar salvo señal explícita de no-psy.
        return True
    try:
        preview = _scrape_mensajes(canal, max_msgs=MAX_MSGS_PREVIEW)
        textos = [m["texto"] for m in preview if (m.get("texto") or "").strip()]
        sim = _similitud_canal(textos)
        if sim >= THRESH_CANAL:
            return True
        return False
    except Exception:
        return True  # ante duda, no bloquear descubrimiento


def _buscar_canales_ddg(combo: str) -> List[str]:
    """Busca canales en DuckDuckGo (endpoint HTML, sin cookies).

    Devuelve usernames de t.me/s/ encontrados en los resultados. Si DDG
    responde 202 (rate-limit), lo marca como bloqueado y devuelve [] sin
    perder tiempo reintentando (el fail-fast lo decide el llamador).
    """
    anti = _get_anti()
    if anti is not None:
        anti.wait_if_needed("duckduckgo.com")
        if anti.is_blocked("duckduckgo.com"):
            return []
    try:
        r = requests.get(
            "https://html.duckduckgo.com/html/",
            params={"q": combo},
            headers=HEADERS,
            timeout=TIMEOUT_REQUEST,
        )
        if anti is not None:
            if r.status_code >= 400:
                anti.mark_blocked("duckduckgo.com")
            else:
                anti.mark_success("duckduckgo.com")
        if r.status_code == 202 or r.status_code >= 400:
            return []
        if r.status_code != 200:
            return []
        soup = BeautifulSoup(r.text, "lxml")
        canales: List[str] = []
        for a in soup.select("a.result__a"):
            href = a.get("href", "")
            url = href
            if "uddg=" in href:
                try:
                    url = unquote(re.search(r"uddg=([^&]+)", href).group(1))
                except Exception:
                    url = href
            m = re.search(r"t\.me/s/([A-Za-z0-9_]+)", url)
            if m:
                canal = m.group(1)
                if canal.lower() not in {c.lower() for c in canales}:
                    canales.append(canal)
        return canales
    except Exception:
        return []


def _descubrir_canales() -> List[str]:
    """Busca canales con DDG y los combina con los ya conocidos/productivos."""
    conocidos: List[str] = []
    estado = _leer_json(CANALES_ESTADO, {})
    if isinstance(estado, dict):
        conocidos = list(estado.get("canales", []))

    productivos = _leer_json(CANALES_PRODUCTIVOS, [])
    if isinstance(productivos, list):
        for p in productivos:
            c = (p.get("canal") or "") if isinstance(p, dict) else ""
            if c and c not in conocidos:
                conocidos.append(c)

    nuevos: List[str] = []
    combos = _combinaciones_busqueda()
    for combo in combos:
        try:
            hit = _buscar_canales_ddg(combo)
            for c in hit:
                if _es_canal_irrelevante(c):
                    continue
                # Filtrado semántico: solo canales con temática similar a
                # los productivos (evita TV / crypto / techno). Si no hay
                # modelo, se aceptan todos los que pasan el filtro keyword.
                if _es_canal_relevante(c) and c not in conocidos and c not in nuevos:
                    nuevos.append(c)
            time.sleep(0.8)
            # Fail-fast: si DDG está rate-limitado (202), no perder tiempo
            # con el resto de búsquedas de esta ejecución.
            anti = _get_anti()
            if anti is not None and anti.is_blocked("duckduckgo.com"):
                break
        except Exception:
            continue

    # Semilla manual: siempre presente (fallback ante rate-limit de DDG)
    for c in CANALES_SEMILLA:
        if c not in conocidos and c not in nuevos:
            nuevos.append(c)

    # Orden: productivos primero, luego nuevos, luego conocidos restantes
    orden = []
    for p in productivos:
        c = (p.get("canal") or "") if isinstance(p, dict) else ""
        if c and c not in orden:
            orden.append(c)
    for c in nuevos:
        if c not in orden:
            orden.append(c)
    for c in conocidos:
        if c not in orden:
            orden.append(c)

    # Persistir estado (aditivo: conserva los ya conocidos)
    estado["canales"] = orden
    estado["ultima_busqueda"] = datetime.now(timezone.utc).isoformat()
    estado["nuevos"] = nuevos
    _escribir_json(CANALES_ESTADO, estado)
    return orden


# ---------------------------------------------------------------------------
# 2) Scraping de mensajes de un canal (t.me/s/<canal>)
# ---------------------------------------------------------------------------
def _es_canal_valido(html: str) -> bool:
    """Un canal válido muestra mensajes; un no-existente muestra 'Contact @'."""
    title = re.search(r"<title>(.*?)</title>", html, re.S)
    t = (title.group(1) if title else "") or ""
    return "Contact @" not in t


def _scrape_mensajes(canal: str, max_msgs: int = MAX_MENSAJES_POR_CANAL) -> List[Dict]:
    """Extrae hasta max_msgs mensajes de t.me/s/<canal> (paginado con ?before=).

    Cada mensaje: {texto, fecha_iso, url, post_id, canal}.
    """
    anti = _get_anti()
    base = f"https://t.me/s/{canal}"
    vistos: Set[str] = set()
    mensajes: List[Dict] = []
    before: Optional[str] = None

    for _pagina in range(PAGINAS_POR_CANAL):
        url = base + (f"?before={before}" if before else "")
        if anti is not None:
            anti.wait_if_needed("t.me")
        try:
            r = requests.get(url, headers=HEADERS, timeout=TIMEOUT_REQUEST)
            if anti is not None:
                if r.status_code >= 400:
                    anti.mark_blocked("t.me")
                else:
                    anti.mark_success("t.me")
            if r.status_code != 200:
                break
            if not _es_canal_valido(r.text):
                break
            soup = BeautifulSoup(r.text, "lxml")
            pagina = 0
            for msg in soup.select(".tgme_widget_message"):
                post_id = msg.get("data-post", "")
                if not post_id or post_id in vistos:
                    continue
                vistos.add(post_id)
                txt = msg.select_one(".tgme_widget_message_text")
                texto = txt.get_text("\n", strip=True) if txt else ""
                fecha_el = msg.select_one(".tgme_widget_message_date time")
                fecha = fecha_el.get("datetime", "") if fecha_el else ""
                canal_nombre = post_id.split("/")[0] if "/" in post_id else canal
                mensajes.append({
                    "canal": canal_nombre,
                    "post_id": post_id,
                    "texto": texto,
                    "fecha": fecha,
                    "url": f"https://t.me/s/{canal_nombre}/{post_id.split('/')[-1]}",
                })
                pagina += 1
                if len(mensajes) >= max_msgs:
                    break
            if len(mensajes) >= max_msgs:
                break
            if not mensajes:
                break
            # Siguiente página: ?before=<id del mensaje más antiguo recogido>
            ids = [m["post_id"].split("/")[-1] for m in mensajes if "/" in m["post_id"]]
            if not ids:
                break
            before = str(min(int(i) for i in ids if i.isdigit()))
            if not before:
                break
            time.sleep(0.6)
        except Exception:
            break

    return mensajes


# ---------------------------------------------------------------------------
# 3) Extracción de eventos desde los mensajes
# ---------------------------------------------------------------------------
def _es_psytrancero(texto: str) -> bool:
    """Un mensaje solo produce evento si menciona señales psytrance."""
    t = texto.lower()
    return any(k in t for k in PSYTRANCE_KEYWORDS)


def _es_recap(texto: str) -> bool:
    """Galerías de fotos/vídeo de eventos pasados no son eventos nuevos."""
    t = texto.lower()
    return any(s in t for s in RECAP_SENALES)


def _es_bio(texto: str) -> bool:
    """Descripciones/bios del canal (historia, fundación) no son eventos."""
    t = texto.lower()
    return any(s in t for s in BIO_SENALES)


def _mensaje_a_evento(mensaje: Dict) -> Optional[Dict]:
    """Convierte un mensaje de canal en un evento (si tiene fecha/lugar)."""
    extractor = _get_extractor()
    texto = (mensaje.get("texto") or "").strip()
    if len(texto) < 15 or not texto:
        return None

    # Filtro de relevancia: evitar ruido (techno, TV, crypto, etc.)
    if not _es_psytrancero(texto):
        return None
    # Filtro de recaps: fotos/vídeo de eventos pasados
    if _es_recap(texto):
        return None
    # Filtro de bios: descripción del canal (historia/fundación)
    if _es_bio(texto):
        return None
    # Filtro semántico: si el mensaje es muy distinto a los anuncios de
    # eventos semilla, probablemente es ruido (saludo, encuesta, reenvío).
    if not _es_mensaje_util(texto):
        return None

    eventos = []
    if extractor is not None:
        try:
            eventos = extractor.extract_all(
                texto, source_name="Telegram", source_url=mensaje.get("url", "")
            )
        except Exception:
            eventos = []

    canal = mensaje.get("canal") or ""
    base = eventos[0] if eventos else None

    # Fallback: extractor no encontró evento (p.ej. anuncio corto en alemán),
    # pero hay una fecha parseable → construir evento mínimo.
    if base is None and extractor is not None:
        try:
            fecha = extractor._extract_date(texto)
        except Exception:
            fecha = None
        if fecha:
            base = {
                "nombre": _titulo_anuncio(texto),
                "fecha": fecha,
                "lugar": "N/A",
                "ciudad": "N/A",
                "pais": "N/A",
                "tipo_lugar": "N/A",
                "organizador": canal,
                "email": "N/A",
                "subgenero": "general",
            }

    if base is None:
        return None
    if base.get("fecha") in (None, "", "N/A"):
        # El mensaje no tiene fecha reconocible → no construimos evento
        return None

    # El canal es el organizador natural cuando el extractor devuelve
    # el valor genérico "Telegram" como fuente.
    organizador = base.get("organizador") or ""
    if not organizador or organizador.lower() == "telegram":
        organizador = canal

    evento = {
        "nombre": (base.get("nombre") or "").strip()[:200] or "Evento Telegram",
        "fecha": base.get("fecha") or "N/A",
        "lugar": base.get("lugar") or "N/A",
        "ciudad": base.get("ciudad") or "N/A",
        "pais": base.get("pais") or "N/A",
        "tipo_lugar": base.get("tipo_lugar") or "N/A",
        "organizador": organizador or "N/A",
        "email": base.get("email") or "N/A",
        "link": mensaje.get("url") or "N/A",
        "url": mensaje.get("url") or "N/A",
        "fuente": "Telegram",
        "subgenero": base.get("subgenero") or "general",
        "descripcion": texto[:500],
    }
    # Mejora opcional: rellenar subgénero/tipo_lugar con álgebra lineal.
    _predecir_campos(canal, texto, evento)
    return evento


def _titulo_anuncio(texto: str) -> str:
    """Primera línea útil como título (quita separadores y fechas sueltas)."""
    for linea in texto.splitlines():
        limpia = re.sub(r"^[\s\.\-_=~*]+", "", linea).strip()
        limpia = re.sub(r"[\s\.\-_=~*]+$", "", limpia)
        if len(limpia) >= 8:
            return limpia[:200]
    return texto[:200]


def _procesar_canal(canal: str) -> Tuple[List[Dict], List[Dict]]:
    """Scrapea mensajes de un canal y extrae eventos.

    Devuelve (eventos, mensajes) para que el llamador pueda actualizar la
    semilla de mensajes productivos.
    """
    mensajes = _scrape_mensajes(canal)
    eventos = []
    for m in mensajes:
        ev = _mensaje_a_evento(m)
        if ev:
            eventos.append(ev)
    return eventos, mensajes


# ---------------------------------------------------------------------------
# 4) Loop de mejora: canales productivos y rendimiento
# ---------------------------------------------------------------------------
def _registrar_resultado(canal: str, eventos: int, mensajes: int, tiempo_s: float) -> None:
    """Actualiza telegram_canales_productivos.json y telegram_rendimiento.json."""
    rend = _leer_json(RENDIMIENTO, {})
    entry = rend.get(canal, {"canal": canal, "runs": 0, "eventos": 0,
                             "mensajes": 0, "tiempo_s": 0.0})
    entry["runs"] = int(entry.get("runs", 0)) + 1
    entry["eventos"] = int(entry.get("eventos", 0)) + eventos
    entry["mensajes"] = int(entry.get("mensajes", 0)) + mensajes
    entry["tiempo_s"] = round(float(entry.get("tiempo_s", 0)) + tiempo_s, 2)
    entry["ultimo_run"] = datetime.now(timezone.utc).isoformat()
    rend[canal] = entry
    _escribir_json(RENDIMIENTO, rend)

    # Productivos: canales con >= MIN_MENSAJES_CANAL y al menos 1 evento
    prod = _leer_json(CANALES_PRODUCTIVOS, [])
    prod_map = {p.get("canal"): p for p in prod if isinstance(p, dict)}
    if eventos >= 1 and mensajes >= MIN_MENSAJES_CANAL:
        prod_map[canal] = {
            "canal": canal,
            "eventos": int(prod_map.get(canal, {}).get("eventos", 0)) + eventos,
            "ultima_visita": datetime.now(timezone.utc).isoformat(),
        }
    _escribir_json(CANALES_PRODUCTIVOS, list(prod_map.values()))


def _canales_a_procesar(orden: List[str]) -> List[str]:
    """Selecciona canales para esta ejecución (rotación con productivos primero)."""
    prod = _leer_json(CANALES_PRODUCTIVOS, [])
    prod_map: Dict[str, Dict] = {}
    if isinstance(prod, list):
        for p in prod:
            if isinstance(p, dict) and p.get("canal"):
                prod_map[p["canal"]] = p
    # Productivos primero (top por eventos acumulados)
    prods = sorted(prod_map.keys(),
                   key=lambda c: -int(prod_map[c].get("eventos", 0)))
    seleccion = [c for c in prods if c in orden][:2]
    for c in orden:
        if len(seleccion) >= MAX_CANALES_POR_RUN:
            break
        if c not in seleccion:
            seleccion.append(c)
    return seleccion[:MAX_CANALES_POR_RUN]


# ---------------------------------------------------------------------------
# 5) Mejora semántica con álgebra lineal (opcional)
# ---------------------------------------------------------------------------
def _cargar_semilla() -> List[str]:
    """Mensajes semilla (de anuncios de eventos reales). [] si no hay."""
    d = _leer_json(SEMILLA_MENSAJES, {})
    if isinstance(d, list):
        return [x for x in d if isinstance(x, str) and x.strip()]
    if isinstance(d, dict):
        return [x for x in d.get("mensajes", []) if isinstance(x, str) and x.strip()]
    return []


def _guardar_semilla(mensajes: List[str]) -> None:
    _escribir_json(SEMILLA_MENSAJES, {"mensajes": list(mensajes)[:200]})


def _texto_relevante_para_semilla(mensajes: List[Dict]) -> List[str]:
    """Extrae los textos de mensajes que produjeron eventos (para semilla)."""
    out: List[str] = []
    for m in mensajes:
        texto = (m.get("texto") or "").strip()
        if texto and _es_psytrancero(texto) and not _es_recap(texto) and not _es_bio(texto):
            out.append(texto[:500])
    return out


def _es_mensaje_util(texto: str) -> bool:
    """Filtra mensajes irrelevantes por similitud a los mensajes semilla.

    Si no hay semilla almacenada aún, acepta el mensaje (calienta la semilla).
    """
    semilla = _cargar_semilla()
    if not semilla:
        return True
    alg = _get_algebra()
    if not alg:
        return True
    try:
        vec = alg.VectorizadorTFIDF()
        vec.fit(semilla + [texto])
        X = vec.transform(semilla + [texto])
        if X.shape[0] < 2 or X.shape[1] == 0:
            return True
        import numpy as np
        sims = (X @ X.T)
        # similitud del mensaje (último) con cada mensaje semilla
        msg_vec = X[-1]
        sims_msg = X[:-1] @ msg_vec
        best = float(sims_msg.max()) if sims_msg.size else 0.0
        return best >= THRESH_MENSAJE
    except Exception:
        return True


def _reentrenar_si_toca():
    """Reentrena el modelo de canales productivos cada RERENTRENAR_CADA runs.

    Usa los mensajes acumulados de los canales productivos para recalcular
    sus vectores TF-IDF promedio. Guarda modelo_canales.pkl.
    """
    alg = _get_algebra()
    if not alg:
        return
    try:
        prod = _leer_json(CANALES_PRODUCTIVOS, [])
        if not isinstance(prod, list) or not prod:
            return
        model = _cargar_modelo_canales()
        counter = 0
        ruta_counter = Path(_PROJECT_ROOT) / "models" / "telegram_counter.txt"
        try:
            if ruta_counter.exists():
                raw = open(ruta_counter, "r").read().strip()
                counter = int(raw) if raw else 0
        except Exception:
            counter = 0
        counter += 1
        try:
            ruta_counter.parent.mkdir(parents=True, exist_ok=True)
            with open(ruta_counter, "w") as f:
                f.write(str(counter))
        except Exception:
            pass

        if counter % RERENTRENAR_CADA != 0:
            return

        textos_por_canal: Dict[str, List[str]] = {}
        for p in prod:
            if not isinstance(p, dict):
                continue
            canal = p.get("canal") or ""
            if not canal:
                continue
            try:
                msgs = _scrape_mensajes(canal, max_msgs=MAX_MENSAJES_POR_CANAL)
            except Exception:
                msgs = []
            textos_por_canal[canal] = [m["texto"] for m in msgs if m.get("texto")]
        nuevo = _entrenar_vectorizador_canales(textos_por_canal)
        if nuevo:
            model["vectorizador"] = nuevo["vectorizador"]
            model["canales"] = nuevo["canales"]
            _guardar_modelo_canales()
    except Exception:
        return


def _actualizar_semilla(canal: str, mensajes: List[Dict],
                        eventos_del_canal: List[Dict]) -> None:
    """Almacena textos de mensajes que produjeron eventos como semilla."""
    if not eventos_del_canal:
        return
    nuevos = _texto_relevante_para_semilla(mensajes)
    if not nuevos:
        return
    actuales = _cargar_semilla()
    combinados = list(actuales)
    for t in nuevos:
        t = t[:500]
        if t not in combinados:
            combinados.append(t)
    _guardar_semilla(combinados)


def _predecir_campos(canal: str, texto: str, evento: Dict) -> None:
    """Rellena subgénero y tipo de lugar con el clasificador de álgebra lineal."""
    alg = _get_algebra()
    if not alg:
        return
    # Sólo predecir si vienen vacíos/N/A.
    if evento.get("subgenero") in (None, "", "N/A", "general"):
        try:
            sub, conf = alg.predecir_subgenero(texto)
            if sub and sub != "N/A" and conf is not None and conf >= CONF_MIN_PREDICCION:
                evento["subgenero"] = sub
        except Exception:
            pass
    if evento.get("tipo_lugar") in (None, "", "N/A"):
        try:
            tipo, conf = alg.predecir_tipo_lugar(texto)
            if tipo and tipo != "N/A" and conf is not None and conf >= CONF_MIN_PREDICCION:
                evento["tipo_lugar"] = tipo
        except Exception:
            pass


# ---------------------------------------------------------------------------
# 5) Entrada principal
# ---------------------------------------------------------------------------
def scrape_telegram_events(timeout: int = TIMEOUT_FASE) -> List[Dict]:
    """Búsqueda de canales + scraping de mensajes + extracción de eventos.

    Respeta un timeout global de `timeout` segundos (default 180). Si se
    supera, devuelve lo que tenga. Aditivo: nunca lanza excepciones al caller.
    """
    fin = time.time() + timeout
    print("📣 Scraping Telegram (canales públicos, sin API)...")
    eventos: List[Dict] = []

    try:
        canales = _descubrir_canales()
        seleccion = _canales_a_procesar(canales)
        if not seleccion:
            print("  ⚠️ Telegram: sin canales para procesar")
            return []

        for canal in seleccion:
            if time.time() > fin:
                print("  ⏰ Telegram: timeout global alcanzado")
                break
            try:
                t0 = time.time()
                evs_canal, mensajes = _procesar_canal(canal)
                if evs_canal:
                    eventos.extend(evs_canal)
                    print(f"  ✅ @{canal}: {len(evs_canal)} eventos de {len(mensajes)} mensajes")
                else:
                    print(f"  ⚠️ @{canal}: {len(mensajes)} mensajes, 0 eventos")
                _registrar_resultado(canal, len(evs_canal), len(mensajes),
                                     round(time.time() - t0, 2))
                # Actualizar semilla de mensajes productivos
                _actualizar_semilla(canal, mensajes, evs_canal)
            except Exception as e:
                print(f"  ❌ @{canal}: {type(e).__name__}: {e}")
                continue

        # Dedup local por url
        vistos: Set[str] = set()
        unicos: List[Dict] = []
        for ev in eventos:
            k = ev.get("link") or ev.get("url") or ev.get("nombre") or ""
            if k and k not in vistos:
                vistos.add(k)
                unicos.append(ev)
        eventos = unicos

        # Reentrenar modelo de canales periódicamente (aditivo, async-safe)
        try:
            _reentrenar_si_toca()
        except Exception:
            pass

    except Exception as e:
        print(f"  ⚠️ Telegram scraper: {type(e).__name__}: {e}")
        return []

    print(f"  📊 Telegram: {len(eventos)} eventos totales")
    return eventos


if __name__ == "__main__":
    t0 = time.time()
    evs = scrape_telegram_events()
    print(f"Tiempo: {time.time() - t0:.0f}s, Eventos Telegram: {len(evs)}")
    for e in evs[:5]:
        print(f"  {e['nombre'][:50]} | {e.get('fecha','?')} | {e.get('lugar','?')}")
