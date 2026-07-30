import asyncio
import random

async def scrape_grupos_fijos(config):
    """
    Scraper de grupos fijos. Recibe config (dict) y devuelve eventos de prueba.
    """
    eventos = []
    limit = config.get("max_eventos", 150)
    # Asegurar que limit es entero
    if isinstance(limit, dict):
        limit = 150
    for i in range(min(limit, 15)):
        eventos.append({
            "nombre": f"Grupo Fijo {i+1}",
            "fecha": f"2026-{random.randint(1,12):02d}-{random.randint(1,28):02d}",
            "lugar": f"Ciudad {i+1}",
            "fuente": "Grupos Fijos"
        })
    return eventos
