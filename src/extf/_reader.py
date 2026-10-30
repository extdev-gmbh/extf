"""Readers of files in the DATEV format (EXTF) and of the CSV lists that come with them.

**DATEV format** (``EXTF``, format version 700): Windows-1252, fields separated by ``;``,
text in double quotes (``""`` inside a text is one quote), amounts with a decimal comma.
Line 1 is the header (category, fiscal year start, length of the general ledger account
numbers, period), line 2 names the columns, every further line is a record. Three
categories are read:

* ``21`` Buchungsstapel — booking entries (:func:`read_bookings`),
* ``20`` Kontenbeschriftungen — the labels of the general ledger accounts (:func:`read_account_labels`),
* ``16`` Debitoren/Kreditoren — the personal accounts (:func:`read_personal_accounts`;
  the list Kanzlei-Rechnungswesen exports is read as well).

Columns are found by their name (DATEV writes the full list; only the ones needed here
are read), so the order of the columns does not matter.

**CSV lists** (``;``, decimal comma, UTF-8 or Windows-1252): the trial balance
(Summen- und Saldenliste, :func:`read_trial_balance`), the personal accounts as
Kanzlei-Rechnungswesen lists them (Debitoren-/Kreditorenstammdaten) and the fixed-asset register
(Anlagenverzeichnis, :func:`read_fixed_assets`). The header row is the first row that
names the column ``Konto``; lines before it (a title, the period) are skipped, within
:data:`MAX_HEADER_SEARCH` characters.

Every reader is strict: a file with a single bad line is refused as a whole, with every
bad line named (:class:`DatevFormatError`); reading stops at the 200th. Nothing is guessed.

**Input is hostile.** :data:`MAX_INPUT_BYTES`, :data:`MAX_LINES`, :data:`MAX_COLUMNS` and
:data:`MAX_FIELD_LENGTH` bound what is read at all, :data:`MAX_HEADER_SEARCH` what is searched
for the header row of a CSV list; nothing from the input is evaluated. On any bytes a reader
returns or raises :class:`DatevFormatError`.
"""

from __future__ import annotations

import csv
import io
import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from extf._errors import DatevFormatError, LineError
from extf._format import (
    ACCOUNT_LABELS,
    ACCOUNT_LENGTHS,
    AMOUNT_LIMIT,
    BOOKINGS,
    CATEGORY_NAMES,
    DATEV_ENCODING,
    DEFAULT_ACCOUNT_LENGTH,
    MONEY_CONTEXT,
    PERSONAL_ACCOUNTS,
    fiscal_year_end,
    is_reversal_key,
    is_utf8,
)

#: The size of an input in bytes above which it is refused unread.
MAX_INPUT_BYTES = 64 * 1024 * 1024
#: The lines, the columns of a line and the characters of a field a file may have.
MAX_LINES = 1_000_000
MAX_COLUMNS = 512
MAX_FIELD_LENGTH = 4096
#: The characters of a CSV list searched for its header row, the header row included. A
#: separator and a line end count as one each, so that empty cells use up the search as well.
MAX_HEADER_SEARCH = 1024 * 1024
#: The format versions read (the header's second field).
FORMAT_VERSIONS = (500, 700)
_MAX_ERRORS = 200
#: The characters of the longest row that passes both limits: every cell quoted, every character
#: of it a doubled quote, the separators and a line end of two. A longer row breaks one of them.
_MAX_ROW_CHARS = MAX_COLUMNS * (2 * MAX_FIELD_LENGTH + 3) + 1
_NO_LIST_HEADER = "no header row with the column Konto"

_AMOUNT = re.compile(r"^[0-9]{1,3}(?:\.?[0-9]{3})*(?:,[0-9]{1,2})?$")
_SIGNED_AMOUNT = re.compile(r"^-?[0-9]{1,3}(?:\.?[0-9]{3})*(?:,[0-9]{1,2})?$")
_DIGITS = re.compile(r"^[0-9]+$")


@dataclass(frozen=True, slots=True)
class Header:
    """Line 1 of a file in the DATEV format."""

    category: int
    format_version: int
    #: The first day of the fiscal year (WJ-Beginn).
    fiscal_year_start: date
    #: The length of a general ledger account number (Sachkontenlänge).
    account_length: int
    #: The period of a Buchungsstapel (Datum vom / Datum bis); ``None`` for the other categories.
    date_from: date | None
    date_to: date | None
    #: The Buchungsstapel is locked (Festschreibung ``1``).
    locked: bool

    @property
    def fiscal_year(self) -> int:
        return self.fiscal_year_start.year


