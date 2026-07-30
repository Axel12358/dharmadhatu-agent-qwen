#!/bin/bash
echo "🔧 CORRIGIENDO ARQUITECTURA - SUBAGENTES SEPARADOS"
echo "===================================================="

cd ~/dharmadhatu_agent_qwen || exit

# 1. BACKUP DE ARCHIVOS ACTUALES
echo "📂 Creando backup..."
mkdir -p backup_$(date +%Y%m%d_%H%M%S)
cp scrapers/facebook_public.py scrapers/grupos_buscador.py scrapers/eventos_directos.py backup_$(date +%Y%m%d_%H%M%S)/ 2>/dev/null

# 2. INSTALAR DEPENDENCIAS SI FALTAN
echo "📦 Instalando dependencias..."
pip install playwright beautifulsoup4 lxml playwright-stealth --quiet

# 3. SOBRESCRIBIR grupos_buscador.py (SIN tipos de evento en búsqueda de grupos)
cat > scrapers/grupos_buscador.py << 'GRUPOS'
import asyncio
import random
import logging
import re
from playwright.async_api import async_playwright
from bs4 import BeautifulSoup

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TIPOS_EVENTO = ["festival", "party", "gathering", "rave", "outdoor", "indoor", "camp", "retreat", "open air"]

def generar_queries_grupos(config):
    """Genera queries para GRUPOS: subgénero + país + ciudad (SIN tipos de evento)."""
    queries = []
    for sub in config.get("subgeneros", []):
        for pais in config.get("paises", []):
            queries.append(f"{sub} {pais}")
        for ciudad in config.get("ciudades", []):
            queries.append(f"{sub} {ciudad}")
        for pais in config.get("paises", []):
            for ciudad in config.get("ciudades", []):
                queries.append(f"{sub} {pais} {ciudad}")
    random.shuffle(queries)
    return queries[:50]

async def buscar_grupos_facebook(query, page, timeout):
    grupos_urls = []
    try:
        url = f"https://www.facebook.com/search/groups/?q={query.replace(' ', '%20')}"
        await page.goto(url, timeout=timeout * 1000, wait_until='networkidle')
        await asyncio.sleep(random.uniform(2, 4))
        links = await page.query_selector_all('a[href*="/groups/"]')
        for link in links:
            href = await link.get_attribute('href')
            if href and '/groups/' in href and 'search' not in href:
                grupo_url = f"https://www.facebook.com{href}" if href.startswith('/') else href
                if grupo_url not in grupos_urls:
                    grupos_urls.append(grupo_url)
    except Exception as e:
        logger.error(f"Error en búsqueda de grupos: {e}")
    return grupos_urls

async def extraer_eventos_de_grupo(grupo_url, page, timeout):
    eventos = []
    try:
        await page.goto(grupo_url, timeout=timeout * 1000, wait_until='networkidle')
        await asyncio.sleep(random.uniform(2, 4))
        try:
            await page.click('span[dir="auto"]:has-text("Eventos")')
            await asyncio.sleep(2)
        except:
            pass
        for _ in range(3):
            await page.evaluate('window.scrollBy(0, window.innerHeight)')
            await asyncio.sleep(random.uniform(1, 2))
        html = await page.content()
        soup = BeautifulSoup(html, 'html.parser')
        elementos = soup.select('div[role="article"]')
        if not elementos:
            elementos = soup.select('div[data-testid="event-card"]')
        for elem in elementos[:10]:
            texto = elem.get_text(separator=" ", strip=True)
            if not texto or len(texto) < 20:
                continue
            # FILTRO: solo si contiene algún tipo de evento
            if not any(t in texto.lower() for t in TIPOS_EVENTO):
                continue
            nombre = "Sin nombre"
            nombre_elem = elem.find('span', {'dir': 'auto'})
            if nombre_elem:
                nombre = nombre_elem.get_text(strip=True)
            organizador = "No disponible"
            org_elem = elem.find('span', {'data-testid': 'event-card-host'})
            if org_elem:
                organizador = org_elem.get_text(strip=True).replace('Hosted by', '').replace('Organizado por', '').strip()
            else:
                match = re.search(r'Hosted by\s*([^\n]+)', texto, re.IGNORECASE)
                if match:
                    organizador = match.group(1).strip()
            enlace = ""
            enlace_elem = elem.find('a', href=True)
            if enlace_elem and '/events/' in enlace_elem.get('href', ''):
                enlace = enlace_elem.get('href')
                if enlace and not enlace.startswith('http'):
                    enlace = f"https://www.facebook.com{enlace}"
            if nombre and nombre != "Sin nombre":
                eventos.append({
                    "nombre": nombre.strip(),
                    "fecha": "Fecha no disponible",
                    "lugar": "Lugar no disponible",
                    "organizador": organizador,
                    "enlace": enlace,
                    "fuente": "Facebook (grupo)"
                })
    except Exception as e:
        logger.warning(f"Error en grupo {grupo_url}: {e}")
    return eventos

