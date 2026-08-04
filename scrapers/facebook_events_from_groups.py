#!/usr/bin/env python3
"""
Pipeline de eventos reales de grupos de Facebook SIN cookies ni sesión.

Estrategia (todo local, gratuito y a prueba de cuelgues):
1. Para cada grupo de grupos_encontrados.json se construye la URL de su
   pestaña de eventos en la versión ligera: https://mbasic.facebook.com/groups/{id}/events
2. Petición HTTP GET con requests (cabecera móvil), timeout estricto de 8s.
   Si devuelve HTML con eventos (enlaces a /events/...), se parsea con
   BeautifulSoup y se extraen nombre, fecha, lugar, organizador, descripción.
3. Concurrencia con ThreadPoolExecutor (10 workers): el pipeline es inmune a
   cuelgues; cada worker tiene timeout estricto.
4. Si mbasic falla o exige login → fallback automático por buscador
   (site:facebook.com/events + nombre del grupo + ciudad/subgénero), máximo 3
   resultados por grupo, reutilizando la lógica ya existente en
   facebook_public_events.py (Google → Startpage, sin login).
5. Formato de salida idéntico al resto de scrapers + campo "subgenero"
   inferido del nombre del grupo y del texto.
6. Bucle ligero de mejora: se guarda grupos_productivos.json con los IDs que
   sí dieron eventos; en ejecuciones siguientes se procesan primero esos
   grupos y se reintenta el resto con presupuesto de tiempo.
7. Salida final en eventos_facebook_grupos.json (lista lista para integrar).

Principio: sumar, nunca restar. No modifica ningún otro módulo.
"""

import asyncio
import json
import random
import re
import socket
import sys
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Dict, Optional

import requests
from bs4 import BeautifulSoup

socket.setdefaulttimeout(8)

_PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

_LOGIN_WALL_DETECTADO = False

COOKIES_FILE = str(Path(_PROJECT_ROOT) / "facebook_cookies.json")
GRUPOS_FILE = str(Path(_PROJECT_ROOT) / "grupos_encontrados.json")
OUTPUT_FILE = str(Path(_PROJECT_ROOT) / "eventos_facebook_grupos.json")
GRUPOS_PRODUCTIVOS_FILE = str(Path(_PROJECT_ROOT) / "grupos_productivos.json")
GRUPOS_RENDIMIENTO_FILE = str(Path(_PROJECT_ROOT) / "grupos_rendimiento.json")

from scrapers.event_extractor import EventExtractor
from scrapers.anti_block import get_anti_block
from scrapers.facebook_public_events import (
    _buscar,
    _buscar_brave,
    _buscar_yahoo,
    _extraer_del_serp,
    _es_relevante,
)
from loop_optimizer import get_optimizer

TIMEOUT_PER_GROUP = 8
HTTP_TIMEOUT = (4, TIMEOUT_PER_GROUP)
MAX_WORKERS = 10
TARGET_EVENTS = 22
TIME_BUDGET_SEC = 165
HTTP_BUDGET_SEC = 30
MAX_RESULTS_POR_GRUPO = 3
LOGIN_MARKERS = ["login.php", "iniciar sesión", "log in", "crear cuenta", "sign up"]

try:
    from scrapers.facebook_mcp import SUBGENERO_ALIASES, SUBGENERO_ETIQUETAS
except Exception:
    SUBGENERO_ALIASES = {}
    SUBGENERO_ETIQUETAS = {}


# --------------------------------------------------------------------------
# Utilidades
# --------------------------------------------------------------------------

def _load_json(path: str) -> Optional[dict]:
    if not Path(path).exists():
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return None


def _save_json(path: str, data) -> None:
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except IOError:
        pass


def _session_movil() -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36"
        ),
        "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    })
    return s


