#!/usr/bin/env python3
"""
Dharmadhatu Bot v5 - Con scraping asíncrono y enriquecimiento selectivo
"""

import os
import sys
import json
import time
import asyncio
import pandas as pd

# ============================================================
# CARGA DE CONFIGURACIÓN
# ============================================================
def cargar_config_loop():
    try:
        with open('config_temp.json', 'r') as f:
            return json.load(f)
    except:
        return {}

CONFIG_LOOP = cargar_config_loop()
MAX_EVENTOS = CONFIG_LOOP.get('max_eventos', 150)  # Aumentado
BUSQUEDAS_FACEBOOK = CONFIG_LOOP.get('busquedas_facebook', 150)  # Aumentado
TIMEOUT = CONFIG_LOOP.get('timeout', 30)
EXTRAER_CONTACTOS = CONFIG_LOOP.get('extraer_contactos', True)
PRIORIZAR_PAISES = CONFIG_LOOP.get('priorizar_paises', [])
BUSCAR_EN_GRUPOS = CONFIG_LOOP.get('buscar_en_grupos', True)

print("="*60)
print("🌀 DHARMADHATU BOT v5 (ASÍNCRONO + ENRIQUECIMIENTO SELECTIVO)")
print("="*60)
print(f"📊 max_eventos: {MAX_EVENTOS}")
print(f"📊 busquedas_facebook: {BUSQUEDAS_FACEBOOK}")
print(f"📊 extraer_contactos: {EXTRAER_CONTACTOS}")
print(f"📊 priorizar_paises: {PRIORIZAR_PAISES}")
print(f"📊 buscar_en_grupos: {BUSCAR_EN_GRUPOS}")
print("="*60)

# ============================================================
# IMPORTS DE SCRAPERS
# ============================================================
from scrapers.goabase import scrape_goabase
from scrapers.psynews import scrape_psynews
from scrapers.grupos_fijos import scrape_grupos_fijos
from scrapers.resident_advisor import scrape_resident_advisor
from scrapers.songkick import scrape_songkick
from scrapers.eventbrite import scrape_eventbrite
from scrapers.instagram.scraper_publico import scrape_instagram_publico
from scrapers.scraper_asincrono import ejecutar_scrapers_async

# ============================================================
# FUNCIONES GENERAR EVENTOS DE PRUEBA
# ============================================================
def generar_eventos_prueba(limit=20):
    eventos = []
    eventos_reales = [
        {"nombre": "Boom Festival 2026 - Portugal", "fecha": "2026-07-20", "lugar": "Idanha-a-Nova", "organizador": "Boom Team"},
        {"nombre": "Ozora Festival 2026 - Hungría", "fecha": "2026-07-27", "lugar": "Ozora", "organizador": "Ozora Crew"},
        {"nombre": "Universo Paralello 2026 - Brasil", "fecha": "2026-12-28", "lugar": "Bahia", "organizador": "UP Team"},
        {"nombre": "Modem Festival 2026 - Croacia", "fecha": "2026-08-15", "lugar": "Duga", "organizador": "Modem Crew"},
        {"nombre": "Psy-Fi Festival 2026 - Holanda", "fecha": "2026-07-10", "lugar": "Ámsterdam", "organizador": "Psy-Fi"},
        {"nombre": "Shankra Festival 2026 - Suiza", "fecha": "2026-07-02", "lugar": "Losone", "organizador": "Shankra"},
        {"nombre": "Free Earth Festival 2026 - Grecia", "fecha": "2026-06-25", "lugar": "Thessaloniki", "organizador": "Free Earth"},
        {"nombre": "Sol Festival 2026 - España", "fecha": "2026-07-18", "lugar": "Barcelona", "organizador": "Sol Crew"},
        {"nombre": "Mystic Garden 2026 - Portugal", "fecha": "2026-08-05", "lugar": "Lisboa", "organizador": "Mystic Team"},
        {"nombre": "Cosmic Gathering 2026 - Austria", "fecha": "2026-07-12", "lugar": "Viena", "organizador": "Cosmic Crew"},
    ]
    for evento in eventos_reales[:limit]:
        eventos.append({
            'nombre': evento['nombre'],
            'fuente': 'Facebook Grupo (prueba)',
            'fecha': evento.get('fecha', 'N/A'),
            'lugar': evento.get('lugar', 'N/A'),
            'organizador': evento.get('organizador', 'N/A'),
            'email': 'N/A'
        })
    return eventos

