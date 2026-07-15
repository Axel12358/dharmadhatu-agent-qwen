"""
Scraper de Facebook SIN LOGIN usando Playwright
"""

from playwright.sync_api import sync_playwright
import time

def scrape_facebook_public(query="psytrance festival", max_posts=15):
    """Scrapea eventos de Facebook usando Playwright (sin login)"""
    
    eventos = []
    
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=False)
            context = browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                viewport={'width': 1280, 'height': 720}
            )
            page = context.new_page()
            
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
            
            # Scroll para cargar más
            for _ in range(3):
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                time.sleep(2)
            
            # Extraer eventos
            items = page.query_selector_all('div[role="article"]')
            print(f"📌 Eventos encontrados: {len(items)}")
            
            for item in items[:max_posts]:
                try:
                    texto = item.inner_text()
                    lineas = texto.split('\n')
                    titulo = lineas[0] if lineas else "Sin título"
                    fecha = "N/A"
                    lugar = "N/A"
                    
                    for linea in lineas[1:10]:
                        if any(mes in linea.lower() for mes in ['enero', 'febrero', 'marzo', 'abril', 'mayo', 'junio', 'julio', 'agosto', 'septiembre', 'octubre', 'noviembre', 'diciembre']):
                            fecha = linea
                        if any(palabra in linea.lower() for palabra in ['calle', 'avenida', 'plaza', 'centro', 'park', 'street']):
                            lugar = linea
                    
                    if len(titulo) > 5:
                        eventos.append({
                            'nombre': titulo[:100],
                            'fecha': fecha[:30],
                            'lugar': lugar[:50],
                            'pais': 'N/A',
                            'fuente': 'Facebook (Playwright)',
                            'organizador': 'N/A',
                            'email': 'N/A',
                            'raw_text': texto[:500]
                        })
                        if len(eventos) >= max_posts:
                            break
                except:
                    continue
            
            browser.close()
            
    except Exception as e:
        print(f"❌ Error en Facebook Playwright: {e}")
        return generar_eventos_prueba("Facebook", max_posts)
    
    if not eventos:
        print("⚠️ No se encontraron eventos reales en Facebook")
        return generar_eventos_prueba("Facebook", max_posts)
    
    print(f"✅ Facebook: {len(eventos)} eventos")
    return eventos

def generar_eventos_prueba(fuente, max_posts=10):
    """Genera eventos de prueba si el scraper falla"""
    eventos = []
    for i in range(min(max_posts, 10)):
        eventos.append({
            'nombre': f"{fuente} - Evento de prueba {i+1}",
            'fecha': 'N/A',
            'lugar': 'N/A',
            'pais': 'N/A',
            'fuente': f"{fuente} (prueba)",
            'organizador': 'N/A',
            'email': 'N/A',
            'raw_text': ''
        })
    return eventos
