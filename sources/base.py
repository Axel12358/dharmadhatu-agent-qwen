#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""SourceAdapter protocol + Lead dataclass.

No importa nada del ledger. Solo define la estructura de un Lead y el contrato
de los adaptadores. Este módulo es autónomo: puede importarse y unit-testearse
sin que existan core/ ni cli.py.
"""

from __future__ import annotations
from dataclasses import dataclass
from datetime import date
from typing import Protocol, Optional, Tuple


@dataclass(frozen=True)
class Lead:
    """Un lead (promotor + evento) extraído de una fuente.

    Identity rules:
    - Within a source, unicity es (source, external_id) via UNIQUE constraint en SQLite.
    - Across sources, posible duplicate es (normalized_promoter_name, event_date).
    - normalized_promoter_name = casefolded, accents stripped, punctuation removed,
      whitespace collapsed.
    """
    source: str
    external_id: str
    promoter: str
    event: Optional[str] = None
    city: Optional[str] = None
    country: Optional[str] = None
    event_date: Optional[date] = None
    url: str = ""
    contact_url: Optional[str] = None
    contact_email: Optional[str] = None
    genre_tags: Tuple[str, ...] = ()


def _strip_accents(s: str) -> str:
    """ casefold + quita acentos + quita signos de puntuación + colapsa espacios."""
    import unicodedata
    s = unicodedata.normalize("NFD", s)
    s = s.encode("ascii", "ignore").decode("ascii")
    return "".join(ch for ch in s if ch.isalnum() or ch.isspace())


def normalize_promoter_name(name: str) -> str:
    """Normalized promoter name for cross-source duplicate detection."""
    return _strip_accents(name)


class SourceAdapter(Protocol):
    """Protocolo para cada adaptador de fuente.

    Cada fuente implementa un módulo en sources/ que cumpla este protocolo.
    El protocolo es intencionalmente flojo en I/O: fetch() recibe ciudad y fecha,
    devuelve listado de Lead objects. El adaptador decide qué hacer con fechas,
    filtros, etc.
    """

    name: str

    def fetch(self, city: str, since: date) -> List[Lead]:
        """Return leads nuevo desde `since` para la ciudad dada."""
        ...


# Registry: lista de adaptadores activos.cli.py los inscribirá aquí.
_adapters: list[SourceAdapter] = []


def register(adapter: SourceAdapter) -> None:
    """Add adapter to the global registry."""
    _adapters.append(adapter)


def get_adapters() -> list[SourceAdapter]:
    """Return list of registered adapters."""
    return list(_adapters)