def _html_a_texto(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    return re.sub(r"\s+", " ", soup.get_text(" ", strip=True)).strip()


def _es_pagina_login(url: str, texto: str) -> bool:
    if "login.php" in url:
        return True
    bajo = texto.lower()
    return any(m in bajo for m in LOGIN_MARKERS) and len(bajo) < 2000


def _subgenero_del_grupo(nombre: str, tipo_grupo: Optional[list] = None) -> str:
    if tipo_grupo:
        preferidos = ["dark", "forest", "psychill", "psybient", "fullon",
                      "progressive", "goa", "hitech", "twilight", "psycore",
                      "suomi", "zenon", "psychedelic"]
        for p in preferidos:
            if p in tipo_grupo:
                return p
    texto = f"{nombre}".lower()
    for sub, aliases in SUBGENERO_ALIASES.items():
        if any(a.lower() in texto for a in aliases):
            return SUBGENERO_ETIQUETAS.get(sub, sub)
    return ""


# Subgéneros cuyo término es ambiguo en el SERP (también significa
# Finlandia, mercado, deporte, tecnología, jazz, etc.): exigen que además
# del propio término aparezca un término psy de música/evento inequívoco.
_SUBGENEROS_AMBIGUOS = {"suomi", "twilight", "river", "beach", "urban",
                        "festival", "dark", "hitech", "psychedelic", "goa",
                        "forest", "progressive", "fullon"}

_TERMINOS_PSY_FUERTES = re.compile(
    r"\b(rave|party|dj|trance|techno|psytrance|psych|goa|club|open.?air|"
    r"music|dance|bass|acid|bpm|sound|set|night)\b",
    re.IGNORECASE,
)

_IRRELEVANTES_EXTRA = [
    "bible", "jazz", "bikefest", "dinner", "seminar", "conference",
    "exhibition", "exposición", "congreso", "lecture", "prayer", "church",
    "metal", "instrumental", "indiana jones", "yungblud", "tour", "ball",
    "marathon", "fun run", "charity walk",
]


def _es_relevante_grupos(bloque, sub: str) -> bool:
    """`_es_relevante` + filtro estricto para subgéneros ambiguos."""
    if not _es_relevante(bloque):
        return False
    texto = " ".join([
        bloque.get("titulo", ""),
        bloque.get("texto", ""),
        urllib.parse.unquote(bloque.get("url", "")),
    ])
    if any(t in texto.lower() for t in _IRRELEVANTES_EXTRA):
        return False
    if sub not in _SUBGENEROS_AMBIGUOS:
        return True
    # Quitar el término ambiguo: no basta con que aparezca el subgénero
    # (p.ej. "hitech" solo), debe haber señal musical independiente.
    resto = re.sub(rf"\b{re.escape(sub)}\b", " ", texto, flags=re.I)
    if not _TERMINOS_PSY_FUERTES.search(resto):
        return False
    return True


# --------------------------------------------------------------------------
# Fase HTTP: mbasic.facebook.com/groups/{id}/events
# --------------------------------------------------------------------------

def _scrape_group_events_tab(group_id: str, group_name: str,
                             group_url: str) -> List[Dict]:
    """Descarga la pestaña de eventos del grupo en mbasic y extrae eventos."""
    eventos = []
    urls_eventos = []

    def _extraer_del_html(html: str, base_url: str):
        soup = BeautifulSoup(html, "html.parser")
        for a in soup.find_all("a", href=True):
            href = a.get("href", "")
            if re.search(r"/events/\d+", href):
                if href.startswith("/"):
                    href = "https://www.facebook.com" + href
                texto = a.get_text(" ", strip=True)
                if texto and len(texto) >= 4 and href not in urls_eventos:
                    urls_eventos.append(href)
                    eventos.append({"url": href, "titulo": texto[:120], "html": str(a)})

    global _LOGIN_WALL_DETECTADO
    try:
        s = _session_movil()
        mbasic_url = group_url.replace(
            "https://facebook.com", "https://mbasic.facebook.com"
        ).replace("http://facebook.com", "https://mbasic.facebook.com")
        if "/mbasic.facebook.com" not in mbasic_url and "facebook.com" in mbasic_url:
            mbasic_url = mbasic_url.replace("//facebook.com", "//mbasic.facebook.com", 1)
        mbasic_url = mbasic_url.rstrip("/") + "/events"

        r = s.get(mbasic_url, timeout=HTTP_TIMEOUT, allow_redirects=True)
        if r.status_code != 200:
            return []
        html = r.text
        if _es_pagina_login(r.url, _html_a_texto(html)[:1500]):
            _LOGIN_WALL_DETECTADO = True
            return []
        if len(html) < 500:
            return []

        _extraer_del_html(html, mbasic_url)

        if not eventos:
            text = _html_a_texto(html)
            extractor = EventExtractor()
            bloques = [p.get_text(" ", strip=True)
                       for p in BeautifulSoup(html, "html.parser").find_all(["p", "div"])
                       if len(p.get_text(" ", strip=True)) >= 30]
            for bloque in bloques[:12]:
                for ev in extractor.extract_all(bloque, group_name, group_url):
                    eventos.append(ev)
    except Exception:
        return []

    return eventos


def _soup_par(html: str):
    return BeautifulSoup(html, "html.parser")


def _scrape_group_http_full(group_id: str, group_name: str,
                            group_url: str) -> List[Dict]:
    """Intenta mbasic (pestaña de eventos) y el texto del grupo."""
    evs = _scrape_group_events_tab(group_id, group_name, group_url)
    if evs:
        return evs
    try:
        s = _session_movil()
        mbasic_url = group_url.replace(
            "https://facebook.com", "https://mbasic.facebook.com"
        ).replace("http://facebook.com", "https://mbasic.facebook.com")
        if "/mbasic.facebook.com" not in mbasic_url and "facebook.com" in mbasic_url:
            mbasic_url = mbasic_url.replace("//facebook.com", "//mbasic.facebook.com", 1)
        r = s.get(mbasic_url, timeout=HTTP_TIMEOUT, allow_redirects=True)
        if r.status_code != 200:
            return []
        html = r.text
        if _es_pagina_login(r.url, _html_a_texto(html)[:1500]):
            return []
        text = _html_a_texto(html)
        if len(text) < 300:
            return []
        extractor = EventExtractor()
        soup = _soup_par(html)
        bloques = [b.get_text(" ", strip=True)
                   for b in soup.find_all(["p", "div"],
                                          class_=lambda c: c and "story" in str(c))]
        if not bloques:
            bloques = [p.get_text(" ", strip=True) for p in soup.find_all("p")]
        bloques = [b for b in bloques if len(b) >= 30]
        if not bloques:
            bloques = [text[:8000]]
        evs = []
        for bloque in bloques[:12]:
            for ev in extractor.extract_all(bloque, group_name, group_url):
                evs.append(ev)
        if not evs:
            evs = extractor.extract_all(text[:8000], group_name, group_url)
        return evs
    except Exception:
        return []


# --------------------------------------------------------------------------
# Fase fallback: buscador (DDG html/lite → Google → Startpage), sin login
# --------------------------------------------------------------------------

def _buscar_ddg_requests(consulta: str) -> List[Dict]:
    """DDG html/lite vía requests (rápido, sin Playwright)."""
    bloques = []
    s = requests.Session()
    s.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "en-US,en;q=0.9",
        "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
    })
    for base in ("https://html.duckduckgo.com/html/",
                 "https://lite.duckduckgo.com/lite/"):
        try:
            r = s.post(base, data={"q": consulta}, timeout=6)
            if r.status_code not in (200, 202):
                continue
            if r.status_code == 202:
                break
            soup = BeautifulSoup(r.text, "html.parser")
            if base.endswith("html/"):
                resultado = soup.select(".result")
            else:
                resultado = soup.select(".result-link")
            if not resultado:
                continue
            for res in resultado:
                a = res.find("a") if base.endswith("html/") else res
                if not a:
                    continue
                href = a.get("href", "")
                href = urllib.parse.unquote(href)
                m = re.search(r"uddg=([^&]+)", href)
                if m:
                    href = urllib.parse.unquote(m.group(1))
                if not re.search(r"facebook\.com/events", href):
                    continue
                titulo = a.get_text(" ", strip=True)
                snippet = ""
                if base.endswith("html/"):
                    sn = res.select_one(".result__snippet")
                    if sn:
                        snippet = sn.get_text(" ", strip=True)
                texto = f"{titulo} {snippet}".strip()
                bloques.append({
                    "titulo": titulo,
                    "url": href.split("?")[0].rstrip("/"),
                    "texto": texto,
                })
            if bloques:
                break
        except Exception:
            continue
    return bloques