async def buscar_grupos_y_eventos(config):
    queries = generar_queries_grupos(config)
    timeout = 30
    semaphore = asyncio.Semaphore(5)
    grupos_encontrados = []
    eventos = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=['--no-sandbox'])
        context = await browser.new_context(
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        )
        async def buscar_grupos(query):
            async with semaphore:
                page = await context.new_page()
                try:
                    return await buscar_grupos_facebook(query, page, timeout)
                finally:
                    await page.close()
        tareas_grupos = [buscar_grupos(q) for q in queries]
        resultados_grupos = await asyncio.gather(*tareas_grupos, return_exceptions=True)
        for res in resultados_grupos:
            if isinstance(res, list):
                grupos_encontrados.extend(res)
        grupos_unicos = list(set(grupos_encontrados))
        logger.info(f"✅ {len(grupos_unicos)} grupos encontrados")
        async def extraer(grupo_url):
            async with semaphore:
                page = await context.new_page()
                try:
                    return await extraer_eventos_de_grupo(grupo_url, page, timeout)
                finally:
                    await page.close()
        tareas_eventos = [extraer(g) for g in grupos_unicos[:15]]
        resultados_eventos = await asyncio.gather(*tareas_eventos, return_exceptions=True)
        for res in resultados_eventos:
            if isinstance(res, list):
                eventos.extend(res)
        await browser.close()
    return eventos
GRUPOS

# 4. SOBRESCRIBIR eventos_directos.py (CON tipos de evento en queries)
cat > scrapers/eventos_directos.py << 'EVENTOS'
import asyncio
import random
import logging
import re
from playwright.async_api import async_playwright
from bs4 import BeautifulSoup

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def generar_queries_eventos(config):
    queries = []
    for sub in config.get("subgeneros", []):
        for tipo in config.get("tipos_evento", []):
            queries.append(f"{sub} {tipo}")
            for pais in config.get("paises", []):
                queries.append(f"{sub} {tipo} {pais}")
            for ciudad in config.get("ciudades", []):
                queries.append(f"{sub} {tipo} {ciudad}")
            for pais in config.get("paises", []):
                for ciudad in config.get("ciudades", []):
                    queries.append(f"{sub} {tipo} {pais} {ciudad}")
    random.shuffle(queries)
    return queries[:50]

async def buscar_eventos_directos_facebook(query, page, timeout):
    eventos = []
    try:
        url = f"https://www.facebook.com/search/events/?q={query.replace(' ', '%20')}"
        await page.goto(url, timeout=timeout * 1000, wait_until='networkidle')
        await asyncio.sleep(random.uniform(2, 4))
        for _ in range(3):
            await page.evaluate('window.scrollBy(0, window.innerHeight)')
            await asyncio.sleep(random.uniform(1, 2))
        html = await page.content()
        soup = BeautifulSoup(html, 'html.parser')
        elementos = soup.select('div[role="article"]')
        if not elementos:
            elementos = soup.select('div[data-testid="event-card"]')
        for elem in elementos[:10]:
            texto = elem.get_text(separator=" ", strip=True)
            if not texto or len(texto) < 20:
                continue
            nombre = "Sin nombre"
            nombre_elem = elem.find('span', {'dir': 'auto'})
            if nombre_elem:
                nombre = nombre_elem.get_text(strip=True)
            organizador = "No disponible"
            org_elem = elem.find('span', {'data-testid': 'event-card-host'})
            if org_elem:
                organizador = org_elem.get_text(strip=True).replace('Hosted by', '').replace('Organizado por', '').strip()
            else:
                match = re.search(r'Hosted by\s*([^\n]+)', texto, re.IGNORECASE)
                if match:
                    organizador = match.group(1).strip()
            enlace = ""
            enlace_elem = elem.find('a', href=True)
            if enlace_elem and '/events/' in enlace_elem.get('href', ''):
                enlace = enlace_elem.get('href')
                if enlace and not enlace.startswith('http'):
                    enlace = f"https://www.facebook.com{enlace}"
            if nombre and nombre != "Sin nombre":
                eventos.append({
                    "nombre": nombre.strip(),
                    "fecha": "Fecha no disponible",
                    "lugar": "Lugar no disponible",
                    "organizador": organizador,
                    "enlace": enlace,
                    "fuente": "Facebook (directo)"
                })
    except Exception as e:
        logger.error(f"Error en búsqueda directa: {e}")
    return eventos