@dataclass(frozen=True, slots=True)
class Booking:
    """One record of a Buchungsstapel, with the sides resolved: ``amount`` (euros) debited
    to ``debit_account`` and credited to ``credit_account``."""

    file: str
    line: int
    amount: Decimal
    debit_account: str
    credit_account: str
    #: The account the tax key belongs to as written: ``Gegenkonto``.
    contra_account: str
    #: BU-Schlüssel as written (``None``: none).
    tax_key: str | None
    booking_date: date
    #: Belegfeld 1.
    document_number: str | None
    #: Belegfeld 2.
    document_number_2: str | None
    text: str | None
    currency: str
    foreign_amount: Decimal | None
    fx_rate: str | None
    #: Buchungs GUID, if DATEV wrote one: the entry's identity across deliveries.
    guid: str | None
    #: Festschreibung of the record (``None``: not stated).
    locked: bool | None
    #: Generalumkehr: the record cancels an entry. A record marked in the column
    #: ``Generalumkehr (GU)`` carries its effective side in the export already; the sides
    #: above are as written (see :func:`read_bookings`).
    reversal: bool = False


@dataclass(frozen=True, slots=True)
class BookingFile:
    header: Header
    bookings: list[Booking]


@dataclass(frozen=True, slots=True)
class AccountLabel:
    line: int
    number: str
    label: str


@dataclass(frozen=True, slots=True)
class PersonalAccount:
    line: int
    number: str
    name: str
    #: The VAT id (country code and number, no blanks), if stated.
    vat_id: str | None
    #: The advisor's customer or supplier number of the account (Kunden-/Lief.-Nr.), if stated.
    customer_number: str | None = None


@dataclass(frozen=True, slots=True)
class Balance:
    """One line of a trial balance: the balance of ``account``, debit positive."""

    line: int
    account: str
    label: str | None
    #: The balance at the end of the period, the opening balance included.
    balance: Decimal
    #: The opening balance (EB-Wert), debit positive, if the list states it. Part of
    #: ``balance`` already: the opening entries are entries of the Buchungsstapel.
    opening: Decimal | None = None


@dataclass(frozen=True, slots=True)
class FixedAsset:
    line: int
    asset_number: str
    name: str
    account: str
    acquired_on: date
    acquisition_cost: Decimal
    #: Useful life in years (``None``: not stated, e.g. land).
    useful_life_years: int | None
    method: str | None
    #: The book value on the day of the register (``None``: not stated).
    book_value: Decimal | None


# --- low level ---------------------------------------------------------------------------


def _norm(name: str) -> str:
    """A column name as compared: lower case, umlauts spelled out, only letters and digits."""
    lowered = name.casefold().replace("ä", "ae").replace("ö", "oe").replace("ü", "ue").replace("ß", "ss")
    return re.sub(r"[^a-z0-9]", "", lowered).replace("adressatentyp", "adressattyp")


class _Errors:
    def __init__(self, file: str) -> None:
        self.file = file
        self.items: list[LineError] = []

    def add(self, line: int, message: str) -> None:
        """Note a bad line; the 200th ends the reading: the file is refused by then."""
        self.items.append(LineError(self.file, line, message))
        if len(self.items) >= _MAX_ERRORS:
            raise DatevFormatError(self.items) from None

    def raise_any(self) -> None:
        if self.items:
            raise DatevFormatError(self.items)


def _rows(text: str, file: str) -> Iterator[tuple[int, list[str]]]:
    """The rows of ``text`` with the number of the line each starts on."""
    start = 1
    characters = 0

    def lines() -> Iterator[str]:
        """The lines of ``text``, refusing a row that passes the bound before csv.reader parses it."""
        nonlocal characters
        for line in io.StringIO(text, newline=""):
            characters += len(line)
            if characters > _MAX_ROW_CHARS:
                message = f"the line has more than {MAX_COLUMNS} columns or a field of more than {MAX_FIELD_LENGTH} characters"
                raise DatevFormatError([LineError(file, start, message)])
            yield line

    reader = csv.reader(lines(), delimiter=";", quotechar='"', strict=True)
    try:
        for row in reader:
            if reader.line_num > MAX_LINES:
                raise DatevFormatError([LineError(file, start, f"the file has more than {MAX_LINES} lines")])
            if len(row) > MAX_COLUMNS:
                raise DatevFormatError([LineError(file, start, f"the line has more than {MAX_COLUMNS} columns")])
            if any(len(cell) > MAX_FIELD_LENGTH for cell in row):
                raise DatevFormatError([LineError(file, start, f"a field has more than {MAX_FIELD_LENGTH} characters")])
            yield start, row
            start = reader.line_num + 1
            characters = 0
    except csv.Error as exc:
        raise DatevFormatError([LineError(file, start, f"not readable as CSV: {exc}")]) from exc


def _records(rows: Iterator[tuple[int, list[str]]]) -> Iterator[tuple[int, list[str]]]:
    """The rows left in ``rows`` without the blank ones."""
    return ((line, row) for line, row in rows if any(cell.strip() for cell in row))


