import asyncio
import json
import re
from playwright.async_api import async_playwright

async def extraer_goabase_completo():
    """Extrae TODOS los países, ciudades y subgéneros de Goabase."""
    
    datos = {
        "paises": set(),
        "ciudades": set(),
        "subgeneros": set(),
        "tipos_evento": set()
    }
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        
        # Ir a la página de eventos
        await page.goto('https://www.goabase.net/party')
        await page.wait_for_selector('.party-list', timeout=10000)
        
        # Scroll para cargar todos los eventos
        for _ in range(5):
            await page.evaluate('window.scrollBy(0, window.innerHeight)')
            await asyncio.sleep(1)
        
        # Extraer eventos
        eventos = await page.query_selector_all('.party-item, .party-card, .event-item')
        
        for evento in eventos:
            try:
                texto = await evento.inner_text()
                # Extraer país (formato común en Goabase)
                pais_match = re.search(r'([A-Z][a-z]+)\s*[/|]\s*[A-Z]{2}', texto)
                if pais_match:
                    datos["paises"].add(pais_match.group(1).strip())
                
                # Extraer ciudad (formato común)
                ciudad_match = re.search(r'([A-Z][a-z]+)\s*[,.]\s*[A-Z]', texto)
                if ciudad_match:
                    datos["ciudades"].add(ciudad_match.group(1).strip())
                
                # Extraer subgéneros (tags comunes)
                if 'psytrance' in texto.lower():
                    datos["subgeneros"].add('psytrance')
                if 'goa' in texto.lower():
                    datos["subgeneros"].add('goa')
                if 'fullon' in texto.lower():
                    datos["subgeneros"].add('fullon')
                if 'progressive' in texto.lower():
                    datos["subgeneros"].add('progressive')
                if 'darkpsy' in texto.lower():
                    datos["subgeneros"].add('darkpsy')
                if 'hitech' in texto.lower():
                    datos["subgeneros"].add('hitech')
                if 'suomi' in texto.lower():
                    datos["subgeneros"].add('suomi')
                if 'forest' in texto.lower():
                    datos["subgeneros"].add('forest')
                    
            except Exception as e:
                continue
        
        await browser.close()
    
    # Convertir sets a listas ordenadas
    datos = {k: sorted(list(v)) for k, v in datos.items()}
    
    print(f"✅ Países encontrados: {len(datos['paises'])}")
    print(f"✅ Ciudades encontradas: {len(datos['ciudades'])}")
    print(f"✅ Subgéneros: {len(datos['subgeneros'])}")
    
    with open('goabase_completo.json', 'w') as f:
        json.dump(datos, f, indent=2)
    
    return datos

if __name__ == "__main__":
    asyncio.run(extraer_goabase_completo())