async def buscar_eventos_directos(config):
    queries = generar_queries_eventos(config)
    timeout = 30
    semaphore = asyncio.Semaphore(5)
    eventos = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=['--no-sandbox'])
        context = await browser.new_context(
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        )
        async def trabajar(query):
            async with semaphore:
                page = await context.new_page()
                try:
                    return await buscar_eventos_directos_facebook(query, page, timeout)
                finally:
                    await page.close()
        tareas = [trabajar(q) for q in queries]
        resultados = await asyncio.gather(*tareas, return_exceptions=True)
        for res in resultados:
            if isinstance(res, list):
                eventos.extend(res)
        await browser.close()
    return eventos
EVENTOS

# 5. SOBRESCRIBIR facebook_public.py (orquestador)
cat > scrapers/facebook_public.py << 'ORQUESTADOR'
import json
import logging
from .grupos_buscador import buscar_grupos_y_eventos
from .eventos_directos import buscar_eventos_directos

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def cargar_config_grupos():
    with open('config_grupos.json', 'r') as f:
        return json.load(f)

def cargar_config_eventos():
    with open('config_eventos.json', 'r') as f:
        return json.load(f)

async def scrape_facebook_public(config=None):
    """Orquesta los dos subagentes con configuraciones separadas."""
    logger.info("🔍 Iniciando scraping de Facebook (grupos + eventos directos)")

    # Cargar configuraciones específicas
    config_grupos = cargar_config_grupos()
    config_eventos = cargar_config_eventos()

    # Verificar que cada configuración tiene los campos correctos
    if 'tipos_evento' in config_grupos:
        logger.warning("⚠️ config_grupos.json contiene 'tipos_evento' - no debería. Se ignorará.")
    if not config_eventos.get('tipos_evento'):
        logger.error("❌ config_eventos.json debe contener 'tipos_evento'")

    # Ejecutar subagentes en paralelo
    import asyncio
    resultados = await asyncio.gather(
        buscar_grupos_y_eventos(config_grupos),
        buscar_eventos_directos(config_eventos),
        return_exceptions=True
    )

    eventos_grupos = resultados[0] if isinstance(resultados[0], list) else []
    eventos_directos = resultados[1] if isinstance(resultados[1], list) else []

    # Consolidar
    todos = eventos_grupos + eventos_directos
    unicos = []
    seen = set()
    for e in todos:
        key = (e.get("nombre", ""), e.get("organizador", ""))
        if key not in seen and e.get("nombre") and e["nombre"] != "Sin nombre":
            seen.add(key)
            unicos.append(e)

    logger.info(f"✅ Facebook TOTAL: {len(unicos)} eventos (grupos: {len(eventos_grupos)}, directos: {len(eventos_directos)})")
    return unicos
ORQUESTADOR

# 6. CREAR main.py (punto de entrada)
cat > main.py << 'MAIN'
import asyncio
import logging
from scrapers.facebook_public import scrape_facebook_public
from scrapers.goabase import scrape_goabase
from scrapers.songkick import scrape_songkick

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def main():
    config = {
        "max_eventos": 150,
        "busquedas_facebook": 50,
        "timeout": 30,
        "extraer_contactos": True
    }

    print("🧘 DHARMADHATU BOT v5 (ARQUITECTURA SEPARADA)")
    print("=" * 50)

    resultados = await asyncio.gather(
        scrape_goabase(150),
        scrape_facebook_public(config),
        scrape_songkick(150),
        return_exceptions=True
    )

    eventos = []
    for res in resultados:
        if isinstance(res, list):
            eventos.extend(res)
        elif isinstance(res, Exception):
            logger.error(f"Error en scraper: {res}")

    print(f"\n✅ Total eventos: {len(eventos)}")
    print("=" * 50)

if __name__ == "__main__":
    asyncio.run(main())
MAIN

echo "✅ Archivos corregidos generados."
echo ""
echo "📂 Archivos actualizados:"
echo "   - scrapers/grupos_buscador.py"
echo "   - scrapers/eventos_directos.py"
echo "   - scrapers/facebook_public.py"
echo "   - main.py"
echo ""
echo "🚀 Para ejecutar el bot: python3 main.py"
echo ""
echo "🔚 FIN"
