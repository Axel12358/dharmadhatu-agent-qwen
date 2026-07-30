"""
Dharmadhatu Bot - Groups Global Scraper (sync version)
Adaptado de temp_dharmadhatu/groups_global.py
"""

import time
import os
import json
import re
import urllib.parse
import random
from playwright.sync_api import sync_playwright

# === LISTAS COMPLETAS (extraídas de temp_dharmadhatu/config.py) ===
PAISES = [
    "Germany", "France", "Spain", "Italy", "Portugal",
    "Netherlands", "Belgium", "Switzerland", "Austria",
    "Czech", "Poland", "Hungary", "Romania", "Bulgaria",
    "Greece", "Croatia", "Serbia", "Slovenia", "Slovakia",
    "UK", "England", "Scotland", "Ireland",
    "Sweden", "Norway", "Finland", "Denmark",
    "Lithuania", "Latvia", "Estonia", "United Kingdom"
]

GENEROS = [
    "darkpsy", "dark psy", "forest psy", "forest", "ritual",
    "psytrance", "goa trance", "goa", "hitech", "suomi",
    "twilight", "psytech", "progressive psy", "psychill", "psydub"
]

TIPOS = [
    "festival", "open air", "outdoor", "indoor", "club",
    "party", "rave", "gathering", "weekender", "retreat",
    "burner", "camp", "warehouse", "ritual"
]

CIUDADES = [
    "Berlin", "Hamburg", "Munich", "London", "Manchester",
    "Paris", "Lyon", "Madrid", "Barcelona", "Valencia",
    "Rome", "Milan", "Amsterdam", "Rotterdam", "Brussels",
    "Vienna", "Prague", "Budapest", "Warsaw", "Lisbon",
    "Stockholm", "Copenhagen", "Athens", "Dublin", "Zurich"
]

KEYWORDS = [
    "festival", "party", "open air", "gathering", "rave",
    "booking", "contact", "email", "line up", "artist",
    "psytrance", "forest", "goa", "darkpsy"
]
EMAIL_REGEX = r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}"

def generar_queries(config):
    """Genera queries combinando géneros, tipos, países y ciudades."""
    queries = []
    
    # Combinaciones estilo groups_global.py
    for genero in random.sample(GENEROS, min(8, len(GENEROS))):
        for tipo in random.sample(TIPOS, min(5, len(TIPOS))):
            queries.append(f"{genero} {tipo}")
            for pais in random.sample(PAISES, min(3, len(PAISES))):
                queries.append(f"{genero} {tipo} {pais}")
    
    # Combinaciones con ciudades (estilo fb_combinations.py)
    for genero in random.sample(GENEROS, min(5, len(GENEROS))):
        for tipo in random.sample(TIPOS, min(3, len(TIPOS))):
            for ciudad in random.sample(CIUDADES, min(2, len(CIUDADES))):
                queries.append(f"{genero} {tipo} {ciudad}")
    
    # Búsquedas fijas
    fijas = [
        "psytrance festival europe",
        "goa party",
        "psytrance open air",
        "goa trance europe",
        "psy rave",
        "ritual psytrance",
        "dark ritual gathering",
        "forest ritual party"
    ]
    queries.extend(fijas)
    
    # Limpiar duplicados y limitar
    queries = list(dict.fromkeys(queries))
    max_queries = config.get("busquedas_facebook", 50)
    random.shuffle(queries)
    return queries[:max_queries]

