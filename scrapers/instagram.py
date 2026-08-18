#!/usr/bin/env python3
"""
Extractor de eventos de Instagram (psYTrance) — FASE 1 + FASE 2 + LOOP DE MEJORA
- Fase 1: requests al HTML público del hashtag + extracción JSON (ld+json / window.__INITIAL_STATE__)
- Fase 2: DuckDuckGo `site:instagram.com "#<hashtag>"` + Playwright stealth (mismo patrón que Facebook)
- Pipeline tolerante a fallos: try/except por hashtag, nunca se detiene
- Loop de mejora: hashtags productivos, estado rotativo, métricas por run
- AntiBlock integrado (rate limiting, user-agent rotativo, Tor opcional)
- Output: eventos_instagram.json + archivos de estado
"""

import asyncio
import json
import random
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Dict, Optional, Any

import requests
from bs4 import BeautifulSoup

_PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

try:
    from scrapers.event_extractor import EventExtractor
except Exception:
    EventExtractor = None

try:
    from scrapers.anti_block import get_anti_block
except Exception:
    def get_anti_block():
        return None

# Loop Central de Optimización: deduplicador global y recursos compartidos
# (opcional). Se usan si están disponibles; el scraper funciona igual sin ellos.
try:
    from core.deduplicador import Deduplicador
except Exception:
    Deduplicador = None

try:
    from core import recursos as _recursos
except Exception:
    _recursos = None

# ------------------------------------------------------------------ #
# CONFIGURACIÓN Y ARCHIVOS DE ESTADO
# ------------------------------------------------------------------ #
CONFIG_FILE = str(Path(_PROJECT_ROOT) / "config_grupos.json")
OUTPUT_FILE = str(Path(_PROJECT_ROOT) / "eventos_instagram.json")

ESTADO_FILE = str(Path(_PROJECT_ROOT) / "instagram_hashtags_estado.json")
PRODUCTIVOS_FILE = str(Path(_PROJECT_ROOT) / "instagram_hashtags_productivos.json")
RENDIMIENTO_FILE = str(Path(_PROJECT_ROOT) / "instagram_rendimiento.json")

# Timeouts y límites
TIMEOUT_REQUESTS = 10
TIMEOUT_PLAYWRIGHT = 15
TIMEOUT_GLOBAL = 180  # 3 minutos
MAX_POSTS_POR_HASHTAG = 12
MAX_HASHTAGS_POR_RUN = 20

# ------------------------------------------------------------------ #
# UTILIDADES DE PERSISTENCIA
# ------------------------------------------------------------------ #
def _load_json(path: str) -> Any:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError, FileNotFoundError):
        return None

def _save_json(path: str, data: Any) -> None:
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except IOError:
        pass

def _limpiar_hashtag(raw: str) -> str:
    """Limpia hashtag: sin @/#/espacios/puntuación, minúsculas alfanumérico."""
    h = (raw or "").lower()
    h = re.sub(r"[^a-z0-9]+", "", h)
    return h

