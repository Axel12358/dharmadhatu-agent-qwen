import asyncio
import logging
from scrapers.facebook_public import scrape_facebook_public
from scrapers.goabase import scrape_goabase
from scrapers.songkick import scrape_songkick

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def main():
    config = {
        "max_eventos": 150,
        "busquedas_facebook": 50,
        "timeout": 30,
        "extraer_contactos": True
    }

    print("🧘 DHARMADHATU BOT v5 (ARQUITECTURA SEPARADA)")
    print("==================================================")

    # Verificar si las funciones son asíncronas, si no, usar to_thread
    import inspect
    tasks = []
    if inspect.iscoroutinefunction(scrape_goabase):
        tasks.append(scrape_goabase(150))
    else:
        tasks.append(asyncio.to_thread(scrape_goabase, 150))
    
    tasks.append(scrape_facebook_public(config))
    
    if inspect.iscoroutinefunction(scrape_songkick):
        tasks.append(scrape_songkick(150))
    else:
        tasks.append(asyncio.to_thread(scrape_songkick, 150))

    resultados = await asyncio.gather(*tasks, return_exceptions=True)

    eventos = []
    for res in resultados:
        if isinstance(res, list):
            eventos.extend(res)
        elif isinstance(res, Exception):
            logger.error(f"Error en scraper: {res}")

    print(f"\n✅ Total eventos: {len(eventos)}")
    print("=" * 50)

if __name__ == "__main__":
    asyncio.run(main())
