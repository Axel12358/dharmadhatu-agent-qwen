#!/usr/bin/env python3
"""
Verificación de eventos clasificados como "general" (columna subgenero).

Aplica a TODA la columna subgenero=="general" (47 filas actuales: 45 Goabase +2 Ektoplazm),
no solo a Gaggalacka/Free Earth. Verifica si son festivales renombrados psytrance
vía búsqueda general en motores que funcionan por Tor (DDG-lite, Mojeek) sin login,
sin API key, nunca IP real (socks5h://127.0.0.1:9050).

Estrategia (prioridad alta, gratis, local):
1. Para cada evento general, construye dork: '"{nombre}" psytrance'
2. Busca en lite.duckduckgo.com/lite/?q= via core.http_client.get_html (Tor)
   Fallback: mojeek.com/search?q= si DDG bloquea (403/0).
3. Si snippet/título contiene psytrance|goa|forest|darkpsy|progressive|hitech|psychill|fullon|twilight → confirma como psytrance
4. Fallback Goabase: si fuente in ["Goabase","Ektoplazm","Psytrance.pl"] y no hubo hit pero es festival renombrado, clasifica como psytrance (portal especializado)
5. Aditivo: nunca resta, solo reclasifica general → psytrance/otro; deja general si no verificable.

Uso:
    python3 core/verificar_general.py            # verifica y reescribe eventos_encontrados.csv (con backup)
    python3 core/verificar_general.py --dry-run  # solo muestra sin escribir
    python3 -c "from core.verificar_general import verificar_general; print(verificar_general(dry_run=True))"
"""
from __future__ import annotations
import csv
import re
import sys
import time
import urllib.parse
from pathlib import Path
from typing import Dict, List, Tuple

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

try:
    from core.http_client import get_html as _get_html_tor
except Exception:
    _get_html_tor = None

try:
    from scrapers.event_extractor import EventExtractor
    _extractor = EventExtractor()
except Exception:
    _extractor = None

CSV_TODOS = _PROJECT_ROOT / "eventos_encontrados.csv"
PSY_KEYWORDS = [
    "psytrance", "psy trance", "goa trance", "goatrance", "forest",
    "darkpsy", "dark psy", "progressive", "hitech", "hi-tech",
    "psychill", "psybient", "fullon", "full on", "twilight",
    "psycore", "suomisaundi", "zenon", "ozora", "boom festival",
]

# Regex para detectar psy en snippet
PSY_RE = re.compile(r"\b(" + "|".join(re.escape(k) for k in PSY_KEYWORDS) + r")\b", re.I)

# Fuentes que son portales psy por definición (general → psytrance sin búsqueda)
FUENTES_PSY = {"goabase", "ektoplazm", "psytrance.pl", "isratrance"}


def _buscar_dork_tor(dork: str, timeout: int = 12) -> Tuple[List[Dict], bool]:
    """Busca dork en DDG-lite vía Tor. Devuelve (resultados, bloqueado)."""
    if _get_html_tor is None:
        return [], False
    # DDG-lite
    url = "https://lite.duckduckgo.com/lite/?q=" + urllib.parse.quote(dork)
    html = _get_html_tor(url, timeout=timeout)
    if not html:
        return [], True
    if "unusual traffic" in html.lower() or "captcha" in html.lower():
        return [], True
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    resultados = []
    # DDG-lite usa <a> con href que contiene uddg= y snippet en td
    for a in soup.select("a"):
        href = a.get("href", "")
        if "uddg=" in href:
            m = re.search(r"uddg=([^&]+)", href)
            if m:
                url_real = urllib.parse.unquote(m.group(1))
                titulo = a.get_text(" ", strip=True)
                # snippet: siguiente td o texto cercano
                parent = a.find_parent("tr")
                snippet = parent.get_text(" ", strip=True) if parent else titulo
                resultados.append({"url": url_real, "titulo": titulo, "snippet": snippet})
        elif href.startswith("http") and "duckduckgo" not in href:
            titulo = a.get_text(" ", strip=True)
            if len(titulo) > 10:
                snippet = titulo
                resultados.append({"url": href, "titulo": titulo, "snippet": snippet})
        if len(resultados) >= 8:
            break
    # Filtrar solo resultados con psy keyword en titulo/snippet/url
    return resultados, False


def _verificar_psy_via_busqueda(nombre: str, timeout: int = 12) -> bool:
    """True si búsqueda '"nombre" psytrance' devuelve hit psy."""
    dork = f'"{nombre}" psytrance'
    resultados, bloqueado = _buscar_dork_tor(dork, timeout=timeout)
    if bloqueado or not resultados:
        # Fallback Mojeek vía Tor
        if _get_html_tor is not None:
            try:
                url_m = "https://www.mojeek.com/search?q=" + urllib.parse.quote(dork)
                html_m = _get_html_tor(url_m, timeout=timeout)
                if html_m and PSY_RE.search(html_m):
                    return True
            except Exception:
                pass
        return False
    # Si algún resultado contiene psy keyword en titulo/snippet, confirma
    for r in resultados:
        texto = f"{r.get('titulo','')} {r.get('snippet','')} {r.get('url','')}"
        if PSY_RE.search(texto):
            return True
        # También si el dominio es Goabase/psymedia/psy portal, es hit
        if "goabase" in r.get("url","").lower() or "psytrance" in r.get("url","").lower():
            return True
    return False


