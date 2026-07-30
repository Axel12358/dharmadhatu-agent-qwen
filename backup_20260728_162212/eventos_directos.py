import asyncio
import random
import logging
from playwright.async_api import async_playwright
from bs4 import BeautifulSoup

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def validar_config_eventos(config):
    """Verifica que el JSON de eventos tenga tipos de evento."""
    if "tipos_evento" not in config or not config["tipos_evento"]:
        logger.error("❌ config_eventos.json DEBE contener 'tipos_evento'")
        return False
    if "subgeneros" not in config or "paises" not in config or "ciudades" not in config:
        logger.error("❌ config_eventos.json debe tener 'subgeneros', 'paises', 'ciudades' y 'tipos_evento'")
        return False
    return True

def generar_queries_eventos(config):
    queries = []
    for sub in config.get("subgeneros", []):
        for tipo in config.get("tipos_evento", []):
            queries.append(f"{sub} {tipo}")
            for pais in config.get("paises", []):
                queries.append(f"{sub} {tipo} {pais}")
            for ciudad in config.get("ciudades", []):
                queries.append(f"{sub} {tipo} {ciudad}")
            for pais in config.get("paises", []):
                for ciudad in config.get("ciudades", []):
                    queries.append(f"{sub} {tipo} {pais} {ciudad}")
    random.shuffle(queries)
    return queries[:50]

async def buscar_eventos_directos(config):
    if not validar_config_eventos(config):
        return []
    queries = generar_queries_eventos(config)
    logger.info(f"🔍 Buscando eventos directos con {len(queries)} queries (CON tipos de evento)")
    eventos = []
    timeout = 30
    semaphore = asyncio.Semaphore(5)
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=['--no-sandbox'])
        context = await browser.new_context(
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        )
        async def buscar(query):
            async with semaphore:
                page = await context.new_page()
                try:
                    evs = []
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
                                "fuente": "Facebook (directo)"
                            })
                    return evs
                finally:
                    await page.close()
        tareas = [buscar(q) for q in queries]
        resultados = await asyncio.gather(*tareas, return_exceptions=True)
        for res in resultados:
            if isinstance(res, list):
                eventos.extend(res)
        await browser.close()
    logger.info(f"✅ {len(eventos)} eventos directos encontrados")
    return eventos
