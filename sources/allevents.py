#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""adapter para allevents.in.

SourceAdapter que rasga allevents.in buscando eventos de psytrance en la ciudad
indicada. Devuelve Lead objects. No usa Tor, no hace rotación de identidad.

Regla de descubrimiento: allevents.in indexa eventos públicos y los resultados
vienen con URLs destino reales (no redirecciones).
"""
from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime
from typing import List, Optional

from sources.base import SourceAdapter, Lead, normalize_promoter_name


def _strip_accents(s: str) -> str:
    import unicodedata
    s = unicodedata.normalize("NFD", s)
    s = s.encode("ascii", "ignore").decode("ascii")
    return "".join(ch for ch in s if ch.isalnum() or ch.isspace())


def _normalize_city(city: str) -> str:
    return _strip_accents(city.lower().strip())


def _parse_date(text: str) -> date | None:
    """Intenta parsear fechas en formato dd/mm/yyyy o yyyy-mm-dd."""
    text = text.strip()
    # dd/mm/yyyy
    m = re.search(r"(\d{2})/(\d{2})/(\d{4})", text)
    if m:
        try:
            return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        except ValueError:
            pass
    # yyyy-mm-dd
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", text)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            pass
    # Month name formats (ej. "15 de marzo de 2026")
    try:
        return datetime.strptime(text, "%d de %B de %Y").date()
    except ValueError:
        pass
    return None


def _extract_genre(text: str) -> tuple[str, ...]:
    psytrance_keywords = {"psytrance", "darkpsy", "forest", "goa", "hitech",
                          "psychill", "fullon", "psycore", "suomisaundi"}
    lowered = text.lower()
    found = [kw for kw in psytrance_keywords if kw in lowered]
    return tuple(found)


class AlleventsInAdapter:
    """Adapter para allevents.in.

    Nota: este adaptador usa requests sin Tor (según regla 9 del brief).
    Si la fuente bloquea requests puro, se descartaría y se pasaría a la siguiente.
    """

    name = "allevents.in"

    def fetch(self, city: str, since: date) -> list[Lead]:
        """Return leads from allevents.in for `city` since `since`.

        La lógica es muy simple: consultar la URL de la ciudad, parsear los
        primeros resultados y devolver Lead objects.
        """
        import requests
        from bs4 import BeautifulSoup

        city_norm = _normalize_city(city)
        url = f"https://www.allevents.in/{city_norm}/psytrance"
        try:
            resp = requests.get(url, timeout=15,
                                headers={"User-Agent":
                                        "booking-pipeline/1.0 (contact@example.com)"})
        except Exception:
            return []

        if resp.status_code != 200:
            return []

        soup = BeautifulSoup(resp.text, "lxml")
        leads: list[Lead] = []

        for article in soup.select("article.event-card"):
            # Título / enlace
            h2 = article.select_one("h2 a")
            if not h2 or not h2.get("href"):
                continue
            external_id = h2["href"].split("/")[-1] or None
            url = h2["href"] if h2["href"].startswith("http") else f"https://www.allevents.in{h2['href']}"

            titulo = h2.get_text(strip=True)

            # Fecha / ciudad
            date_elem = article.select_one(".event-meta time")
            event_date = _parse_date(date_elem.get_text(strip=True)) if date_elem else None

            # Promotor (a veces viene en el meta)
            promoter_match = re.search(r"por\s+(.+?)$", article.get_text(strip=True), re.I)
            promoter = promoter_match.group(1).strip() if promoter_match else None

            # Ciudad en el meta a veces
            city_meta = article.select_one(".event-meta .city")
            city_text = city_meta.get_text(strip=True) if city_meta else None

            # Género
            genre = _extract_genre(titulo or "")

            if not event_date:
                continue

            lead = Lead(
                source=self.name,
                external_id=external_id or url,
                promoter=promoter or "",
                event=titulo,
                city=city_text or city,
                country="IT",  # allevents.in es italiano, pero ponemos lo que aparezca
                event_date=event_date,
                url=url,
                contact_url=None,
                contact_email=None,
                genre_tags=genre,
            )
            leads.append(lead)

        return leads


# Instanciar y registrar automáticamente al importarse
_allevents = AlleventsInAdapter()
register(_allevents)