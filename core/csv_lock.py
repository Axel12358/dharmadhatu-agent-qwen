#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Lock cooperativo para eventos_encontrados.csv.

Varios procesos (orquestador, agente_coordinador, loop_completar,
venue_enricher, ediciones manuales) hacen read-modify-write sobre el mismo
CSV. Sin lock, el último en escribir pisa el trabajo de los demás
(emails/organizadores perdidos, "resurrecciones" de datos limpiados).

Uso:
    from core.csv_lock import csv_locked_rows
    with csv_locked_rows(CSV_PATH, timeout=120) as (rows, fieldnames):
        ... modificar rows ...
    # al salir se escribe automáticamente (siempre bajo el mismo lock)

El lock es fcntl.flock exclusivo sobre un archivo .lock hermano.
Todos los escritores DEBEN usar este helper para que funcione.
"""
import csv
import fcntl
import time
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def csv_locked_rows(csv_path, timeout=180, encoding="utf-8"):
    """Lee el CSV bajo lock exclusivo, cede (rows, fieldnames), escribe al salir."""
    csv_path = str(csv_path)
    lock_path = csv_path + ".lock"
    rows = []
    fieldnames = None
    t0 = time.time()
    with open(lock_path, "w") as lockfh:
        while True:
            try:
                fcntl.flock(lockfh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except (OSError, IOError):
                if time.time() - t0 > timeout:
                    raise TimeoutError(f"csv_lock: timeout esperando {lock_path}")
                time.sleep(0.5)
        try:
            with open(csv_path, "r", encoding=encoding, newline="") as f:
                reader = csv.DictReader(f)
                fieldnames = reader.fieldnames
                rows = list(reader)
            yield rows, fieldnames
            with open(csv_path, "w", encoding=encoding, newline="") as f:
                w = csv.DictWriter(f, fieldnames=fieldnames)
                w.writeheader()
                w.writerows(rows)
        finally:
            try:
                fcntl.flock(lockfh.fileno(), fcntl.LOCK_UN)
            except Exception:
                pass
