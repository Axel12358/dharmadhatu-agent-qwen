import re
import time
import requests
from bs4 import BeautifulSoup

def scrape_goabase(limit=100):
    """Scraper para Goabase.net usando JSON API (sin login)"""
    
    eventos = []
    
    try:
        print(f"🌐 Conectando a Goabase API...")
        # 1. Obtener lista de eventos desde JSON-LD endpoint
        response = requests.get("https://www.goabase.net/api/party/jsonld/", timeout=30)
        response.raise_for_status()
        data = response.json()
        
        items = data.get("itemListElement", [])[:limit]
        print(f"   📋 {len(items)} eventos en lista")
        
        # 2. Obtener detalles de cada evento
        for i, item in enumerate(items):
            try:
                event_url = item.get("url", "")
                event_id = event_url.split("/")[-1] if event_url else None
                
                if not event_id:
                    continue
                
                # Obtener detalles del evento
                detail_url = f"https://www.goabase.net/api/party/jsonld/{event_id}"
                detail_resp = requests.get(detail_url, timeout=15)
                
                if detail_resp.status_code != 200:
                    continue
                
                event_data = detail_resp.json()
                
                nombre = event_data.get("name", "Sin nombre")
                
                # Fecha en formato ISO
                fecha_raw = event_data.get("startDate", "N/A")
                fecha = fecha_raw.split("T")[0] if "T" in fecha_raw else fecha_raw
                
                # Ubicación
                location = event_data.get("location", {})
                lugar = location.get("name", "N/A")
                address = location.get("address", {})
                pais = address.get("addressCountry", "N/A")
                ciudad = address.get("addressLocality", "")
                
                # Si el lugar es genérico, usar la ciudad
                if lugar == "N/A" and ciudad:
                    lugar = ciudad
                
                # Organizador y contacto
                organizer = event_data.get("organizer", {})
                org_name = organizer.get("name", "N/A")
                org_email = organizer.get("email", "N/A")
                org_url = organizer.get("url", "N/A")
                
                # Email: usar el del organizador, si no hay usar el del evento
                email = org_email if org_email and org_email != "N/A" else "N/A"
                
                # Si no hay email, usar URL del evento como contacto
                evento_url = event_data.get("url", "N/A")
                if email == "N/A" and org_url and org_url != "N/A":
                    email = org_url
                
                # URL canónica del evento (link)
                if evento_url and evento_url != "N/A":
                    link = evento_url
                elif event_id:
                    link = f"https://www.goabase.net/party/{event_id}"
                else:
                    link = "N/A"
                
                eventos.append({
                    'nombre': nombre,
                    'fecha': fecha,
                    'lugar': lugar,
                    'pais': pais,
                    'fuente': 'Goabase',
                    'organizador': org_name,
                    'email': email,
                    'link': link,
                    'descripcion': event_data.get('description') or "",
                })
                
                # Pequeña pausa para no sobrecargar el servidor
                if (i + 1) % 10 == 0:
                    print(f"   ✅ {i+1}/{len(items)} procesados")
                    time.sleep(0.5)
                    
            except Exception as e:
                continue
        
        print(f"✅ Goabase: {len(eventos)} eventos extraídos")
        
    except Exception as e:
        print(f"❌ Error en Goabase: {e}")
    
    return eventos