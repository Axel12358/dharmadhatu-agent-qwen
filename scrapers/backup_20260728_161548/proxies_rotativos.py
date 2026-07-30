import asyncio
import random
import logging
from playwright.async_api import async_playwright

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Lista de proxies gratuitos de ejemplo (puedes ampliarla)
PROXIES = [
    "http://proxy1:8080",
    "http://proxy2:8080",
    "http://proxy3:8080",
]

async def get_proxies():
    """Obtiene una lista de proxies gratuitos (simulado)."""
    # En producción, podrías scrapear https://free-proxy-list.net/
    # pero para evitar dependencias externas, usamos una lista fija por ahora.
    return PROXIES

async def rotate_proxy(page):
    """Asigna un proxy aleatorio a la página."""
    proxies = await get_proxies()
    if proxies:
        proxy = random.choice(proxies)
        await page.set_extra_http_headers({"Proxy": proxy})
        logger.info(f"🔄 Proxy asignado: {proxy}")
    else:
        logger.warning("⚠️ No hay proxies disponibles. Usando IP directa.")
