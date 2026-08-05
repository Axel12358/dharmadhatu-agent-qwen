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

def deduplicar_eventos(eventos, claves=("nombre", "fecha", "lugar")):
    """Elimina duplicados basándose en una clave compuesta.

    Args:
        eventos: lista de dicts con datos de eventos.
        claves: tupla de campos para formar la clave de deduplicación.

    Returns:
        Lista de eventos sin duplicados (conserva el primero encontrado).
    """
    vistos = set()
    unicos = []
    for ev in eventos:
        k = tuple(str(ev.get(c, "") or "").strip().lower() for c in claves)
        if k not in vistos:
            vistos.add(k)
            unicos.append(ev)
    return unicos


def validar_evento(evento):
    """Verifica que un evento tenga datos mínimos de calidad.

    Criterios:
    - fecha: formato YYYY-MM-DD o "N/A" (aceptable si fuente es
      especializada como Goabase).
    - lugar: no vacío ni "N/A" para fuentes generales.
    - link: URL http(s) válida.
    - nombre: no vacío.

    Returns:
        True si el evento pasa la validación.
    """
    nombre = (evento.get("nombre") or "").strip()
    if not nombre or nombre == "N/A":
        return False

    link = (evento.get("link") or "").strip()
    if not link or not link.startswith("http"):
        return False

    fecha = (evento.get("fecha") or "").strip()
    if fecha and fecha != "N/A":
        import re as _re
        if not _re.match(r"^\d{4}-\d{2}-\d{2}", fecha):
            return False

    lugar = (evento.get("lugar") or "").strip()
    fuente = (evento.get("fuente") or "").strip()
    fuentes_especializadas = {"Goabase", "Psytrance.pl", "IsraTrance",
                              "Ektoplazm", "Songkick"}
    if fuente not in fuentes_especializadas:
        if not lugar or lugar == "N/A":
            return False

    return True


def limpiar_eventos(eventos):
    """Filtra eventos inválidos y duplicados.

    Aplica `validar_evento` y luego `deduplicar_eventos`.
    """
    validos = [ev for ev in eventos if validar_evento(ev)]
    return deduplicar_eventos(validos)


def save_csv(data, filename="eventos_encontrados.csv"):
    """Guarda datos en un archivo CSV con columna link."""
    if not data:
        print("⚠️ No hay datos para guardar")
        return
    try:
        keys_estandar = [
            "nombre", "fecha", "lugar", "pais", "continente", "subcontinente",
            "fuente", "organizador", "email", "link", "subgenero",
        ]
        with open(filename, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=keys_estandar, extrasaction="ignore")
            writer.writeheader()
            for ev in data:
                link = (ev.get("link") or ev.get("url") or ev.get("source_url") or "N/A")
                ev = dict(ev)
                ev["link"] = link
                writer.writerow(ev)
        print(f"✅ CSV guardado: {filename} ({len(data)} filas)")
    except Exception as e:
        print(f"❌ Error guardando CSV: {e}")
