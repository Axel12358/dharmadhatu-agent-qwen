import asyncio
import random
import logging
from playwright.async_api import async_playwright

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def scrape_eventbrite(max_eventos=50):
    url = "https://www.eventbrite.com/d/online/music-events/"
    logger.info(f"🌐 Buscando en Eventbrite: {url}")
    eventos = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=['--no-sandbox'])
        page = await browser.new_page()
        try:
            await page.goto(url, wait_until='networkidle')
            await asyncio.sleep(random.uniform(2, 4))

            elementos = await page.query_selector_all('section[data-testid="listing-card"]')
            for elem in elementos[:max_eventos]:
                try:
                    nombre_elem = await elem.query_selector('h2[data-testid="listing-card-title"]')
                    nombre = await nombre_elem.inner_text() if nombre_elem else "Sin nombre"

                    fecha_elem = await elem.query_selector('p[data-testid="listing-card-date"]')
                    fecha = await fecha_elem.inner_text() if fecha_elem else "Fecha no disponible"

                    lugar_elem = await elem.query_selector('p[data-testid="listing-card-location"]')
                    lugar = await lugar_elem.inner_text() if lugar_elem else "Lugar no disponible"

                    enlace_elem = await elem.query_selector('a[href*="/e/"]')
                    enlace = await enlace_elem.get_attribute('href') if enlace_elem else ""
                    if enlace and not enlace.startswith('http'):
                        enlace = f"https://www.eventbrite.com{enlace}"

                    evento = {
                        "nombre": nombre.strip(),
                        "fecha": fecha.strip(),
                        "lugar": lugar.strip(),
                        "enlace": enlace,
                        "fuente": "Eventbrite"
                    }
                    eventos.append(evento)
                except Exception as e:
                    logger.warning(f"Error extrayendo evento: {e}")
        except Exception as e:
            logger.error(f"Error en Eventbrite: {e}")
        await browser.close()

    logger.info(f"✅ Eventbrite: {len(eventos)} eventos")
    return eventos
