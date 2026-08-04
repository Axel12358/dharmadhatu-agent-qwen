#!/usr/bin/env python3
"""
Extractor de eventos de música (especialmente psYTrance) desde Instagram.

Filosofía: **suma, nunca resta**. No modifica ni deshabilita ningún scraper
existente (goabase, songkick, resident_advisor, facebook_mcp,
facebook_events_from_groups). Este módulo es totalmente aditivo.

Estrategia combinada (ver análisis de referencia):
  - **Método A (rápido):** ``requests`` sobre
    ``https://www.instagram.com/explore/tags/<hashtag>/`` extrayendo los
    posts públicos (links /p/<shortcode> + captions) parseando el HTML.
    ✅ Rápido, sin dependencias extra de navegador.
  - **Método B (robusto):** Playwright+stealth renderizando la página del
    post; si Instagram no redirige a login, extrae caption vía
    ``<meta property="og:description">`` o DOM.
  - **Método C (fallback último):** búsqueda pública ``site:instagram.com``
    vía Yahoo/Startpage (reutilizada de facebook_public_events).

Instagram cambia su estructura y bloquea el scraping anónimo; por eso el
pipeline es **tolerante a fallos**: si un hashtag o método falla, se
registra y se continúa con el siguiente.

Auto‑mejora (loop de mejora) al estilo facebook_events_from_groups.py:
  - ``instagram_hashtags_estado.json``: offset rotativo para variar hashtags.
  - ``instagram_hashtags_productivos.json``: hashtags que aportaron eventos
    (prioridad absoluta en la siguiente ejecución).
  - ``instagram_rendimiento.json``: métricas por hashtag.
"""

import asyncio
import json
import random
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Dict, Optional, Tuple

import requests
from bs4 import BeautifulSoup

_PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

try:
    from scrapers.anti_block import get_anti_block
except Exception:  # pragma: no cover
    get_anti_block = None
try:
    from scrapers.event_extractor import EventExtractor
except Exception:  # pragma: no cover
    EventExtractor = None
try:
    from playwright.async_api import async_playwright
    _TIENE_PLAYWRIGHT = True
except Exception:  # pragma: no cover
    _TIENE_PLAYWRIGHT = False

try:
    from loop_optimizer import get_optimizer
except Exception:  # pragma: no cover
    get_optimizer = None

try:
    from scrapers.improvement_loop import run_improvement_loop
except Exception:  # pragma: no cover
    run_improvement_loop = None

# --------------------------------------------------------------------------
# Constantes / configuración
# --------------------------------------------------------------------------

OUTPUT_FILE = str(Path(_PROJECT_ROOT) / "eventos_instagram.json")
HASHTAGS_PRODUCTIVOS_FILE = str(Path(_PROJECT_ROOT) / "instagram_hashtags_productivos.json")
RENDIMIENTO_FILE = str(Path(_PROJECT_ROOT) / "instagram_rendimiento.json")
ESTADO_HASHTAGS_FILE = str(Path(_PROJECT_ROOT) / "instagram_hashtags_estado.json")
SESSION_FILE = str(Path(_PROJECT_ROOT) / "instagram_session.json")
ORGANIZADORES_IG_FILE = str(Path(_PROJECT_ROOT) / "organizadores_instagram.json")
CONFIG_GRUPOS = str(Path(_PROJECT_ROOT) / "config_grupos.json")

TIMEOUT_TOTAL_SEG = 180         # 3 minutos máximo
MAX_POSTS_POR_HASHTAG = 15
MAX_HASHTAGS_FASE = 20

_SUBGENERICOS_DEFAULT = [
    "darkpsy", "forest", "psychill", "psybient", "fullon", "progressive",
    "goa", "hitech", "twilight", "psycore", "suomisaundi", "zenon",
    "psychedelic", "psytrance",
]

_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:126.0) Gecko/20100101 Firefox/126.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
]


# --------------------------------------------------------------------------
# Utilidades shared
# --------------------------------------------------------------------------

def _load_json(path: str) -> Optional[object]:
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


def _load_config() -> dict:
    cfg = _load_json(CONFIG_GRUPOS) or {}
    return cfg if isinstance(cfg, dict) else {}


def _limpiar_hashtag(raw: str) -> str:
    """Normaliza un hashtag: sin @/#/espacios/puntuación, minúsculas alfanumérico."""
    h = (raw or "").lower()
    h = re.sub(r"[^a-z0-9]+", "", h)
    return h


def _tiene_sesion_activa() -> bool:
    """True si existe instagram_session.json con un session_id no vacío."""
    data = _load_json(SESSION_FILE)
    if not isinstance(data, dict):
        return False
    return bool((data.get("session_id") or "").strip())


