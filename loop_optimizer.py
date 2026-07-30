import os
import time
import json
import random
import asyncio
from scrapers.facebook_public import generar_queries_facebook, buscar_eventos_en_grupos
from main_v5 import ejecutar_bot
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def optimizar_configuracion():
    """Loop de optimización que prueba configuraciones y guarda la mejor."""
    # Cargar configuración base
    with open('config_temp.json', 'r') as file:
        config = json.load(file)
    
    mejor_score = 0
    mejor_config = None
    resultados = []

    logger.info("🧠 INICIANDO LOOP DE OPTIMIZACIÓN (5 iteraciones)")
    
    for i in range(5):
        logger.info(f"\n--- Iteración {i+1}/5 ---")
        
        # Modificar configuración aleatoriamente
        config_modificada = config.copy()
        
        # Cambiar países
        if config_modificada.get("priorizar_paises"):
            config_modificada["priorizar_paises"] = random.sample(
                config_modificada["priorizar_paises"], 
                max(1, len(config_modificada["priorizar_paises"]) - random.randint(1, 3))
            )
        
        # Cambiar número de búsquedas
        config_modificada["busquedas_facebook"] = random.randint(30, 100)
        
        # Cambiar timeout
        config_modificada["timeout"] = random.randint(20, 45)
        
        logger.info(f"📊 Países: {len(config_modificada.get('priorizar_paises', []))}")
        logger.info(f"📊 Búsquedas: {config_modificada.get('busquedas_facebook', 50)}")
        
        # Ejecutar bot con esta configuración
        try:
            eventos, score = await ejecutar_bot(config_modificada)
            logger.info(f"🏆 Score: {score}")
            
            resultados.append({
                'config': config_modificada,
                'score': score,
                'eventos': len(eventos)
            })
            
            if score > mejor_score:
                mejor_score = score
                mejor_config = config_modificada.copy()
                logger.info(f"⭐ NUEVO MEJOR SCORE: {score}")
                
                # Guardar mejor configuración
                with open('mejor_config.json', 'w') as f:
                    json.dump(mejor_config, f, indent=2)
                logger.info("✅ Configuración guardada en 'mejor_config.json'")
                
        except Exception as e:
            logger.error(f"❌ Error en iteración {i+1}: {e}")
            continue
    
    logger.info("\n" + "=" * 50)
    logger.info(f"🏆 MEJOR SCORE: {mejor_score}")
    if mejor_config:
        logger.info("📌 MEJOR CONFIGURACIÓN:")
        logger.info(json.dumps(mejor_config, indent=2))
    logger.info("=" * 50)
    
    return mejor_score, mejor_config

if __name__ == "__main__":
    asyncio.run(optimizar_configuracion())
