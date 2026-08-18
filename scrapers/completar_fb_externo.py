#!/usr/bin/env python3
"""
Completar N/A de Facebook usando búsquedas externas (DuckDuckGo / Google).

Problema: algunas páginas de eventos de Facebook (sin login) no muestran lugar o
fecha. Para esos eventos, buscamos en DuckDuckGo una consulta basada en el
título del evento + fecha conocida para encontrar un foro, sitio de entradas o
página externa donde el lugar/fecha sea público.

Antes de buscar se aplica `_es_basura()`: los eventos que son código JS suelto,
URLs sin limpiar, webinars o cadenas irrelevantes se ELIMINAN del CSV (no se
intentan completar).

Filosofía: **suma, nunca resta**. Solo se agregan valores si están vacíos;
nunca se sobreescribe una fecha/lugar ya conocido. Si el valor extraído no
parece verosímil (muy largo, es un bloque de toda la página, o no es un
lugar/fecha claro), se descarta y se deja N/A.

Uso standalone:
    python3 scrapers/completar_fb_externo.py
    python3 scrapers/completar_fb_externo.py --limit 3

Uso como función (desde main.py):
    from scrapers.completar_fb_externo import completar_fb_externo
    completar_fb_externo()
"""

import csv
import json
import os
import random
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright
from playwright_stealth import Stealth

# -------------------------------------------------------------------- #
# Constantes
# -------------------------------------------------------------------- #

PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
DEFAULT_CSV = str(Path(PROJECT_ROOT) / "eventos_encontrados.csv")
RESUMEN_FILE = str(Path(PROJECT_ROOT) / "eventos_completados_externo.json")

MAX_WORKERS = 3
PAGE_TIMEOUT_MS = 15000  # 15 segundos por página
MAX_INTENTOS = 2
MAX_RESULTADOS_VISITADOS = 3

# Saltos humanos entre solicitudes (segundos)
SLEEP_MIN, SLEEP_MAX = 1.5, 3.5

# Indicadores de bloqueo fuerte (captcha / checkpoint / detección de tráfico)
BLOQUEO_MARCADORES = [
    "checkpoint", "login.php", "nuestro sistema ha detectado", "enter the code",
    "confirm you", "our systems have detected unusual traffic", "captcha",
    "you must log in", "log in to continue", "your request has been blocked",
    "access denied", "demasiadas solicitudes", "too many requests",
    "unsupported browser", "navegador no compatible",
]

CSV_KEYS = ["nombre", "fecha", "lugar", "pais", "continente", "subcontinente",
            "fuente", "organizador", "email", "link", "subgenero"]

NA_VALORES = {"", "N/A", "n/a", "NA", "na", "none", "-", "?",
              "desconocido", "desconocida", "no disponible",
              "fecha no disponible", "unknown", "not available", "tba", "TBD"}

# Palabras que delatan contenido no-psytrance (webinars, soporte, capacitación)
NO_PSY_KEYWORDS = ("webinar", "webinar", "capacitación", "capacitacion",
                   "soporte", "support", "tutorial", "training", "curso",
                   "workshop", "conferencia", "conference")

USER_AGENT = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_2 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 "
    "Mobile/15E148 Safari/604.1"
)

# -------------------------------------------------------------------- #
# Utilidades
# -------------------------------------------------------------------- #

def _es_na(valor: Optional[str]) -> bool:
    """Devuelve True si el valor se considera no disponible."""
    if valor is None:
        return True
    return str(valor).strip().lower() in NA_VALORES


def _limpiar_texto(texto: Optional[str]) -> str:
    if not texto:
        return ""
    return re.sub(r"\s+", " ", texto).strip()


