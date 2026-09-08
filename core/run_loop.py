"""Driver "en loop" — acumula contactos/confirmaciones en lotes capsados.

Cada etapa corre en un subprocess hijo y se la mata por wall-clock si no
termina (curl_cffi/requests dejan hilos no-daemon que bloquean la salida).
El CSV se guarda incrementalmente dentro de cada etapa, así no se pierde
avance al matar el subprocess. Re-ejecutar `python3 core/run_loop.py`
reanuda y sigue acumulando.

Etapas:
1. Goabase 2027 (gratis, Tor) — descubrimiento principal, funciona sin proxy.
2-5. Requieren proxy residencial de terceros (bloqueados por Tor sin proxy).
"""
from __future__ import annotations
import subprocess
import sys
import time

STAGES = [
    # Goabase: funciona gratis vía Tor (sin IP del usuario)
    "from core.goabase_2027 import escanear; print('GOABASE', len(escanear()))",
    # --- Las siguientes requieren proxy residencial (DHARMA_PROXY_URL) ---
    "from core.fuentes_extra import recolectar_fuentes; print('FASE4', recolectar_fuentes())",
    ("from core.enriquecer_fb_og import enriquecer; "
     "print('OG', enriquecer(max_fetches=4, rotar_cada=1, timeout_por_url=6, tiempo_max_seg=300))"),
    ("from core.enriquecer_contactos_dorks import enriquecer_contactos_dorks; "
     "print('DK', enriquecer_contactos_dorks(max_n=8, timeout_dork=12))"),
    ("from core.monitor_organizadores import monitor_2027; "
     "print('MON', len(monitor_2027(max_candidatos=8)))"),
]


def run_round(round_no: int, cap: int = 45):
    print(f"\n=== ROUND {round_no} ===")
    for code in STAGES:
        p = subprocess.Popen([sys.executable, "-u", "-c", code])
        try:
            rc = p.wait(timeout=cap)
            print(f"[ok rc={rc}] {code[:40]}")
        except subprocess.TimeoutExpired:
            p.kill()
            print(f"[kill] {code[:40]}")
        except Exception as e:
            print(f"[err] {code[:40]}: {e}")


if __name__ == "__main__":
    rounds = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    cap = int(sys.argv[2]) if len(sys.argv) > 2 else 45
    t0 = time.time()
    for r in range(1, rounds + 1):
        run_round(r, cap)
        if time.time() - t0 > 240:
            print("global cap 240s alcanzado")
            break
    print("loop done")