def _check_size(data: bytes, file: str) -> None:
    if len(data) > MAX_INPUT_BYTES:
        raise DatevFormatError([LineError(file, 1, f"the file has {len(data)} bytes, at most {MAX_INPUT_BYTES}")])


def _decode_datev(data: bytes, file: str) -> str:
    _check_size(data, file)
    if data.startswith(b"\xef\xbb\xbf"):
        raise DatevFormatError(
            [LineError(file, 1, "starts with a UTF-8 byte order mark: the DATEV format is Windows-1252; export it again")]
        )
    if is_utf8(data):
        raise DatevFormatError([LineError(file, 1, "is UTF-8: the DATEV format is Windows-1252; export it again")])
    try:
        return data.decode(DATEV_ENCODING)
    except UnicodeDecodeError as exc:
        line = data[: exc.start].count(b"\n") + 1
        raise DatevFormatError([LineError(file, line, "not Windows-1252, as the DATEV format is")]) from exc


def _decode_list(data: bytes) -> str:
    """A CSV list: UTF-8 (with or without BOM), else Windows-1252."""
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode(DATEV_ENCODING, errors="strict")


def parse_amount(value: str, *, signed: bool = False) -> Decimal:
    """``1234,56`` or ``1.234,56`` (``-`` in front if ``signed``) as a Decimal.

    :class:`ValueError` for anything else, and for an amount of 15 digits or more before
    the comma. Zero is never negative: ``-0,00`` is ``0.00``."""
    text = value.strip()
    if not (_SIGNED_AMOUNT if signed else _AMOUNT).match(text):
        raise ValueError(f"not an amount with a decimal comma: {value[:40]!r}")
    amount = Decimal(text.replace(".", "").replace(",", "."))
    if amount.copy_abs() >= AMOUNT_LIMIT:
        raise ValueError(f"amount out of range: {text[:40]}")
    return amount if amount else amount.copy_abs()


def _negated(amount: Decimal) -> Decimal:
    """``amount`` on the other side, exact in any decimal context, and zero stays unsigned."""
    return amount.copy_negate() if amount else amount


def _parse_day(value: str, what: str) -> date:
    text = value.strip()
    try:
        if re.fullmatch(r"[0-9]{8}", text):
            return date(int(text[:4]), int(text[4:6]), int(text[6:8]))
        if re.fullmatch(r"[0-9]{1,2}\.[0-9]{1,2}\.[0-9]{4}", text):
            day, month, year = (int(part) for part in text.split("."))
            return date(year, month, day)
    except ValueError:
        pass
    raise ValueError(f"{what} is no date: {value[:40]!r}")


def _account(value: str, length: int) -> str:
    """An account number padded to the length of the general ledger accounts."""
    text = value.strip()
    if not _DIGITS.match(text):
        raise ValueError(f"account {value[:40]!r} is no number")
    text = text.zfill(length)
    if len(text) > length + 1 or int(text) == 0:
        raise ValueError(f"account {value[:40]!r} is no account of {length} or {length + 1} digits")
    return text


def _cut(value: str | None, limit: int, what: str) -> str | None:
    if value is None or not value.strip():
        return None
    text = value.strip()
    if len(text) > limit:
        raise ValueError(f"{what} has more than {limit} characters")
    return text


# --- the DATEV format --------------------------------------------------------------------


def _number(text: str) -> int:
    """A number of the header, ``-1`` if it is none."""
    return int(text) if re.fullmatch(r"[0-9]{1,9}", text) else -1


def _header(row: list[str], file: str, category: int) -> Header:
    def field(index: int) -> str:
        return row[index].strip() if index < len(row) else ""

    marker = field(0)
    if marker not in ("EXTF", "DTVF"):
        raise DatevFormatError([LineError(file, 1, "no DATEV header: line 1 starts with neither EXTF nor DTVF")])
    errors = _Errors(file)
    if _number(field(2)) != category:
        name = CATEGORY_NAMES[category]
        errors.add(1, f"format category {field(2)[:40] or '(empty)'} ({field(3)[:40] or '?'}), expected {category} ({name})")
        errors.raise_any()
    format_version = _number(field(1))
    if not FORMAT_VERSIONS[0] <= format_version <= FORMAT_VERSIONS[1]:
        errors.add(1, f"format version {field(1)[:40] or '(empty)'} is not supported ({FORMAT_VERSIONS[0]} to {FORMAT_VERSIONS[1]} are)")
    fiscal_year_start = date(1, 1, 1)
    try:
        fiscal_year_start = _parse_day(field(12), "the start of the fiscal year (WJ-Beginn)")
    except ValueError as exc:
        errors.add(1, str(exc))
    account_length = _number(field(13))
    if not ACCOUNT_LENGTHS[0] <= account_length <= ACCOUNT_LENGTHS[1]:
        errors.add(
            1,
            f"general ledger accounts have {field(13)[:40] or '(no)'} digits (Sachkontenlänge); "
            f"{ACCOUNT_LENGTHS[0]} to {ACCOUNT_LENGTHS[1]} are possible",
        )
    date_from = date_to = None
    if category == BOOKINGS:
        try:
            date_from = _parse_day(field(14), "the first day (Datum vom)")
            date_to = _parse_day(field(15), "the last day (Datum bis)")
            if date_to < date_from:
                errors.add(1, "the period ends before it starts")
        except ValueError as exc:
            errors.add(1, str(exc))
    errors.raise_any()
    return Header(
        category=category,
        format_version=format_version,
        fiscal_year_start=fiscal_year_start,
        account_length=account_length,
        date_from=date_from,
        date_to=date_to,
        locked=field(20) == "1",
    )


