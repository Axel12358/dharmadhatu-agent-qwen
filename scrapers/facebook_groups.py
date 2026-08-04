#!/usr/bin/env python3
"""
Buscador de GRUPOS de Facebook usando la versión móvil (m.facebook.com).
Con rotación de IP via Tor y loop de mejora.
"""

import asyncio
import random
import json
import re
from playwright.async_api import async_playwright

# Configuración
TOR_PROXY = "socks5://127.0.0.1:9150"
USE_TOR = True  # Cambiar a False si no quieres usar Tor
MAX_RETRIES = 3
TIMEOUT = 45000

class FacebookGroupFinder:
    def __init__(self):
        self.name = "Facebook Groups (Móvil)"
        self.user_agents = [
            "Mozilla/5.0 (Linux; Android 10; SM-G973F) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.120 Mobile Safari/537.36",
            "Mozilla/5.0 (iPhone; CPU iPhone OS 14_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/14.0 Mobile/15E148 Safari/604.1",
            "Mozilla/5.0 (Linux; Android 11; Pixel 5) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/90.0.4430.91 Mobile Safari/537.36"
        ]

    async def search_groups(self, keyword: str, semaphore) -> list:
        """Busca grupos usando la versión móvil de Facebook."""
        async with semaphore:
            for attempt in range(MAX_RETRIES):
                try:
                    async with async_playwright() as p:
                        # Configurar proxy (si está activo)
                        launch_args = {
                            "headless": True,
                            "args": ["--disable-blink-features=AutomationControlled"]
                        }
                        if USE_TOR:
                            launch_args["proxy"] = {"server": TOR_PROXY}
                        
                        browser = await p.chromium.launch(**launch_args)
                        context = await browser.new_context(
                            user_agent=random.choice(self.user_agents),
                            viewport={"width": 375, "height": 812},  # Móvil
                            locale="en-US",
                            timezone_id="America/New_York"
                        )
                        page = await context.new_page()
                        
                        # Navegar a búsqueda móvil
                        url = f"https://m.facebook.com/search/groups/?q={keyword.replace(' ', '%20')}"
                        print(f"🔍 Buscando (intento {attempt+1}): {keyword}")
                        
                        await page.goto(url, timeout=TIMEOUT, wait_until="domcontentloaded")
                        await asyncio.sleep(random.uniform(3, 6))
                        
                        # Scroll para cargar más
                        for _ in range(3):
                            await page.mouse.wheel(0, 500)
                            await asyncio.sleep(random.uniform(1, 2))
                        
                        # Extraer grupos (selectores de la versión móvil)
                        grupos = []
                        # Buscar enlaces a grupos
                        enlaces = await page.query_selector_all("a[href*='/groups/']")
                        for enlace in enlaces:
                            href = await enlace.get_attribute("href")
                            if href and "/groups/" in href and not href.startswith("/search"):
                                nombre = await enlace.inner_text()
                                if nombre and len(nombre) > 2:
                                    # Limpiar URL
                                    if href.startswith("/"):
                                        href = "https://m.facebook.com" + href
                                    grupos.append({
                                        "nombre": nombre.strip(),
                                        "enlace": href,
                                        "tipo": "grupo",
                                        "fuente": "Facebook"
                                    })
                                    print(f"   ✅ {nombre[:40]}")
                        
                        await browser.close()
                        
                        if grupos:
                            return grupos
                        else:
                            print(f"   ⚠️ No se encontraron grupos para '{keyword}'")
                            # Si no hay resultados, reintentar con más scroll
                            continue
                            
                except Exception as e:
                    print(f"   ⚠️ Error en '{keyword}' (intento {attempt+1}): {e}")
                    if attempt == MAX_RETRIES - 1:
                        return []
                    await asyncio.sleep(2 ** attempt * 5)  # Backoff exponencial

            return []

    async def scrape(self, keywords: list) -> list:
        """Busca grupos para cada keyword en paralelo."""
        print(f"📋 Total keywords: {len(keywords)}")
        if keywords:
            print(f"   Ejemplos: {keywords[:3]}")
        
        semaphore = asyncio.Semaphore(3)  # Máximo 3 búsquedas en paralelo
        tasks = [self.search_groups(kw, semaphore) for kw in keywords]
        resultados = await asyncio.gather(*tasks)
        
        todos = []
        for res in resultados:
            todos.extend(res)
        
        # Eliminar duplicados
        vistos = set()
        unicos = []
        for g in todos:
            if g["enlace"] not in vistos:
                vistos.add(g["enlace"])
                unicos.append(g)
        
        print(f"\n📊 Total grupos únicos: {len(unicos)}")
        return unicos

# Función de compatibilidad con main.py
async def scrape_facebook_events():
    """Busca grupos usando la configuración."""
    try:
        with open("config_grupos.json") as f:
            config = json.load(f)
    except FileNotFoundError:
        print("⚠️ config_grupos.json no encontrado")
        return []
    
    subgeneros = config.get("subgeneros", ["psytrance"])
    paises_data = config.get("paises", [])
    
    keywords = []
    for sub in subgeneros:
        for pais_obj in paises_data:
            pais = pais_obj["nombre"]
            keywords.append(f"{sub} {pais}")
            for ciudad in pais_obj.get("ciudades", []):
                keywords.append(f"{sub} {ciudad}")
    
    keywords = list(set(keywords))
    random.shuffle(keywords)
    
    finder = FacebookGroupFinder()
    grupos = await finder.scrape(keywords)
    return grupos

if __name__ == "__main__":
    async def test():
        grupos = await scrape_facebook_events()
        print(f"\n🎯 Total: {len(grupos)} grupos")
        for g in grupos[:5]:
            print(f"  - {g['nombre'][:50]} -> {g['enlace']}")
    asyncio.run(test())