def _es_basura(nombre: Optional[str], fuente: str = "",
               row: Optional[dict] = None) -> bool:
    """Devuelve True si el "nombre" es basura y debe eliminarse del CSV.

    Detecta:
    - código JS suelto ("use strict", "var e=window", etc.)
    - URLs sin limpiar ("Facebook facebook.com › events › ...")
    - webinars / soporte / capacitación (no psytrance)
    - fragmento roto muy corto (< 15 alfanuméricos) SOLO para fuente Facebook
      y SOLO si el evento está realmente incompleto (sin fecha LUGAR). Nunca
      se borra un evento completo aunque sea corto ("The Abyss" es real).

    Se es conservador para respetar "suma, nunca resta": solo se elimina lo
    inequívocamente basura. Nunca se borra por longitud un evento de
    Goabase/RA/Ektoplazm/etc., solo se marcan posibles fragmentos de
    Facebook.
    """
    if not nombre:
        return True
    n = str(nombre).strip()

    src = str(fuente or "").lower()
    es_fb = "facebook" in src

    # Normalizar para la comprobación (bold unicode → base; solo alnum)
    import unicodedata
    normalized = unicodedata.normalize("NFKD", n)
    normalized = re.sub(r"[\u2190-\u21FF\u2764\uFE0F\u2600-\u27BF\uD800-\uDFFF]",
                        "", normalized, flags=re.UNICODE)
    n_decor = re.sub(r"[^a-zA-Z0-9]", "", normalized)
    n_low = n_decor.lower()

    if not n_decor:
        return True

    # Código JS suelto (típico de scraping que capturó un archivo .js)
    if "usestrict" in n_low or "varewindow" in n_low or \
       "windowperformance" in n_low or "getelementbyid" in n_low or \
       "documentgetelement" in n_low:
        return True
    if re.search(r"\(function\(", n, re.IGNORECASE):
        return True

    # URL sin limpiar / breadcrumbs de Facebook ("facebook.com › events › ...")
    if "facebook.com" in n.lower() and (" › " in n or "events" in n_low):
        return True
    if "m.facebook.com" in n.lower() and ("›" in n or "events" in n_low):
        return True

    # Contenido no-psytrance (webinars, soporte, capacitación, tutoriales)
    for kw in NO_PSY_KEYWORDS:
        kw_norm = re.sub(r"[^a-zA-Z0-9]", "", kw)
        if kw_norm and kw_norm in n_low:
            return True

    # Fragmento roto muy corto: solo URL-like ("facebook.com ›"), mini-fragmento
    # minúsculas continuas, o evento FB sin fecha NI lugar. Nunca borrar nombre
    # corto pero con datos reales.
    if es_fb and len(n_decor) < 15:
        # Si el evento tiene fecha O lugar/org rellenos, es real → no borrar.
        if row:
            tiene_datos = any(
                str(row.get(k, "")).strip() not in ("", "N/A", "nan")
                for k in ("fecha", "lugar", "organizador")
            )
            if not tiene_datos:
                return True
        else:
            # Sin contexto de fila: fragmento pegado sin espacio es basura,
            # pero "Namaste" (una palabra, con mayúscula) no lo borramos.
            if " " not in n.strip() and n_decor == n_low:
                return True

    return False


def _es_lugar_verosímil(texto: str) -> bool:
    t = texto.lower()
    if len(texto) > 200 or len(texto) < 4:
        return False
    # "Lugar, Ciudad" o "Ciudad, País"
    if re.search(r"\b[A-ZÀ-ÿ][A-Za-zÀ-ÿ'’\-\. ]{2,60},\s*[A-ZÀ-ÿ][A-Za-zÀ-ÿ'’\-\. ]{2,45}\b", texto):
        return True
    # Dirección con número + código postal
    if re.search(r"\b[A-ZÀ-ÿ][A-Za-zÀ-ÿ'’\-\. ]{2,60}\s+\d{1,4}[a-z]?,\s*\d{4,5}\b", texto):
        return True
    if any(b in t for b in ("evento por", "hosted by", "organizado por",
                            "festival de", "http", "www.", "facebook")):
        return False
    return False


def _es_fecha_verosímil(texto: str) -> bool:
    t = texto.lower()
    if len(texto) > 100 or len(texto) < 4:
        return False
    meses = ("enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
             "agosto", "septiembre", "octubre", "noviembre", "diciembre",
             "january", "february", "march", "april", "june", "july", "august",
             "september", "october", "november", "december", "jan", "feb", "mar",
             "apr", "jun", "jul", "aug", "sep", "oct", "nov", "dec", "ene")
    if any(m in t for m in meses):
        return True
    if re.search(r"\b\d{1,2}[-/\.]\d{1,2}[-/\.]\d{2,4}\b", t):
        return True
    if re.search(r"\b\d{4}[-/\.]\d{1,2}[-/\.]\d{1,2}\b", t):
        return True
    return False


