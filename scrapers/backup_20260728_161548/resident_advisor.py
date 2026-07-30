import asyncio
import random
import logging
from playwright.async_api import async_playwright

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def scrape_resident_advisor(max_eventos=50):
    url = "https://www.residentadvisor.net/events"
    logger.info(f"🌐 Buscando en Resident Advisor: {url}")
    eventos = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=['--no-sandbox'])
        page = await browser.new_page()
        try:
            await page.goto(url, wait_until='networkidle')
            await asyncio.sleep(random.uniform(2, 4))

            # Buscar eventos (ajustar selectores)
            elementos = await page.query_selector_all('li[data-type="event"]')
            for elem in elementos[:max_eventos]:
                try:
                    nombre_elem = await elem.query_selector('span[class*="event-title"]')
                    nombre = await nombre_elem.inner_text() if nombre_elem else "Sin nombre"

                    fecha_elem = await elem.query_selector('span[class*="date"]')
                    fecha = await fecha_elem.inner_text() if fecha_elem else "Fecha no disponible"

                    lugar_elem = await elem.query_selector('span[class*="location"]')
                    lugar = await lugar_elem.inner_text() if lugar_elem else "Lugar no disponible"

                    enlace_elem = await elem.query_selector('a[href*="/events/"]')
                    enlace = await enlace_elem.get_attribute('href') if enlace_elem else ""
                    if enlace and not enlace.startswith('http'):
                        enlace = f"https://www.residentadvisor.net{enlace}"

                    evento = {
                        "nombre": nombre.strip(),
                        "fecha": fecha.strip(),
                        "lugar": lugar.strip(),
                        "enlace": enlace,
                        "fuente": "Resident Advisor"
                    }
                    eventos.append(evento)
                except Exception as e:
                    logger.warning(f"Error extrayendo evento: {e}")
        except Exception as e:
            logger.error(f"Error en Resident Advisor: {e}")
        await browser.close()

    logger.info(f"✅ Resident Advisor: {len(eventos)} eventos")
    return eventos
