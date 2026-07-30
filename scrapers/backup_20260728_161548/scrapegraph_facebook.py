import asyncio
import logging
from scrapegraphai.graphs import SmartScraperGraph
from playwright.async_api import async_playwright

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def scrape_facebook_scrapegraph(config):
    """Versión asíncrona de ScrapeGraphAI para Facebook."""
    try:
        # Configuración de ScrapeGraphAI
        graph_config = {
            "llm": {
                "model": "ollama/qwen2.5-coder:7b",
                "base_url": "http://localhost:11434",
            },
            "verbose": True,
        }

        # Crear el grafo
        smart_scraper_graph = SmartScraperGraph(
            prompt="Extrae los eventos de psytrance con nombre, fecha, lugar, organizador y enlace.",
            source="https://www.facebook.com/events/search/?q=psytrance+festival",
            config=graph_config
        )

        # Ejecutar de forma síncrona en un hilo separado
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(None, smart_scraper_graph.run)
        
        eventos = []
        if result and 'events' in result:
            for item in result['events']:
                evento = {
                    "nombre": item.get('name', 'Sin nombre'),
                    "fecha": item.get('date', 'Fecha no disponible'),
                    "lugar": item.get('location', 'Lugar no disponible'),
                    "organizador": item.get('organizer', 'No disponible'),
                    "email": item.get('email', 'No disponible'),
                    "enlace": item.get('link', ''),
                    "fuente": "ScrapeGraphAI"
                }
                eventos.append(evento)
        
        logger.info(f"✅ ScrapeGraphAI: {len(eventos)} eventos")
        return eventos

    except Exception as e:
        logger.error(f"❌ Error en ScrapeGraphAI: {e}")
        return []
