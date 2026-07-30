import json
import logging
from .eventos_directos import buscar_eventos_directos

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def cargar_config_eventos():
    with open('config_eventos.json', 'r') as f:
        return json.load(f)

async def scrape_facebook_public(config=None):
    """SOLO EVENTOS DIRECTOS - grupos desactivados temporalmente."""
    logger.info("🔍 Iniciando scraping de Facebook (SOLO eventos directos)")
    
    config_eventos = cargar_config_eventos()
    
    if not config_eventos.get('tipos_evento'):
        logger.error("❌ config_eventos.json debe contener 'tipos_evento'")
        return []
    
    eventos = await buscar_eventos_directos(config_eventos)
    logger.info(f"✅ Facebook DIRECTOS: {len(eventos)} eventos")
    return eventos
