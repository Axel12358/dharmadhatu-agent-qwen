#!/usr/bin/env python3
"""
Buscador de eventos en grupos de Facebook para Dharmadhatu Bot v5.
4 estrategias en cascada con rotación de países y keywords enriquecidas.

Estrategias:
  1. DuckDuckGo vía Playwright → extraer enlaces de grupos de Facebook
  2. requests + BeautifulSoup con proxies (fallback si Playwright falla)
  3. Lista conocida ampliada + grupos descubiertos en ejecuciones anteriores
  4. Búsqueda directa en Facebook con cookies (si existen)

Rotación: cada ejecución procesa N países y combina subgéneros × países,
guardando el estado en rotation_state.json (países) y
estado_busqueda.json (combinaciones subgénero×país) para cobertura
progresiva sin repetir búsquedas.

Uso:
  python3 scrapers/facebook_mcp.py                  # Ejecutar
  python3 scrapers/facebook_mcp.py --setup-cookies   # Generar cookies
  python3 scrapers/facebook_mcp.py --reset-rotation  # Reset rotación de países
  python3 scrapers/facebook_mcp.py --reset-search    # Reset estado de búsqueda
"""

import asyncio
import json
import os
import random
import re
import hashlib
import sys
import time
import socket
import urllib.parse
from pathlib import Path
from datetime import datetime, timezone

socket.setdefaulttimeout(6)

_PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

try:
    from bs4 import BeautifulSoup
except ImportError:
    BeautifulSoup = None

CACHE_FILE = str(Path(_PROJECT_ROOT) / "grupos_cache.json")
COOKIES_FILE = str(Path(_PROJECT_ROOT) / "facebook_cookies.json")
CONFIG_FILE = str(Path(_PROJECT_ROOT) / "config_grupos.json")
ROTATION_STATE_FILE = str(Path(_PROJECT_ROOT) / "rotation_state.json")
SEARCH_STATE_FILE = str(Path(_PROJECT_ROOT) / "estado_busqueda.json")
GRUPOS_EXPORT_FILE = str(Path(_PROJECT_ROOT) / "grupos_encontrados.json")
EVENTOS_VISITADOS_FILE = str(Path(_PROJECT_ROOT) / "eventos_visitados.json")
# Versión del formato del caché: invalidar si cambia la lógica de extracción
ENRIQUECIMIENTO_CACHE_VERSION = 2

    # Límite de páginas de FB visitadas por ejecución en el enriquecimiento
MAX_VISITAS_ENRIQUECIMIENTO = 80
VISITA_TIMEOUT_MS = 20000

MAX_KEYWORDS_POR_RUN = 50

# Techo de eventos nuevos por pasada de scrape_facebook_events (volumen agresivo)
MAX_EVENTOS_POR_RUN = 80

# Sobrescritura dinámica desde loop_mejora.py (None = usar valor por defecto).
# Mecanismo aditivo: si no se llama a set_limites_loop(), todo se comporta
# exactamente como antes (no se rompe nada).
CONFIG_RUNTIME = {
    "max_keywords_por_run": None,
    "max_visitas_enriquecimiento": None,
}


def set_limites_loop(max_keywords=None, max_visitas=None):
    """Permite que loop_mejora.py ajuste los límites de FB sin tocar constantes.

    Pasar None deja el valor por defecto (reversible). Se resetea a None al
    terminar cada scrape_facebook_events() para no contaminar futuras llamadas.
    """
    if max_keywords is not None:
        CONFIG_RUNTIME["max_keywords_por_run"] = int(max_keywords)
    if max_visitas is not None:
        CONFIG_RUNTIME["max_visitas_enriquecimiento"] = int(max_visitas)

# El SERP (Startpage/DDG/Bing/Google) + visitas a páginas públicas de FB necesita
# más tiempo con los delays ampliados (10-20s) y 4 motores en cascada.
SERP_TIMEOUT = 180

# Términos psytrance para filtrar ruido en los resultados del SERP
# (términos fuertes: un evento real de la escena casi siempre los contiene)
PSY_KEYWORDS = [
    "psytrance", "darkpsy", "psychill", "psybient", "fullon",
    "full-on", "progressive psy", "goa trance", "hitech", "hi-tech",
    "twilight", "psycore", "suomisaundi", "psychedelic trance",
    "trance party", "psy party", "psy scene", "psy open air",
    "bush doof", "doof", "psy rave", "psytrance rave",
    "dark psy", "forest psy", "psytrance party", "psytrance festival",
    "goa", "psy", "rave",
]

# Palabras que descartan eventos claramente NO psytrance (comedy, art, music bands, etc.)
NOISE_KEYWORDS = [
    "comedy", "stand up", "open mic", "poetry", "karaoke",
    "acoustic", "jazz night", "blues", "classical concert",
    "theatre", "theater", "play", "musical", "opera",
    "art exhibition", "gallery", "painting", "sculpture",
    "wine tasting", "brunch", "breakfast", "lunch", "dinner",
    "yoga class", "meditation class", "workshop",
    "book club", "reading", "film screening", "movie night",
    "sports", "football", "basketball", "tennis", "golf",
    "marathon", "run", "cycling", "swimming",
    "market", "fair", "bazaar", "flea market",
    "fashion show", "beauty", "hair", "nail",
    "pet", "dog show", "cat show",
    "christmas", "easter", "halloween party",
    "new year eve", "silvester",
    "silent disco", "silent night",
    "dark arts", "dark hearts", "dark comedy",
    "dark sky", "dark matter", "dark chocolate",
    "tranquillity", "serenity", "peace",
    "plant tea", "tea party", "cajanka", "high voltage",
    "honeys", "in tune", "native plant",
    "pantomime", "awards", "conference", "polyamor",
    "rafting adventure", "celibataire", "bar de l",
    "skleničku", "vinařem", "vanse", "montererry",
    "no cover", "dj maiko", "american festival",
    "open house", "deep creek", "moss avenue", "blue hills",
    "winter social", "sale & fest", "annual",
    # Ampliación del filtro de ruido (aditiva): eventos claramente no-psytrance
    "wedding", "boda", "bautizo", "communion", "comunión",
    "birthday party", "cumpleaños", "baby shower", "gender reveal",
    "corporate", "empresarial", "business", "networking", "meetup",
    "webinar", "seminar", "seminario", "workshop", "curso", "masterclass",
    "fundraiser", "recogida de fondos", "caridad", "charity",
    "food festival", "gastronomía", "street food", "food market",
    "craft fair", "mercado artesanal", "art market",
    "music festival", "festival de música", "rock concert", "pop concert",
    "indie concert", "metal concert", "folk concert", "hip hop concert",
    "reggae concert", "latin concert", "gospel", "choir", "coro",
    "orchestra", "orquesta", "symphony", "sinfonía",
    "cinema", "cine", "film festival", "festival de cine",
    "photography", "fotografía", "exposición de arte", "expo",
    "fashion week", "semana de la moda",
    "trading", "investing", "crypto", "bitcoin", "blockchain",
    "gaming", "esports", "video game", "videojuegos", "lan party",
    "superbowl", "olympics", "olimpiadas", "world cup", "mundial",
    "quinceañera", "prom", "graduation", "graduación",
    "retiro", "retreat", "ayurveda", "detox", "wellness",
    "zumba", "pilates", "crossfit", "spinning", "bootcamp",
    "climbing", "escalada", "hiking", "senderismo", "sailing", "regata",
    "rodeo", "bullfight", "corrida de toros", "gala dinner", "cena de gala",
    "soul food", "restaurant opening", "inauguración de restaurante",
    "coffee tasting", "cata de café", "whisky tasting", "cata de whisky",
    "beer festival", "oktoberfest", "harvest festival", "fiesta de la vendimia",
    "pride parade", "desfile", "march", "manifestación", "protest",
    "town hall", "ayuntamiento", "city council", "asamblea",
    "convention", "congreso", "exhibición", "trade show", "feria de muestras",
    "open day", "jornada de puertas abiertas", "puertas abiertas",
    "kids", "niños", "infantil", "family day", "día de la familia",
    "church", "iglesia", "mosque", "sinagoga", "templo", "sermon",
    "funeral", "memorial service", "servicio conmemorativo",
    "fundacion", "fundación", "ong", "nonprofit", "sin ánimo de lucro",
    "airport", "aeropuerto", "station opening", "inauguración de estación",
    "escapes", "escape room", "escape game", "arcade",
    "quiz night", "trivia", "bingo night", "casino night",
    "dance competition", "concurso de baile", "pageant", "concurso de belleza",
    "pet expo", "animal shelter", "protectora de animales",
    "yoga", "meditación", "reiki", "chakra", "crystal healing",
    "garden party", "fiesta de jardín", "bbq", "barbacoa",
    "picnic", "picnic comunal", "potluck",
]

from scrapers.event_extractor import EventExtractor, VENUE_TYPE_KEYWORDS
from scrapers.anti_block import get_anti_block, AntiBlock
from loop_optimizer import get_optimizer

_extractor = EventExtractor()
_anti_block = get_anti_block()
_optimizer = get_optimizer()

CONOCIDOS = {
    "psytrancefamily": "Psytrance Family",
    "goapsytrance": "Psytrance & Goa Trance",
    "psychedelicmindz": "Psychedelic Mindz",
    "darkpsytrancefamily": "Dark Psytrance Family",
    "forestpsytrance": "Forest Psytrance Worldwide",
    "psytranceeventsworldwide": "Psytrance Events Worldwide",
    "psytranceespania": "Psytrance España",
    "psytrancegermany": "Psytrance Germany",
    "psytrancebrasil": "Psytrance Brasil",
    "psytrancecommunity": "Psytrance Community",
    "fullonpsytrance": "Full-On Psytrance",
    "psychedelicfestivals": "Psychedelic Festivals Worldwide",
    "psytrancepartypeople": "Psytrance Party People",
    "psytrance": "Psytrance",
    "goatrancefamily": "Goa Trance Family",
    "psytranceculture": "Psytrance Culture",
    "darkpsyfamily": "Darkpsy Family",
    "psytranceuniverse": "Psytrance Universe",
    "psytranceworld": "Psytrance World",
    "psytranceaddicts": "Psytrance Addicts",
    "psytrancemovement": "Psytrance Movement",
    "suomipsy": "Suomi Psytrance",
    "progressivepsytrance": "Progressive Psytrance",
    "twilightpsytrance": "Twilight Psytrance",
    "hitechpsytrance": "Hi-Tech Psytrance",
    "psytranceargentina": "Psytrance Argentina",
    "psytrancemexico": "Psytrance México",
    "psytrancechile": "Psytrance Chile",
    "psytrancecolombia": "Psytrance Colombia",
    "psytranceportugal": "Psytrance Portugal",
    "psytranceitalia": "Psytrance Italia",
    "psytranceuk": "Psytrance UK",
    "psytrancejapan": "Psytrance Japan",
    "psytranceaustralia": "Psytrance Australia",
    "forestfamily": "Forest Family",
    "nightfullon": "Night Full-On",
    "morningfullon": "Morning Full-On",
    "psytranceindia": "Psytrance India",
    "psytranceisrael": "Psytrance Israel",
    "psytranceusa": "Psytrance USA",
    "psytrancecostarica": "Psytrance Costa Rica",
    "psytranceperu": "Psytrance Perú",
    "psytranceecuador": "Psytrance Ecuador",
    "psytrancevenezuela": "Psytrance Venezuela",
    "psytrancebolivia": "Psytrance Bolivia",
    "psytranceuruguay": "Psytrance Uruguay",
    "psytranceparaguay": "Psytrance Paraguay",
    "psytranceguatemala": "Psytrance Guatemala",
    "psytrancehonduras": "Psytrance Honduras",
    "psytranceelsalvador": "Psytrance El Salvador",
    "psytrancepanama": "Psytrance Panamá",
    "psytrancecuba": "Psytrance Cuba",
    "psytrancepuertorico": "Psytrance Puerto Rico",
    "psytrancerepublicadominicana": "Psytrance República Dominicana",
    "psytrancefrancia": "Psytrance Francia",
    "psytrancebelgica": "Psytrance Bélgica",
    "psytrancepaisesbajos": "Psytrance Países Bajos",
    "psytranceaustria": "Psytrance Austria",
    "psytrancepolonia": "Psytrance Polonia",
    "psytrancegrecia": "Psytrance Grecia",
    "psytrancehungria": "Psytrance Hungría",
    "psytrancecroacia": "Psytrance Croacia",
    "psytranceucrania": "Psytrance Ucrania",
    "psytrancerumania": "Psytrance Rumania",
    "psytrancebulgaria": "Psytrance Bulgaria",
    "psytranceserbia": "Psytrance Serbia",
    "psytrancemacedonia": "Psytrance Macedonia",
    "psytranceeslovenia": "Psytrance Eslovenia",
    "psytranceeslovaquia": "Psytrance Eslovaquia",
    "psytrancechequia": "Psytrance Chequia",
    "psytrancefinlandia": "Psytrance Finlandia",
    "goatrance": "Goa Trance",
    "psytranceglobal": "Psytrance Global",
    "psytranceevents": "Psytrance Events",
    "psychedelictranceevents": "Psychedelic Trance Events",
    "goatranceevents": "Goa Trance Events",
    "darkpsyevents": "Dark Psy Events",
    "forestpsyevents": "Forest Psy Events",
    "hitechevents": "Hi-Tech Events",
    "fullonevents": "Full-On Events",
    "psytranceparties": "Psytrance Parties",
    "psytrancefestivals": "Psytrance Festivals",
    "psytranceworldwide": "Psytrance Worldwide",
    "psytrancefamilygathering": "Psytrance Family Gathering",
    "goagil": "Goa Gil",
    "psytranceasia": "Psytrance Asia",
    "psytranceoceania": "Psytrance Oceania",
    "psytranceafrica": "Psytrance Africa",
    "psytrancemiddleeast": "Psytrance Middle East",
    "psytranceukraine": "Psytrance Ukraine",
    "psytrancepoland": "Psytrance Poland",
    "psytranceczech": "Psytrance Czech",
    "psytrancehungary": "Psytrance Hungary",
    "psytrancegreece": "Psytrance Greece",
    "psytranceportugalia": "Psytrance Portugal",
    "psytranceitaly": "Psytrance Italy",
    "psytrancefrance": "Psytrance France",
    "psytranceusa": "Psytrance USA",
    "psytrancecanada": "Psytrance Canada",
    "psytrancemexico": "Psytrance México",
    "psytrancebrazil": "Psytrance Brazil",
    "psytranceargentina": "Psytrance Argentina",
    "psytrancechile": "Psytrance Chile",
    "psytrancecolombia": "Psytrance Colombia",
    "psytranceperu": "Psytrance Perú",
    "psytrancejapan": "Psytrance Japan",
    "psytranceaustralia": "Psytrance Australia",
    "psytranceindia": "Psytrance India",
    "psytranceisrael": "Psytrance Israel",
    "psytrancecorea": "Psytrance Korea",
    "psytrancechina": "Psytrance China",
    "psytranceuk": "Psytrance UK",
    "psytrancegermany": "Psytrance Germany",
    "psytranceespaña": "Psytrance España",
}

