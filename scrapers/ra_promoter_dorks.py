#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
RA Promoter Dorks — Encuentra contactos de promoters de RA via dorks de DDG.

Pipeline:
  1. DDG dorks → nombres/promoters de RA
  2. Para cada nombre → buscar web/redes en DDG
  3. Scrapear webs → extraer emails (regex)
  4. Guardar en ra_promoter_contacts.json

Tor obligatorio. Max 1 request Tor a la vez.
"""
import json
import os
import re
import sys
import time
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

_PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from serp_worker import search as ddg_search
from core.http_client import get_html

# --- Paths ---
_PROMOTER_CONTACTS_FILE = Path(_PROJECT_ROOT) / "ra_promoter_contacts.json"
_PROMOTER_CHECKPOINT_FILE = Path(_PROJECT_ROOT) / "ra_promoter_dorks_checkpoint.json"
_PROMOTER_LOG_FILE = Path(_PROJECT_ROOT) / "ra_promoter_dorks.log"

# --- Constants ---
TIMEOUT_TOTAL = 300
MAX_DORKS_PER_RUN = 20
SLEEP_BETWEEN_QUERIES = (5.0, 10.0)
TIMEOUT_DDG = 25
TIMEOUT_FETCH = 15

# --- Email patterns ---
EMAIL_REGEX = re.compile(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}')
GENERIC_EMAILS = {
    'support@', 'info@', 'noreply@', 'no-reply@', 'admin@',
    'webmaster@', 'hostmaster@', 'postmaster@', 'abuse@',
    'spam@', 'hello@', 'contact@', 'marketing@', 'press@',
    'office@', 'team@', 'help@', 'feedback@', 'billing@',
    'your@', 'email@', 'test@', 'example@', 'user@',
    'privacy@', 'tickets@', 'volunteer@', 'subscribe@',
}

# Dominios no relevantes (CDN, analytics, placeholders)
NO_RELEVANT_DOMAINS = {
    'w3.org', 'cloudflareinsights.com', 'cloudflare.com',
    'googleapis.com', 'gstatic.com', 'google.com',
    'jquery.com', 'bootstrap.com', 'fontawesome.com',
    'example.com', 'example.org', 'test.com',
    'nightlifetokyo.com',  # sitio de ejemplo, no promoter real
    'sentry.io', 'ingest.sentry.io',  # error tracking
    'github.io', 'github.com',  # code hosting
    'herokuapp.com', 'vercel.app', 'netlify.app',  # hosting
    'wixpress.com', 'squarespace.com', 'wix.com',  # website builders
    'recaptcha.net',  # captcha
    'seo-500.jpg', 'responsive-100.avif',  # image files
    'sina.raw', 'raw.githubusercontent.com',  # raw content
}

# --- Discovery dorks (find RA promoter/artist names) ---
DORKS_DISCOVERY = [
    'site:ra.co/promoters psytrance',
    'site:ra.co/promoters "psytrance" OR "goa" OR "darkpsy"',
    'site:ra.co/promoters "psytrance" OR "forest" OR "hitech"',
    'site:ra.co psytrance promoter',
    'site:ra.co psytrance label',
    'site:ra.co/dj psytrance',
    'site:ra.co/dj "psytrance" OR "goa"',
    'site:ra.co psytrance "booking" OR "organizer"',
]

# --- Noise filters ---
NOISE_TITLES = {
    'login', 'sign in', 'sign up', 'register', 'privacy',
    'terms of service', 'cookie', 'advertisement', 'help',
    'faq', 'support', 'contact us', 'about us',
}

NOISE_URLS = {
    'login', 'signin', 'signup', 'register', 'marketplace',
    'buy', 'sell', 'shop', 'store', 'ad', 'sponsored',
}


def _log(msg: str) -> None:
    ts = datetime.now().strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    try:
        with open(_PROMOTER_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def _leer_json(ruta: Path, default: Any = None) -> Any:
    if not ruta.exists():
        return default
    try:
        with open(ruta, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return default


def _escribir_json(ruta: Path, datos: Any) -> None:
    try:
        tmp = str(ruta) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(datos, f, ensure_ascii=False, indent=2)
        os.replace(tmp, str(ruta))
    except (IOError, OSError):
        pass


def _random_sleep() -> None:
    import random
    time.sleep(random.uniform(*SLEEP_BETWEEN_QUERIES))


def _es_email_generico(email: str) -> bool:
    if any(email.lower().startswith(p) for p in GENERIC_EMAILS):
        return True
    # Filtrar dominios no relevantes
    domain = email.split('@')[1].lower() if '@' in email else ''
    if any(d in domain for d in NO_RELEVANT_DOMAINS):
        return True
    # Filtrar falsos positivos (archivos, URLs, etc.)
    if re.search(r'\.(jpg|jpeg|png|gif|webp|avif|svg|pdf|html)$', email.lower()):
        return True
    if re.search(r'^[a-zA-Z0-9]{20,}@', email):  # Strings muy largos antes del @
        return True
    if '@' in email and '.' not in email.split('@')[1]:
        return True
    return False


def _extraer_emails_html(html: str) -> List[str]:
    """Extrae emails unicos de HTML, filtrando genericos."""
    emails = EMAIL_REGEX.findall(html)
    vistos = set()
    resultado = []
    for e in emails:
        e_lower = e.lower()
        if e_lower in vistos:
            continue
        vistos.add(e_lower)
        if not _es_email_generico(e):
            resultado.append(e)
    return resultado


def _extraer_socials_snippet(texto: str) -> Dict[str, str]:
    """Extrae handles de redes sociales de un snippet de texto."""
    socials = {}
    # Instagram
    m = re.search(r'instagram\.com/([A-Za-z0-9_.]+)', texto)
    if m:
        handle = m.group(1)
        if handle.lower() not in ('reel', 'p', 'tv', 'explore', 'accounts'):
            socials['instagram'] = handle
    # Facebook
    m = re.search(r'facebook\.com/([A-Za-z0-9_.]+)', texto)
    if m:
        handle = m.group(1)
        if handle.lower() not in ('groups', 'events', 'pages', 'login', 'share'):
            # Filtrar años (e.g., "2008") y IDs numericos
            if not re.match(r'^\d{4,}$', handle):
                socials['facebook'] = handle
    # Soundcloud
    m = re.search(r'soundcloud\.com/([A-Za-z0-9_-]+)', texto)
    if m:
        socials['soundcloud'] = m.group(1)
    # Telegram
    m = re.search(r'(?:t\.me|telegram\.me)/([A-Za-z0-9_]+)', texto)
    if m:
        handle = m.group(1)
        if handle.lower() not in ('share', 'join', 'add', 'login', 'privacy'):
            socials['telegram'] = handle
    # Website (exclude social media + CDN/analytics)
    NO_WEBSITE_DOMAINS = [
        'facebook', 'instagram', 'soundcloud', 'twitter', 'x.com',
        'youtube', 'ra.co', 'google', 'w3.org', 'cloudflare', 'analytics',
        'fonts.', 'cdn.', 'static.', 'assets.', 'awswaf',
        'aws.', 'amazonaws.com', 'github.io', 'github.com',
        'herokuapp.com', 'vercel.app', 'netlify.app',
        'bandcamp.com',  # music platform, not promoter website
        'bcbits.com',  # Bandcamp CDN
        'linktr.ee', 'linktree.com',  # link aggregator
        'linkin.bio', 'bio.site', 'campsite.bio',  # link aggregators
        'babbel.sjv.io',  # translation service
        't.me', 'telegram.me',  # Telegram (handled separately)
        'wa.me', 'whatsapp.com',  # WhatsApp
        'pxf.io',  # ad tracking
        'reddit.com', 'reddit.co',  # Reddit
        'm.x.com', 'mobile.x.com',  # Twitter mobile
    ]
    for m in re.finditer(r'https?://([a-zA-Z0-9.-]+\.[a-zA-Z]{2,})', texto):
        domain = m.group(1)
        if any(s in domain for s in NO_WEBSITE_DOMAINS):
            continue
        socials['website'] = domain
    return socials


def _es_noise(titulo: str, url: str) -> bool:
    titulo_low = titulo.lower()
    if any(n in titulo_low for n in NOISE_TITLES):
        return True
    url_low = url.lower()
    if any(n in url_low for n in NOISE_URLS):
        return True
    return False


def _extraer_nombre_de_url(url: str, titulo: str) -> Optional[str]:
    """Extrae nombre de promoter/label/DJ de una URL de RA."""
    # ra.co/promoters/12345 → titulo es el nombre
    if '/promoters/' in url or '/dj/' in url or '/labels/' in url:
        # Limpiar titulo
        t = titulo.split('·')[0].strip()
        t = re.sub(r'\s*[-–·]\s*RA$', '', t)
        t = re.sub(r'\s*[-–·]\s*Resident Advisor$', '', t, flags=re.I)
        t = re.sub(r'\s*[-–·]\s*Próximos eventos.*$', '', t)
        t = re.sub(r'\s*[-–·]\s*Upcoming Events.*$', '', t)
        t = re.sub(r'\s*[-–·]\s*Artist Profile$', '', t, flags=re.I)
        t = re.sub(r'\s*[-–·]\s*Label$', '', t, flags=re.I)
        t = re.sub(r'\s*[-–·]\s*Record label$', '', t, flags=re.I)
        # Limpiar sufijos de locale (e.g., " - de.ra.co", " - es.ra.co")
        t = re.sub(r'\s*[-–]\s*[a-z]{2}\.ra\.co.*$', '', t)
        t = t.strip(' .')
        # Filtrar nombres genericos
        GENERIC_NAMES = {
            'promoters', 'promoter', 'artists', 'dj', 'djs',
            'labels', 'label', 'events', 'event', 'venues', 'venue',
            'upcoming events', 'past events', 'all events',
            'view all', 'see all', 'more', 'home', 'about',
            'resident advisor', 'ra',
        }
        if t.lower() in GENERIC_NAMES:
            return None
        if t and len(t) >= 3 and len(t) <= 80:
            return t
    return None


def _fase1_discovery(dorks: List[str], max_dorks: int) -> List[Dict]:
    """Fase 1: Buscar promoters de RA en DDG."""
    _log(f"Fase 1: {len(dorks)} dorks de discovery")
    nombres_encontrados = []
    urls_ra_vistas = set()

    for idx, dork in enumerate(dorks[:max_dorks]):
        _log(f"  Dork {idx+1}/{min(len(dorks), max_dorks)}: {dork[:60]}...")
        result = ddg_search(dork, timeout=TIMEOUT_DDG)

        for r in result.get('results', []):
            url = r.get('url', '')
            titulo = r.get('titulo', '')
            snippet = r.get('snippet', '')

            if _es_noise(titulo, url):
                continue
            if 'ra.co' not in url:
                continue
            if url in urls_ra_vistas:
                continue
            urls_ra_vistas.add(url)

            nombre = _extraer_nombre_de_url(url, titulo)
            if nombre:
                socials = _extraer_socials_snippet(f"{titulo} {snippet}")
                nombres_encontrados.append({
                    'nombre': nombre,
                    'fuente_ra': url,
                    'tipo': 'promoter' if '/promoters/' in url else
                            'dj' if '/dj/' in url else 'label',
                    'snippet': snippet[:300],
                    'socials_ddg': socials,
                })

        if idx < len(dorks) - 1:
            _random_sleep()

    _log(f"Fase 1: {len(nombres_encontrados)} promoters encontrados")
    return nombres_encontrados


def _fase2_contacto(promoters: List[Dict], max_searches: int) -> List[Dict]:
    """Fase 2: Buscar contacto de cada promoter en DDG + web scraping."""
    _log(f"Fase 2: {len(promoters)} promoters a buscar contacto")
    contactos = []
    buscados = set()

    for idx, prom in enumerate(promoters[:max_searches]):
        nombre = prom['nombre']
        if nombre in buscados:
            continue
        buscados.add(nombre)

        _log(f"  Buscando contacto: {nombre} ({idx+1}/{min(len(promoters), max_searches)})")

        # Buscar "{nombre}" email OR contact OR website
        queries = [
            f'"{nombre}" email contact',
            f'"{nombre}" website instagram',
        ]

        emails_encontrados = []
        socials = dict(prom.get('socials_ddg', {}))

        for q in queries:
            result = ddg_search(q, timeout=TIMEOUT_DDG)
            for r in result.get('results', []):
                url = r.get('url', '')
                titulo = r.get('titulo', '')
                snippet = r.get('snippet', '')

                # Extraer emails del snippet
                texto = f"{titulo} {snippet}"
                emails_snippet = EMAIL_REGEX.findall(texto)
                for e in emails_snippet:
                    if not _es_email_generico(e) and e not in emails_encontrados:
                        emails_encontrados.append(e)

                # Extraer socials del snippet
                new_socials = _extraer_socials_snippet(texto)
                for k, v in new_socials.items():
                    if k not in socials:
                        socials[k] = v

                # Intentar scrapear la pagina si es relevante
                if url and not any(s in url for s in [
                    'facebook.com', 'instagram.com', 'twitter.com',
                    'youtube.com', 'soundcloud.com'
                ]):
                    try:
                        html = get_html(url, timeout=TIMEOUT_FETCH)
                        if html:
                            emails_page = _extraer_emails_html(html)
                            for e in emails_page:
                                if e not in emails_encontrados:
                                    emails_encontrados.append(e)
                            new_socials = _extraer_socials_snippet(html)
                            for k, v in new_socials.items():
                                if k not in socials:
                                    socials[k] = v
                    except Exception:
                        pass

            if emails_encontrados:
                break  # Ya tenemos email, no buscar mas

        contactos.append({
            'nombre': nombre,
            'fuente_ra': prom.get('fuente_ra', ''),
            'tipo': prom.get('tipo', ''),
            'email': emails_encontrados[0] if emails_encontrados else '',
            'emails_todos': emails_encontrados,
            'instagram': socials.get('instagram', ''),
            'facebook': socials.get('facebook', ''),
            'website': socials.get('website', ''),
            'soundcloud': socials.get('soundcloud', ''),
            'telegram': socials.get('telegram', ''),
        })

        if idx < len(promoters) - 1:
            _random_sleep()

    _log(f"Fase 2: {len(contactos)} contactos procesados")
    return contactos


def scrape_ra_promoter_dorks(
    dorks: Optional[List[str]] = None,
    limite: int = 20,
) -> List[Dict]:
    """Punto de entrada principal.

    Pipeline de 2 fases:
    1. DDG dorks → nombres de promoters de RA
    2. Para cada nombre → buscar web/redes → extraer emails
    """
    start = time.time()
    _log(f"=== RA Promoter Dorks (limite={limite}) ===")

    # Cargar checkpoint
    checkpoint = _leer_json(_PROMOTER_CHECKPOINT_FILE, {'procesados': []})
    procesados_previos = set(checkpoint.get('procesados', []))

    # Cargar contactos existentes
    existentes = _leer_json(_PROMOTER_CONTACTS_FILE, [])
    if not isinstance(existentes, list):
        existentes = []
    nombres_existentes = {c.get('nombre', '') for c in existentes}

    # Fase 1: Discovery
    dorks_a_usar = dorks or DORKS_DISCOVERY
    promoters = _fase1_discovery(dorks_a_usar, limite)

    # Filtrar los ya procesados
    promoters_nuevos = [
        p for p in promoters
        if p['nombre'] not in procesados_previos
        and p['nombre'] not in nombres_existentes
    ]
    _log(f"Promoters nuevos: {len(promoters_nuevos)} (de {len(promoters)} total)")

    if not promoters_nuevos:
        _log("No hay nuevos promoters que procesar")
        return existentes

    # Fase 2: Contacto
    contactos_nuevos = _fase2_contacto(promoters_nuevos, limite)

    # Guardar resultados
    todos_contactos = existentes + contactos_nuevos
    _escribir_json(_PROMOTER_CONTACTS_FILE, todos_contactos)

    # Actualizar checkpoint
    for c in contactos_nuevos:
        procesados_previos.add(c['nombre'])
    _escribir_json(_PROMOTER_CHECKPOINT_FILE, {'procesados': list(procesados_previos)})

    elapsed = time.time() - start
    con_email = sum(1 for c in contactos_nuevos if c.get('email'))
    _log(f"=== Completado en {elapsed:.1f}s: {len(contactos_nuevos)} contactos, {con_email} con email ===")

    return todos_contactos


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="RA Promoter Dorks")
    parser.add_argument("--limite", type=int, default=10)
    parser.add_argument("--dorks", nargs="+", default=None)
    args = parser.parse_args()

    contactos = scrape_ra_promoter_dorks(dorks=args.dorks, limite=args.limite)
    print(f"\nTotal contactos: {len(contactos)}")
    for c in contactos[-10:]:
        email = c.get('email', '')
        ig = c.get('instagram', '')
        print(f"  {c['nombre'][:40]} | email={email} | ig={ig}")
