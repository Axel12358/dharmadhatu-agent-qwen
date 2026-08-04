#!/usr/bin/env python3
"""
Funciones auxiliares para el bot.
"""

import csv
import json
from pathlib import Path

def load_config(config_path="configs_exitosas/config_grupos.json"):
    """Carga la configuración desde un archivo JSON."""
    try:
        with open(config_path) as f:
            return json.load(f)
    except FileNotFoundError:
        print(f"⚠️ Configuración no encontrada: {config_path}")
        return {}

def save_csv(data, filename="eventos_encontrados.csv"):
    """Guarda datos en un archivo CSV con columna link."""
    if not data:
        print("⚠️ No hay datos para guardar")
        return
    try:
        keys_estandar = [
            "nombre", "fecha", "lugar", "pais", "fuente",
            "organizador", "email", "link",
        ]
        with open(filename, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=keys_estandar, extrasaction="ignore")
            writer.writeheader()
            for ev in data:
                # Normalizar el link: link > url > source_url
                link = (ev.get("link") or ev.get("url") or ev.get("source_url") or "N/A")
                ev = dict(ev)
                ev["link"] = link
                writer.writerow(ev)
        print(f"✅ CSV guardado: {filename} ({len(data)} filas)")
    except Exception as e:
        print(f"❌ Error guardando CSV: {e}")
