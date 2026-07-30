#!/bin/bash

echo "🔥 ARREGLANDO BOT CON CONFIGURACIÓN DEL REPOSITORIO Y GOABASE"
echo "=============================================================="

cd ~/dharmadhatu_agent_qwen

# 1. Extraer la configuración del repositorio de GitHub (versión que funcionaba)
echo "📂 Extrayendo configuración del repositorio..."
if [ -d ".git" ]; then
    # Buscar en commits anteriores la configuración que funcionaba
    git show HEAD:config_temp.json > config_repo.json 2>/dev/null || echo "{}" > config_repo.json
else
    echo "{}" > config_repo.json
fi

# 2. Extraer clasificación de Goabase
echo "🌐 Extrayendo clasificación de Goabase..."
python3 -c "
import asyncio
import json
import re
from playwright.async_api import async_playwright

async def extraer_clasificacion():
    clasificacion = {'subgeneros': set(), 'tipos_evento': set(), 'paises': set(), 'ciudades': set()}
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.goto('https://www.goabase.net/party')
        await page.wait_for_timeout(3000)
        # Extraer géneros
        generos = await page.query_selector_all('.genre, .tag, .label')
        for g in generos:
            texto = await g.inner_text()
            texto = texto.strip().lower()
            if texto and len(texto) > 2:
                clasificacion['subgeneros'].add(texto)
        # Extraer países de los eventos
        paises = await page.query_selector_all('.country, .flag, .location-country')
        for p in paises:
            texto = await p.inner_text()
            texto = texto.strip()
            if texto and len(texto) > 2:
                clasificacion['paises'].add(texto)
        # Ciudades
        ciudades = await page.query_selector_all('.city, .location-city')
        for c in ciudades:
            texto = await c.inner_text()
            texto = texto.strip()
            if texto and len(texto) > 2:
                clasificacion['ciudades'].add(texto)
        await browser.close()
    with open('clasificacion_goabase.json', 'w') as f:
        json.dump({k: sorted(list(v)) for k, v in clasificacion.items()}, f, indent=2)
    print('✅ Clasificación extraída')
asyncio.run(extraer_clasificacion())
" 2>/dev/null

# 3. Fusionar configuraciones
echo "🔀 Fusionando configuraciones..."
python3 -c "
import json

# Cargar config del repo
try:
    with open('config_repo.json', 'r') as f:
        repo_config = json.load(f)
except:
    repo_config = {}

# Cargar clasificación de Goabase
try:
    with open('clasificacion_goabase.json', 'r') as f:
        goabase = json.load(f)
except:
    goabase = {}

# Cargar config actual
try:
    with open('config_temp.json', 'r') as f:
        current = json.load(f)
except:
    current = {}

# Fusionar: priorizar lo que funcionaba en el repo
config = current.copy()
config['priorizar_paises'] = list(set(
    repo_config.get('priorizar_paises', []) +
    current.get('priorizar_paises', []) +
    goabase.get('paises', [])
))
config['subgeneros'] = list(set(
    repo_config.get('subgeneros', []) +
    current.get('subgeneros', []) +
    goabase.get('subgeneros', [])
))
config['tipos_evento'] = list(set(
    repo_config.get('tipos_evento', []) +
    current.get('tipos_evento', []) +
    goabase.get('tipos_evento', [])
))
config['ciudades'] = list(set(
    repo_config.get('ciudades', []) +
    current.get('ciudades', []) +
    goabase.get('ciudades', [])
))

# Guardar
with open('config_temp.json', 'w') as f:
    json.dump(config, f, indent=2)

print('✅ Configuración fusionada')
print(f'Países: {len(config[\"priorizar_paises\"])}')
print(f'Subgéneros: {len(config[\"subgeneros\"])}')
print(f'Tipos: {len(config[\"tipos_evento\"])}')
print(f'Ciudades: {len(config[\"ciudades\"])}')
"

# 4. Actualizar facebook_public.py para usar combinaciones correctas
echo "📝 Actualizando facebook_public.py..."
cat > scrapers/facebook_public.py << 'PYEOF'
import asyncio
import random
import logging
import re
from playwright.async_api import async_playwright

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def generar_queries_facebook(config):
    queries = []
    paises = config.get("priorizar_paises", [])
    subgeneros = config.get("subgeneros", [])
    tipos = config.get("tipos_evento", [])
    ciudades = config.get("ciudades", [])

    # Si no hay datos, usar valores por defecto
    if not subgeneros:
        subgeneros = ["psytrance", "goa", "fullon", "progressive", "darkpsy", "hitech", "suomi", "forest"]
    if not tipos:
        tipos = ["festival", "party", "gathering", "rave", "outdoor", "indoor", "camp"]

    # 1. Combinaciones con países
    if paises:
        for pais in paises:
            for sub in subgeneros:
                for tipo in tipos:
                    queries.append(f"{sub} {tipo} {pais}")
            for tipo in tipos:
                queries.append(f"{tipo} {pais}")
            for sub in subgeneros:
                queries.append(f"{sub} {pais}")
            if ciudades:
                for ciudad in ciudades:
                    for tipo in tipos:
                        queries.append(f"{tipo} {ciudad} {pais}")
                    for sub in subgeneros:
                        queries.append(f"{sub} {ciudad} {pais}")
    else:
        for sub in subgeneros:
            for tipo in tipos:
                queries.append(f"{sub} {tipo}")
        for tipo in tipos:
            queries.append(tipo)
        for sub in subgeneros:
            queries.append(sub)
        if ciudades:
            for ciudad in ciudades:
                for tipo in tipos:
                    queries.append(f"{tipo} {ciudad}")
                for sub in subgeneros:
                    queries.append(f"{sub} {ciudad}")

    if not queries:
        queries.append("psytrance festival")

    max_queries = min(config.get("busquedas_facebook", 100), 100)
    random.shuffle(queries)
    queries = queries[:max_queries]
    logger.info(f"🔍 Generadas {len(queries)} queries (combinaciones reales)")
    return queries