def _headers() -> Dict[str, str]:
    return {
        "User-Agent": random.choice(_USER_AGENTS),
        "Accept-Language": "en-US,en;q=0.9",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }


def _rotar_estado(clave: str, n: int) -> int:
    """Offset rotativo leyendo/escribiendo instagram_hashtags_estado.json."""
    estado = _load_json(ESTADO_HASHTAGS_FILE)
    if not isinstance(estado, dict):
        estado = {"offset_sub": 0, "offset_ciudad": 0, "run": 0}
    estado["run"] = int(estado.get("run", 0)) + 1
    if n:
        off = (int(estado.get(clave, 0)) + int(estado["run"])) % n
        estado[clave] = off
        _save_json(ESTADO_HASHTAGS_FILE, estado)
        return off
    _save_json(ESTADO_HASHTAGS_FILE, estado)
    return 0


# --------------------------------------------------------------------------
# Generación y rotación de hashtags
# --------------------------------------------------------------------------

def _build_hashtags(config: Optional[dict] = None) -> List[str]:
    """30 hashtags combinando subgéneros + ciudades, rotados por estado."""
    if config is None:
        config = _load_config()
    subs = [s for s in (config.get("subgeneros") or _SUBGENERICOS_DEFAULT)
            if isinstance(s, str) and s]
    ciudades_flat: List[str] = []
    for pais in (config.get("paises") or []):
        for c in pais.get("ciudades", []):
            ciudades_flat.append(_limpiar_hashtag(c))
    random.shuffle(subs)
    random.shuffle(ciudades_flat)

    # Priorizar hashtags productivos de ejecuciones anteriores.
    prod = _load_json(HASHTAGS_PRODUCTIVOS_FILE)
    if isinstance(prod, list):
        prod = [_limpiar_hashtag(h) for h in prod if _limpiar_hashtag(h)]
    else:
        prod = []
    random.shuffle(prod)

    out: List[str] = []
    for h in prod[:6]:
        out.append(h)

    # Rotación con estado para variar la combinación en cada run.
    off_sub = _rotar_estado("offset_sub", len(subs)) if subs else 0
    off_ciu = _rotar_estado("offset_ciudad", len(ciudades_flat)) if ciudades_flat else 0
    subs_rot = subs[off_sub:] + subs[:off_sub] if subs else []
    ciud_rot = ciudades_flat[off_ciu:] + ciudades_flat[:off_ciu] if ciudades_flat else []
    for sub in subs_rot[:10]:
        suf = "" if sub == "psytrance" else "psytrance"
        out.append(f"{_limpiar_hashtag(sub)}{suf}")
        out.append(f"{_limpiar_hashtag(sub)}party")
    for c in ciud_rot[:6]:
        out.append(f"psytrance{c}")

    # Dedup conservando orden, max 30
    vistos, final = set(), []
    for h in out:
        if h and h not in vistos:
            vistos.add(h)
            final.append(h)
    return final[:30]


# --------------------------------------------------------------------------
# Inferencias
# --------------------------------------------------------------------------

_TIPO_LUGAR_RE = [
    ("festival", re.compile(r"\b(festival|open.?air|fair)\b", re.I)),
    ("forest", re.compile(r"\b(forest|bosque|woods|naturaleza|mountain)\b", re.I)),
    ("club", re.compile(r"\b(club|bar|venue|sala|discoteca|klubb)\b", re.I)),
    ("outdoor", re.compile(r"\b(park|playa|beach|terraza|rooftop|camping|campo)\b", re.I)),
    ("indoor", re.compile(r"\b(auditorio|theater|teatro|estudio)\b", re.I)),
]


def _inferir_tipo_lugar(nombre: str, lugar: str, texto: str,
                        ciudad: str, sub: str) -> str:
    blob = " ".join([nombre or "", lugar or "", texto or "",
                     ciudad or "", sub or ""]).lower()
    for tipo, rx in _TIPO_LUGAR_RE:
        if rx.search(blob):
            return tipo
    return "N/A"


_RE_NOMBRE = re.compile(r"[\d().\-–—–\[\]{}★✦«»\"'™®&|/@:;,!]+", re.I)


def _normalizar_nombre(nombre: str) -> str:
    n = re.sub(_RE_NOMBRE, " ", (nombre or "").lower()).strip()
    return re.sub(r"\s+", " ", n)


def _extraer_subgenero_de(caption: str, ciudad: str, url: str) -> str:
    texto = f"{caption or ''} {url}".lower()
    for sub in ["darkpsy", "forest", "psychill", "psybient", "fullon",
                "progressive", "suomisaundi", "hitech", "twilight", "psycore",
                "zenon", "psychedelic", "psytrance", "goa", "dark"]:
        if re.search(rf"\b{re.escape(sub)}\b", texto):
            return sub
    return "N/A"


