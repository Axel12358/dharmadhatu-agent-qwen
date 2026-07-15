import requests
from bs4 import BeautifulSoup

def scrape_songkick(limit=100):
    eventos = []
    url = "https://www.songkick.com/search?query=psytrance"
    try:
        print(f"🎵 Buscando en Songkick...")
        response = requests.get(url, timeout=30)
        soup = BeautifulSoup(response.text, 'html.parser')
        items = soup.find_all('li', class_='event') or soup.find_all('div', class_='event')
        for item in items[:limit]:
            try:
                nombre = item.find('h2').text.strip() if item.find('h2') else "Sin nombre"
                fecha = item.find('span', class_='date').text.strip() if item.find('span', class_='date') else "N/A"
                lugar = item.find('span', class_='location').text.strip() if item.find('span', class_='location') else "N/A"
                eventos.append({
                    'nombre': nombre,
                    'fecha': fecha,
                    'lugar': lugar,
                    'pais': 'N/A',
                    'fuente': 'Songkick',
                    'organizador': 'N/A',
                    'email': 'N/A'
                })
            except:
                continue
        print(f"✅ Songkick: {len(eventos)} eventos")
    except Exception as e:
        print(f"❌ Songkick: {e}")
    return eventos