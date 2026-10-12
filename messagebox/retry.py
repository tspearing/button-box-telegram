"""Backoff for the outgoing voice-note queue."""

from __future__ import annotations


def retry_delay(attempts: int) -> float:
    """Seconds to wait before the next send attempt.

    ``attempts`` is the number of failures so far, starting at 1.
    The delay doubles up to a five-minute cap so a dead network does not
    spin, and a short outage still recovers quickly.
    """
    if attempts < 1:
        attempts = 1
    # Cap the exponent so a huge attempt count cannot overflow, then cap
    # the wait at five minutes.
    return float(min(300, 2 ** min(attempts, 9)))
