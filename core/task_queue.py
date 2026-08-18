#!/usr/bin/env python3
"""
Cola de tareas compartida (Dharmadhatu Bot v5) — Sistema Multiagente Ligero.

Cola de subagentes con persistencia en `task_queue_state.json`.
El Agente Coordinador encola subagentes (con timeout y prioridad) y los
marca como completados conforme terminan. Es aditiva e idempotente:
re-encolar un subagente ya registrado no duplica entradas.
"""

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

# Archivo por defecto de persistencia
STATE_FILE = "task_queue_state.json"

# Límite de entradas antes de recortar automáticamente
LIMITE_ENTRADAS = 1000


class TaskQueue:
    """Cola FIFO de tareas/subagentes con persistencia en JSON.

    Uso:
        cola = TaskQueue()
        cola.encolar("facebook_events_agent", timeout=300, prioridad=1)
        pendientes = cola.pendientes()
        cola.marcar_completada("facebook_events_agent", resultado={...})
        cola.guardar()
    """

    def __init__(self, archivo: str = STATE_FILE, limite: int = LIMITE_ENTRADAS):
        self.archivo = archivo
        self.limite = limite
        self._lock = threading.RLock()
        self._tareas: Dict[str, Dict[str, Any]] = {}
        self._orden: List[str] = []
        self._cargar()

    # ---------- persistencia ----------
    def _cargar(self) -> None:
        if not Path(self.archivo).exists():
            return
        try:
            with open(self.archivo, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                return
            self._tareas = data.get("tareas", {}) or {}
            self._orden = data.get("orden", []) or []
        except (json.JSONDecodeError, IOError, ValueError):
            self._tareas = {}
            self._orden = []

    def guardar(self) -> None:
        with self._lock:
            self._recortar()
            try:
                tmp = self.archivo + ".tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump({"tareas": self._tareas, "orden": self._orden},
                              f, indent=2, ensure_ascii=False)
                os.replace(tmp, self.archivo)
            except (IOError, OSError):
                pass

    def _recortar(self) -> None:
        if len(self._tareas) <= self.limite:
            return
        # Conservar las más recientes por fecha de encolado
        ordenadas = sorted(self._tareas.items(),
                           key=lambda kv: kv[1].get("encolada", ""),
                           reverse=True)
        self._tareas = dict(ordenadas[:self.limite])
        ids = set(self._tareas.keys())
        self._orden = [tid for tid in self._orden if tid in ids]

    # ---------- operaciones ----------
    def encolar(self, nombre: str,
                timeout: Optional[int] = None,
                prioridad: Optional[int] = None,
                **extras) -> str:
        """Encola una tarea/subagente (idempotente por nombre).

        Devuelve el id de la tarea (que coincide con `nombre`).
        """
        ahora = datetime.now(timezone.utc).isoformat()
        tarea: Dict[str, Any] = {
            "nombre": nombre,
            "id": nombre,
            "estado": "pendiente",
            "timeout": int(timeout or 0),
            "prioridad": int(prioridad if prioridad is not None else 5),
            "encolada": ahora,
        }
        for k, v in extras.items():
            tarea[k] = v
        with self._lock:
            if nombre not in self._tareas:
                self._orden.append(nombre)
            self._tareas[nombre] = tarea
        return nombre

    def marcar_completada(self, nombre: str,
                          resultado: Optional[Dict[str, Any]] = None,
                          error: Optional[str] = None) -> bool:
        with self._lock:
            tarea = self._tareas.get(nombre)
            if tarea is None:
                return False
            tarea["estado"] = "completada" if not error else "error"
            tarea["completada"] = datetime.now(timezone.utc).isoformat()
            if error:
                tarea["error"] = error
            if resultado is not None:
                tarea["resultado"] = resultado
            return True

    def pendientes(self) -> List[Dict[str, Any]]:
        with self._lock:
            return [dict(self._tareas[tid]) for tid in self._orden
                    if self._tareas[tid].get("estado") == "pendiente"]

    def completadas(self) -> List[Dict[str, Any]]:
        with self._lock:
            return [dict(self._tareas[tid]) for tid in self._orden
                    if self._tareas[tid].get("estado") in ("completada", "error")]

    def obtener(self, nombre: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            t = self._tareas.get(nombre)
            return dict(t) if t else None

    def limpiar(self) -> None:
        with self._lock:
            self._tareas = {}
            self._orden = []

    @property
    def total(self) -> int:
        with self._lock:
            return len(self._tareas)

    def resumen(self) -> Dict[str, int]:
        with self._lock:
            estados: Dict[str, int] = {}
            for t in self._tareas.values():
                e = t.get("estado", "desconocido")
                estados[e] = estados.get(e, 0) + 1
            return estados


if __name__ == "__main__":
    cola = TaskQueue()
    cola.encolar("facebook_events_agent", timeout=300, prioridad=1)
    cola.encolar("goabase_agent", timeout=120, prioridad=2)
    cola.encolar("facebook_events_agent", timeout=300, prioridad=1)  # idempotente
    print("Pendientes:", [t["nombre"] for t in cola.pendientes()])
    print("Total:", cola.total)
    cola.marcar_completada("goabase_agent", resultado={"eventos": 5})
    cola.guardar()
    print("Resumen:", cola.resumen())