def generar_eventos_prueba_facebook(limit=8):
    eventos = [
        {"nombre": "Psytrance Festival Barcelona 2026", "fecha": "2026-07-15", "lugar": "Barcelona", "organizador": "Psy Events"},
        {"nombre": "Goa Trance Party Madrid", "fecha": "2026-07-22", "lugar": "Madrid", "organizador": "Goa Crew"},
        {"nombre": "Darkpsy Night Berlin", "fecha": "2026-07-29", "lugar": "Berlín", "organizador": "Dark Collective"},
        {"nombre": "Full-on Festival Amsterdam", "fecha": "2026-08-05", "lugar": "Ámsterdam", "organizador": "Full-on Team"},
        {"nombre": "Progressive Trance Paris", "fecha": "2026-08-12", "lugar": "París", "organizador": "Prog Squad"},
        {"nombre": "Forest Psy Gathering Lisbon", "fecha": "2026-08-19", "lugar": "Lisboa", "organizador": "Forest Tribe"},
        {"nombre": "Zenonesque Party Zurich", "fecha": "2026-08-26", "lugar": "Zúrich", "organizador": "Zen Crew"},
        {"nombre": "Hi-tech Trance Milan", "fecha": "2026-09-02", "lugar": "Milán", "organizador": "Hi-tech Team"},
    ]
    for evento in eventos[:limit]:
        eventos.append({
            'nombre': evento['nombre'],
            'fuente': 'Facebook (prueba)',
            'fecha': evento.get('fecha', 'N/A'),
            'lugar': evento.get('lugar', 'N/A'),
            'organizador': evento.get('organizador', 'N/A'),
            'email': 'N/A'
        })
    return eventos

def generar_eventos_prueba_instagram(limit=10):
    eventos = [
        {"nombre": "Instagram Psy Gathering 2026", "fecha": "2026-07-20", "lugar": "Lisboa", "organizador": "IG Psy"},
        {"nombre": "Goa Trance IG Edition", "fecha": "2026-07-27", "lugar": "Berlín", "organizador": "IG Goa"},
        {"nombre": "Darkpsy Stories", "fecha": "2026-08-03", "lugar": "Londres", "organizador": "IG Dark"},
        {"nombre": "Full-on Reels", "fecha": "2026-08-10", "lugar": "Ámsterdam", "organizador": "IG Full"},
        {"nombre": "Progressive IG Live", "fecha": "2026-08-17", "lugar": "París", "organizador": "IG Prog"},
        {"nombre": "Forest Psy IG", "fecha": "2026-08-24", "lugar": "Zúrich", "organizador": "IG Forest"},
        {"nombre": "Zenonesque IG Party", "fecha": "2026-08-31", "lugar": "Barcelona", "organizador": "IG Zen"},
        {"nombre": "Hi-tech IG Stories", "fecha": "2026-09-07", "lugar": "Milán", "organizador": "IG Hi-tech"},
        {"nombre": "Twilight IG Gathering", "fecha": "2026-09-14", "lugar": "Viena", "organizador": "IG Twilight"},
        {"nombre": "Suomi IG Celebration", "fecha": "2026-09-21", "lugar": "Helsinki", "organizador": "IG Suomi"},
    ]
    for evento in eventos[:limit]:
        eventos.append({
            'nombre': evento['nombre'],
            'fuente': 'Instagram (prueba)',
            'fecha': evento.get('fecha', 'N/A'),
            'lugar': evento.get('lugar', 'N/A'),
            'organizador': evento.get('organizador', 'N/A'),
            'email': 'N/A'
        })
    return eventos

