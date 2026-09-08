"""Lead-gen B2B gratis y sin IP del usuario: empresas -> dominio -> contactos.

Pipeline (todo por Tor / proxy activo, nunca IP real):
  1. Fuente de leads: lista de empresas o sector via API libre (Arbeitnow).
  2. Resolucion empresa->dominio: Clearbit autocomplete (publico, sin clave).
  3. Extraccion de contactos: se raspen las paginas publicas de la empresa
     (/, /contact, /about) por Tor -> emails, telefonos, redes.

Nota: esto entrega contactos NIVEL EMPRESA (info@, ventas@, telefono, LinkedIn
de la compania). No perfiles personales de LinkedIn/Indeed (eso exige proxy de
pago). Es lo que una empresa suele necesitar para outreach B2B.
"""
from __future__ import annotations

import re
import csv
import time
from pathlib import Path

import requests

from core.http_client import get_active_proxies

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SALIDA = PROJECT_ROOT / "contactos_empresas.csv"
CAMPOS = ["empresa", "dominio", "email", "telefono", "linkedin", "web", "fuente"]
RUTAS = ["", "/contact", "/contact-us", "/about", "/about-us", "/en/contact"]
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_DESCARTE = re.compile(r"@(w3\.org|schema\.org|sentry\.|.*\.(png|jpg|jpeg|gif|svg|webp|css|js))", re.I)
_TEL = re.compile(r"(?:\+?\d[\d\s().-]{7,}\d)")
_LINKEDIN = re.compile(r"https?://(?:www\.)?linkedin\.com/(?:company|in)/[A-Za-z0-9_-]+", re.I)
TOPE_SEG = 180


def _sesion():
    s = requests.Session()
    s.proxies.update(get_active_proxies())
    s.headers.update({"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/125.0 Safari/537.36"})
    return s


def resolver_dominio(s, nombre):
    try:
        r = s.get("https://autocomplete.clearbit.com/v1/companies/suggest?query=" + nombre, timeout=15)
        if r.status_code != 200:
            return None
        for c in r.json():
            if c.get("domain"):
                return c["domain"]
    except Exception:
        return None
    return None


def extraer_contactos(s, dominio):
    emails, tels, links = set(), set(), set()
    for ruta in RUTAS:
        try:
            url = f"https://{dominio}{ruta}"
            resp = s.get(url, timeout=20, allow_redirects=True)
            if resp.status_code != 200:
                continue
            txt = resp.text
        except Exception:
            continue
        for e in _EMAIL.findall(txt):
            if not _DESCARTE.search(e) and not e.lower().endswith((".png", ".jpg")):
                emails.add(e.lower())
        for t in _TEL.findall(txt):
            t = re.sub(r"\s+", "", t)
            if len(re.sub(r"\D", "", t)) >= 9:
                tels.add(t)
        for l in _LINKEDIN.findall(txt):
            links.add(l)
    return emails, tels, links


def buscar_leads(s, query, max_n=40):
    """Empresas desde Arbeitnow (API libre, Tor)."""
    try:
        r = s.get("https://www.arbeitnow.com/api/job-board-api?search=" + query, timeout=30)
        data = r.json().get("data", []) if r.status_code == 200 else []
    except Exception:
        data = []
    nombres = []
    for j in data:
        n = (j.get("company_name") or "").strip()
        if n and n not in nombres:
            nombres.append(n)
        if len(nombres) >= max_n:
            break
    return nombres


def escanear(empresas=None, sector=None, max_n=40):
    s = _sesion()
    if not empresas:
        empresas = buscar_leads(s, sector or "", max_n=max_n)
    print(f"  [contactos] {len(empresas)} empresas a procesar")
    existentes = set()
    if SALIDA.exists():
        with SALIDA.open(encoding="utf-8") as f:
            for row in csv.DictReader(f):
                if row.get("dominio"):
                    existentes.add(row["dominio"])
    t0 = time.time()
    nuevos = []
    for i, nombre in enumerate(empresas, 1):
        if time.time() - t0 > TOPE_SEG:
            print("  [contactos] tope de tiempo")
            break
        dom = resolver_dominio(s, nombre)
        if not dom or dom in existentes:
            continue
        emails, tels, links = extraer_contactos(s, dom)
        if not (emails or tels or links):
            continue
        nuevos.append({
            "empresa": nombre, "dominio": dom,
            "email": "; ".join(sorted(emails)),
            "telefono": "; ".join(sorted(tels)[:3]),
            "linkedin": "; ".join(sorted(links)[:2]),
            "web": f"https://{dom}", "fuente": "empresa_web",
        })
        existentes.add(dom)
        if i % 10 == 0:
            print(f"  [contactos] {i}/{len(empresas)} | {len(nuevos)} contactos")
        time.sleep(0.4)
    if nuevos:
        escribir = not SALIDA.exists()
        with SALIDA.open("a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=CAMPOS)
            if escribir:
                w.writeheader()
            w.writerows(nuevos)
    print(f"  [contactos] nuevos: {len(nuevos)} -> {SALIDA}")
    return nuevos


if __name__ == "__main__":
    # Demo: pasa empresas o un sector. Ej: escanear(sector="software")
    import sys
    if len(sys.argv) > 1:
        escanear(empresas=sys.argv[1].split(","))
    else:
        escanear(sector="software")
