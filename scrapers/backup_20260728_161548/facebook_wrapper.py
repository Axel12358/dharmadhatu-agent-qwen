import asyncio
import random
import logging
import sys
import os

# Añadir rutas de los scrapers clonados
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'fb_scraper_baberibrar'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'fb_scraper_mrkkr'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'fb_scraper_adityamukhopadhyay'))

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Proxies gratuitos (puedes ampliar o usar un servicio)
PROXIES = [
    "http://proxy1:8080",
    "http://proxy2:8080",
    "http://proxy3:8080",
]

def get_random_proxy():
    return random.choice(PROXIES) if PROXIES else None

async def ejecutar_scraper_con_proxies(scraper_func, query, max_eventos):
    for intento in range(3):
        proxy = get_random_proxy()
        try:
            logger.info(f"🔍 Ejecutando scraper con proxy {proxy}")
            # Adaptar llamada según la firma de cada scraper
            eventos = await scraper_func(query, max_eventos)
            if eventos:
                logger.info(f"✅ Encontrados {len(eventos)} eventos")
                return eventos
        except Exception as e:
            logger.warning(f"❌ Error con proxy {proxy}: {e}")
            await asyncio.sleep(random.uniform(2, 5))
    return []

async def scrape_facebook_multi(config):
    query = "psytrance festival"
    max_eventos = config.get('max_eventos', 150)
    todos_eventos = []

    # Importar funciones de cada scraper (con manejo de errores)
    scrapers = []
    try:
        from scraper import scrape_events as scrape_baberibrar
        scrapers.append(scrape_baberibrar)
    except ImportError:
        logger.warning("⚠️ No se pudo importar scraper baberibrar")

    try:
        from scraper import get_events as scrape_mrkkr
        scrapers.append(scrape_mrkkr)
    except ImportError:
        logger.warning("⚠️ No se pudo importar scraper mrkkr")

    try:
        from fb_event_scraper import scrape_events as scrape_adityamukhopadhyay
        scrapers.append(scrape_adityamukhopadhyay)
    except ImportError:
        logger.warning("⚠️ No se pudo importar scraper adityamukhopadhyay")

    if not scrapers:
        logger.error("❌ No se pudo importar ningún scraper externo")
        return []

    tareas = [ejecutar_scraper_con_proxies(scraper, query, max_eventos) for scraper in scrapers]
    resultados = await asyncio.gather(*tareas, return_exceptions=True)

    for res in resultados:
        if isinstance(res, list):
            todos_eventos.extend(res)
        elif isinstance(res, Exception):
            logger.warning(f"⚠️ Error en un scraper: {res}")

    # Consolidar y eliminar duplicados
    eventos_unicos = {}
    for e in todos_eventos:
        key = (e.get('nombre', ''), e.get('fecha', ''))
        if key not in eventos_unicos:
            eventos_unicos[key] = e

    eventos_final = list(eventos_unicos.values())
    logger.info(f"✅ Facebook Multi: {len(eventos_final)} eventos únicos")
    return eventos_final
