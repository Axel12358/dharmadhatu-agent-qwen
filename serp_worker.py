"""Proceso hijo: búsqueda SERP (DuckDuckGo html) con timeout HARD.

Corre con el python del venv. Sale por Tor (nunca IP real) via
core.http_client.get_html. Devuelve JSON con snippet results.
"""
import sys
import json
import re
import urllib.parse
from pathlib import Path

_PROJECT_ROOT = str(Path(__file__).resolve().parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from core.http_client import get_html


def search(query: str, timeout: int = 20) -> dict:
    try:
        url = "https://html.duckduckgo.com/html/?q=" + urllib.parse.quote(query)
        html = get_html(url, timeout=timeout)
        if not html:
            return {"status": 0, "results": [], "error": "empty"}
        if len(html) < 500:
            return {"status": 0, "results": [], "error": "short"}
        bloqueado = re.search(
            r"anomaly|unusual traffic|captcha|cannot verify|blocked", html, re.I
        )
        if bloqueado and "result__a" not in html:
            return {"status": 403, "results": [], "error": "blocked"}
        results = []
        for m in re.finditer(
            r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>'
            r'.*?class="result__snippet"[^>]*>(.*?)</div>',
            html, re.S,
        ):
            href, titulo, snip = m.group(1), m.group(2), m.group(3)
            titulo = re.sub(r"<[^>]+>", "", titulo).strip()
            snip = re.sub(r"<[^>]+>", "", snip).strip()
            dec = urllib.parse.unquote(href)
            mm = re.search(r"uddg=([^&]+)", dec)
            if mm:
                dec = urllib.parse.unquote(mm.group(1))
            results.append({"url": dec, "titulo": titulo, "snippet": snip})
        if not results and "result__a" not in html:
            return {"status": 0, "results": [], "error": "no-results"}
        return {"status": 200, "results": results, "error": ""}
    except Exception as e:
        return {"status": 0, "results": [], "error": str(e)[:200]}


if __name__ == "__main__":
    query = sys.argv[1]
    timeout = int(sys.argv[2]) if len(sys.argv) > 2 else 20
    print(json.dumps(search(query, timeout)))