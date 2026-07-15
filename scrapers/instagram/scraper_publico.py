from playwright.sync_api import sync_playwright
import time
import json
import re

def scrape_instagram_publico(hashtag="psytrance", max_posts=15):
    """
    Scraper de Instagram SIN LOGIN usando Playwright.
    Navega a un hashtag público y extrae publicaciones.
    """
    posts = []
    
    with sync_playwright() as p:
        # Modo visible para evitar detección
        browser = p.chromium.launch(headless=False)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            viewport={'width': 1280, 'height': 720}
        )
        page = context.new_page()
        
        url = f"https://www.instagram.com/explore/tags/{hashtag}/"
        print(f"🌐 Navegando a: {url}")
        
        try:
            page.goto(url, timeout=60000)
            time.sleep(4)
            
            # Scroll para cargar más publicaciones
            for _ in range(5):
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                time.sleep(2)
            
            # Extraer enlaces de publicaciones
            enlaces = page.query_selector_all('a[href*="/p/"]')
            print(f"📌 Enlaces encontrados: {len(enlaces)}")
            
            for enlace in enlaces[:max_posts]:
                try:
                    href = enlace.get_attribute('href')
                    if href and '/p/' in href:
                        posts.append({
                            'url': f"https://www.instagram.com{href}",
                            'fuente': 'Instagram',
                            'nombre': f"Post {len(posts)+1}",
                            'fecha': 'N/A',
                            'lugar': 'N/A',
                            'organizador': 'N/A',
                            'email': 'N/A'
                        })
                except:
                    continue
        except Exception as e:
            print(f"❌ Error: {e}")
        
        browser.close()
    
    # Si no hay posts, generar datos de prueba
    if not posts:
        print("⚠️ No se encontraron posts reales. Generando prueba...")
        for i in range(min(max_posts, 10)):
            posts.append({
                'nombre': f"Evento Instagram {i+1}",
                'fuente': 'Instagram (prueba)',
                'fecha': 'N/A',
                'lugar': 'N/A',
                'organizador': 'N/A',
                'email': 'N/A'
            })
    
    return posts
