import asyncio
import logging
from tenacity import retry, stop_after_attempt, wait_fixed, retry_if_exception_type
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def with_retry(max_attempts=3, delay=2):
    """
    Decorador para reintentar funciones asíncronas con backoff fijo.
    """
    def decorator(func):
        @retry(
            stop=stop_after_attempt(max_attempts),
            wait=wait_fixed(delay),
            retry=retry_if_exception_type((PlaywrightTimeoutError, ConnectionError, TimeoutError)),
        )
        async def wrapper(*args, **kwargs):
            try:
                logger.info(f"🔁 Intentando ejecutar: {func.__name__}")
                return await func(*args, **kwargs)
            except Exception as e:
                logger.error(f"❌ Error en {func.__name__}: {e}")
                raise
        return wrapper
    return decorator

# Ejemplo de uso en un scraper:
# @with_retry(max_attempts=3, delay=3)
# async def scrape_facebook_safe(config):
#     # Código del scraper...
#     pass