class _Columns:
    """The columns of a file by their normalised name."""

    def __init__(self, names: list[str]) -> None:
        self.names = [_norm(name) for name in names]
        self.index: dict[str, int] = {}
        for position, name in enumerate(self.names):
            if name:
                self.index.setdefault(name, position)

    def find(self, *names: str) -> int | None:
        return next((self.index[_norm(name)] for name in names if _norm(name) in self.index), None)

    def marks(self, position: int | None) -> tuple[int, int] | None:
        """The columns ``S`` and ``H`` right after the amount column at ``position``, which
        mark the side of its amount (Kanzlei-Rechnungswesen: ``Saldo;S;H``); ``None``:
        there are none."""
        if position is None or self.names[position + 1 : position + 3] != ["s", "h"]:
            return None
        return position + 1, position + 2


def _datev_file(data: bytes, file: str, category: int) -> tuple[Header, _Columns, Iterator[tuple[int, list[str]]]]:
    rows = _rows(_decode_datev(data, file), file)
    first = next(rows, None)
    if first is None:
        raise DatevFormatError([LineError(file, 1, "the file is empty")])
    header = _header(first[1], file, category)
    second = next(rows, None)
    if second is None:
        raise DatevFormatError([LineError(file, 2, "line 2 (the column names) is missing")])
    return header, _Columns(second[1]), _records(rows)


def _require(columns: _Columns, file: str, wanted: dict[str, tuple[str, ...]]) -> dict[str, int]:
    found: dict[str, int] = {}
    missing = []
    for key, names in wanted.items():
        position = columns.find(*names)
        if position is None:
            missing.append(names[0])
        else:
            found[key] = position
    if missing:
        raise DatevFormatError([LineError(file, 2, f"column(s) missing: {', '.join(missing)}")])
    return found


def _cell(row: list[str], position: int | None) -> str:
    if position is None or position >= len(row):
        return ""
    return row[position].strip()


def _record_day(value: str, header: Header) -> date:
    """Belegdatum ``DDMM`` (or ``DMM``) in the fiscal year of the Buchungsstapel (WJ-Beginn
    and the twelve months after it), not only in its period: a late entry of an earlier
    month is booked in the batch of a later one. The year is the one that puts the day
    into the fiscal year — across the turn of the year of a fiscal year that is not the
    calendar year as well."""
    text = value.strip()
    if not re.fullmatch(r"[0-9]{3,4}", text):
        raise ValueError(f"Belegdatum {value!r} is not DDMM")
    text = text.zfill(4)
    start = header.fiscal_year_start
    end = fiscal_year_end(start)
    for year in (start.year, start.year + 1):
        try:
            day = date(year, int(text[2:]), int(text[:2]))
        except ValueError:
            continue
        if start <= day <= end:
            return day
    raise ValueError(f"Belegdatum {text} is no day of the fiscal year {start.isoformat()} – {end.isoformat()}")


def read_bookings(data: bytes, file: str = "Buchungsstapel") -> BookingFile:
    """A Buchungsstapel (category 21). ``S``: ``Konto`` is debited and ``Gegenkonto``
    credited; ``H`` the other way round. ``Umsatz`` is in ``WKZ Umsatz``; in another
    currency than euros, ``Basis-Umsatz`` holds the euro amount.

    A Generalumkehr cancels an entry. Marked in the column ``Generalumkehr (GU)`` = ``1``,
    the record carries its effective side already — DATEV exports the cancellation with
    the side it books — so the sides are read as written and ``reversal`` only marks it
    (read so, the cancelled entries come out at the balances of the trial balance;
    swapped again, they count twice). Marked the old way, by the correction digit ``2``
    in front of the tax key (e.g. ``29``), the sides are swapped and the tax key without
    the correction digit applies — unproven, the exports seen so far do not write it. The Belegdatum may be any day of the fiscal year (a late entry, see
    :func:`_record_day`). A record with a ``Skonto`` amount is refused as a bad line: DATEV
    splits the discount off automatically, which is not rebuilt here."""
    header, columns, records = _datev_file(data, file, BOOKINGS)
    need = _require(
        columns,
        file,
        {
            "amount": ("Umsatz (ohne Soll/Haben-Kz)", "Umsatz"),
            "side": ("Soll/Haben-Kennzeichen", "Soll-/Haben-Kennzeichen"),
            "account": ("Konto",),
            "contra": ("Gegenkonto (ohne BU-Schlüssel)", "Gegenkonto"),
            "day": ("Belegdatum",),
        },
    )
    optional = {
        "currency": columns.find("WKZ Umsatz"),
        "rate": columns.find("Kurs"),
        "base": columns.find("Basis-Umsatz"),
        "base_currency": columns.find("WKZ Basis-Umsatz"),
        "key": columns.find("BU-Schlüssel"),
        "number": columns.find("Belegfeld 1"),
        "number_2": columns.find("Belegfeld 2"),
        "text": columns.find("Buchungstext"),
        "locked": columns.find("Festschreibung"),
        "guid": columns.find("Buchungs GUID"),
        "reversal": columns.find("Generalumkehr (GU)", "Generalumkehr"),
        "discount": columns.find("Skonto"),
    }
    errors = _Errors(file)
    bookings: list[Booking] = []
    for line, row in records:
        try:
            bookings.append(_booking(file, line, row, header, need, optional))
        except ValueError as exc:
            errors.add(line, str(exc))
    errors.raise_any()
    return BookingFile(header=header, bookings=bookings)