def verificar_general(dry_run: bool = False, timeout_por_busqueda: int = 12,
                      max_verificar: int = 60) -> Dict:
    """Verifica todos los general y reclasifica. Devuelve stats."""
    if not CSV_TODOS.exists():
        print(f"  ❌ No existe {CSV_TODOS}")
        return {"total_general": 0, "verificados": 0, "reclasificados": 0}

    with CSV_TODOS.open(encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        fieldnames = reader.fieldnames or ["nombre","fecha","lugar","pais","continente","subcontinente","fuente","organizador","email","link","subgenero","tipo_lugar"]

    general_idx = [i for i, r in enumerate(rows) if (r.get("subgenero") or "").strip().lower() == "general"]
    total = len(general_idx)
    print(f"🔍 Verificar general: {total} eventos en columna subgenero=='general'")

    reclasificados = 0
    verificados = 0
    detalle = []

    for idx in general_idx[:max_verificar]:
        r = rows[idx]
        nombre = (r.get("nombre") or "").strip()
        fuente = (r.get("fuente") or "").strip()
        organizador = (r.get("organizador") or "").strip()
        lugar = (r.get("lugar") or "").strip()
        if not nombre or len(nombre) < 3:
            continue

        # 1) Fuente psy especializada → verificación implícita (sin búsqueda, rápido)
        es_fuente_psy = fuente.lower() in FUENTES_PSY

        # 2) Búsqueda Tor para confirmar (festival renombrado como Free Earth/Gaggalacka)
        hit = False
        if len(nombre) >= 4:
            # Para Goabase renombrados, búsqueda es rápida y confirma
            try:
                hit = _verificar_psy_via_busqueda(nombre, timeout=timeout_por_busqueda)
                verificados += 1
                time.sleep(1.2)  # rate limit Tor
            except Exception:
                hit = False

        nuevo_sub = r.get("subgenero")
        motivo = "no verificado"
        if hit:
            nuevo_sub = "psytrance"
            motivo = "hit búsqueda Tor DDG/Mojeek"
            reclasificados += 1
        elif es_fuente_psy:
            # Goabase general sin hit pero portal psy → psytrance (documentado README:42)
            nuevo_sub = "psytrance"
            motivo = "fuente psy especializada"
            reclasificados += 1
        else:
            # 3) Fallback extractor sobre nombre+organizador+lugar
            if _extractor is not None:
                try:
                    texto = f"{nombre} {organizador} {lugar}"
                    cand = _extractor.clasificar_subgenero(texto)
                    if cand and cand != "general":
                        nuevo_sub = cand
                        motivo = f"extractor {cand}"
                        reclasificados += 1
                except Exception:
                    pass

        if nuevo_sub != r.get("subgenero"):
            print(f"  ✅ {nombre[:55]:55} | {fuente:20} | general → {nuevo_sub} ({motivo})")
            rows[idx]["subgenero"] = nuevo_sub
            detalle.append({"nombre": nombre, "fuente": fuente, "nuevo": nuevo_sub, "motivo": motivo})
        else:
            print(f"  ▫️ {nombre[:55]:55} | {fuente:20} | general (sin hit)")

    if reclasificados and not dry_run:
        backup = CSV_TODOS.with_suffix(f".csv.bak_{int(time.time())}")
        try:
            CSV_TODOS.rename(backup)
            print(f"  💾 Backup: {backup}")
        except Exception:
            pass
        # Asegurar continente/subcontinente etc existen
        with CSV_TODOS.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            w.writeheader()
            for r in rows:
                w.writerow(r)
        print(f"  ✅ CSV actualizado: {CSV_TODOS} ({len(rows)} filas, {reclasificados} reclasificados)")
    elif dry_run:
        print(f"  🔍 dry-run: {reclasificados}/{total} reclasificarían (no escrito)")

    return {"total_general": total, "verificados": verificados, "reclasificados": reclasificados, "detalle": detalle}


if __name__ == "__main__":
    dry = "--dry-run" in sys.argv
    max_n = 60
    for a in sys.argv:
        if a.startswith("--max="):
            try: max_n = int(a.split("=")[1])
            except: pass
    res = verificar_general(dry_run=dry, max_verificar=max_n)
    print(f"\nResumen: total={res['total_general']} verificados={res['verificados']} reclasificados={res['reclasificados']}")
