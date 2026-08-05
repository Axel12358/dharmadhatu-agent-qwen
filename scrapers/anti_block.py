#!/usr/bin/env python3
"""
Manejo de bloqueos, rate limiting y rotación de proxies/user-agents.
Estrategias:
  - Rotación de proxies (Tor, HTTP, SOCKS5)
  - Rate limiting por dominio
  - Exponential backoff en retries
  - Rotación de user-agents y fingerprints
  - Detección de CAPTCHA/bloqueo
"""

import random
import time
import asyncio
import json
import logging
import os
import socket
import subprocess
import sys
from pathlib import Path
from collections import defaultdict
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

_PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

USER_AGENTS = [
    # Chrome Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 11.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0",
    # Chrome macOS
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0",
    # Chrome Linux
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36",
    # Firefox Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/115.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/119.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/121.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/124.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/128.0",
    # Firefox macOS
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:109.0) Gecko/20100101 Firefox/119.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:109.0) Gecko/20100101 Firefox/122.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:109.0) Gecko/20100101 Firefox/125.0",
    # Firefox Linux
    "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:109.0) Gecko/20100101 Firefox/119.0",
    "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:109.0) Gecko/20100101 Firefox/122.0",
    "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:109.0) Gecko/20100101 Firefox/125.0",
    # Safari macOS
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/15.6.1 Safari/605.1.15",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Safari/605.1.15",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 Safari/605.1.15",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15",
    # Edge Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36 Edg/122.0.0.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36 Edg/123.0.0.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 Edg/124.0.0.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36 Edg/125.0.0.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36 Edg/126.0.0.0",
    # Opera
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 OPR/106.0.0.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36 OPR/107.0.0.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36 OPR/109.0.0.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36 OPR/108.0.0.0",
    # Safari iOS
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (iPad; CPU OS 17_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 Mobile/15E148 Safari/604.1",
    # Chrome Android
    "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (Linux; Android 13; SM-G998B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (Linux; Android 14; Pixel 7 Pro) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Mobile Safari/537.36",
]

PROXY_FILE = str(Path(_PROJECT_ROOT) / "proxies.txt")
PROXY_CACHE_FILE = str(Path(_PROJECT_ROOT) / "proxies_cache.json")

TOR_SOCKS_PORT = 9050
TOR_DATA_DIR = str(Path(_PROJECT_ROOT) / "tor_data")  # writable por el usuario
_TOR_PROC = None

PROXY_FILE = str(Path(_PROJECT_ROOT) / "proxies.txt")
PROXY_CACHE_FILE = str(Path(_PROJECT_ROOT) / "proxies_cache.json")


