#!/usr/bin/env python3
"""
Completar campos "N/A" de eventos de Facebook en el CSV consolidado.

Problema: en `eventos_encontrados.csv` hay eventos con fuente Facebook que tienen
"fecha", "lugar" u "organizador" = N/A (o "Fecha no disponible", vacío). Tenemos
el link del evento; este módulo visita cada URL pública con Playwright (sin
cookies) y completa los campos que faltan.

Filosofía: **suma, nunca resta**. Solo actualiza los campos N/A; si no se puede
obtener un valor, se deja como estaba (N/A). No modifica ningún otro módulo.

Uso standalone:
    python3 scrapers/completar_fb_na.py            # proceso completo
    python3 scrapers/completar_fb_na.py --limit 5  # solo los 5 primeros

Uso como función:
    from scrapers.completar_fb_na import completar_eventos_fb
    resumen = completar_eventos_fb()

Salidas:
    - `eventos_encontrados.csv` actualizado (mismo orden de columnas).
    - `eventos_completados.json` con resumen (actualizados / siguen incompletos).
"""

import csv
import json
import random
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from bs4 import BeautifulSoup

from playwright.sync_api import sync_playwright
from playwright_stealth import Stealth

# --------------------------------------------------------------------- #
# Constantes
# --------------------------------------------------------------------- #

PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
DEFAULT_CSV = str(Path(PROJECT_ROOT) / "eventos_encontrados.csv")
RESUMEN_FILE = str(Path(PROJECT_ROOT) / "eventos_completados.json")

# Máximo de eventos simultáneos (Playwright con navegador propio por hilo)
MAX_WORKERS = 5
# Máximo de intentos por URL (reintentos incluidos)
MAX_INTENTOS = 2
# Timeout por página (ms)
PAGE_TIMEOUT_MS = 20000
# Pausa humana entre acciones dentro de una página
SLEEP_MIN, SLEEP_MAX = 1.2, 2.5

# Campos del CSV (orden de columnas preservado al escribir)
CSV_KEYS = ["nombre", "fecha", "lugar", "pais", "continente", "subcontinente",
            "fuente", "organizador", "email", "link", "subgenero"]

# Valores que se consideran "vacío" / no disponible
NA_VALORES = {"", "N/A", "n/a", "NA", "na", "None", "none", "-", "?",
              "desconocido", "desconocida", "no disponible", "fecha no disponible",
              "unknown", "not available", "tba", "TBD"}

# User-Agent móvil realista (FB detecta menos el scraping de páginas móviles)
UA_MOVIL = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_2 like Mac OS X) "
            "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 "
            "Mobile/15E148 Safari/604.1")

# Selectores típicos de la página pública de evento de FB (m.v. y desktop).
# Se intentan en orden; si el selector no existe, se pasa al siguiente.
SELECTORES = {
    "nombre": [
        'h1[data-testid="event-title"]',
        'div[data-testid="event-title"]',
        'meta[property="og:title"]',
    ],
    "fecha": [
        'span[data-testid="event-info"]',
        'div[data-testid="event-info"]',
        "span.x193iq5w.xeuugli.x13faqbe.x1vvkbs.x1xmvt09.x1lliihq.x1s928wv.xhkezso.x1gmr53x.x1cpjm7i.x1fgarty.x1943h6x.x4zkp8e.x41vudc.x1fey0fg.x3x7a5m",
    ],
    "lugar": [
        'span[data-testid="event-place"]',
        'div[data-testid="event-place"]',
        'a[data-testid="event-place-link"]',
    ],
    "organizador": [
        'a[data-testid="event-host"]',
        'span[data-testid="event-host"]',
        'div[data-testid="event-host"]',
    ],
}

# Indicadores de bloqueo FUERTE (solo se considera bloqueada si la página está
# detrás de un muro real: checkpoint/captcha/detección de tráfico). CTAs tipo
# "Inicia sesión para ver el contenido más" aparecen al fondo de páginas de
# eventos públicas y NO son un muro → no deben marcar la página como bloqueada.
BLOQUEO_MARCADORES = [
    "checkpoint", "login.php", "nuestro sistema ha detectado", "enter the code",
    "confirm you", "our systems have detected unusual traffic", "captcha",
    "you must log in", "log in to continue", "your request has been blocked",
    "access denied", "demasiadas solicitudes", "too many requests",
    "unsupported browser", "navegador no compatible",
]


