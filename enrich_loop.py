"""Loop de enriquecimiento con timeout HARD por subprocess.

Estrategia por campo:
- organizador: 1) slug de URL (gratis, sin red) → 2) OpenGraph og:description via fetch
- email      : fetch de RA (mailto) + regex de texto plano

Resume-safe: checkpoint guarda los links procesados. Escritura única + atomic rename.
Respeto al semaforo Tor: una peticion simultanea.
"""
import csv
import json
import os
import random
import re
import signal
import subprocess
import sys
import time
from typing import Optional
from datetime import datetime
from pathlib import Path
from threading import Semaphore

_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

VENV_PY = str(_ROOT / ".venv/bin/python")
WORKER = str(_ROOT / "enrich_worker.py")
CSV_PATH = _ROOT / "eventos_encontrados.csv"
CHECKPOINT = _ROOT / "enrichment_checkpoint.json"
BACKUP_DIR = _ROOT / "backups_enrich"
LOG_FILE = _ROOT / "enrichment.log"

TOR_SEMAPHORE = Semaphore(1)
HARD_TIMEOUT = 20
ROTATE_EVERY = 12
CHECKPOINT_EVERY = 25
MAX_RETRIES = 3
BACKOFF_BASE = 4

EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")
NO_EMAIL = ("sentry", "noreply", "example", "domain", "email", "@2x", "twitter", "@facebook", "@instagram")

VACIOS = {"", "N/A", "None", "nan", "NA", "NONE"}


