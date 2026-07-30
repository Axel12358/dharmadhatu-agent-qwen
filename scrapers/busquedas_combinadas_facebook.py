import asyncio
import logging
from playwright.async_api import async_playwright

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def search_facebook_combinations(config):
    """
    Genera búsquedas combinadas para Facebook usando países, subgéneros y tipos de evento.
    """
    paises = config.get('priorizar_paises', [])
    subgeneros = config.get('subgeneros', ['psytrance'])
    tipos = config.get('tipos_evento', ['festival'])
    
    queries = []
    for pais in paises:
        for sub in subgeneros:
            for tipo in tipos:
                queries.append(f"{sub} {tipo} {pais}")
    
    logger.info(f"🔍 Generadas {len(queries)} queries combinadas para Facebook")
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=['--no-sandbox'])
        context = await browser.new_context()
        page = await context.new_page()
        
        eventos = []
        max_eventos = config.get('max_eventos', 150)
        
        for query in queries[:max_eventos]:
            try:
                url = f"https://www.facebook.com/search/events/?q={query.replace(' ', '%20')}"
                await page.goto(url, timeout=30000)
                await asyncio.sleep(2)
                
                # Aquí iría la extracción de eventos (similar a facebook_public.py)
                # Por ahora, solo simulamos la extracción
                logger.info(f"✅ Query procesada: {query}")
            except Exception as e:
                logger.error(f"❌ Error en query '{query}': {e}")
        
        await browser.close()
        return eventos

# Si quieres usarlo como scraper principal, puedes integrarlo en facebook_public.py