EXTRA_KEYWORDS = [
    "trance", "festival", "rave", "party", "eventos",
    "underground", "comunidad", "tribe", "gathering", "psy",
]

TIMEOUT_PER_STRATEGY = 20

# Subgéneros: alias de búsqueda (variantes para DDG/FB) y etiqueta corta
SUBGENERO_ALIASES = {
    "darkpsy": ["dark psy", "darkpsy", "dark"],
    "forest": ["forest psy", "forestpsy", "forest"],
    "psychill": ["psychill", "psy chill", "psy-chill"],
    "psybient": ["psybient", "psybiental", "psy ambience"],
    "fullon": ["full on", "fullon", "full-on"],
    "progressive": ["progressive psy", "prog psy", "progressive"],
    "goa": ["goa", "goa trance", "goatrance"],
    "hitech": ["hi tech", "hitech", "hi-tech"],
    "twilight": ["twilight psy", "twilight"],
    "psycore": ["psycore", "psy core"],
    "suomisaundi": ["suomisaundi", "suomi psy", "suomi"],
    "zenon": ["zenon psy", "zenon"],
    "psychedelic": ["psychedelic", "psychedelic trance", "psychedelics"],
    "psytrance": ["psytrance", "psy trance", "psy"],
}

SUBGENERO_ETIQUETAS = {
    "darkpsy": "dark",
    "forest": "forest",
    "psychill": "psychill",
    "psybient": "psybient",
    "fullon": "fullon",
    "progressive": "progressive",
    "goa": "goa",
    "hitech": "hitech",
    "twilight": "twilight",
    "psycore": "psycore",
    "suomisaundi": "suomi",
    "zenon": "zenon",
    "psychedelic": "psychedelic",
    "psytrance": "psytrance",
}


# ---------------------------------------------------------------------- #
# ROTACIÓN DE PAÍSES
# ---------------------------------------------------------------------- #
def _load_rotation_state():
    if Path(ROTATION_STATE_FILE).exists():
        try:
            with open(ROTATION_STATE_FILE) as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return {"procesados": [], "ultimo_run": None}


def _save_rotation_state(state):
    try:
        with open(ROTATION_STATE_FILE, "w") as f:
            json.dump(state, f, indent=2)
    except IOError:
        pass


def _reset_rotation():
    _save_rotation_state({"procesados": [], "ultimo_run": None})
    print("   🔄 Rotación reiniciada.")


def _seleccionar_paises_rotacion(config):
    todos = config.get("paises", [])
    if not todos:
        return []
    max_por_run = config.get("rotacion_paises_por_run", 10)
    state = _load_rotation_state()
    procesados = set(state.get("procesados", []))
    pendientes = [p for p in todos if p.get("nombre") not in procesados]
    if not pendientes:
        procesados = set()
        pendientes = list(todos)
        print("   🔄 Todos cubiertos. Reiniciando ciclo.")
    random.shuffle(pendientes)
    seleccionados = pendientes[:max_por_run]
    for p in seleccionados:
        procesados.add(p.get("nombre"))
    state["procesados"] = list(procesados)
    state["ultimo_run"] = datetime.now().isoformat()
    _save_rotation_state(state)
    return seleccionados


# ---------------------------------------------------------------------- #
# ESTADO DE BÚSQUEDA (subgénero × país) — cobertura progresiva
# ---------------------------------------------------------------------- #
def _load_search_state():
    if Path(SEARCH_STATE_FILE).exists():
        try:
            with open(SEARCH_STATE_FILE) as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return {"combos_procesados": [], "ultimo_run": None}


def _save_search_state(state):
    try:
        with open(SEARCH_STATE_FILE, "w") as f:
            json.dump(state, f, indent=2)
    except IOError:
        pass


def _reset_search_state():
    _save_search_state({"combos_procesados": [], "ultimo_run": None})
    print("   🔄 Estado de búsqueda reiniciado.")


def _combo_key(sub, pais):
    return f"{sub}::{pais}"


def _generar_keywords(config, paises_seleccionados):
    """Genera keywords combinando subgénero × país × ciudad × extras.

    Fase 1 (base obligatoria): cada subgénero × cada país seleccionado,
    garantizando que TODOS los subgéneros aparezcan en cada ejecución.

    Fase 2 (expansión rotada): añade ciudad + palabras extra para combos
    (subgénero, país) aún no procesados según estado_busqueda.json, para
    cubrir todas las combinaciones con el tiempo sin repetir búsquedas.

    Garantía de cobertura: la Fase 1 (todos los subgéneros × países) SIEMPRE
    se conserva íntegra. Si hay más de max_keywords_por_run, el recorte solo
    afecta a la Fase 2 (expansión), nunca a la base de subgéneros.
    """
    subgeneros = config.get("subgeneros", ["psytrance"])
    max_kw = CONFIG_RUNTIME["max_keywords_por_run"] or config.get(
        "max_keywords_por_run", MAX_KEYWORDS_POR_RUN
    )
    extras = list(EXTRA_KEYWORDS)

    state = _load_search_state()
    procesados = set(state.get("combos_procesados", []))

    # Fase 1: base obligatoria subgénero × país (todos los subgéneros)
    base = []
    for sub in subgeneros:
        aliases = SUBGENERO_ALIASES.get(sub, [sub])
        principal = aliases[0]
        base.append(sub)
        base.append(principal)
        for alias in aliases[1:]:
            base.append(alias)
        for p in paises_seleccionados:
            pais = p.get("nombre", "")
            base.append(f"{principal} {pais}")

    # Fase 2: expansión rotada (ciudades + extras) por combo
    combos = [
        _combo_key(sub, p.get("nombre", ""))
        for sub in subgeneros
        for p in paises_seleccionados
    ]
    pendientes = [c for c in combos if c not in procesados]
    if not pendientes:
        procesados = set()
        pendientes = list(combos)
        print("   🔄 Todas las combinaciones cubiertas. Reiniciando ciclo.")

    random.shuffle(pendientes)
    extras_base = random.sample(extras, min(3, len(extras)))
    extras_final = random.sample(extras, min(2, len(extras)))
    combos_usados = []
    expansion = []
    t_gen_start = time.time()

    for combo in pendientes:
        if len(base) + len(expansion) >= max_kw:
            break
        if time.time() - t_gen_start > 60:
            break
        sub, pais = combo.split("::", 1)
        pais_obj = next(
            (p for p in paises_seleccionados if p.get("nombre") == pais), {}
        )
        ciudades = pais_obj.get("ciudades", [])[:3]
        aliases = SUBGENERO_ALIASES.get(sub, [sub])
        principal = aliases[0]

        expansion.append(f"{principal} {pais}")
        for extra in extras_base:
            expansion.append(f"{principal} {pais} {extra}")

        for ciudad in ciudades:
            expansion.append(f"{principal} {ciudad}")
            for extra in extras_base[:2]:
                expansion.append(f"{principal} {ciudad} {extra}")

        for alias in aliases[1:2]:
            expansion.append(f"{alias} {pais}")

        for extra in extras_final:
            expansion.append(f"{sub} {pais} {extra}")

        procesados.add(combo)
        combos_usados.append(combo)

    state["combos_procesados"] = sorted(procesados)
    state["ultimo_run"] = datetime.now().isoformat()
    _save_search_state(state)

    # Recorte: la base de subgéneros es intocable; solo se acota la expansión.
    if len(base) >= max_kw:
        return base[:max_kw]
    return base + expansion[:max_kw - len(base)]


def _seleccionar_keywords_diversas(keywords, n=8):
    """Selecciona keywords garantizando variedad de subgéneros.

    Agrupa por subgénero (pista más específica primero) y, si hay huecos,
    los rellena con el resto. Así cada ejecución busca todos los subgéneros
    y no solo los genéricos 'Psytrance <país>'.
    """
    orden_subgeneros = list(SUBGENERO_ALIASES.keys())
    agrupadas = {sub: [] for sub in orden_subgeneros}
    restantes = []
    for kw in keywords:
        kw_lower = kw.lower()
        sub_detectado = None
        for sub in orden_subgeneros:
            aliases = SUBGENERO_ALIASES.get(sub, [sub])
            if any(alias.lower() in kw_lower for alias in aliases):
                sub_detectado = sub
                break
        if sub_detectado is not None:
            agrupadas[sub_detectado].append(kw)
        else:
            restantes.append(kw)

    elegidas = []
    random.shuffle(orden_subgeneros)
    for sub in orden_subgeneros:
        pool = agrupadas[sub]
        if pool:
            elegidas.append(random.choice(pool))
        if len(elegidas) >= n:
            return elegidas
    random.shuffle(restantes)
    elegidas.extend(restantes[:n - len(elegidas)])
    return elegidas[:n]


def _generar_grupos_desde_config(paises_seleccionados):
    grupos = []
    for pais_obj in paises_seleccionados:
        pais = pais_obj.get("nombre", "")
        ciudades = pais_obj.get("ciudades", [])
        slug = _slug(pais)
        grupos.append({
            "id": slug, "nombre": f"Psytrance {pais}",
            "url": f"https://facebook.com/groups/{slug}",
            "tipo_grupo": _clasificar_grupo(f"Psytrance {pais}"),
        })
        for ciudad in ciudades[:3]:
            cslug = _slug(ciudad)
            grupos.append({
                "id": cslug, "nombre": f"Psytrance {ciudad}",
                "url": f"https://facebook.com/groups/{cslug}",
                "tipo_grupo": _clasificar_grupo(f"Psytrance {ciudad}"),
            })
    return grupos


def _clasificar_grupo(nombre, url=""):
    """Etiqueta un grupo por subgénero y tipo de localización."""
    texto = f"{nombre} {url}".lower()
    etiquetas = []

    for sub, aliases in SUBGENERO_ALIASES.items():
        if any(alias in texto for alias in aliases):
            etiqueta = SUBGENERO_ETIQUETAS.get(sub, sub)
            if etiqueta not in etiquetas:
                etiquetas.append(etiqueta)

    for vtype, keywords in VENUE_TYPE_KEYWORDS.items():
        if any(kw in texto for kw in keywords):
            if vtype not in etiquetas:
                etiquetas.append(vtype)

    return etiquetas


def _slug(text):
    import unicodedata
    text = unicodedata.normalize("NFKD", text)
    text = text.encode("ascii", "ignore").decode("ascii")
    return text.lower().replace(" ", "")


# ---------------------------------------------------------------------- #
# GRUPOS DESCUBIERTOS ANTERIORES (auto-crecimiento)
# ---------------------------------------------------------------------- #
def _cargar_grupos_descubiertos():
    if not Path(GRUPOS_EXPORT_FILE).exists():
        return {}
    try:
        with open(GRUPOS_EXPORT_FILE) as f:
            data = json.load(f)
        return {g["id"]: g for g in data.get("groups", [])}
    except (json.JSONDecodeError, IOError):
        return {}