class AntiBlock:
    def __init__(self):
        self.domain_stats = defaultdict(lambda: {
            "requests": 0,
            "last_request": 0,
            "errors": 0,
            "blocked": False,
        })
        self.proxies = self._load_proxies()
        self.proxy_failures = defaultdict(int)
        self.current_proxy_index = 0

    # ------------------------------------------------------------------ #
    # PROXIES
    # ------------------------------------------------------------------ #
    def _load_proxies(self):
        proxies = []

        if Path(PROXY_CACHE_FILE).exists():
            try:
                with open(PROXY_CACHE_FILE) as f:
                    cached = json.load(f)
                    if isinstance(cached, list):
                        proxies = cached
            except (json.JSONDecodeError, IOError):
                pass

        if Path(PROXY_FILE).exists():
            try:
                with open(PROXY_FILE) as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith("#"):
                            proxies.append(line)
            except IOError:
                pass

        return proxies

    def _save_proxies(self):
        try:
            with open(PROXY_CACHE_FILE, "w") as f:
                json.dump(self.proxies, f, indent=2)
        except IOError:
            pass

    def add_proxy(self, proxy):
        if proxy not in self.proxies:
            self.proxies.append(proxy)
            self._save_proxies()

    def get_proxy(self):
        if not self.proxies:
            return None

        valid = [p for p in self.proxies if self.proxy_failures.get(p, 0) < 3]
        if not valid:
            self.proxy_failures.clear()
            valid = self.proxies

        proxy = random.choice(valid)
        return proxy

    def mark_proxy_failed(self, proxy):
        self.proxy_failures[proxy] += 1
        if self.proxy_failures[proxy] >= 3:
            self.proxies = [p for p in self.proxies if p != proxy]
            self._save_proxies()

    def fetch_free_proxies(self):
        try:
            import requests
            from bs4 import BeautifulSoup
            urls = [
                "https://free-proxy-list.net/",
                "https://www.sslproxies.org/",
            ]
            for url in urls:
                try:
                    resp = requests.get(url, timeout=10)
                    soup = BeautifulSoup(resp.text, "html.parser")
                    table = soup.find("table")
                    if table:
                        for row in table.find_all("tr")[1:]:
                            cols = row.find_all("td")
                            if len(cols) >= 2:
                                ip = cols[0].text.strip()
                                port = cols[1].text.strip()
                                self.add_proxy(f"http://{ip}:{port}")
                except Exception:
                    continue
        except ImportError:
            pass

    # ------------------------------------------------------------------ #
    # TOR (socks5 local, arranque perezoso / proceso hijo reutilizable)
    # ------------------------------------------------------------------ #
    def _socks_open(self):
        """True si el socks 9050 responde."""
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(2)
            s.connect(("127.0.0.1", TOR_SOCKS_PORT))
            s.close()
            return True
        except OSError:
            return False

    def _ensure_tor(self):
        """Arranca Tor como proceso hijo si el socks 9050 no responde."""
        global _TOR_PROC
        if self._socks_open():
            return _TOR_PROC
        # No reiniciamos si ya lo lanzamos y murió.
        if _TOR_PROC is not None and _TOR_PROC.poll() is not None:
            _TOR_PROC = None
        try:
            os.makedirs(TOR_DATA_DIR, exist_ok=True)
            cfg_path = str(Path(TOR_DATA_DIR) / "torrc")
            with open(cfg_path, "w") as f:
                f.write(
                    f"SocksPort {TOR_SOCKS_PORT}\n"
                    f"DataDirectory {TOR_DATA_DIR}\n"
                    "RunAsDaemon 0\n"
                )
            _TOR_PROC = subprocess.Popen(
                ["tor", "-f", cfg_path],
                stdout=open(str(Path(TOR_DATA_DIR) / "tor.log"), "w"),
                stderr=subprocess.STDOUT,
            )
            # Esperar a que el socks abra (máx 25s).
            for _ in range(25):
                if self._socks_open():
                    return _TOR_PROC
                time.sleep(1)
        except Exception as e:
            logger.debug(f"Tor no disponible: {e}")
        return None

    def tor_active(self):
        """True si Tor (socks 9050) está activo."""
        return self._socks_open()

    def get_socks_proxy(self):
        """Devuelve el proxy socks5 de Tor o None si Tor está offline."""
        if not self.tor_active():
            return None
        return f"socks5://127.0.0.1:{TOR_SOCKS_PORT}"

    def requests_proxies(self):
        """Dict de proxies para requests, usando Tor si está activo."""
        p = self.get_socks_proxy()
        if p is None:
            return None
        return {"http": p, "https": p}

    def fetch_free_proxies(self):
        try:
            import requests
            from bs4 import BeautifulSoup
            urls = [
                "https://free-proxy-list.net/",
                "https://www.sslproxies.org/",
            ]
            for url in urls:
                try:
                    resp = requests.get(url, timeout=10)
                    soup = BeautifulSoup(resp.text, "html.parser")
                    table = soup.find("table")
                    if table:
                        for row in table.find_all("tr")[1:]:
                            cols = row.find_all("td")
                            if len(cols) >= 2:
                                ip = cols[0].text.strip()
                                port = cols[1].text.strip()
                                self.add_proxy(f"http://{ip}:{port}")
                except Exception:
                    continue
        except ImportError:
            pass

    # ------------------------------------------------------------------ #
    # RATE LIMITING
    # ------------------------------------------------------------------ #
    def wait_if_needed(self, domain):
        stats = self.domain_stats[domain]
        now = time.time()

        min_interval = 3.0
        if stats["blocked"]:
            min_interval = 60.0

        elapsed = now - stats["last_request"]
        if elapsed < min_interval:
            sleep_time = min_interval - elapsed + random.uniform(0.5, 1.5)
            time.sleep(sleep_time)

        stats["requests"] += 1
        stats["last_request"] = time.time()

    def mark_blocked(self, domain):
        stats = self.domain_stats[domain]
        stats["blocked"] = True
        stats["errors"] += 1

    def mark_success(self, domain):
        stats = self.domain_stats[domain]
        if stats["errors"] > 0:
            stats["errors"] = max(0, stats["errors"] - 1)
        if stats["errors"] == 0:
            stats["blocked"] = False

    # ------------------------------------------------------------------ #
    # EXPONENTIAL BACKOFF
    # ------------------------------------------------------------------ #
    def backoff(self, domain, attempt):
        if attempt == 0:
            return
        delay = min(2 ** attempt + random.uniform(0, 1), 120)
        logger.info(f"⏳ Backoff {domain}: esperando {delay:.1f}s (intento {attempt})")
        time.sleep(delay)

    # ------------------------------------------------------------------ #
    # PLAYWRIGHT STEALTH
    # ------------------------------------------------------------------ #
    async def apply_playwright_stealth(self, page):
        try:
            await page.evaluate("""
                () => {
                    Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
                    Object.defineProperty(navigator, 'plugins', { get: () => [1,2,3,4,5] });
                    Object.defineProperty(navigator, 'languages', { get: () => ['es-ES', 'es', 'en-US', 'en'] });
                    Object.defineProperty(navigator, 'hardwareConcurrency', { get: () => 8 });
                    Object.defineProperty(navigator, 'deviceMemory', { get: () => 8 });
                    Object.defineProperty(navigator, 'maxTouchPoints', { get: () => 1 });
                }
            """)
        except Exception:
            pass

    def random_user_agent(self):
        return random.choice(USER_AGENTS)

    # ------------------------------------------------------------------ #
    # DETECCIÓN DE BLOQUEO
    # ------------------------------------------------------------------ #
    def detect_block(self, html, url=""):
        indicators = [
            "navigator.webdriver",
            "please show you're human",
            "please confirm you are human",
            "our systems have detected unusual traffic",
            "enter the code you see",
            "captcha",
            "too many requests",
            "your request has been blocked",
            "access denied",
            "sorry, we can't access",
            "facebook.com/checkpoint",
            "login.php",
        ]
        html_lower = html.lower()
        for ind in indicators:
            if ind in html_lower:
                return True
        return False

    # ------------------------------------------------------------------ #
    # CONTEXT MANAGER PARA PLAYWRIGHT
    # ------------------------------------------------------------------ #
    async def create_stealth_context(self, browser, use_tor=True):
        """Crea un contexto Playwright con stealth. Si use_tor y Tor activo, enruta por socks5."""
        self._ensure_tor() if use_tor else None
        kwargs = dict(
            user_agent=self.random_user_agent(),
            viewport={"width": random.choice([1280, 1366, 1440, 1920]),
                       "height": random.choice([720, 768, 900, 1080])},
            locale="es-ES",
            timezone_id="Europe/Madrid",
        )
        if use_tor and self.get_socks_proxy():
            kwargs["proxy"] = {"server": self.get_socks_proxy()}
        context = await browser.new_context(**kwargs)
        return context


# Singleton
_anti_block_instance = None


def get_anti_block():
    global _anti_block_instance
    if _anti_block_instance is None:
        _anti_block_instance = AntiBlock()
    return _anti_block_instance
