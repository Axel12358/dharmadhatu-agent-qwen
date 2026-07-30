import asyncio
import random
import re
import json
import logging
from playwright.async_api import async_playwright
from bs4 import BeautifulSoup

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ============================================================
# CARGA DE CONFIGURACIONES SEPARADAS
# ============================================================
def cargar_config_grupos():
    with open('config_grupos.json', 'r') as f:
        return json.load(f)

def cargar_config_eventos():
    with open('config_eventos.json', 'r') as f:
        return json.load(f)

# ============================================================
# 1. BÚSQUEDA DE GRUPOS (SOLO subgénero + país + ciudad)
#    SIN TIPOS DE EVENTO
# ============================================================
def generar_queries_grupos(config):
    queries = []
    subgeneros = config.get("subgeneros", [])
    paises = config.get("paises", [])
    ciudades = config.get("ciudades", [])

    for sub in subgeneros:
        for pais in paises:
            queries.append(f"{sub} {pais}")
        for ciudad in ciudades:
            queries.append(f"{sub} {ciudad}")
        for pais in paises:
            for ciudad in ciudades:
                queries.append(f"{sub} {pais} {ciudad}")

    random.shuffle(queries)
    return queries[:50]

async def buscar_grupos_facebook(query, page, timeout):
    """Busca grupos en Facebook y devuelve URLs."""
    grupos_urls = []
    try:
        url = f"https://www.facebook.com/search/groups/?q={query.replace(' ', '%20')}"
        await page.goto(url, timeout=timeout * 1000, wait_until='networkidle')
        await asyncio.sleep(random.uniform(2, 4))

        links = await page.query_selector_all('a[href*="/groups/"]')
        for link in links:
            href = await link.get_attribute('href')
            if href and '/groups/' in href and 'search' not in href:
                grupo_url = f"https://www.facebook.com{href}" if href.startswith('/') else href
                if grupo_url not in grupos_urls:
                    grupos_urls.append(grupo_url)
    except Exception as e:
        logger.error(f"Error en búsqueda de grupos: {e}")
    return grupos_urls

async def scrape_grupos():
    """Función principal para buscar grupos."""
    config = cargar_config_grupos()
    queries = generar_queries_grupos(config)
    timeout = 30
    semaphore = asyncio.Semaphore(5)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=['--no-sandbox'])
        context = await browser.new_context(
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            viewport={'width': 1280, 'height': 800}
        )

        async def trabajar(query):
            async with semaphore:
                page = await context.new_page()
                try:
                    return await buscar_grupos_facebook(query, page, timeout)
                finally:
                    await page.close()

        tareas = [trabajar(q) for q in queries]
        resultados = await asyncio.gather(*tareas, return_exceptions=True)
        await browser.close()

    grupos = []
    for res in resultados:
        if isinstance(res, list):
            grupos.extend(res)
    return list(set(grupos))

# ============================================================
# 2. BÚSQUEDA DE EVENTOS DIRECTOS (CON tipos de evento)
#    Usa subgénero + tipo + país + ciudad
# ============================================================
def generar_queries_eventos(config):
    queries = []
    subgeneros = config.get("subgeneros", [])
    paises = config.get("paises", [])
    ciudades = config.get("ciudades", [])
    tipos = config.get("tipos_evento", [])

    for sub in subgeneros:
        for tipo in tipos:
            queries.append(f"{sub} {tipo}")
            for pais in paises:
                queries.append(f"{sub} {tipo} {pais}")
            for ciudad in ciudades:
                queries.append(f"{sub} {tipo} {ciudad}")
            for pais in paises:
                for ciudad in ciudades:
                    queries.append(f"{sub} {tipo} {pais} {ciudad}")

    random.shuffle(queries)
    return queries[:50]

async def buscar_eventos_directos(query, page, timeout):
    eventos = []
    try:
        url = f"https://www.facebook.com/search/events/?q={query.replace(' ', '%20')}"
        await page.goto(url, timeout=timeout * 1000, wait_until='networkidle')
        await asyncio.sleep(random.uniform(2, 4))

        for _ in range(3):
            await page.evaluate('window.scrollBy(0, window.innerHeight)')
            await asyncio.sleep(random.uniform(1, 2))

        html = await page.content()
        soup = BeautifulSoup(html, 'html.parser')
        elementos = soup.select('div[role="article"]')
        if not elementos:
            elementos = soup.select('div[data-testid="event-card"]')

        for elem in elementos[:10]:
            texto = elem.get_text(separator=" ", strip=True)
            if not texto or len(texto) < 20:
                continue

            nombre = "Sin nombre"
            nombre_elem = elem.find('span', {'dir': 'auto'})
            if nombre_elem:
                nombre = nombre_elem.get_text(strip=True)

            organizador = "No disponible"
            org_elem = elem.find('span', {'data-testid': 'event-card-host'})
            if org_elem:
                organizador = org_elem.get_text(strip=True).replace('Hosted by', '').replace('Organizado por', '').strip()
            else:
                match = re.search(r'Hosted by\s*([^\n]+)', texto, re.IGNORECASE)
                if match:
                    organizador = match.group(1).strip()

            enlace = ""
            enlace_elem = elem.find('a', href=re.compile(r'/events/'))
            if enlace_elem:
                enlace = enlace_elem.get('href')
                if enlace and not enlace.startswith('http'):
                    enlace = f"https://www.facebook.com{enlace}"

            if nombre and nombre != "Sin nombre":
                eventos.append({
                    "nombre": nombre.strip(),
                    "fecha": "Fecha no disponible",
                    "lugar": "Lugar no disponible",
                    "organizador": organizador,
                    "enlace": enlace,
                    "fuente": "Facebook (directo)"
                })
    except Exception as e:
        logger.error(f"Error en búsqueda directa: {e}")
    return eventos