# ============================================================
# FUNCIÓN PRINCIPAL
# ============================================================
def main():
    inicio_total = time.time()
    eventos = []

    # ============================================================
    # 1. SCRAPERS EN PARALELO (Goabase + Facebook)
    # ============================================================
    print("\n🌐 Ejecutando scrapers en paralelo (Goabase + Facebook)...")
    try:
        eventos_scrapers = asyncio.run(ejecutar_scrapers_async({
            "max_eventos": MAX_EVENTOS,
            "busquedas_facebook": BUSQUEDAS_FACEBOOK
        }))
        eventos.extend(eventos_scrapers)
        print(f"✅ Scrapers paralelos: {len(eventos_scrapers)} eventos")
    except Exception as e:
        print(f"⚠️ Error en scrapers paralelos: {e}")

    # ============================================================
    # 2. PSYNEWS (secuencial)
    # ============================================================
    try:
        print("\n🌐 Scraping Psynews...")
        eventos_psynews = scrape_psynews(limit=MAX_EVENTOS)
        eventos.extend(eventos_psynews)
        print(f"✅ Psynews: {len(eventos_psynews)} eventos")
    except Exception as e:
        print(f"❌ Error en Psynews: {e}")

    # ============================================================
    # 3. FACEBOOK GRUPOS (prueba)
    # ============================================================
    print("\n📂 Facebook Grupos: desactivado (timeouts constantes)")
    print("📌 Generando eventos de prueba para grupos...")
    eventos_grupos = generar_eventos_prueba(10)
    eventos.extend(eventos_grupos)
    print(f"✅ Facebook Grupos (prueba): {len(eventos_grupos)} eventos")

    # ============================================================
    # 4. INSTAGRAM (secuencial)
    # ============================================================
    try:
        print("\n📸 Scraping Instagram (sin login)...")
        eventos_instagram = scrape_instagram_publico("psytrance", max_posts=10)
        for e in eventos_instagram:
            eventos.append({
                'nombre': e.get('nombre', 'Evento sin nombre'),
                'fuente': 'Instagram',
                'fecha': e.get('fecha', 'N/A'),
                'lugar': e.get('lugar', 'N/A'),
                'pais': 'N/A',
                'organizador': e.get('organizador', 'N/A'),
                'email': e.get('email', 'N/A')
            })
        print(f"✅ Instagram: {len(eventos_instagram)} eventos")
    except Exception as e:
        print(f"⚠️ Error en Instagram: {e}")
        for i in range(10):
            eventos.append({
                'nombre': f"Evento Instagram {i+1}",
                'fuente': 'Instagram (prueba)',
                'fecha': 'N/A',
                'lugar': 'N/A',
                'pais': 'N/A',
                'organizador': 'N/A',
                'email': 'N/A'
            })
        print(f"✅ Instagram (prueba): 10 eventos")

    # ============================================================
    # 5. GRUPOS FIJOS
    # ============================================================
    try:
        print("\n🌐 Scraping Grupos Fijos...")
        eventos_grupos_fijos = scrape_grupos_fijos(limit=MAX_EVENTOS)
        eventos.extend(eventos_grupos_fijos)
        print(f"✅ Grupos Fijos: {len(eventos_grupos_fijos)} eventos")
    except Exception as e:
        print(f"❌ Error en Grupos Fijos: {e}")

    # ============================================================
    # 6. RESIDENT ADVISOR
    # ============================================================
    try:
        print("\n🌐 Scraping Resident Advisor...")
        eventos_ra = scrape_resident_advisor(limit=MAX_EVENTOS)
        eventos.extend(eventos_ra)
        print(f"✅ Resident Advisor: {len(eventos_ra)} eventos")
    except Exception as e:
        print(f"❌ Error en Resident Advisor: {e}")

    # ============================================================
    # 7. SONGKICK
    # ============================================================
    try:
        print("\n🎵 Scraping Songkick...")
        eventos_sk = scrape_songkick(limit=MAX_EVENTOS)
        eventos.extend(eventos_sk)
        print(f"✅ Songkick: {len(eventos_sk)} eventos")
    except Exception as e:
        print(f"❌ Error en Songkick: {e}")

    # ============================================================
    # 8. EVENTBRITE
    # ============================================================
    try:
        print("\n🎫 Scraping Eventbrite...")
        eventos_eb = scrape_eventbrite(limit=MAX_EVENTOS)
        eventos.extend(eventos_eb)
        print(f"✅ Eventbrite: {len(eventos_eb)} eventos")
    except Exception as e:
        print(f"❌ Error en Eventbrite: {e}")

    # ============================================================
    # 9. CONSOLIDAR
    # ============================================================
    if eventos:
        df = pd.DataFrame(eventos)
        df = df.drop_duplicates(subset=['nombre', 'fecha', 'lugar'], keep='first')
        os.makedirs('data', exist_ok=True)
        df.to_csv('data/events_consolidated_v5.csv', index=False)
        print(f"\n✅ TOTAL: {len(df)} eventos consolidados")

        # ============================================================
        # 10. ENRIQUECER CONTACTOS (SOLO EVENTOS REALES)
        # ============================================================
        if EXTRAER_CONTACTOS:
            print("\n📧 Extrayendo contactos con Qwen (solo eventos reales)...")
            try:
                from scrapers.enriquecer_eventos import enriquecer_eventos_con_qwen
                # Definir fuentes reales (excluir pruebas)
                fuentes_reales = ['Goabase', 'Facebook', 'Instagram', 'Resident Advisor', 'Songkick', 'Eventbrite']
                df_reales = df[df['fuente'].isin(fuentes_reales)]
                print(f"🔍 Eventos reales a enriquecer: {len(df_reales)}")
                if len(df_reales) > 0:
                    eventos_reales = df_reales.to_dict('records')
                    eventos_enriquecidos = enriquecer_eventos_con_qwen(eventos_reales)
                    # Actualizar solo los eventos reales en el DataFrame principal
                    df_enriquecido = df.copy()
                    for i, row in df_reales.iterrows():
                        # Buscar el evento enriquecido correspondiente
                        for e in eventos_enriquecidos:
                            if e['nombre'] == row['nombre']:
                                df_enriquecido.at[i, 'organizador'] = e.get('organizador', row.get('organizador', 'N/A'))
                                df_enriquecido.at[i, 'email'] = e.get('email', row.get('email', 'N/A'))
                                break
                    # Los eventos de prueba mantienen N/A (ya están así)
                    df_enriquecido.to_csv('data/events_consolidated_v5.csv', index=False)
                    print("✅ Eventos reales enriquecidos guardados")
                else:
                    print("⚠️ No hay eventos reales para enriquecer")
            except Exception as e:
                print(f"❌ Error al enriquecer: {e}")

        # ============================================================
        # 11. ESTADÍSTICAS (reales)
        # ============================================================
        print("\n📈 ESTADÍSTICAS:")
        print(f" • Eventos totales: {len(df)}")
        # Contar organizadores reales (excluyendo N/A)
        if 'organizador' in df.columns:
            org_count = df[df['organizador'] != 'N/A']['organizador'].notna().sum()
            print(f" • Organizadores reales: {org_count}")
        if 'email' in df.columns:
            email_count = df[df['email'] != 'N/A']['email'].notna().sum()
            print(f" • Emails reales: {email_count}")
        if 'fuente' in df.columns:
            print("\n📊 Distribución por fuente:")
            for fuente, count in df['fuente'].value_counts().items():
                print(f" • {fuente}: {count}")

        fin_total = time.time()
        print(f"\n⏱️ Tiempo: {(fin_total - inicio_total) / 60:.2f} min")
    else:
        print("⚠️ No se encontraron eventos")

if __name__ == "__main__":
    main()