def _booking(
    file: str, line: int, row: list[str], header: Header, need: dict[str, int], optional: dict[str, int | None]
) -> Booking:
    length = header.account_length
    amount = parse_amount(_cell(row, need["amount"]))
    if amount <= 0:
        raise ValueError("Umsatz must be above zero")
    side = _cell(row, need["side"]).upper()
    if side not in ("S", "H"):
        raise ValueError(f"Soll/Haben-Kennzeichen {side or '(empty)'} is neither S nor H")
    account = _account(_cell(row, need["account"]), length)
    contra = _account(_cell(row, need["contra"]), length)
    if account == contra:
        raise ValueError(f"Konto and Gegenkonto are both {account}")
    key = _cell(row, optional["key"]) or None
    if key is not None and not re.fullmatch(r"[0-9]{1,4}", key):
        raise ValueError(f"BU-Schlüssel {key!r} is no key")
    reversal_text = _cell(row, optional["reversal"])
    if reversal_text not in ("", "0", "1"):
        raise ValueError(f"Generalumkehr {reversal_text!r} is neither 0 nor 1")
    reversal = reversal_text == "1"
    swapped = False
    if key is not None and is_reversal_key(key):
        # The old way of writing a Generalumkehr: correction digit 2 in front of the tax key.
        if reversal:
            raise ValueError(f"BU-Schlüssel {key} and the column Generalumkehr both mark a Generalumkehr")
        reversal, swapped, key = True, True, (None if key[1] == "0" else key[1])
    discount = _cell(row, optional["discount"])
    if discount and parse_amount(discount) != 0:
        raise ValueError(
            "Skonto is not supported (DATEV splits the discount off automatically); "
            "export the discount as entries of their own"
        )
    currency = (_cell(row, optional["currency"]) or "EUR").upper()
    foreign_amount: Decimal | None = None
    fx_rate: str | None = None
    if currency != "EUR":
        if not re.fullmatch(r"[A-Z]{3}", currency):
            raise ValueError(f"WKZ Umsatz {currency!r} is no currency")
        base_currency = (_cell(row, optional["base_currency"]) or "EUR").upper()
        base = _cell(row, optional["base"])
        if base_currency != "EUR" or not base:
            raise ValueError(f"an amount in {currency} needs its euro amount (Basis-Umsatz in EUR)")
        foreign_amount, amount = amount, parse_amount(base)
        if amount <= 0:
            raise ValueError("Basis-Umsatz must be above zero")
        rate = _cell(row, optional["rate"])
        fx_rate = rate.replace(",", ".") if rate else None
        if fx_rate is not None and not re.fullmatch(r"[0-9]{1,6}(\.[0-9]{1,6})?", fx_rate):
            raise ValueError(f"Kurs {rate!r} is no rate")
    debit, credit = (account, contra) if (side == "S") != swapped else (contra, account)
    locked = _cell(row, optional["locked"])
    return Booking(
        file=file,
        line=line,
        amount=amount,
        debit_account=debit,
        credit_account=credit,
        contra_account=contra,
        tax_key=key,
        booking_date=_record_day(_cell(row, need["day"]), header),
        document_number=_cut(_cell(row, optional["number"]), 36, "Belegfeld 1"),
        document_number_2=_cut(_cell(row, optional["number_2"]), 12, "Belegfeld 2"),
        text=_cut(_cell(row, optional["text"]), 60, "Buchungstext"),
        currency=currency,
        foreign_amount=foreign_amount,
        fx_rate=fx_rate,
        guid=_cut(_cell(row, optional["guid"]), 36, "Buchungs GUID"),
        locked=None if not locked else locked == "1",
        reversal=reversal,
    )


