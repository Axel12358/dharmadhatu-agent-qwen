import asyncio
import random
import logging
import re
from playwright.async_api import async_playwright
from bs4 import BeautifulSoup

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TIPOS_EVENTO = ["festival", "party", "gathering", "rave", "outdoor", "indoor", "camp", "retreat", "open air"]

def generar_queries_grupos(config):
    """Genera queries para GRUPOS: subgénero + país + ciudad (SIN tipos de evento)."""
    queries = []
    for sub in config.get("subgeneros", []):
        for pais in config.get("paises", []):
            queries.append(f"{sub} {pais}")
        for ciudad in config.get("ciudades", []):
            queries.append(f"{sub} {ciudad}")
        for pais in config.get("paises", []):
            for ciudad in config.get("ciudades", []):
                queries.append(f"{sub} {pais} {ciudad}")
    random.shuffle(queries)
    return queries[:50]

async def buscar_grupos_facebook(query, page, timeout):
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

async def extraer_eventos_de_grupo(grupo_url, page, timeout):
    eventos = []
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
        for elem in elementos[:10]:
            texto = elem.get_text(separator=" ", strip=True)
            if not texto or len(texto) < 20:
                continue
            # FILTRO: solo si contiene algún tipo de evento
            if not any(t in texto.lower() for t in TIPOS_EVENTO):
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
                    "fuente": "Facebook (grupo)"
                })
    except Exception as e:
        logger.warning(f"Error en grupo {grupo_url}: {e}")
    return eventos

async def buscar_grupos_y_eventos(config):
    queries = generar_queries_grupos(config)
    timeout = 30
    semaphore = asyncio.Semaphore(5)
    grupos_encontrados = []
    eventos = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=['--no-sandbox'])
        context = await browser.new_context(
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        )
        async def buscar_grupos(query):
            async with semaphore:
                page = await context.new_page()
                try:
                    return await buscar_grupos_facebook(query, page, timeout)
                finally:
                    await page.close()
        tareas_grupos = [buscar_grupos(q) for q in queries]
        resultados_grupos = await asyncio.gather(*tareas_grupos, return_exceptions=True)
        for res in resultados_grupos:
            if isinstance(res, list):
                grupos_encontrados.extend(res)
        grupos_unicos = list(set(grupos_encontrados))
        logger.info(f"✅ {len(grupos_unicos)} grupos encontrados")
        async def extraer(grupo_url):
            async with semaphore:
                page = await context.new_page()
                try:
                    return await extraer_eventos_de_grupo(grupo_url, page, timeout)
                finally:
                    await page.close()
        tareas_eventos = [extraer(g) for g in grupos_unicos[:15]]
        resultados_eventos = await asyncio.gather(*tareas_eventos, return_exceptions=True)
        for res in resultados_eventos:
            if isinstance(res, list):
                eventos.extend(res)
        await browser.close()
    return eventos
