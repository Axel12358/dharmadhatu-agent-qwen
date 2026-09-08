#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Tor Pool — Gestión profesional de múltiples identidades Tor.

IDENTIDADES_TOR = [(9050, 9051), (9052, 9053), (9054, 9055)].
"""
from __future__ import annotations

import json
import os
import random
import signal as _sig
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

IDENTIDADES_TOR: List[Tuple[int, int]] = [(9050, 9051), (9052, 9053), (9054, 9055)]
TOR_BIN = "/usr/local/bin/tor"
TOR_DATA_DIR = Path(_PROJECT_ROOT) / "tor_data"
TOR_STATE_FILE = TOR_DATA_DIR / "tor_pool_estado.json"
IDENTITY_SLEEP = 5
CIRCUIT_COOLDOWN = 300
CHECK_TIMEOUT = 2
STARTUP_WAIT = 8
_tor_semaphore = None

def _puerto_disponible(puerto: int) -> bool:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(CHECK_TIMEOUT)
        s.connect(("127.0.0.1", puerto))
        s.close()
        return True
    except (socket.error, OSError, ConnectionRefusedError):
        return False

def _tor_esta_activo(puerto_socks: int) -> bool:
    return _puerto_disponible(puerto_socks)

def _leer_estado() -> Dict[str, any]:
    try:
        if TOR_STATE_FILE.exists():
            with open(TOR_STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
    except (json.JSONDecodeError, OSError):
        pass
    return {"circuitos": {}, "ultima_rotacion": time.time()}

def _guardar_estado(estado: Dict[str, any]) -> None:
    TOR_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = str(TOR_STATE_FILE) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(estado, f, indent=2)
    os.replace(tmp, str(TOR_STATE_FILE))

def _iniciar_tor(socks_port, control_port, data_dir_name):
    data_dir = TOR_DATA_DIR / data_dir_name
    data_dir.mkdir(parents=True, exist_ok=True)
    torrc_path = TOR_DATA_DIR / f"torrc_{data_dir_name}"
    torrc_content = f"SocksPort {socks_port}\nControlPort {control_port}\nDataDirectory {data_dir}\nRunAsDaemon 0\n"
    try:
        with open(torrc_path, "w") as f:
            f.write(torrc_content)
    except OSError:
        return None
    try:
        proc = subprocess.Popen([TOR_BIN, "-f", str(torrc_path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        for _ in range(20):
            if _tor_esta_activo(socks_port):
                time.sleep(1)
                return proc
            time.sleep(0.5)
        try:
            proc.kill()
            proc.wait(timeout=3)
        except Exception:
            pass
        return None
    except Exception:
        return None

def verificar_tor(puerto_socks: int = 9050) -> bool:
    return _tor_esta_activo(puerto_socks)

def rotar_tor(indice: int = 0) -> bool:
    try:
        from core.tor_utils import rotar_tor as _rotar_utils
        result = _rotar_utils()
        if result:
            return True
    except Exception:
        pass
    socks_port, control_port = IDENTIDADES_TOR[indice]
    estado = _leer_estado()
    circuito_key = f"circuito_{socks_port}"
    circuito_info = estado.get("circuitos", {}).get(circuito_key, {})
    pid = circuito_info.get("proceso_pid")
    if pid is not None:
        try:
            os.kill(pid, _sig.SIGTERM)
            time.sleep(2)
        except Exception:
            pass
    data_dir_name = f"tor_{socks_port}"
    proc = _iniciar_tor(socks_port, control_port, data_dir_name)
    if proc is not None:
        time.sleep(STARTUP_WAIT)
        if _tor_esta_activo(socks_port):
            estado.setdefault("circuitos", {})[circuito_key] = {"idx": indice, "socks_port": socks_port, "control_port": control_port, "proceso_pid": proc.pid, "bloqueado_desdes": None}
            _guardar_estado(estado)
            return True
    marcar_circuito_bloqueado(indice)
    return False

def marcar_circuito_bloqueado(indice: int) -> None:
    estado = _leer_estado()
    socks_port, control_port = IDENTIDADES_TOR[indice]
    circuito_key = f"circuito_{socks_port}"
    estado.setdefault("circuitos", {})[circuito_key] = {"idx": indice, "socks_port": socks_port, "control_port": control_port, "bloqueado_desdes": time.time()}
    _guardar_estado(estado)

def obtener_siguiente_identidad() -> int:
    """Devuelve el índice del circuito Tor menos usado/no bloqueado.

    SOLO devuelve circuitos cuyo puerto SOCKS está realmente escuchando.
    Si ninguno de los circuitos del pool responde, intenta iniciar el pool;
    si aún así no hay circuito activo, devuelve el índice del primer circuito
    (la verificación de puerto se hace en el caller antes de usarlo).
    """
    estado = _leer_estado()
    circuitos = estado.get("circuitos", {})
    ahora = time.time()
    candidatos_activos = []
    for clave, info in circuitos.items():
        idx = info.get("idx")
        if idx is None:
            continue
        bloqueado_desde = info.get("bloqueado_desdes")
        if bloqueado_desde is None or (ahora - bloqueado_desde) > CIRCUIT_COOLDOWN:
            socks_port = IDENTIDADES_TOR[idx][0]
            if _tor_esta_activo(socks_port):
                candidatos_activos.append(idx)
    if candidatos_activos:
        return random.choice(candidatos_activos)
    # Ninguno de los circuitos registrados responde: probar los tres puertos
    # y quedarse con los que sí escuchan.
    puertos_activos = [
        i for i, (socks_port, _control_port) in enumerate(IDENTIDADES_TOR)
        if _tor_esta_activo(socks_port)
    ]
    if puertos_activos:
        return random.choice(puertos_activos)
    # Intentar levantar el pool por si hay circuitos apagados
    try:
        iniciar_pool()
    except Exception:
        pass
    puertos_activos = [
        i for i, (socks_port, _control_port) in enumerate(IDENTIDADES_TOR)
        if _tor_esta_activo(socks_port)
    ]
    if puertos_activos:
        return random.choice(puertos_activos)
    return 0

def obtener_sesion_tor(indice: Optional[int] = None) -> Optional["requests.Session"]:
    import requests
    # Proxy residencial de terceros (NUNCA la IP del usuario): si está
    # configurado, se usa y no requiere Tor.
    from core.http_client import get_active_proxies as _gap, _load_proxy_url as _lp
    if _lp():
        s = requests.Session()
        s.proxies.update(_gap())
        s.headers.update({"User-Agent": random.choice([
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/15.3 Safari/605.1.15",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/110.0.0.0 Safari/537.36",
        ])})
        return s
    if not _tor_semaphore:
        iniciar_pool()
        if not _tor_semaphore:
            return None
    estado = _leer_estado()
    if indice is None:
        indice = obtener_siguiente_identidad()
    socks_port, control_port = IDENTIDADES_TOR[indice]
    if not _tor_esta_activo(socks_port):
        marcar_circuito_bloqueado(indice)
        return None
    session = requests.Session()
    proxy_url = f"socks5h://127.0.0.1:{socks_port}"
    session.proxies.update({"http": proxy_url, "https": proxy_url})
    session.headers.update({"User-Agent": random.choice(["Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36", "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36", "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"])})
    circuito_key = f"circuito_{socks_port}"
    estado.setdefault("circuitos", {}).setdefault(circuito_key, {})
    estado["circuitos"][circuito_key].setdefault("ultimo_uso", time.time())
    estado["circuitos"][circuito_key]["bloqueado_desdes"] = None
    _guardar_estado(estado)
    return session

def iniciar_pool() -> bool:
    global _tor_semaphore
    _tor_semaphore = threading.Semaphore(1)
    estado = _leer_estado()
    proxies_levantados = []
    for idx, (socks_port, control_port) in enumerate(IDENTIDADES_TOR):
        circuito_key = f"circuito_{socks_port}"
        if circuito_key in estado:
            bloqueado_desde = estado["circuitos"].get(circuito_key, {}).get("bloqueado_desdes")
            if bloqueado_desde and time.time() - bloqueado_desdes < CIRCUIT_COOLDOWN:
                continue
        if _tor_esta_activo(socks_port):
            proxies_levantados.append(idx)
            continue
        if socks_port != 9050:
            data_dir_name = f"tor_{socks_port}"
            proc = _iniciar_tor(socks_port, control_port, data_dir_name)
            if proc is not None:
                time.sleep(2)
                if _tor_esta_activo(socks_port):
                    proxies_levantados.append(idx)
                    estado.setdefault("circuitos", {})[circuito_key] = {"idx": idx, "socks_port": socks_port, "control_port": control_port, "proceso_pid": proc.pid, "bloqueado_desdes": None}
                    _guardar_estado(estado)
    if proxies_levantados:
        _guardar_estado(estado)
    return len(proxies_levantados) > 0

if __name__ == "__main__":
    print(f"Tor verificado (9050): {verificar_tor()}")
    pool_ok = iniciar_pool()
    print(f"Pool iniciado: {pool_ok}")
    if pool_ok:
        s = obtener_sesion_tor(0)
        if s:
            print(f"Sesión proxies: {s.proxies}")
            print(f"User-Agent: {s.headers['User-Agent'][:50]}")
