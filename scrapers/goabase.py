import re
import time
import requests
from bs4 import BeautifulSoup

def scrape_goabase(limit=100):
    """Scraper para Goabase.net"""
    
    eventos = []
    url = "https://www.goabase.net/party/"
    
    try:
        print(f"🌐 Conectando a Goabase...")
        response = requests.get(url, timeout=30)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # Buscar eventos en la página
        items = soup.find_all('div', class_='party-item') or soup.find_all('div', class_='event-item')
        
        if not items:
            items = soup.find_all('div', class_=re.compile(r'party|event|festival|item', re.I))
        
        for item in items[:limit]:
            try:
                nombre_tag = item.find('h3') or item.find('h2') or item.find('a')
                nombre = nombre_tag.text.strip() if nombre_tag else "Sin nombre"
                
                fecha_tag = item.find('span', class_=re.compile(r'date|fecha', re.I)) or item.find('time')
                fecha = fecha_tag.text.strip() if fecha_tag else "N/A"
                
                lugar_tag = item.find('span', class_=re.compile(r'place|location|city|ciudad', re.I))
                lugar = lugar_tag.text.strip() if lugar_tag else "N/A"
                
                eventos.append({
                    'nombre': nombre,
                    'fecha': fecha,
                    'lugar': lugar,
                    'pais': 'N/A',
                    'fuente': 'Goabase',
                    'organizador': 'N/A',
                    'email': 'N/A'
                })
                
                if len(eventos) >= limit:
                    break
                    
            except Exception as e:
                continue
        
        print(f"✅ Goabase: {len(eventos)} eventos encontrados")
        
    except Exception as e:
        print(f"❌ Error en Goabase: {e}")
    
    return eventos
