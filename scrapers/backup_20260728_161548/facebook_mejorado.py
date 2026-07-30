"""
Scraper de Facebook SIN LOGIN usando Playwright (mejorado)
"""

from playwright.sync_api import sync_playwright
import time
import random

def scrape_facebook_mejorado(query="psytrance festival", max_posts=30):
    """
    Scraper de Facebook sin login usando Playwright con headless=False,
    scroll dinámico, cierre de modales y extracción de datos.
    """
    eventos = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)  # Visible para evitar detección
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            viewport={'width': 1280, 'height': 720}
        )
        page = context.new_page()

        # Ir a la URL de búsqueda de eventos
        url = f"https://www.facebook.com/events/search/?q={query.replace(' ', '%20')}"
        print(f"🌐 Navegando a: {url}")
        page.goto(url, timeout=60000)
        time.sleep(5)

        # Cerrar modal de login si aparece
        try:
            page.click('div[aria-label="Close"]', timeout=3000)
            print("✅ Modal de login cerrado")
        except:
            pass

        # Scroll dinámico para cargar eventos
        eventos_encontrados = 0
        scrolls = 0
        max_scrolls = 10

        while eventos_encontrados < max_posts and scrolls < max_scrolls:
            # Scroll al final de la página
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            time.sleep(random.uniform(2, 4))  # Pausa aleatoria para evitar detección
            scrolls += 1

            # Buscar eventos con selectores genéricos
            items = page.query_selector_all('div[role="article"]')
            if not items:
                items = page.query_selector_all('div[class*="event"]')
            if not items:
                items = page.query_selector_all('a[href*="/events/"]')

            for item in items:
                try:
                    texto = item.inner_text()
                    if not texto or len(texto) < 20:
                        continue

                    lineas = texto.split('\n')
                    titulo = lineas[0] if lineas else "Sin título"
                    fecha = "N/A"
                    lugar = "N/A"

                    for linea in lineas[1:10]:
                        # Detectar fecha (meses en español/inglés)
                        if any(mes in linea.lower() for mes in ['enero', 'febrero', 'marzo', 'abril', 'mayo', 'junio', 'julio', 'agosto', 'septiembre', 'octubre', 'noviembre', 'diciembre', 'january', 'february', 'march', 'april', 'may', 'june', 'july', 'august', 'september', 'october', 'november', 'december']):
                            fecha = linea.strip()
                        # Detectar lugar
                        if any(palabra in linea.lower() for palabra in ['calle', 'avenida', 'plaza', 'centro', 'park', 'street', 'avenue']):
                            lugar = linea.strip()

                    if len(titulo) > 5:
                        eventos.append({
                            'nombre': titulo[:100],
                            'fecha': fecha[:50],
                            'lugar': lugar[:50],
                            'pais': 'N/A',
                            'fuente': 'Facebook',
                            'organizador': 'N/A',
                            'email': 'N/A',
                            'raw_text': texto[:500]
                        })
                        eventos_encontrados += 1

                    if eventos_encontrados >= max_posts:
                        break
                except:
                    continue

        browser.close()

    # Si no se encontraron eventos reales, generar datos de prueba
    if not eventos:
        print("⚠️ No se encontraron eventos reales. Generando eventos de prueba...")
        for i in range(min(max_posts, 20)):
            eventos.append({
                'nombre': f"Evento Facebook {i+1}",
                'fecha': 'N/A',
                'lugar': 'N/A',
                'pais': 'N/A',
                'fuente': 'Facebook (prueba)',
                'organizador': 'N/A',
                'email': 'N/A',
                'raw_text': ''
            })

    return eventos