def _es_na(valor: Optional[str]) -> bool:
    """Devuelve True si el valor se considera no disponible."""
    if valor is None:
        return True
    return str(valor).strip().lower() in NA_VALORES


def _limpiar_texto(texto: Optional[str]) -> str:
    if not texto:
        return ""
    return re.sub(r"\s+", " ", texto).strip()


def _es_fecha_real(texto: str) -> bool:
    """Heurística: el texto parece una fecha/hora y no es una etiqueta suelta."""
    t = texto.lower()
    if any(b in t for b in ("publicado", "editado", "sugerir", "compartido",
                            "duración", "público", "solo invitados", "privado",
                            "public", "duration", "invite", "going", "interested",
                            "interesado", "asistiendo", "invitados")):
        return False
    # Debe contener un mes (ES/EN) o un formato numérico de fecha.
    # Un año suelto ("CAMAKAVUM FESTIVAL 2026") NO cuenta como fecha.
    meses = ("enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
             "agosto", "septiembre", "sept", "octubre", "noviembre", "diciembre",
             "january", "february", "march", "april", "june", "july", "august",
             "september", "october", "november", "december", "jan", "feb",
             "mar", "apr", "jun", "jul", "aug", "sep", "oct", "nov", "dec",
             "ene")
    if any(m in t for m in meses):
        return True
    if re.search(r"\b\d{1,2}[-/\.]\d{1,2}[-/\.]\d{2,4}\b", t):
        return True
    if re.search(r"\b\d{4}[-/\.]\d{1,2}[-/\.]\d{1,2}\b", t):
        return True
    if re.search(r"\d{1,2}\s+de\s+\w+", t):
        return True
    return False


def _extraer_lugar_del_texto(texto: str) -> Optional[str]:
    """Extrae un lugar plausible (Ciudad, País | Lugar, Ciudad | dirección)."""
    patrones = [
        # "Lugar, Ciudad" o "Ciudad, País"
        r"\b[A-ZÀ-ÿ][A-Za-zÀ-ÿ'’\-\. ]{2,45},\s*[A-ZÀ-ÿ][A-Za-zÀ-ÿ'’\-\. ]{2,45}\b",
        # Dirección: "Calle 8, 19386" (calle con número + código postal)
        r"\b[A-ZÀ-ÿ][A-Za-zÀ-ÿ'’\- ]{2,40}\s+\d{1,4}[a-z]?,\s*\d{4,5}\b",
    ]
    for linea in texto.split("\n"):
        linea = linea.strip()
        if len(linea) < 3 or len(linea) > 120:
            continue
        for pat in patrones:
            m = re.search(pat, linea)
            if not m:
                continue
            cand = _limpiar_texto(m.group(0))
            cand_l = cand.lower()
            if any(b in cand_l for b in ("facebook", "login", "checkpoint",
                                         "crear evento", "sign up", "ver más",
                                         "see more", "publicado", "compartido")):
                continue
            if len(cand) >= 4:
                return cand[:90]
    return None


def _extraer_organizador_del_texto(texto: str) -> Optional[str]:
    """Extrae organizador con patrones ES/EN del texto público (línea a línea)."""
    patrones = [
        r"(?:organized by|hosted by|presented by|promoted by|organizado por|"
        r"presentado por|producido por|evento de|event by|a cargo de|por)[:\s]+"
        r"([A-Z][A-Za-zÀ-ÿ0-9&.' \-]{2,60})",
    ]
    for linea in texto.split("\n"):
        linea = linea.strip()
        if len(linea) < 3 or len(linea) > 160:
            continue
        for pat in patrones:
            m = re.search(pat, linea, re.IGNORECASE)
            if not m:
                continue
            cand = _limpiar_texto(m.group(1)).strip(".,;")
            cand_l = cand.lower()
            if len(cand) < 3 or any(b in cand_l for b in
                                    ("facebook", "instagram", "http", "twitter",
                                     "evento", "noche", "público", "public",
                                     "detalles", "duración", "duration")):
                continue
            if re.search(r"\b\d[\d.,]*\s*(?:guests?|people|personas|asistentes?"
                         r"|attendees?|going|interested|friends)\b", cand_l):
                continue
            return cand[:60]
    return None


