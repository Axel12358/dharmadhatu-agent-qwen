import asyncio
import json
import re
from playwright.async_api import async_playwright

async def extraer_clasificacion_goabase():
    """Extrae todas las categorías, subgéneros y tipos de evento de Goabase."""
    
    clasificacion = {
        "subgeneros": set(),
        "tipos_evento": set(),
        "locaciones": set(),
        "paises": set()
    }
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        
        # Ir a la página de eventos
        await page.goto('https://www.goabase.net/party')
        await page.wait_for_selector('.party-list', timeout=10000)
        
        # Extraer subgéneros de los tags
        tags = await page.query_selector_all('.tag, .genre, .category')
        for tag in tags:
            texto = await tag.inner_text()
            texto = texto.strip().lower()
            if texto and len(texto) > 2:
                clasificacion["subgeneros"].add(texto)
        
        # Extraer tipos de evento de los filtros o breadcrumbs
        tipos = await page.query_selector_all('.event-type, .type, .filter-option')
        for tipo in tipos:
            texto = await tipo.inner_text()
            texto = texto.strip().lower()
            if texto and len(texto) > 2:
                clasificacion["tipos_evento"].add(texto)
        
        # Extraer países de los eventos listados
        paises = await page.query_selector_all('.country, .location-country, .flag')
        for pais in paises:
            texto = await pais.inner_text()
            texto = texto.strip()
            if texto and len(texto) > 2:
                clasificacion["paises"].add(texto)
        
        # Extraer ciudades
        ciudades = await page.query_selector_all('.city, .location-city')
        for ciudad in ciudades:
            texto = await ciudad.inner_text()
            texto = texto.strip()
            if texto and len(texto) > 2:
                clasificacion["locaciones"].add(texto)
        
        await browser.close()
    
    # Convertir sets a lists y ordenar
    clasificacion = {k: sorted(list(v)) for k, v in clasificacion.items()}
    
    # Guardar en archivo
    with open('clasificacion_goabase.json', 'w') as f:
        json.dump(clasificacion, f, indent=2)
    
    print("✅ Clasificación extraída y guardada en 'clasificacion_goabase.json'")
    print(f"📊 Subgéneros: {len(clasificacion['subgeneros'])}")
    print(f"📊 Tipos de evento: {len(clasificacion['tipos_evento'])}")
    print(f"📊 Países: {len(clasificacion['paises'])}")
    print(f"📊 Ciudades: {len(clasificacion['locaciones'])}")
    
    return clasificacion

if __name__ == "__main__":
    asyncio.run(extraer_clasificacion_goabase())