def read_account_labels(data: bytes, file: str = "Kontenbeschriftungen") -> tuple[Header, list[AccountLabel]]:
    """Kontenbeschriftungen (category 20): general ledger accounts and their labels (the
    long label where there is one)."""
    header, columns, records = _datev_file(data, file, ACCOUNT_LABELS)
    need = _require(columns, file, {"account": ("Konto",), "label": ("Kontenbeschriftung",)})
    long_label = columns.find("Kontenbeschriftung lang")
    errors = _Errors(file)
    labels: list[AccountLabel] = []
    seen: dict[str, int] = {}
    for line, row in records:
        try:
            number = _account(_cell(row, need["account"]), header.account_length)
            if len(number) != header.account_length:
                raise ValueError(f"account {number} is no general ledger account ({header.account_length} digits)")
            label = _cut(_cell(row, long_label) or _cell(row, need["label"]), 300, "the label")
            if label is None:
                raise ValueError(f"account {number} has no label")
            if number in seen:
                raise ValueError(f"account {number} is labelled in line {seen[number]} already")
            seen[number] = line
            labels.append(AccountLabel(line=line, number=number, label=label))
        except ValueError as exc:
            errors.add(line, str(exc))
    errors.raise_any()
    return header, labels


def is_datev_format(data: bytes) -> bool:
    """Whether ``data`` starts with the header of the DATEV format (``EXTF`` / ``DTVF``)."""
    first = data[:16].lstrip(b"\xef\xbb\xbf")[:6].lstrip(b'"')
    return first.startswith((b"EXTF", b"DTVF"))


def read_personal_accounts(
    data: bytes, file: str = "Debitoren/Kreditoren", *, account_length: int = DEFAULT_ACCOUNT_LENGTH
) -> tuple[Header | None, list[PersonalAccount]]:
    """Debitoren/Kreditoren: the personal accounts with name and VAT id — the DATEV format
    (category 16), or the list Kanzlei-Rechnungswesen exports (Debitoren-/Kreditoren-
    stammdaten: a title line, then ``Konto;K/I;Beschriftung;…;USt-IdNr.;…;Kunden-/Lief.-Nr.``;
    no header of the DATEV format, ``None`` for it, so ``account_length`` says how long
    a general ledger account is there). A list names a person as ``Nachname, Vorname``;
    the name is kept as written."""
    if not is_datev_format(data):
        return None, _read_personal_list(data, file, account_length)
    header, columns, records = _datev_file(data, file, PERSONAL_ACCOUNTS)
    need = _require(columns, file, {"account": ("Konto",)})
    company = columns.find("Name (Adressattyp Unternehmen)")
    surname = columns.find("Name (Adressattyp natürl. Person)")
    first_name = columns.find("Vorname (Adressattyp natürl. Person)")
    unspecified = columns.find("Name (Adressattyp keine Angabe)")
    short = columns.find("Kurzbezeichnung")
    country = columns.find("EU-Land", "EU-Mitgliedstaat")
    vat_number = columns.find("EU-UStID", "EU-USt-IdNr.", "EU-UStIdNr")
    errors = _Errors(file)
    accounts: list[PersonalAccount] = []
    seen: dict[str, int] = {}
    for line, row in records:
        try:
            number = _account(_cell(row, need["account"]), header.account_length)
            if len(number) != header.account_length + 1:
                raise ValueError(f"account {number} is no personal account ({header.account_length + 1} digits)")
            person = " ".join(part for part in (_cell(row, first_name), _cell(row, surname)) if part)
            name = _cell(row, company) or person or _cell(row, unspecified) or _cell(row, short)
            if not name:
                raise ValueError(f"account {number} has no name")
            if number in seen:
                raise ValueError(f"account {number} is listed in line {seen[number]} already")
            seen[number] = line
            vat = re.sub(r"\s", "", _cell(row, country) + _cell(row, vat_number)).upper() or None
            if vat is not None and len(vat) > 20:
                raise ValueError("the VAT id has more than 20 characters")
            if len(name) > 200:
                raise ValueError("the name has more than 200 characters")
            accounts.append(PersonalAccount(line=line, number=number, name=name, vat_id=vat))
        except ValueError as exc:
            errors.add(line, str(exc))
    errors.raise_any()
    return header, accounts


# --- CSV lists ---------------------------------------------------------------------------


def _list_rows(data: bytes, file: str) -> tuple[_Columns, Iterator[tuple[int, list[str]]]]:
    _check_size(data, file)
    try:
        text = _decode_list(data)
    except UnicodeDecodeError as exc:
        raise DatevFormatError([LineError(file, 1, "neither UTF-8 nor Windows-1252")]) from exc
    rows = _rows(text, file)
    searched = 0
    for _line, row in rows:
        searched += len(";".join(row)) + 1
        if searched > MAX_HEADER_SEARCH:
            break
        if any(_norm(cell) == "konto" for cell in row):
            return _Columns(row), _records(rows)
    raise DatevFormatError([LineError(file, 1, _NO_LIST_HEADER)])