_MESES = {"enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6,
          "julio": 7, "agosto": 8, "septiembre": 9, "octubre": 10,
          "noviembre": 11, "diciembre": 12,
          "january": 1, "february": 2, "march": 3, "april": 4, "may": 5,
          "june": 6, "july": 7, "august": 8, "september": 9, "october": 10,
          "november": 11, "december": 12,
          "jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6, "jul": 7,
          "aug": 8, "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12,
          "ene": 1}


def _normalizar_fecha(texto: str) -> Optional[str]:
    """Devuelve una fecha ISO (YYYY-MM-DD) o None si no es reconocible.

    Se aplica sobre candidatos CORTOS (una línea de fecha, no el body entero).
    Soporta:
    - ISO/NUMÉRICO: 2026-08-15, 15/08/2026, 15.08.2026
    - ES: "sábado, 16 de agosto de 2026", "16 agosto 2026"
    - EN: "Friday, August 15 at 10:00 PM", "Aug 15, 2026"
    - Día+mes sin año: se asume el año actual (o el siguiente si ya pasó).
    """
    import calendar
    t = texto.strip()
    if not t or len(t) > 160:
        return None

    # 1) Fechas numéricas (año primero o día/mes/año)
    fechas = re.findall(r"\b(\d{4})[-/\.](\d{1,2})[-/\.](\d{1,2})(?!\d)", t)
    if fechas:
        a, m, d = fechas[0]
        try:
            return f"{a}-{int(m):02d}-{int(d):02d}"
        except ValueError:
            pass
    fechas = re.findall(r"\b(\d{1,2})[-/\.](\d{1,2})[-/\.](\d{2,4})(?!\d)", t)
    if fechas:
        d, m, a = fechas[0]
        a = f"20{a}" if len(a) == 2 else a
        try:
            return f"{a}-{int(m):02d}-{int(d):02d}"
        except ValueError:
            pass

    # 2) Mes + día (+ año opcional), día antes o después del mes.
    #    Los nombres largos van ANTES que los abreviados para que la
    #    alternancia capture "July" entero y no solo "Jul".
    pat_mes = (r"(septiembre|september|enero|febrero|february|"
               r"noviembre|november|diciembre|december|octubre|october|"
               r"january|agosto|august|abril|april|marzo|march|"
               r"junio|june|julio|july|feb|jan|mar|apr|may|jun|jul|aug|sep|"
               r"sept|oct|nov|dec|ene)")
    for m in re.finditer(pat_mes, t, re.IGNORECASE):
        mes_idx = _MESES[m.group(1).lower()]
        # día antes del mes: "16 de agosto", "16 agosto", "15 Aug"
        prev = t[:m.start()]
        pd = re.search(r"(\d{1,2})\s*(?:de\s+)?$", prev)
        # día después del mes: "15, 2026", "15 at 10:00 PM", "15th, 2026"
        resto = t[m.end():]
        md = re.match(r"[\s,]*(?:el\s+|día\s+|day\s+)?(\d{1,2})"
                      r"(?:st|nd|rd|th)?(?:[\s,]+(?:de\s+)?(\d{4}))?", resto)
        dia, año = None, None
        if pd:
            # día ANTES del mes ("15 Aug 2026"): el texto tras el mes solo
            # puede aportar el año; si no, el día anterior es el válido.
            dia = int(pd.group(1))
            my = re.match(r"[\s,]*(?:de\s+)?(\d{4})", resto)
            if my:
                año = int(my.group(1))
        elif md:
            dia = int(md.group(1))
            if md.group(2):
                año = int(md.group(2))
        if dia is None:
            continue
        if año is None:
            hoy = datetime.now(timezone.utc)
            año = hoy.year
            if (mes_idx, dia) < (hoy.month, hoy.day):
                año += 1
        try:
            calendar.monthrange(año, mes_idx)  # valida día
            return f"{año}-{mes_idx:02d}-{dia:02d}"
        except ValueError:
            continue

    # 3) Solo año (4 dígitos) sin mes conocido — solo si el texto ES un año
    #    ("2026"), no si el año aparece dentro de un título o etiqueta.
    if re.fullmatch(r"\s*(?:año|year)?\s*20\d{2}\s*", t):
        m2 = re.search(r"(20\d{2})", t)
        if m2:
            return f"{m2.group(1)}-01-01"
    return None


# --------------------------------------------------------------------- #
# Navegación con Playwright (sync) + stealth
# --------------------------------------------------------------------- #

