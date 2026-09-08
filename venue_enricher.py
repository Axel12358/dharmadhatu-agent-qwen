#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
VenueContactCache — pipeline venue→contacto reutilizable.

Enriquece los eventos RA que tienen el venue como organizador: hace los dorks
UNA vez por venue y aplica el email encontrado a TODOS los eventos de ese venue.

Cache persistente: venue_cache.json
"""
import csv
import json
import random
import re
import string
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

_PROJECT_ROOT = str(Path(__file__).resolve().parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from serp_worker import search as ddg_search
from core.http_client import get_html

CSV_FILE = Path(_PROJECT_ROOT) / "eventos_encontrados.csv"
CACHE_FILE = Path(_PROJECT_ROOT) / "venue_cache.json"
LOG_FILE = Path(_PROJECT_ROOT) / "venue_enricher.log"

EMAIL_REGEX = re.compile(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}')

# Venues genéricos que NO son enriquecibles (no es un venue real)
GENERIC_VENUES = {
    'tba', 'john doe', 'city hall', 'secret location', 'location',
    'various venues', 'to be announced', 'outdoors', 'outdoor',
    'online', 'tbd', 'nowhere', 'somewhere', 'berlin', 'paris',
    'amsterdam', 'london', 'barcelona', 'n/a', 'none', 'unknown',
    'presented by', 'b2b', 'vinyl only', 'rooftop', 'festivals',
    'festival', 'r/psytrance', 'reddit',
}

# Falsos positivos de email
GENERIC_EMAILS = {
    'support@', 'info@', 'noreply@', 'no-reply@', 'admin@',
    'webmaster@', 'hostmaster@', 'postmaster@', 'abuse@',
    'spam@', 'hello@', 'contact@', 'marketing@', 'press@',
    'office@', 'team@', 'help@', 'feedback@', 'billing@',
    'your@', 'email@', 'test@', 'example@', 'user@',
    'privacy@', 'tickets@', 'volunteer@', 'subscribe@',
    'phishing@', 'scam@', 'fraud@',
}
PLACEHOLDER_LOCAL = {
    'deine', 'dein', 'tu', 'name', 'nombre', 'vorname', 'somebody',
    'john', 'john.doe', 'jane', 'jane.doe', 'johndoe', 'janedoe',
    'testing', 'unknown', 'someone', 'insert', 'fake', 'prueba',
    'jean', 'dupont', 'jeandupont', 'jdupont', 'pierre', 'martin',
    'hans', 'mueller', 'muller', 'rossi', 'garcia', 'lopez',
    'prenom', 'nom', 'cognome', 'nachname', 'apellido',
    'contacto', 'kontakt', 'contatto',
}
NO_DOMAINS = {
    'w3.org', 'cloudflare.com', 'googleapis.com', 'gstatic.com',
    'sentry.io', 'example.com', 'test.com', 'github.io',
    'wix.com', 'squarespace.com', 'wordpress.com', 'delta.com',
    'booking.com', 'expedia.com', 'amazon.com', 'airbnb.com',
    # sociales/plataformas: su email no es contacto del venue/promoter
    'facebook.com', 'fb.com', 'instagram.com', 'twitter.com', 'x.com',
    'reddit.com', 'youtube.com', 't.me', 'telegram.org', 'whatsapp.com',
    'google.com', 'linkedin.com',
}
PLACEHOLDER_DOMAINS = {
    'email.de', 'email.com', 'mail.de', 'mail.com', 'gmail.de',
    'hotmail.de', 'web.de', 'example.de', 'test.de', 'domain.com',
    'yourdomain.com', 'example.org', 'noreply.de',
}

# Dominios técnicos (credenciales filtradas de JS/bundles, no contactos)
TECHNICAL_DOMAIN_PAT = re.compile(
    r'gserviceaccount\.com|appspotmail\.com|cloudfunctions\.net|'
    r'amazonaws\.com|azure\.com|serviceaccount|@.*\.internal$|'
    r'sentry\.io|sentry\.dsn|datadog|newrelic',
    re.I
)
# Plataformas ticketeras/guías: su email corporativo NO es el contacto del venue.
# (dice.fm, eventbrite, skiddle, etc. — salvo que el venue SEA la plataforma)
PLATFORM_DOMAINS = {
    'dice.fm', 'eventbrite.com', 'eventbrite.co.uk', 'ticketmaster.com',
    'ticketmaster.co.uk', 'skiddle.com', 'fatsoma.com', 'ticketswap.com',
    'ticketswap.nl', 'songkick.com', 'bandsintown.com', 'seetickets.com',
    'ticketweb.com', 'axs.com', 'livenation.com', 'livenation.fr',
    'booking.de', 'booking.com', 'booking.si', 'shotgun.live',
    'londonnightguide.com', 'beatport.com', 'duckduckgo.com',
    'yandex-team.ru', 'gigxchange.app',
}

# Agencias/plataformas/agregadores de booking cuyo email genérico NO es el
# contacto del venue (aparecen en muchos venues distintos en DDG).
AGENCY_DOMAINS = {
    'discotech.me', 'festscanner.com', 'sounds-promotion.de',
    'gethuman.com', 'redhavas.com', 'nightlife030.de',
    'powerline-agency.com', 'eventlocations.com',
    'speakerbookingagency.com',
}
TECHNICAL_LOCAL_PAT = re.compile(
    r'^(kube-|svc-|service-|noreply|no-reply|donotreply|mailer-daemon|postmaster)',
    re.I
)

PRIORITY_DOMAINS = (
    'gmail.com', 'yahoo.com', 'hotmail.com', 'outlook.com', 'googlemail.com',
    'icloud.com', 'protonmail.com', 'gmx.de', 'web.de', 'live.com',
    'aol.com', 'posteo.de', 'mailbox.org',
)


def log(msg):
    ts = datetime.now().strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    try:
        with open(LOG_FILE, 'a', encoding='utf-8') as f:
            f.write(line + '\n')
    except Exception:
        pass


def es_venue_util(venue):
    """True si el venue es enriquecible (nombre real, no genérico)."""
    if not venue:
        return False
    v = venue.strip().lower()
    if len(v) < 3:
        return False
    if v in GENERIC_VENUES:
        return False
    # "TBA - " o empieza con genérico
    if v.startswith('tba') or v.startswith('secret'):
        return False
    if any(g in v for g in ('john doe', 'city hall')):
        return False
    return True


def es_email_valido(email):
    """Filtra emails falsos o genéricos."""
    if not email:
        return False
    # URLs pegadas como email (https://..., www..., rutas) = basura de scrape
    if 'http' in email.lower() or 'www.' in email.lower() or '/' in email:
        return False
    # TLD con mayúsculas en el ORIGINAL (deWebsite) = artefacto de pegado HTML.
    # Se verifica antes del lower(). Dominios tipo RunningWild.co.uk son legítimos
    # (mayúsculas en 2º nivel), solo el TLD final debe ser minúsculas.
    _tld_orig = email.rsplit('.', 1)[-1] if '.' in email else ''
    if re.search(r'[A-Z]', _tld_orig):
        return False
    e = email.lower()
    if any(e.startswith(p) for p in GENERIC_EMAILS):
        return False
    if re.search(r'\.(jpg|jpeg|png|gif|webp|avif|svg|pdf|html|css|js)$', e):
        return False
    if re.search(r'^\w{20,}@', e):
        return False
    domain = e.split('@')[1] if '@' in e else ''
    if domain in NO_DOMAINS or domain in PLACEHOLDER_DOMAINS:
        return False
    if domain in PLATFORM_DOMAINS or domain in AGENCY_DOMAINS:
        return False
    if TECHNICAL_DOMAIN_PAT.search(e):
        return False
    local = e.split('@')[0] if '@' in e else ''
    if local in PLACEHOLDER_LOCAL:
        return False
    # Variantes con puntos/guiones: jean.dupont, jean-dupont == jeandupont
    local_norm = re.sub(r'[._-]', '', local)
    if local_norm in PLACEHOLDER_LOCAL:
        return False
    # Enmascarados/redactados: xxxxxx, ------, info/info repetidos sin identidad
    if len(set(local_norm)) <= 1 and len(local_norm) >= 3:
        return False
    if len(set(domain.replace('.', ''))) <= 1:
        return False
    # TLD final sospechosamente largo (>10) = pegado
    tld = domain.rsplit('.', 1)[-1] if '.' in domain else ''
    if len(tld) > 10:
        return False
    if TECHNICAL_LOCAL_PAT.match(local):
        return False
    if not re.match(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$', e):
        return False
    return True


def fetch_hard(url, timeout=12, rotar_si_falla=True):
    """Fetch con timeout HARD via subprocess + SIGKILL (curl_cffi+Tor).

    Si falla y rotar_si_falla=True, rota la identidad Tor (NEWNYM, seguro:
    solo afecta a circuitos nuevos) y reintenta una vez.
    """
    def _intento(u, t):
        try:
            import subprocess
            code = (
                "import sys; sys.path.insert(0,'/Users/angelgarcia/dharmadhatu_agent_qwen'); "
                "from core.http_client import get_html; "
                f"html=get_html({u!r}, timeout={t}); "
                "print(html if html else '')"
            )
            r = subprocess.run(
                ['/Users/angelgarcia/dharmadhatu_agent_qwen/.venv/bin/python', '-c', code],
                capture_output=True, text=True, timeout=t + 3
            )
            if r.returncode == 0 and r.stdout.strip():
                return r.stdout
            return None
        except subprocess.TimeoutExpired:
            return None
        except Exception:
            return None

    html = _intento(url, timeout)
    if html or not rotar_si_falla:
        return html
    # Reintento con circuito Tor nuevo
    if _rotar_identidad():
        time.sleep(3)
        return _intento(url, timeout)
    return None


def _rotar_identidad():
    """NEWNYM via Stem (solo circuitos nuevos, no interrumpe workers)."""
    try:
        from stem import Signal
        from stem.control import Controller
        with Controller.from_port(port=9051) as controller:
            try:
                controller.authenticate(password="dharmadhatu_tor_pass")
            except Exception:
                try:
                    controller.authenticate()
                except Exception:
                    return False
            controller.signal(Signal.NEWNYM)
            time.sleep(4)
            return True
    except Exception:
        return False


def _normalizar_texto(t):
    """Decodifica escapes unicode (\\u003e, \\u0040...) y entidades HTML."""
    if not t:
        return ''
    try:
        t = re.sub(r'\\u([0-9a-fA-F]{4})',
                   lambda m: chr(int(m.group(1), 16)), t)
    except Exception:
        pass
    t = t.replace('&gt;', '>').replace('&lt;', '<').replace('&amp;', '&')
    t = t.replace('&#64;', '@').replace('(at)', '@').replace('[at]', '@')
    return t


def _extraer_emails(texto):
    """Extrae emails válidos de un texto (con normalización previa)."""
    texto = _normalizar_texto(texto)
    out = []
    for e in EMAIL_REGEX.findall(texto):
        # Quitar prefijos artefacto: escapes unicode uXXXX, >, comillas.
        # OJO: no quitar 'x' suelta (xavier@... es legítimo).
        e = re.sub(r'^(u[0-9a-fA-F]{4}|[>\"\'])+', '', e)
        if es_email_valido(e):
            out.append(e)
    return out


def _buscar_una_query(venue, pais):
    """Ejecuta una query DDG y extrae emails de resultados + páginas."""
    emails = []
    try:
        if len(venue) > 45:
            venue_corto = venue[:45]
        else:
            venue_corto = venue
        query = f'{venue_corto} {pais} booking contact'.strip()
        result = ddg_search(query, timeout=20)
        for r in result.get('results', []):
            texto = f"{r.get('titulo','')} {r.get('snippet','')}"
            emails.extend(_extraer_emails(texto))
            url = r.get('url', '')
            if url and not any(s in url for s in ['facebook', 'instagram', 'twitter', 'reddit', 'youtube', 'tiktok', 'tripadvisor', 'yelp', 'google.com/maps']):
                # Priorizar páginas de contacto
                html = fetch_hard(url, timeout=10)
                if html:
                    emails.extend(_extraer_emails(html))
    except Exception:
        pass
    return emails


def _dedup(y):
    seen = set()
    out = []
    for e in y:
        k = e.lower()
        if k not in seen:
            seen.add(k)
            out.append(e)
    return out


def buscar_email_venue(venue, pais=''):
    """Busca email del venue vía DDG (3 queries en paralelo)."""
    if len(venue) > 45:
        v = venue[:45]
    else:
        v = venue
    queries = [
        f'{v} {pais} contact'.strip(),
        f'{v} {pais} booking'.strip(),
        f'{v} {pais} promoter'.strip(),
    ]
    emails = []
    with ThreadPoolExecutor(max_workers=3) as pool:
        futuros = [pool.submit(_buscar_una_query, q, '') for q in queries]
        for f in futuros:
            try:
                emails.extend(f.result(timeout=30))
            except Exception:
                pass
    emails = _dedup(emails)

    if not emails:
        return None

    # Priorizar email "propio" del venue (dominio no genérico tipo gmail).
    for e in emails:
        dom = e.split('@')[1].lower()
        if dom not in PRIORITY_DOMAINS:
            return e
    return emails[0]


def buscar_email_venue_v2(venue, pais=''):
    """2º intento: queries con ciudad/país + términos locales de contacto.

    Para clubs ES/FR/DE/IT donde 'booking email contact' en inglés falla.
    """
    if len(venue) > 40:
        v = venue[:40]
    else:
        v = venue
    p = (pais or '').strip()
    queries = [
        f'{v} {p} contacto'.strip(),
        f'{v} {p} club contact'.strip(),
        f'{v} {p} kontakt booking'.strip(),
    ]
    emails = []
    with ThreadPoolExecutor(max_workers=3) as pool:
        futuros = [pool.submit(_buscar_una_query, q, '') for q in queries]
        for f in futuros:
            try:
                emails.extend(f.result(timeout=30))
            except Exception:
                pass
    emails = _dedup(emails)

    if not emails:
        return None

    for e in emails:
        dom = e.split('@')[1].lower()
        if dom not in PRIORITY_DOMAINS:
            return e
    return emails[0]


# ---------- V3 (asesor externo): CT logs vía crt.sh (funciona vía Tor) ----------
_SOCIAL_URL_PAT = ('facebook', 'instagram', 'twitter', 'reddit', 'youtube',
                   'tiktok', 'tripadvisor', 'yelp', 'google.com/maps',
                   'songkick', 'bandsintown', 'skiddle', 'dice.fm',
                   'eventbrite', 'ticketmaster', 'residentadvisor',
                   'wikipedia', 'wikidata', 'musicbrainz', 'discogs',
                   'last.fm', 'setlist.fm', 'goabase', 'ektoplazm',
                   'psytrance.pl', 'partyflock', 'eventfinda')


def _dominio_desde_ddg(venue, pais='', timeout=20):
    """Dominio homepage del venue vía 1 query DDG (fiable vía Tor).

    Puntúa dominios candidatos por coincidencia con los tokens del nombre
    (evita que Wikipedia/guías ganen el ranking). Devuelve '' si no hay
    coincidencia razonable.
    """
    try:
        tokens = [t.lower() for t in re.split(r'[^a-zA-Z0-9]+', venue) if len(t) >= 4]
        v = venue[:45] if len(venue) > 45 else venue
        query = f'{v} {pais}'.strip()
        result = ddg_search(query, timeout=timeout)
        cands = []
        for r in result.get('results', []):
            url = (r.get('url') or '').lower()
            if not url or any(s in url for s in _SOCIAL_URL_PAT):
                continue
            m = re.match(r'https?://([^/]+)', url)
            if not m:
                continue
            dom = m.group(1).split(':')[0].lstrip('www.')
            if not re.match(r'^(?!-)[a-z0-9]([a-z0-9.-]*[a-z0-9])?\.[a-z]{2,}$', dom):
                continue
            if dom in cands:
                continue
            cands.append(dom)
            if len(cands) >= 8:
                break
        mejor, mejor_sc = '', 0
        for dom in cands:
            plano = dom.rsplit('.', 1)[0].replace('.', ' ').replace('-', ' ')
            sc = sum(1 for t in tokens if t in plano)
            if sc > mejor_sc:
                mejor, mejor_sc = dom, sc
        return mejor if mejor_sc >= 1 else ''
    except Exception:
        pass
    return ''


def _subdominios_desde_crtsh(domain, timeout=30):
    """Enumera subdominios vía CT logs (query exacta; best-effort vía Tor).

    crt.sh es intermitente vía Tor (403 según exit): ante fallo devuelve [].
    """
    try:
        from core.http_client import crear_sesion_tor
        s = crear_sesion_tor()
        r = s.get(f'https://crt.sh/?q={domain}&output=json', timeout=timeout)
        if r.status_code != 200:
            return []
        subs = set()
        for entry in r.json():
            for nv in (entry.get('name_value') or '').split('\n'):
                d = nv.strip().lower().lstrip('*.')
                if d.endswith('.' + domain) and d != domain:
                    subs.add(d)
        return sorted(subs)[:5]
    except Exception:
        return []


def _dominios_desde_crtsh(venue, timeout=30):
    """Descubre dominios reales del venue: DDG-homepage + subdominios CT.

    (La búsqueda wildcard de crt.sh falla vía Tor; se usa query exacta
    sobre el dominio descubierto por DDG.)
    """
    try:
        dominios = []
        base = _dominio_desde_ddg(venue)
        if base:
            dominios.append(base)
            for sub in _subdominios_desde_crtsh(base):
                if sub not in dominios:
                    dominios.append(sub)
        return dominios[:3]
    except Exception:
        return []


_CONTACT_PATHS = ('', '/contact', '/kontakt', '/contacto', '/contatto',
                  '/impressum', '/imprint', '/about', '/info')


def _emails_de_dominio(domain, timeout=10):
    """Extrae emails de la portada + rutas de contacto del dominio vía Tor."""
    emails = []
    try:
        for p in _CONTACT_PATHS:
            url = f'https://{domain}{p}'
            try:
                html = fetch_hard(url, timeout=timeout, rotar_si_falla=False)
            except TypeError:
                html = fetch_hard(url, timeout=timeout)
            if html:
                emails.extend(_extraer_emails(html))
                if emails and p != '':
                    break
    except Exception:
        pass
    return _dedup([e for e in emails if es_email_valido(e)])


# ---------- V1 (asesor externo): verificación SMTP vía Tor (puerto 25) ----------
def _mx_via_doh(domain, timeout=25):
    """Resuelve registros MX vía DNS-over-HTTPS a través de Tor."""
    try:
        from core.http_client import crear_sesion_tor
        s = crear_sesion_tor()
        for base in ('https://cloudflare-dns.com/dns-query',
                     'https://dns.google/resolve'):
            try:
                r = s.get(f'{base}?name={domain}&type=MX', timeout=timeout,
                          headers={'accept': 'application/dns-json'})
                if r.status_code != 200:
                    continue
                mxs = []
                for a in (r.json().get('Answer') or []):
                    if a.get('type') == 15:
                        parts = (a.get('data') or '').split()
                        if len(parts) == 2:
                            try:
                                mxs.append((int(parts[0]), parts[1].rstrip('.')))
                            except ValueError:
                                pass
                mxs.sort()
                if mxs:
                    return [h for _, h in mxs]
            except Exception:
                continue
    except Exception:
        pass
    return []


def _smtp_rcpt(mx_host, target, timeout=25):
    """Handshake SMTP manual vía Tor (RCPT TO sin enviar nada).

    Devuelve (código, texto): código '250' = aceptado, '5xx' = rechazado,
    'ERROR' = fallo de conexión.
    """
    try:
        import socks as _socks
    except ImportError:
        return ('ERROR', 'sin PySocks')
    s = _socks.socksocket()
    s.settimeout(timeout)
    try:
        s.connect((mx_host, 25))
        f = s.makefile('rw', newline='\r\n')
        f.readline()
        f.write('EHLO torverify.local\r\n')
        f.flush()
        while True:
            line = f.readline()
            if not line or len(line) < 4 or line[3:4] != '-':
                break
        f.write('MAIL FROM:<check@torverify.local>\r\n')
        f.flush()
        m = f.readline() or ''
        if not m.startswith('250'):
            try:
                f.write('QUIT\r\n')
                f.flush()
            except Exception:
                pass
            s.close()
            return ('ERROR', m.strip()[:80])
        f.write(f'RCPT TO:<{target}>\r\n')
        f.flush()
        r = f.readline() or ''
        try:
            f.write('QUIT\r\n')
            f.flush()
        except Exception:
            pass
        s.close()
        code = r[:3] if r[:3].isdigit() else 'ERR'
        return (code, r.strip()[:90])
    except Exception as e:
        try:
            s.close()
        except Exception:
            pass
        return ('ERROR', f'{type(e).__name__}: {str(e)[:60]}')


def smtp_verificar(email, timeout=25):
    """Verifica existencia de un email vía RCPT TO sobre Tor.

    Devuelve: 'verificado' | 'inexistente' | 'catchall' | 'sin_señal'.
    Primero sondea con local aleatorio: si el dominio acepta todo
    (catch-all, típico Google Workspace), la verificación no da señal.
    Ante fallo de conexión, rota identidad Tor (NEWNYM) y reintenta 1 vez.
    """
    try:
        _, domain = email.rsplit('@', 1)
    except ValueError:
        return 'sin_señal'
    domain = domain.lower()
    for intento in range(2):
        mxs = _mx_via_doh(domain, timeout=timeout)
        if not mxs:
            return 'sin_señal'
        hosts = mxs[:2]
        conecto = False
        for mx in hosts:
            rnd = 'zzqx' + ''.join(random.choice(string.ascii_lowercase + string.digits)
                                   for _ in range(8))
            code_rnd, _ = _smtp_rcpt(mx, f'{rnd}@{domain}', timeout=timeout)
            if code_rnd == 'ERROR':
                continue
            conecto = True
            if code_rnd.startswith('250'):
                return 'catchall'
            code, _ = _smtp_rcpt(mx, email, timeout=timeout)
            if code == 'ERROR':
                continue
            if code.startswith('250'):
                return 'verificado'
            if code.startswith('5'):
                return 'inexistente'
            return 'sin_señal'
        if not conecto and intento == 0:
            try:
                if _rotar_identidad():
                    time.sleep(8)
                    continue
            except Exception:
                pass
        return 'sin_señal'
    return 'sin_señal'


CANONICAL_LOCALS = ('booking', 'info', 'contact', 'bookings', 'events')


def buscar_email_venue_v3(venue, pais='', skip_ddg=False, smtp_timeout=15):
    """3er nivel: dominios vía CT logs + extracción directa + verificación SMTP.

    Devuelve (email|None, meta dict con domain/smtp/fuente).
    Los guesses canónicos (booking@...) solo se aceptan si SMTP los verifica.
    skip_ddg=True evita repetir DDG si el venue ya pasó por retry.
    """
    meta = {'domain': '', 'smtp': '', 'fuente': ''}
    dominios = _dominios_desde_crtsh(venue)
    if dominios:
        meta['domain'] = dominios[0]
        log(f"  🔍 CT logs {venue} → {dominios}")
    candidatos = []
    for d in dominios:
        candidatos.extend(_emails_de_dominio(d))
    candidatos = _dedup([e for e in candidatos if es_email_valido(e)])
    # V9: ficha Partyflock (email estructurado, alta confianza).
    # Se acepta directo salvo que SMTP demuestre inexistencia.
    try:
        pf = _email_desde_partyflock(venue, dominios_ct=dominios)
    except Exception:
        pf = None
    if pf:
        st = smtp_verificar(pf, timeout=smtp_timeout)
        log(f"  📧 SMTP {pf} → {st}")
        if st != 'inexistente':
            meta.update(domain=pf.split('@')[1].lower(), smtp=st,
                        fuente='partyflock')
            return pf, meta
        log(f"  🚫 Partyflock descartado (SMTP inexistente): {pf}")
    if not candidatos and not skip_ddg:
        for fn in (buscar_email_venue, buscar_email_venue_v2):
            try:
                e = fn(venue, pais)
            except Exception:
                e = None
            if e and es_email_valido(e) and e.lower() not in [c.lower() for c in candidatos]:
                candidatos.append(e)
    propios = [e for e in candidatos if e.split('@')[1].lower() not in PRIORITY_DOMAINS]
    orden = propios + [e for e in candidatos if e not in propios]
    fallback = None
    for e in orden[:2]:
        st = smtp_verificar(e, timeout=smtp_timeout)
        log(f"  📧 SMTP {e} → {st}")
        if st == 'verificado':
            meta.update(domain=e.split('@')[1].lower(), smtp='verificado',
                        fuente='smtp')
            return e, meta
        if st == 'inexistente':
            continue
        if fallback is None:
            fallback = (e, st)
    if fallback:
        e, st = fallback
        dom = e.split('@')[1].lower()
        # Si conocemos el dominio real (CT), un dominio corporativo no
        # relacionado es casi seguro ruido (agencia/PR). Solo se acepta el
        # dominio CT o providers personales (gmail de venues pequeños).
        if dominios and dom not in dominios and dom not in PRIORITY_DOMAINS:
            log(f"  🚫 Fallback descartado (dominio ajeno): {e}")
            return None, meta
        meta.update(domain=dom, smtp=st, fuente='ct+ddg')
        return e, meta
    if dominios and not candidatos:
        for d in dominios[:2]:
            for local in CANONICAL_LOCALS[:3]:
                e = f'{local}@{d}'
                if smtp_verificar(e, timeout=smtp_timeout) == 'verificado':
                    meta.update(domain=d, smtp='verificado', fuente='guess')
                    log(f"  ✅ Guess verificado: {e}")
                    return e, meta
    return None, meta


# ---------- V9 (propio): directorios con email estructurado (schema.org) ----------
# Partyflock (NL) expone <meta itemprop="email" content="..."> en sus fichas
# de venue y responde vía Tor. Los prefijos genéricos (info@) se aceptan SOLO
# si el dominio coincide con el venue (tokens o dominio CT).
def _email_desde_partyflock(venue, dominios_ct=None, timeout=25):
    """Email del venue desde su ficha Partyflock (microdato itemprop=email)."""
    try:
        v = venue[:45] if len(venue) > 45 else venue
        result = ddg_search(f'partyflock {v}', timeout=timeout)
        url_venue = ''
        for r in result.get('results', []):
            u = r.get('url', '')
            if re.search(r'partyflock\.nl/location/\d+', u):
                url_venue = u
                break
        if not url_venue:
            return None
        try:
            html = fetch_hard(url_venue, timeout=12, rotar_si_falla=False)
        except TypeError:
            html = fetch_hard(url_venue, timeout=12)
        if not html:
            return None
        m = re.search(r'itemprop="email"\s+content="([^"]+)"', html)
        if not m:
            m = re.search(r"itemprop='email'\s+content='([^']+)'", html)
        if not m:
            return None
        email = m.group(1).strip()
        if not re.match(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$', email):
            return None
        if re.search(r'\.(jpg|jpeg|png|gif|webp|avif|svg|png)$', email.lower()):
            return None
        dom = email.split('@')[1].lower()
        tokens = [t.lower() for t in re.split(r'[^a-zA-Z0-9]+', venue) if len(t) >= 4]
        plano = dom.rsplit('.', 1)[0].replace('.', ' ').replace('-', ' ')
        coincide = any(t in plano for t in tokens) or (dominios_ct and dom in dominios_ct)
        if coincide:
            log(f"  🏛️ Partyflock {venue} → {email}")
            return email
        if es_email_valido(email):
            log(f"  🏛️ Partyflock {venue} → {email} (genérico válido)")
            return email
        return None
    except Exception:
        return None


class VenueContactCache:
    """Cache persistente venue→contacto."""

    def __init__(self, cache_path=None):
        self.path = Path(cache_path or CACHE_FILE)
        self.cache = {}
        if self.path.exists():
            try:
                with open(self.path, 'r') as f:
                    self.cache = json.load(f)
            except Exception:
                self.cache = {}
        self.stats = {'fetched': 0, 'found': 0}

    def get(self, venue):
        return self.cache.get(venue)

    def get_or_fetch(self, venue, pais=''):
        key = venue.strip()
        if key in self.cache:
            info = self.cache[key]
            if info.get('fetched_at'):
                return info
        # Fetch
        self.stats['fetched'] += 1
        email = buscar_email_venue(key, pais)
        if email and not es_email_valido(email):
            email = None
        info = {
            'email': email or '',
            'pais': pais,
            'fetched_at': datetime.now().isoformat(),
        }
        if email:
            self.stats['found'] += 1
            log(f"  ✅ Venue: {key} → {email}")
        else:
            log(f"  ⚠️ Venue sin email: {key}")
        self.cache[key] = info
        self.save()
        time.sleep(2)
        return info

    def retry_failed(self, venue, pais=''):
        """2º intento para venues con email vacío: queries con contexto de país
        + términos locales (contacto/kontakt) + rotación Tor en fetch."""
        key = venue.strip()
        email = buscar_email_venue_v2(key, pais)
        if email and not es_email_valido(email):
            email = None
        info = {
            'email': email or '',
            'pais': pais,
            'fetched_at': datetime.now().isoformat(),
            'retry': True,
        }
        if email:
            log(f"  ✅ Retry OK: {key} → {email}")
        else:
            log(f"  ⚠️ Retry sin éxito: {key}")
        self.cache[key] = info
        self.save()
        time.sleep(2)
        return info

    def fetch_v3(self, venue, pais=''):
        """3er nivel (asesor externo): CT logs + extracción directa + SMTP."""
        key = venue.strip()
        previo = self.cache.get(key, {})
        email, meta = buscar_email_venue_v3(key, pais,
                                            skip_ddg=bool(previo.get('retry')))
        if email and not es_email_valido(email):
            email, meta = None, {'domain': '', 'smtp': '', 'fuente': ''}
        info = self.cache.get(key, {})
        info.update({
            'email': email or '',
            'pais': pais,
            'fetched_at': datetime.now().isoformat(),
            'retry_v3': True,
            'domain': meta.get('domain', ''),
            'smtp': meta.get('smtp', ''),
        })
        if email:
            log(f"  ✅ V3 OK: {key} → {email} [{meta.get('smtp')}]")
        else:
            log(f"  ⚠️ V3 sin éxito: {key}")
        self.cache[key] = info
        self.save()
        time.sleep(2)
        return info

    def marcar_imposible(self, venue):
        """V8: venue agotado (sin email tras todos los métodos) → no reintentar."""
        key = venue.strip()
        info = self.cache.get(key, {})
        info['imposible'] = True
        self.cache[key] = info
        self.save()

    def save(self):
        tmp = str(self.path) + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(self.cache, f, indent=2, ensure_ascii=False)
        try:
            self.path.write_text(Path(tmp).read_text(encoding='utf-8'), encoding='utf-8')
        except Exception:
            import shutil
            shutil.move(tmp, self.path)
        if Path(tmp).exists():
            try:
                Path(tmp).unlink()
            except Exception:
                pass

    def apply_to(self, venue, email, csv_rows):
        """Aplica email a filas de CSV cuyo venue coincide y no tengan email."""
        n = 0
        for r in csv_rows:
            if r.get('organizador', '').strip() == venue and not r.get('email', '').strip():
                r['email'] = email
                n += 1
        return n


def main():
    log("=== Venue Enricher iniciado ===")
    cache = VenueContactCache()

    with open(CSV_FILE, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        fieldnames = reader.fieldnames

    ra_sin = [r for r in rows if 'Resident Advisor' in r.get('fuente', '') and not r.get('email', '').strip()]
    log(f"RA sin email: {len(ra_sin)}")

    # Contar venues
    venues = Counter(r.get('organizador', '').strip() for r in ra_sin)
    venues_util = {v: c for v, c in venues.items() if es_venue_util(v)}
    log(f"Venues únicos: {len(venues)}, enriquecibles: {len(venues_util)}")

    # Procesar por frecuencia (primero los que cubren más eventos)
    ordenados = sorted(venues_util.items(), key=lambda x: -x[1])
    log(f"Venues enriquecibles (top): {[(v, c) for v, c in ordenados[:15]]}")

    updated_total = 0
    for venue, count in ordenados:
        # Obtener el país del primer evento RA de ese venue
        pais_ejemplo = ''
        for ev in ra_sin:
            if ev.get('organizador', '').strip() == venue and ev.get('pais', '').strip():
                pais_ejemplo = ev.get('pais', '').strip()
                break
        info = cache.get_or_fetch(venue, pais_ejemplo)
        if info.get('email'):
            updated = cache.apply_to(venue, info['email'], rows)
            updated_total += updated
            log(f"  → Aplicado a {updated} eventos")

    # Pase 2: reintento para venues con email vacío en cache.
    # Solo los que cubren >=2 eventos (ROI) y aún no tienen retry. V8: saltar imposibles.
    fallidos = []
    for venue, count in ordenados:
        info = cache.get(venue)
        if info and not info.get('email') and not info.get('retry') and not info.get('imposible') and count >= 2:
            fallidos.append((venue, count))
    if fallidos:
        log(f"=== Pase 2 (retry): {len(fallidos)} venues fallidos con >=2 eventos ===")
        for venue, count in fallidos:
            pais_ejemplo = ''
            for ev in ra_sin:
                if ev.get('organizador', '').strip() == venue and ev.get('pais', '').strip():
                    pais_ejemplo = ev.get('pais', '').strip()
                    break
            info = cache.retry_failed(venue, pais_ejemplo)
            if info.get('email'):
                updated = cache.apply_to(venue, info['email'], rows)
                updated_total += updated
                log(f"  → Retry aplicado a {updated} eventos")

    # Pase 3: reintento para venues con 1 solo evento (ROI menor).
    # Cierran huecos sueltos; no se repiten porque retry_failed marca retry=True.
    single_fallidos = [v for v, c in ordenados if c == 1]
    if single_fallidos:
        procesables = []
        for venue in single_fallidos:
            info = cache.get(venue)
            if not info:
                procesables.append(venue)
            elif not info.get('email') and not info.get('retry') and not info.get('imposible'):
                procesables.append(venue)
        if procesables:
            log(f"=== Pase 3 (retry single): {len(procesables)} venues con 1 evento ===")
            for venue in procesables:
                pais_ejemplo = ''
                for ev in ra_sin:
                    if ev.get('organizador', '').strip() == venue and ev.get('pais', '').strip():
                        pais_ejemplo = ev.get('pais', '').strip()
                        break
                info = cache.retry_failed(venue, pais_ejemplo)
                if info.get('email'):
                    updated = cache.apply_to(venue, info['email'], rows)
                    updated_total += updated
                    log(f"  → Retry aplicado a {updated} eventos")

    # Pase 4 (asesor externo V1+V3): CT logs + extracción directa + SMTP
    # para venues sin email, sin retry_v3 y sin flag imposible.
    v3_candidatos = []
    for venue, count in ordenados:
        info = cache.get(venue)
        if not info:
            v3_candidatos.append((venue, count))
        elif not info.get('email') and not info.get('retry_v3') and not info.get('imposible'):
            v3_candidatos.append((venue, count))
    if v3_candidatos:
        log(f"=== Pase 4 (V1+V3: CT logs + SMTP): {len(v3_candidatos)} venues ===")
        for venue, count in v3_candidatos:
            pais_ejemplo = ''
            for ev in ra_sin:
                if ev.get('organizador', '').strip() == venue and ev.get('pais', '').strip():
                    pais_ejemplo = ev.get('pais', '').strip()
                    break
            info = cache.fetch_v3(venue, pais_ejemplo)
            if info.get('email'):
                updated = cache.apply_to(venue, info['email'], rows)
                updated_total += updated
                log(f"  → V3 aplicado a {updated} eventos")

    # Pase V8 (asesor externo): marcar imposibles.
    # Venues con <2 eventos, sin email, ya reintentados (retry + retry_v3) → no gastar más ciclos.
    n_impos = 0
    for venue, count in ordenados:
        info = cache.get(venue)
        if (info and not info.get('email') and info.get('retry') and info.get('retry_v3')
                and not info.get('imposible') and count < 2):
            cache.marcar_imposible(venue)
            n_impos += 1
    if n_impos:
        log(f"=== V8: {n_impos} venues marcados como imposibles ===")

    # Guardar CSV con merge ATÓMICO bajo lock (core/csv_lock): re-leer fresco
    # y aplicar solo nuestras actualizaciones sobre filas sin esos datos.
    # Evita sobrescribir el enriquecimiento concurrente de otros procesos.
    def _clave(r):
        return r.get('link', '') or f"{r.get('nombre','')}|{r.get('fecha','')}"
    mejoras = {}
    for r in rows:
        em = r.get('email', '').strip()
        og = r.get('organizador', '').strip()
        if em or (og and og.lower() != 'n/a'):
            mejoras[_clave(r)] = (em, og)
    from core.csv_lock import csv_locked_rows
    with csv_locked_rows(str(CSV_FILE)) as (frescas, fieldnames):
        n_merge = 0
        for r in frescas:
            m = mejoras.get(_clave(r))
            if m:
                em, og = m
                if em and not r.get('email', '').strip():
                    r['email'] = em
                    n_merge += 1
                if og and og.lower() != 'n/a' and (not r.get('organizador', '').strip() or r.get('organizador', '').strip().lower() == 'n/a'):
                    r['organizador'] = og
                    n_merge += 1
    log(f"=== Merge CSV bajo lock: {n_merge} campos aplicados ===")

    log(f"=== Total actualizados: {updated_total} ===")
    log(f"=== Cache: {cache.stats} ===")


if __name__ == "__main__":
    main()