import json
import logging
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

async def scrape_facebook_public(config=None):
    """Orquesta los dos subagentes con configuraciones separadas."""
    logger.info("🔍 Iniciando scraping de Facebook (grupos + eventos directos)")

    # Cargar configuraciones específicas
    config_grupos = cargar_config_grupos()
    config_eventos = cargar_config_eventos()

    # Verificar que cada configuración tiene los campos correctos
    if 'tipos_evento' in config_grupos:
        logger.warning("⚠️ config_grupos.json contiene 'tipos_evento' - no debería. Se ignorará.")
    if not config_eventos.get('tipos_evento'):
        logger.error("❌ config_eventos.json debe contener 'tipos_evento'")

    # Ejecutar subagentes en paralelo
    import asyncio
    resultados = await asyncio.gather(
        buscar_grupos_y_eventos(config_grupos),
        buscar_eventos_directos(config_eventos),
        return_exceptions=True
    )

    eventos_grupos = resultados[0] if isinstance(resultados[0], list) else []
    eventos_directos = resultados[1] if isinstance(resultados[1], list) else []

    # Consolidar
    todos = eventos_grupos + eventos_directos
    unicos = []
    seen = set()
    for e in todos:
        key = (e.get("nombre", ""), e.get("organizador", ""))
        if key not in seen and e.get("nombre") and e["nombre"] != "Sin nombre":
            seen.add(key)
            unicos.append(e)

    logger.info(f"✅ Facebook TOTAL: {len(unicos)} eventos (grupos: {len(eventos_grupos)}, directos: {len(eventos_directos)})")
    return unicos
