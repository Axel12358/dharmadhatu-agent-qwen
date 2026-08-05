#!/usr/bin/env python3
"""
Gestión de base de datos SQLite para el bot Dharmadhatu.

Proporciona una tabla `eventos` con todas las columnas relevantes de los eventos consolidados
(nombre, fecha, lugar, país, continente, subcontinente, fuente, organizador, email, link, subgenero, fecha_extracción),
permitiendo operaciones CRUD y consultas filtradas.

Uso:
    from utils.db_manager import init_db, save_events, query_events, export_csv

    # Inicializar (si no existe)
    init_db()

    # Guardar una lista de eventos dicts
    save_events(eventos)

    # Consultar, por ejemplo, por subgénero
    results = query_events(subgenero="psytrance")

    # Exportar a CSV
    export_csv("eventos.db", "eventos_filtrados.csv")
"""

import csv
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Optional, Any

# ---------------------------------------------------------------------- #
# CONFIGURACIÓN
# ---------------------------------------------------------------------- #

DB_FILE = Path(__file__).resolve().parent.parent / "eventos.db"

# ---------------------------------------------------------------------- #
# CONEXIÓN A LA BASE DE DATOS
# ---------------------------------------------------------------------- #


def get_connection() -> sqlite3.Connection:
    """Devuelve una conexión a SQLite (persistente, sin timeout).

    El archivo se guarda en la raíz del proyecto para facilitar la identificación.
    """
    conn = sqlite3.connect(str(DB_FILE), timeout=30, detect_types=sqlite3.PARSE_DECLTYPES)
    conn.row_factory = sqlite3.Row
    # Ajustes de rendimiento
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA cache_size = -8000")  # ~8MB de caché
    return conn


# ---------------------------------------------------------------------- #
# CREACIÓN DE TABLA
# ---------------------------------------------------------------------- #


def init_db():
    """Crea la tabla `eventos` si no existe."""
    create_sql = """
    CREATE TABLE IF NOT EXISTS eventos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nombre TEXT,
        fecha TEXT,
        lugar TEXT,
        pais TEXT,
        continente TEXT,
        subcontinente TEXT,
        fuente TEXT,
        organizador TEXT,
        email TEXT,
        link TEXT,
        subgenero TEXT,
        fecha_extraccion TEXT
    );
    """

    with get_connection() as conn:
        conn.execute(create_sql)
        conn.commit()


# ---------------------------------------------------------------------- #
# OPERACIONES CRUD
# ---------------------------------------------------------------------- #


def save_events(eventos: List[Dict[str, Any]]) -> int:
    """Guarda o reemplaza eventos en la base de datos.

    Parámetros:
        eventos: Lista de dicts con claves estándar (nombre, fecha, lugar, pais, continente, subcontinente,
                 fuente, organizador, email, link, subgenero). Las claves extras se ignoran.

    Devuelve:
        Número de filas insertadas.
    """
    if not eventos:
        return 0

    columns = [
        "nombre", "fecha", "lugar", "pais", "continente", "subcontinente",
        "fuente", "organizador", "email", "link", "subgenero", "fecha_extraccion",
    ]

    # Preparar valores
    rows = []
    for ev in eventos:
        rows.append(tuple(ev.get(c) for c in columns))

    with get_connection() as conn:
        # Limpiar la tabla antes de insertar nuevos datos (¿reemplazar?)
        conn.execute("DELETE FROM eventos")
        conn.executemany(
            f"INSERT INTO eventos ({', '.join(columns)}) VALUES ({', '.join(['?' for _ in columns])})",
            rows,
        )
        conn.commit()
        return len(rows)


# ---------------------------------------------------------------------- #
# CONSULTAS
# ---------------------------------------------------------------------- #