async def scrape_eventos_directos():
    config = cargar_config_eventos()
    queries = generar_queries_eventos(config)
    timeout = 30
    semaphore = asyncio.Semaphore(5)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=['--no-sandbox'])
        context = await browser.new_context(
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            viewport={'width': 1280, 'height': 800}
        )

        async def trabajar(query):
            async with semaphore:
                page = await context.new_page()
                try:
                    return await buscar_eventos_directos(query, page, timeout)
                finally:
                    await page.close()

        tareas = [trabajar(q) for q in queries]
        resultados = await asyncio.gather(*tareas, return_exceptions=True)
        await browser.close()

    eventos = []
    for res in resultados:
        if isinstance(res, list):
            eventos.extend(res)
    return eventos

# ============================================================
# 3. EXTRAER EVENTOS DESDE GRUPOS (CON FILTRO DE TIPOS)
# ============================================================
TIPOS_LOCACION = [
    "festival", "party", "gathering", "rave",
    "outdoor", "indoor", "camp", "retreat",
    "open air", "warehouse", "club", "ritual"
]

async def extraer_eventos_de_grupos(grupos):
    eventos = []
    if not grupos:
        return eventos

    timeout = 30
    semaphore = asyncio.Semaphore(5)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=['--no-sandbox'])
        context = await browser.new_context(
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            viewport={'width': 1280, 'height': 800}
        )

        async def extraer(grupo_url):
            async with semaphore:
                page = await context.new_page()
                try:
                    await page.goto(grupo_url, timeout=timeout * 1000, wait_until='networkidle')
                    await asyncio.sleep(random.uniform(2, 4))
                    try:
                        await page.click('span[dir="auto"]:has-text("Eventos")')
                        await asyncio.sleep(2)
                    except:
                        pass

                    for _ in range(3):
                        await page.evaluate('window.scrollBy(0, window.innerHeight)')
                        await asyncio.sleep(random.uniform(1, 2))

                    html = await page.content()
                    soup = BeautifulSoup(html, 'html.parser')
                    elementos = soup.select('div[role="article"]')
                    if not elementos:
                        elementos = soup.select('div[data-testid="event-card"]')

                    evs = []
                    for elem in elementos[:10]:
                        texto = elem.get_text(separator=" ", strip=True)
                        if not texto or len(texto) < 20:
                            continue
                        # FILTRO: solo si contiene tipo de evento
                        if not any(t in texto.lower() for t in TIPOS_LOCACION):
                            continue

                        nombre = "Sin nombre"
                        nombre_elem = elem.find('span', {'dir': 'auto'})
                        if nombre_elem:
                            nombre = nombre_elem.get_text(strip=True)

                        organizador = "No disponible"
                        org_elem = elem.find('span', {'data-testid': 'event-card-host'})
                        if org_elem:
                            organizador = org_elem.get_text(strip=True).replace('Hosted by', '').replace('Organizado por', '').strip()
                        else:
                            match = re.search(r'Hosted by\s*([^\n]+)', texto, re.IGNORECASE)
                            if match:
                                organizador = match.group(1).strip()

                        enlace = ""
                        enlace_elem = elem.find('a', href=re.compile(r'/events/'))
                        if enlace_elem:
                            enlace = enlace_elem.get('href')
                            if enlace and not enlace.startswith('http'):
                                enlace = f"https://www.facebook.com{enlace}"

                        if nombre and nombre != "Sin nombre":
                            evs.append({
                                "nombre": nombre.strip(),
                                "fecha": "Fecha no disponible",
                                "lugar": "Lugar no disponible",
                                "organizador": organizador,
                                "enlace": enlace,
                                "fuente": "Facebook (grupo)"
                            })
                    return evs
                finally:
                    await page.close()

        tareas = [extraer(g) for g in grupos[:15]]
        resultados = await asyncio.gather(*tareas, return_exceptions=True)
        await browser.close()

    for res in resultados:
        if isinstance(res, list):
            eventos.extend(res)
    return eventos

# ============================================================
# 4. FUNCIÓN PRINCIPAL (CONSOLIDA AMBAS FUENTES)
# ============================================================
async def scrape_facebook_public(config):
    """
    Orquesta ambas lógicas:
    - Busca grupos (SIN tipos de evento) y extrae eventos filtrando por tipos.
    - Busca eventos directos (CON tipos de evento).
    - Consolida ambas fuentes.
    """
    logger.info("🔍 Iniciando scraping de Facebook (grupos + eventos directos)")

    # 1. Buscar grupos
    logger.info("📂 Buscando GRUPOS (sin tipos de evento)...")
    grupos = await scrape_grupos()
    logger.info(f"✅ {len(grupos)} grupos encontrados")

    # 2. Extraer eventos de grupos (filtrando por tipos)
    logger.info("📂 Extrayendo eventos de grupos (filtrando por tipos)...")
    eventos_grupos = await extraer_eventos_de_grupos(grupos)
    logger.info(f"✅ {len(eventos_grupos)} eventos extraídos de grupos")

    # 3. Buscar eventos directos (con tipos de evento)
    logger.info("📂 Buscando eventos DIRECTOS (con tipos de evento)...")
    eventos_directos = await scrape_eventos_directos()
    logger.info(f"✅ {len(eventos_directos)} eventos directos encontrados")

    # 4. Consolidar
    todos = eventos_grupos + eventos_directos
    unicos = []
    seen = set()
    for e in todos:
        key = (e["nombre"], e["organizador"])
        if key not in seen and e["nombre"] != "Sin nombre":
            seen.add(key)
            unicos.append(e)

    logger.info(f"✅ Facebook TOTAL: {len(unicos)} eventos (grupos + directos)")
    return unicos