def log(msg):
    ts = datetime.now().strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def backup_csv():
    BACKUP_DIR.mkdir(exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = BACKUP_DIR / f"eventos_{ts}.csv"
    import shutil
    shutil.copy2(CSV_PATH, dest)
    log(f"BACKUP -> {dest}")
    return dest


def rotar_tor():
    try:
        from core.tor_manager import renovar_identidad_tor
        renovar_identidad_tor()
        log("Tor rotado")
    except Exception as e:
        log(f"Tor rotation fail: {e}")


def fetch_hard(url: str) -> dict:
    """Fetch con timeout HARD: subprocess + SIGKILL. NUNCA se cuelga."""
    try:
        proc = subprocess.Popen(
            [VENV_PY, WORKER, url, str(HARD_TIMEOUT)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(_ROOT),
        )
        try:
            stdout, stderr = proc.communicate(timeout=HARD_TIMEOUT)
            if proc.returncode != 0:
                return {"status": 0, "html": "", "error": f"exit {proc.returncode}"}
            return json.loads(stdout.decode())
        except subprocess.TimeoutExpired:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
            proc.wait(timeout=5)
            return {"status": 0, "html": "", "error": "HARD_TIMEOUT"}
    except Exception as e:
        return {"status": 0, "html": "", "error": f"popen: {e}"}


# ---------- EXTRACCIÓN ----------

def extraer_organizador(html: str, url: str) -> Optional[str]:
    # Slug de URL (gratis): facebook.com/<perfil>/events/<id>
    m = re.search(r"facebook\.com/([^/]+)/events/\d", url)
    if m:
        slug = m.group(1)
        if slug.lower() not in ("events", "groups", "pages", "explore", "search", "me"):
            perfil = slug.replace("-", " ").replace(".", " ").replace("%s", "").strip()
            perfil = re.sub(r"\s+", " ", perfil).title()
            perfil = re.sub(r"\d+", "", perfil).strip()
            if len(perfil) >= 3 and "%" not in perfil:
                return perfil
    if not html:
        return None
    # og:description: "Event in <lugar> by <NOMBRE> and N others on <fecha>"
    m2 = re.search(r'<meta[^>]+property=["\']og:description["\'][^>]+content=["\'](.*?)["\']', html, re.I | re.S)
    if m2:
        desc = m2.group(1)
        # capturar lo que sigue a "by " hasta " on " o " and "
        m3 = re.search(r"\bby\s+([^·\n]{0,80})", desc)
        if m3:
            org = m3.group(1).strip()
            org = re.split(r"\s+on\s+|\s+and\s+\d|,?\s+\d+\s+others?", org, flags=re.I)[0].strip()
            org = org.strip(",.").strip()
            if 3 <= len(org) <= 60:
                return org
    # JSON-LD organizer
    m4 = re.search(r'"organizer"\s*:\s*\{"@type":\s*"[^"]*",\s*"name":\s*"([^"]+)"', html)
    if m4:
        return m4.group(1).strip()
    return None


def extraer_email(html: str) -> Optional[str]:
    if not html:
        return None
    mailtos = re.findall(r'href="mailto:([^"?]+)', html)
    for e in mailtos:
        e = e.strip()
        if "@" in e and not any(x in e.lower() for x in NO_EMAIL):
            return e
    candidatos = EMAIL_RE.findall(html)
    for e in candidatos:
        if not any(x in e.lower() for x in NO_EMAIL):
            return e.lower()
    return None


# ---------- CHECKPOINT (persistente: link -> valor) ----------

def load_checkpoint(campo=None) -> dict:
    """Devuelve dict {link: {campo: valor}} ya procesados. Compatible con formato anterior."""
    if CHECKPOINT.exists():
        try:
            data = json.loads(CHECKPOINT.read_text())
            if "valores" in data:
                return data["valores"]
            # formato antiguo: lista de links
            return {l: {} for l in data.get("procesados", [])}
        except Exception:
            return {}
    return {}


def save_checkpoint(valores: dict):
    tmp = CHECKPOINT.with_suffix(".tmp")
    tmp.write_text(json.dumps({"valores": valores}))
    tmp.replace(CHECKPOINT)


def aplicar_checkpoint(eventos, valores, campo):
    """Reaplica los valores ya enriquecidos y guardados en checkpoint al dataset en memoria."""
    idx = {ev.get("link"): ev for ev in eventos}
    aplicados = 0
    for link, info in valores.items():
        if campo in info and link in idx:
            v = (info[campo] or "").strip()
            if v and v.upper() not in VACIOS:
                idx[link][campo] = v
                aplicados += 1
    if aplicados:
        log(f"Re-aplicados {aplicados} valores de checkpoint al dataset")
    return aplicados


# ---------- COLA ----------

def construir_cola(eventos, procesados, campo, url_filtro=None):
    cola = []
    vistos = set()
    for ev in eventos:
        link = (ev.get("link") or "").strip()
        if url_filtro and url_filtro not in link:
            continue
        valor = (ev.get(campo) or "").strip()
        if valor.upper() in VACIOS and link and link not in procesados and link not in vistos:
            cola.append(ev)
            vistos.add(link)
    log(f"Cola '{campo}': {len(cola)} eventos")
    return cola


def _cobertura(eventos, campo):
    return sum(1 for ev in eventos if (ev.get(campo) or "").strip().upper() not in VACIOS)


def escribir_csv(eventos, cols):
    tmp = CSV_PATH.with_suffix(".nuevo.csv")
    with open(tmp, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader(); w.writerows(eventos)
    tmp.replace(CSV_PATH)


# ---------- LOOP ----------

def enriquecer_campo(campo, extractor_fn, solo_fuente=None, max_items=None, url_filtro=None, max_seconds=None):
    backup_csv()
    with open(CSV_PATH, encoding="utf-8") as f:
        eventos = list(csv.DictReader(f))
    total_original = len(eventos)
    cols = list(eventos[0].keys())
    idx = {ev.get("link"): i for i, ev in enumerate(eventos)}

    valores = load_checkpoint()
    # reaplicar valores ya enriquecidos de este campo
    for link, info in valores.items():
        if campo in info and (info[campo] or "").strip().upper() not in VACIOS:
            pos = idx.get(link)
            if pos is not None:
                eventos[pos][campo] = info[campo]
    en_valores = {l for l, info in valores.items() if (info.get(campo) or "").strip().upper() not in VACIOS}
    log(f"Checkpoint: {len(valores)} vistos, {len(en_valores)} con valor de '{campo}'")

    cola = construir_cola(eventos, en_valores, campo, url_filtro)
    if not cola:
        log("Nada que hacer (todo procesado).")
        if CHECKPOINT.exists():
            CHECKPOINT.unlink()
        return
    if max_items:
        cola = cola[:max_items]
        log(f"Limite de prueba: {max_items}")
    if max_seconds:
        log(f"Tope de tiempo por ejecucion: {max_seconds}s")

    inicio = time.time()
    exitos = fallos = peticiones = 0
    cortado = False

    for i, evento in enumerate(cola):
        if max_seconds and (time.time() - inicio) > max_seconds:
            cortado = True
            log(f"Tope de {max_seconds}s alcanzado. Guardando...")
            break
        link = (evento.get("link") or "").strip()
        if solo_fuente and solo_fuente not in (evento.get("fuente") or ""):
            valores.setdefault(link, {})[campo] = ""
            continue

        resultado = None
        if campo == "organizador":
            resultado = extraer_organizador("", link)

        if not resultado:
            for intento in range(MAX_RETRIES):
                with TOR_SEMAPHORE:
                    peticiones += 1
                    if peticiones % ROTATE_EVERY == 0:
                        rotar_tor()
                    resp = fetch_hard(link)

                status = resp.get("status", 0)
                html = resp.get("html", "")
                error = resp.get("error", "")

                if error == "HARD_TIMEOUT":
                    log(f"HARD_TIMEOUT {link[:50]}")
                    time.sleep(BACKOFF_BASE * (2 ** intento) + random.uniform(0, 2))
                    continue
                if status == 429:
                    log("429 backoff largo")
                    time.sleep(60 + random.uniform(0, 30)); rotar_tor(); continue
                if status == 403:
                    log("403 captcha"); rotar_tor(); break
                if status == 0 and not html:
                    time.sleep(BACKOFF_BASE * (2 ** intento)); continue
                if status == 200:
                    resultado = extractor_fn(html, link)
                    break
                break

        valores.setdefault(link, {})[campo] = resultado or ""
        if resultado:
            pos = idx.get(link)
            if pos is not None:
                eventos[pos][campo] = resultado
                exitos += 1
        else:
            fallos += 1

        if (i + 1) % CHECKPOINT_EVERY == 0:
            save_checkpoint(valores)
            escribir_csv(eventos, cols)
            rate = (i + 1) / max(0.1, time.time() - inicio)
            log(f"[{i+1}/{len(cola)}] rate={rate:.1f}/s exitos={exitos} fallos={fallos} "
                f"cobertura={_cobertura(eventos,campo)}/{total_original} CP+CSV")
        time.sleep(random.uniform(1.5, 4))

    # Escritura final siempre (atómica) + checkpoint
    save_checkpoint(valores)
    escribir_csv(eventos, cols)
    total_final = len(eventos)
    if total_final != total_original:
        log(f"INTEGRIDAD ROTA abortando: {total_final} != {total_original}")
        return {"error": "integridad"}
    cobertura = _cobertura(eventos, campo)
    if not cortado:
        if CHECKPOINT.exists():
            CHECKPOINT.unlink()
    log(f"PASO {campo}: exitos={exitos} fallos={fallos} "
        f"cobertura={cobertura}/{total_original} ({100*cobertura/total_original:.1f}%) "
        f"tiempo={(time.time()-inicio)/60:.1f}min {'[CORTADO, resume con max_seconds]' if cortado else '[COMPLETO]'}")
    return {"exitos": exitos, "fallos": fallos, "cobertura": cobertura, "cortado": cortado}


if __name__ == "__main__":
    # Paso 1: organizador (primero slug gratis, luego fetch)
    enriquecer_campo("organizador", lambda html, url: extraer_organizador(html, url))
    # Paso 2: email (RA / otras fuentes con mailto)
    # enriquecer_campo("email", lambda html, url: extraer_email(html))