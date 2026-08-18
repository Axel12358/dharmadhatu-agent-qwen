#!/usr/bin/env python3
"""
Recupera eventos de Facebook que quedaron en eventos_visitados.json pero NO en
el CSV (se perdieron en ejecuciones anteriores antes del fix de re-visita).

Re-visita cada URL con organizador vía Playwright (FB bloquea requests simples),
extrae nombre (og:title) + fecha + lugar + organizador del HTML público y los
consolida. Aditivo: nunca borra.

Uso:
    python3 recuperar_fb_cacheados.py [--dry-run]
"""

import asyncio
import csv
import random
import re
import sys
from datetime import datetime

from scrapers.facebook_mcp import FacebookEventsFinder
from utils.helpers import deduplicar_eventos
from main_fuentes import clasificar_eventos, limpiar_calidad

OUTPUT_TODOS = "eventos_encontrados.csv"
OUTPUT_LIMPIO = "eventos_psytrance.csv"


def cargar_csv_existente(filename):
    eventos = []
    try:
        with open(filename, encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                eventos.append(dict(row))
        print(f"📥 Cargados {len(eventos)} eventos de {filename}")
    except FileNotFoundError:
        print(f"⚠️ {filename} no encontrado")
    return eventos


def exportar_csv(eventos, filename):
    keys = ["nombre", "fecha", "lugar", "pais", "continente", "subcontinente",
            "fuente", "organizador", "email", "link", "subgenero"]
    with open(filename, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=keys, extrasaction="ignore")
        writer.writeheader()
        for ev in eventos:
            link = ev.get("link") or ev.get("url") or "N/A"
            ev_out = dict(ev)
            ev_out["link"] = link
            writer.writerow(ev_out)
    print(f"✅ CSV exportado: {filename} ({len(eventos)} filas)")


async def visitar_con_playwright(scraper, url, sem):
    """Visita una URL de evento FB con Playwright y extrae nombre/fecha/lugar."""
    from playwright.async_api import async_playwright

    async with sem:
        ev = {
            "nombre": "N/A", "fecha": "N/A", "lugar": "N/A", "ciudad": "N/A",
            "pais": "N/A", "tipo_lugar": "N/A", "fuente": "Facebook (público SERP)",
            "organizador": "N/A", "email": "N/A", "url": url, "link": url,
            "url_perfil": None,
            "subgenero": "general", "descripcion": "N/A",
        }
        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(
                    headless=True,
                    args=["--no-sandbox", "--disable-dev-shm-usage",
                           "--disable-blink-features=AutomationControlled"],
                )
                context = await scraper._crear_context_con_fallback(browser, url_probe=url)
                page = await context.new_page()
                await scraper.anti_block.apply_playwright_stealth(page)
                await page.goto(url, timeout=20000, wait_until="domcontentloaded")
                await asyncio.sleep(random.uniform(1.5, 3.0))
                cuerpo = await page.evaluate("document.body ? document.body.innerText : ''")
                html = await page.content()
                await page.close()
                await context.close()
                await browser.close()

                # Nombre: og:title
                if ev["nombre"] in ("N/A", ""):
                    mt = re.search(r'<meta\s+property="og:title"\s+content="([^"]+)"', html)
                    if mt:
                        ev["nombre"] = mt.group(1).strip()[:120]
                if ev["nombre"] in ("N/A", "") and cuerpo:
                    primeras = [l.strip() for l in cuerpo.split("\n")
                                if l.strip() and "facebook.com" not in l][:5]
                    if primeras:
                        ev["nombre"] = primeras[0][:120]

                # Fecha
                fecha = scraper._normalizar_fecha_publica(cuerpo[:8000])
                if fecha:
                    ev["fecha"] = fecha

                # Organizador desde HTML
                if ev["organizador"] in ("N/A", ""):
                    morg = re.search(
                        r"(?:Evento de|event by|hosted by|by)\s+"
                        r"([A-Z][A-Za-zÀ-ÿ0-9&.' -]{2,60})",
                        cuerpo[:10000].replace("\n", " "), re.IGNORECASE
                    )
                    if morg and "facebook" not in (morg.group(1).lower()):
                        ev["organizador"] = morg.group(1).strip().rstrip(".,")[:60]

                # Lugar
                if ev["lugar"] in ("N/A", ""):
                    lugares = re.findall(
                        r"\b[A-Z][A-Za-zÀ-ÿ'\- ]{2,40}\s*,\s*[A-Z][a-zà-ÿ]{2,40}",
                        cuerpo[:2500]
                    )
                    if lugares:
                        ev["lugar"] = lugares[0][:80]

                # Email
                email = scraper._extractor_email_desde_texto(cuerpo)
                if not email:
                    email = scraper._extractor_email_desde_texto(
                        re.sub(r"<[^>]+>", " ", html)[:12000])
                if email:
                    ev["email"] = email
            return ev
        except Exception as e:
            ev["_error"] = str(e)
            return ev


async def main(dry_run=False):
    print("=" * 60)
    print("🔁 Recuperación de Facebook cacheados (Playwright)")
    print(f"📅 {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 60)

    eventos_existentes = cargar_csv_existente(OUTPUT_TODOS)
    csv_urls = set()
    for ev in eventos_existentes:
        u = ev.get("link") or ev.get("url") or ""
        if u:
            csv_urls.add(u.strip().lower())

    scraper = FacebookEventsFinder()
    cache = scraper._cargar_eventos_visitados()

    pendientes = []
    for url, data in cache.items():
        url_l = url.strip().lower()
        if url_l in csv_urls:
            continue
        org = data.get("organizador", "")
        if not org or org in ("N/A", "", "None"):
            continue
        pendientes.append(url)

    print(f"\n🔍 {len(pendientes)} URLs cacheadas con organizador y sin CSV")
    if not pendientes:
        print("Nada que recuperar.")
        return
    if dry_run:
        for u in pendientes:
            print(f"   - {u}")
        print(f"\n🔍 Modo dry-run — sin exportar ({len(pendientes)} recuperables)")
        return

    sem = asyncio.Semaphore(3)
    resultados = await asyncio.gather(
        *[visitar_con_playwright(scraper, url, sem) for url in pendientes],
        return_exceptions=True
    )

    recuperados = []
    for i, (url, res) in enumerate(zip(pendientes, resultados), 1):
        if isinstance(res, Exception):
            print(f"  ❌ [{i}/{len(pendientes)}] {url[:55]}... {str(res)[:40]}")
            continue
        ok = res.get("nombre") and res["nombre"] not in ("N/A", "")
        print(f"  {'✅' if ok else '❌'} [{i}/{len(pendientes)}] "
              f"{res.get('nombre','?')[:60]} | org={res.get('organizador','')[:25]}")
        if ok:
            recuperados.append(res)

    print(f"\n📊 Recuperados: {len(recuperados)} eventos con nombre")

    # Clasificar nuevos
    recuperados = clasificar_eventos(recuperados)
    antes = len(recuperados)
    recuperados = limpiar_calidad(recuperados)
    print(f"🧹 Calidad: {antes} → {len(recuperados)} recuperados reales")

    todos = deduplicar_eventos(eventos_existentes + recuperados)

    exportar_csv(todos, OUTPUT_TODOS)
    exportar_csv(todos, OUTPUT_LIMPIO)

    fuentes = {}
    for e in todos:
        f = e.get("fuente", "?")
        fuentes[f] = fuentes.get(f, 0) + 1
    print("\n📊 Desglose por fuente:")
    for f, c in sorted(fuentes.items(), key=lambda x: -x[1]):
        print(f"   {f}: {c}")
    fb_count = sum(1 for e in todos if "Facebook" in e.get("fuente", ""))
    print(f"\n✅ Total: {len(todos)} | Facebook: {fb_count}")


if __name__ == "__main__":
    dry = "--dry-run" in sys.argv
    asyncio.run(main(dry_run=dry))
