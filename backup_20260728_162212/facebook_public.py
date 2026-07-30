import logging
import json
import os
from .grupos_buscador import buscar_grupos_y_eventos
from .eventos_directos import buscar_eventos_directos

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def cargar_config_grupos():
    with open('config_grupos.json', 'r') as f:
        return json.load(f)

def cargar_config_eventos():
    with open('config_eventos.json', 'r') as f:
        return json.load(f)

async def scrape_facebook_public(config):
    """
    Orquesta la búsqueda en grupos y eventos directos.
    Supervisa que cada JSON tenga los campos correctos.
    """
    logger.info("🔍 Iniciando scraping de Facebook con supervisión")
    
    # Cargar configuraciones
    config_grupos = cargar_config_grupos()
    config_eventos = cargar_config_eventos()
    
    # Validar que config_grupos NO tenga tipos de evento
    if "tipos_evento" in config_grupos:
        logger.error("❌ config_grupos.json contiene 'tipos_evento' - CORRIGIENDO...")
        config_grupos.pop("tipos_evento", None)
        with open('config_grupos.json', 'w') as f:
            json.dump(config_grupos, f, indent=2)
        logger.info("✅ config_grupos.json corregido (se eliminó 'tipos_evento')")
    
    # Validar que config_eventos SÍ tenga tipos de evento
    if "tipos_evento" not in config_eventos or not config_eventos["tipos_evento"]:
        logger.error("❌ config_eventos.json no tiene 'tipos_evento' - CORRIGIENDO...")
        config_eventos["tipos_evento"] = [
            "festival", "party", "gathering", "rave", "outdoor", "indoor",
            "camp", "retreat", "open air", "warehouse", "club", "ritual"
        ]
        with open('config_eventos.json', 'w') as f:
            json.dump(config_eventos, f, indent=2)
        logger.info("✅ config_eventos.json corregido (se añadió 'tipos_evento')")
    
    # Ejecutar búsquedas
    logger.info("📂 Buscando eventos en GRUPOS...")
    eventos_grupos = await buscar_grupos_y_eventos(config_grupos)
    
    logger.info("📂 Buscando eventos DIRECTOS...")
    eventos_directos = await buscar_eventos_directos(config_eventos)
    
    # Consolidar
    todos = eventos_grupos + eventos_directos
    unicos = []
    seen = set()
    for e in todos:
        key = (e["nombre"], e["organizador"])
        if key not in seen and e["nombre"] != "Sin nombre":
            seen.add(key)
            unicos.append(e)
    
    logger.info(f"✅ Facebook TOTAL: {len(unicos)} eventos (grupos: {len(eventos_grupos)}, directos: {len(eventos_directos)})")
    return unicos