# ---------------------------------------------------------------------- #
# CLASE PRINCIPAL
# ---------------------------------------------------------------------- #
class FacebookEventsFinder:
    def __init__(self):
        self.cache = self._load_cache()
        self.cookies = self._load_cookies()
        self.extractor = EventExtractor()
        self.anti_block = get_anti_block()
        self.optimizer = get_optimizer()
        self.grupos_encontrados = {}
        self.grupos_nuevos = []

    def _load_cache(self):
        if Path(CACHE_FILE).exists():
            try:
                with open(CACHE_FILE) as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError):
                return {}
        return {}

    def _save_cache(self):
        try:
            with open(CACHE_FILE, "w") as f:
                json.dump(self.cache, f, indent=2)
        except IOError:
            pass

    def _load_cookies(self):
        if Path(COOKIES_FILE).exists():
            try:
                with open(COOKIES_FILE) as f:
                    data = json.load(f)
                if isinstance(data, list) and len(data) > 0:
                    return data
            except (json.JSONDecodeError, IOError):
                pass
        return None

    def _cache_key(self, text):
        return hashlib.md5(text.encode()).hexdigest()

    def _agregar_grupo(self, gid, nombre, url, fuente="desconocido"):
        if gid in self.grupos_encontrados:
            return
        self.grupos_encontrados[gid] = {
            "id": gid, "nombre": nombre, "url": url,
            "fuente": fuente,
            "fecha_descubrimiento": datetime.now(timezone.utc).isoformat(),
            "tipo_grupo": _clasificar_grupo(nombre, url),
        }
        self.grupos_nuevos.append(gid)

    def _es_grupo_psytrance(self, nombre, url=""):
        nombre_lower = nombre.lower()
        pistas = [
            "psytrance", "psy trance", "goa", "trance", "psy",
            "dark psy", "forest psy", "fullon", "full-on",
            "psychedelic", "goatrance", "hitech", "hi-tech",
            "darkpsy", "forestpsy", "progressive psy", "prog psy",
            "psychill", "psybient", "twilight", "psycore",
            "suomisaundi", "suomi psy", "zenon",
        ]
        if any(p in nombre_lower for p in pistas):
            return True
        if any(sub in nombre_lower for sub in ["festival", "party", "rave", "event"]):
            return True
        return False

    def _nombre_desde_keyword(self, kw, gid):
        """Construye un nombre legible para el grupo usando el keyword buscado."""
        kw_lower = kw.lower()
        for sub, aliases in SUBGENERO_ALIASES.items():
            for alias in aliases:
                if alias in kw_lower:
                    etiqueta = SUBGENERO_ETIQUETAS.get(sub, sub)
                    resto = kw_lower.replace(alias, "", 1).strip()
                    if resto:
                        palabras = [w.capitalize() for w in resto.split()]
                        return f"{alias.capitalize()} {' '.join(palabras)}"
                    return alias.capitalize()
        return f"Psytrance {gid}"

    # ------------------------------------------------------------------ #
    # ESTRATEGIA 1: DuckDuckGo vía Playwright → enlaces FB
    # ------------------------------------------------------------------ #
    async def _via_duckduckgo_playwright(self, keywords):
        eventos = []
        t0 = time.time()
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            return eventos

        search_kws = _seleccionar_keywords_diversas(keywords, 8)
        grupos_nuevos = []

        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(
                    headless=True,
                    args=["--no-sandbox", "--disable-dev-shm-usage",
                           "--disable-blink-features=AutomationControlled"],
                )
                context = await self.anti_block.create_stealth_context(browser)

                sem = asyncio.Semaphore(2)

                async def buscar_keyword(kw):
                    async with sem:
                        page = None
                        try:
                            page = await context.new_page()
                            await self.anti_block.apply_playwright_stealth(page)

                            query = f"site:facebook.com/groups {kw}"
                            url = f"https://duckduckgo.com/?q={query.replace(' ', '+')}"
                            await page.goto(url, timeout=15000, wait_until="domcontentloaded")
                            await asyncio.sleep(random.uniform(1, 2))

                            html = await page.content()
                            links = re.findall(
                                r'href="(https?://(?:www\.)?facebook\.com/groups/[^"&?]+)"',
                                html
                            )
                            links += re.findall(
                                r'href="(https?://l\.facebook\.com/l\.php\?u=(https%3A%2F%2F(?:www\.)?facebook\.com%2Fgroups%2F[^"&]+))"',
                                html
                            )

                            for link in links:
                                if isinstance(link, tuple):
                                    link = link[1] if len(link) > 1 else link[0]
                                link = link.split("?")[0].rstrip("/")
                                m = re.search(r'facebook\.com/groups/([^/?]+)', link)
                                if m:
                                    gid = m.group(1)
                                    gurl = f"https://facebook.com/groups/{gid}"
                                    nombre = self._nombre_desde_keyword(kw, gid)
                                    if self._es_grupo_psytrance(gid) or self._es_grupo_psytrance(nombre):
                                        self._agregar_grupo(gid, nombre, gurl, "DuckDuckGo")
                                        grupos_nuevos.append(gid)

                            await page.close()
                            page = None
                        except Exception:
                            pass
                        finally:
                            if page:
                                try:
                                    await page.close()
                                except Exception:
                                    pass

                tareas = [buscar_keyword(kw) for kw in search_kws]
                await asyncio.gather(*tareas, return_exceptions=True)
                await browser.close()
        except Exception:
            pass

        for gid in grupos_nuevos[:20]:
            gdata = self.grupos_encontrados.get(gid, {})
            gname = gdata.get("nombre", gid)
            gurl = gdata.get("url", f"https://facebook.com/groups/{gid}")
            try:
                async with asyncio.timeout(TIMEOUT_PER_STRATEGY):
                    evs = await self._extraer_grupo_playwright(gid, gname, gurl)
                    eventos.extend(evs)
            except Exception:
                pass

        dt = time.time() - t0
        self.optimizer.registrar_resultado("duckduckgo_playwright", eventos, dt, bool(eventos))
        return eventos

    async def _extraer_grupo_playwright(self, gid, gname, gurl):
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            return []

        eventos = []
        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(
                    headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"],
                )
                context = await self.anti_block.create_stealth_context(browser)
                page = await context.new_page()
                await self.anti_block.apply_playwright_stealth(page)

                await page.goto(gurl, timeout=12000, wait_until="domcontentloaded")
                await asyncio.sleep(random.uniform(1, 2))

                html = await page.content()
                await page.close()
                await browser.close()

                soup_text = re.sub(r'<[^>]+>', ' ', html)
                soup_text = re.sub(r'\s+', ' ', soup_text)

                if len(soup_text) > 50:
                    evs = self.extractor.extract_all(soup_text[:5000], gname, gurl)
                    kw_subgenero = _extraer_subgenero_desde_keyword(gname)
                    for ev in evs:
                        ev["subgenero"] = kw_subgenero
                    eventos.extend(evs)
        except Exception:
            pass
        return eventos

    # ------------------------------------------------------------------ #
    # ESTRATEGIA 2: requests + BeautifulSoup con proxies
    # ------------------------------------------------------------------ #
    async def _via_requests(self, keywords):
        """Estrategia 2 (reactivada): grupos FB conocidos vía curl_cffi + Tor.

        Sustituye el requests plano (bloqueado en Tor) por core.http_client
        (TLS de navegador + proxy SOCKS5, sin IP real). Si get_html devuelve
        None, se omite el grupo y se pasa al siguiente (no cuelga).
        """
        eventos = []
        t0 = time.time()
        try:
            from bs4 import BeautifulSoup
        except ImportError:
            return eventos
        try:
            from core.http_client import get_html
        except Exception:
            return eventos

        grupos_a_visitar = list(self.grupos_encontrados.keys())[:10]
        random.shuffle(grupos_a_visitar)

        for gid in grupos_a_visitar:
            if time.time() - t0 > TIMEOUT_PER_STRATEGY:
                break
            gdata = self.grupos_encontrados.get(gid, {})
            gname = gdata.get("nombre", gid)
            gurl = gdata.get("url", f"https://facebook.com/groups/{gid}")

            # Tor + curl_cffi (sin IP real). get_html -> None si falla/block.
            html = await asyncio.to_thread(get_html, gurl, 15)
            if not html:
                continue
            soup = BeautifulSoup(html, "html.parser")
            body = soup.get_text(separator=" ", strip=True)
            evs = self.extractor.extract_all(body[:3000], gname, gurl)
            kw_subgenero = _extraer_subgenero_desde_keyword(gname)
            for ev in evs:
                ev["subgenero"] = kw_subgenero
            eventos.extend(evs)

        dt = time.time() - t0
        self.optimizer.registrar_resultado("requests_tor_curl", eventos, dt, bool(eventos))
        return eventos

    # ------------------------------------------------------------------ #
    # ESTRATEGIA 3: Lista conocida + descubiertos (fallback)
    # ------------------------------------------------------------------ #
    async def _via_grupos_conocidos(self, keywords):
        """Grupos conocidos de Facebook: visita cada grupo público y extrae
        eventos reales de sus publicaciones usando Playwright o requests.
        Sin cookies: extrae del HTML público del grupo (sin login).
        Con cookies: extrae posts reales del grupo con más detalle.
        """
        eventos = []
        t0 = time.time()
        grupos_a_visitar = {}

        # Construir lista de grupos: conocidos + descubiertos + encontrados
        for gid, gname in CONOCIDOS.items():
            grupos_a_visitar[gid] = {"nombre": gname, "url": f"https://facebook.com/groups/{gid}"}
        descubiertos = _cargar_grupos_descubiertos()
        for gid, gdata in descubiertos.items():
            if gid not in grupos_a_visitar:
                grupos_a_visitar[gid] = {"nombre": gdata.get("nombre", gid),
                                         "url": gdata.get("url", f"https://facebook.com/groups/{gid}")}
        for gid, gdata in self.grupos_encontrados.items():
            if gid not in grupos_a_visitar:
                grupos_a_visitar[gid] = gdata

        # Filtrar por keywords si se proporcionan
        if keywords:
            kw_lower = [kw.lower() for kw in keywords]
            grupos_filtrados = {}
            for gid, gdata in grupos_a_visitar.items():
                if any(kw in gdata["nombre"].lower() for kw in kw_lower):
                    grupos_filtrados[gid] = gdata
            grupos_a_visitar = grupos_filtrados

        # Limitar a 60 grupos por ejecución para evitar timeouts
        grupos_list = list(grupos_a_visitar.items())[:60]
        random.shuffle(grupos_list)

        # Estrategia A: Playwright (más robusto para grupos públicos)
        for gid, gdata in grupos_list:
            if time.time() - t0 > 90:
                break
            gname = gdata.get("nombre", gid)
            gurl = gdata.get("url", f"https://facebook.com/groups/{gid}")
            try:
                evs = await self._extraer_grupo_playwright(gid, gname, gurl)
                kw_subgenero = _extraer_subgenero_desde_keyword(gname)
                for ev in evs:
                    ev["subgenero"] = kw_subgenero
                eventos.extend(evs)
            except Exception:
                pass

        # Estrategia B: requests (fallback si Playwright no disponible o lento)
        if len(eventos) < 10:
            for gid, gdata in grupos_list[:10]:
                if time.time() - t0 > 90:
                    break
                gname = gdata.get("nombre", gid)
                gurl = gdata.get("url", f"https://facebook.com/groups/{gid}")
                try:
                    evs = await self._requests_grupo(gurl, gname)
                    kw_subgenero = _extraer_subgenero_desde_keyword(gname)
                    for ev in evs:
                        ev["subgenero"] = kw_subgenero
                    eventos.extend(evs)
                except Exception:
                    pass

        dt = time.time() - t0
        self.optimizer.registrar_resultado("grupos_conocidos", eventos, dt, bool(eventos))
        return eventos

    async def _requests_grupo(self, gurl, gname):
        """Extrae eventos de la página pública de un grupo FB vía curl_cffi + Tor.

        Sustituye requests plano por core.http_client (sin IP real). Si
        get_html devuelve None, omite el grupo. No requiere JavaScript.
        """
        eventos = []
        try:
            from bs4 import BeautifulSoup as _BS
            from core.http_client import get_html
            mb = gurl
            if "mbasic.facebook.com" not in mb:
                mb = mb.replace("https://facebook.com", "https://mbasic.facebook.com")
                mb = mb.replace("http://facebook.com", "https://mbasic.facebook.com")
            html = await asyncio.to_thread(get_html, mb, 15)
            if not html:
                return eventos
            soup = _BS(html, "html.parser")
            body = soup.get_text(separator=" ", strip=True)
            if len(body) < 50:
                return eventos
            evs = self.extractor.extract_all(body[:5000], gname, gurl)
            for ev in evs:
                ev["subgenero"] = _extraer_subgenero_desde_keyword(gname)
            eventos.extend(evs)
        except Exception:
            pass
        return eventos

    # ------------------------------------------------------------------ #
    # ESTRATEGIA 2b: curl_cffi + Tor (reactivación FB sin Playwright)
    # ------------------------------------------------------------------ #
    async def _via_curl_cffi(self, keywords):
        """Reactivación de Facebook SIN cookies ni Playwright (vía Tor).

        Estrategia: busca site:facebook.com/events <kw> psytrance en DDG-lite
        usando curl_cffi + Tor (TLS de navegador, sin IP real), y extrae los
        eventos directamente de los *snippets* de resultados (título + snippet),
        que ya traen nombre, fecha y lugar. NO visita las páginas de Facebook
        (login-walled vía Tor), evitando cuelgues y bloqueos.

        Si get_html devuelve None (bloqueo/timeout), omite y sigue. Acotado
        por tiempo (~90s) para no colgar. No usa conexión directa.
        """
        eventos = []
        t0 = time.time()
        try:
            from core.http_client import get_html
        except Exception:
            return eventos
        from bs4 import BeautifulSoup
        import re as _re
        import urllib.parse as _up

        def _buscar_bloques(kw):
            q = f"site:facebook.com/events {kw} psytrance"
            html = get_html(
                "https://lite.duckduckgo.com/lite/?q=" + _up.quote(q), 20
            )
            if not html:
                return []
            soup = BeautifulSoup(html, "html.parser")
            links = soup.select("a.result-link")
            snips = soup.select(".result-snippet")
            bloques = []
            for a, sn in zip(links, snips):
                href = a.get("href", "")
                m = _re.search(r"uddg=([^&]+)", href)
                fburl = _up.unquote(m.group(1)) if m else ""
                titulo = a.get_text(" ", strip=True)
                snip = sn.get_text(" ", strip=True) if sn else ""
                bloques.append((titulo, snip, fburl))
            return bloques

        kws = _seleccionar_keywords_diversas(keywords, 8)
        vistos_url = set()
        for kw in kws:
            if time.time() - t0 > 70:
                break
            try:
                bloques = await asyncio.to_thread(_buscar_bloques, kw)
            except Exception:
                bloques = []
            for titulo, snip, fburl in bloques:
                if fburl and fburl in vistos_url:
                    continue
                if fburl:
                    vistos_url.add(fburl)
                blob = f"{titulo} {snip}"
                if len(blob) < 10:
                    continue
                evs = self.extractor.extract_all(blob[:1500], "Tor", fburl)
                sub = _extraer_subgenero_desde_keyword(kw)
                for ev in evs:
                    ev["subgenero"] = sub or ev.get("subgenero", "")
                    if fburl:
                        ev["url"] = fburl
                    nombre = (ev.get("nombre") or "").lower()
                    candidato = nombre + " " + blob.lower()
                    if any(nk in candidato for nk in NOISE_KEYWORDS):
                        continue
                    # Solo eventos con fecha real (evita ruido del snippet)
                    if ev.get("fecha") in (None, "", "N/A"):
                        continue
                    eventos.append(ev)
            if len(vistos_url) >= 25:
                break

        dt = time.time() - t0
        self.optimizer.registrar_resultado(
            "curl_cffi_tor", eventos, dt, bool(eventos)
        )
        return eventos

    # ------------------------------------------------------------------ #
    # ESTRATEGIA 4: Búsqueda directa Facebook con cookies
    # ------------------------------------------------------------------ #
    async def _via_facebook_directo(self, keywords):
        eventos = []
        if not self.cookies:
            return eventos
        t0 = time.time()

        try:
            from facebook_scraper import get_posts
        except ImportError:
            return eventos

        grupos = list(CONOCIDOS.keys())
        random.shuffle(grupos)
        grupos = grupos[:8]

        for gid in grupos:
            if time.time() - t0 > TIMEOUT_PER_STRATEGY:
                break
            gname = CONOCIDOS.get(gid, gid)
            try:
                posts = await asyncio.to_thread(
                    lambda: list(get_posts(
                        group=gid, pages=1, timeout=12,
                        cookies=self.cookies,
                        options={"allow_extra_requests": False},
                    ))
                )
                for post in posts[:5]:
                    text = post.get("text") or post.get("caption") or ""
                    evs = self.extractor.extract_all(text, gname, post.get("post_url", ""))
                    # Heredar subgénero de la keyword del grupo
                    kw_subgenero = _extraer_subgenero_desde_keyword(gname)
                    for ev in evs:
                        ev["subgenero"] = kw_subgenero
                    eventos.extend(evs)
            except Exception:
                continue

        dt = time.time() - t0
        self.optimizer.registrar_resultado("facebook_directo", eventos, dt, bool(eventos))
        return eventos

    # ------------------------------------------------------------------ #
    # ESTRATEGIA 5: Búsqueda pública SERP (sin login, via Playwright)
    # ------------------------------------------------------------------ #
    async def _via_google_serp_public(self, keywords):
        """Busca eventos públicos de Facebook via SERP (site:facebook.com/events).
        Sin login. Motores en cascada según prioridad:
          1. Startpage (Playwright) — motor principal (sin CAPTCHA, 90+ resultados)
          2. DuckDuckGo (HTML POST) — respaldo
          3. Bing (Playwright) — respaldo si los anteriores no alcanzan
          4. Google (Playwright) — último recurso
        Delays de 10-20s entre consultas para evitar rate-limit.
        Extrae del SERP: título, fecha, ciudad, venue, descripción.
        """
        eventos = []
        used = set()
        t0 = time.time()

        if not keywords:
            return eventos

        # Generar consultas: seleccionar keywords BROAD (sin país) para SERP
        # Startpage funciona mejor con keywords generales que con específicas por país
        # Limitar a 10 keywords para evitar rate-limit (~10 queries máx)
        # Deduplicar y priorizar keywords que contengan "psytrance" directamente
        # Países conocidos para filtrar keywords específicas
        paises_nombres = {
            "alemania", "argentina", "australia", "austria", "bélgica", "bielorrusia",
            "bolivia", "bosnia", "brasil", "bulgaria", "canadá", "chile", "chipre",
            "colombia", "costa rica", "croacia", "dinamarca", "ecuador", "egipto",
            "eslovaquia", "eslovenia", "españa", "estados unidos", "estonia",
            "finlandia", "francia", "grecia", "guatemala", "hungría", "india",
            "indonesia", "irlanda", "islandia", "israel", "italia", "japón",
            "letonia", "lituania", "luxemburgo", "malta", "méxico", "moldavia",
            "mónaco", "montenegro", "marruecos", "noruega", "nueva zelanda",
            "países bajos", "perú", "polonia", "portugal", "reino unido",
            "república checa", "rumanía", "rusia", "san marino", "serbia",
            "singapur", "suecia", "suiza", "turquía", "ucrania", "uruguay",
        }
        seen = set()
        keywords_broad = []
        for kw in keywords:
            kw_l = kw.strip().lower()
            if kw_l in seen:
                continue
            # Filtrar keywords que contengan nombre de país: las queries
            # genéricas rinden más resultados por SERP que las específicas.
            if any(pais in kw_l for pais in paises_nombres):
                continue
            seen.add(kw_l)
            keywords_broad.append(kw.strip())
            if len(keywords_broad) >= 15:
                break

        # Priorizar keywords que ya contengan "psytrance"
        keywords_sorted = sorted(keywords_broad, key=lambda x: (
            0 if "psytrance" in x.lower() else
            1 if any(p in x.lower() for p in ["psy", "goa"]) else 2
        ))

        consultas = []
        for kw in keywords_sorted[:10]:
            if kw:
                # SIEMPRE incluir "psytrance" para asegurar relevancia
                if "psytrance" in kw.lower():
                    consultas.append(f'site:facebook.com/events {kw}')
                else:
                    consultas.append(f'site:facebook.com/events {kw} psytrance')

        if not consultas:
            return eventos

        # FASE 1: Startpage via Playwright (motor principal, 90+ resultados/query)
        try:
            from playwright.async_api import async_playwright

            async with async_playwright() as p:
                browser = await p.chromium.launch(
                    headless=True,
                    args=["--no-sandbox", "--disable-dev-shm-usage",
                           "--disable-blink-features=AutomationControlled"],
                )
                context = await self._crear_context_con_fallback(browser, url_probe=consultas[0] if consultas else None)

                for i, consulta in enumerate(consultas, 1):
                    if len(eventos) >= MAX_EVENTOS_POR_RUN:
                        break
                    if time.time() - t0 > SERP_TIMEOUT:
                        print("  ⏰ Startpage: tiempo agotado, saliendo.")
                        break

                    kw_subgenero = _extraer_subgenero_desde_keyword(keywords_sorted[i - 1])
                    bloques = await self._serp_startpage_playwright(context, consulta)

                    for b in bloques:
                        ev = self._extraer_del_serp_publico(b, subgenero=kw_subgenero)
                        if not ev:
                            continue
                        # Filtro de ruido: descartar eventos claramente NO psytrance
                        nombre_lower = (ev.get("nombre", "") or "").lower()
                        texto_lower = (b.get("texto", "") or "").lower()
                        candidato = nombre_lower + " " + texto_lower
                        if any(nk in candidato for nk in NOISE_KEYWORDS):
                            continue
                        uid = ev.get("url", "")
                        if uid not in used:
                            eventos.append(ev)
                            used.add(uid)
                            if len(eventos) >= MAX_EVENTOS_POR_RUN:
                                break

                    if i < len(consultas):
                        await asyncio.sleep(random.uniform(3.0, 5.0))

                # Enriquecer fechas visitando las páginas públicas de FB (sin login)
                if eventos and time.time() - t0 < SERP_TIMEOUT:
                    await self._enriquecer_fechas_publicas(eventos, context)

                await browser.close()

        except Exception as e:
            print(f"  ⚠️ Error en Startpage Playwright: {e}")

        # FASE 2: DuckDuckGo HTML POST como respaldo si Startpage no alcanzó 40
        if len(eventos) < MAX_EVENTOS_POR_RUN and time.time() - t0 < SERP_TIMEOUT:
            try:
                import requests as _requests

                session = _requests.Session()
                session.headers.update({
                    'User-Agent': self.anti_block.random_user_agent(),
                    'Accept': 'text/html,application/xhtml+xml',
                    'Accept-Language': 'en-US,en;q=0.9',
                })

                for i, consulta in enumerate(consultas, 1):
                    if len(eventos) >= MAX_EVENTOS_POR_RUN:
                        break
                    if time.time() - t0 > SERP_TIMEOUT:
                        print("  ⏰ DuckDuckGo: tiempo agotado, saliendo.")
                        break

                    # Rotar UA por consulta para diversificar la huella
                    session.headers.update({'User-Agent': self.anti_block.random_user_agent()})

                    kw_subgenero = _extraer_subgenero_desde_keyword(keywords_sorted[i - 1])
                    bloques = self._serp_duckduckgo_html(consulta, session)

                    for b in bloques:
                        ev = self._extraer_del_serp_publico(b, subgenero=kw_subgenero)
                        if not ev:
                            continue
                        nombre_lower = (ev.get("nombre", "") or "").lower()
                        texto_lower = (b.get("texto", "") or "").lower()
                        candidato = nombre_lower + " " + texto_lower
                        if any(nk in candidato for nk in NOISE_KEYWORDS):
                            continue
                        uid = ev.get("url", "")
                        if uid not in used:
                            eventos.append(ev)
                            used.add(uid)
                            if len(eventos) >= MAX_EVENTOS_POR_RUN:
                                break

                    if i < len(consultas):
                        await asyncio.sleep(random.uniform(3.0, 5.0))

            except Exception as e:
                print(f"  ⚠️ Error en DuckDuckGo fallback: {e}")

        # FASE 2b: Mojeek/Qwant/Brave (fallbacks ligeros sin CAPTCHA)
        if len(eventos) < MAX_EVENTOS_POR_RUN and time.time() - t0 < SERP_TIMEOUT:
            try:
                import requests as _requests
                session = _requests.Session()
                session.headers.update({
                    'User-Agent': self.anti_block.random_user_agent(),
                })
                for i, consulta in enumerate(consultas, 1):
                    if len(eventos) >= MAX_EVENTOS_POR_RUN:
                        break
                    if time.time() - t0 > SERP_TIMEOUT:
                        print("  ⏰ Mojeek/Qwant/Brave: tiempo agotado, saliendo.")
                        break
                    kw_subgenero = _extraer_subgenero_desde_keyword(keywords_sorted[i - 1])
                    # Mojeek
                    bloques = self._serp_mojeek_html(consulta, session)
                    for b in bloques:
                        ev = self._extraer_del_serp_publico(b, subgenero=kw_subgenero)
                        if not ev:
                            continue
                        nombre_lower = (ev.get("nombre", "") or "").lower()
                        texto_lower = (b.get("texto", "") or "").lower()
                        candidato = nombre_lower + " " + texto_lower
                        if any(nk in candidato for nk in NOISE_KEYWORDS):
                            continue
                        uid = ev.get("url", "")
                        if uid not in used:
                            eventos.append(ev)
                            used.add(uid)
                    # Qwant
                    bloques = self._serp_qwant_html(consulta, session)
                    for b in bloques:
                        ev = self._extraer_del_serp_publico(b, subgenero=kw_subgenero)
                        if not ev:
                            continue
                        nombre_lower = (ev.get("nombre", "") or "").lower()
                        texto_lower = (b.get("texto", "") or "").lower()
                        candidato = nombre_lower + " " + texto_lower
                        if any(nk in candidato for nk in NOISE_KEYWORDS):
                            continue
                        uid = ev.get("url", "")
                        if uid not in used:
                            eventos.append(ev)
                            used.add(uid)
                    # Brave
                    bloques = self._serp_brave_html(consulta, session)
                    for b in bloques:
                        ev = self._extraer_del_serp_publico(b, subgenero=kw_subgenero)
                        if not ev:
                            continue
                        nombre_lower = (ev.get("nombre", "") or "").lower()
                        texto_lower = (b.get("texto", "") or "").lower()
                        candidato = nombre_lower + " " + texto_lower
                        if any(nk in candidato for nk in NOISE_KEYWORDS):
                            continue
                        uid = ev.get("url", "")
                        if uid not in used:
                            eventos.append(ev)
                            used.add(uid)
                    if i < len(consultas):
                        await asyncio.sleep(random.uniform(3.0, 5.0))
            except Exception as e:
                print(f"  ⚠️ Error en Mojeek/Qwant/Brave fallback: {e}")

        # FASE 3: Bing Playwright (respaldo si los anteriores no alcanzaron 50)
        if len(eventos) < MAX_EVENTOS_POR_RUN and time.time() - t0 < SERP_TIMEOUT:
            try:
                from playwright.async_api import async_playwright

                async with async_playwright() as p:
                    browser = await p.chromium.launch(
                        headless=True,
                        args=["--no-sandbox", "--disable-dev-shm-usage",
                               "--disable-blink-features=AutomationControlled"],
                    )
                    context = await self.anti_block.create_stealth_context(browser, use_tor=True)
                    for i, consulta in enumerate(consultas[:6], 1):
                        if len(eventos) >= MAX_EVENTOS_POR_RUN:
                            break
                        if time.time() - t0 > SERP_TIMEOUT:
                            print("  ⏰ Bing: tiempo agotado, saliendo.")
                            break

                        kw_subgenero = _extraer_subgenero_desde_keyword(keywords_sorted[i - 1])
                        bloques = await self._serp_bing_playwright(context, consulta)

                        for b in bloques:
                            ev = self._extraer_del_serp_publico(b, subgenero=kw_subgenero)
                            if not ev:
                                continue
                            nombre_lower = (ev.get("nombre", "") or "").lower()
                            texto_lower = (b.get("texto", "") or "").lower()
                            candidato = nombre_lower + " " + texto_lower
                            if any(nk in candidato for nk in NOISE_KEYWORDS):
                                continue
                            uid = ev.get("url", "")
                            if uid not in used:
                                eventos.append(ev)
                                used.add(uid)
                                if len(eventos) >= MAX_EVENTOS_POR_RUN:
                                    break

                        if i < len(consultas[:6]):
                            await asyncio.sleep(random.uniform(3.0, 5.0))
                    await browser.close()
            except Exception as e:
                print(f"  ⚠️ Error en Bing Playwright: {e}")

        # FASE 4: Google Playwright (último recurso si los anteriores no alcanzaron 50)
        if len(eventos) < MAX_EVENTOS_POR_RUN and time.time() - t0 < SERP_TIMEOUT:
            try:
                from playwright.async_api import async_playwright

                async with async_playwright() as p:
                    browser = await p.chromium.launch(
                        headless=True,
                        args=["--no-sandbox", "--disable-dev-shm-usage",
                               "--disable-blink-features=AutomationControlled"],
                    )
                    context = await self.anti_block.create_stealth_context(browser, use_tor=True)
                    for i, consulta in enumerate(consultas[:6], 1):
                        if len(eventos) >= MAX_EVENTOS_POR_RUN:
                            break
                        if time.time() - t0 > SERP_TIMEOUT:
                            print("  ⏰ Google: tiempo agotado, saliendo.")
                            break

                        kw_subgenero = _extraer_subgenero_desde_keyword(keywords_sorted[i - 1])
                        bloques = await self._serp_google_playwright(context, consulta)

                        for b in bloques:
                            ev = self._extraer_del_serp_publico(b, subgenero=kw_subgenero)
                            if not ev:
                                continue
                            nombre_lower = (ev.get("nombre", "") or "").lower()
                            texto_lower = (b.get("texto", "") or "").lower()
                            candidato = nombre_lower + " " + texto_lower
                            if any(nk in candidato for nk in NOISE_KEYWORDS):
                                continue
                            uid = ev.get("url", "")
                            if uid not in used:
                                eventos.append(ev)
                                used.add(uid)
                                if len(eventos) >= MAX_EVENTOS_POR_RUN:
                                    break

                        if i < len(consultas[:6]):
                            await asyncio.sleep(random.uniform(3.0, 5.0))
                    await browser.close()
            except Exception as e:
                print(f"  ⚠️ Error en Google Playwright: {e}")

        dt = time.time() - t0
        self.optimizer.registrar_resultado("google_serp_public", eventos, dt, bool(eventos))
        return eventos

    async def _serp_startpage_playwright(self, context, consulta):
        """Busca vía Startpage con Playwright. Extrae enlaces a facebook.com/events.
        Startpage no tiene CAPTCHA y devuelve 90+ resultados por query.
        """
        bloques = []
        page = None
        try:
            page = await context.new_page()
            await self.anti_block.apply_playwright_stealth(page)
            url = "https://www.startpage.com/sp/search?query=" + urllib.parse.quote(consulta)
            await page.goto(url, timeout=30000, wait_until="domcontentloaded")
            await asyncio.sleep(random.uniform(3.0, 5.0))
            html = await page.content()

            if BeautifulSoup is None:
                return bloques

            soup = BeautifulSoup(html, "html.parser")
            vistos = set()

            # Startpage usa div.result para resultados
            for res in soup.select("div.result"):
                a = res.find("a", href=True)
                if not a:
                    continue
                href = urllib.parse.unquote(a.get("href", ""))
                m = re.search(r"facebook\.com/events/[^/]+/[^/?]+/\d+", href)
                if not m:
                    continue
                url_ev = "https://" + m.group(0)
                if url_ev in vistos:
                    continue
                vistos.add(url_ev)
                titulo = ""
                for h in ("h2", "h3", "h4"):
                    htag = res.find(h)
                    if htag:
                        titulo = htag.get_text(strip=True)
                        break
                texto = res.get_text(" ", strip=True)
                bloques.append({"titulo": titulo, "url": url_ev, "texto": texto})

            # Fallback: regex sobre todo el HTML
            if not bloques:
                for m in re.finditer(
                    r'https?://(?:www\.)?facebook\.com/events/[^/]+/[^/?]+/\d+',
                    html
                ):
                    url_ev = m.group(0)
                    if url_ev not in vistos:
                        vistos.add(url_ev)
                        bloques.append({"titulo": "", "url": url_ev, "texto": ""})

        except Exception:
            pass
        finally:
            if page:
                try:
                    await page.close()
                except Exception:
                    pass
        return bloques

    def _serp_duckduckgo_html(self, consulta, session=None):
        """Busca vía DuckDuckGo HTML POST. Extrae enlaces a facebook.com/events.
        Funciona sin CAPTCHA. Devuelve lista de dicts con titulo, url, texto.
        Si se provee session, la reutiliza (mantiene cookies entre consultas).
        """
        bloques = []
        vistos = set()
        try:
            import requests
            if session is None:
                session = requests.Session()
                session.headers.update({
                    "User-Agent": self.anti_block.random_user_agent(),
                })
            proxies = self.anti_block.requests_proxies() if hasattr(self, "anti_block") else None
            resp = session.post(
                "https://html.duckduckgo.com/html/",
                data={"q": consulta},
                timeout=15,
                proxies=proxies,
            )
            if resp.status_code != 200 or "vqd" in resp.url and "anomaly" in resp.text.lower():
                return bloques
            if BeautifulSoup is None:
                return bloques
            soup = BeautifulSoup(resp.text, "html.parser")
            vistos = set()
            for res in soup.select(".result"):
                a = res.select_one("a.result__a")
                if not a:
                    continue
                href = a.get("href", "")
                m = re.search(r"facebook\.com/events/[^/]+/[^/?]+/\d+", href)
                if not m:
                    continue
                url_ev = "https://" + m.group(0)
                if url_ev in vistos:
                    continue
                vistos.add(url_ev)
                titulo = a.get_text(" ", strip=True)
                snip = res.select_one(".result__snippet")
                texto = snip.get_text(" ", strip=True) if snip else ""
                bloques.append({"titulo": titulo, "url": url_ev, "texto": texto})
        except Exception:
            pass
        return bloques

    def _serp_mojeek_html(self, consulta, session=None):
        """Busca vía Mojeek HTML. Extrae enlaces a facebook.com/events.
        Mojeek es un motor de búsqueda independiente sin CAPTCHA.
        """
        bloques = []
        vistos = set()
        try:
            import requests
            if session is None:
                session = requests.Session()
                session.headers.update({
                    "User-Agent": self.anti_block.random_user_agent(),
                })
            resp = session.get(
                "https://www.mojeek.com/search",
                params={"q": consulta},
                timeout=15,
            )
            if resp.status_code != 200:
                return bloques
            if BeautifulSoup is None:
                return bloques
            soup = BeautifulSoup(resp.text, "html.parser")
            for res in soup.select(".results-standard li, .results-standard .result"):
                a = res.find("a", href=True)
                if not a:
                    continue
                href = a.get("href", "")
                m = re.search(r"facebook\.com/events/[^/]+/[^/?]+/\d+", href)
                if not m:
                    continue
                url_ev = "https://" + m.group(0)
                if url_ev in vistos:
                    continue
                vistos.add(url_ev)
                titulo = a.get_text(" ", strip=True)
                snippet = ""
                p_tag = res.find("p")
                if p_tag:
                    snippet = p_tag.get_text(" ", strip=True)
                bloques.append({"titulo": titulo, "url": url_ev, "texto": snippet})
        except Exception:
            pass
        return bloques

    def _serp_qwant_html(self, consulta, session=None):
        """Busca vía Qwant HTML. Extrae enlaces a facebook.com/events.
        Qwant es un motor de búsqueda privado sin CAPTCHA.
        """
        bloques = []
        vistos = set()
        try:
            import requests
            if session is None:
                session = requests.Session()
                session.headers.update({
                    "User-Agent": self.anti_block.random_user_agent(),
                })
            resp = session.get(
                "https://lite.qwant.com/",
                params={"q": consulta, "t": "web"},
                timeout=15,
            )
            if resp.status_code != 200:
                return bloques
            if BeautifulSoup is None:
                return bloques
            soup = BeautifulSoup(resp.text, "html.parser")
            for res in soup.select("li"):
                a = res.find("a", href=True)
                if not a:
                    continue
                href = a.get("href", "")
                m = re.search(r"facebook\.com/events/[^/]+/[^/?]+/\d+", href)
                if not m:
                    continue
                url_ev = "https://" + m.group(0)
                if url_ev in vistos:
                    continue
                vistos.add(url_ev)
                titulo = a.get_text(" ", strip=True)
                snippet = ""
                p_tag = res.find("p")
                if p_tag:
                    snippet = p_tag.get_text(" ", strip=True)
                bloques.append({"titulo": titulo, "url": url_ev, "texto": snippet})
        except Exception:
            pass
        return bloques

    def _serp_brave_html(self, consulta, session=None):
        """Busca vía Brave Search HTML. Extrae enlaces a facebook.com/events.
        Brave es un motor de búsqueda privado sin CAPTCHA.
        """
        bloques = []
        vistos = set()
        try:
            import requests
            if session is None:
                session = requests.Session()
                session.headers.update({
                    "User-Agent": self.anti_block.random_user_agent(),
                })
            resp = session.get(
                "https://search.brave.com/search",
                params={"q": consulta},
                timeout=15,
            )
            if resp.status_code != 200:
                return bloques
            if BeautifulSoup is None:
                return bloques
            soup = BeautifulSoup(resp.text, "html.parser")
            for res in soup.select(".snippet"):
                a = res.find("a", href=True)
                if not a:
                    continue
                href = a.get("href", "")
                m = re.search(r"facebook\.com/events/[^/]+/[^/?]+/\d+", href)
                if not m:
                    continue
                url_ev = "https://" + m.group(0)
                if url_ev in vistos:
                    continue
                vistos.add(url_ev)
                titulo = a.get_text(" ", strip=True)
                snippet = ""
                p_tag = res.find("p")
                if p_tag:
                    snippet = p_tag.get_text(" ", strip=True)
                bloques.append({"titulo": titulo, "url": url_ev, "texto": snippet})
        except Exception:
            pass
        return bloques

    async def _serp_bing_playwright(self, context, consulta):
        """Busca vía Bing con Playwright. Extrae enlaces a facebook.com/events.
        Respaldo por si DuckDuckGo falla.
        """
        bloques = []
        page = None
        try:
            page = await context.new_page()
            await self.anti_block.apply_playwright_stealth(page)
            url = "https://www.bing.com/search?q=" + urllib.parse.quote(consulta) + "&count=20"
            await page.goto(url, timeout=20000, wait_until="domcontentloaded")
            await asyncio.sleep(random.uniform(2.0, 3.5))

            html = await page.content()
            if BeautifulSoup is None:
                return bloques

            soup = BeautifulSoup(html, "html.parser")
            vistos = set()

            for res in soup.select("li.b_algo"):
                a = res.find("a", href=True)
                if not a:
                    continue
                href = urllib.parse.unquote(a.get("href", ""))
                m = re.search(r"facebook\.com/events/[^/]+/[^/?]+/\d+", href)
                if not m:
                    continue
                url_ev = "https://" + m.group(0)
                if url_ev in vistos:
                    continue
                vistos.add(url_ev)
                titulo = a.get_text(" ", strip=True)
                snippet = ""
                p_tag = res.find("p")
                if p_tag:
                    snippet = p_tag.get_text(" ", strip=True)
                bloques.append({"titulo": titulo, "url": url_ev, "texto": snippet})

        except Exception:
            pass
        finally:
            if page:
                try:
                    await page.close()
                except Exception:
                    pass
        return bloques

    async def _serp_google_playwright(self, context, consulta):
        """Busca vía Google con Playwright. Extrae enlaces a facebook.com/events.
        Último recurso (Google suele mostrar CAPTCHA). Usa página regional y
        detecta bloqueo para no perder tiempo.
        """
        bloques = []
        page = None
        try:
            page = await context.new_page()
            await self.anti_block.apply_playwright_stealth(page)
            url = ("https://www.google.com/search?q=" + urllib.parse.quote(consulta)
                   + "&num=20&hl=en&gl=us")
            await page.goto(url, timeout=25000, wait_until="domcontentloaded")
            await asyncio.sleep(random.uniform(3.0, 5.0))

            html = await page.content()
            if self.anti_block.detect_block(html, url):
                return bloques
            if BeautifulSoup is None:
                return bloques

            soup = BeautifulSoup(html, "html.parser")
            vistos = set()

            for res in soup.select("div.g, div.MjjYud"):
                a = res.find("a", href=True)
                if not a:
                    continue
                href = urllib.parse.unquote(a.get("href", ""))
                m = re.search(r"facebook\.com/events/[^/]+/[^/?]+/\d+", href)
                if not m:
                    continue
                url_ev = "https://" + m.group(0)
                if url_ev in vistos:
                    continue
                vistos.add(url_ev)
                titulo = a.get_text(" ", strip=True)
                h3 = res.find("h3")
                if h3:
                    titulo = h3.get_text(" ", strip=True)
                texto = res.get_text(" ", strip=True)
                bloques.append({"titulo": titulo, "url": url_ev, "texto": texto})

            # Fallback: regex sobre todo el HTML
            if not bloques:
                for m in re.finditer(
                    r'https?://(?:www\.)?facebook\.com/events/[^/]+/[^/?]+/\d+',
                    html
                ):
                    url_ev = m.group(0)
                    if url_ev not in vistos:
                        vistos.add(url_ev)
                        bloques.append({"titulo": "", "url": url_ev, "texto": ""})

        except Exception:
            pass
        finally:
            if page:
                try:
                    await page.close()
                except Exception:
                    pass
        return bloques

    def _cargar_eventos_visitados(self):
        """Carga el caché de URLs de eventos de FB ya visitadas."""
        try:
            if Path(EVENTOS_VISITADOS_FILE).exists():
                with open(EVENTOS_VISITADOS_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, dict) and data.get("version") == ENRIQUECIMIENTO_CACHE_VERSION:
                        return data.get("visitados", {})
        except (json.JSONDecodeError, IOError):
            pass
        return {}

    async def _crear_context_con_fallback(self, browser, url_probe=None):
        """Crea un contexto stealth con Tor; si Tor falla al navegar, cae a directo."""
        context = await self.anti_block.create_stealth_context(browser, use_tor=True)
        if not url_probe:
            return context
        probe_ok = True
        page = None
        try:
            page = await context.new_page()
            await self.anti_block.apply_playwright_stealth(page)
            await page.goto(url_probe, timeout=10000, wait_until="domcontentloaded")
        except Exception as e:
            probe_ok = False
            err_str = str(e)[:100]
            if "SOCKS" in err_str or "ERR_SOCKS" in err_str or "Proxy" in err_str:
                print(f"  ⚠️ Tor SOCKS no disponible; reintentando por Tor")
            else:
                print(f"  ⚠️ Tor no navega ({err_str}); reintentando por Tor")
        finally:
            if page:
                try:
                    await page.close()
                except Exception:
                    pass
        if not probe_ok:
            try:
                await context.close()
            except Exception:
                pass
            # Seguimos por Tor (create_stealth_context SIEMPRE usa el proxy
            # socks de Tor si está disponible): nunca conexión directa/IP real.
            context = await self.anti_block.create_stealth_context(browser, use_tor=True)
        return context

    def _guardar_eventos_visitados(self, cache):
        """Guarda el caché de URLs visitadas con la fecha de visita."""
        try:
            tmp = EVENTOS_VISITADOS_FILE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"version": ENRIQUECIMIENTO_CACHE_VERSION, "visitados": cache},
                          f, indent=2, ensure_ascii=False)
            os.replace(tmp, EVENTOS_VISITADOS_FILE)
        except (IOError, OSError):
            pass

    def _extractor_organizador_desde_texto(self, texto):
        """Extrae el organizador de una página pública de FB con patrones ES/EN.
        Trabaja línea a línea para no mezclar el organizador con etiquetas
        adyacentes de FB (Duración, Público, ubicación, etc.)."""
        patrones = [
            r"(?:organized by|hosted by|presented by|promoted by|powered by|"
            r"organizado por|presentado por|producido por|a cargo de|"
            r"evento de|event by|party de|noche de|festival de)[:\s]+"
            r"([A-Z][A-Za-zÀ-ÿ0-9&.' -]{2,60})",
        ]
        for linea in texto.split("\n"):
            linea = linea.strip()
            if len(linea) < 3 or len(linea) > 160:
                continue
            for pat in patrones:
                m = re.search(pat, linea, re.IGNORECASE)
                if not m:
                    continue
                cand = self._limpiar_candidato_organizador(m.group(1))
                if cand:
                    return cand
            # Fallback: línea completa "by X" / "por X" (sin texto extra)
            m = re.match(r"(?:by|por)\s+([A-Z][A-Za-zÀ-ÿ0-9&.' -]{2,50})$",
                         linea, re.IGNORECASE)
            if m:
                cand = self._limpiar_candidato_organizador(m.group(1))
                if cand:
                    return cand
        return None

    def _limpiar_candidato_organizador(self, cand):
        """Valida y limpia un candidato a organizador; devuelve str o None."""
        cand = cand.strip().rstrip(".,;:")
        # Cortar en fin de frase y limpiar etiquetas de FB
        cand = re.split(r"[.!?]\s+", cand)[0].strip()
        cand = re.sub(r"\s*(público|public|durac[ió]on|detalles?)\s*$",
                      "", cand, flags=re.IGNORECASE).strip()
        if len(cand) < 3:
            return None
        cand_l = cand.lower()
        if any(bad in cand_l for bad in ("facebook", "instagram", "http",
                                         "twitter", "evento", "noche")):
            return None
        # Descartar frases con conteos de asistentes ("9 guests", "120 personas")
        if re.search(r"\b\d[\d.,]*\s*(?:k|mil)?\s*(?:guests?|people|personas|"
                     r"asistentes?|attendees?|going|interested|joined|liked|"
                     r"friends)\b", cand_l):
            return None
        # El candidato no debe ser solo un verbo común o una ciudad
        if cand_l in ("privacy", "terms", "login", "sign up", "create event",
                      "public", "público"):
            return None
        return cand[:60]

    def _extractor_email_desde_texto(self, texto):
        """Extrae el primer email real (no de redes sociales) del texto."""
        emails = re.findall(r"[\w.+-]+@[\w.-]+\.\w+", texto[:8000])
        for em in emails:
            em = em.strip(".")
            em_l = em.lower()
            if any(skip in em_l for skip in ("facebook.com", "instagram.com", "twitter.com", "fb.com", "linkedin.com", "youtube.com", "google.com", "whatsapp")):
                continue
            if len(em) < 6:
                continue
            return em
        return None

    async def _extraer_organizador_desde_pagina_evento(self, page, html, cuerpo):
        """Extrae el organizador (nombre de perfil) visitando la página del evento.
        Estrategia encadenada: JSON embebido > DOM 'Organizador(a)' > texto.
        Devuelve (organizador, perfil_url) o (None, None)."""
        # 1) JSON embebido de FB: event_creator.name + profile_url/host_context_row
        # FB a veces encaja el JSON dentro de literales JS escapados (\\"name\\");
        # normalizamos para una sola expresión.
        texto = html or ""
        texto_json = texto.replace('\\"', '"').replace("\\'", "'")
        m = re.search(
            r'"event_creator"\s*:\s*\{[^}]*?"name"\s*:\s*"([^"\\]+)"',
            texto_json,
        )
        organizador = None
        perfil_url = None
        if m:
            organizador = self._limpiar_candidato_organizador(m.group(1).strip())
        if not organizador:
            # hosts_that_can_view_guestlist -> profile_url + name
            murl = re.search(r'"profile_url"\s*:\s*"([^"]+)"', texto_json)
            mname = re.search(
                r'"hosts_that_can_view_guestlist"\s*:\s*\[\{\s*"__typename"\s*:\s*"User"\s*,\s*"__isProfile"\s*:\s*"User"\s*,\s*"id"\s*:\s*"[^"]+"\s*,\s*"name"\s*:\s*"([^"\\]+)"',
                texto_json,
            )
            if mname:
                organizador = self._limpiar_candidato_organizador(mname.group(1))
                if murl:
                    perfil_url = re.sub(r"\\", "", murl.group(1))

        # 2) DOM: etiqueta 'Organizador(a)' / 'Hosted by' -> enlace al perfil
        if not organizador:
            try:
                for label in ("Organizador(a)", "Hosted by", "Organizador",
                              "Organizado por", "Presentado por", "A cargo de"):
                    el = await page.query_selector(f'text="{label}"')
                    if not el:
                        continue
                    # buscar el primer <a> perfil en este elemento o ancestros/sub árbol
                    anchor = await el.evaluate(
                        "(n) => { let s=n; while(s){ const a=s.querySelector && s.querySelector('a');"
                        "if(a) return a; s=s.parentElement;} return null; }")
                    if anchor:
                        href = await (anchor.get_attribute("href") or "")
                        txt = (await (anchor.inner_text() or "")).strip()
                        if txt and not any(b in txt.lower() for b in ("facebook","editar","ver más","see more","invitar","share")):
                            organizador = self._limpiar_candidato_organizador(txt)
                            perfil_url = href
                        break
            except Exception:
                pass

        # 3) Texto visible como último recurso
        if not organizador:
            organizador = self._extractor_organizador_desde_texto(cuerpo or "")
            if organizador:
                organizador = self._limpiar_candidato_organizador(organizador)

        return organizador, perfil_url

    async def _enriquecer_fechas_publicas(self, eventos, context):
        """Visita las páginas públicas de Facebook para extraer fecha, organizador y email.
        Visita TODOS los eventos con URL del SERP (máx. MAX_VISITAS_ENRIQUECIMIENTO por run),
        con caché en eventos_visitados.json para no repetir visitas y con reintentos.
        Si context es None (SERP vía requests), crea un browser Playwright propio.
        """
        visitados = self._cargar_eventos_visitados()

        pendientes = []
        for ev in eventos:
            url = ev.get("url", "")
            if not url or url in ("N/A", "", "Fecha no disponible"):
                continue
            if url in visitados:
                # Marcar datos ya extraídos en una visita anterior si el evento los pierde
                prev = visitados[url]
                if prev.get("organizador") and ev.get("organizador") in (None, "", "N/A"):
                    ev["organizador"] = prev["organizador"]
                if prev.get("email") and ev.get("email") in (None, "", "N/A"):
                    ev["email"] = prev["email"]
                if prev.get("fecha") and ev.get("fecha") in (None, "", "Fecha no disponible"):
                    ev["fecha"] = prev["fecha"]
                # Si la visita anterior no logró organizador, reintentar:
                # limpiar_calidad exige organizador para FB sin fecha.
                if not prev.get("organizador") or prev.get("organizador") in ("", "N/A"):
                    pendientes.append(ev)
                continue
            pendientes.append(ev)

        # Limitar visitas por ejecución (configurable desde loop_mejora.py)
        limite = CONFIG_RUNTIME["max_visitas_enriquecimiento"] or MAX_VISITAS_ENRIQUECIMIENTO
        if len(pendientes) > limite:
            print(f"  🗂️ Enriquecimiento FB: {len(pendientes)} eventos, visitando {limite} (caché {len(visitados)})")
            pendientes = pendientes[:limite]
        elif pendientes:
            print(f"  🗂️ Enriquecimiento FB: visitando {len(pendientes)} páginas (caché {len(visitados)})")

        def _post_filtro_anios():
            """Elimina eventos con fechas de años anteriores a 2025."""
            for ev in eventos[:]:
                fecha = ev.get("fecha", "")
                if fecha and fecha not in ("Fecha no disponible", "N/A", ""):
                    m = re.search(r'(20\d{2})', fecha)
                    if m and int(m.group(1)) < 2025:
                        eventos.remove(ev)

        if not pendientes:
            _post_filtro_anios()
            return

        sem = asyncio.Semaphore(3)
        own_browser = False
        own_context = context
        visitados_guardar = dict(visitados)

        # Si no hay context, crear uno propio con Playwright
        if own_context is None:
            try:
                from playwright.async_api import async_playwright
                pw = await async_playwright().start()
                browser = await pw.chromium.launch(
                    headless=True,
                    args=["--no-sandbox", "--disable-dev-shm-usage",
                           "--disable-blink-features=AutomationControlled"],
                )
                url_probe = pendientes[0].get("url") if pendientes else None
                own_context = await self._crear_context_con_fallback(browser, url_probe=url_probe)
                own_browser = True
            except Exception as e:
                print(f"  ⚠️ No se pudo crear browser para enriquecimiento: {e}")
                return

        async def visitar(ev):
            async with sem:
                cuerpo = ""
                html = ""
                for intento in range(2):  # 1 reintento
                    page = None
                    try:
                        page = await own_context.new_page()
                        await self.anti_block.apply_playwright_stealth(page)
                        await page.goto(ev["url"], timeout=VISITA_TIMEOUT_MS,
                                        wait_until="domcontentloaded")
                        await asyncio.sleep(random.uniform(1.5, 3.0))
                        # Hacer scroll para cargar contenido diferido (info/contacto)
                        try:
                            for _ in range(5):
                                await page.evaluate("window.scrollBy(0, 800)")
                                await asyncio.sleep(random.uniform(0.4, 0.8))
                            await page.evaluate("window.scrollTo(0, 0)")
                        except Exception:
                            pass
                        # Expandir descripciones truncadas ("Ver más" / "See more")
                        try:
                            boton = await page.query_selector(
                                'text="Ver más" , text="See more"')
                            if boton:
                                await boton.click()
                                await asyncio.sleep(random.uniform(0.8, 1.5))
                        except Exception:
                            pass
                        cuerpo = await page.evaluate("document.body ? document.body.innerText : ''")
                        if not cuerpo:
                            raise TimeoutError("cuerpo vacío")
                        html = await page.content()
                        break
                    except Exception as e:
                        print(f"  ⚠️ Visita FB falló ({intento + 1}/2): {ev['url']} → {e}")
                        if page:
                            try:
                                await page.close()
                            except Exception:
                                pass
                            page = None
                        if intento == 0:
                            await asyncio.sleep(random.uniform(2.0, 4.0))

                if not cuerpo:
                    return

                # Fecha
                fecha = self._normalizar_fecha_publica(cuerpo[:8000])
                if fecha:
                    ev["fecha"] = fecha
                # Organizador (JSON embebido / DOM label / texto). page debe estar vivo
                org, perfil_url = await self._extraer_organizador_desde_pagina_evento(page, html, cuerpo)
                if org:
                    ev["organizador"] = org
                if perfil_url and not ev.get("url_perfil") and "facebook.com" in perfil_url:
                    ev["url_perfil"] = perfil_url
                # Email (texto visible + HTML por si aparece en meta/scripts)
                email = self._extractor_email_desde_texto(cuerpo)
                if not email and html:
                    email = self._extractor_email_desde_texto(
                        re.sub(r"<[^>]+>", " ", html)[:12000])
                if email:
                    ev["email"] = email
                # Lugar/ciudad/pais
                if ev.get("lugar") in (None, "", "N/A"):
                    lugares = re.findall(
                        r"\b[A-Z][A-Za-zÀ-ÿ'\- ]{2,40}\s*,\s*[A-Z][a-zà-ÿ]{2,40}",
                        cuerpo[:2500]
                    )
                    if lugares:
                        ev["lugar"] = lugares[0][:80]
                m_ciudad = re.search(
                    r"\b(?:en|in|@)\s+([A-Z][A-Za-zÀ-ÿ'\- ]+?)(?:\s*,\s*)?([A-Z][a-zà-ÿ]+)?",
                    cuerpo[:2000]
                )
                if m_ciudad and ev.get("ciudad") in (None, "", "N/A"):
                    if re.search(r"[A-Za-zÀ-ÿ]{3,}", m_ciudad.group(1)):
                        ev["ciudad"] = m_ciudad.group(1).strip()[:60]
                    if m_ciudad.group(2):
                        ev["pais"] = m_ciudad.group(2)[:40]
                # Descripción (primeras líneas útiles)
                if not ev.get("descripcion") or ev["descripcion"] == "N/A":
                    lineas = [l.strip() for l in cuerpo.split("\n") if l.strip()]
                    util = [l for l in lineas if len(l) > 20 and "facebook.com" not in l][:3]
                    if util:
                        ev["descripcion"] = " ".join(util)[:200]

                # Cerrar la página (la extracción DOM ya terminó)
                if page:
                    try:
                        await page.close()
                    except Exception:
                        pass

                # Actualizar caché con lo que quedó (aunque sea parcial)
                visitados_guardar[ev["url"]] = {
                    "fecha": ev.get("fecha"),
                    "organizador": ev.get("organizador"),
                    "email": ev.get("email"),
                    "url_perfil": ev.get("url_perfil"),
                    "visitado": datetime.now(timezone.utc).isoformat(),
                }
                print(f"  ✅ Visitado: {ev.get('nombre','')[:45]} | org={ev.get('organizador')} | email={ev.get('email')}")

        tareas = [visitar(ev) for ev in pendientes]
        await asyncio.gather(*tareas, return_exceptions=True)

        # Guardar caché de URLs visitadas
        self._guardar_eventos_visitados(visitados_guardar)

        # Cerrar browser propio si se creó
        if own_browser:
            try:
                await own_context.browser.close()
            except Exception:
                pass

        # Post-filtro: eliminar eventos con fechas de años anteriores a 2025
        _post_filtro_anios()

    async def _enriquecer_fb_requests(self, ev):
        """Enriquece un evento visitando su página FB vía requests (sin Playwright).
        Las páginas de eventos de FB son públicas; extrae fecha, organizador, lugar.
        """
        try:
            import requests as _requests
            sess = _requests.Session()
            sess.headers.update({
                'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
                              'AppleWebKit/537.36 (KHTML, like Gecko) '
                              'Chrome/120.0.0.0 Safari/537.36',
                'Accept': 'text/html,application/xhtml+xml',
                'Accept-Language': 'en-US,en;q=0.9',
            })
            proxies = self.anti_block.requests_proxies() if hasattr(self, "anti_block") else None
            r = sess.get(ev["url"], timeout=20, proxies=proxies, allow_redirects=True)
            r.raise_for_status()
            body = r.text[:30000]

            # Extraer fecha
            fecha = self._normalizar_fecha_publica(body)
            if fecha:
                ev["fecha"] = fecha

            # Extraer título del evento desde meta tags
            if not ev.get("nombre") or ev["nombre"] in ("N/A", ""):
                mt = re.search(r'<meta\s+property="og:title"\s+content="([^"]+)"', body)
                if mt:
                    ev["nombre"] = mt.group(1)[:120]

            # Extraer descripción
            if not ev.get("descripcion") or ev["descripcion"] == "N/A":
                md = re.search(r'<meta\s+property="og:description"\s+content="([^"]+)"', body)
                if md:
                    ev["descripcion"] = md.group(1)[:300]

            # Extraer organizador
            if ev.get("organizador") in (None, "", "N/A"):
                morg = re.search(
                    r"(?:Evento de|event by|hosted by|by)\s+([A-Z][A-Za-zÀ-ÿ0-9&.' -]{2,60})",
                    body[:10000].replace("\n", " "), re.IGNORECASE
                )
                if morg and "facebook" not in (morg.group(1).lower()):
                    ev["organizador"] = morg.group(1).strip().rstrip(".,")[:60]

            # Extraer lugar/ciudad
            if ev.get("lugar") in (None, "", "N/A"):
                ml = re.search(
                    r'"name"\s*:\s*"([^"]+)"', body[:8000]
                )
                if ml:
                    ev["lugar"] = ml.group(1)[:80]

            if ev.get("ciudad") in (None, "", "N/A"):
                mc = re.search(
                    r'"location"\s*:\s*\{[^}]*"city"\s*:\s*"([^"]+)"',
                    body[:8000]
                )
                if mc:
                    ev["ciudad"] = mc.group(1)[:60]

        except Exception:
            pass

    def _extraer_del_serp_publico(self, bloque, subgenero="general"):
        """Extrae evento desde bloque SERP (Google/Startpage) sin visitar FB.

        subgenero: subgénero heredado de la keyword que generó este resultado.
        """
        titulo = (bloque.get("titulo") or "").strip()
        texto = bloque.get("texto") or ""
        url = bloque.get("url") or ""

        if not titulo or not re.search(r"facebook\.com/events", url):
            return None

        # Filtro de año: descartar eventos de años anteriores a 2025
        anio_match = re.search(r'\b(20\d{2})\b', titulo)
        if anio_match:
            anio = int(anio_match.group(1))
            if anio < 2025:
                return None

        partes_titulo = [p.strip() for p in titulo.split(" | ") if p.strip()]
        nombre = partes_titulo[0].split(" · Facebook")[0].strip()
        nombre = re.sub(r"\s*[-–]?\s*Facebook$", "", nombre, flags=re.IGNORECASE)
        nombre = re.sub(r"\s+", " ", nombre)[:100]

        fecha = self._normalizar_fecha_publica(texto) or self._normalizar_fecha_publica(titulo) or "Fecha no disponible"

        lugar = "N/A"
        ciudad = "N/A"
        pais = "N/A"
        organizador = "N/A"
        descripcion = "N/A"

        if len(partes_titulo) >= 2:
            ultima = partes_titulo[-1]
            mpartes = [x.strip() for x in ultima.split(",")]
            candidato_lugar = mpartes[0].strip()
            if re.search(r"[A-Za-zÀ-ÿ]{3,}", candidato_lugar):
                lugar = candidato_lugar[:80]
            if len(mpartes) >= 2:
                candidato_ciudad = mpartes[1].strip()
                if re.search(r"[A-Za-zÀ-ÿ]{3,}", candidato_ciudad):
                    ciudad = candidato_ciudad[:60]

        m = re.search(
            r"(?:event|party|festival)\s+in\s+([^,]+?)\s*,\s*([A-Z][a-zà-ÿ]+)(?:\s*and\s*\d+\s*others)?",
            texto, re.IGNORECASE
        )
        if m:
            ciudad_c = m.group(1).strip()
            if re.search(r"[A-Za-zÀ-ÿ]{3,}", ciudad_c):
                ciudad = ciudad_c[:60]
            pais = m.group(2).strip()[:40]

        # Patrones para extraer organizador del texto SERP
        org_patterns = [
            r"\bby\s+([A-Z][A-Za-zÀ-ÿ0-9&\s.'-]*?)(?:\s+and\s+\d+\s+others|\s+[A-Za-zÀ-ÿ]+?\s+on\s+facebook|\.\s|$)",
            r"(?:hosted by|organized by|organized by|presented by|promoted by)[:\s]+([A-Z][A-Za-zÀ-ÿ0-9&\s.'-]+?)(?:\n|$|\.|,)",
            r"(?:evento de|event by|noche de|party de)[:\s]+([A-Z][A-Za-zÀ-ÿ0-9&\s.'-]+?)(?:\n|$|\.|,)",
            r"(?:por|by)\s+([A-Z][a-zA-ZÀ-ÿ0-9&\s.'-]{2,40}?)(?:\s+on\s+facebook|\s+·|\s*\||$)",
        ]
        for pat in org_patterns:
            morg = re.search(pat, texto, re.IGNORECASE)
            if morg:
                cand = morg.group(1).strip().rstrip(".,")
                if len(cand) >= 3 and "facebook" not in cand.lower():
                    organizador = cand[:60]
                    break

        # Extraer email del texto si existe
        email = "N/A"
        email_match = re.search(r'[\w\.-]+@[\w\.-]+\.\w+', texto)
        if email_match:
            email = email_match.group(0)

        tipo_lugar = self.extractor._extract_venue_type(f"{nombre} {lugar} {descripcion}") or "N/A"

        return {
            "nombre": nombre,
            "fecha": fecha,
            "lugar": lugar,
            "ciudad": ciudad,
            "pais": pais,
            "tipo_lugar": tipo_lugar,
            "fuente": "Facebook (público SERP)",
            "organizador": organizador,
            "email": email if email != "N/A" else url,
            "url": url,
            "link": url,
            "descripcion": descripcion,
            "subgenero": subgenero,
        }

    def _normalizar_fecha_publica(self, fecha_str):
        """Convierte múltiples formatos de fecha a ISO (YYYY-MM-DD), o None.
        
        Formatos soportados:
        - 2026-04-10 / 10.04.2026 / 10-04-2026 / 10/4/26
        - 10. April 2026 / 10 April 2026 / 10th April 2026
        - 1 de octubre de 2016
        - 15 Aug 2026 / Aug 15, 2026 / August 15, 2026
        - 15/08/2026 / 2026/08/15
        """
        if not fecha_str:
            return None
        flat = re.sub(r"\s+", " ", fecha_str)

        # 1) Numérico: 2026-04-10 / 10.04.2026 / 10-04-2026 / 10/4/26
        m_num = re.search(r"\b(\d{1,4})[./\-](\d{1,2})[./\-](\d{2,4})\b", flat)
        if m_num:
            a, b, c = m_num.group(1), m_num.group(2), m_num.group(3)
            vals = [int(a), int(b), int(c)]
            anio = max(vals)
            if anio > 1000:
                if int(a) > 1000:
                    # YYYY-MM-DD o YYYY-DD-MM
                    d, mes = int(c), int(b)
                    if int(b) > 12:
                        d, mes = int(b), int(c)
                else:
                    # DD-MM-YYYY (día es el más pequeño razonable)
                    resto = [v for v in vals if v != anio]
                    d, mes = resto[0], resto[1]
                    if int(b) > 12:
                        d, mes = int(b), int(c)
                if 1 <= mes <= 12 and 1 <= d <= 31:
                    return f"{anio:04d}-{mes:02d}-{d:02d}"
            else:
                try:
                    d, mes = int(a), int(b)
                    anio_i = int(c)
                    if len(c) == 2:
                        anio_i = 2000 + anio_i if anio_i < 100 else anio_i
                    if 1 <= mes <= 12 and 1 <= d <= 31:
                        return f"{anio_i:04d}-{mes:02d}-{d:02d}"
                except ValueError:
                    pass

        # 2) Nombre de mes, en ambos órdenes:
        #    - día primero: '10. April 2026', '10 April 2026', '10th April 2026',
        #      '1 de octubre de 2016', '15 Aug 2026', '15 August 2026'
        #    - mes primero: 'Aug 15, 2026', 'August 15, 2026', 'Aug 15', 'August 15'
        #    - precedido de día de la semana: 'Friday, August 15 at 10:00 PM'
        meses_es = {
            "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5,
            "junio": 6, "julio": 7, "agosto": 8, "septiembre": 9,
            "octubre": 10, "noviembre": 11, "diciembre": 12,
            "ene": 1, "feb": 2, "mar": 3, "abr": 4, "may": 5, "jun": 6,
            "jul": 7, "ago": 8, "sep": 9, "sept": 9, "oct": 10, "nov": 11,
            "dic": 12,
        }
        meses_en = {
            "january": 1, "february": 2, "march": 3, "april": 4, "may": 5,
            "june": 6, "july": 7, "august": 8, "september": 9, "october": 10,
            "november": 11, "december": 12,
            "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
            "jul": 7, "aug": 8, "sep": 9, "sept": 9, "oct": 10, "nov": 11,
            "dec": 12,
        }
        mes_nombres = ("enero|febrero|marzo|abril|mayo|junio|julio|agosto|"
                       "septiembre|octubre|noviembre|diciembre|january|february|"
                       "march|april|may|june|july|august|september|october|"
                       "november|december|jan|feb|mar|apr|may|jun|jul|aug|sep|"
                       "sept|oct|nov|dec")
        def _a_iso(dia, mes_nombre, anio):
            mese = meses_es.get(mes_nombre.lower()) or meses_en.get(mes_nombre.lower())
            if not mese:
                return None
            anio_int = int(anio) if anio else datetime.now().year
            try:
                return f"{anio_int:04d}-{mese:02d}-{int(dia):02d}"
            except ValueError:
                return None

        # 2a) Día primero: '15 Aug 2026', '15 August 2026', '10th April 2026'
        m = re.search(
            r"\b(\d{1,2})(?:st|nd|rd|th)?[./\-\s]+(?:de\s+)?(" + mes_nombres +
            r")\w*(?:\s*,\s*)?(?:\s+de\s+|\s+del\s+|\s+of\s+|\s+)?\s*(\d{4})?\b",
            flat, re.IGNORECASE
        )
        if m:
            iso = _a_iso(m.group(1), m.group(2), m.group(3))
            if iso:
                return iso

        # 2b) Mes primero: 'Aug 15, 2026', 'August 15, 2026', 'Aug 15', 'August 15'
        #     (también con día de la semana previo: 'Friday, August 15 at 10:00 PM')
        m = re.search(
            r"\b(" + mes_nombres + r")\w*(?:\s*,?\s*|\s+de\s+|\s+del\s+|\s+of\s+)"
            r"(\d{1,2})(?:st|nd|rd|th)?(?:\s*,?\s*(?:de\s+|del\s+|of\s+)?\s*(\d{4}))?\b",
            flat, re.IGNORECASE
        )
        if m:
            iso = _a_iso(m.group(2), m.group(1), m.group(3))
            if iso:
                return iso

        return None

    # ------------------------------------------------------------------ #
    # ORQUESTADOR
    # ------------------------------------------------------------------ #
    async def scrape(self, keywords):
        todos = []
        used = set()
        t_start = time.time()

        async def run_with_timeout(coro, timeout=TIMEOUT_PER_STRATEGY):
            try:
                return await asyncio.wait_for(coro, timeout=timeout)
            except (asyncio.TimeoutError, Exception):
                return []

        def time_left():
            return max(0, 180 - (time.time() - t_start))

        # ESTRATEGIA 2b PRIMERO: curl_cffi + Tor (reactivación FB sin Playwright,
        # sin IP real). Extrae del SERP vía Tor y parsea los snippets. Rápido
        # y no se cuelga: si Tor/DDG falla, get_html devuelve None y seguimos.
        resultados = await run_with_timeout(self._via_curl_cffi(keywords), timeout=min(90, time_left()))
        for ev in resultados:
            uid = ev.get("url", "")
            if uid not in used:
                todos.append(ev)
                used.add(uid)

        if time_left() < 10:
            return todos

        # Si curl_cffi+Tor ya aportó suficientes eventos, no gastamos tiempo
        # en la SERP/Playwright (más lenta y solo respaldo).
        if len(todos) >= 10:
            return todos

        # ESTRATEGIA 5: Búsqueda pública SERP (respaldo, vía proxy Tor)
        resultados = await run_with_timeout(self._via_google_serp_public(keywords), timeout=min(SERP_TIMEOUT, time_left()))
        for ev in resultados:
            uid = ev.get("url", "")
            if uid not in used:
                todos.append(ev)
                used.add(uid)

        if time_left() < 10:
            return todos

        # Grupos conocidos (sin cookies retorna 0)
        resultados = await run_with_timeout(self._via_grupos_conocidos(keywords), timeout=min(90, time_left()))
        for ev in resultados:
            uid = ev.get("url", "")
            if uid not in used:
                todos.append(ev)
                used.add(uid)

        if time_left() < 10:
            return todos

        # Facebook directo (solo con cookies)
        if self.cookies:
            resultados = await run_with_timeout(self._via_facebook_directo(keywords), timeout=min(TIMEOUT_PER_STRATEGY, time_left()))
            for ev in resultados:
                uid = ev.get("url", "")
                if uid not in used:
                    todos.append(ev)
                    used.add(uid)

        if time_left() < 10:
            return todos

        # DuckDuckGo Playwright (respaldo)
        if len(todos) < 10:
            resultados = await run_with_timeout(self._via_duckduckgo_playwright(keywords), timeout=min(TIMEOUT_PER_STRATEGY, time_left()))
            for ev in resultados:
                uid = ev.get("url", "")
                if uid not in used:
                    todos.append(ev)
                    used.add(uid)

        return todos

    def exportar_grupos_encontrados(self, keywords, paises_seleccionados):
        grupos = []
        seen_ids = set()

        def _grupo_con_tipo(gid, nombre, url):
            return {
                "id": gid, "nombre": nombre, "url": url,
                "tipo_grupo": _clasificar_grupo(nombre, url),
            }

        for gid, gname in CONOCIDOS.items():
            gname_lower = gname.lower()
            if any(kw.lower() in gname_lower for kw in keywords):
                if gid not in seen_ids:
                    grupos.append(_grupo_con_tipo(
                        gid, gname, f"https://facebook.com/groups/{gid}",
                    ))
                    seen_ids.add(gid)

        descubiertos = _cargar_grupos_descubiertos()
        for gid, gdata in descubiertos.items():
            if gid not in seen_ids:
                grupos.append(_grupo_con_tipo(
                    gid,
                    gdata.get("nombre", gid),
                    gdata.get("url", f"https://facebook.com/groups/{gid}"),
                ))
                seen_ids.add(gid)

        for gid, gdata in self.grupos_encontrados.items():
            if gid not in seen_ids:
                grupos.append(_grupo_con_tipo(
                    gid,
                    gdata.get("nombre", gid),
                    gdata.get("url", f"https://facebook.com/groups/{gid}"),
                ))
                seen_ids.add(gid)

        grupos_config = _generar_grupos_desde_config(paises_seleccionados)
        for g in grupos_config:
            if g["id"] not in seen_ids:
                grupos.append(g)
                seen_ids.add(g["id"])

        data = {
            "groups": grupos,
            "timestamp": datetime.now().isoformat(),
            "total": len(grupos),
            "paises_rotacion": [p.get("nombre") for p in paises_seleccionados],
        }
        try:
            with open(GRUPOS_EXPORT_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            print(f"   💾 {len(grupos)} grupos exportados a grupos_encontrados.json")
        except IOError as e:
            print(f"   ⚠️ Error guardando grupos: {e}")


def _extraer_subgenero_desde_keyword(keyword):
    """Extrae el subgénero del primer token de una keyword.

    Las keywords generadas por _generar_keywords tienen el formato
    "{subgenero} {pais}" o "{subgenero} {ciudad}" o "{subgenero} {pais} {extra}".
    El primer token es el subgénero. Se valida contra la lista conocida;
    si no coincide, devuelve "general".
    """
    if not keyword:
        return "general"
    tokens = keyword.strip().split()
    if not tokens:
        return "general"
    candidato = tokens[0].lower()
    # Validar contra subgéneros conocidos
    subgeneros_conocidos = {
        "darkpsy", "forest", "psychill", "psybient", "fullon",
        "progressive", "goa", "hitech", "twilight", "psycore",
        "suomisaundi", "zenon", "psychedelic", "psytrance",
    }
    if candidato in subgeneros_conocidos:
        return candidato
    return "general"


    # ---------------------------------------------------------------------- #
    # FUNCIÓN PRINCIPAL
    # ---------------------------------------------------------------------- #
async def scrape_facebook_events(max_keywords=None, max_visitas=None):
    """Punto de entrada principal de Facebook.

    Parámetros opcionales (usados por loop_mejora.py):
      - max_keywords: nº máximo de keywords (None = MAX_KEYWORDS_POR_RUN).
      - max_visitas: nº de páginas de enriquecimiento (None = MAX_VISITAS_...).
    Sin argumentos se comporta exactamente igual que antes (nunca restar).
    """
    set_limites_loop(max_keywords=max_keywords, max_visitas=max_visitas)
    try:
        return await _scrape_facebook_events_inner()
    finally:
        # Reversible: restablece los valores por defecto tras la ejecución
        set_limites_loop(max_keywords=None, max_visitas=None)


async def _scrape_facebook_events_inner():
    try:
        with open(CONFIG_FILE) as f:
            config = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        config = {"subgeneros": ["psytrance"], "paises": []}

    paises_seleccionados = _seleccionar_paises_rotacion(config)
    if not paises_seleccionados:
        print("   ⚠️ No hay países para procesar.")
        return []

    nombres = [p.get("nombre", "?") for p in paises_seleccionados]
    print(f"   🌍 Países: {', '.join(nombres)}")

    subgeneros = config.get("subgeneros", ["psytrance"])
    print(f"   🎛️ Subgéneros ({len(subgeneros)}): {', '.join(subgeneros)}")

    keywords = _generar_keywords(config, paises_seleccionados)
    max_kw_activo = CONFIG_RUNTIME["max_keywords_por_run"] or config.get(
        "max_keywords_por_run", MAX_KEYWORDS_POR_RUN
    )
    print(f"   Facebook: {len(keywords)} keywords generadas (máx {max_kw_activo})")

    finder = FacebookEventsFinder()

    if finder.cookies:
        print(f"   Cookies encontradas. Usando autenticación.")
    else:
        print(f"   Sin cookies. python3 scrapers/facebook_mcp.py --setup-cookies")

    eventos = await finder.scrape(keywords)
    print(f"   Facebook: {len(eventos)} eventos")

    finder.exportar_grupos_encontrados(keywords, paises_seleccionados)

    if _optimizer.historial:
        print(f"   🧠 Loop: mejor estrategia: {_optimizer.mejor_estrategia()}")

    return eventos


def setup_cookies():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("❌ playwright no instalado. pip install playwright && playwright install chromium")
        return

    print("=" * 60)
    print("  SETUP DE COOKIES DE FACEBOOK")
    print("=" * 60)
    print()
    input("Presiona Enter para abrir el navegador...")

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=False, args=["--no-sandbox"])
            context = browser.new_context(
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                viewport={"width": 1280, "height": 800},
            )
            page = context.new_page()
            page.goto("https://www.facebook.com/")
            print("\n⏳ Inicia sesión en Facebook y presiona Enter aquí.\n")
            input("Presiona Enter cuando hayas iniciado sesión...")

            cookies = context.cookies()
            if not cookies:
                print("⚠️ No se encontraron cookies.")
                browser.close()
                return

            with open(COOKIES_FILE, "w") as f:
                json.dump(cookies, f, indent=2)
            print(f"\n✅ {len(cookies)} cookies guardadas en {COOKIES_FILE}")
            browser.close()
    except KeyboardInterrupt:
        print("\n\n❌ Cancelado.")
    except Exception as e:
        print(f"\n❌ Error: {e}")


if __name__ == "__main__":
    if "--setup-cookies" in sys.argv:
        setup_cookies()
    elif "--report" in sys.argv:
        print(get_optimizer().generar_reporte())
    elif "--fetch-proxies" in sys.argv:
        ab = get_anti_block()
        ab.fetch_free_proxies()
        print(f"✅ {len(ab.proxies)} proxies cargados")
    elif "--reset-rotation" in sys.argv:
        _reset_rotation()
    elif "--reset-search" in sys.argv:
        _reset_search_state()
    else:
        async def test():
            eventos = await scrape_facebook_events()
            print(f"\nTotal: {len(eventos)} eventos")
            for ev in eventos[:15]:
                print(f"  - {ev.get('nombre', '?')[:60]}")
                print(f"    {ev.get('fecha', '?')} | {ev.get('lugar', '?')} | {ev.get('fuente', '?')}")
            print()
            print(get_optimizer().generar_reporte())
        asyncio.run(test())
