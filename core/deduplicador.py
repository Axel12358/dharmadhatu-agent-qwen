#!/usr/bin/env python3
"""
Deduplicador global (Dharmadhatu Bot v5).

Mantiene un archivo `cache_dedup.json` con hashes de eventos vistos.
`filtrar_nuevos(eventos)` devuelve solo los eventos que no están en la caché,
es decir, los que aún no se han consolidado en el CSV (aditivo, nunca resta).

La caché se limpia automáticamente cuando supera LIMITE_ENTRADAS entradas.
"""

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

# Máx. entradas antes de limpiar la caché automáticamente
LIMITE_ENTRADAS = 5000

# Campos usados para construir el hash de deduplicación (clave global).
# Incluye "fuente" para respetar la regla "sumar nunca restar": un mismo
# evento listado por fuentes distintas (p. ej. RA y Facebook) se conserva en
# ambas, y solo se evita re-sumar el MISMO evento de la MISMA fuente.
CLAVES_HASH = ("nombre", "fecha", "lugar", "fuente")


def _normalizar(valor: Any) -> str:
    """Normaliza un valor para el hash (minúsculas, sin espacios extra)."""
    return " ".join(str(valor or "").strip().lower().split())


def _hash_evento(evento: Dict) -> str:
    """Hash SHA-256 de (nombre, fecha, lugar) normalizados."""
    componentes = "|".join(_normalizar(evento.get(c, "")) for c in CLAVES_HASH)
    return hashlib.sha256(componentes.encode("utf-8")).hexdigest()


class Deduplicador:
    """Deduplicación global con caché persistente en JSON.

    Uso:
        dedup = Deduplicador()
        nuevos = dedup.filtrar_nuevos(eventos)   # solo no vistos
        dedup.registrar_vistos(eventos)          # marca como vistos
        dedup.guardar()                          # persiste cache_dedup.json
    """

    def __init__(self, archivo: str = "cache_dedup.json", limite: int = LIMITE_ENTRADAS):
        self.archivo = archivo
        self.limite = limite
        self._hashes: Dict[str, str] = {}  # hash -> fecha de primera vista (ISO)
        self.cargar()

    def cargar(self) -> None:
        """Carga la caché desde disco (tolerante a fallos)."""
        if not Path(self.archivo).exists():
            return
        try:
            with open(self.archivo, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                self._hashes = {
                    k: v for k, v in data.items()
                    if isinstance(k, str) and isinstance(v, str)
                }
        except (json.JSONDecodeError, IOError, ValueError):
            self._hashes = {}

    def guardar(self) -> None:
        """Persiste la caché en disco (atómico vía .tmp + os.replace)."""
        self._limpiar_si_excede()
        try:
            tmp = self.archivo + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self._hashes, f, indent=2, ensure_ascii=False)
            os.replace(tmp, self.archivo)
        except (IOError, OSError):
            pass

    def _limpiar_si_excede(self) -> None:
        """Limpia automáticamente cuando la caché supera el límite.

        Conserva las entradas más recientes (ordenadas por fecha de vista).
        """
        if len(self._hashes) <= self.limite:
            return
        ordenados = sorted(
            self._hashes.items(),
            key=lambda kv: kv[1] or "",  # fecha ISO ordena cronológicamente
            reverse=True,
        )
        self._hashes = dict(ordenados[: self.limite])

    def hash_evento(self, evento: Dict) -> str:
        return _hash_evento(evento)

    def es_visto(self, evento: Dict) -> bool:
        return self.hash_evento(evento) in self._hashes

    def registrar_vistos(self, eventos: List[Dict]) -> int:
        """Marca una lista de eventos como vistos. Devuelve cuántos se añadieron."""
        ahora = datetime.now(timezone.utc).isoformat()
        nuevos = 0
        for ev in eventos:
            h = self.hash_evento(ev)
            if h not in self._hashes:
                self._hashes[h] = ahora
                nuevos += 1
        return nuevos

    def filtrar_nuevos(self, eventos: List[Dict]) -> List[Dict]:
        """Devuelve solo los eventos NO vistos (sin tocar la caché aún).

        El estado real se actualiza con registrar_vistos()/guardar().
        """
        return [ev for ev in eventos if not self.es_visto(ev)]

    @property
    def total(self) -> int:
        return len(self._hashes)


if __name__ == "__main__":
    demo = [
        {"nombre": "Festival Psytrance", "fecha": "2026-08-15", "lugar": "Barcelona"},
        {"nombre": "Darkpsy Night", "fecha": "2026-09-01", "lugar": "Berlín"},
    ]
    dedup = Deduplicador()
    nuevos = dedup.filtrar_nuevos(demo)
    print(f"Hash de prueba: {Deduplicador().hash_evento(demo[0])[:16]}...")
    print(f"Filtrados nuevos: {len(nuevos)} (caché actual: {dedup.total})")
