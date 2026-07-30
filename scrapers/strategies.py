import asyncio
import re
import json
import subprocess
import os
import time
import random
from pathlib import Path
from playwright.async_api import async_playwright
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ----- ESTRATEGIA 1: baberibrar/facebook-events-scraper -----
async def estrategia_baberibrar(query, config):
    """Usa el scraper de baberibrar (Playwright, sin login)."""
    scraper_dir = Path(__file__).parent / "fb_scraper_baberibrar"
    fb_script = scraper_dir / "fb_events.py"
    
    if not scraper_dir.exists():
        logger.error("❌ No se encontró fb_scraper_baberibrar. Clonando...")
        os.system("git clone https://github.com/baberibrar/facebook-events-scraper.git scrapers/fb_scraper_baberibrar")
        os.system("pip install -r scrapers/fb_scraper_baberibrar/requirements.txt")
        os.system("playwright install chromium")
    
    output_file = f"fb_temp_{int(time.time())}_{random.randint(1,999)}.json"
    cmd = [
        "python3", str(fb_script),
        "--query", query,
        "--max", str(config.get("max_eventos", 150) // 5),
        "--output", output_file,
        "--headless"
    ]
    
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60, cwd=str(scraper_dir))
        if os.path.exists(output_file):
            with open(output_file, 'r') as f:
                try:
                    data = json.load(f)
                except:
                    data = {"events": []}
            os.remove(output_file)
            eventos = []
            for item in data.get('events', []):
                event_url = item.get('event_url', '')
                if event_url and not event_url.startswith('http'):
                    event_url = 'https://www.facebook.com' + event_url
                eventos.append({
                    'nombre': item.get('title', 'Sin nombre').strip(),
                    'fecha': item.get('date', 'Fecha no disponible').strip(),
                    'lugar': item.get('location', 'Lugar no disponible').strip(),
                    'organizador': item.get('organizer', 'No disponible').strip(),
                    'enlace': event_url,
                    'fuente': 'Facebook (baberibrar)'
                })
            logger.info(f"✅ Estrategia baberibrar: {len(eventos)} eventos")
            return eventos
    except Exception as e:
        logger.warning(f"⚠️ Error en baberibrar: {e}")
    return []

# ----- ESTRATEGIA 2: mrkkr/fb-events-scraper -----
async def estrategia_mrkkr(query, config):
    """Usa el scraper de mrkkr (alternativa)."""
    scraper_dir = Path(__file__).parent / "fb_scraper_mrkkr"
    if not scraper_dir.exists():
        logger.error("❌ No se encontró fb_scraper_mrkkr. Clonando...")
        os.system("git clone https://github.com/mrkkr/fb-events-scraper.git scrapers/fb_scraper_mrkkr")
        os.system("pip install -r scrapers/fb_scraper_mrkkr/requirements.txt")
        os.system("playwright install chromium")
    
    # mrkkr usa Flask, podemos llamarlo via HTTP o usar su módulo directamente
    # Por simplicidad, lo dejamos como placeholder y usamos el de baberibrar de nuevo
    # pero con otra query.
    return await estrategia_baberibrar(query, config)  # fallback

# ----- ESTRATEGIA 3: Extracción por texto (regex) -----
async def estrategia_texto(query, config):
    """Extrae eventos del HTML usando expresiones regulares."""
    eventos = []
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True, args=['--no-sandbox'])
            page = await browser.new_page()
            url = f"https://www.facebook.com/search/events/?q={query.replace(' ', '+')}"
            await page.goto(url, timeout=config.get('timeout', 30) * 1000)
            await page.wait_for_timeout(3000)
            html = await page.content()
            await browser.close()
            
            # Buscar patrones típicos de eventos
            patrones = [
                r'<span[^>]*dir="auto"[^>]*>(.*?)</span>',
                r'<span[^>]*data-testid="event-card-date"[^>]*>(.*?)</span>',
                r'<span[^>]*data-testid="event-card-location"[^>]*>(.*?)</span>',
                r'<span[^>]*data-testid="event-card-host"[^>]*>(.*?)</span>'
            ]
            # Extraer nombres
            nombres = re.findall(patrones[0], html)
            for nombre in nombres[:config.get('max_eventos', 150)]:
                if nombre and 'Sin nombre' not in nombre:
                    eventos.append({
                        'nombre': nombre.strip(),
                        'fecha': 'Fecha no disponible',
                        'lugar': 'Lugar no disponible',
                        'organizador': 'No disponible',
                        'enlace': '',
                        'fuente': 'Facebook (texto)'
                    })
            logger.info(f"✅ Estrategia texto: {len(eventos)} eventos")
    except Exception as e:
        logger.warning(f"⚠️ Error en estrategia_texto: {e}")
    return eventos

# ----- ESTRATEGIA 4: Qwen (interpreta HTML y extrae eventos) -----
async def estrategia_qwen(query, config):
    """Usa Qwen (Ollama) para interpretar el HTML y extraer eventos."""
    eventos = []
    try:
        import ollama
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True, args=['--no-sandbox'])
            page = await browser.new_page()
            url = f"https://www.facebook.com/search/events/?q={query.replace(' ', '+')}"
            await page.goto(url, timeout=config.get('timeout', 30) * 1000)
            await page.wait_for_timeout(3000)
            html = await page.content()
            await browser.close()
            
            prompt = f"""
            Eres un experto en extracción de datos de Facebook. Analiza este HTML y extrae TODOS los eventos.
            Para cada evento, extrae:
            - nombre: título del evento
            - fecha: fecha del evento
            - lugar: ubicación del evento
            - organizador: quien organiza (Hosted by)
            - enlace: URL del evento (si existe)
            
            Devuelve SOLO un JSON con la lista de eventos.
            
            HTML (primeros 15000 caracteres):
            {html[:15000]}
            """
            
            response = ollama.chat(
                model='qwen2.5-coder:7b',
                messages=[{'role': 'user', 'content': prompt}],
                options={'temperature': 0.1}
            )
            try:
                data = json.loads(response['message']['content'])
                if isinstance(data, list):
                    for item in data:
                        eventos.append({
                            'nombre': item.get('nombre', 'Sin nombre'),
                            'fecha': item.get('fecha', 'Fecha no disponible'),
                            'lugar': item.get('lugar', 'Lugar no disponible'),
                            'organizador': item.get('organizador', 'No disponible'),
                            'enlace': item.get('enlace', ''),
                            'fuente': 'Facebook (Qwen)'
                        })
                    logger.info(f"✅ Estrategia Qwen: {len(eventos)} eventos")
            except:
                logger.warning("⚠️ Qwen no devolvió JSON válido")
    except Exception as e:
        logger.warning(f"⚠️ Error en estrategia_qwen: {e}")
    return eventos

# ----- ORQUESTACIÓN DE ESTRATEGIAS -----
async def get_events(query, config):
    estrategias = [
        estrategia_baberibrar,
        estrategia_mrkkr,
        estrategia_texto,
        estrategia_qwen
    ]
    
    for estrategia in estrategias:
        try:
            eventos = await estrategia(query, config)
            if eventos:
                return eventos
        except Exception as e:
            logger.warning(f"⚠️ Estrategia {estrategia.__name__} falló: {e}")
    
    return []
