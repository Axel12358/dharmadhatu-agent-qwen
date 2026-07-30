import os
import json
import datetime
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

CONFIGS_DIR = "configs_exitosas"

def guardar_configuracion(config, score, eventos, tiempo):
    """Guarda una configuración exitosa en un archivo markdown."""
    os.makedirs(CONFIGS_DIR, exist_ok=True)
    fecha = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    archivo = f"{CONFIGS_DIR}/config_{fecha}.md"
    
    with open(archivo, "w") as f:
        f.write(f"# Configuración exitosa - {fecha}\n\n")
        f.write(f"## Resultados\n")
        f.write(f"- **Score**: {score}\n")
        f.write(f"- **Eventos**: {eventos}\n")
        f.write(f"- **Tiempo**: {tiempo:.2f} min\n\n")
        f.write(f"## Configuración\n```json\n{json.dumps(config, indent=2)}\n```\n\n")
        f.write(f"## Estrategia\n")
        f.write(f"- **Scraper**: Facebook (estrategias múltiples)\n")
        f.write(f"- **Anti-detección**: playwright-stealth, user-agents rotativos\n")
        f.write(f"- **Proxies**: No (directo)\n\n")
        f.write(f"## Fecha de ejecución\n{fecha}\n")
    
    logger.info(f"✅ Configuración guardada en {archivo}")
    return archivo

def cargar_configuraciones():
    """Carga todas las configuraciones exitosas guardadas."""
    configs = []
    if not os.path.exists(CONFIGS_DIR):
        return configs
    
    for archivo in os.listdir(CONFIGS_DIR):
        if archivo.endswith(".md"):
            try:
                with open(os.path.join(CONFIGS_DIR, archivo), "r") as f:
                    contenido = f.read()
                    import re
                    match = re.search(r'```json\n(.*?)\n```', contenido, re.DOTALL)
                    if match:
                        config = json.loads(match.group(1))
                        configs.append(config)
            except:
                pass
    
    return configs

def aplicar_fallback(url, configs):
    """Busca una configuración previa para la URL."""
    for config in configs:
        if config.get('url') == url:
            return config
    return None
