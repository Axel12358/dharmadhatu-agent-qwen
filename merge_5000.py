#!/usr/bin/env python3
"""Merge múltiples CSVs de eventos en uno, dedup por (nombre, fecha).

Prioridad de riqueza: a mayor número de campos completos, gana la fila.
Escribe bajo csv_lock (cooperativo con el bot). Uso: python3 merge_5000.py
"""
import csv
import sys
from pathlib import Path
from core.csv_lock import csv_locked_rows

TARGET = 5000
FUENTES = [
    "eventos_encontrados.csv",
    "eventos_encontrados.csv.bak_jeandupont",
    "eventos_encontrados.csv.bak_filtro_falsos",
]
CAMPOS = ["nombre", "fecha", "lugar", "pais", "continente", "subcontinente",
          "fuente", "organizador", "email", "link", "subgenero", "tipo_lugar",
          "contactos"]

def leer(ruta: str) -> list:
    p = Path(ruta)
    if not p.exists():
        return []
    with open(p, "r", encoding="utf-8", newline="") as f:
        return [dict(r) for r in csv.DictReader(f)]

def riqueza(ev: dict) -> tuple:
    peso = {c: (ev.get(c) or "").strip() for c in CAMPOS}
    n = sum(1 for c in CAMPOS if peso[c] not in ("", "N/A"))
    # subgenero/tipo_lugar detallados suman más (no_psy/indoor/outdoor)
    extra = 0
    sg = peso["subgenero"].lower()
    if sg not in ("", "n/a", "na", "general"):
        extra += 2
    if peso["tipo_lugar"] in ("indoor", "outdoor"):
        extra += 2
    return n, extra

def main():
    todos = []
    for src in FUENTES:
        filas = leer(src)
        print(f"{src}: {len(filas)} filas")
        todos.extend(filas)
    print(f"Total bruto: {len(todos)}")

    mejor = {}
    for ev in todos:
        nombre = (ev.get("nombre") or "").strip()
        fecha = (ev.get("fecha") or "").strip()
        if not nombre:
            continue
        clave = f"{nombre.lower()}::{fecha}"
        if clave not in mejor or riqueza(ev) > riqueza(mejor[clave]):
            mejor[clave] = ev

    filas = list(mejor.values())
    print(f"Únicas por (nombre,fecha): {len(filas)}")
    if len(filas) < TARGET:
        print(f"AVISO: solo {len(filas)} únicas; no hay 5000 reales (habría que llenar más con bot/multidías)")

    with csv_locked_rows("eventos_encontrados.csv", timeout=180) as (estado, _fn):
        # estado ya tiene filas del bot en vivo; dedup contra las del merge
        idx = {}
        for item in estado:
            if not item or not item.get("nombre"):
                continue
            idx[f"{item['nombre'].lower()}::{item.get('fecha','').strip()}"] = item
        for ev in filas:
            key = f"{ev['nombre'].lower()}::{ev.get('fecha','').strip()}"
            if key not in idx:
                idx[key] = ev
        # Reconstruir con campos en orden fijo y dedup interno final
        final = []
        seen = set()
        for ev in idx.values():
            key = f"{ev['nombre'].lower()}::{ev.get('fecha','').strip()}"
            if key in seen:
                continue
            seen.add(key)
            final.append({c: ev.get(c, "") for c in CAMPOS})
        estado[:] = final
    print(f"CSV final: {len(final)+1} líneas (incluye header)")

if __name__ == "__main__":
    main()