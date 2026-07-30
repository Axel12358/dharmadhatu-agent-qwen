import asyncio
import random
import logging
import json
from playwright.async_api import async_playwright
from bs4 import BeautifulSoup

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TIPOS_EVENTO = [
    "festival", "party", "gathering", "rave", "outdoor", "indoor",
    "camp", "retreat", "open air", "warehouse", "club", "ritual"
]

def validar_config_grupos(config):
    """Verifica que el JSON de grupos NO tenga tipos de evento."""
    if "tipos_evento" in config:
        logger.error("❌ config_grupos.json NO debe contener 'tipos_evento'. Eliminando...")
        config.pop("tipos_evento", None)
    if "subgeneros" not in config or "paises" not in config or "ciudades" not in config:
        logger.error("❌ config_grupos.json debe tener 'subgeneros', 'paises' y 'ciudades'")
        return False
    return True

def generar_queries_grupos(config):
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

async def buscar_grupos_y_eventos(config):
    if not validar_config_grupos(config):
        return []
    queries = generar_queries_grupos(config)
    logger.info(f"🔍 Buscando grupos con {len(queries)} queries (SIN tipos de evento)")
    eventos = []
    timeout = 30
    semaphore = asyncio.Semaphore(5)
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=['--no-sandbox'])
        context = await browser.new_context(
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        )
        async def buscar_grupos(query):
            async with semaphore:
                page = await context.new_page()
                try:
                    grupos = []
                    url = f"https://www.facebook.com/search/groups/?q={query.replace(' ', '%20')}"
                    await page.goto(url, timeout=timeout * 1000, wait_until='networkidle')
                    await asyncio.sleep(random.uniform(2, 4))
                    links = await page.query_selector_all('a[href*="/groups/"]')
                    for link in links:
                        href = await link.get_attribute('href')
                        if href and '/groups/' in href and 'search' not in href:
                            grupo_url = f"https://www.facebook.com{href}" if href.startswith('/') else href
                            if grupo_url not in grupos:
                                grupos.append(grupo_url)
                    return grupos
                finally:
                    await page.close()
        tareas = [buscar_grupos(q) for q in queries]
        resultados = await asyncio.gather(*tareas, return_exceptions=True)
        grupos = []
        for res in resultados:
            if isinstance(res, list):
                grupos.extend(res)
        grupos_unicos = list(set(grupos))
        logger.info(f"✅ {len(grupos_unicos)} grupos encontrados")
        async def extraer(grupo_url):
            async with semaphore:
                page = await context.new_page()
                try:
                    evs = []
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
                            import re
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
        tareas_eventos = [extraer(g) for g in grupos_unicos[:15]]
        resultados_eventos = await asyncio.gather(*tareas_eventos, return_exceptions=True)
        for res in resultados_eventos:
            if isinstance(res, list):
                eventos.extend(res)
        await browser.close()
    logger.info(f"✅ {len(eventos)} eventos extraídos desde grupos")
    return eventos
