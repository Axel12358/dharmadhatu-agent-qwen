#!/usr/bin/env python3
"""
Busca eventos públicos de Facebook SIN LOGIN usando Google vía Playwright.

Facebook obliga a iniciar sesión para ver eventos, así que se explota lo que
Google indexa en el SERP: título, fecha, ciudad, venue y descripción de los
eventos de Facebook aparecen en los resultados de búsqueda sin necesidad de
visitar Facebook.

Estrategia:
1. Genera consultas combinando subgéneros × países × ciudades × año (2026)
   desde config_grupos.json, con el operador site:facebook.com/events.
2. Busca en Google con Playwright (stealth, sin cookies, sin login).
3. Extrae eventos de los bloques de resultados del SERP (título, fecha, ciudad,
   lugar, snippet).
4. Opcionalmente visita la URL del evento (timeout 10s, máx 3 intentos):
   si la página renderiza contenido público, usa ese contenido; si no,
   se queda con los datos del SERP.

Devuelve eventos en el formato estándar del proyecto.
"""

import asyncio
import json
import random
import re
import sys
import time
import urllib.parse
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Optional

import socket
socket.setdefaulttimeout(10)

try:
    from bs4 import BeautifulSoup
except ImportError:
    BeautifulSoup = None

_PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

CONFIG_FILE = str(Path(_PROJECT_ROOT) / "config_grupos.json")
OUTPUT_FILE = str(Path(_PROJECT_ROOT) / "eventos_facebook_publicos.json")

from scrapers.event_extractor import EventExtractor
from scrapers.anti_block import get_anti_block
from scrapers.facebook_mcp import SUBGENERO_ALIASES

TIMEOUT_POR_EVENTO = 10
MAX_INTENTOS = 3
MAX_CONSULTAS = 20
MAX_EVENTOS = 40
AÑO = "2027"
_anti_block = get_anti_block()
_extractor = EventExtractor()

PATRON_FECHA = re.compile(
    r"\b(\d{1,2})[./\-\s]+\s*(?:de\s+)?(enero|febrero|marzo|abril|mayo|junio|"
    r"julio|agosto|septiembre|octubre|noviembre|diciembre|january|february|"
    r"march|april|may|june|july|august|september|october|november|december|"
    r"jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec)\w*[\s.,]*\s*(\d{4})?\b",
    re.IGNORECASE,
)
PATRON_CIUDAD_PAIS = re.compile(
    r"(?:event|party|festival)\s+in\s+([^,]+?),\s*([A-Z][a-zà-ÿ]+)(?:\s*and\s*\d+\s*others)?",
    re.IGNORECASE,
)


