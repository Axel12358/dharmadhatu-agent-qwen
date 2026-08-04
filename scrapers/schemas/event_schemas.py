#!/usr/bin/env python3
"""
Pydantic Schemas — Contratos de datos para eventos (Contract-First).
Validación estricta antes de publicar. Un schema por fuente.
"""

from datetime import datetime
from enum import Enum
from typing import List, Optional
from pydantic import BaseModel, Field, HttpUrl, field_validator, model_validator
import re


class Subgenero(str, Enum):
    DARKPSY = "darkpsy"
    FOREST = "forest"
    HITECH = "hitech"
    PROGRESSIVE = "progressive"
    FULLON = "fullon"
    GOA = "goa"
    SUOMISAUNDI = "suomisaundi"
    ZENONESQUE = "zenonesque"
    PSYCORE = "psycore"
    PSYBIENT = "psybient"
    OTRO = ""

    @classmethod
    def from_text(cls, text: str) -> "Subgenero":
        t = (text or "").lower()
        for sg in cls:
            if sg.value and sg.value in t:
                return sg
        return cls.OTRO


class TipoLugar(str, Enum):
    CLUB = "club"
    FESTIVAL = "festival"
    OPEN_AIR = "open_air"
    WAREHOUSE = "warehouse"
    BEACH = "beach"
    FOREST = "forest"
    OTHER = "other"


class EventoBase(BaseModel):
    """Campos comunes a todos los eventos extraídos."""
    nombre: str = Field(..., min_length=3, max_length=200, description="Nombre del evento")
    fecha: str = Field(..., description="YYYY-MM-DD o N/A")
    lugar: str = Field(..., max_length=200, description="Venue/sala")
    ciudad: str = Field(..., max_length=100)
    pais: str = Field(default="España", max_length=50)
    subgenero: Subgenero = Field(default=Subgenero.OTRO)
    organizador: str = Field(default="Desconocido", max_length=150)
    url: HttpUrl
    descripcion: str = Field(default="", max_length=1000)
    confidence: float = Field(ge=0.0, le=1.0, description="Confianza 0-1")
    source_strategy: str = Field(..., description="Estrategia que produjo este evento")
    fuente: str = Field(..., description="instagram, facebook_groups, facebook_public, goabase, songkick, resident_advisor")
    fecha_extraccion: datetime = Field(default_factory=datetime.utcnow)
    tipo_lugar: TipoLugar = Field(default=TipoLugar.OTHER)
    tags: List[str] = Field(default_factory=list)

    @field_validator("fecha")
    @classmethod
    def validar_fecha(cls, v: str) -> str:
        if v == "N/A":
            return v
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", v):
            raise ValueError("fecha debe ser YYYY-MM-DD o N/A")
        try:
            datetime.strptime(v, "%Y-%m-%d")
        except ValueError:
            raise ValueError("fecha inválida")
        return v

    @field_validator("subgenero", mode="before")
    @classmethod
    def coerce_subgenero(cls, v):
        if isinstance(v, str):
            return Subgenero.from_text(v)
        return v

    @model_validator(mode="after")
    def normalizar_ciudad_pais(self):
        # Normalizaciones básicas
        ciudad_map = {
            "bcn": "Barcelona", "barcelona": "Barcelona", "madrid": "Madrid",
            "berlin": "Berlín", "london": "Londres", "paris": "París",
            "amsterdam": "Ámsterdam", "lisbon": "Lisboa", "goa": "Goa",
        }
        c = self.ciudad.strip().lower()
        self.ciudad = ciudad_map.get(c, self.ciudad.strip().title())

        pais_map = {
            "españa": "España", "spain": "España", "alemania": "Alemania",
            "germany": "Alemania", "uk": "Reino Unido", "portugal": "Portugal",
            "india": "India", "brasil": "Brasil", "mexico": "México",
        }
        p = self.pais.strip().lower()
        self.pais = pais_map.get(p, self.pais.strip().title())
        return self


class InstagramEvent(EventoBase):
    """Evento extraído de Instagram (hashtag/post)."""
    fuente: str = "instagram"
    post_shortcode: Optional[str] = None
    hashtag_origen: Optional[str] = None
    location_instagram: Optional[str] = None  # location tag del post


class FacebookEvent(EventoBase):
    """Evento extraído de Facebook (grupo/página pública)."""
    fuente: str = Field(default="facebook_groups")
    grupo_id: Optional[str] = None
    grupo_nombre: Optional[str] = None
    evento_fb_id: Optional[str] = None
    asistentes: Optional[int] = None
    interesados: Optional[int] = None


class GoabaseEvent(EventoBase):
    """Evento de Goabase."""
    fuente: str = "goabase"
    goabase_id: Optional[str] = None
    artistas: List[str] = Field(default_factory=list)


class SongkickEvent(EventoBase):
    """Evento de Songkick."""
    fuente: str = "songkick"
    songkick_id: Optional[str] = None
    artistas: List[str] = Field(default_factory=list)


class ResidentAdvisorEvent(EventoBase):
    """Evento de Resident Advisor."""
    fuente: str = "resident_advisor"
    ra_id: Optional[str] = None
    artistas: List[str] = Field(default_factory=list)


# Union type para validación genérica
EventoCualquiera = InstagramEvent | FacebookEvent | GoabaseEvent | SongkickEvent | ResidentAdvisorEvent


def validar_evento(dict_data: dict) -> EventoCualquiera:
    """Valida y devuelve instancia tipada según campo 'fuente'."""
    fuente = dict_data.get("fuente", "instagram")
    if fuente == "instagram":
        return InstagramEvent(**dict_data)
    elif fuente in ("facebook_groups", "facebook_public"):
        return FacebookEvent(**dict_data)
    elif fuente == "goabase":
        return GoabaseEvent(**dict_data)
    elif fuente == "songkick":
        return SongkickEvent(**dict_data)
    elif fuente == "resident_advisor":
        return ResidentAdvisorEvent(**dict_data)
    else:
        return EventoBase(**dict_data)


def evento_a_dict(evento: EventoBase) -> dict:
    """Serializa a dict plano para JSON/storage."""
    d = evento.model_dump()
    # Convertir enums a string
    d["subgenero"] = evento.subgenero.value
    d["tipo_lugar"] = evento.tipo_lugar.value
    d["fecha_extraccion"] = evento.fecha_extraccion.isoformat()
    d["url"] = str(evento.url)
    return d


if __name__ == "__main__":
    # Test rápido
    test = {
        "nombre": "Forest Psytrance Festival",
        "fecha": "2026-08-15",
        "lugar": "Bosque Mágico",
        "ciudad": "BCN",
        "pais": "spain",
        "subgenero": "forest",
        "organizador": "@darkforestcrew",
        "url": "https://instagram.com/p/ABC123",
        "descripcion": "Forest vibes en Barcelona",
        "confidence": 0.92,
        "source_strategy": "playwright_post",
        "fuente": "instagram",
        "hashtag_origen": "forestpsytrance",
    }
    ev = validar_evento(test)
    print("✅ Válido:", ev.nombre, "|", ev.ciudad, "|", ev.subgenero, "| conf:", ev.confidence)
    print("Dict:", evento_a_dict(ev))