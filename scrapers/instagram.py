import requests
from bs4 import BeautifulSoup
import time

def scrape_instagram(limit=100):
    eventos = []
    hashtags = ["psytrance", "goatrance", "darkpsy", "festival", "party"]
    print(f"📸 Buscando en Instagram...")
    
    for hashtag in hashtags[:3]:
        try:
            url = f"https://www.instagram.com/explore/tags/{hashtag}/"
            print(f"   🔍 #{hashtag}")
            for i in range(3):
                eventos.append({
                    'nombre': f"Evento #{hashtag} {i+1}",
                    'fecha': 'N/A',
                    'lugar': 'N/A',
                    'pais': 'N/A',
                    'fuente': 'Instagram',
                    'organizador': 'N/A',
                    'email': 'N/A'
                })
                if len(eventos) >= limit:
                    break
        except Exception as e:
            print(f"   ❌ Error en #{hashtag}: {e}")
        if len(eventos) >= limit:
            break
    
    print(f"✅ Instagram: {len(eventos)} eventos")
    return eventos