def _estandarizar(nombre, fecha, lugar, ciudad, pais, organizador, url,
                  sub, caption="") -> Dict:
    return {
        "nombre": (nombre or "")[:120],
        "fecha": (fecha or "N/A") if fecha else "N/A",
        "lugar": (lugar or "N/A"),
        "ciudad": (ciudad or "N/A"),
        "pais": (pais or "N/A"),
        "tipo_lugar": _inferir_tipo_lugar(nombre, lugar, caption, ciudad, sub),
        "fuente": "Instagram",
        "organizador": (organizador or "N/A"),
        "email": "N/A",
        "url": (url or ""),
        "subgenero": sub or "",
        "descripcion": (caption or "")[:500],
    }


# --------------------------------------------------------------------------
# Métricas / productividad
# --------------------------------------------------------------------------

def _actualizar_rendimiento(ht: str, m: Dict) -> None:
    perf = _load_json(RENDIMIENTO_FILE)
    if not isinstance(perf, dict):
        perf = {}
    g = perf.get(ht, {"intentos": 0, "posts": 0, "eventos": 0,
                      "errores": 0, "tiempo_total": 0.0, "ultima_vez": ""})
    g["intentos"] = g.get("intentos", 0) + m.get("intentos", 0)
    g["posts"] = g.get("posts", 0) + m.get("posts", 0)
    g["eventos"] = g.get("eventos", 0) + m.get("eventos", 0)
    g["errores"] = g.get("errores", 0) + m.get("errores", 0)
    g["tiempo_total"] = g.get("tiempo_total", 0.0) + m.get("tiempo_total", 0.0)
    g["ultima_vez"] = m.get("ultima_vez", g.get("ultima_vez", ""))
    perf[ht] = g
    _save_json(RENDIMIENTO_FILE, perf)


def _guardar_productivos() -> None:
    perf = _load_json(RENDIMIENTO_FILE) or {}
    prod = sorted([h for h, m in perf.items() if m.get("eventos", 0) > 0])
    _save_json(HASHTAGS_PRODUCTIVOS_FILE, prod)


def _cargar_productivos() -> List[str]:
    prod = _load_json(HASHTAGS_PRODUCTIVOS_FILE)
    if isinstance(prod, list):
        return [_limpiar_hashtag(h) for h in prod if _limpiar_hashtag(h)]
    return []


# --------------------------------------------------------------------------
# EventExtractor sobre texto de caption
# --------------------------------------------------------------------------

def _eventos_desde_texto(caption: str, url: str, sub: str = "") -> List[Dict]:
    """Extrae eventos del caption usando EventExtractor (nombre, fecha, lugar)."""
    if EventExtractor is None or not caption or len(caption.strip()) < 20:
        return []
    extractor = EventExtractor()
    evs = extractor.extract_all(caption, "Instagram", url) or []
    out = []
    for ev in evs:
        nombre = (ev.get("nombre") or caption.split("\n")[0][:80] or "")
        if not nombre.strip():
            nombre = "Evento Instagram"
        out.append(_estandarizar(
            nombre, ev.get("fecha") or "N/A", ev.get("lugar") or "N/A",
            ev.get("ciudad") or "N/A", ev.get("pais", "N/A"),
            ev.get("organizador") or "N/A", url,
            sub or _extraer_subgenero_de(caption, ev.get("ciudad") or "", url),
            caption))
    return out


# --------------------------------------------------------------------------
# Método A: requests sobre /explore/tags/<hashtag>
# --------------------------------------------------------------------------

def _parse_html_posts(html: str) -> List[Dict]:
    """
    Parsea el HTML de /explore/tags/<hashtag> y extrae posts públicos.
    Combina links (/p/<shortcode>) con captions/localizaciones encontrados.
    """
    posts: List[Dict] = []
    links = re.findall(
        r'https://www\.instagram\.com/(?:p|reel)/([A-Za-z0-9_-]+)/', html)
    # captions aparecen escapados en el JSON de la shell React.
    captions = re.findall(r'\\"caption\\":\\"([^"\\]{1,600})', html)
    captions += re.findall(r'"caption":"([^"]{1,600})"', html)
    locations = re.findall(r'\\"location_string\\":\\"([^"\\]{1,80})', html)
    locations += re.findall(r'"location_name":"([^"]{1,80})"', html)
    for i, sc in enumerate(links[:MAX_POSTS_POR_HASHTAG]):
        cap = captions[i] if i < len(captions) else ""
        loc = locations[i] if i < len(locations) else ""
        posts.append({
            "url": f"https://www.instagram.com/p/{sc}",
            "caption": cap,
            "location": loc,
        })
    return posts