def query_events(
    subgenero: Optional[str] = None,
    continente: Optional[str] = None,
    subcontinente: Optional[str] = None,
    pais: Optional[str] = None,
    fuente: Optional[str] = None,
    desde: Optional[str] = None,  # ISO fecha string (YYYY-MM-DD)
    hasta: Optional[str] = None,
    limite: Optional[int] = None,
) -> List[Dict]:
    """Devuelve eventos como dicts, opcionalmente filtrados.

    Parámetros:
        subgenero, continente, subcontinente, pais, fuente: Filtros exactos (case-sensitive).
        desde, hasta: Filtrar por fecha_extraccion (YYYY-MM-DD, ISO).
        limite: Número máximo de filas.
    """
    query_parts = []
    params = []

    if subgenero:
        query_parts.append("subgenero = ?")
        params.append(subgenero)
    if continente:
        query_parts.append("continente = ?")
        params.append(continente)
    if subcontinente:
        query_parts.append("subcontinente = ?")
        params.append(subcontinente)
    if pais:
        query_parts.append("pais = ?")
        params.append(pais)
    if fuente:
        query_parts.append("fuente = ?")
        params.append(fuente)
    if desde:
        query_parts.append("fecha_extraccion >= ?")
        params.append(desde)
    if hasta:
        query_parts.append("fecha_extraccion <= ?")
        params.append(hasta)

    where_clause = ""
    if query_parts:
        where_clause = " WHERE " + " AND ".join(query_parts)

    limite_clause = ""
    if limite is not None:
        limite_clause = " LIMIT ?"
        params.append(limite)

    sql = f"SELECT * FROM eventos{where_clause} ORDER BY id DESC{limite_clause}"

    with get_connection() as conn:
        rows = conn.execute(sql, params)
        if limite_clause:
            rows = rows.fetchmany(limite)
        return [dict(row) for row in rows]


# ---------------------------------------------------------------------- #
# EXPORTACIÓN A CSV
# ---------------------------------------------------------------------- #


def export_csv(output_path: str, filename: str = "eventos.db.csv"):
    """Exporta los eventos de la tabla a CSV.

    Parámetros:
        output_path: Ignorado (mantiene la firma consistente con save_csv).
        filename: Nombre del archivo CSV a escribir.
    """
    events = query_events()
    if not events:
        print("⚠️ No hay eventos para exportar a CSV")
        return

    keys = events[0].keys()
    with open(filename, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=keys)
        writer.writeheader()
        for ev in events:
            writer.writerow(ev)
    print(f"✅ CSV exportado: {filename} ({len(events)} filas)")


# ---------------------------------------------------------------------- #
# UTILIDAD: POPULATE DESDE EVENTOS_EXISTENTES (si es necesario)
# ---------------------------------------------------------------------- #


def populate_from_list(eventos: List[Dict[str, Any]]) -> int:
    """Alias de save_events para mayor claridad."""
    return save_events(eventos)


# ---------------------------------------------------------------------- #
# EJEMPLO (cuando se ejecuta directamente)
# ---------------------------------------------------------------------- #


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Herramienta de gestión de eventos SQLite")
    parser.add_argument("--init", action="store_true", help="Inicializar la base de datos")
    parser.add_argument("--save", help="Archivo JSON con eventos a guardar (formato lista de dicts)")
    parser.add_argument("--query", action="store_true", help="Listar eventos")
    parser.add_argument("--export-csv", dest="csv", help="Exportar a CSV")

    args = parser.parse_args()

    if args.init:
        init_db()
        print(f"✅ Base de datos inicializada en {DB_FILE}")

    if args.save:
        path = Path(args.save)
        if not path.exists():
            print(f"❌ Archivo {args.save} no encontrado")
            sys.exit(1)
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            count = populate_from_list(data)
            print(f"✅ {count} eventos guardados")
        except Exception as e:
            print(f"❌ Error guardando: {e}")

    if args.query:
        from pprint import pprint
        rows = query_events(limite=10)
        print(f"📊 {len(rows)} filas")
        pprint(rows)

    if args.csv:
        export_csv("", args.csv)