def _consultas_de_grupo(group) -> List[str]:
    nombre = (group.get("nombre") or "").strip()
    sub = _subgenero_del_grupo(nombre, group.get("tipo_grupo") or [])
    consultas = []
    # 1) subgénero general primero (más resultados por consulta)
    if sub:
        consultas.append(f'site:facebook.com/events "{sub}"')
    # 2) nombre del grupo (específico)
    if nombre:
        nombre_corto = re.sub(r"\s+", " ", nombre)[:60]
        consultas.append(f'site:facebook.com/events "{nombre_corto}"')
        if sub and sub.lower() not in nombre_corto.lower():
            consultas.append(f'site:facebook.com/events "{nombre_corto}" "{sub}"')
    return consultas[:3]


async def _buscar_consulta_unica(consulta, context):
    """Busca una consulta única: DDG (requests) → Google/Startpage (Playwright)."""
    bloques = await asyncio.to_thread(_buscar_ddg_requests, consulta)
    if bloques:
        return bloques, "OK"
    try:
        estado, bloques = await _buscar(consulta, context, usar_startpage=False)
    except Exception:
        return [], "ERROR"
    if estado == "BLOQUEADO":
        return [], estado
    return bloques, estado


async def _fase_fallback_async(groups_restantes, context,
                               presupuesto: int,
                               objetivo: int) -> List[Dict]:
    """
    Procesa grupos por buscador con consultas únicas desduplicadas y
    búsquedas en serie con esperas (frugal, no satura rate-limit),
    hasta alcanzar objetivo o agotar presupuesto.
    """
    eventos = []
    urls_vistas = set()
    t0 = time.time()

    # Mapa consulta -> lista de grupos que la solicitan
    consulta_grupos = {}
    for g in groups_restantes:
        for c in _consultas_de_grupo(g):
            if c not in consulta_grupos:
                consulta_grupos[c] = []
            consulta_grupos[c].append(g)

    # Consultas globales por subgénero (muy productivas)
    subs_conocidos = set()
    try:
        cfg = _load_json(GRUPOS_FILE) or {}
        for g in cfg.get("groups", []):
            sub = _subgenero_del_grupo(g.get("nombre", ""), g.get("tipo_grupo") or [])
            if sub:
                subs_conocidos.add(sub)
        for sub in sorted(subs_conocidos):
            c = f'site:facebook.com/events "{sub}"'
            if c not in consulta_grupos:
                consulta_grupos[c] = []
    except Exception:
        pass

    # Subgéneros generales primero, luego nombres de grupo.
    # Una consulta de subgénero es aquella cuyo término entre comillas
    # coincide con un subgénero conocido (más productiva: tope 10).
    consultas_sub = [c for c in consulta_grupos
                     if any(f'"{sub}"' in c for sub in subs_conocidos)]
    consultas_nombre = [c for c in consulta_grupos if c not in consultas_sub]
    random.shuffle(consultas_sub)
    random.shuffle(consultas_nombre)
    consultas = consultas_sub + consultas_nombre

    motores = {
        "yahoo": {"fallos": 0, "cooldown": 0.0, "ultimo_ok": -1000.0},
        "brave": {"fallos": 0, "cooldown": 0.0, "ultimo_ok": -1000.0},
        "google": {"fallos": 0, "cooldown": 0.0, "ultimo_ok": -1000.0},
        "startpage": {"fallos": 0, "cooldown": 0.0, "ultimo_ok": -1000.0},
        "ddg": {"fallos": 0, "cooldown": 0.0, "ultimo_ok": -1000.0},
    }
    COOLDOWN_ENGINE = 12.0

    def _motor_disponible():
        """Prioriza el motor que más recientemente dio resultados (favorito)."""
        disponibles = [m for m, st in motores.items()
                       if time.time() >= st["cooldown"]]
        if not disponibles:
            return None
        return max(disponibles, key=lambda m: motores[m]["ultimo_ok"])

    # ---- Prospección paralela: probar todos los motores a la vez ----
    # Los motores se saturan de forma independiente (dominios distintos);
    # lanzar 5 consultas de subgénero en paralelo detecta al instante
    # cuáles están disponibles y los marca como favoritos.
    prospeccion_cache = {}
    if consultas_sub and len(eventos) < objetivo:
        print("   🔀 Prospección paralela de motores...", flush=True)
        orden_motores = list(motores.keys())
        pares = []
        for k, c in enumerate(consultas_sub):
            if k >= len(orden_motores):
                break
            pares.append((orden_motores[k], c))
        async def _prospeccion(m, c):
            try:
                if m == "ddg":
                    return m, await asyncio.to_thread(_buscar_ddg_requests, c)
                if m == "yahoo":
                    _, b = await _buscar_yahoo(c, context)
                    return m, b
                if m == "brave":
                    _, b = await _buscar_brave(c, context)
                    return m, b
                if m == "google":
                    _, b = await _buscar(c, context, usar_startpage=False)
                    return m, b
                if m == "startpage":
                    _, b = await _buscar(c, context, usar_startpage=True)
                    return m, b
            except Exception:
                return m, []
            return m, []
        resultados_prospeccion = await asyncio.gather(
            *[_prospeccion(m, c) for m, c in pares])
        for m, b in resultados_prospeccion:
            if b:
                motores[m]["fallos"] = 0
                motores[m]["ultimo_ok"] = time.time()
                motores[m]["cooldown"] = 0.0
                print(f"   ✅ Motor {m} disponible ({len(b)} bloques).", flush=True)
            else:
                motores[m]["fallos"] = 0
                motores[m]["cooldown"] = time.time() + COOLDOWN_ENGINE
                print(f"   ❌ Motor {m} saturado → cooldown {COOLDOWN_ENGINE:.0f}s.", flush=True)
        # Las consultas usadas en prospección se procesan igualmente abajo
        # (consulta -> bloques ya descargados), evitando re-descargarlas.
        prospeccion_cache = {}
        for (m, c), (_, b) in zip(pares, resultados_prospeccion):
            if b:
                prospeccion_cache[c] = (m, b)
        await asyncio.sleep(random.uniform(0.5, 1.0))

    for i, consulta in enumerate(consultas):
        if len(eventos) >= objetivo:
            break
        if time.time() - t0 >= presupuesto:
            break

        # Si esta consulta ya se descargó en la prospección, procesarla
        # directamente sin consumir otra petición al motor.
        if consulta in prospeccion_cache:
            motor, bloques = prospeccion_cache[consulta]
            estado = "OK"
        else:
            motor = _motor_disponible()
            if motor is None:
                # Todos en cooldown: esperar el que antes se libere y reintentar.
                restante = min(max(st["cooldown"] - time.time(), 0.0)
                               for st in motores.values())
                if restante > 0:
                    print(f"   ⚠️ Todos los motores en cooldown, esperando {restante:.0f}s...",
                          flush=True)
                    await asyncio.sleep(min(restante + 0.5, 20.0))
                continue

            bloques = []
            estado = "OK"
            if motor == "ddg":
                bloques = await asyncio.to_thread(_buscar_ddg_requests, consulta)
            elif motor == "yahoo":
                try:
                    estado, bloques = await _buscar_yahoo(consulta, context)
                except Exception:
                    bloques = []
            elif motor == "brave":
                try:
                    estado, bloques = await _buscar_brave(consulta, context)
                except Exception:
                    bloques = []
            elif motor == "google":
                try:
                    estado, bloques = await _buscar(consulta, context, usar_startpage=False)
                except Exception:
                    bloques = []
            elif motor == "startpage":
                try:
                    estado, bloques = await _buscar(consulta, context, usar_startpage=True)
                except Exception:
                    bloques = []

        if not bloques:
            motores[motor]["fallos"] += 1
            print(f"   ⚠️ {motor} sin resultados (fallo {motores[motor]['fallos']}).", flush=True)
            if motores[motor]["fallos"] >= 2:
                motores[motor]["cooldown"] = time.time() + COOLDOWN_ENGINE
                motores[motor]["fallos"] = 0
                print(f"   ⏳ {motor} en cooldown {COOLDOWN_ENGINE:.0f}s.", flush=True)
            await asyncio.sleep(random.uniform(0.8, 1.4))
            continue

        motores[motor]["fallos"] = 0
        motores[motor]["ultimo_ok"] = time.time()

        # Consultas de subgénero → hasta 10 resultados;
        # consultas de nombre de grupo → 3 resultados.
        tope = MAX_RESULTS_POR_GRUPO if consulta not in consultas_sub else 10
        sub_consulta = ""
        if consulta in consultas_sub:
            m = re.search(r'"([^"]+)"', consulta)
            if m:
                sub_consulta = m.group(1)
        for b in bloques[:tope]:
            if not _es_relevante_grupos(b, sub_consulta):
                continue
            ev = _extraer_del_serp(b)
            if not ev:
                continue
            if not ev.get("nombre", "").strip():
                continue
            url = ev.get("url", "")
            if url and url in urls_vistas:
                continue
            if url:
                urls_vistas.add(url)
            g = consulta_grupos[consulta][0] if consulta_grupos[consulta] else {}
            ev["grupo_id"] = g.get("id", "")
            ev["grupo_nombre"] = g.get("nombre", "") or (consulta.split('"')[1] if '"' in consulta else "")
            if not ev.get("subgenero"):
                ev["subgenero"] = _subgenero_del_grupo(
                    g.get("nombre", ""), g.get("tipo_grupo") or []) or (consulta.split('"')[1] if '"' in consulta else "")
            eventos.append(ev)
            print(
                f"  ✅ [{len(eventos)}] ({motor}) {ev['nombre'][:40]} | "
                f"{ev.get('fecha','?')} | {ev.get('ciudad','?')}", flush=True
            )

        # Espera entre consultas para no saturar el buscador
        await asyncio.sleep(random.uniform(1.5, 2.2))
    return eventos


