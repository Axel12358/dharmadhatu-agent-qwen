#!/usr/bin/env python3
"""
Buscador de grupos de Facebook con anti-detección avanzada.
- Patchright (indetectable) o Playwright estándar
- Comportamiento humano manual (scroll, pausas, movimientos)
- Rotación de fingerprints (user-agent, viewport, locale)
- DuckDuckGo como motor de búsqueda
"""

import asyncio
import random
import json
import csv
import re
import sys
import os

# Intentar usar Patchright (indetectable)
try:
    from patchright.async_api import async_playwright
    print("✅ Usando Patchright (indetectable)")
except ImportError:
    from playwright.async_api import async_playwright
    print("⚠️ Patchright no instalado, usando Playwright estándar")

# --- Fingerprint Generator (integrado) ---
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/119.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 Safari/605.1.15",
]

def random_ua():
    return random.choice(USER_AGENTS)

def random_viewport():
    return {"width": random.randint(1280, 1920), "height": random.randint(720, 1080)}

def random_locale():
    return random.choice(["es-ES", "en-US", "de-DE", "fr-FR", "it-IT", "pt-PT"])

def random_timezone():
    return random.choice(["Europe/Madrid", "Europe/Berlin", "Europe/London", "Europe/Paris"])

def generate_profile():
    return {
        "user_agent": random_ua(),
        "viewport": random_viewport(),
        "locale": random_locale(),
        "timezone": random_timezone(),
    }

# --- Comportamiento humano manual ---
async def human_scroll(page, times=3):
    for _ in range(times):
        await page.mouse.wheel(0, random.randint(300, 700))
        await asyncio.sleep(random.uniform(1, 3))

async def human_pause(min_sec=2, max_sec=5):
    await asyncio.sleep(random.uniform(min_sec, max_sec))

async def simulate_human(page):
    """Simula comportamiento humano en la página."""
    # Movimiento de ratón suave
    await page.mouse.move(random.randint(100, 500), random.randint(100, 400))
    await human_pause(0.5, 1.5)
    await page.mouse.move(random.randint(600, 1200), random.randint(300, 600))
    await human_pause(0.5, 1.5)
    # Scroll humano
    await human_scroll(page, random.randint(2, 4))

# --- Configuración ---
MAX_QUERIES = 15
DELAY_BETWEEN_QUERIES = (30, 60)
DELAY_AFTER_PAGE_LOAD = (8, 15)

# Tor (opcional)
TOR_PROXY = "socks5://127.0.0.1:9150"
USE_TOR = False  # Cambiar a True si quieres usar Tor

# Stem para rotar IP
try:
    from stem import Signal
    from stem.control import Controller
    STEM_AVAILABLE = True
except ImportError:
    STEM_AVAILABLE = False

def renew_tor_ip():
    if not STEM_AVAILABLE or not USE_TOR:
        return
    try:
        with Controller.from_port(port=9051) as controller:
            controller.signal(Signal.NEWNYM)
            print("🔄 IP de Tor renovada")
    except Exception as e:
        print(f"⚠️ Error renovando IP: {e}")

# Países y ciudades
PAISES_DEFECTO = [
    {"nombre": "España", "ciudades": ["Madrid", "Barcelona", "Valencia", "Sevilla"]},
    {"nombre": "Alemania", "ciudades": ["Berlín", "Múnich", "Hamburgo"]},
    {"nombre": "Bélgica", "ciudades": ["Bruselas", "Amberes"]},
    {"nombre": "Portugal", "ciudades": ["Lisboa", "Oporto"]},
    {"nombre": "Francia", "ciudades": ["París", "Lyon", "Marsella"]},
    {"nombre": "Italia", "ciudades": ["Roma", "Milán", "Turín"]},
    {"nombre": "Países Bajos", "ciudades": ["Ámsterdam", "Róterdam"]},
    {"nombre": "Reino Unido", "ciudades": ["Londres", "Mánchester", "Bristol"]},
]

def generate_queries(subgeneros, paises_data):
    queries = []
    for sub in subgeneros:
        for pais_obj in paises_data:
            pais = pais_obj["nombre"]
            queries.append(f"{sub} {pais}")
            for ciudad in pais_obj.get("ciudades", []):
                queries.append(f"{sub} {ciudad}")
    queries = list(set(queries))
    random.shuffle(queries)
    return queries

def random_search_engine():
    return "duckduckgo"  # DuckDuckGo es más permisivo

