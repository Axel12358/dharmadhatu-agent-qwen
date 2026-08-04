import requests
from bs4 import BeautifulSoup

def scrape_songkick(limit=100):
    """Scraper para Songkick - busca eventos psytrance"""
    eventos = []
    seen = set()
    
    # Buscar en diferentes términos para más cobertura
    search_terms = ["psytrance", "psychedelic trance", "goa trance"]
    
    for term in search_terms:
        if len(eventos) >= limit:
            break
            
        url = f"https://www.songkick.com/search?query={term}&type=events"
        try:
            print(f"🎵 Buscando en Songkick: '{term}'...")
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            }
            response = requests.get(url, headers=headers, timeout=30)
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # Buscar eventos - la estructura real usa li con clases que contienen "event"
            # Ejemplo: <li class="festival-instance event">
            items = soup.find_all('li', class_=lambda x: x and 'event' in x)
            
            for item in items:
                if len(eventos) >= limit:
                    break
                    
                try:
                    # Verificar que es un evento (no artista o venue)
                    item_classes = item.get('class', [])
                    if 'artist' in item_classes or 'venue' in item_classes:
                        continue
                    
                    # Nombre del evento: buscar en p.summary a strong o strong directamente
                    nombre = "Sin nombre"
                    summary_tag = item.find('p', class_='summary')
                    if summary_tag:
                        strong_tag = summary_tag.find('strong')
                        if strong_tag:
                            nombre = strong_tag.get_text(strip=True)
                    else:
                        # Fallback: buscar cualquier strong dentro del item
                        strong_tag = item.find('strong')
                        if strong_tag:
                            nombre = strong_tag.get_text(strip=True)
                    
                    if nombre == "Sin nombre" or not nombre:
                        continue
                    
                    # Fecha: time[datetime]
                    fecha = "N/A"
                    time_tag = item.find('time')
                    if time_tag:
                        fecha = time_tag.get_text(strip=True)
                    
                    # Ubicación: p.location
                    lugar = "N/A"
                    pais = "N/A"
                    location_tag = item.find('p', class_='location')
                    if location_tag:
                        lugar_text = location_tag.get_text(strip=True)
                        # La ubicación viene como "Ciudad, Estado, País"
                        parts = [p.strip() for p in lugar_text.split(',')]
                        if len(parts) >= 2:
                            lugar = parts[0]
                            pais = parts[-1]  # Último elemento es el país
                        elif len(parts) == 1:
                            lugar = parts[0]
                    
                    # URL del evento
                    evento_url = "N/A"
                    link_tag = item.find('a', href=True)
                    if link_tag:
                        href = link_tag.get('href', '')
                        if '/concerts/' in href or '/festivals/' in href:
                            evento_url = f"https://www.songkick.com{href}"
                    
                    # Organizador: usar venue como organizador (venues suelen organizar)
                    organizador = lugar if lugar != "N/A" else "N/A"
                    
                    # Evitar duplicados
                    key = f"{nombre}_{fecha}"
                    if key in seen:
                        continue
                    seen.add(key)
                    
                    eventos.append({
                        'nombre': nombre,
                        'fecha': fecha,
                        'lugar': lugar,
                        'pais': pais,
                        'fuente': 'Songkick',
                        'organizador': organizador,
                        'email': evento_url if evento_url != "N/A" else 'N/A',
                        'link': evento_url if evento_url != "N/A" else 'N/A'
                    })
                    
                except Exception as e:
                    continue
            
            print(f"   ✅ {len(eventos)} eventos encontrados con '{term}'")
            
        except Exception as e:
            print(f"   ❌ Error con '{term}': {e}")
            continue
    
    print(f"✅ Songkick total: {len(eventos)} eventos")
    return eventos
