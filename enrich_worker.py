"""Proceso hijo de fetch con timeout HARD (subprocess + SIGKILL).

Corre con el python del venv del proyecto. Usa el cliente Tor del proyecto
(core.http_client) para garantizar que NUNCA se expone IP real.
"""
import sys
import json
import os
from pathlib import Path

_ROOT = Path("/Users/angelgarcia/dharmadhatu_agent_qwen")
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from core.http_client import get_html


def fetch(url: str, timeout: int = 15) -> dict:
    """Descarga el HTML vía el cliente Tor del proyecto (socks5h)."""
    try:
        html = get_html(url, timeout=timeout)
        if html:
            return {"status": 200, "html": html[:50000], "error": ""}
        return {"status": 0, "html": "", "error": "empty"}
    except Exception as e:
        return {"status": 0, "html": "", "error": str(e)[:200]}


if __name__ == "__main__":
    url = sys.argv[1]
    timeout = int(sys.argv[2]) if len(sys.argv) > 2 else 15
    result = fetch(url, timeout)
    print(json.dumps(result))