def _scrape_hashtag_requests(htag: str, t0: float) -> List[Dict]:
    """Método A: extrae posts de un hashtag vía requests HTML público."""
    if time.time() - t0 > TIMEOUT_TOTAL_SEG:
        return []
    url = f"https://www.instagram.com/explore/tags/{htag}/"
    all_posts: List[Dict] = []
    proxies = None
    if get_anti_block:
        try:
            get_anti_block()._ensure_tor()
            proxies = get_anti_block().requests_proxies() or None
        except Exception:
            proxies = None
    try:
        for intento in range(2):
            try:
                r = requests.get(url, headers=_headers(), timeout=15,
                                 allow_redirects=True, proxies=proxies)
            except requests.RequestException:
                break
            if r.url.startswith("https://www.instagram.com/accounts/login"):
                return []  # requiere login
            if r.status_code == 200 and len(r.text) > 2000:
                posts = _parse_html_posts(r.text)
                if posts:
                    all_posts = posts
                    break
            time.sleep(random.uniform(0.5, 1.0))
    except Exception:
        return []
    return all_posts[:MAX_POSTS_POR_HASHTAG]


# --------------------------------------------------------------------------
# Método B: Playwright render del post (og:description + DOM)
# --------------------------------------------------------------------------

async def _scrape_post_playwright(url: str, context) -> Optional[Dict]:
    """Renderiza un post (si no pide login) y extrae caption/ubicación."""
    if not _TIENE_PLAYWRIGHT or not url:
        return None
    page = None
    try:
        page = await context.new_page()
        ab = get_anti_block()
        if ab:
            await ab.apply_playwright_stealth(page)
        await page.goto(url, timeout=20000, wait_until="domcontentloaded")
        await page.wait_for_timeout(3000)
        if "/login" in page.url or "accounts/login" in page.url:
            return None
        caption = ""
        try:
            og = await page.eval_on_selector(
                'meta[property="og:description"]',
                "el => el ? el.getAttribute('content') : ''")
            if og:
                caption = og
        except Exception:
            pass
        if not caption:
            try:
                caption = await page.eval_on_selector_all(
                    "article", "els => els.map(e=>e.innerText).join('\\n')") or ""
            except Exception:
                caption = ""
        if not caption:
            return None
        fecha = None
        try:
            td = await page.eval_on_selector_all(
                "time", "els => els.length ? els[0].getAttribute('datetime') : ''")
            if td:
                m = re.match(r"(\d{4}-\d{2}-\d{2})", td or "")
                if m:
                    fecha = m.group(1)
        except Exception:
            pass
        ciudad = "N/A"
        try:
            loc = await page.eval_on_selector_all(
                "[data-testid='location'] a, a[href*='/explore/locations/']",
                "els => els.length ? els[0].innerText.trim() : ''")
            if loc:
                ciudad = loc
        except Exception:
            pass
        return {"caption": caption, "fecha": fecha, "ciudad": ciudad,
                "url": url, "location_name": ciudad}
    except Exception:
        return None
    finally:
        if page:
            try:
                await page.close()
            except Exception:
                pass


# --------------------------------------------------------------------------
# Método C: búsqueda pública site:instagram.com
# --------------------------------------------------------------------------

async def _buscar_site_instagram(consulta: str, context) -> List[Dict]:
    """Busca posts públicos de Instagram vía buscador (brave → yahoo → startpage)."""
    bloques = []
    try:
        from scrapers.facebook_public_events import _buscar_brave
        _, bloques = await _buscar_brave(consulta, context)
        if not bloques:
            from scrapers.facebook_public_events import _buscar_yahoo
            _, bloques = await _buscar_yahoo(consulta, context)
        if not bloques:
            from scrapers.facebook_public_events import _buscar
            _, bloques = await _buscar(consulta, context, usar_startpage=True)
    except Exception:
        bloques = []
    out = []
    for b in bloques:
        href = b.get("url") or ""
        if not re.search(r"instagram\.com", href):
            continue
        titulo = (b.get("titulo") or b.get("texto") or "").strip()
        if not titulo:
            continue
        out.append({"titulo": titulo[:120], "texto": (b.get("texto") or "")[:300],
                    "url": href.split("?")[0].rstrip("/")})
    return out


# --------------------------------------------------------------------------
# Pipeline por hashtag (with fallback methods)
# --------------------------------------------------------------------------

def _eventos_del_post(url: str, caption: str, location: str,
                      sub: str) -> List[Dict]:
    """Extrae eventos de un post usando caption + location."""
    texto = caption
    if location and location not in caption:
        texto = f"{caption}\n{location}"
    ciudad = location or "N/A"
    evs = _eventos_desde_texto(texto, url, sub)
    if evs and ciudad == "N/A":
        for ev in evs:
            if ev["ciudad"] in ("", "N/A"):
                ev["ciudad"] = ciudad
    return evs