# --------------------------------------------------------------------------
# Deduplicación semántica
# --------------------------------------------------------------------------

_RE_NOMBRE = re.compile(r"[\d().\-–—–\[\]{}★✦«»\"'™®&|/@]+", re.I)
_RE_TIPO_DUPLICADO = re.compile(r"\b(20\d{2}|vol|volume|part|parte|ed|edicion|special|anniversary|20th)\b", re.I)


def _normalizar_nombre(nombre: str) -> str:
    """Normaliza el nombre del evento para deduplicación semántica."""
    n = nombre.lower().strip()
    n = _RE_NOMBRE.sub(" ", n)
    n = _RE_TIPO_DUPLICADO.sub(" ", n)
    n = re.sub(r"\s+", " ", n).strip()
    return n


# --------------------------------------------------------------------------
# Orquestador
# --------------------------------------------------------------------------

def _ordenar_grupos(groups: List[Dict]) -> List[Dict]:
    """Grupos productivos primero, luego el resto."""
    productivos = _load_json(GRUPOS_PRODUCTIVOS_FILE) or []
    ids_productivos = set(productivos) if isinstance(productivos, list) else set()
    productivos_list = [g for g in groups if g.get("id") in ids_productivos]
    resto = [g for g in groups if g.get("id") not in ids_productivos]
    random.shuffle(resto)
    return productivos_list + resto