# ------------------------------------------------------------------ #
# GENERACIÓN Y ROTACIÓN DE HASHTAGS
# ------------------------------------------------------------------ #
def _cargar_config() -> Dict:
    if not Path(CONFIG_FILE).exists():
        return {"subgeneros": ["psytrance"], "paises": []}
    try:
        with open(CONFIG_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return {"subgeneros": ["psytrance"], "paises": []}

def _generar_hashtags_base(config: Dict) -> List[str]:
    """Genera hashtags combinando subgéneros + país/ciudad (ej: darkpsyberlin, forestpsytrance)."""
    subs = config.get("subgeneros", ["psytrance"])
    paises = config.get("paises", [])

    hashtags = []
    for sub in subs:
        base = _limpiar_hashtag(sub)
        hashtags.append(f"{base}psytrance")
        hashtags.append(f"{base}party")
        hashtags.append(f"{base}festival")

    for p in paises:
        nombre_pais = _limpiar_hashtag(p.get("nombre", ""))
        for c in p.get("ciudades", [])[:3]:
            ciudad = _limpiar_hashtag(c)
            hashtags.append(f"psytrance{ciudad}")
            hashtags.append(f"{nombre_pais}{base}")
            hashtags.append(f"{ciudad}{base}")

    # Dedup conservando orden
    vistos = set()
    unicos = []
    for h in hashtags:
        if h and h not in vistos:
            vistos.add(h)
            unicos.append(h)
    return unicos

def _rotar_estado(key: str, total: int) -> int:
    """Offset rotativo persistido en JSON para variar hashtags cada run."""
    if total <= 0:
        return 0
    estado = _load_json(ESTADO_FILE) or {}
    off = (estado.get(key, 0) + 1) % total
    estado[key] = off
    _save_json(ESTADO_FILE, estado)
    return off

def _cargar_productivos() -> List[str]:
    data = _load_json(PRODUCTIVOS_FILE)
    if isinstance(data, list):
        return [_limpiar_hashtag(h) for h in data if h]
    return []

def _guardar_productivos(productivos: List[str]) -> None:
    _save_json(PRODUCTIVOS_FILE, productivos)

def _actualizar_rendimiento(hashtag: str, metricas: Dict) -> None:
    perf = _load_json(RENDIMIENTO_FILE) or {}
    perf[hashtag] = metricas
    _save_json(RENDIMIENTO_FILE, perf)

def _seleccionar_hashtags() -> List[str]:
    """Selecciona hashtags para este run: productivos primero + rotación."""
    config = _cargar_config()
    base = _generar_hashtags_base(config)
    productivos = _cargar_productivos()

    # Prioridad absoluta a productivos
    prioritarios = [h for h in base if h in productivos]

    # Resto rotando con estado
    restantes = [h for h in base if h not in productivos]
    off_sub = _rotar_estado("offset_sub", len(subs := [s for s in config.get("subgeneros", []) if _limpiar_hashtag(s) in restantes])) if any(_limpiar_hashtag(s) in restantes for s in config.get("subgeneros", [])) else 0
    off_ciu = _rotar_estado("offset_ciudad", len(ciudades := [c for p in config.get("paises", []) for c in p.get("ciudades", []) if f"psytrance{_limpiar_hashtag(c)}" in restantes])) if any(f"psytrance{_limpiar_hashtag(c)}" in restantes for p in config.get("paises", []) for c in p.get("ciudades", [])) else 0

    # Rotar subgéneros y ciudades
    subs = [_limpiar_hashtag(s) for s in config.get("subgeneros", [])]
    ciudades_flat = [_limpiar_hashtag(c) for p in config.get("paises", []) for c in p.get("ciudades", [])]
    random.shuffle(subs)
    random.shuffle(ciudades_flat)

    out = prioritarios[:]
    out.extend([f"{s}psytrance" for s in subs[off_sub:off_sub+8] if f"{s}psytrance" in restantes])
    out.extend([f"{c}psytrance" for c in ciudades_flat[off_ciu:off_ciu+6] if f"psytrance{c}" in restantes])

    # Dedup final, max por run
    final = []
    vistos = set()
    for h in out:
        if h not in vistos:
            vistos.add(h)
            final.append(h)
    return final[:MAX_HASHTAGS_POR_RUN]

# ------------------------------------------------------------------ #
# FASE 1: EXTRACCIÓN JSON DEL HTML PÚBLICO (requests rápido)
# ------------------------------------------------------------------ #
def _extraer_json_instagram(html: str) -> Optional[Dict]:
    """Extrae JSON de Instagram: ld+json, window.__INITIAL_STATE__, window._sharedData."""
    soup = BeautifulSoup(html, "html.parser")

    # 1) script type="application/ld+json"
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string)
            if isinstance(data, dict) and ("@graph" in data or "itemListElement" in data):
                return data
        except (json.JSONDecodeError, TypeError):
            continue

    # 2) window.__INITIAL_STATE__ o window._sharedData
    for script in soup.find_all("script"):
        if not script.string:
            continue
        content = script.string.strip()
        for var in ["window.__INITIAL_STATE__", "window._sharedData"]:
            if var in content:
                try:
                    start = content.index("{", content.index(var))
                    brace_count = 0
                    end = start
                    for i, ch in enumerate(content[start:], start):
                        if ch == "{":
                            brace_count += 1
                        elif ch == "}":
                            brace_count -= 1
                            if brace_count == 0:
                                end = i + 1
                                break
                    json_str = content[start:end]
                    return json.loads(json_str)
                except (ValueError, json.JSONDecodeError):
                    continue
    return None