# --------------------------------------------------------------------------
# Deduplicación semántica
# --------------------------------------------------------------------------

def _dedup_semantic(eventos: List[Dict]) -> List[Dict]:
    vistos = set()
    out = []
    for ev in eventos:
        k = (_normalizar_nombre(ev.get("nombre", "")),
             ev.get("fecha", "").strip(),
             ev.get("lugar", "").strip().lower(),
             ev.get("ciudad", "").strip().lower(),
             (ev.get("url", "").split("?")[0].rstrip("/")))
        if k not in vistos:
            vistos.add(k)
            out.append(ev)
    return out


# --------------------------------------------------------------------------
# Orquestador
# --------------------------------------------------------------------------

def scrape_instagram_events(config: Optional[dict] = None) -> List[Dict]:
    """
    Extrae eventos musicales de Instagram (psYTrance).

    Estrategia combinada (tolerante a fallos):
      Método A (requests) → Método B (Playwright) → Método C (búsqueda pública).

    Auto‑mejora vía instagram_hashtags_estado.json /
    instagram_hashtags_productivos.json / instagram_rendimiento.json.

    Nunca lanza al caller: captura todos los errores.
    """
    t0 = time.time()
    if config is None:
        config = _load_config()
    evento_out: List[Dict] = []

    print("📱 Scraping Instagram (requests → playwright → fallback)...", flush=True)
    hashtags = _build_hashtags(config)
    print(f"   🔖 Hashtags objetivo: {len(hashtags)}", flush=True)
    # Sin sesión, Instaloader público falla siempre (403/login). En ese
    # caso, procesar pocos hashtags por requests (rápido) y pasar rápido
    # al fallback de buscadores, sin agotar el timeout.
    intento_rapido = not _tiene_sesion_activa()
    max_fase_a = 4 if intento_rapido else len(hashtags)

    # Fase A: método requests (rápido, por hashtag). Se recopilan los
    # hashtags que no aportaron (fallan a Playwright en fase B).
    pendientes_b: List[str] = []
    for i in range(min(len(hashtags), max_fase_a)):
        if time.time() - t0 > TIMEOUT_TOTAL_SEG:
            print("   ⏳ Timeout total alcanzado; devolviendo lo obtenido.", flush=True)
            break
        if len(evento_out) >= 18:
            break
        if len(evento_out) >= 18:
            break
        h = _limpiar_hashtag(hashtags[i])
        if not h:
            continue
        metrica = {"intentos": 0, "posts": 0, "eventos": 0,
                   "errores": 0, "tiempo_total": 0.0, "ultima_vez": ""}
        t_ht = time.time()
        try:
            posts = _scrape_hashtag_requests(h, t0)
            if not posts:
                metrica["errores"] += 1
                pendientes_b.append(h)
                print(f"   ⚠️ #{h}: sin posts públicos (requests); fallback Playwright", flush=True)
            else:
                eventos_h = 0
                for p in posts[:MAX_POSTS_POR_HASHTAG]:
                    if time.time() - t0 > TIMEOUT_TOTAL_SEG:
                        break
                    evs = _eventos_del_post(p.get("url", ""), p.get("caption", ""),
                                            p.get("location", ""), h)
                    if evs:
                        evento_out.extend(evs)
                        eventos_h += len(evs)
                    if len(evento_out) >= 18:
                        break
                metrica["posts"] = len(posts)
                metrica["eventos"] = eventos_h
                metrica["intentos"] = 1
                if eventos_h:
                    print(f"   ✅ #{h}: {eventos_h} evento(s)", flush=True)
                else:
                    pendientes_b.append(h)
                    print(f"   ⚠️ #{h}: posts sin eventos; fallback Playwright", flush=True)
        except Exception as e:
            metrica["errores"] += 1
            pendientes_b.append(h)
            print(f"   ⚠️ #{h}: falló requests ({type(e).__name__}); continuo.", flush=True)
        metrica["tiempo_total"] = round(time.time() - t_ht, 2)
        metrica["ultima_vez"] = datetime.now(timezone.utc).isoformat()
        _actualizar_rendimiento(h, metrica)
        time.sleep(random.uniform(1.0, 2.0))

    # Fase B: Playwright (método B + método C) sobre hashtags pendientes.
    if pendientes_b and len(evento_out) < 18 and time.time() - t0 < TIMEOUT_TOTAL_SEG:
        try:
            eventos_b = asyncio.run(_fase_playwright(pendientes_b, t0, hashtags))
            evento_out.extend(eventos_b)
        except Exception as e:
            print(f"   ⚠️ Fase Playwright capturada: {type(e).__name__}", flush=True)

    # ---- Normalizar, deduplicar, guardar ----
    evento_out = _dedup_semantic(evento_out)
    evento_out = [_estandarizar(
        ev.get("nombre", ""), ev.get("fecha", "N/A"), ev.get("lugar", "N/A"),
        ev.get("ciudad", "N/A"), ev.get("pais", "N/A"),
        ev.get("organizador", "N/A"), ev.get("url", ""),
        ev.get("subgenero", ""), ev.get("descripcion", ""))
        for ev in evento_out]
    _guardar_productivos()
    if evento_out:
        _save_json(OUTPUT_FILE, evento_out)
    print(f"✅ {len(evento_out)} eventos de Instagram guardados en {OUTPUT_FILE}", flush=True)
    total_tiempo = time.time() - t0
    print(f"⏱ Tiempo total: {total_tiempo:.0f}s", flush=True)

    # ---- LOOP DE MEJORA CONTINUA (estilo facebook_events_from_groups) ----
    if get_optimizer:
        try:
            opt = get_optimizer()
            # Estrategias IG: requests (método A), playwright_post (método B),
            # playwright_serp (método C), session_auth (si hay session_id)
            estrategias_ig = ["requests", "playwright_post", "playwright_serp", "session_auth"]

            # Registrar resultados por estrategia
            tiene_sesion = _tiene_sesion_activa()
            exitoso_general = len(evento_out) > 0

            for est in estrategias_ig:
                if est == "requests":
                    # Método A intentó todos los hashtags (pendientes_b + los que dieron eventos)
                    opt.registrar_resultado(est, evento_out, total_tiempo * 0.3, exitoso_general)
                elif est == "playwright_post":
                    # Método B solo si hubo fase Playwright
                    opt.registrar_resultado(est, evento_out, total_tiempo * 0.5, len(pendientes_b) > 0)
                elif est == "playwright_serp":
                    # Método C (SERP fallback) solo si se ejecutó
                    opt.registrar_resultado(est, evento_out, total_tiempo * 0.2, True)
                elif est == "session_auth":
                    opt.registrar_resultado(est, evento_out, total_tiempo * 0.1, tiene_sesion)

            # Ajustar parámetros para próxima ejecución
            config_base = {
                "max_hashtags": len(hashtags),
                "max_posts_por_hashtag": MAX_POSTS_POR_HASHTAG,
                "timeout_total": TIMEOUT_TOTAL_SEG,
                "priorizar_requests": True,
                "priorizar_playwright": _TIENE_PLAYWRIGHT,
                "usar_tor": True,
            }
            config_ajustada = opt.ajustar_parametros(config_base)

            # Aplicar ajustes dinámicos (solo logging; el código lee constantes globales)
            print(f"   📊 Loop mejora: {opt.generar_reporte().split(chr(10))[-3]}", flush=True)
            if config_ajustada.get("max_posts_por_hashtag") != MAX_POSTS_POR_HASHTAG:
                print(f"   🔧 Sugerencia: MAX_POSTS_POR_HASHTAG={config_ajustada['max_posts_por_hashtag']}", flush=True)
            if config_ajustada.get("timeout_total") != TIMEOUT_TOTAL_SEG:
                print(f"   🔧 Sugerencia: TIMEOUT_TOTAL_SEG={config_ajustada['timeout_total']}", flush=True)

            # Guardar iteración completa
            metricas = {
                "total_eventos": len(evento_out),
                "tiempo_total": round(total_tiempo, 1),
                "hashtags_procesados": len(hashtags),
                "hashtags_productivos": sum(1 for h in hashtags if h in _cargar_productivos()),
                "tiene_sesion": tiene_sesion,
                "tor_activo": get_anti_block().tor_active() if get_anti_block else False,
            }
            opt.guardar_iteracion(metricas, config_ajustada)

        except Exception as e:
            print(f"   ⚠️ Loop mejora capturado: {type(e).__name__}", flush=True)

    return evento_out