def _es_pagina_bloqueada(texto: str, url: str) -> bool:
    t = texto.lower()
    u = url.lower()
    if any(b in u for b in ("login", "checkpoint", "unsupportedbrowser")):
        return True
    return any(b in t for b in BLOQUEO_MARCADORES)


def _extraer_desde_selectores(page, campo: str, html: str) -> Optional[str]:
    """Intenta selectores típicos; si no, meta og / JSON-LD / cuerpo."""
    for sel in SELECTORES.get(campo, []):
        if sel.startswith("meta"):
            match = re.search(
                r'<meta\s+property="og:title"\s+content="([^"]+)"', html)
            if match:
                cand = match.group(1).strip()
                cl = cand.lower()
                if any(b in cl for b in ("navegador", "browser", "no es compatible",
                                         "not supported", "unsupported",
                                         "facebook", "log in", "inicia sesión")):
                    continue
                return cand[:120]
            continue
        try:
            el = page.query_selector(sel)
            if el:
                txt = _limpiar_texto(el.inner_text())
                if txt:
                    return txt[:160]
        except Exception:
            continue
    return None


def _parsear_pagina(page, url: str) -> Dict[str, str]:
    """Extrae los datos de una página pública de evento FB ya cargada."""
    resultado = {"nombre": None, "fecha": None, "lugar": None,
                 "organizador": None, "descripcion": None, "bloqueado": False}
    try:
        html = page.content()
    except Exception:
        html = ""
    try:
        cuerpo = page.evaluate("document.body ? document.body.innerText : ''")
    except Exception:
        cuerpo = ""

    if not html and not cuerpo:
        return resultado

    if _es_pagina_bloqueada(f"{cuerpo} {html[:2000]}", url):
        resultado["bloqueado"] = True
        return resultado

    # Líneas del cuerpo (sin vacías) para parseo línea a línea
    lineas = [l.strip() for l in cuerpo.split("\n") if l.strip()]
    # Texto crudo unido (descripción)
    if cuerpo:
        resultado["descripcion"] = _limpiar_texto(cuerpo[:12000])[:2000]

    # ---- Nombre ----
    # 1) h1[data-testid=event-title] / meta og:title (desktop)
    nombre = _extraer_desde_selectores(page, "nombre", html)
    # 2) h1 real: saltar el aviso "Este navegador no es compatible" (móvil)
    if not nombre:
        try:
            for h1 in page.query_selector_all("h1"):
                t = _limpiar_texto(h1.inner_text())
                if not t:
                    continue
                tl = t.lower()
                if any(b in tl for b in ("navegador", "browser", "no es compatible",
                                         "not supported", "unsupported")):
                    continue
                nombre = t[:120]
                break
        except Exception:
            pass
    # 3) primera línea larga con mayúscula inicial que parezca título
    if not nombre and lineas:
        for l in lineas:
            if len(l) >= 8 and l[0].isupper() and "facebook.com" not in l and \
               "·" not in l and "|" not in l and not _es_fecha_real(l):
                nombre = l[:120]
                break
    resultado["nombre"] = nombre

    # ---- Fecha: candidato corto línea a línea ----
    fecha = _extraer_desde_selectores(page, "fecha", html)
    if not fecha:
        # buscar primera línea que sea claramente una fecha
        for l in lineas:
            if len(l) > 3 and len(l) < 120 and _es_fecha_real(l):
                cand = _normalizar_fecha(l)
                if cand:
                    fecha = cand
                    break
    if not fecha:
        # atributo datetime en el HTML (ISO)
        mdt = re.search(r'datetime="(\d{4}-\d{2}-\d{2})', html)
        if mdt:
            fecha = mdt.group(1)
    resultado["fecha"] = fecha

    # ---- Lugar ----
    lugar = _extraer_desde_selectores(page, "lugar", html)
    if not lugar:
        # línea "26.12.2025 | 23:00 | Spotlight Bar" → texto tras el 2º "|"
        for l in lineas:
            if "|" in l and re.search(r"\d{1,2}[\./]\d{1,2}", l):
                partes = [p.strip() for p in l.split("|") if p.strip()]
                if len(partes) >= 2:
                    lugar = partes[-1][:90]
                    break
    if not lugar:
        lugar = _extraer_lugar_del_texto(cuerpo[:6000])
    resultado["lugar"] = lugar

    # ---- Organizador ----
    org = _extraer_desde_selectores(page, "organizador", html)
    if not org:
        org = _extraer_organizador_del_texto(cuerpo[:10000])
    # Recortar conjunciones residuales ("HUN7A y" → "HUN7A")
    if org:
        org = re.sub(r"\s+(?:y|and|und|&)\s*$", "", org).strip()
    resultado["organizador"] = org

    return resultado


