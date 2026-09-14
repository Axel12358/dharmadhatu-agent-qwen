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
def csv_locked_rows(csv_path, timeout=180, encoding="utf-8", extrasaction="ignore"):
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
                w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction=extrasaction)
                w.writeheader()
                w.writerows(rows)
        finally:
            try:
                fcntl.flock(lockfh.fileno(), fcntl.LOCK_UN)
            except Exception:
                pass


def _clave_fila(r: dict) -> tuple:
    """Clave estable para merge: link, o nombre|fecha si no hay link."""
    lk = (r.get("link") or "").strip().lower()
    if lk and lk not in ("n/a", "na", "none", "-", "?"):
        return ("L", lk)
    nombre = (r.get("nombre") or "").strip().lower()
    fecha = (r.get("fecha") or "").strip().lower()
    return ("N", nombre, fecha)


def _na_valor(v) -> bool:
    return (not str(v or "").strip()
            or str(v or "").strip().lower() in ("n/a", "na", "none", "null", "-", "?"))


def escribir_fusionando(csv_path, filas_calculadas, timeout=180):
    """Consolida CAMBIOS de N/A sin pisar filas añadidas por otros procesos.

    Re-lee el CSV fresco bajo lock, rellena campos vacíos/N/A con los
    valores de `filas_calculadas` (clave: link o nombre|fecha) y añade las
    filas de `filas_calculadas` que no existan. NUNCA borra filas ajenas.
    Correcto para writer-mutadores (loop_completar, completar_na, fb_og).
    """
    from core.csv_lock import csv_locked_rows
    with csv_locked_rows(csv_path, timeout=timeout) as (frescas, _fn):
        idx = {_clave_fila(r): r for r in filas_calculadas}
        exist = set()
        for fr in frescas:
            exist.add(_clave_fila(fr))
            src = idx.get(_clave_fila(fr))
            if src is None:
                continue
            for kk, vv in src.items():
                if _na_valor(fr.get(kk)) and str(vv or "").strip():
                    fr[kk] = vv
        agregadas = 0
        for r in filas_calculadas:
            if _clave_fila(r) not in exist:
                frescas.append(r)
                exist.add(_clave_fila(r))
                agregadas += 1
    return {"fusionadas": len(frescas), "agregadas": agregadas}