def _parsear_posts_desde_json(json_data: Dict) -> List[Dict]:
    """Convierte JSON de Instagram a lista de posts con caption, url, location.

    Cubre los esquemas públicos 2024-2026:
    - graphql.hashtag.edge_hashtag_to_media.edges[].node  (página de tag pública)
    - graph.hashtag.edge_hashtag_to_top_posts.edges[].node
    - Recursivo sobre __typename GraphImage/GraphSidecar/GraphVideo.
    """
    posts = []
    vistos_shortcode = set()

    def _agregar(shortcode, caption, location):
        if not shortcode or shortcode in vistos_shortcode:
            return
        vistos_shortcode.add(shortcode)
        posts.append({
            "shortcode": shortcode,
            "url": f"https://www.instagram.com/p/{shortcode}/",
            "caption": caption,
            "location": location,
        })

    # 1) Buqueda dirigida: cualquier diccionario con edge_hashtag_to_top_posts/
    #    edge_hashtag_to_media (vive bajo graphql.hashtag o graph.hashtag).
    def buscar_directo(obj):
        if isinstance(obj, dict):
            for clave in ("edge_hashtag_to_top_posts", "edge_hashtag_to_media",
                          "edge_hashtag_to_recent_media"):
                lista = obj.get(clave)
                if isinstance(lista, dict) and isinstance(lista.get("edges"), list):
                    for edge in lista["edges"]:
                        node = edge.get("node") or {}
                        sc = node.get("shortcode")
                        cap = ""
                        caption_edges = node.get("edge_media_to_caption", {}).get("edges", [])
                        if caption_edges:
                            cap = caption_edges[0].get("node", {}).get("text", "") or ""
                        loc = ""
                        if node.get("location"):
                            loc = node["location"].get("name", "") or ""
                        _agregar(sc, cap, loc)
            for v in obj.values():
                buscar_directo(v)
        elif isinstance(obj, list):
            for item in obj:
                buscar_directo(item)

    buscar_directo(json_data)

    # 2) Cuadro recursivo genérico (cubre GraphImage/GraphVideo/GraphSidecar).
    def recurse(obj):
        if isinstance(obj, dict):
            if obj.get("__typename") in ("GraphImage", "GraphSidecar", "GraphVideo"):
                shortcode = obj.get("shortcode")
                caption_edges = obj.get("edge_media_to_caption", {}).get("edges", [])
                caption = caption_edges[0]["node"]["text"] if caption_edges else ""
                location = obj.get("location", {}).get("name", "") if obj.get("location") else ""
                _agregar(shortcode, caption, location)
            for v in obj.values():
                recurse(v)
        elif isinstance(obj, list):
            for item in obj:
                recurse(item)

    recurse(json_data)
    return posts

def _fase1_requests(hashtag: str, t0_global: float) -> List[Dict]:
    """Fase 1: requests al HTML del hashtag + extracción JSON."""
    if time.time() - t0_global > TIMEOUT_GLOBAL:
        return []

    url = f"https://www.instagram.com/explore/tags/{hashtag}/"
    ab = get_anti_block()

    try:
        if ab:
            ab.wait_if_needed("instagram.com")

        # User-Agent desde recursos compartidos (con fallback a anti_block/aleatorio)
        ua = None
        if _recursos is not None:
            try:
                ua = _recursos.obtener_user_agent()
            except Exception:
                ua = None
        if not ua:
            ua = ab.random_user_agent() if ab else random.choice([
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
            ])

        headers = {
            "User-Agent": ua,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
            "Accept-Encoding": "identity",
        }

        # Proxy desde recursos compartidos (con fallback a anti_block)
        proxies = None
        if _recursos is not None:
            try:
                proxies = _recursos.obtener_proxies_dict()
            except Exception:
                proxies = None
        if proxies is None:
            proxies = ab.requests_proxies() if ab else None
        r = requests.get(url, headers=headers, timeout=TIMEOUT_REQUESTS, proxies=proxies)

        if r.status_code != 200 or len(r.text) < 5000:
            return []

        json_data = _extraer_json_instagram(r.text)
        if not json_data:
            return []

        posts = _parsear_posts_desde_json(json_data)
        return posts[:MAX_POSTS_POR_HASHTAG]

    except Exception:
        return []