# --------------------------------------------------------------------------
# Inferencia de tipo de lugar + métricas de rendimiento (auto‑mejora)
# --------------------------------------------------------------------------

_TIPO_LUGAR_RE = [
    ("forest", re.compile(r"\b(forest|bosque|wood|bosque|naturaleza)\b", re.I)),
    ("festival", re.compile(r"\b(festival|open air|openair|fair|feria)\b", re.I)),
    ("club", re.compile(r"\b(club|bar|venue|sala|discoteca|klubb)\b", re.I)),
    ("outdoor", re.compile(r"\b(park|playa|beach|terraza|rooftop|outdoor|garden|al aire libre|camping|campo)\b", re.I)),
    ("indoor", re.compile(r"\b(casa|centro cultural|galería|auditorio|theater|teatro|estudio|stúdio|indoor)\b", re.I)),
]


def _inferir_tipo_lugar(nombre: str, lugar: str, texto: str,
                        grupo_nombre: str) -> str:
    blob = " ".join([nombre or "", lugar or "", texto or "",
                     grupo_nombre or ""]).lower()
    for tipo, rx in _TIPO_LUGAR_RE:
        if rx.search(blob):
            return tipo
    return "N/A"


def _actualizar_rendimiento(grupo_id: str, metrica: Dict) -> None:
    """Actualiza grupos_rendimiento.json de forma acumulativa (suma, no resta)."""
    perf = _load_json(GRUPOS_RENDIMIENTO_FILE)
    if not isinstance(perf, dict):
        perf = {}
    g = perf.get(grupo_id, {"intentos": 0, "eventos": 0, "errores": 0,
                            "tiempo_total": 0.0, "ultima_vez": ""})
    g["intentos"] = g.get("intentos", 0) + metrica.get("intentos", 0)
    g["eventos"] = g.get("eventos", 0) + metrica.get("eventos", 0)
    g["errores"] = g.get("errores", 0) + metrica.get("errores", 0)
    g["tiempo_total"] = g.get("tiempo_total", 0.0) + metrica.get("tiempo_total", 0.0)
    g["ultima_vez"] = metrica.get("ultima_vez", g.get("ultima_vez", ""))
    if metrica.get("evento_ejemplo"):
        g["evento_ejemplo"] = metrica["evento_ejemplo"]
    perf[grupo_id] = g
    _save_json(GRUPOS_RENDIMIENTO_FILE, perf)


