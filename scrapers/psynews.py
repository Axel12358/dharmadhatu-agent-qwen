import re
import time
import requests
from bs4 import BeautifulSoup

def scrape_psynews(limit=100):
    eventos = []
    url = "https://www.psynews.org/forums/forum/20-events/"
    
    try:
        response = requests.get(url, timeout=30)
        soup = BeautifulSoup(response.text, 'html.parser')
        items = soup.find_all('li', class_=re.compile(r'thread|topic', re.I))
        for item in items[:limit]:
            try:
                nombre = item.find('a', class_=re.compile(r'title|subject', re.I))
                if nombre:
                    eventos.append({
                        'nombre': nombre.text.strip(),
                        'fuente': 'Psynews',
                        'fecha': 'N/A',
                        'lugar': 'N/A',
                        'pais': 'N/A',
                        'organizador': 'N/A',
                        'email': 'N/A'
                    })
            except:
                continue
        print(f"✅ Psynews: {len(eventos)} eventos")
    except Exception as e:
        print(f"❌ Error en Psynews: {e}")
    
    return eventos