# ------------------------------------------------------------------ #
# FASE 2: DUCKDUCKGO + PLAYWRIGHT (patrón Facebook)
# ------------------------------------------------------------------ #
async def _fase2_playwright(hashtag: str, t0_global: float) -> List[Dict]:
    """Fase 2: búsqueda DuckDuckGo `site:instagram.com "#hashtag"` + extracción posts."""
    if time.time() - t0_global > TIMEOUT_GLOBAL:
        return []

    ab = get_anti_block()
    query = f'site:instagram.com "#{hashtag}" psytrance party event'
    url = f"https://duckduckgo.com/html/?q={requests.utils.quote(query)}"

    try:
        from playwright.async_api import async_playwright
    except ImportError:
        return []

    posts = []
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-blink-features=AutomationControlled"],
            )
            context = await ab.create_stealth_context(browser, use_tor=True) if ab else await browser.new_context()

            try:
                from playwright.async_api import async_playwright as _apw
            except Exception:
                pass
            # User-Agent realista desde recursos compartidos (si el context lo permite)
            if _recursos is not None and context is not None and not getattr(context, "_locked", False):
                try:
                    await context.add_init_script(
                        f"Object.defineProperty(navigator,'userAgent',{{get:()=> '{_recursos.obtener_user_agent()}'}});"
                    )
                except Exception:
                    pass

            page = await context.new_page()
            await page.goto(url, timeout=TIMEOUT_PLAYWRIGHT * 1000, wait_until="domcontentloaded")
            await asyncio.sleep(random.uniform(1.5, 2.5))

            html = await page.content()
            soup = BeautifulSoup(html, "html.parser")

            # Resultados de DuckDuckGo: .result__snippet, .result__url, a.result__snippet
            for res in soup.select(".result, .web-result, .result__snippet"):
                a = res.find("a", href=True)
                if not a:
                    continue
                href = a.get("href", "")
                if "instagram.com/p/" not in href and "instagram.com/reel/" not in href:
                    continue
                snippet = res.get_text(" ", strip=True)[:500]
                posts.append({
                    "url": href.split("?")[0].rstrip("/"),
                    "caption": snippet,
                    "location": "",
                })
                if len(posts) >= MAX_POSTS_POR_HASHTAG:
                    break

            await browser.close()
    except Exception:
        pass
    return posts[:MAX_POSTS_POR_HASHTAG]

# ------------------------------------------------------------------ #
# EXTRACCIÓN DE EVENTOS CON EVENTEXTRACTOR
# ------------------------------------------------------------------ #
def _extraer_eventos_post(post: Dict, hashtag: str, extractor: EventExtractor) -> List[Dict]:
    """Extrae eventos del caption + location usando EventExtractor."""
    caption = post.get("caption", "") or ""
    location = post.get("location", "") or ""
    url = post.get("url", "") or post.get("shortcode", "")

    if not url.startswith("http"):
        url = f"https://www.instagram.com/p/{post.get('shortcode', '')}/"

    # Texto combinado para el extractor
    texto = f"{caption}\nLocation: {location}" if location else caption
    if not texto or len(texto.strip()) < 20:
        return []

    eventos = extractor.extract_all(texto, f"Instagram #{hashtag}", url)
    if not eventos:
        return []

    # Enriquecer con subgénero inferido del hashtag
    for ev in eventos:
        ev["fuente"] = "Instagram"
        ev["subgenero"] = _inferir_subgenero(hashtag, caption)
        ev["tipo_lugar"] = ev.get("tipo_lugar", "N/A")
        # Asegurar campos obligatorios
        for campo in ["nombre", "fecha", "lugar", "ciudad", "pais", "organizador", "email", "url", "descripcion"]:
            ev.setdefault(campo, "N/A")
    return eventos

def _inferir_subgenero(hashtag: str, caption: str) -> str:
    """Infiere subgénero a partir del hashtag y caption."""
    h = hashtag.lower()
    text = (caption or "").lower()
    for sub in ["darkpsy", "forest", "hitech", "progressive", "fullon", "goa", "suomisaundi", "zenon", "psycore", "psybient", "twilight", "psychill"]:
        if sub in h or sub in text:
            return sub
    if "psytrance" in h or "psytrance" in text:
        return "psytrance"
    return ""