def _visitar_evento(url: str) -> Dict[str, str]:
    """Visita un evento FB con Playwright sync + stealth (sin cookies).

    Máximo MAX_INTENTOS intentos, PAGE_TIMEOUT_MS por página. Devuelve dict con
    los campos extraídos (None si no se pudo obtener / página bloqueada).
    """
    datos = {"url": url, "nombre": None, "fecha": None, "lugar": None,
             "organizador": None, "descripcion": None, "error": None,
             "bloqueado": False}
    for intento in range(1, MAX_INTENTOS + 1):
        try:
            with Stealth().use_sync(sync_playwright()) as p:
                browser = p.chromium.launch(
                    headless=True,
                    args=["--no-sandbox", "--disable-dev-shm-usage",
                          "--disable-blink-features=AutomationControlled"],
                )
                context = browser.new_context(
                    user_agent=UA_MOVIL,
                    viewport={"width": 390, "height": 844},
                    device_scale_factor=3,
                    is_mobile=True,
                    has_touch=True,
                    locale="es-ES",
                    timezone_id="Europe/Madrid",
                )
                page = context.new_page()
                page.set_default_timeout(PAGE_TIMEOUT_MS)
                page.set_default_navigation_timeout(PAGE_TIMEOUT_MS)
                try:
                    page.goto(url, timeout=PAGE_TIMEOUT_MS,
                              wait_until="domcontentloaded")
                    page.wait_for_timeout(random.randint(
                        int(SLEEP_MIN * 1000), int(SLEEP_MAX * 1000)))
                    extraido = _parsear_pagina(page, url)
                    datos.update(extraido)
                finally:
                    try:
                        context.close()
                    except Exception:
                        pass
                    try:
                        browser.close()
                    except Exception:
                        pass
                # Si la página cargó y no está bloqueada, salir del retry
                if not datos["bloqueado"]:
                    break
                print(f"     ⚠️ intento {intento}/{MAX_INTENTOS}: página bloqueada "
                      f"({url[:60]}…)")
        except Exception as e:
            datos["error"] = f"{type(e).__name__}: {str(e)[:90]}"
            print(f"     ⚠️ intento {intento}/{MAX_INTENTOS}: {datos['error']}")
    return datos


# --------------------------------------------------------------------- #
# CSV
# --------------------------------------------------------------------- #

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
            out["url"] = out["link"]
            writer.writerow(out)
    import os
    os.replace(tmp, csv_path)
    print(f"✅ CSV actualizado: {csv_path} ({len(eventos)} filas)")


def _guardar_resumen(resumen: Dict) -> None:
    tmp = RESUMEN_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(resumen, f, indent=2, ensure_ascii=False)
    import os
    os.replace(tmp, RESUMEN_FILE)
    print(f"📄 Resumen guardado: {RESUMEN_FILE}")


# --------------------------------------------------------------------- #
# Orquestación
# --------------------------------------------------------------------- #

def _procesar_evento(ev: Dict) -> Dict:
    """Procesa un evento: visita la URL y rellena los campos N/A."""
    resultado = dict(ev)
    resultado["_completado"] = False
    url = ev.get("link") or ev.get("url") or ""
    if not url or url == "N/A":
        resultado["_error"] = "sin URL"
        return resultado
    print(f"  🌐 Visitando {url[:70]}…", flush=True)
    datos = _visitar_evento(url)
    if datos["error"]:
        resultado["_error"] = datos["error"]

    # Solo completar campos que faltan; nunca pisar un valor existente
    cambios = {}
    for campo in ("fecha", "lugar", "organizador"):
        if _es_na(resultado.get(campo)) and datos.get(campo):
            cambios[campo] = datos[campo]
    if _es_na(resultado.get("nombre")) and datos.get("nombre"):
        cambios["nombre"] = datos["nombre"]
    if _es_na(resultado.get("email")) and datos.get("email"):
        cambios["email"] = datos["email"]

    if cambios:
        resultado.update(cambios)
        resultado["_completado"] = True
        print(f"     ✅ completado: {', '.join(cambios.keys())}")
    else:
        motivo = "bloqueada/login" if datos.get("bloqueado") else (
            "sin datos nuevos" if not datos["error"] else "error")
        print(f"     ⏭ sin cambios ({motivo})")
    return resultado