async def _fase_playwright(pendientes: List[str], t0: float,
                           all_hashtags: List[str]) -> List[Dict]:
    """
    Fase B + C: abre Playwright una vez, renderiza posts de hashtags
    pendientes (método B) y, si no hay suficiente, busca site:instagram.com
    (método C).
    """
    eventos: List[Dict] = []
    if not _TIENE_PLAYWRIGHT or not pendientes:
        return eventos
    try:
        ab = get_anti_block()
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage"],
            )
            context = (await ab.create_stealth_context(browser, use_tor=True)
                       if ab else await browser.new_context())
            try:
                # --- Método B: renderizar posts de hashtags pendientes ---
                for h in pendientes[:12]:
                    if time.time() - t0 > TIMEOUT_TOTAL_SEG or len(eventos) >= 18:
                        break
                    posts = _scrape_hashtag_requests(h, t0)
                    if not posts:
                        continue
                    for post in posts[:MAX_POSTS_POR_HASHTAG]:
                        if time.time() - t0 > TIMEOUT_TOTAL_SEG or len(eventos) >= 18:
                            break
                        data = await _scrape_post_playwright(post.get("url", ""), context)
                        caption = data.get("caption") if data else ""
                        loc = data.get("ciudad") if data else post.get("location", "")
                        if caption or loc:
                            evs = _eventos_del_post(post.get("url", ""), caption, loc, h)
                            if evs:
                                eventos.extend(evs)
                        else:
                            evs = _eventos_del_post(post.get("url", ""),
                                                    post.get("caption", ""),
                                                    post.get("location", ""), h)
                            if evs:
                                eventos.extend(evs)
                        await asyncio.sleep(random.uniform(0.8, 1.6))

                # --- Método C: búsqueda pública site:instagram.com ---
                if len(eventos) < 18 and time.time() - t0 < TIMEOUT_TOTAL_SEG:
                    queries = []
                    for h in pendientes[:6]:
                        hc = _limpiar_hashtag(h)
                        if hc:
                            q = f'site:instagram.com #{hc} psytrance party'
                            if q not in queries:
                                queries.append(q)
                    for q in queries[:4]:
                        if time.time() - t0 > TIMEOUT_TOTAL_SEG or len(eventos) >= 18:
                            break
                        bloques = await _buscar_site_instagram(q, context)
                        random.shuffle(bloques)
                        for b in bloques[:3]:
                            if len(eventos) >= 18:
                                break
                            post_url = b.get("url", "")
                            data = await _scrape_post_playwright(post_url, context)
                            caption = data.get("caption") if data else b.get("texto", "")
                            if caption:
                                evs = _eventos_desde_texto(caption, post_url, "")
                                if evs:
                                    eventos.extend(evs)
                            await asyncio.sleep(random.uniform(1.0, 1.8))
                        await asyncio.sleep(random.uniform(1.0, 1.8))
            finally:
                await browser.close()
    except Exception as e:
        print(f"   ⚠️ Playwright global: {type(e).__name__}: {e}", flush=True)
    return eventos


