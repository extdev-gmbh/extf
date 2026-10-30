"""What the reader and the writer share: the constants of the format and the amount range."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Context, Decimal

#: The encoding of the DATEV format.
DATEV_ENCODING = "cp1252"
BOOKINGS, ACCOUNT_LABELS, PERSONAL_ACCOUNTS = 21, 20, 16
CATEGORY_NAMES = {BOOKINGS: "Buchungsstapel", ACCOUNT_LABELS: "Kontenbeschriftungen", PERSONAL_ACCOUNTS: "Debitoren/Kreditoren"}
#: The length of a general ledger account number a header may state (Sachkontenlänge).
ACCOUNT_LENGTHS = (4, 8)
#: The length the CSV lists are read with unless the caller says otherwise: they have no header.
DEFAULT_ACCOUNT_LENGTH = 4

#: The arithmetic of amounts, independent of the caller's decimal context.
MONEY_CONTEXT = Context(prec=40, rounding=ROUND_HALF_UP)
CENT = Decimal("0.01")
#: The range of an amount read: its absolute value lies below this.
AMOUNT_LIMIT = Decimal(10) ** 14
#: The correction digit (Berichtigungsschlüssel) of a two-digit BU-Schlüssel that marks a
#: Generalumkehr: ``29`` is the cancellation of an entry with tax key 9. Unproven: the
#: exports seen so far mark a Generalumkehr in its column.
REVERSAL_CORRECTION_DIGIT = "2"


def round_money(value: Decimal) -> Decimal:
    """``value`` rounded commercially (half up) to cents."""
    return value.quantize(CENT, context=MONEY_CONTEXT)


def is_reversal_key(key: str) -> bool:
    """Whether the BU-Schlüssel ``key`` is the old notation of a Generalumkehr. The reader
    takes such a key as one, so the writer refuses it: it would not come back as written."""
    return len(key) == 2 and key[0] == REVERSAL_CORRECTION_DIGIT


def is_utf8(data: bytes) -> bool:
    """Whether ``data`` is UTF-8 beyond ASCII: as Windows-1252 it would read as mojibake. The
    reader refuses such a file, so the writer refuses to write one."""
    if data.isascii():
        return False
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return True


def fiscal_year_end(start: date) -> date:
    """The last day of the fiscal year that starts on ``start`` (one starting on 29.02
    ends on the last day of February of the next year).

    Raises :class:`ValueError` for a fiscal year that would end after year 9999, the end of
    ``date``: that is every start in year 9999 but 01.01."""
    if start.year == date.max.year:
        if (start.month, start.day) == (1, 1):
            return date.max
        raise ValueError(f"the fiscal year starting on {start.isoformat()} would end after year {date.max.year}")
    try:
        following = date(start.year + 1, start.month, start.day)
    except ValueError:
        following = date(start.year + 1, 3, 1)
    return following - timedelta(days=1)
