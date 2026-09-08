#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Scraper de canales públicos de Telegram (Dharmadhatu Bot v5) — SIN API token.

Estrategia validada con herramientas open-source:
- Búsqueda de canales: DuckDuckGo (endpoint LITE HTML) vía Tor,
  consultas naturales sin `site:` agresivo: "psytrance t.me/s", "darkpsy t.me/s", etc.
  Fuentes de referencia:
    • ulinycoin/shadow-tg  (https://github.com/ulinycoin/shadow-tg) — MIT,
      usa el mismo patrón: t.me/s/{channel} + búsqueda DDG.
    • Kisspeace/accless-tg-scraper (https://github.com/kisspeace/accless-tg-scraper)
    • PythonicCafe/tchan (https://github.com/PythonicCafe/tchan)
- Scraping de mensajes: `requests` + `BeautifulSoup` sobre la vista web
  pública `https://t.me/s/<canal>` (HTML estático, sin JavaScript). Selectores
  verificados en vivo: `.tgme_widget_message` / `.tgme_widget_message_text` /
  `.tgme_widget_message_date time`. Paginación con `?before=<id>` para llegar
  hasta 50 mensajes (20 por página).
- Extracción de eventos: `EventExtractor.extract_all` (fechas, lugares,
  organizadores, emails, subgénero). URL del evento: t.me/s/<canal>/<id>.

Todo gratuito/open-source, solo dependencias ya presentes (requests, bs4).
Archivos de estado dentro de `scrapers/telegram/`.

Timeout global: 120 segundos. Rate limiting con Tor NEWNYM cada 3 canales.
Siempre aditivo: si algo falla, devuelve [] sin romper el bot.
"""

from __future__ import annotations

import json
import os
import random
import re
import sys
import time
import threading
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
CANALES_DESCUBIERTOS = _STATE_DIR / "canales_descubiertos.json"
RENDIMIENTO = _STATE_DIR / "telegram_rendimiento.json"

# Límites y control de tiempo
TIMEOUT_FASE = 120          # 120 segundos máximo para toda la fase Telegram
TIMEOUT_REQUEST = 10        # 10 segundos por petición individual
MAX_MENSAJES_POR_CANAL = 50
PAGINAS_POR_CANAL = 3
MAX_CANALES_POR_RUN = 15
MAX_BUSQUEDAS_POR_RUN = 8
MIN_MENSAJES_CANAL = 5
TOR_ROTACION_CADA = 3       # Rotar identidad Tor cada 3 canales
TOR_PAUSA_ROTACION = 10     # Pausa 10s tras rotar Tor

HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/125.0.0.0 Safari/537.36"),
    "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
}

# Configuración proxy activo (residencial de terceros si está configurado, si no Tor).
# Nunca se usa la IP real del usuario.
from core.http_client import get_active_proxies
TOR_PROXY = get_active_proxies()
TOR_CONTROL_PORT = 9051
TOR_CONTROL_PASSWORD = None  # Sin contraseña por defecto

# Subgéneros a combinar para búsquedas naturales
SUBGENEROS_BUSQUEDA = [
    "psytrance", "darkpsy", "forest psy", "goa trance", "hitech",
    "psychedelic trance", "fullon", "psychill", "progressive psy",
    "psybient", "psydub", "suomisaundi", "zenon", "twilight",
]

# Términos específicos de eventos para búsquedas más precisas
EVENT_TERMS = [
    "events", "eventos", "festival", "party", "rave", "open air",
    "timetable", "lineup", "festival", "gathering", "meetup",
]

# Canales semilla validados manualmente
CANALES_SEMILLA = [
    "TheMysticRose",
    "shorthanduniverseofficial",
    "progressivetakeover",
    "PsychedelicSocietyBerlinOfficial",
    "goa_party",
    "bestgoaparty",
    "psychedelic_germany",
    "ogovergroundmusic",
    "ancient_trance_festival",
    "afishagoa",
    "Teletrance",
    "phanganparty",
]

# Keywords específicas de psytrance (filtrado de mensajes)
PSYTRANCE_KEYWORDS = [
    "psytrance", "psy trance", "goa trance", "goa", "darkpsy", "dark psy",
    "forest psy", "hitech", "hi-tech", "full on", "psychedelic trance",
    "psydub", "psychill", "psybient", "progressive psy", "progressive trance",
    "psycore", "suomisaundi", "zenon", "twilight", "mystic rose",
    "evento", "fiesta", "festival", "rave", "open air", "openair",
    "timetable", "lineup", "festival", "gathering", "meetup",
    "entradas", "tickets", "ticket", "compra", "vip", "early bird",
    "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre",
    "noviembre", "diciembre", "enero", "febrero", "marzo",
    # Keywords para eventos psicodélicos/comunitarios adyacentes
    "psychedelic session", "psychedelic sessions", "psychonaut", "psychonauts",
    "entheogenic", "entheogen", "ceremony", "ceremonia", "ritual",
    "workshop", "taller", "retreat", "retiro", "gathering", "encuentro",
    "dance", "baila", "bailar", "ecstatic", "ecstático", "movement", "movimiento",
    "community", "comunidad", "tribe", "tribu", "circle", "circulo", "círculo",
    "shamanic", "chamanico", "chamánico", "medicine", "medicina", "plant medicine",
]

# Blacklist: señales claras de que el canal NO es de psytrance/eventos
NO_PSY_SENALES = [
    "tv", "pelicula", "film", "movie", "series", "netflix", "kodi",
    "iptv", "telegram channel for", "crypto", "forex", "bitcoin",
    "programas de tv", "fútbol", "futbol", "f1", "partido",
    "news", "noticias", "daily", "diario", "update", "updates",
    "music", "música", "releases", "lanzamientos", "album", "álbum",
    "track", "single", "ep", "lp", "mixtape", "mix", "podcast",
    "radio", "station", "stream", "streaming", "youtube", "soundcloud",
    "lossless", "flac", "mp3", "download", "descarga", "free music",
    "promo", "promoción", "label", "records", "recordings",
    "porn", "xxx", "adult", "sex", "erotic", "nsfw",
    "gambling", "apuestas", "casino", "poker", "slots",
    "shop", "tienda", "store", "merch", "merchandise", "comprar",
    "job", "empleo", "trabajo", "hiring", "contratando",
    "dating", "citas", "singles", "pareja",
    "politics", "política", "politico", "elecciones", "gobierno",
    "religion", "religión", "church", "iglesia", "bible", "biblia",
]

# Señales de que un mensaje es un RECAP (galería de fotos/vídeo) y NO un
# anuncio de evento → nunca debe generar evento.
RECAP_SENALES = [
    "bilder der letzten", "fotos vom", "fotos del", "photos from",
    "video vom", "aftermovie", "after movie", "aftermovie",
    "teil 1", "teil 2", "teil 3", "part 1", "part 2", "part 3",
    "galería de fotos", "galeria de fotos", "photo gallery",
    "impressions", "recap", "recap video", "thank you for the",
    "gracias a todos", "danksagung", "thanks to all", "thank you all",
    "fotograf", "fotógrafo", "photographer", "visuals", "artes",
]

# Señales de que un mensaje es la DESCRIPCIÓN/BIO del canal
BIO_SENALES = [
    "willkommen zum offiziellen", "welcome to the official",
    "bienvenido al canal", "bienvenid@ al canal", "este es el canal",
    "this is the channel", "official telegram channel",
    "offizieller kanal", "descripción del canal", "descripcion del canal",
    "channel description", "pinned «", "pinned <<",
    "herzlich willkommen", "offiziellen", "bienvenidos al",
    "reglas del canal", "rules of the channel", "normas del canal",
]

# Parámetros semánticos (álgebra lineal opcional)
THRESH_CANAL = 0.4
THRESH_MENSAJE = 0.3
MAX_MSGS_PREVIEW = 8
RERENTRENAR_CADA = 5
CONF_MIN_PREDICCION = 0.15

MODELO_CANALES = _STATE_DIR / "modelo_canales.pkl"
SEMILLA_MENSAJES = _STATE_DIR / "semilla_mensajes.json"

_extractor = None
_algebra = None
_modelo_canales = None
_tor_rotaciones = 0
_tor_lock = threading.Lock()


def _get_extractor():
    global _extractor
    if _extractor is None:
        try:
            from scrapers.event_extractor import EventExtractor
            _extractor = EventExtractor()
        except Exception:
            _extractor = None
    return _extractor


def _get_algebra():
    """Carga core.algebra_lineal de forma opcional (None si no está)."""
    global _algebra
    if _algebra is None:
        try:
            import core.algebra_lineal as alg
            _algebra = alg
        except Exception:
            _algebra = False
    return _algebra if _algebra is not False else None


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
    """Ajusta un VectorizadorTFIDF sobre todos los textos de los canales productivos."""
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
    """Similitud coseno máxima entre el preview de un canal candidato y los canales productivos."""
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


def _rotar_tor():
    """Envía señal NEWNYM a Tor para rotar identidad (IP de salida)."""
    global _tor_rotaciones
    try:
        import socket
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(5)
        sock.connect(("127.0.0.1", TOR_CONTROL_PORT))
        if TOR_CONTROL_PASSWORD:
            sock.send(f'AUTHENTICATE "{TOR_CONTROL_PASSWORD}"\r\n'.encode())
        else:
            sock.send(b'AUTHENTICATE ""\r\n')
        resp = sock.recv(1024)
        if b'250' not in resp:
            sock.close()
            return False
        sock.send(b'SIGNAL NEWNYM\r\n')
        resp = sock.recv(1024)
        sock.close()
        if b'250' in resp:
            _tor_rotaciones += 1
            time.sleep(2)  # Esperar a que Tor establezca nuevo circuito
            return True
    except Exception:
        pass
    return False


def _hacer_peticion(url: str, params: dict = None, timeout: int = TIMEOUT_REQUEST,
                    use_tor: bool = True, headers: dict = None) -> Optional[requests.Response]:
    """Petición HTTP robusta con Tor opcional."""
    h = HEADERS.copy()
    if headers:
        h.update(headers)
    p = TOR_PROXY if use_tor else None
    try:
        r = requests.get(url, params=params, headers=h, proxies=p, timeout=timeout)
        return r
    except Exception:
        return None


# ---------------------------------------------------------------------------
# 1) Búsqueda de canales con DuckDuckGo LITE (vía Tor, consultas naturales)
# ---------------------------------------------------------------------------
def _generar_queries_busqueda() -> List[str]:
    """Genera consultas naturales para búsqueda de canales."""
    queries = []
    # Búsquedas combinadas: subgénero + términos de eventos
    for sg in SUBGENEROS_BUSQUEDA[:6]:
        for et in EVENT_TERMS[:4]:
            queries.append(f"{sg} {et} telegram")
            queries.append(f"{sg} {et} t.me")
    # Búsquedas solo subgénero (fallback)
    for sg in SUBGENEROS_BUSQUEDA[:6]:
        queries.append(f"{sg} telegram channel")
    random.shuffle(queries)
    return queries[:MAX_BUSQUEDAS_POR_RUN]


def _es_canal_irrelevante(canal: str) -> bool:
    """Descartar canales claramente no-psytrance (TV, crypto, música genérica, etc.)."""
    c = canal.lower()
    return any(s in c for s in NO_PSY_SENALES)


def _es_canal_relevante(canal: str) -> bool:
    """True si el canal candidato es temáticamente similar a los productivos."""
    if _es_canal_irrelevante(canal):
        return False
    model = _cargar_modelo_canales()
    # Si no hay modelo o hay pocos canales productivos (<3), ser permisivo
    canales_prod = model.get("canales", {})
    if model.get("vectorizador") is None or len(canales_prod) < 3:
        return True
    try:
        preview = _scrape_mensajes(canal, max_msgs=MAX_MSGS_PREVIEW, use_tor=True)
        textos = [m["texto"] for m in preview if (m.get("texto") or "").strip()]
        sim = _similitud_canal(textos)
        return sim >= THRESH_CANAL
    except Exception:
        return True


def _buscar_canales_ddg(query: str) -> List[str]:
    """Busca canales en DuckDuckGo LITE vía Tor. Devuelve usernames de t.me/s/."""
    r = _hacer_peticion(
        "https://lite.duckduckgo.com/lite/",
        params={"q": query},
        use_tor=True,
    )
    if not r or r.status_code != 200:
        return []
    soup = BeautifulSoup(r.text, "html.parser")
    canales: List[str] = []
    for link in soup.select("table a"):
        href = link.get("href", "")
        real_url = href
        if "uddg=" in href:
            try:
                real_url = unquote(re.search(r"uddg=([^&]+)", href).group(1))
            except Exception:
                real_url = href
        # Extraer username de t.me/ o t.me/s/
        m = re.search(r"t\.me/(?:s/)?([A-Za-z0-9_]+)", real_url)
        if m:
            canal = m.group(1)
            if canal.lower() not in {c.lower() for c in canales}:
                canales.append(canal)
    return canales


def _descubrir_canales() -> List[str]:
    """Busca canales con DDG LITE y los combina con los ya conocidos/productivos."""
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

    # Cargar canales ya descubiertos previamente
    descubiertos = _leer_json(CANALES_DESCUBIERTOS, [])
    if isinstance(descubiertos, list):
        for c in descubiertos:
            if isinstance(c, str) and c not in conocidos:
                conocidos.append(c)

    nuevos: List[str] = []
    queries = _generar_queries_busqueda()
    for query in queries:
        try:
            hit = _buscar_canales_ddg(query)
            for c in hit:
                if _es_canal_irrelevante(c):
                    continue
                if _es_canal_relevante(c) and c not in conocidos and c not in nuevos:
                    nuevos.append(c)
            time.sleep(1.5)  # Rate limit amable
        except Exception:
            continue

    # Semilla manual: siempre presente (fallback)
    for c in CANALES_SEMILLA:
        if c not in conocidos and c not in nuevos:
            nuevos.append(c)

    # Orden: productivos primero, luego nuevos, luego descubiertos, luego conocidos
    orden = []
    for p in productivos:
        c = (p.get("canal") or "") if isinstance(p, dict) else ""
        if c and c not in orden:
            orden.append(c)
    for c in nuevos:
        if c not in orden:
            orden.append(c)
    for c in descubiertos:
        if c not in orden:
            orden.append(c)
    for c in conocidos:
        if c not in orden:
            orden.append(c)

    # Persistir estado (aditivo)
    estado["canales"] = orden
    estado["ultima_busqueda"] = datetime.now(timezone.utc).isoformat()
    estado["nuevos"] = nuevos
    _escribir_json(CANALES_ESTADO, estado)

    # Guardar descubiertos (acumulativo)
    todos_descubiertos = list(set(descubiertos + nuevos))
    _escribir_json(CANALES_DESCUBIERTOS, todos_descubiertos)

    return orden


# ---------------------------------------------------------------------------
# 2) Scraping de mensajes de un canal (t.me/s/<canal>)
# ---------------------------------------------------------------------------
def _es_canal_valido(html: str) -> bool:
    """Un canal válido muestra mensajes; un no-existente muestra 'Contact @'."""
    title = re.search(r"<title>(.*?)</title>", html, re.S)
    t = (title.group(1) if title else "") or ""
    return "Contact @" not in t


def _scrape_mensajes(canal: str, max_msgs: int = MAX_MENSAJES_POR_CANAL,
                     use_tor: bool = True) -> List[Dict]:
    """Extrae hasta max_msgs mensajes de t.me/s/<canal> (paginado con ?before=)."""
    base = f"https://t.me/s/{canal}"
    vistos: Set[str] = set()
    mensajes: List[Dict] = []
    before: Optional[str] = None

    for _pagina in range(PAGINAS_POR_CANAL):
        url = base + (f"?before={before}" if before else "")
        r = _hacer_peticion(url, use_tor=use_tor)
        if not r or r.status_code != 200:
            break
        if not _es_canal_valido(r.text):
            break
        soup = BeautifulSoup(r.text, "html.parser")
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
        # Siguiente página: ?before=<id del mensaje más antiguo>
        ids = [m["post_id"].split("/")[-1] for m in mensajes if "/" in m["post_id"]]
        if not ids:
            break
        before = str(min(int(i) for i in ids if i.isdigit()))
        if not before:
            break
        time.sleep(0.6)

    return mensajes


# ---------------------------------------------------------------------------
# 3) Filtrado de mensajes y extracción de eventos
# ---------------------------------------------------------------------------
def _es_psytrancero(texto: str) -> bool:
    """Un mensaje solo produce evento si menciona señales psytrance/eventos."""
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

    if not _es_psytrancero(texto):
        return None
    if _es_recap(texto):
        return None
    if _es_bio(texto):
        return None
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

    # Fallback: extractor no encontró evento pero hay fecha parseable
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
        return None

    # Filtrar eventos pasados
    try:
        from datetime import date
        ev_date = date.fromisoformat(base.get("fecha")[:10])
        if ev_date < date.today():
            return None
    except Exception:
        pass

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
        "canal": canal or "N/A",
        "subgenero": base.get("subgenero") or "general",
        "descripcion": texto[:500],
    }
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


def _procesar_canal(canal: str, use_tor: bool = True) -> Tuple[List[Dict], List[Dict]]:
    """Scrapea mensajes de un canal y extrae eventos."""
    mensajes = _scrape_mensajes(canal, use_tor=use_tor)
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
    Si el mensaje tiene keywords psytrance Y una fecha parseable, acepta aunque
    la similitud semántica sea baja (diferentes formatos de eventos).
    """
    semilla = _cargar_semilla()
    if not semilla:
        return True
    
    # Si tiene keywords psytrance y fecha, ser permisivo
    extractor = _get_extractor()
    if extractor is not None:
        try:
            fecha = extractor._extract_date(texto)
        except Exception:
            fecha = None
    else:
        fecha = None
    if fecha and _es_psytrancero(texto):
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
        msg_vec = X[-1]
        sims_msg = X[:-1] @ msg_vec
        best = float(sims_msg.max()) if sims_msg.size else 0.0
        return best >= THRESH_MENSAJE
    except Exception:
        return True


def _reentrenar_si_toca():
    """Reentrena el modelo de canales productivos cada RERENTRENAR_CADA runs."""
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
                msgs = _scrape_mensajes(canal, max_msgs=MAX_MENSAJES_POR_CANAL, use_tor=True)
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
# 6) Entrada principal
# ---------------------------------------------------------------------------
def scrape_telegram_events(timeout: int = TIMEOUT_FASE) -> List[Dict]:
    """Búsqueda de canales + scraping de mensajes + extracción de eventos.

    Usa Tor para todas las peticiones. Rota identidad cada TOR_ROTACION_CADA canales.
    Respeta timeout global de `timeout` segundos (default 120). Aditivo.
    """
    global _tor_rotaciones
    fin = time.time() + timeout
    print("📣 Scraping Telegram (canales públicos, sin API, vía Tor)...")
    eventos: List[Dict] = []
    canales_procesados = 0

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

            # Rotar Tor cada TOR_ROTACION_CADA canales
            if canales_procesados > 0 and canales_procesados % TOR_ROTACION_CADA == 0:
                print(f"  🔄 Rotando identidad Tor (cada {TOR_ROTACION_CADA} canales)...")
                if _rotar_tor():
                    print(f"  ✅ Tor rotado (total rotaciones: {_tor_rotaciones})")
                    time.sleep(TOR_PAUSA_ROTACION)
                else:
                    print(f"  ⚠️ No se pudo rotar Tor, continuando con IP actual")

            try:
                t0 = time.time()
                evs_canal, mensajes = _procesar_canal(canal, use_tor=True)
                if evs_canal:
                    eventos.extend(evs_canal)
                    print(f"  ✅ @{canal}: {len(evs_canal)} eventos de {len(mensajes)} mensajes")
                else:
                    print(f"  ⚠️ @{canal}: {len(mensajes)} mensajes, 0 eventos")
                _registrar_resultado(canal, len(evs_canal), len(mensajes),
                                     round(time.time() - t0, 2))
                _actualizar_semilla(canal, mensajes, evs_canal)
                canales_procesados += 1
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

        try:
            _reentrenar_si_toca()
        except Exception:
            pass

    except Exception as e:
        print(f"  ⚠️ Telegram scraper: {type(e).__name__}: {e}")
        return []

    print(f"  📊 Telegram: {len(eventos)} eventos totales (canales procesados: {canales_procesados})")
    return eventos


if __name__ == "__main__":
    t0 = time.time()
    evs = scrape_telegram_events()
    print(f"Tiempo: {time.time() - t0:.0f}s, Eventos Telegram: {len(evs)}")
    for e in evs[:5]:
        print(f"  {e['nombre'][:50]} | {e.get('fecha','?')} | {e.get('lugar','?')}")