def _estandarizar(ev: Dict, grupo_nombre: str) -> Dict:
    return {
        "nombre": ev.get("nombre", ""),
        "fecha": ev.get("fecha", "N/A"),
        "lugar": ev.get("lugar", "N/A"),
        "ciudad": ev.get("ciudad", "N/A"),
        "pais": ev.get("pais", "N/A"),
        "tipo_lugar": ev.get("tipo_lugar", "N/A"),
        "fuente": f"Facebook ({grupo_nombre or 'desconocido'})",
        "organizador": ev.get("organizador", "N/A"),
        "email": ev.get("email", "N/A"),
        "url": ev.get("url", ""),
        "subgenero": ev.get("subgenero", ""),
        "descripcion": ev.get("descripcion", ""),
    }


def run_pipeline(config: Optional[dict] = None) -> List[Dict]:
    """
    Fase 1: HTTP mbasic en paralelo (10 workers, timeout 8s).
    Fase 2: fallback por buscador con presupuesto de tiempo.
    Guarda grupos_productivos.json y eventos_facebook_grupos.json.
    """
    data = _load_json(GRUPOS_FILE)
    if not data:
        print("❌ No existe grupos_encontrados.json.")
        return []
    groups = data.get("groups", [])
    if not groups:
        print("⚠️ No hay grupos.")
        return []

    groups = _ordenar_grupos(groups)
    print(f"🔍 Extrayendo eventos de {len(groups)} grupos (sin cookies, 2 fases)...")

    eventos = []
    produjo_eventos = set()
    t_inicio = time.time()

    # ---- Fase 1: HTTP mbasic en paralelo (por lotes para salir a tiempo) ----
    print("⚡ Fase 1: HTTP mbasic (10 workers, timeout 8s)...")
    t_http = time.time()
    try:
        from concurrent.futures import ThreadPoolExecutor as _TPE
        pool = _TPE(max_workers=MAX_WORKERS)
        try:
            for inicio in range(0, len(groups), MAX_WORKERS):
                if time.time() - t_http >= HTTP_BUDGET_SEC:
                    break
                if len(eventos) >= TARGET_EVENTS:
                    break
                lote = groups[inicio:inicio + MAX_WORKERS]
                futuros = {
                    pool.submit(
                        _scrape_group_http_full,
                        g.get("id", ""), g.get("nombre", ""),
                        g.get("url", f"https://facebook.com/groups/{g.get('id','')}"),
                    ): g for g in lote
                }
                for fut, g in futuros.items():
                    if time.time() - t_http >= HTTP_BUDGET_SEC:
                        break
                    try:
                        evs = fut.result(timeout=0.5)
                    except Exception:
                        continue
                    for ev in evs:
                        ev["grupo_id"] = g.get("id", "")
                        ev["grupo_nombre"] = g.get("nombre", "")
                        ev["subgenero"] = ev.get("subgenero") or _subgenero_del_grupo(
                            g.get("nombre", ""), g.get("tipo_grupo"))
                        eventos.append(ev)
                    if evs:
                        produjo_eventos.add(g.get("id", ""))
                if _LOGIN_WALL_DETECTADO:
                    print("   ⚠️ Login wall detectado en mbasic, saltando fase 1.", flush=True)
                    break
        finally:
            pool.shutdown(wait=False)
    except Exception as e:
        print(f"  ⚠️ Fase 1: {e}")

    # ---- Fase 2: fallback por buscador ----
    restantes = [g for g in groups if g.get("id") not in produjo_eventos]
    if len(eventos) < TARGET_EVENTS and restantes:
        presupuesto = max(
            15,
            TIME_BUDGET_SEC - (time.time() - t_inicio) - 8,
        )
        print(
            f"🌐 Fase 2: fallback por buscador (presupuesto {presupuesto:.0f}s, "
            f"{len(restantes)} grupos)..."
        )
        try:
            async def _fase():
                from playwright.async_api import async_playwright
                async with async_playwright() as p:
                    browser = await p.chromium.launch(
                        headless=True,
                        args=["--no-sandbox", "--disable-dev-shm-usage",
                               "--disable-blink-features=AutomationControlled"],
                    )
                    context = await get_anti_block().create_stealth_context(browser, use_tor=True)
                    try:
                        return await _fase_fallback_async(
                            restantes, context,
                            presupuesto=max(5, presupuesto),
                            objetivo=TARGET_EVENTS - len(eventos),
                        )
                    finally:
                        await browser.close()
            fase2 = asyncio.run(_fase())
            for ev in fase2:
                eventos.append(ev)
            produjo_eventos |= {ev.get("grupo_id", "") for ev in fase2}
        except Exception as e:
            print(f"  ⚠️ Fase 2: {e}")

    # ---- Deduplicación ----
    vistos = set()
    unicos = []
    for ev in eventos:
        k = (_normalizar_nombre(ev.get("nombre", "")),
             ev.get("fecha", "").strip(),
             ev.get("lugar", "").strip().lower(),
             ev.get("ciudad", "").strip().lower())
        if k not in vistos:
            vistos.add(k)
            unicos.append(ev)

    # ---- Normalizar ----
    estandar = []
    for ev in unicos:
        e = _estandarizar(ev, ev.get("grupo_nombre", ""))
        if not e.get("tipo_lugar") or e["tipo_lugar"] in ("N/A", ""):
            e["tipo_lugar"] = _inferir_tipo_lugar(
                ev.get("nombre", ""), ev.get("lugar", ""),
                ev.get("descripcion", ""), ev.get("grupo_nombre", ""))
        estandar.append(e)

    # ---- Guardar grupos productivos, rendimiento y salida ----
    _save_json(GRUPOS_PRODUCTIVOS_FILE, sorted(p for p in produjo_eventos if p))
    try:
        from datetime import datetime as _dt
        for gid in sorted(p for p in produjo_eventos if p):
            _actualizar_rendimiento(gid, {
                "intentos": 1, "eventos": 1, "errores": 0,
                "tiempo_total": 0.0,
                "ultima_vez": _dt.now(timezone.utc).isoformat(),
            })
    except Exception:
        pass
    _save_json(OUTPUT_FILE, estandar)
    print(f"✅ {len(estandar)} eventos reales guardados en {OUTPUT_FILE}")
    print(f"   Grupos productivos: {len(produjo_eventos)}")
    return estandar


if __name__ == "__main__":
    if "--setup-cookies" in sys.argv:
        try:
            from scrapers.facebook_mcp import setup_cookies
        except ImportError:
            print("❌ No se pudo importar setup_cookies de facebook_mcp.")
            sys.exit(1)
        setup_cookies()
        sys.exit(0)

    eventos = run_pipeline()
    print(f"\nTotal eventos reales: {len(eventos)}")
    for ev in eventos[:10]:
        sg = ev.get("subgenero", "")
        print(f"  - {ev['nombre'][:55]} | {ev['fecha']} | {ev['lugar']} | {sg}")