async def buscar_query_motor(query, semaforo, motor="duckduckgo"):
    async with semaforo:
        if USE_TOR:
            renew_tor_ip()
            await asyncio.sleep(3)

        async with async_playwright() as p:
            profile = generate_profile()
            
            launch_args = {
                "headless": False,  # Cambiar a True en producción
                "args": [
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                ]
            }
            if USE_TOR:
                launch_args["proxy"] = {"server": TOR_PROXY}
            
            browser = await p.chromium.launch(**launch_args)
            context = await browser.new_context(
                user_agent=profile["user_agent"],
                viewport=profile["viewport"],
                locale=profile["locale"],
                timezone_id=profile["timezone"],
                java_script_enabled=True,
                ignore_https_errors=True,
            )
            page = await context.new_page()
            
            # Inyección JS para ocultar webdriver
            await page.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
                Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
                window.chrome = { runtime: {} };
            """)
            
            google_query = f'site:facebook.com/groups/ "{query}"'
            url = f"https://duckduckgo.com/html/?q={google_query.replace(' ', '%20')}"
            print(f"🔍 [{motor.upper()}] {query} (UA: {profile['user_agent'][:30]}...)")
            grupos = []
            
            try:
                await page.goto(url, timeout=45000, wait_until="domcontentloaded")
                await human_pause(DELAY_AFTER_PAGE_LOAD[0], DELAY_AFTER_PAGE_LOAD[1])
                await simulate_human(page)
                
                # Extraer enlaces a grupos de Facebook
                enlaces = await page.query_selector_all("a[href*='facebook.com/groups/']")
                for enlace in enlaces:
                    href = await enlace.get_attribute("href")
                    if href and "facebook.com/groups/" in href:
                        match = re.search(r'(https?://[^\s&"\']+)', href)
                        if match:
                            href = match.group(1)
                        texto = await enlace.inner_text()
                        if texto and len(texto) > 2:
                            grupos.append({
                                "nombre": texto.strip(),
                                "enlace": href,
                                "tipo": "grupo"
                            })
                            print(f"   ✅ {texto[:50]}")
                
                if len(grupos) == 0:
                    print(f"   ⚠️ No se encontraron grupos para '{query}'")
                
            except Exception as e:
                print(f"   ⚠️ Error: {str(e)[:100]}")
            
            finally:
                await browser.close()
            
            return grupos

async def buscar_grupos_y_eventos(config):
    subgeneros = config.get("subgeneros", ["psytrance", "goa", "fullon", "darkpsy", "forest", "psychill"])
    paises_data = config.get("paises", PAISES_DEFECTO)
    
    queries = generate_queries(subgeneros, paises_data)
    queries = queries[:MAX_QUERIES]
    print(f"📋 Total queries a ejecutar: {len(queries)}")
    
    semaforo = asyncio.Semaphore(1)
    todos = []
    
    for i, q in enumerate(queries, 1):
        print(f"\n📌 Query {i}/{len(queries)}")
        motor = random_search_engine()
        resultados = await buscar_query_motor(q, semaforo, motor)
        todos.extend(resultados)
        if i < len(queries):
            wait_time = random.uniform(DELAY_BETWEEN_QUERIES[0], DELAY_BETWEEN_QUERIES[1])
            print(f"⏳ Esperando {wait_time:.1f} segundos...")
            await asyncio.sleep(wait_time)
    
    # Eliminar duplicados
    vistos = set()
    unicos = []
    for g in todos:
        if g["enlace"] not in vistos:
            vistos.add(g["enlace"])
            unicos.append(g)
    
    print(f"\n📊 Total grupos únicos: {len(unicos)}")
    return unicos

if __name__ == "__main__":
    async def main():
        try:
            with open("config_grupos.json") as f:
                config = json.load(f)
        except FileNotFoundError:
            print("⚠️ config_grupos.json no encontrado. Usando valores por defecto.")
            config = {}
        
        grupos = await buscar_grupos_y_eventos(config)
        
        with open("grupos_encontrados.csv", "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=["nombre", "enlace", "tipo"])
            writer.writeheader()
            writer.writerows(grupos)
        
        print(f"\n✅ CSV guardado: grupos_encontrados.csv ({len(grupos)} grupos)")
        for g in grupos[:5]:
            print(f"  - {g['nombre'][:60]} ({g['enlace']})")
    
    asyncio.run(main())