def _read_list[T](data: bytes, file: str, parse: Callable[[_Columns, str], Callable[[int, list[str]], T]]) -> list[T]:
    columns, records = _list_rows(data, file)
    one = parse(columns, file)
    errors = _Errors(file)
    result: list[T] = []
    for line, row in records:
        try:
            result.append(one(line, row))
        except ValueError as exc:
            errors.add(line, str(exc))
    errors.raise_any()
    return result


def _marked(row: list[str], position: int, marks: tuple[int, int], what: str) -> Decimal:
    """The amount at ``position`` with its side from the columns ``S`` and ``H`` after it:
    exactly one of them marks a non-zero amount; an empty amount is zero and has no mark."""
    text = _cell(row, position)
    debit_mark, credit_mark = _cell(row, marks[0]).upper(), _cell(row, marks[1]).upper()
    if debit_mark not in ("", "S") or credit_mark not in ("", "H"):
        raise ValueError(f"{what}: the side marks {debit_mark!r} / {credit_mark!r} are not S / H")
    if not text:
        if debit_mark or credit_mark:
            raise ValueError(f"{what}: a side mark without an amount")
        return Decimal(0)
    amount = parse_amount(text)
    if debit_mark and credit_mark:
        raise ValueError(f"{what} {text} is marked S and H")
    if not debit_mark and not credit_mark:
        if amount == 0:
            return amount
        raise ValueError(f"{what} {text} is marked neither S nor H")
    return amount if debit_mark else _negated(amount)


def read_trial_balance(
    data: bytes, file: str = "Summen- und Saldenliste", *, account_length: int = DEFAULT_ACCOUNT_LENGTH
) -> list[Balance]:
    """A trial balance (Summen- und Saldenliste) as CSV: ``Konto``, optionally
    ``Beschriftung``, and the balance as ``Saldo Soll`` / ``Saldo Haben`` or as ``Saldo``
    — signed, with an ``S/H`` column, or with its side in the columns ``S`` and ``H``
    right after it, as Kanzlei-Rechnungswesen exports it (``EB-Wert;S;H;Saldo;S;H;…``,
    the opening balance ``EB-Wert`` marked the same way). Debit is positive. ``Saldo``
    is the balance at the end of the period, the opening balance included. Rows without
    an account number are the totals of the export (per account class, the grand total)
    and are skipped. The list has no header: ``account_length`` says how long a general
    ledger account is, and shorter numbers are padded to it."""

    def parse(columns: _Columns, file: str) -> Callable[[int, list[str]], Balance | None]:
        account = columns.find("Konto")
        label = columns.find("Beschriftung", "Kontenbeschriftung", "Bezeichnung")
        debit, credit = columns.find("Saldo Soll"), columns.find("Saldo Haben")
        signed, side = columns.find("Saldo"), columns.find("S/H", "Soll/Haben")
        marks = columns.marks(signed)
        opening = columns.find("EB-Wert")
        opening_marks = columns.marks(opening)
        if (debit is None or credit is None) and signed is None:
            raise DatevFormatError([LineError(file, 1, "columns missing: Saldo Soll and Saldo Haben, or Saldo")])
        seen: dict[str, int] = {}

        def one(line: int, row: list[str]) -> Balance | None:
            if not _cell(row, account):
                # A total row (account class, grand total) of the export: no account of its own.
                return None
            number = _account(_cell(row, account), account_length)
            if debit is not None and credit is not None:
                debit_text, credit_text = _cell(row, debit), _cell(row, credit)
                if debit_text and credit_text and parse_amount(debit_text) and parse_amount(credit_text):
                    raise ValueError(f"account {number} has a debit and a credit balance")
                balance = MONEY_CONTEXT.subtract(
                    parse_amount(debit_text) if debit_text else Decimal(0),
                    parse_amount(credit_text) if credit_text else Decimal(0),
                )
            elif marks is not None:
                assert signed is not None
                balance = _marked(row, signed, marks, f"Saldo of account {number}")
            else:
                balance = parse_amount(_cell(row, signed) or "0", signed=True)
                marker = _cell(row, side).upper()
                if marker not in ("", "S", "H"):
                    raise ValueError(f"S/H {marker!r} is neither S nor H")
                if marker == "H":
                    balance = _negated(balance)
            opening_balance = None
            if opening is not None and opening_marks is not None:
                opening_balance = _marked(row, opening, opening_marks, f"EB-Wert of account {number}")
            if number in seen:
                raise ValueError(f"account {number} is listed in line {seen[number]} already")
            seen[number] = line
            return Balance(
                line=line,
                account=number,
                label=_cut(_cell(row, label), 200, "the label"),
                balance=balance,
                opening=opening_balance,
            )

        return one

    return [balance for balance in _read_list(data, file, parse) if balance is not None]