def completar_eventos_fb(csv_path: str = DEFAULT_CSV,
                         limit: Optional[int] = None,
                         max_workers: int = MAX_WORKERS) -> Dict:
    """Completa los campos N/A de eventos de Facebook visitando sus URLs.

    Args:
        csv_path: ruta del CSV consolidado (por defecto eventos_encontrados.csv).
        limit: opcional, procesa solo los primeros N eventos incompletos.
        max_workers: eventos simultáneos (por defecto 5).

    Returns:
        dict resumen: {"total_candidatos", "procesados", "actualizados",
                       "siguen_incompletos": [...]}
    """
    print("=" * 60)
    print("🔧 Completar N/A de Facebook (Playwright + stealth, sin cookies)")
    print("📅 " + datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M"))
    print("=" * 60)

    eventos = _cargar_csv(csv_path)
    if not eventos:
        return {"total_candidatos": 0, "procesados": 0, "actualizados": 0,
                "siguen_incompletos": []}

    # Filtrar candidatos: fuente Facebook y algún campo N/A
    candidatos = []
    for ev in eventos:
        fuente = str(ev.get("fuente", "")).lower()
        if "facebook" not in fuente:
            continue
        faltan = [c for c in ("fecha", "lugar", "organizador")
                  if _es_na(ev.get(c))]
        if faltan:
            ev["_faltan"] = faltan
            candidatos.append(ev)
    if limit:
        candidatos = candidatos[:limit]

    print(f"📥 {len(eventos)} filas | {len(candidatos)} candidatos "
          f"(fuente Facebook con N/A)")
    if not candidatos:
        print("🎉 No hay eventos de Facebook incompletos. Nada que hacer.")
        return {"total_candidatos": 0, "procesados": 0, "actualizados": 0,
                "siguen_incompletos": []}

    # Procesar en paralelo (max_workers navegadores a la vez)
    procesados = []
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futuros = {pool.submit(_procesar_evento, ev): ev for ev in candidatos}
        for fut in as_completed(futuros):
            try:
                procesados.append(fut.result())
            except Exception as e:
                ev = futuros[fut]
                ev["_error"] = f"{type(e).__name__}: {str(e)[:90]}"
                procesados.append(ev)

    # Aplicar cambios al listado global (mismo orden que el CSV original)
    procesados_por_link = {}
    for ev in procesados:
        url = (ev.get("link") or ev.get("url") or "").strip().lower()
        if url:
            procesados_por_link[url] = ev
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
            if any(_es_na(ev.get(c)) for c in ("fecha", "lugar", "organizador")):
                sigue_incompleto.append({
                    "nombre": ev.get("nombre", "N/A"),
                    "link": ev.get("link") or ev.get("url") or "N/A",
                    "faltan": [c for c in ("fecha", "lugar", "organizador")
                               if _es_na(ev.get(c))],
                })

    _guardar_csv(csv_path, eventos)

    resumen = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "csv": csv_path,
        "total_filas": len(eventos),
        "total_candidatos": len(candidatos),
        "procesados": len(procesados),
        "actualizados": actualizados,
        "siguen_incompletos": sigue_incompleto,
    }
    _guardar_resumen(resumen)

    print("\n" + "=" * 60)
    print(f"📊 Resultado: {actualizados} eventos completados de "
          f"{len(candidatos)} candidatos")
    print(f"⚠️ Siguen incompletos: {len(sigue_incompleto)}")
    for s in sigue_incompleto[:8]:
        print(f"   - {s['nombre'][:50]} | faltan: {', '.join(s['faltan'])}")
    print("=" * 60)
    return resumen


# --------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------- #

if __name__ == "__main__":
    _limit = None
    _workers = MAX_WORKERS
    if "--limit" in sys.argv:
        i = sys.argv.index("--limit")
        try:
            _limit = int(sys.argv[i + 1])
        except (IndexError, ValueError):
            pass
    if "--workers" in sys.argv:
        i = sys.argv.index("--workers")
        try:
            _workers = int(sys.argv[i + 1])
        except (IndexError, ValueError):
            pass
    res = completar_eventos_fb(limit=_limit, max_workers=_workers)
    print(f"\nDone. Actualizados: {res['actualizados']}")
