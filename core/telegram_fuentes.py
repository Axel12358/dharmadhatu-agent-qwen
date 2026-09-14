"""Descubrimiento de eventos vía canales públicos de Telegram (t.me/s/<canal>).

Ventaja frente a buscadores/Facebook:
- t.me/s es server-side (no requiere login ni proxy).
- No está bloqueado por los buscadores ni por Meta.
- Se usa el proxy activo (residencial de terceros si está configurado, si no Tor).
  NUNCA se usa la IP real del usuario.

Cada post público expone fecha (<time datetime>) y texto, de donde extraemos
candidatos a eventos 2025-2027 que luego el pipeline normaliza.
"""
from __future__ import annotations

import re
import csv
from pathlib import Path

from core.http_client import get_html

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SALIDA = PROJECT_ROOT / "eventos_telegram.csv"

# Canales curados de psytrance / festivales / sellos (públicos).
CANALES = [
    "psytrance", "goa", "psytrancefestivals", "psychedelictrance",
    "fullonnation", "darkpsy", "shankra", "boomfestival", "ozora",
    "voovexperience", "universecestival", "atmanfestival", "hadra",
    "visionquestfest", "glastonburypsy", "tribalmix", "suntriprecords",
    "nanobeat", "digestivepsy", "psychedeliccircus", "lostinpsy",
    "ayahuascaexperience", "sensoriumfestival", "gatheringfest",
    # Descubiertos por dorks DDG/Tor (Sep 2026): canales reales activos
    "goatrancechannel", "goatranceforever", "tranceportalSOL",
]

_KEYWORDS = re.compile(
    r"\b(festival|party|rave|line[- ]?up|event|gathering|open[- ]?air|"
    r"camping|ticket|pass|stage|dj set|live act|retreat|celebration)\b",
    re.I,
)
_ANIO = re.compile(r"\b20(?:2[5-7])\b")
_FECHA = re.compile(r"\b(20[0-9]{2})[-/.](0?[1-9]|1[0-2])([-/.](0?[1-9]|[12][0-9]|3[01]))?\b")
_POST_RE = re.compile(
    r'<a class="tgme_widget_message_date" href="(https://t\.me/[^"]+/\d+)"[^>]*>'
    r'<time[^>]*datetime="([^"]+)"',
    re.S,
)
_TEXTO_RE = re.compile(r'<div class="tgme_widget_message_text[^"]*">(.*?)</div>', re.S)
_TAG = re.compile(r"<[^>]+>")


def _limpiar(html: str) -> str:
    txt = _TAG.sub(" ", html)
    txt = re.sub(r"&amp;", "&", txt)
    txt = re.sub(r"&quot;", '"', txt)
    txt = re.sub(r"&#39;|&apos;", "'", txt)
    txt = re.sub(r"\s+", " ", txt).strip()
    return txt


def _es_evento(txt: str) -> bool:
    return bool(_KEYWORDS.search(txt) and (_ANIO.search(txt) or _FECHA.search(txt)))


def extraer_eventos_canal(canal: str, timeout: int = 25):
    """Devuelve lista de dicts candidato para un canal."""
    html = get_html(f"https://t.me/s/{canal}", timeout=timeout)
    if not html or "tgme_widget_message" not in html:
        return []
    out = []
    for m in _POST_RE.finditer(html):
        link, dt = m.group(1), m.group(2)
        bloque_fin = html.find("</div>", m.end())
        bloque = html[m.start():bloque_fin if bloque_fin != -1 else m.end() + 4000]
        tm = _TEXTO_RE.search(bloque)
        texto = _limpiar(tm.group(1)) if tm else ""
        if not _es_evento(texto):
            continue
        anio = _ANIO.search(texto)
        fecha = anio.group(0) if anio else ""
        out.append({
            "nombre": f"{canal}: {texto[:80]}",
            "fecha": fecha,
            "lugar": "",
            "pais": "",
            "organizador": "",
            "email": "",
            "link": link,
            "fuente": "telegram",
            "texto": texto[:500],
        })
    return out


def escanear(canales=None, timeout: int = 25):
    canales = canales or CANALES
    todos = []
    for c in canales:
        try:
            evs = extraer_eventos_canal(c, timeout=timeout)
        except Exception as e:  # nunca romper el bucle por un canal
            evs = []
            print(f"  [telegram] {c}: error {e}")
        if evs:
            print(f"  [telegram] {c}: {len(evs)} candidato(s)")
        todos.extend(evs)
    if todos:
        campos = ["nombre", "fecha", "lugar", "pais", "organizador",
                  "email", "link", "fuente", "texto"]
        with SALIDA.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=campos)
            w.writeheader()
            w.writerows(todos)
    return todos


if __name__ == "__main__":
    res = escanear()
    print(f"Total candidatos Telegram: {len(res)} -> {SALIDA}")
