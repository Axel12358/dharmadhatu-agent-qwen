import asyncio
import time
import random
import json
import os
from playwright.async_api import async_playwright

# ============================================================
# SCRAPER DE GOABASE (asíncrono)
# ============================================================
async def scrape_goabase_async(limit=100):
    eventos = []
    url = "https://www.goabase.net/party/"
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            await page.goto(url, timeout=30000)
            # Esperar a que cargue algún contenido (selector alternativo)
            try:
                await page.wait_for_selector('div.party-item', timeout=5000)
            except:
                # Si no encuentra .party-item, intenta con otro selector
                await page.wait_for_selector('div[class*="party"]', timeout=5000)
            items = await page.query_selector_all('div.party-item, div[class*="party"]')
            if not items:
                # Intentar obtener el texto de la página
                texto = await page.inner_text('body')
                # Extraer eventos manualmente (simple fallback)
                lineas = texto.split('\n')
                for linea in lineas[:limit*2]:
                    if 'party' in linea.lower() or 'festival' in linea.lower() or 'trance' in linea.lower():
                        if len(linea) > 10:
                            eventos.append({
                                'nombre': linea.strip()[:100],
                                'fecha': 'N/A',
                                'lugar': 'N/A',
                                'pais': 'N/A',
                                'fuente': 'Goabase',
                                'organizador': 'N/A',
                                'email': 'N/A',
                                'raw_text': ''
                            })
            else:
                for item in items[:limit]:
                    try:
                        nombre = await item.text_content()
                        eventos.append({
                            'nombre': nombre.strip() if nombre else "Sin nombre",
                            'fecha': 'N/A',
                            'lugar': 'N/A',
                            'pais': 'N/A',
                            'fuente': 'Goabase',
                            'organizador': 'N/A',
                            'email': 'N/A',
                            'raw_text': ''
                        })
                    except:
                        continue
            await browser.close()
            print(f"✅ Goabase async: {len(eventos)} eventos")
    except Exception as e:
        print(f"❌ Goabase async error: {e}")
    return eventos

# ============================================================
# SCRAPER DE FACEBOOK MEJORADO (asíncrono, múltiples queries)
# ============================================================
async def scrape_facebook_async(query="psytrance festival", max_posts=50):
    """
    Scraper de Facebook mejorado:
    - Prueba múltiples queries para cubrir más eventos.
    - Hasta 20 scrolls por query.
    - Pausas humanas (3-5 segundos).
    - headless=False para evitar detección.
    """
    eventos = []
    # Lista de queries para cubrir más eventos
    queries = [
        query,
        "goa trance",
        "psy party",
        "psychedelic trance",
        "festival psytrance",
        "darkpsy",
        "forest psy",
        "hitech trance",
        "zenonesque",
        "progressive trance"
    ]
    for q in queries:
        if len(eventos) >= max_posts:
            break
        url = f"https://www.facebook.com/events/search/?q={q.replace(' ', '%20')}"
        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=False)
                context = await browser.new_context(
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                    viewport={'width': 1280, 'height': 720}
                )
                page = await context.new_page()
                await page.goto(url, timeout=60000)
                await asyncio.sleep(5)

                # Cerrar modal de login si aparece
                try:
                    await page.click('div[aria-label="Close"]', timeout=3000)
                    print(f"✅ Modal cerrado para '{q}'")
                except:
                    pass

                eventos_encontrados = 0
                scrolls = 0
                max_scrolls = 20  # Más scrolls

                while eventos_encontrados < max_posts and scrolls < max_scrolls:
                    await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                    await asyncio.sleep(random.uniform(3, 5))
                    scrolls += 1

                    # Selectores flexibles
                    items = await page.query_selector_all('div[role="article"]')
                    if not items:
                        items = await page.query_selector_all('div[class*="event"]')
                    if not items:
                        items = await page.query_selector_all('a[href*="/events/"]')

                    for item in items:
                        try:
                            texto = await item.inner_text()
                            if not texto or len(texto) < 20:
                                continue
                            lineas = texto.split('\n')
                            titulo = lineas[0] if lineas else "Sin título"
                            fecha = "N/A"
                            lugar = "N/A"
                            for linea in lineas[1:10]:
                                # Detectar fecha (meses en español/inglés)
                                if any(mes in linea.lower() for mes in [
                                    'enero', 'febrero', 'marzo', 'abril', 'mayo', 'junio',
                                    'julio', 'agosto', 'septiembre', 'octubre', 'noviembre', 'diciembre',
                                    'january', 'february', 'march', 'april', 'may', 'june',
                                    'july', 'august', 'september', 'october', 'november', 'december'
                                ]):
                                    fecha = linea.strip()
                                # Detectar lugar
                                if any(palabra in linea.lower() for palabra in [
                                    'calle', 'avenida', 'plaza', 'centro', 'park', 'street',
                                    'avenue', 'plaza', 'square', 'road'
                                ]):
                                    lugar = linea.strip()
                            if len(titulo) > 5:
                                eventos.append({
                                    'nombre': titulo[:100],
                                    'fecha': fecha[:50],
                                    'lugar': lugar[:50],
                                    'pais': 'N/A',
                                    'fuente': 'Facebook',
                                    'organizador': 'N/A',
                                    'email': 'N/A',
                                    'raw_text': texto[:500]
                                })
                                eventos_encontrados += 1
                            if eventos_encontrados >= max_posts:
                                break
                        except:
                            continue
                await browser.close()
                print(f"✅ Facebook '{q}': {eventos_encontrados} eventos")
        except Exception as e:
            print(f"❌ Facebook error con '{q}': {e}")
    return eventos

# ============================================================
# CACHING (opcional, pero lo dejamos)
# ============================================================
CACHE_FILE = "data/scraper_cache.json"

def cargar_cache():
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, 'r') as f:
                return json.load(f)
        except:
            return {}
    return {}

def guardar_cache(cache):
    os.makedirs("data", exist_ok=True)
    with open(CACHE_FILE, 'w') as f:
        json.dump(cache, f, indent=2)

# ============================================================
# ORQUESTADOR ASÍNCRONO
# ============================================================
async def ejecutar_scrapers_async(config):
    print("🚀 Ejecutando scrapers en paralelo (asíncrono)...")
    inicio = time.time()

    # Cargar caché (por ahora no lo usamos, pero está aquí)
    # cache = cargar_cache()

    # Ejecutar scrapers en paralelo
    tareas = [
        scrape_goabase_async(config.get('max_eventos', 100)),
        scrape_facebook_async("psytrance festival", config.get('busquedas_facebook', 50))
    ]

    resultados = await asyncio.gather(*tareas, return_exceptions=True)

    # Consolidar
    eventos = []
    for res in resultados:
        if isinstance(res, list):
            eventos.extend(res)
        elif isinstance(res, Exception):
            print(f"⚠️ Error en scraper: {res}")

    # Guardar caché (opcional)
    # if eventos:
    #     cache[f"scrape_{int(time.time())}"] = eventos
    #     guardar_cache(cache)

    fin = time.time()
    print(f"✅ Scrapers completados en {fin - inicio:.2f}s")
    return eventos