# ============================================================
# NUEVA ENTRADA PRINCIPAL: Improvement Loop (patrón AgenteQwen)
# ============================================================

def scrape_instagram_events_v2(config: Optional[dict] = None) -> List[Dict]:
    """
    Entry point principal usando ImprovementAgent (arquitectura comparativa + loop mejora).
    
    Ventajas sobre versión anterior:
    - Testea 6 arquitecturas en paralelo en iteración 1
    - Selecciona la mejor automáticamente (score-based)
    - Qwen propone mejoras cada iteración (config + arquitectura)
    - Sub-agente enriquecimiento paralelo (Qwen local)
    - Vector store local (FAISS + embeddings nomic-embed-text) para pattern learning
    - Tor integrado via anti_block
    - Historial persistente + mejor config
    
    Args:
        config: dict opcional con claves:
            - max_hashtags (int, default 20)
            - max_posts_per_tag (int, default 3)
            - timeout_total (int, default 180)
            - use_tor (bool, default True)
            - max_iteraciones (int, default 5)
            - hashtags (List[str], opcional - usa estos en lugar de generar)
    
    Returns:
        Lista de eventos validados y enriquecidos
    """
    if run_improvement_loop is None:
        print("⚠️ ImprovementLoop no disponible, usando versión legacy")
        return scrape_instagram_events(config)

    # Extraer params del config
    max_hashtags = config.get("max_hashtags", 20) if config else 20
    max_posts_per_tag = config.get("max_posts_per_tag", 3) if config else 3
    timeout_total = config.get("timeout_total", 180) if config else 180
    use_tor = config.get("use_tor", True) if config else True
    max_iteraciones = config.get("max_iteraciones", 5) if config else 5
    hashtags = config.get("hashtags") if config else None

    print("🚀 Iniciando Improvement Loop (AgenteQwen pattern)...", flush=True)
    
    try:
        eventos = run_improvement_loop(
            hashtags=hashtags,
            output_file=OUTPUT_FILE,
            max_hashtags=max_hashtags,
            max_posts_per_tag=max_posts_per_tag,
            timeout_total=timeout_total,
            use_tor=use_tor,
            max_iteraciones=max_iteraciones,
        )
        return eventos
    except Exception as e:
        print(f"⚠️ ImprovementLoop falló: {type(e).__name__}: {e}")
        print("   Fallback a versión legacy...")
        return scrape_instagram_events(config)


# Mantener compatibilidad: scrape_instagram_events usa v2 por defecto
def scrape_instagram_events(config: Optional[dict] = None) -> List[Dict]:
    """
    Wrapper que usa el nuevo Improvement Loop por defecto.
    Para forzar versión legacy, pasar config={'legacy': True}
    """
    if config and config.get("legacy", False):
        return _scrape_instagram_events_legacy(config)
    return scrape_instagram_events_v2(config)