def _normalizar_fecha(texto: str) -> Optional[str]:
    """Devuelve una fecha ISO (YYYY-MM-DD) a partir de una línea candidata."""
    import calendar
    t = texto.strip()
    if not t or len(t) > 120 or not _es_fecha_verosímil(t):
        return None

    # Año primero (ISO)
    for m in re.finditer(r"\b(\d{4})[-/\.](\d{1,2})[-/\.](\d{1,2})\b", t):
        a, mth, d = m.group(1), m.group(2), m.group(3)
        try:
            return f"{a}-{int(mth):02d}-{int(d):02d}"
        except ValueError:
            continue

    # Día/mes/año
    for m in re.finditer(r"\b(\d{1,2})[-/\.](\d{1,2})[-/\.](\d{2,4})\b", t):
        d, mth, a = m.group(1), m.group(2), m.group(3)
        if len(a) == 2:
            a = f"20{a}"
        try:
            return f"{a}-{int(mth):02d}-{int(d):02d}"
        except ValueError:
            continue

    # Mes + día (+ año opcional)
    meses = {"enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5,
             "junio": 6, "julio": 7, "agosto": 8, "septiembre": 9, "octubre": 10,
             "noviembre": 11, "diciembre": 12, "january": 1, "february": 2,
             "march": 3, "april": 4, "may": 5, "june": 6, "july": 7,
             "august": 8, "september": 9, "october": 10, "november": 11,
             "december": 12, "jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6,
             "jul": 7, "aug": 8, "sep": 9, "sept": 9, "oct": 10, "nov": 11,
             "dec": 12, "ene": 1}
    for mes_name, mes_num in meses.items():
        if mes_name in t:
            d = re.search(r"\b(\d{1,2})(?:st|nd|rd|th)?\b", t)
            if not d:
                continue
            dia = int(d.group(1))
            a = re.search(r"\b(20\d{2})\b", t)
            año = int(a.group(1)) if a else None
            if not año:
                hoy = datetime.now(timezone.utc)
                año = hoy.year
                if (mes_num, dia) < (hoy.month, hoy.day):
                    año += 1
            try:
                calendar.monthrange(año, mes_num)
                return f"{año}-{mes_num:02d}-{dia:02d}"
            except ValueError:
                continue

    # Solo año ("2026") → fecha genérica de enero
    a = re.search(r"\b(20\d{2})\b", t)
    if a:
        return f"{a.group(1)}-01-01"
    return None


def _es_pagina_bloqueada(texto: str, url: str) -> bool:
    t = texto.lower()
    u = url.lower()
    if any(b in u for b in ("login", "checkpoint", "unsupportedbrowser")):
        return True
    return any(b in t for b in BLOQUEO_MARCADORES)


# -------------------------------------------------------------------- #
# Navegación con Playwright (sync) + DuckDuckGo
# -------------------------------------------------------------------- #

