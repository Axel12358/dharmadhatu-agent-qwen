#!/usr/bin/env python3
"""
Generador de fingerprints realistas usando undetectable-fingerprint-browser.
"""

import random

# User-agents extensos
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/119.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 Safari/605.1.15",
]

def get_random_ua():
    return random.choice(USER_AGENTS)

def get_random_viewport():
    return {"width": random.randint(1280, 1920), "height": random.randint(720, 1080)}

def get_random_locale():
    return random.choice(["es-ES", "en-US", "de-DE", "fr-FR", "it-IT", "pt-PT"])

def get_random_timezone():
    return random.choice(["Europe/Madrid", "Europe/Berlin", "Europe/London", "Europe/Paris", "America/New_York"])

def generate_profile():
    """Genera un perfil de navegación con fingerprint realista."""
    return {
        "user_agent": get_random_ua(),
        "viewport": get_random_viewport(),
        "locale": get_random_locale(),
        "timezone": get_random_timezone(),
    }