async def buscar_grupos_facebook(query, page, timeout):
    eventos = []
    try:
        url_grupos = f"https://www.facebook.com/search/groups/?q={query.replace(' ', '%20')}"
        await page.goto(url_grupos, timeout=timeout * 1000, wait_until='networkidle')
        await asyncio.sleep(random.uniform(1, 2))

        grupo_links = await page.query_selector_all('a[href*="/groups/"]')
        for link in grupo_links[:3]:
            try:
                href = await link.get_attribute('href')
                if not href:
                    continue
                grupo_url = f"https://www.facebook.com{href}" if href.startswith('/') else href
                await page.goto(grupo_url, timeout=timeout * 1000, wait_until='networkidle')
                await asyncio.sleep(random.uniform(1, 2))

                try:
                    await page.click('span[dir="auto"]:has-text("Eventos")')
                    await asyncio.sleep(1)
                except:
                    pass

                for _ in range(2):
                    await page.evaluate('window.scrollBy(0, window.innerHeight)')
                    await asyncio.sleep(random.uniform(0.5, 1))

                selectores = ['div[role="article"]', 'div[data-testid="event-card"]']
                elementos = []
                for selector in selectores:
                    try:
                        elementos = await page.query_selector_all(selector)
                        if elementos:
                            break
                    except:
                        continue

                if not elementos:
                    continue

                for elem in elementos[:5]:
                    try:
                        nombre = "Sin nombre"
                        for n_sel in ['span[dir="auto"]', 'span[class*="title"]', 'h2', 'h3']:
                            nombre_elem = await elem.query_selector(n_sel)
                            if nombre_elem:
                                nombre = await nombre_elem.inner_text()
                                if nombre and nombre.strip() and nombre != "Sin nombre":
                                    break

                        fecha = "Fecha no disponible"
                        for f_sel in ['span[data-testid="event-card-date"]', 'span[class*="date"]', 'span[class*="time"]']:
                            fecha_elem = await elem.query_selector(f_sel)
                            if fecha_elem:
                                fecha = await fecha_elem.inner_text()
                                if fecha and fecha.strip():
                                    break

                        lugar = "Lugar no disponible"
                        for l_sel in ['span[data-testid="event-card-location"]', 'span[class*="location"]', 'span[class*="place"]']:
                            lugar_elem = await elem.query_selector(l_sel)
                            if lugar_elem:
                                lugar = await lugar_elem.inner_text()
                                if lugar and lugar.strip():
                                    break

                        organizador = "No disponible"
                        org_elem = await elem.query_selector('span[data-testid="event-card-host"]')
                        if org_elem:
                            organizador = await org_elem.inner_text()
                            organizador = organizador.replace('Hosted by', '').replace('Organizado por', '').strip()
                        else:
                            texto_completo = await elem.inner_text()
                            match = re.search(r'Hosted by\s*([^\n]+)', texto_completo)
                            if match:
                                organizador = match.group(1).strip()
                            else:
                                match = re.search(r'Organizado por\s*([^\n]+)', texto_completo)
                                if match:
                                    organizador = match.group(1).strip()

                        enlace = ""
                        enlace_elem = await elem.query_selector('a[href*="/events/"]')
                        if enlace_elem:
                            enlace = await enlace_elem.get_attribute('href')
                            if enlace and not enlace.startswith('http'):
                                enlace = f"https://www.facebook.com{enlace}"

                        if nombre and nombre != "Sin nombre":
                            evento = {
                                "nombre": nombre.strip(),
                                "fecha": fecha.strip(),
                                "lugar": lugar.strip(),
                                "organizador": organizador,
                                "enlace": enlace,
                                "fuente": "Facebook (grupo)"
                            }
                            eventos.append(evento)
                    except Exception:
                        continue
            except Exception:
                continue
    except Exception as e:
        logger.error(f"Error en búsqueda de grupos: {e}")
    return eventos

async def scrape_facebook_public(config):
    queries = generar_queries_facebook(config)
    max_eventos = config.get("max_eventos", 150)
    timeout = config.get("timeout", 30)
    semaphore = asyncio.Semaphore(10)

    logger.info(f"🔍 Ejecutando {len(queries)} queries en grupos...")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=['--no-sandbox', '--disable-setuid-sandbox'])
        context = await browser.new_context(
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            viewport={'width': 1280, 'height': 800}
        )

        async def trabajar_con_query(query):
            async with semaphore:
                page = await context.new_page()
                try:
                    return await buscar_grupos_facebook(query, page, timeout)
                finally:
                    await page.close()

        tareas = [trabajar_con_query(query) for query in queries]
        resultados = await asyncio.gather(*tareas, return_exceptions=True)
        await browser.close()

    todos_eventos = []
    for res in resultados:
        if isinstance(res, list):
            todos_eventos.extend(res)
        elif isinstance(res, Exception):
            logger.warning(f"Error en tarea: {res}")

    eventos_filtrados = []
    seen = set()
    for e in todos_eventos:
        key = (e["nombre"], e["fecha"])
        if key not in seen and e["nombre"] != "Sin nombre" and e["nombre"].strip():
            seen.add(key)
            eventos_filtrados.append(e)
            if len(eventos_filtrados) >= max_eventos:
                break

    logger.info(f"✅ Facebook: {len(eventos_filtrados)} eventos")
    return eventos_filtrados
PYEOF

# 5. Ejecutar el bot
echo "🚀 Ejecutando bot..."
python3 main_v5.py