def _visitar_busqueda(query: str, worker_id: int = 0) -> Dict[str, str]:
    """Visita DuckDuckGo (HTML) con una consulta; devuelve cuerpo y HTML."""
    from urllib.parse import quote
    url = f"https://html.duckduckgo.com/html/?q={quote(query)}"
    datos = {"url": url, "cuerpo": "", "html": "", "error": None, "bloqueado": False}
    for intento in range(1, MAX_INTENTOS + 1):
        try:
            with Stealth().use_sync(sync_playwright()) as p:
                browser = p.chromium.launch(
                    headless=True,
                    args=["--no-sandbox", "--disable-dev-shm-usage",
                          "--disable-blink-features=AutomationControlled"],
                )
                ctx = browser.new_context(
                    user_agent=USER_AGENT,
                    viewport={"width": 1280, "height": 800},
                    locale="es-ES",
                    timezone_id="Europe/Madrid",
                )
                page = ctx.new_page()
                page.set_default_timeout(PAGE_TIMEOUT_MS)
                page.set_default_navigation_timeout(PAGE_TIMEOUT_MS)
                page.goto(url, timeout=PAGE_TIMEOUT_MS, wait_until="domcontentloaded")
                page.wait_for_timeout(random.randint(int(SLEEP_MIN * 1000),
                                                     int(SLEEP_MAX * 1000)))

                try:
                    datos["cuerpo"] = page.evaluate(
                        "document.body ? document.body.innerText : ''")
                except Exception:
                    pass
                try:
                    datos["html"] = page.content()
                except Exception:
                    pass

                if _es_pagina_bloqueada(f"{datos['cuerpo']} {datos['html'][:2000]}", url):
                    datos["bloqueado"] = True
                    print(f"     💤 worker {worker_id}: página bloqueada (intento {intento})")
                    continue

                print(f"     ✅ worker {worker_id}: búsqueda completada "
                      f"({len(datos['cuerpo'])} chars)")
                return datos
        except Exception as e:
            datos["error"] = f"{type(e).__name__}: {str(e)[:90]}"
            print(f"     ⚠️ worker {worker_id}: intento {intento} fallado: "
                  f"{datos['error']}")
    return datos


def _extraer_de_resultado(resultado: Dict[str, str], objetivo: str) -> Optional[str]:
    """Extrae lugar o fecha del HTML/cuerpo de los resultados de DuckDuckGo."""
    html = resultado.get("html", "")
    cuerpo = resultado.get("cuerpo", "")
    if not html and not cuerpo:
        return None

    soup = BeautifulSoup(html, "html.parser")
    enlaces = soup.select("a.result__url, a[class*='result__url']")
    if not enlaces:
        enlaces = soup.select("a[href]")

    # Snippets de resultados (el texto visible en la SERP ya suele bastar)
    snippets = []
    for enlace in enlaces[:MAX_RESULTADOS_VISITADOS]:
        snippet_div = enlace.find_next_sibling("div") or enlace.find_parent(
            "div", class_=True)
        if snippet_div:
            txt = snippet_div.get_text(separator=" ", strip=True)
            if txt:
                snippets.append(txt)

    if objetivo == "lugar":
        for snip in snippets:
            if not _es_lugar_verosímil(snip):
                continue
            for parte in re.split(r"\s{2,}|\n", snip):
                if len(parte) > 4 and "," in parte and _es_lugar_verosímil(parte):
                    return _limpiar_texto(parte)[:90]
        m = re.search(r"\b[A-ZÀ-ÿ][A-Za-zÀ-ÿ'’\-\. ]{2,60}\s+\d{1,4}[a-z]?,\s*\d{4,5}\b",
                      cuerpo + html)
        if m:
            return m.group(0).strip()[:90]
    elif objetivo == "fecha":
        for snip in snippets:
            for m in re.finditer(
                    r"(\d{4}[-/\.]\d{1,2}[-/\.]\d{1,2})"
                    r"|(\d{1,2}[-/\.]\d{1,2}[-/\.]\d{2,4})"
                    r"|(\d{1,2}\s+(?:de\s+)?[A-Za-z]{3,9}[a-z]*\s+(?:de\s+)?\d{4})",
                    snip):
                fech = _normalizar_fecha(m.group(0))
                if fech and fech.endswith(("01-01",)) is False or _es_fecha_verosímil(fech):
                    if fech:
                        return fech
        # Mes + día en snippet (sin año) → inferir
        for snip in snippets:
            fech = _normalizar_fecha(snip)
            if fech:
                return fech
        # Solo año
        m = re.search(r"\b(20\d{2})\b", cuerpo + html)
        if m:
            return f"{m.group(1)}-01-01"
    return None


# -------------------------------------------------------------------- #
# CSV
# -------------------------------------------------------------------- #

def _cargar_csv(csv_path: str) -> List[Dict]:
    if not Path(csv_path).exists():
        print(f"⚠️ CSV no encontrado: {csv_path}")
        return []
    with open(csv_path, encoding="utf-8") as f:
        return [dict(r) for r in csv.DictReader(f)]


