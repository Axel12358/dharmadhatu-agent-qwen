#### ARCHIVO: scrapers/facebook_public.py (código completo con la lógica co[2D[K
corregida)

```python
import asyncio
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright, expect

TIPOS_EVENTO = {
    'outdoor': ['outdoor', 'camp', 'rave'],
    'gathering': ['festival', 'party'],
    # Otros tipos de eventos según sea necesario
}

async def generar_queries_grupos(config):
    subgeneros = config['subgeneros']
    paises = config['paises']
    ciudades = config['ciudades']

    queries = []
    for subgen in subgeneros:
        for pais in paises:
            queries.append(f"{subgen} {pais}")
        
        for ciudad in ciudades:
            queries.append(f"{subgen} {ciudad}")

        for pais in paises:
            for comunidad in config['comunidades']:
                queries.append(f"{subgen} {pais} {comunidad}")

    return queries

async def buscar_grupos_y_eventos(query, page, timeout, config):
    await page.goto(f"https://www.facebook.com/search/groups?q={query}", wa[2D[K
wait_until="networkidle", timeout=timeout)
    await page.wait_for_selector("text='Grupos'", timeout=timeout)

    groups = await page.query_selector_all("div[data-testid='group_item']")[56D[K
page.query_selector_all("div[data-testid='group_item']")
    events = []

    for group in groups[:3]:  # Limitamos a los primeros 3 grupos
        group_name = (await group.get_attribute("aria-label")) or ""
        if "Grupos" not in group_name:
            continue

        await group.click()
        await page.wait_for_selector("div[data-testid='group_content']", ti[2D[K
timeout=timeout)
        
        publications = await page.query_selector_all("._5pcr")
        for publication in publications:
            try:
                text = (await publication.get_attribute('innerHTML')) or ""[2D[K
""
                soup = BeautifulSoup(text, 'html.parser')
                
                event_type = next((k for k, v in TIPOS_EVENTO.items() if an[2D[K
any(term.lower() in text.lower() for term in v)), None)
                organizer_match = re.search(r"Hosted by (.+?)\n", text) or [K
re.search(r"Organizado por (.+?)\n", text)
                organizer = organizer_match.group(1).strip() if organizer_m[11D[K
organizer_match else "No especificado"
                
                name = publication['aria-label'].split('\n')[0]
                date = None
                location = None
                link = None
                
                events.append({
                    'nombre': name,
                    'fecha': date,
                    'lugar': location,
                    'organizador': organizer,
                    'enlace': link
                })
            except Exception as e:
                print(f"Error parsing publication: {e}")

        await page.click('text="X"', timeout=timeout)  # Cerrar el grupo

    return events

async def scrape_facebook_public(config):
    queries = await generar_queries_grupos(config)
    semaphore = asyncio.Semaphore(5)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()

        tasks = []
        for query in queries:
            task = asyncio.create_task(buscar_grupos_y_eventos(query, page,[5D[K
page, config['timeout'], config))
            tasks.append(task)

        results = await asyncio.gather(*tasks)

        events = [event for sublist in results for event in sublist]
        duplicates = {event['nombre']: event for event in set(tuple(event.i[17D[K
set(tuple(event.items()) for event in events)}
        unique_events = list(duplicates.values())

        await browser.close()

    return unique_events

# Ejemplo de uso
config_example = {
    "subgeneros": ["psytrance", "goa"],
    "paises": ["españa", "alemania", "portugal"],
    "ciudades": ["berlin", "amsterdam"],
    "comunidades": ["comunidad1", "comunidad2"],
    "timeout": 60 * 5  # 5 minutos
}

async def main():
    events = await scrape_facebook_public(config_example)
    print(events)

# asyncio.run(main())
```

### PASOS PARA APLICAR EL CAMBIO

1. **Reemplazar el archivo `scrapers/facebook_public.py`** con el código pr[2D[K
proporcionado.

2. **Asegurarse de que la configuración en `config_temp.json` sea correcta*[9D[K
correcta**, incluyendo subgéneros, países, ciudades y comunidades.

3. **Ejecutar el scraper** utilizando la función `asyncio.run(main())`.

4. **Revisar los resultados** para asegurar que se están extrayendo eventos[7D[K
eventos correctamente y sin errores.

5. **Ajustar las consultas y lógica según sea necesario** basándose en los [K
resultados obtenidos.

6. **Documentar cualquier cambio o ajuste realizado** para futuras referenc[8D[K
referencias.