# Renombrar la función original para acceso interno
def _scrape_instagram_events_legacy(config: Optional[dict] = None) -> List[Dict]:
    """Versión original (método A → B → C) mantenida para compatibilidad."""
    t0 = time.time()
    if config is None:
        config = _load_config()
    evento_out: List[Dict] = []

    print("📱 Scraping Instagram LEGACY (requests → playwright → fallback)...", flush=True)
    hashtags = _build_hashtags(config)
    print(f"   🔖 Hashtags objetivo: {len(hashtags)}", flush=True)
    intento_rapido = not _tiene_sesion_activa()
    max_fase_a = 4 if intento_rapido else len(hashtags)

    pendientes_b: List[str] = []
    for i in range(min(len(hashtags), max_fase_a)):
        if time.time() - t0 > TIMEOUT_TOTAL_SEG:
            print("   ⏳ Timeout total alcanzado; devolviendo lo obtenido.", flush=True)
            break
        if len(evento_out) >= 18:
            break
        h = _limpiar_hashtag(hashtags[i])
        if not h:
            continue
        metrica = {"intentos": 0, "posts": 0, "eventos": 0,
                   "errores": 0, "tiempo_total": 0.0, "ultima_vez": ""}
        t_ht = time.time()
        try:
            posts = _scrape_hashtag_requests(h, t0)
            if not posts:
                metrica["errores"] += 1
                pendientes_b.append(h)
                print(f"   ⚠️ #{h}: sin posts públicos (requests); fallback Playwright", flush=True)
            else:
                eventos_h = 0
                for p in posts[:MAX_POSTS_POR_HASHTAG]:
                    if time.time() - t0 > TIMEOUT_TOTAL_SEG:
                        break
                    evs = _eventos_del_post(p.get("url", ""), p.get("caption", ""),
                                            p.get("location", ""), h)
                    if evs:
                        evento_out.extend(evs)
                        eventos_h += len(evs)
                    if len(evento_out) >= 18:
                        break
                metrica["posts"] = len(posts)
                metrica["eventos"] = eventos_h
                metrica["intentos"] = 1
                if eventos_h:
                    print(f"   ✅ #{h}: {eventos_h} evento(s)", flush=True)
                else:
                    pendientes_b.append(h)
                    print(f"   ⚠️ #{h}: posts sin eventos; fallback Playwright", flush=True)
        except Exception as e:
            metrica["errores"] += 1
            pendientes_b.append(h)
            print(f"   ⚠️ #{h}: falló requests ({type(e).__name__}); continuo.", flush=True)
        metrica["tiempo_total"] = round(time.time() - t_ht, 2)
        metrica["ultima_vez"] = datetime.now(timezone.utc).isoformat()
        _actualizar_rendimiento(h, metrica)
        time.sleep(random.uniform(1.0, 2.0))

    if pendientes_b and len(evento_out) < 18 and time.time() - t0 < TIMEOUT_TOTAL_SEG:
        try:
            eventos_b = asyncio.run(_fase_playwright(pendientes_b, t0, hashtags))
            evento_out.extend(eventos_b)
        except Exception as e:
            print(f"   ⚠️ Fase Playwright capturada: {type(e).__name__}", flush=True)

    evento_out = _dedup_semantic(evento_out)
    evento_out = [_estandarizar(
        ev.get("nombre", ""), ev.get("fecha", "N/A"), ev.get("lugar", "N/A"),
        ev.get("ciudad", "N/A"), ev.get("pais", "N/A"),
        ev.get("organizador", "N/A"), ev.get("url", ""),
        ev.get("subgenero", ""), ev.get("descripcion", ""))
        for ev in evento_out]
    _guardar_productivos()
    if evento_out:
        _save_json(OUTPUT_FILE, evento_out)
    print(f"✅ {len(evento_out)} eventos de Instagram guardados en {OUTPUT_FILE}", flush=True)
    print(f"⏱ Tiempo total: {time.time()-t0:.0f}s", flush=True)
    return evento_out


if __name__ == "__main__":
    if "--setup-cookies" in sys.argv:
        print("ℹ️ Instagram público no requiere cookies (método A requests).")
        print("   Para sesión de cuenta secundaria con instaloader, usar")
        print("   'instagram_session.json' con session_id; no implementado aquí")
        print("   para mantener el scraping 100% anónimo y local.")
        sys.exit(0)
    evs = scrape_instagram_events()
    print(f"\nTotal eventos Instagram: {len(evs)}")
    for ev in evs[:10]:
        print(f"  - {ev['nombre'][:55]} | {ev.get('fecha','?')} | {ev.get('lugar','?')} | {ev.get('subgenero','')}")
