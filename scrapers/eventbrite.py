import requests
from bs4 import BeautifulSoup

def scrape_eventbrite(limit=100):
    eventos = []
    url = "https://www.eventbrite.com/d/search/?q=psytrance"
    try:
        print(f"🎫 Buscando en Eventbrite...")
        response = requests.get(url, timeout=30)
        soup = BeautifulSoup(response.text, 'html.parser')
        items = soup.find_all('div', class_='event-card') or soup.find_all('article', class_='event')
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
                    'fuente': 'Eventbrite',
                    'organizador': 'N/A',
                    'email': 'N/A'
                })
            except:
                continue
        print(f"✅ Eventbrite: {len(eventos)} eventos")
    except Exception as e:
        print(f"❌ Eventbrite: {e}")
    return eventos