def _guardar_csv(csv_path: str, eventos: List[Dict]) -> None:
    """Sobrescribe el CSV preservando el orden de columnas de CSV_KEYS."""
    tmp = csv_path + ".tmp"
    with open(tmp, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_KEYS, extrasaction="ignore")
        writer.writeheader()
        for ev in eventos:
            out = dict(ev)
            out["link"] = ev.get("link") or ev.get("url") or "N/A"
            writer.writerow(out)
    os.replace(tmp, csv_path)
    print(f"✅ CSV actualizado: {csv_path} ({len(eventos)} filas)")


def _guardar_resumen(resumen: Dict) -> None:
    tmp = RESUMEN_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(resumen, f, indent=2, ensure_ascii=False)
    os.replace(tmp, RESUMEN_FILE)
    print(f"📄 Resumen guardado: {RESUMEN_FILE}")


# -------------------------------------------------------------------- #
# Orquestación
# -------------------------------------------------------------------- #

def _construir_consulta(nombre: str, fecha: str) -> str:
    """Construye una consulta concisa de DuckDuckGo."""
    limpio = re.sub(r"\s+", " ", nombre).strip()
    if not limpio:
        return ""
    consulta = f"{limpio} psytrance"
    m = re.search(r"(20\d{2})", fecha or "")
    if m:
        consulta += f" {m.group(1)}"
    return consulta


def _procesar_evento(ev: Dict, worker_id: int) -> Dict:
    """Procesa un evento: búsqueda externa para completar fecha/lugar N/A."""
    resultado = dict(ev)
    resultado["_completado"] = False
    if not (_es_na(resultado.get("fecha")) or _es_na(resultado.get("lugar"))):
        return resultado

    nombre = resultado.get("nombre", "")
    consulta = _construir_consulta(nombre, resultado.get("fecha", ""))
    if not consulta:
        resultado["_error"] = "sin consulta"
        return resultado

    print(f"     🔍 worker {worker_id}: consulta → {consulta[:80]}…")
    res_busqueda = _visitar_busqueda(consulta, worker_id)
    if res_busqueda.get("error"):
        resultado["_error"] = res_busqueda["error"]
        return resultado
    if res_busqueda.get("bloqueado"):
        resultado["_error"] = "bloqueada"
        return resultado

    cambios = []
    if _es_na(resultado.get("fecha")):
        fech = _extraer_de_resultado(res_busqueda, "fecha")
        if fech and _es_fecha_verosímil(fech):
            resultado["fecha"] = fech
            resultado["_completado"] = True
            cambios.append("fecha")
            print(f"       ✅ worker {worker_id}: fecha → {fech}")

    if _es_na(resultado.get("lugar")):
        lug = _extraer_de_resultado(res_busqueda, "lugar")
        if lug and _es_lugar_verosímil(lug):
            resultado["lugar"] = lug
            resultado["_completado"] = True
            cambios.append("lugar")
            print(f"       ✅ worker {worker_id}: lugar → {lug}")

    if not cambios:
        print(f"       ⏭ worker {worker_id}: sin datos fiables")
    return resultado