def _read_personal_list(data: bytes, file: str, account_length: int) -> list[PersonalAccount]:
    """The personal accounts as a list (see :func:`read_personal_accounts`)."""

    def parse(columns: _Columns, file: str) -> Callable[[int, list[str]], PersonalAccount]:
        need = _require(columns, file, {"account": ("Konto",), "name": ("Beschriftung", "Name")})
        vat_id = columns.find("USt-IdNr.", "USt-IdNr", "EU-UStID")
        short = columns.find("Kurzbezeichnung")
        number_column = columns.find("Kunden-/Lief.-Nr.", "Kunden-/Lieferantennummer")
        seen: dict[str, int] = {}

        def one(line: int, row: list[str]) -> PersonalAccount:
            number = _account(_cell(row, need["account"]), account_length)
            if len(number) != account_length + 1:
                raise ValueError(f"account {number} is no personal account ({account_length + 1} digits)")
            name = _cell(row, need["name"]) or _cell(row, short)
            if not name:
                raise ValueError(f"account {number} has no name")
            if number in seen:
                raise ValueError(f"account {number} is listed in line {seen[number]} already")
            seen[number] = line
            vat = re.sub(r"\s", "", _cell(row, vat_id)).upper() or None
            if vat is not None and len(vat) > 20:
                raise ValueError("the VAT id has more than 20 characters")
            if len(name) > 200:
                raise ValueError("the name has more than 200 characters")
            return PersonalAccount(
                line=line,
                number=number,
                name=name,
                vat_id=vat,
                customer_number=_cut(_cell(row, number_column), 50, "Kunden-/Lief.-Nr."),
            )

        return one

    try:
        return _read_list(data, file, parse)
    except DatevFormatError as exc:
        if [error.message for error in exc.errors] == [_NO_LIST_HEADER]:
            message = f"no DATEV header (line 1 starts with neither EXTF nor DTVF) and {_NO_LIST_HEADER}"
            raise DatevFormatError([LineError(file, 1, message)]) from exc
        raise


def read_fixed_assets(
    data: bytes, file: str = "Anlagenverzeichnis", *, account_length: int = DEFAULT_ACCOUNT_LENGTH
) -> list[FixedAsset]:
    """A fixed-asset register (Anlagenverzeichnis) as CSV: ``Inventarnummer``,
    ``Bezeichnung``, ``Konto``, ``Anschaffungsdatum`` (``DD.MM.YYYY``),
    ``Anschaffungskosten``, optionally ``Nutzungsdauer`` (years), ``AfA-Art`` and
    ``Buchwert``. The list has no header: ``account_length`` says how long a general ledger
    account is."""

    def parse(columns: _Columns, file: str) -> Callable[[int, list[str]], FixedAsset]:
        wanted = {
            "number": ("Inventarnummer", "Anlagennummer", "Anlage"),
            "name": ("Bezeichnung", "Anlagenbezeichnung"),
            "account": ("Konto",),
            "acquired": ("Anschaffungsdatum", "AHK-Datum", "Zugangsdatum"),
            "cost": ("Anschaffungskosten", "AHK", "Anschaffungs-/Herstellungskosten"),
        }
        need = _require(columns, file, wanted)
        life = columns.find("Nutzungsdauer", "ND")
        method = columns.find("AfA-Art", "AfA-Methode")
        book_value = columns.find("Buchwert", "Restbuchwert")
        seen: dict[str, int] = {}

        def one(line: int, row: list[str]) -> FixedAsset:
            number = _cut(_cell(row, need["number"]), 50, "the asset number")
            if number is None:
                raise ValueError("the asset has no number")
            if number in seen:
                raise ValueError(f"asset {number} is listed in line {seen[number]} already")
            name = _cut(_cell(row, need["name"]), 200, "the name")
            if name is None:
                raise ValueError(f"asset {number} has no name")
            account = _account(_cell(row, need["account"]), account_length)
            if len(account) != account_length:
                raise ValueError(f"account {account} is no general ledger account")
            life_text = _cell(row, life)
            if life_text and not re.fullmatch(r"[0-9]{1,3}", life_text):
                raise ValueError(f"Nutzungsdauer {life_text!r} is no number of years")
            value_text = _cell(row, book_value)
            seen[number] = line
            return FixedAsset(
                line=line,
                asset_number=number,
                name=name,
                account=account,
                acquired_on=_parse_day(_cell(row, need["acquired"]), "Anschaffungsdatum"),
                acquisition_cost=parse_amount(_cell(row, need["cost"])),
                useful_life_years=int(life_text) if life_text else None,
                method=_cut(_cell(row, method), 50, "AfA-Art"),
                book_value=parse_amount(value_text) if value_text else None,
            )

        return one

    return _read_list(data, file, parse)