# ------------------------------------------------------------------ #
# PIPELINE PRINCIPAL
# ------------------------------------------------------------------ #
def scrape_instagram_events(config: Optional[Dict] = None,
                            timeout: Optional[float] = None,
                            deduplicador=None) -> List[Dict]:
    """
    Entry point principal.
    Params (opcionales, usados por core/orquestador; main.py no cambia):
      - timeout: override del TIMEOUT_GLOBAL (segundos).
      - deduplicador: instancia de core.deduplicador.Deduplicador para evitar
        que este scraper devuelva eventos ya registrados en el run actual.
    Returns: lista de eventos en formato estándar del proyecto.
    """
    global TIMEOUT_GLOBAL
    if timeout:
        TIMEOUT_GLOBAL = float(timeout)
    t0 = time.time()
    if config is None:
        config = {}

    extractor = EventExtractor() if EventExtractor else None
    if not extractor:
        print("⚠️ EventExtractor no disponible")
        return []

    hashtags = _seleccionar_hashtags()
    print(f"📱 Scraping Instagram: {len(hashtags)} hashtags (Fase 1: requests, Fase 2: DuckDuckGo+Playwright)")

    todos_eventos = []
    productivos_esta_vez = []

    for i, h in enumerate(hashtags, 1):
        if time.time() - t0 > TIMEOUT_GLOBAL:
            print(f"⏱ Timeout global ({TIMEOUT_GLOBAL}s) alcanzado.")
            break

        t_h = time.time()
        metricas = {"hashtag": h, "eventos": 0, "tiempo_fase1": 0, "tiempo_fase2": 0, "errores": 0}
        posts = []

        # ---- FASE 1: requests JSON ----
        try:
            posts_f1 = _fase1_requests(h, t0)
            metricas["tiempo_fase1"] = round(time.time() - t_h, 2)
            if posts_f1:
                posts = posts_f1
                print(f"  ✅ #{h}: Fase 1 → {len(posts)} posts")
        except Exception as e:
            metricas["errores"] += 1
            metricas["tiempo_fase1"] = round(time.time() - t_h, 2)
            print(f"  ⚠️ #{h}: Fase 1 falló ({type(e).__name__})")

        # ---- FASE 2: Playwright fallback si Fase 1 vacía ----
        if not posts:
            t_f2 = time.time()
            try:
                posts_f2 = asyncio.run(_fase2_playwright(h, t0))
                metricas["tiempo_fase2"] = round(time.time() - t_f2, 2)
                if posts_f2:
                    posts = posts_f2
                    print(f"  ✅ #{h}: Fase 2 → {len(posts)} posts")
            except Exception as e:
                metricas["errores"] += 1
                metricas["tiempo_fase2"] = round(time.time() - t_f2, 2)
                print(f"  ⚠️ #{h}: Fase 2 falló ({type(e).__name__})")

        # ---- Extraer eventos de los posts ----
        eventos_h = []
        for post in posts:
            try:
                evs = _extraer_eventos_post(post, h, extractor)
                eventos_h.extend(evs)
            except Exception:
                metricas["errores"] += 1

        metricas["eventos"] = len(eventos_h)
        metricas["tiempo_total"] = round(time.time() - t_h, 2)
        metricas["ultima_vez"] = datetime.now(timezone.utc).isoformat()

        if eventos_h:
            productivos_esta_vez.append(h)
            print(f"  🎯 #{h}: {len(eventos_h)} evento(s) en {metricas['tiempo_total']}s")
        else:
            print(f"  ⏭ #{h}: sin eventos en {metricas['tiempo_total']}s")

        todos_eventos.extend(eventos_h)
        _actualizar_rendimiento(h, metricas)

        # Pausa anti-bloqueo entre hashtags
        if ab := get_anti_block():
            ab.wait_if_needed("instagram.com")
        else:
            time.sleep(random.uniform(1.5, 3.0))

    # ---- Post-proceso ----
    # Dedup por nombre+fecha+ciudad
    vistos = set()
    unicos = []
    for ev in todos_eventos:
        k = (ev.get("nombre", "").strip().lower(), ev.get("fecha", "").strip(), ev.get("ciudad", "").strip().lower())
        if k not in vistos:
            vistos.add(k)
            unicos.append(ev)

    # Guardar productivos
    if productivos_esta_vez:
        actuales = _cargar_productivos()
        for h in productivos_esta_vez:
            if h not in actuales:
                actuales.append(h)
        _guardar_productivos(actuales)

    # Guardar output
    if unicos:
        _save_json(OUTPUT_FILE, unicos)
        print(f"✅ {len(unicos)} eventos guardados en {OUTPUT_FILE}")

    # Dedup global (Loop Central de Optimización): filtrar los que el
    # deduplicador ya registró en este run. Solo se devuelven los nuevos.
    if deduplicador is not None:
        antes = len(unicos)
        nuevos = []
        for ev in unicos:
            if deduplicador.filtrar_nuevos([ev]):
                nuevos.append(ev)
        unicos = nuevos
        print(f"🎯 Dedup global: {antes} → {len(unicos)} nuevos para el orquestador")
        try:
            deduplicador.guardar()
        except Exception:
            pass

    print("⚠️ No se encontraron eventos en este run" if not unicos else "")

    total_t = round(time.time() - t0, 1)
    print(f"⏱ Tiempo total: {total_t}s | Hashtags: {len(hashtags)} | Eventos: {len(unicos)}")
    return unicos

# ------------------------------------------------------------------ #
# TEST DIRECTO
# ------------------------------------------------------------------ #
if __name__ == "__main__":
    import time
    start = time.time()
    evs = scrape_instagram_events()
    print(f"\nTiempo: {time.time()-start:.0f}s, Eventos: {len(evs)}")
    for e in evs[:5]:
        print(f'  {e["nombre"][:50]} | {e.get("fecha","?")} | {e.get("lugar","?")} | {e.get("subgenero","")}')