def completar_fb_externo(csv_path: str = DEFAULT_CSV,
                         limit: Optional[int] = None,
                         max_workers: int = MAX_WORKERS) -> Dict:
    """Completa N/A de Facebook con búsquedas externas (DuckDuckGo).

    1. Filtra y ELIMINA eventos basura del CSV.
    2. Para los eventos FB restantes con N/A en fecha/lugar, busca en
       DuckDuckGo y completa los campos que falten (solo valores verosímiles).

    Args:
        csv_path: ruta del CSV consolidado.
        limit: opcional, procesa solo los primeros N candidatos.
        max_workers: eventos simultáneos (por defecto 3).

    Returns:
        dict resumen.
    """
    print("=" * 60)
    print("🔎 Completar N/A de Facebook (búsqueda externa DuckDuckGo)")
    print("📅 " + datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M"))
    print("=" * 60)

    eventos = _cargar_csv(csv_path)
    if not eventos:
        return {"total_candidatos": 0, "procesados": 0, "actualizados": 0,
                "eliminados_basura": 0, "siguientes_incompletos": []}

    # ---- Paso 1: eliminar basura de TODO el CSV (no solo FB) ----
    antes = len(eventos)
    limpiados = [ev for ev in eventos
                 if not _es_basura(ev.get("nombre"), ev.get("fuente", ""), ev)]
    eliminados = antes - len(limpiados)
    if eliminados:
        print(f"🗑 Eliminados {eliminados} eventos basura del CSV")
        for ev in eventos:
            if _es_basura(ev.get("nombre"), ev.get("fuente", ""), ev):
                print(f"   - {str(ev.get('nombre'))[:60]!r}")
        _guardar_csv(csv_path, limpiados)
        eventos = limpiados

    # ---- Paso 2: seleccionar candidatos FB con N/A ----
    candidatos = []
    for ev in eventos:
        fuente = str(ev.get("fuente", "")).lower()
        if "facebook" not in fuente:
            continue
        if _es_na(ev.get("fecha")) or _es_na(ev.get("lugar")):
            ev["_faltan"] = [c for c in ("fecha", "lugar")
                             if _es_na(ev.get(c))]
            candidatos.append(ev)
    if limit:
        candidatos = candidatos[:limit]

    print(f"📥 {len(eventos)} filas tras limpieza | {len(candidatos)} candidatos "
          f"(FB con N/A en fecha o lugar)")
    if not candidatos:
        print("🎉 No hay eventos de Facebook para buscar externamente.")
        return {"total_candidatos": 0, "procesados": 0, "actualizados": 0,
                "eliminados_basura": eliminados, "siguientes_incompletos": []}

    # ---- Paso 3: procesar en paralelo ----
    procesados = []
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futuros = {pool.submit(_procesar_evento, ev, i): ev
                   for i, ev in enumerate(candidatos)}
        for fut in as_completed(futuros):
            try:
                procesados.append(fut.result())
            except Exception as e:
                ev = futuros[fut]
                ev["_error"] = f"{type(e).__name__}: {str(e)[:90]}"
                procesados.append(ev)

    procesados_por_link = {}
    for ev in procesados:
        url = (ev.get("link") or ev.get("url") or "").strip().lower()
        if url:
            procesados_por_link[url] = ev

    # ---- Paso 4: aplicar cambios ----
    actualizados = 0
    sigue_incompleto = []
    for ev in eventos:
        url = (ev.get("link") or ev.get("url") or "").strip().lower()
        if url and url in procesados_por_link:
            nuevo = procesados_por_link[url]
            if nuevo.get("_completado"):
                actualizados += 1
            for k in list(ev.keys()):
                if k in nuevo and not _es_na(nuevo[k]) and _es_na(ev[k]):
                    ev[k] = nuevo[k]
        if _es_na(ev.get("fecha")) or _es_na(ev.get("lugar")):
            sigue_incompleto.append({
                "nombre": ev.get("nombre", "N/A")[:60],
                "link": ev.get("link") or ev.get("url") or "N/A",
                "faltan": [c for c in ("fecha", "lugar") if _es_na(ev.get(c))]
            })

    _guardar_csv(csv_path, eventos)

    resumen = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "csv": csv_path,
        "total_filas": len(eventos),
        "total_candidatos": len(candidatos),
        "procesados": len(procesados),
        "actualizados": actualizados,
        "eliminados_basura": eliminados,
        "siguientes_incompletos": sigue_incompleto,
    }
    _guardar_resumen(resumen)

    print("\n" + "=" * 60)
    print(f"📊 Resultado: {actualizados} eventos completados de "
          f"{len(candidatos)} candidatos | {eliminados} basura eliminados")
    print(f"⚠️ Siguen incompletos: {len(sigue_incompleto)}")
    for s in sigue_incompleto[:8]:
        print(f"   - {s['nombre']} | faltan: {', '.join(s['faltan'])}")
    print("=" * 60)
    return resumen


# -------------------------------------------------------------------- #
# CLI
# -------------------------------------------------------------------- #

if __name__ == "__main__":
    _limit = None
    if "--limit" in sys.argv:
        i = sys.argv.index("--limit")
        try:
            _limit = int(sys.argv[i + 1])
        except (IndexError, ValueError):
            pass
    res = completar_fb_externo(limit=_limit)
    print(f"\nHecho. Eventos actualizados externamente: {res['actualizados']}")
