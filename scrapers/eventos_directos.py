import asyncio
import random
import logging
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError
from bs4 import BeautifulSoup

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def generar_queries_eventos(config):
    queries = []
    for sub in config.get("subgeneros", []):
        queries.append(f"{sub}")
        for pais in config.get("paises", []):
            queries.append(f"{sub} {pais}")
        for ciudad in config.get("ciudades", []):
            queries.append(f"{sub} {ciudad}")
    random.shuffle(queries)
    queries = queries[:20]
    logger.info(f"🔍 Queries generadas para eventos directos: {queries}")
    return queries

async def buscar_eventos_directos_facebook(query, page, timeout):
    eventos = []
    try:
        url = f"https://www.facebook.com/search/events/?q={query.replace(' ', '%20')}"
        logger.info(f"🌐 Navegando a: {url}")
        await page.goto(url, timeout=timeout * 1000, wait_until='networkidle')
        await asyncio.sleep(random.uniform(3, 5))
        await page.screenshot(path=f"debug_eventos_{query[:10]}.png")
        for _ in range(3):
            await page.evaluate('window.scrollBy(0, window.innerHeight)')
            await asyncio.sleep(random.uniform(1, 2))
        html = await page.content()
        soup = BeautifulSoup(html, 'html.parser')
        elementos = soup.select('div[role="article"]')
        if not elementos:
            elementos = soup.select('div[data-testid="event-card"]')
        if not elementos:
            logger.warning(f"⚠️ No se encontraron elementos de eventos para query '{query}'")
            return eventos
        logger.info(f"🔎 Elementos encontrados: {len(elementos)}")
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
            enlace = ""
            enlace_elem = elem.find('a', href=True)
            if enlace_elem and '/events/' in enlace_elem.get('href', ''):
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
                logger.info(f"✅ Evento directo extraído: {nombre[:40]}...")
    except PlaywrightTimeoutError:
        logger.error(f"⏱️ Timeout al cargar {url}")
    except Exception as e:
        logger.error(f"❌ Error en búsqueda directa: {e}")
    return eventos

async def buscar_eventos_directos(config):
    queries = generar_queries_eventos(config)
    timeout = 30
    semaphore = asyncio.Semaphore(3)
    eventos = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False, args=['--no-sandbox'])
        context = await browser.new_context(
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        )
        async def trabajar(query):
            async with semaphore:
                page = await context.new_page()
                try:
                    return await buscar_eventos_directos_facebook(query, page, timeout)
                finally:
                    await page.close()
        tareas_eventos = [trabajar(q) for q in queries]
        resultados_eventos = await asyncio.gather(*tareas_eventos, return_exceptions=True)
        for res in resultados_eventos:
            if isinstance(res, list):
                eventos.extend(res)
        await browser.close()
    return eventos