def _cargar_config():
    if not Path(CONFIG_FILE).exists():
        return {"subgeneros": ["psytrance"], "paises": []}
    try:
        with open(CONFIG_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return {"subgeneros": ["psytrance"], "paises": []}


def _generar_consultas(config, año=AÑO):
    subgeneros = config.get("subgeneros", ["psytrance"])
    paises = config.get("paises", [])
    consultas = []
    generales = []
    por_pais = []
    por_ciudad = []

    for sub in subgeneros:
        aliases = SUBGENERO_ALIASES.get(sub, [sub])
        principal = aliases[0]
        generales.append(f'site:facebook.com/events "{principal}"')
        for p in paises:
            nombre = p.get("nombre", "")
            por_pais.append(f'site:facebook.com/events "{principal}" "{nombre}"')
            for ciudad in p.get("ciudades", [])[:2]:
                por_ciudad.append(f'site:facebook.com/events "{principal}" "{ciudad}"')

    # Más probables primero: subgénero solo, luego país, luego ciudad
    random.shuffle(generales)
    random.shuffle(por_pais)
    random.shuffle(por_ciudad)
    consultas = generales + por_pais + por_ciudad
    vistos = set()
    unicas = []
    for c in consultas:
        if c not in vistos:
            vistos.add(c)
            unicas.append(c)
    return unicas[:MAX_CONSULTAS]


async def _buscar_google(consulta, context):
    """Busca en Google y devuelve bloques (titulo, url, texto) de resultados."""
    bloques = []
    page = None
    try:
        page = await context.new_page()
        await _anti_block.apply_playwright_stealth(page)
        url = "https://www.google.com/search?q=" + urllib.parse.quote(consulta) + "&num=20&hl=es"
        await page.goto(url, timeout=15000, wait_until="domcontentloaded")
        await asyncio.sleep(random.uniform(1.5, 2.5))
        html = await page.content()

        # Google nos pidió captcha -> sin resultados y señal de bloqueo
        if "sorry/index" in page.url:
            return ("BLOQUEADO", bloques)

        if BeautifulSoup is None:
            return ("OK", bloques)

        soup = BeautifulSoup(html, "html.parser")
        for res in soup.select("div.tF2Cxc, div.g"):
            a = res.find("a", href=True)
            h3 = res.find("h3")
            if not a or not h3:
                continue
            href = a.get("href", "")
            if "/url?q=" in href:
                href = urllib.parse.parse_qs(
                    urllib.parse.urlparse(href).query
                ).get("q", [""])[0]
            href = urllib.parse.unquote(href)
            if not re.search(r"facebook\.com/events", href):
                continue
            texto = res.get_text(" ", strip=True)
            bloques.append({
                "titulo": h3.get_text(strip=True),
                "url": href.split("?")[0].rstrip("/"),
                "texto": texto,
            })
    except Exception:
        pass
    finally:
        if page:
            try:
                await page.close()
            except Exception:
                pass
    return ("OK", bloques)


async def _buscar_startpage(consulta, context):
    """Busca en Startpage (proxy anónimo de Google). No requiere login."""
    bloques = []
    page = None
    try:
        page = await context.new_page()
        await _anti_block.apply_playwright_stealth(page)
        url = "https://www.startpage.com/sp/search?query=" + urllib.parse.quote(consulta)
        await page.goto(url, timeout=25000, wait_until="domcontentloaded")
        # Startpage redirige a localhost cuando nos bloquea
        if page.url.startswith("http://localhost") or page.url.startswith("http://127.0.0.1"):
            return ("BLOQUEADO", bloques)
        await asyncio.sleep(random.uniform(1.8, 2.6))
        html = await page.content()

        if BeautifulSoup is None:
            return ("OK", bloques)

        soup = BeautifulSoup(html, "html.parser")
        for res in soup.select(".result, .w-gl, section"):
            a = res.find("a", href=True)
            if not a:
                continue
            href = urllib.parse.unquote(a.get("href", ""))
            href = href.split("?")[0].rstrip("/")
            if not re.search(r"facebook\.com/events", href):
                continue
            titulo = ""
            for h in ("h2", "h3", "h4"):
                htag = res.find(h)
                if htag:
                    titulo = htag.get_text(strip=True)
                    break
            texto = res.get_text(" ", strip=True)
            bloques.append({"titulo": titulo, "url": href, "texto": texto})
    except Exception:
        pass
    finally:
        if page:
            try:
                await page.close()
            except Exception:
                pass
    return ("OK", bloques)


async def _buscar_yahoo(consulta, context):
    """Busca en Yahoo (Playwright). Devuelve bloques (titulo, url, texto)."""
    bloques = []
    page = None
    try:
        page = await context.new_page()
        await _anti_block.apply_playwright_stealth(page)
        url = "https://search.yahoo.com/search?p=" + urllib.parse.quote(consulta)
        await page.goto(url, timeout=20000, wait_until="domcontentloaded")
        await asyncio.sleep(random.uniform(1.5, 2.5))
        html = await page.content()

        if BeautifulSoup is None:
            return ("OK", bloques)

        soup = BeautifulSoup(html, "html.parser")
        for res in soup.select("li.algo-sr, div.algo-sr, li.algo, li.dd.algo"):
            a = res.find("a", href=True)
            if not a:
                continue
            href = urllib.parse.unquote(a.get("href", ""))
            if not re.search(r"facebook\.com/events", href):
                continue
            h = res.find(["h3", "h2", "h4"])
            titulo = h.get_text(strip=True) if h else ""
            if not titulo:
                titulo = a.get_text(" ", strip=True)
            texto = res.get_text(" ", strip=True)
            bloques.append({
                "titulo": titulo,
                "url": href.split("?")[0].rstrip("/"),
                "texto": texto,
            })
    except Exception:
        pass
    finally:
        if page:
            try:
                await page.close()
            except Exception:
                pass
    return ("OK", bloques)


async def _buscar_brave(consulta, context):
    """Busca en Brave Search (SPA renderizada vía Playwright)."""
    bloques = []
    page = None
    try:
        page = await context.new_page()
        await _anti_block.apply_playwright_stealth(page)
        url = "https://search.brave.com/search?q=" + urllib.parse.quote(consulta)
        await page.goto(url, timeout=20000, wait_until="domcontentloaded")
        await asyncio.sleep(random.uniform(2.5, 3.5))
        data = await page.evaluate(
            """() => {
                const results = [];
                const els = document.querySelectorAll('a[href*="facebook.com/events"]');
                for (const a of els) {
                    let snip = a.closest('[class*=snippet], [class*=result], section, li, article');
                    const h = snip ? snip.querySelector('h3, [class*=title]') : null;
                    results.push({
                        href: a.href,
                        title: h ? h.innerText.trim() : a.innerText.trim(),
                        text: snip ? snip.innerText.slice(0, 300) : ''
                    });
                }
                return results;
            }"""
        )
        for d in data:
            href = urllib.parse.unquote(d.get("href", ""))
            if not re.search(r"facebook\.com/events", href):
                continue
            titulo = (d.get("title") or "").strip()
            texto = d.get("text") or ""
            bloques.append({
                "titulo": titulo,
                "url": href.split("?")[0].rstrip("/"),
                "texto": texto,
            })
    except Exception:
        pass
    finally:
        if page:
            try:
                await page.close()
            except Exception:
                pass
    return ("OK", bloques)


async def _buscar(consulta, context, usar_startpage=False):
    """Google primero; si pide captcha o no encuentra nada, cae a Startpage."""
    if usar_startpage:
        return await _buscar_startpage(consulta, context)
    estado, bloques = await _buscar_google(consulta, context)
    if estado == "BLOQUEADO" or not bloques:
        estado, bloques = await _buscar_startpage(consulta, context)
    return estado, bloques


def _normalizar_fecha(fecha_str):
    """Convierte '10. April 2026' / '10 April 2026' a ISO, o None."""
    if not fecha_str:
        return None
    m = PATRON_FECHA.search(fecha_str)
    if not m:
        return None
    dia, mes_nombre, anio = m.group(1), m.group(2), m.group(3)
    meses_es = {
        "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5,
        "junio": 6, "julio": 7, "agosto": 8, "septiembre": 9,
        "octubre": 10, "noviembre": 11, "diciembre": 12,
    }
    meses_en = {
        "january": 1, "february": 2, "march": 3, "april": 4, "may": 5,
        "june": 6, "july": 7, "august": 8, "september": 9, "october": 10,
        "november": 11, "december": 12,
    }
    mese = meses_es.get(mes_nombre.lower()) or meses_en.get(mes_nombre.lower())
    if not mese:
        return None
    anio_int = int(anio) if anio else datetime.now().year
    try:
        return f"{anio_int:04d}-{mese:02d}-{int(dia):02d}"
    except ValueError:
        return None


def _extraer_del_serp(bloque):
    """Extrae evento desde el bloque del SERP de Google (sin visitar FB)."""
    titulo = (bloque.get("titulo") or "").strip()
    texto = bloque.get("texto") or ""
    url = bloque.get("url") or ""

    if not titulo or not re.search(r"facebook\.com/events", url):
        return None

    # Título como nombre (a veces incluye '| Venue, Ciudad')
    partes_titulo = [p.strip() for p in titulo.split(" | ") if p.strip()]
    nombre = partes_titulo[0].split(" · Facebook")[0].strip()
    nombre = re.sub(r"\s*[-–]?\s*Facebook$", "", nombre, flags=re.IGNORECASE)
    nombre = re.sub(r"\s+", " ", nombre)[:100]

    fecha = _normalizar_fecha(texto) or _normalizar_fecha(titulo) or "N/A"

    lugar = "N/A"
    ciudad = "N/A"
    pais = "N/A"
    organizador = "N/A"
    descripcion = "N/A"

    # Patrón '... | Venue, Ciudad' típico del título de eventos FB:
    # la última parte tras '|' suele ser 'Venue, Ciudad'
    if len(partes_titulo) >= 2:
        ultima = partes_titulo[-1]
        mpartes = [x.strip() for x in ultima.split(",")]
        candidato_lugar = mpartes[0].strip()
        if re.search(r"[A-Za-zÀ-ÿ]{3,}", candidato_lugar):
            lugar = candidato_lugar[:80]
        if len(mpartes) >= 2:
            candidato_ciudad = mpartes[1].strip()
            if re.search(r"[A-Za-zÀ-ÿ]{3,}", candidato_ciudad):
                ciudad = candidato_ciudad[:60]

    # 'Event in Berlin, Germany by X'
    m = PATRON_CIUDAD_PAIS.search(texto)
    if m:
        ciudad_c = m.group(1).strip()
        if re.search(r"[A-Za-zÀ-ÿ]{3,}", ciudad_c):
            ciudad = ciudad_c[:60]
        pais = m.group(2).strip()[:40]

    morg = re.search(
        r"\bby\s+([A-Z][A-Za-zÀ-ÿ0-9&\s.'-]*?)(?:\s+and\s+\d+\s+others|\s+[A-Za-zÀ-ÿ]+?\s+on\s+facebook|\.\s|$)",
        texto,
        re.IGNORECASE,
    )
    if morg:
        cand = morg.group(1).strip().rstrip(".")
        if len(cand) >= 3 and "facebook" not in cand.lower():
            organizador = cand[:60]

    tipo_lugar = _extractor._extract_venue_type(f"{nombre} {lugar} {descripcion}") or "N/A"

    return {
        "nombre": nombre,
        "fecha": fecha,
        "lugar": lugar,
        "ciudad": ciudad,
        "pais": pais,
        "tipo_lugar": tipo_lugar,
        "fuente": "Facebook (público)",
        "organizador": organizador,
        "email": "N/A",
        "url": url,
        "descripcion": descripcion,
    }


TERMINOS_MUSICA = re.compile(
    r"\b(psytrance|psych|dark.?psy|forest|goa|hi.?tech|progressive|trance|"
    r"techno|tech.?house|house|drum.?n.?bass|dnb|dubstep|minimal|acid|"
    r"ambient|psychedelic|festival|rave|party|dj|live|club|full.?on|"
    r"suomi|hitech|zenon|night)\b",
    re.IGNORECASE,
)


def _es_relevante(bloque):
    """Solo eventos de música/fiesta (evita convenciones, política, etc.)."""
    texto = " ".join(
        [
            bloque.get("titulo", ""),
            bloque.get("texto", ""),
            urllib.parse.unquote(bloque.get("url", "")),
        ]
    )
    if not TERMINOS_MUSICA.search(texto):
        return False
    bajo = texto.lower()
    irrelevantes = [
        "walking dead", "malvinas", "convención", "convencion", "nacionalista",
        "milonga", "asamblea", "conferencia", "platos", "gastronomía",
        "gastronomia", "protesta", "marcha", "feria del libro",
        "6 hour", "race", "carrera", "concierto", "concert", "metal band",
        "rock band",
    ]
    if any(t in bajo for t in irrelevantes):
        return False
    if "iniciar sesión" in bajo or "log in" in bajo:
        return False
    return True


def _es_pagina_login(texto, url=""):
    if "login.php" in url:
        return True
    bajo = texto.lower()
    return ("iniciar sesión" in bajo or "log in" in bajo) and len(bajo) < 2000


async def _enriquecer_con_visita(evento, context, intentos=MAX_INTENTOS):
    """Intenta visitar la URL para mejorar el evento (solo si carga en público)."""
    for intento in range(intentos):
        page = None
        t0 = time.time()
        try:
            page = await context.new_page()
            await _anti_block.apply_playwright_stealth(page)

            async def _route(route):
                if route.request.resource_type in ("image", "media", "font"):
                    await route.abort()
                else:
                    await route.continue_()
            await page.route("**/*", _route)

            await page.goto(
                evento["url"], timeout=TIMEOUT_POR_EVENTO * 1000,
                wait_until="domcontentloaded",
            )
            await asyncio.sleep(random.uniform(1.0, 1.5))
            texto = await page.evaluate(
                "() => document.body ? document.body.innerText : ''"
            )
            if _es_pagina_login(texto, page.url):
                return evento

            extra = _extractor.extract_all(texto[:12000], evento["url"], evento["url"])
            if extra:
                ev = extra[0]
                if ev.get("nombre") and ev["nombre"] not in ("N/A", "Sin nombre"):
                    evento["nombre"] = ev["nombre"]
                if ev.get("fecha") and ev["fecha"] != "N/A":
                    evento["fecha"] = ev["fecha"]
                if ev.get("lugar") and ev["lugar"] != "N/A":
                    evento["lugar"] = ev["lugar"]
                if ev.get("ciudad") and ev["ciudad"] != "N/A":
                    evento["ciudad"] = ev["ciudad"]
                if ev.get("pais") and ev["pais"] != "N/A":
                    evento["pais"] = ev["pais"]
                if ev.get("tipo_lugar") and ev["tipo_lugar"] != "N/A":
                    evento["tipo_lugar"] = ev["tipo_lugar"]
                if ev.get("organizador") and ev["organizador"] != "N/A":
                    evento["organizador"] = ev["organizador"]
                if ev.get("descripcion") and ev["descripcion"] != "N/A":
                    evento["descripcion"] = ev["descripcion"]
            return evento
        except Exception:
            if time.time() - t0 >= TIMEOUT_POR_EVENTO:
                break
        finally:
            if page:
                try:
                    await page.close()
                except Exception:
                    pass
    return evento


async def run_public_events() -> List[Dict]:
    config = _cargar_config()
    consultas = _generar_consultas(config)
    print(f"🔍 Buscando eventos públicos de Facebook ({len(consultas)} consultas Google)...")

    try:
        from playwright.async_api import async_playwright
    except ImportError:
        print("❌ playwright no instalado.")
        return []

    eventos = []
    urls_vistas = set()

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage",
                       "--disable-blink-features=AutomationControlled"],
            )
            context = await _anti_block.create_stealth_context(browser, use_tor=True)
            usar_startpage = False

            for i, consulta in enumerate(consultas, 1):
                if len(eventos) >= MAX_EVENTOS:
                    break
                estado, bloques = await _buscar(consulta, context, usar_startpage)
                if estado == "BLOQUEADO":
                    print("  ⛔ Google pide captcha, cambio a Startpage.", flush=True)
                    usar_startpage = True
                    continue
                nuevos = 0
                for b in bloques:
                    if b["url"] in urls_vistas:
                        continue
                    if not _es_relevante(b):
                        continue
                    urls_vistas.add(b["url"])
                    ev = _extraer_del_serp(b)
                    if ev:
                        if ev.get("fecha") == "N/A" or ev.get("ciudad") == "N/A":
                            ev = await _enriquecer_con_visita(ev, context)
                        if ev.get("nombre", "").strip().lower() in (
                            "", "iniciar sesión", "log in", "iniciar sesión en facebook"
                        ):
                            continue
                        if str(ev.get("organizador", "")).startswith("http"):
                            ev["organizador"] = "N/A"
                        eventos.append(ev)
                        nuevos += 1
                        print(
                            f"  ✅ [{len(eventos)}] {ev['nombre'][:45]} | "
                            f"{ev['fecha']} | {ev['ciudad']}", flush=True
                        )
                print(f"  [{i}/{len(consultas)}] Google: {nuevos} eventos nuevos", flush=True)
                if i < len(consultas):
                    await asyncio.sleep(random.uniform(2.0, 4.0))

            await browser.close()
    except Exception as e:
        print(f"  ⚠️ Error global: {e}")

    # Deduplicar por nombre+fecha
    vistos = set()
    unicos = []
    for ev in eventos:
        k = (ev.get("nombre", ""), ev.get("fecha", ""))
        if k not in vistos:
            vistos.add(k)
            unicos.append(ev)

    unicos = unicos[:MAX_EVENTOS]
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(unicos, f, indent=2, ensure_ascii=False)
    print(f"✅ {len(unicos)} eventos públicos de Facebook guardados en {OUTPUT_FILE}")
    return unicos


if __name__ == "__main__":
    eventos = asyncio.run(run_public_events())
    print(f"\nTotal eventos públicos Facebook: {len(eventos)}")
    for ev in eventos[:10]:
        print(f"  - {ev.get('nombre', '?')[:55]} | {ev.get('fecha', '?')} | {ev.get('lugar', '?')}")
