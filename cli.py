from typing import Optional
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Composition root: une sources + ledger + cadence.

Usage examples (from repo root):
    python cli.py discover --city Berlin
    python cli.py due
    python cli.py touch <id> --channel email --note "..."
    python cli.py status <id> replied

Dependency rule (sección 4 del brief):
- sources/ sabe nada sobre ledger. ledger nada sabe sobre sources.
- cli.py importa los dos. Ése es el único punto de conexión.
"""

import argparse
import sys
from datetime import date
from typing import List

from sources.base import get_adapters, Lead, normalize_promoter_name
from core.ledger import Ledger


def discover(city: str = "Berlin", since: Optional[date] = None) -> int:
    """Descubre leads para la ciudad indicada y los agrega al ledger.

    Escribe al menos un lead y devuelve 0. Si ya existen (UNIQUE constraint),
    no duplica filas.
    """
    ledger = Ledger()
    since = since or date.today()
    adapters = get_adapters()
    total_new = 0

    for adapter in adapters:
        try:
            leads = adapter.fetch(city, since)
            for lead in leads:
                # Validación simple de id duplicado antes de insertar;
                # la UNIQUE constraint en SQLite también lo hace,
                # peronos ahorramos un query extra en el caso común.
                ledger.upsert_lead(lead)
                total_new += 1
        except Exception as e:
            # En producción logging, aquí solo print para el CLI.
            print(f"[warn] adapter {adapter.name} falló: {e}", file=sys.stderr)

    ledger.close()
    return total_new


def due() -> int:
    """Lista leads cuya next touch es hoy o antes, y los imprime.

    Formato de salida: promoter | event | city | contact_channel
    (contact_channel es email o url segú esté disponible).
    """
    ledger = Ledger()
    today_str = date.today().isoformat()
    due_leads = ledger.get_due(today_str)

    if not due_leads:
        print("Ningún lead due hoy.")
        ledger.close()
        return 0

    for lead in due_leads:
        promoter = lead.get("promoter", "")
        event = lead.get("event", "")
        city = lead.get("city", "")
        contact = lead.get("contact_url") or lead.get("contact_email", "")
        print(f"{promoter} | {event} | {city} | {contact}")

    ledger.close()
    return len(due_leads)


def touch(lead_id: int, channel: str, note: Optional[str] = None) -> int:
    """Registra un toque a un lead y lo remueve del output de 'due'.

    Returns 0 on success.
    """
    ledger = Ledger()
    ledger.record_touch(lead_id, channel, note)
    ledger.close()
    return 0


def status(lead_id: int, action: str) -> int:
    """Marca un lead como 'replied' (sale de la cadence permanently)
    o 'booked'.

    Actions: 'replied', 'booked'
    """
    ledger = Ledger()
    if action == "replied":
        ledger.mark_replied(lead_id)
    elif action == "booked":
        ledger.mark_booked(lead_id)
    else:
        print(f"Action '{action}' no reconocida.", file=sys.stderr)
        ledger.close()
        return 1
    ledger.close()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Booking Pipeline CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # discover
    disc_parser = subparsers.add_parser("discover")
    disc_parser.add_argument("--city", default="Berlin", help="Ciudad para descubrir leads")
    disc_parser.add_argument("--since", default=None,
                             help="Fecha desde (yyyy-mm-dd); default: hoy")

    # due
    due_parser = subparsers.add_parser("due")
    # Ningún argumento extra necesario

    # touch
    touch_parser = subparsers.add_parser("touch")
    touch_parser.add_argument("id", type=int, help="ID del lead")
    touch_parser.add_argument("--channel", required=True, help="Canal (email, sms, phone)")
    touch_parser.add_argument("--note", default=None, help="Nota libre")

    # status
    status_parser = subparsers.add_parser("status")
    status_parser.add_argument("id", type=int, help="ID del lead")
    status_parser.add_argument("action", choices=["replied", "booked"],
                               help="Cambiar status del lead")

    args = parser.parse_args()

    if args.command == "discover":
        n = discover(args.city, args.since)
        print(f"Descubrió {n} lead(s).")
        return 0

    if args.command == "due":
        return due()

    if args.command == "touch":
        return touch(args.id, args.channel, args.note)

    if args.command == "status":
        return status(args.id, args.action)

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())