def scrape_facebook_groups(config):
    """Función principal sync (ejecutada con asyncio.to_thread)."""
    queries = generar_queries(config)
    print(f"🔍 Generadas {len(queries)} queries (sync)")
    
    # Estado de login (si no existe, se intenta sin login)
    state_file = "fb_state.json"
    if not os.path.exists(state_file):
        print("⚠️ No hay estado de login. Intentando sin login...")
    
    events = []
    seen_groups = set()
    seen_posts = set()
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=['--no-sandbox'])
        context = None
        
        if os.path.exists(state_file):
            with open(state_file, "r") as f:
                state = json.load(f)
            context = browser.new_context(storage_state=state)
            print("📂 Estado de login cargado")
        else:
            context = browser.new_context(
                user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
            )
        
        page = context.new_page()
        
        for i, query in enumerate(queries):
            print(f"[{i+1}/{len(queries)}] 🔎 Buscando grupos: {query}")
            q = urllib.parse.quote(query)
            url = f"https://www.facebook.com/search/groups/?q={q}"
            
            try:
                page.goto(url, timeout=30000)
                time.sleep(random.uniform(3, 5))
                cerrar_modales(page)
                
                group_links = page.query_selector_all("a[href*='/groups/']")
                group_urls = []
                for el in group_links:
                    href = el.get_attribute("href")
                    if href and "/groups/" in href and "search" not in href:
                        clean = href.split("?")[0]
                        if not clean.startswith("http"):
                            clean = f"https://www.facebook.com{clean}"
                        if clean not in seen_groups:
                            seen_groups.add(clean)
                            group_urls.append(clean)
                
                print(f"   → {len(group_urls)} grupos nuevos")
                
                for group_url in group_urls[:3]:
                    group_page = context.new_page()
                    try:
                        group_events = scrape_group_publications(group_page, group_url, query)
                        for e in group_events:
                            post_id = e.get("facebook", "")
                            if post_id and post_id not in seen_posts:
                                seen_posts.add(post_id)
                                events.append(e)
                    except Exception as e:
                        print(f"      ⚠ Error: {e}")
                    finally:
                        group_page.close()
                        time.sleep(random.uniform(1, 2))
                
            except Exception as e:
                print(f"   ⚠ Error: {e}")
                continue
            
            # Pausa aleatoria entre queries
            time.sleep(random.uniform(1, 3))
        
        browser.close()
    
    print(f"✅ Facebook (sync): {len(events)} eventos extraídos")
    return events

def scrape_group_publications(page, group_url, query):
    """Extrae publicaciones de un grupo."""
    print(f"      📂 Entrando: {group_url}")
    events = []
    try:
        page.goto(group_url, timeout=30000)
        time.sleep(random.uniform(3, 5))
        cerrar_modales(page)
        
        for _ in range(3):
            page.mouse.wheel(0, 800)
            time.sleep(random.uniform(1, 2))
        
        posts = page.query_selector_all("div[role=\"article\"]")
        if not posts:
            posts = page.query_selector_all("div[data-ad-rendering-role=\"feed_story_unit\"]")
        if not posts:
            posts = page.query_selector_all("div[class*=\"x1yztbdb\"]")
        
        print(f"      → {len(posts)} publicaciones encontradas")
        
        for post in posts[:10]:
            try:
                text = ""
                content_selectors = [
                    "div[data-ad-preview=\"message\"]",
                    "div[dir=\"auto\"]",
                    "span[dir=\"auto\"]",
                    "div[class*=\"x1n2onr6\"]",
                ]
                for sel in content_selectors:
                    content_elem = post.query_selector(sel)
                    if content_elem:
                        text = content_elem.inner_text()
                        break
                if not text:
                    text = post.inner_text()
                
                if not text or len(text) < 20:
                    continue
                
                text_lower = text.lower()
                if not any(k in text_lower for k in KEYWORDS):
                    continue
                
                emails = re.findall(EMAIL_REGEX, text)
                title = text[:80].split("\n")[0] if text else "Evento en grupo"
                
                # Extraer organizador
                organizador = "No disponible"
                org_elem = post.query_selector('span[data-testid="event-card-host"]')
                if org_elem:
                    organizador = org_elem.inner_text()
                    organizador = organizador.replace('Hosted by', '').replace('Organizado por', '').strip()
                else:
                    match = re.search(r'Hosted by\s*([^\n]+)', text)
                    if match:
                        organizador = match.group(1).strip()
                
                # Enlace del post
                link_element = post.query_selector("a[href*=\"/posts/\"]")
                if link_element:
                    post_link = link_element.get_attribute("href")
                    if post_link and not post_link.startswith("http"):
                        post_link = f"https://www.facebook.com{post_link}"
                else:
                    post_link = group_url
                
                events.append({
                    "nombre": title.strip(),
                    "fecha": "Fecha no disponible",
                    "lugar": "Lugar no disponible",
                    "organizador": organizador,
                    "email": ", ".join(set(emails)) if emails else "No disponible",
                    "enlace": post_link,
                    "fuente": f"Facebook (grupo: {query})"
                })
            except Exception as e:
                continue
    
    except Exception as e:
        print(f"      ⚠ Error: {e}")
    
    return events

def cerrar_modales(page):
    botones = [
        'text=Aceptar todas',
        'text=Cerrar',
        'text=No, gracias',
        'text=No ahora',
        '[aria-label="Cerrar"]'
    ]
    for btn in botones:
        try:
            page.click(btn, timeout=2000)
            time.sleep(1)
        except:
            continue
