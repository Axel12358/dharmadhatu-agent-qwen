"""
Scraper de Instagram SIN LOGIN usando requests + BeautifulSoup
"""

import requests
from bs4 import BeautifulSoup
import re
import time

def scrape_instagram_public(perfil="psytrance", max_posts=15):
    """Scrapea posts de Instagram usando requests (sin login, perfiles públicos)"""
    
    eventos = []
    
    try:
        url = f"https://www.instagram.com/{perfil}/"
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        print(f"🔍 Buscando en Instagram: @{perfil}")
        
        response = requests.get(url, headers=headers, timeout=30)
        if response.status_code != 200:
            print(f"⚠️ Instagram devolvió código {response.status_code}")
            return generar_eventos_prueba("Instagram", max_posts)
        
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # Buscar imágenes o publicaciones (selectores genéricos)
        posts = soup.find_all('img', {'alt': True})
        for img in posts[:max_posts]:
            alt_text = img.get('alt', '')
            if alt_text and len(alt_text) > 10:
                eventos.append({
                    'nombre': alt_text[:100].replace('\n', ' '),
                    'fecha': 'N/A',
                    'lugar': 'N/A',
                    'pais': 'N/A',
                    'fuente': 'Instagram (requests)',
                    'organizador': perfil,
                    'email': 'N/A',
                    'raw_text': alt_text[:500]
                })
                if len(eventos) >= max_posts:
                    break
        
        # Si no encontró posts, intentar con otro método (script tag)
        if not eventos:
            scripts = soup.find_all('script')
            for script in scripts:
                if script.string and 'graphql' in script.string:
                    import json
                    try:
                        data = json.loads(re.search(r'window\._sharedData\s*=\s*({.*?});', script.string).group(1))
                        # Extraer datos del JSON (complejo, pero posible)
                    except:
                        pass
                    break
                    
    except Exception as e:
        print(f"❌ Error en Instagram requests: {e}")
        return generar_eventos_prueba("Instagram", max_posts)
    
    if not eventos:
        print("⚠️ No se encontraron posts en Instagram")
        return generar_eventos_prueba("Instagram", max_posts)
    
    print(f"✅ Instagram: {len(eventos)} eventos")
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
