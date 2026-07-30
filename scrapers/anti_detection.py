import random
import logging
from playwright.async_api import async_playwright

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Lista de user-agents reales
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36 Edg/119.0.0.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
]

# Lista de proxies gratuitos (puedes ampliarla)
PROXIES = [
    None,  # Sin proxy
    # Agrega aquí proxies de https://free-proxy-list.net/
]

async def apply_stealth(page):
    """Aplica técnicas stealth para evitar detección."""
    try:
        await page.evaluate("""
            () => {
                // Eliminar propiedad webdriver
                Object.defineProperty(navigator, 'webdriver', {
                    get: () => undefined,
                });
                // Simular plugins
                Object.defineProperty(navigator, 'plugins', {
                    get: () => [1, 2, 3, 4, 5],
                });
                // Simular lenguajes
                Object.defineProperty(navigator, 'languages', {
                    get: () => ['es-ES', 'es', 'en-US', 'en'],
                });
            }
        """)
        logger.debug("✅ Stealth aplicado")
    except Exception as e:
        logger.warning(f"⚠️ Error al aplicar stealth: {e}")

async def rotate_user_agent(page):
    """Cambia el user-agent de forma rotativa."""
    user_agent = random.choice(USER_AGENTS)
    await page.set_extra_http_headers({"User-Agent": user_agent})
    logger.debug(f"🔄 User-Agent: {user_agent[:30]}...")

async def apply_anti_detection(page, use_proxy=False):
    """Aplica todas las técnicas anti-detección."""
    await apply_stealth(page)
    await rotate_user_agent(page)
    # Aquí se puede añadir proxy si se desea
    if use_proxy and PROXIES:
        proxy = random.choice([p for p in PROXIES if p])
        if proxy:
            await page.route("**/*", lambda route: route.continue_(overrides={"proxy": {"server": proxy}}))
            logger.debug(f"🔄 Proxy: {proxy}")
    
    # Simular comportamiento humano (scroll aleatorio)
    try:
        await page.evaluate("window.scrollBy(0, window.innerHeight * 0.5)")
        await page.wait_for_timeout(random.randint(500, 1500))
    except:
        pass
