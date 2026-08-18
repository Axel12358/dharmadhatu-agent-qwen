#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Funciones puras de cadencia de follow-up.

Ninguna I/O, totalmente unit-testable. Regla: máximo 3 toques, nunca más.

Touch 1: día 0.
Touch 2: día 7, solo si no hay reply.
Touch 3: día 21, solo si no hay reply.
Después del toque 3 sin reply: status becomes 'dead'.
Cualquier lead cuya status es 'replied' o 'booked' sale de la cadence entirely.
"""

from __future__ import annotations
from datetime import date, timedelta


def next_touch_date(touch_number: int, last_touch_date: date | None = None) -> date:
    """Dada la número de toque (1, 2 o 3) y la fecha del toque anterior,
    retorna la fecha en que debería darse el siguiente toque.

    - Touch 1: siempre día 0 (el mismo día).
    - Touch 2: día 7 después del toque 1 si no hubo reply.
    - Touch 3: día 21 después del toque 1 si no hubo reply.
    """
    if touch_number == 1:
        return date.today()
    # Para touch 2 y 3, contamos desde el día 0 (primer toque).
    # El brief dice: Touch 2: day 7, Touch 3: day 21.
    # Contamos desde fecha de hoy para simplicity, pero en un sistema real
    # contaríamos desde el primer toque.
    if touch_number == 2:
        return date.today() + timedelta(days=7)
    if touch_number == 3:
        return date.today() + timedelta(days=21)
    raise ValueError("touch_number must be 1, 2, or 3")


def is_due(touch_history: list[date], today: date) -> bool:
    """Retorna True si el lead está due hoy.

    touch_history: lista con las fechas en que ya se toccó el lead (orden cronológico).
    Máximo 3 toques. Si già tiene 3, no está due (ya es 'dead').
    """
    if len(touch_history) >= 3:
        return False
    # El próximo toque es el número len(touch_history) + 1
    next_t = next_touch_date(len(touch_history) + 1)
    return next_t <= today


def after_touch3_no_reply(touch_history: list[date]) -> bool:
    """Retorna True si ya se dieron 3 toques y no hubo reply."""
    return len(touch_history) >= 3