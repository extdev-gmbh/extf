"""Writers of files in the DATEV format (``EXTF``, format version 700).

A **Buchungsstapel** (category 21, Formatversion 13), **Kontenbeschriftungen** (category 20)
and **Debitoren/Kreditoren** (category 16): Windows-1252, fields separated by ``;``, text in
double quotes, amounts with a decimal comma, lines ending in CRLF — what :mod:`extf` reads.

**Validation:** before anything is written, every field is checked against the format —
lengths, characters, account numbers, the period (:func:`validate`). One bad field refuses
the whole file, every one named (:class:`~extf.ExtfError`). Nothing is cut or guessed
silently, except what the columns ask for: a label or name longer than its column is cut
there (the long label keeps all of it), and a VAT id that is no EU one is left out.

**Deterministic:** the same input gives the same bytes. ``Erzeugt am`` is
:attr:`ExportHeader.created`, never the clock.
"""

from __future__ import annotations

import calendar
import re
import unicodedata
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from extf._errors import ExtfError, LineError
from extf._format import (
    ACCOUNT_LABELS,
    ACCOUNT_LENGTHS,
    BOOKINGS,
    CATEGORY_NAMES,
    DATEV_ENCODING,
    PERSONAL_ACCOUNTS,
    fiscal_year_end,
    is_reversal_key,
    is_utf8,
    round_money,
)

#: The version of the DATEV format and of each category's layout written here.
FORMAT_VERSION = 700
CATEGORY_VERSIONS = {BOOKINGS: 13, ACCOUNT_LABELS: 3, PERSONAL_ACCOUNTS: 5}
#: Ranges of the numbers in the header.
ADVISOR_NUMBERS = (1001, 9999999)
CLIENT_NUMBERS = (1, 99999)

#: The columns of a Buchungsstapel, Formatversion 13, in the order of the format (125).
BOOKING_COLUMNS: tuple[str, ...] = (
    "Umsatz (ohne Soll/Haben-Kz)",
    "Soll/Haben-Kennzeichen",
    "WKZ Umsatz",
    "Kurs",
    "Basis-Umsatz",
    "WKZ Basis-Umsatz",
    "Konto",
    "Gegenkonto (ohne BU-Schlüssel)",
    "BU-Schlüssel",
    "Belegdatum",
    "Belegfeld 1",
    "Belegfeld 2",
    "Skonto",
    "Buchungstext",
    "Postensperre",
    "Diverse Adressnummer",
    "Geschäftspartnerbank",
    "Sachverhalt",
    "Zinssperre",
    "Beleglink",
    *(name for number in range(1, 9) for name in (f"Beleginfo - Art {number}", f"Beleginfo - Inhalt {number}")),
    "KOST1 - Kostenstelle",
    "KOST2 - Kostenstelle",
    "Kost-Menge",
    "EU-Land u. UStID (Bestimmung)",
    "EU-Steuersatz (Bestimmung)",
    "Abw. Versteuerungsart",
    "Sachverhalt L+L",
    "Funktionsergänzung L+L",
    "BU 49 Hauptfunktionstyp",
    "BU 49 Hauptfunktionsnummer",
    "BU 49 Funktionsergänzung",
    *(
        name
        for number in range(1, 21)
        for name in (f"Zusatzinformation - Art {number}", f"Zusatzinformation - Inhalt {number}")
    ),
    "Stück",
    "Gewicht",
    "Zahlweise",
    "Forderungsart",
    "Veranlagungsjahr",
    "Zugeordnete Fälligkeit",
    "Skontotyp",
    "Auftragsnummer",
    "Buchungstyp",
    "USt-Schlüssel (Anzahlungen)",
    "EU-Land (Anzahlungen)",
    "Sachverhalt L+L (Anzahlungen)",
    "EU-Steuersatz (Anzahlungen)",
    "Erlöskonto (Anzahlungen)",
    "Herkunft-Kz",
    "Buchungs GUID",
    "KOST-Datum",
    "SEPA-Mandatsreferenz",
    "Skontosperre",
    "Gesellschaftername",
    "Beteiligtennummer",
    "Identifikationsnummer",
    "Zeichnernummer",
    "Postensperre bis",
    "Bezeichnung SoBil-Sachverhalt",
    "Kennzeichen SoBil-Buchung",
    "Festschreibung",
    "Leistungsdatum",
    "Datum Zuord. Steuerperiode",
    "Fälligkeit",
    "Generalumkehr (GU)",
    "Steuersatz",
    "Land",
    "Abrechnungsreferenz",
    "BVV-Position",
    "EU-Land u. UStID (Ursprung)",
    "EU-Steuersatz (Ursprung)",
    "Abw. Skontokonto",
)
_COLUMN = {name: position for position, name in enumerate(BOOKING_COLUMNS)}
LABEL_COLUMNS = ("Konto", "Kontenbeschriftung", "Sprach-ID", "Kontenbeschriftung lang")
PERSONAL_COLUMNS = (
    "Konto",
    "Name (Adressatentyp Unternehmen)",
    "Unternehmensgegenstand",
    "Name (Adressatentyp natürl. Person)",
    "Vorname (Adressatentyp natürl. Person)",
    "Name (Adressatentyp keine Angabe)",
    "Adressatentyp",
    "Kurzbezeichnung",
    "EU-Land",
    "EU-UStID",
)
#: Adressatentyp of a personal account: a company.
ADDRESSEE_COMPANY = "2"

#: Field lengths of the format.
_AMOUNT_LIMIT = Decimal(10) ** 10
_AMOUNT_DIGITS = 10
_NUMBER_LENGTH = 36
_TEXT_LENGTH = 60
_INFO_KIND_LENGTH = 20
_INFO_LENGTH = 210
_LABEL_LENGTH = 40
_LONG_LABEL_LENGTH = 300
_NAME_LENGTH = 50
_SHORT_NAME_LENGTH = 15
_TITLE_LENGTH = 30
_EXPORTED_BY_LENGTH = 25
_MAX_INFOS = 8
#: The characters Belegfeld 1 takes.
_NUMBER_CHARS = re.compile(r"[^A-Za-z0-9$&%*+\-/]")
_NUMBER_OK = re.compile(r"[A-Za-z0-9$&%*+\-/]*")
_TAX_KEY = re.compile(r"[0-9]{1,4}")
_DIGITS = re.compile(r"[0-9]+")
_ORIGIN = re.compile(r"[A-Z0-9]{2}")
_CHART = re.compile(r"[0-9]{2}")
#: An EU VAT id: two letters and the number.
_EU_VAT_ID = re.compile(r"([A-Z]{2})([0-9A-Z+*]{2,13})")
_EU_COUNTRIES = frozenset(
    "AT BE BG CY CZ DE DK EE EL ES FI FR HR HU IE IT LT LU LV MT NL PL PT RO SE SI SK XI".split()
)
#: Line 1 is the header and line 2 the column names: record ``i`` (from 0) is on line ``i + 3``.
_FIRST_RECORD_LINE = 3


@dataclass(frozen=True, slots=True)
class ExportHeader:
    """What line 1 of a written file states."""

    #: Beraternummer and Mandantennummer.
    advisor_number: int
    client_number: int
    #: The first day of the fiscal year (WJ-Beginn).
    fiscal_year_start: date
    #: The length of a general ledger account number (Sachkontenlänge); a personal account
    #: has one digit more.
    account_length: int
    #: Herkunft: two upper-case letters or digits.
    origin: str
    #: Exportiert von.
    exported_by: str
    #: The chart of accounts (SKR, two digits).
    chart: str
    #: ``Erzeugt am``; the caller states it, so that the same input gives the same bytes.
    created: datetime
    #: The period of a Buchungsstapel (Datum vom / Datum bis); not written for the other categories.
    date_from: date | None = None
    date_to: date | None = None
    #: Festschreibung of a Buchungsstapel.
    locked: bool = False


@dataclass(frozen=True, slots=True)
class Record:
    """One record of a Buchungsstapel, as written."""

    amount: Decimal
    #: Konto and Gegenkonto as written (``S``: Konto debited).
    account: str
    contra_account: str
    tax_key: str | None
    booking_date: date
    document_number: str | None
    text: str
    #: Generalumkehr: the record cancels an entry; the sides are its own (the reversed
    #: entry's, swapped).
    reversal: bool = False
    #: Beleginfo: (Art, Inhalt) pairs, at most eight.
    infos: tuple[tuple[str, str], ...] = ()

    @property
    def debit(self) -> str:
        """The account the record debits (``S``: Konto)."""
        return self.account

    @property
    def credit(self) -> str:
        return self.contra_account


@dataclass(frozen=True, slots=True)
class Label:
    number: str
    label: str


@dataclass(frozen=True, slots=True)
class Personal:
    number: str
    name: str
    vat_id: str | None = None


# --- text and numbers --------------------------------------------------------------------


def _encodable(text: str) -> bool:
    try:
        text.encode(DATEV_ENCODING)
    except UnicodeEncodeError:
        return False
    return True


def clean_text(value: str | None) -> str:
    """``value`` on one line, in the characters of Windows-1252: what it lacks is written
    without its accents, or as ``?``."""
    text = " ".join((value or "").split())
    return "".join(
        char if _encodable(char) else "".join(part for part in unicodedata.normalize("NFKD", char) if _encodable(part)) or "?"
        for char in text
    )


def document_number(value: str | None) -> str | None:
    """Belegfeld 1: ``value`` with umlauts spelled out and every character the field does
    not take replaced by ``-`` (``RE 2026.001`` → ``RE-2026-001``)."""
    if value is None or not value.strip():
        return None
    text = value.strip()
    for umlaut, ascii_ in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("Ä", "AE"), ("Ö", "OE"), ("Ü", "UE"), ("ß", "ss")):
        text = text.replace(umlaut, ascii_)
    return _NUMBER_CHARS.sub("-", text)


def amount_de(value: Decimal) -> str:
    """An amount as the format writes it: two decimals, decimal comma, no grouping."""
    return f"{round_money(value):.2f}".replace(".", ",")


# --- validation -------------------------------------------------------------------------


def _check_text(errors: list[LineError], where: tuple[str, int], what: str, value: str | None, limit: int) -> None:
    if value is None:
        return
    if len(value) > limit:
        errors.append(LineError(*where, f"{what} has {len(value)} characters, at most {limit}"))
    if not _encodable(value):
        errors.append(LineError(*where, f"{what} holds characters outside Windows-1252"))
    if "\r" in value or "\n" in value:
        errors.append(LineError(*where, f"{what} holds a line break"))
    elif any(ord(char) < 32 or ord(char) == 127 for char in value):
        errors.append(LineError(*where, f"{what} holds a control character"))


def _is_account(number: str, lengths: Collection[int]) -> bool:
    return _DIGITS.fullmatch(number) is not None and len(number) in lengths and int(number) != 0


def _check_amount(errors: list[LineError], where: tuple[str, int], amount: Decimal) -> None:
    if not isinstance(amount, Decimal) or not amount.is_finite():
        errors.append(LineError(*where, f"Umsatz {amount!r} is no finite Decimal"))
    elif amount <= 0:
        errors.append(LineError(*where, "Umsatz must be above zero"))
    elif amount >= _AMOUNT_LIMIT or round_money(amount) != amount:
        errors.append(LineError(*where, f"Umsatz {amount} has more than {_AMOUNT_DIGITS} digits before or 2 after the comma"))


def _check_record(errors: list[LineError], where: tuple[str, int], record: Record, header: ExportHeader, allowed_keys: Collection[str] | None) -> None:
    length = header.account_length
    _check_amount(errors, where, record.amount)
    for what, number in (("Konto", record.account), ("Gegenkonto", record.contra_account)):
        if not _is_account(number, (length, length + 1)):
            errors.append(LineError(*where, f"{what} {number!r} is neither a general ledger account of {length} digits nor a personal account of {length + 1}"))
    if record.account == record.contra_account:
        errors.append(LineError(*where, f"Konto and Gegenkonto are both {record.account}"))
    if record.tax_key is not None:
        if _TAX_KEY.fullmatch(record.tax_key) and is_reversal_key(record.tax_key):
            errors.append(LineError(*where, f"BU-Schlüssel {record.tax_key!r} is the old notation of a Generalumkehr: use reversal=True"))
        elif not _TAX_KEY.fullmatch(record.tax_key) or (allowed_keys is not None and record.tax_key not in allowed_keys):
            errors.append(LineError(*where, f"BU-Schlüssel {record.tax_key!r} is no allowed tax key"))
    if header.date_from is not None and header.date_to is not None and not header.date_from <= record.booking_date <= header.date_to:
        errors.append(LineError(*where, f"Belegdatum {record.booking_date.isoformat()} lies outside the period"))
    if record.document_number is not None:
        if len(record.document_number) > _NUMBER_LENGTH:
            errors.append(LineError(*where, f"Belegfeld 1 has {len(record.document_number)} characters, at most {_NUMBER_LENGTH}"))
        if not _NUMBER_OK.fullmatch(record.document_number):
            errors.append(LineError(*where, f"Belegfeld 1 {record.document_number!r} holds characters the field does not take"))
    if not record.text.strip():
        errors.append(LineError(*where, "Buchungstext is empty"))
    _check_text(errors, where, "Buchungstext", record.text, _TEXT_LENGTH)
    if len(record.infos) > _MAX_INFOS:
        errors.append(LineError(*where, f"more than {_MAX_INFOS} Beleginfo fields"))
    for kind, content in record.infos:
        _check_text(errors, where, "Beleginfo - Art", kind, _INFO_KIND_LENGTH)
        _check_text(errors, where, "Beleginfo - Inhalt", content, _INFO_LENGTH)


def _check_header(errors: list[LineError], header: ExportHeader) -> None:
    where = ("header", 1)
    low, high = ADVISOR_NUMBERS
    if not low <= header.advisor_number <= high:
        errors.append(LineError(*where, f"Beraternummer {header.advisor_number} lies outside {low}–{high}"))
    low, high = CLIENT_NUMBERS
    if not low <= header.client_number <= high:
        errors.append(LineError(*where, f"Mandantennummer {header.client_number} lies outside {low}–{high}"))
    low, high = ACCOUNT_LENGTHS
    if not low <= header.account_length <= high:
        errors.append(LineError(*where, f"Sachkontenlänge {header.account_length} lies outside {low}–{high}"))
    if not _ORIGIN.fullmatch(header.origin):
        errors.append(LineError(*where, f"Herkunft {header.origin!r} is not two upper-case letters or digits"))
    _check_text(errors, where, "Exportiert von", header.exported_by, _EXPORTED_BY_LENGTH)
    if not _CHART.fullmatch(header.chart):
        errors.append(LineError(*where, f"the chart {header.chart!r} is not two digits"))


def _check_period(errors: list[LineError], header: ExportHeader) -> None:
    where = ("header", 1)
    date_from, date_to = header.date_from, header.date_to
    if date_from is None or date_to is None:
        errors.append(LineError(*where, "a Buchungsstapel needs its period (date_from, date_to)"))
        return
    try:
        past_year_end = date_to > fiscal_year_end(header.fiscal_year_start)
    except ValueError as exc:
        errors.append(LineError(*where, str(exc)))
        past_year_end = False
    if date_to < date_from or past_year_end:
        errors.append(LineError(*where, "the period does not lie in one fiscal year"))
    if date_from < header.fiscal_year_start:
        errors.append(LineError(*where, "the period begins before the fiscal year"))
    if date_to >= date_from:
        _check_text(errors, where, "Bezeichnung", _title(header, BOOKINGS), _TITLE_LENGTH)


def _check_label_file(errors: list[LineError], header: ExportHeader, labels: Sequence[Label]) -> None:
    """The one check on the file as written: its column line is ASCII, so nothing but its
    text keeps it from being valid UTF-8, which a reader refuses."""
    lines = _label_lines(header, labels)
    try:
        data = _encode(lines)
    except UnicodeEncodeError:
        return  # Named by _check_text; there is no written file to judge.
    if is_utf8(data):
        line = next(number for number, text in enumerate(lines, start=1) if not text.isascii())
        reason = "the written file is valid UTF-8: its text would be read back as UTF-8, which the reader refuses"
        errors.append(LineError(CATEGORY_NAMES[ACCOUNT_LABELS], line, reason))


def validate(
    header: ExportHeader,
    records: Sequence[Record] | None = None,
    labels: Sequence[Label] | None = None,
    personal: Sequence[Personal] | None = None,
    *,
    allowed_keys: Collection[str] | None = None,
) -> None:
    """Check ``header`` and what is passed of the files against the format;
    :class:`~extf.ExtfError` names every line that breaks it, ``line`` being the line the
    field would have in its file. ``allowed_keys``: the tax keys the books have; ``None``
    takes any key of up to four digits. The keys ``20`` to ``29`` are refused either way:
    the reader takes them as a Generalumkehr, which :attr:`Record.reversal` states. So are
    ``labels`` and a ``header`` whose written file would be valid UTF-8 beyond ASCII, which
    only its text as a whole can tell: the reader would refuse it."""
    errors: list[LineError] = []
    _check_header(errors, header)
    if records is not None:
        _check_period(errors, header)
        for line, record in enumerate(records, start=_FIRST_RECORD_LINE):
            _check_record(errors, (CATEGORY_NAMES[BOOKINGS], line), record, header, allowed_keys)
    length = header.account_length
    seen: set[str] = set()
    for line, label in enumerate(labels or (), start=_FIRST_RECORD_LINE):
        where = (CATEGORY_NAMES[ACCOUNT_LABELS], line)
        if not _is_account(label.number, (length,)):
            errors.append(LineError(*where, f"account {label.number!r}: a label is for a general ledger account of {length} digits"))
        if label.number in seen:
            errors.append(LineError(*where, f"account {label.number} is labelled twice"))
        seen.add(label.number)
        if not label.label.strip():
            errors.append(LineError(*where, f"account {label.number}: the label is empty"))
        _check_text(errors, where, "Kontenbeschriftung", label.label, _LONG_LABEL_LENGTH)
    if labels is not None:
        _check_label_file(errors, header, labels)
    seen = set()
    for line, account in enumerate(personal or (), start=_FIRST_RECORD_LINE):
        where = (CATEGORY_NAMES[PERSONAL_ACCOUNTS], line)
        if not _is_account(account.number, (length + 1,)):
            errors.append(LineError(*where, f"personal account {account.number!r}: a personal account has {length + 1} digits"))
        if account.number in seen:
            errors.append(LineError(*where, f"personal account {account.number} is listed twice"))
        seen.add(account.number)
        if not account.name[:_NAME_LENGTH].strip():
            errors.append(LineError(*where, f"personal account {account.number}: the name is empty"))
        _check_text(errors, where, "Name", account.name[:_NAME_LENGTH], _NAME_LENGTH)
    if errors:
        raise ExtfError(errors)


# --- writing ----------------------------------------------------------------------------


def _quoted(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _title(header: ExportHeader, category: int) -> str:
    if category != BOOKINGS:
        return CATEGORY_NAMES[category]
    assert header.date_from is not None and header.date_to is not None
    start, end = header.date_from, header.date_to
    month_end = date(start.year, start.month, calendar.monthrange(start.year, start.month)[1])
    if start.day == 1 and month_end == end:
        return f"Buchungen {start:%m/%Y}"
    return f"Buchungen {start:%d.%m.}-{end:%d.%m.%Y}"


def _header_line(header: ExportHeader, category: int) -> str:
    """Line 1 of a file (31 fields)."""
    bookings = category == BOOKINGS
    fields = [
        _quoted("EXTF"),
        str(FORMAT_VERSION),
        str(category),
        _quoted(CATEGORY_NAMES[category]),
        str(CATEGORY_VERSIONS[category]),
        f"{header.created:%Y%m%d%H%M%S}{header.created.microsecond // 1000:03d}",
        "",
        _quoted(header.origin),
        _quoted(header.exported_by),
        _quoted(""),
        str(header.advisor_number),
        str(header.client_number),
        f"{header.fiscal_year_start:%Y%m%d}",
        str(header.account_length),
        f"{header.date_from:%Y%m%d}" if bookings else "",
        f"{header.date_to:%Y%m%d}" if bookings else "",
        _quoted(_title(header, category)),
        _quoted(""),
        "1" if bookings else "",
        "0" if bookings else "",
        ("1" if header.locked else "0") if bookings else "",
        _quoted("EUR" if bookings else ""),
        "",
        _quoted(""),
        "",
        "",
        _quoted(header.chart),
        "",
        "",
        _quoted(""),
        _quoted(""),
    ]
    return ";".join(fields)


def _encode(lines: Sequence[str]) -> bytes:
    return ("\r\n".join(lines) + "\r\n").encode(DATEV_ENCODING)


def _record_line(record: Record) -> str:
    cells = [""] * len(BOOKING_COLUMNS)

    def put(column: str, value: str, *, text: bool = False) -> None:
        cells[_COLUMN[column]] = _quoted(value) if text else value

    put("Umsatz (ohne Soll/Haben-Kz)", amount_de(record.amount))
    put("Soll/Haben-Kennzeichen", "S", text=True)
    put("WKZ Umsatz", "EUR", text=True)
    put("Konto", record.account)
    put("Gegenkonto (ohne BU-Schlüssel)", record.contra_account)
    put("BU-Schlüssel", record.tax_key or "", text=True)
    put("Belegdatum", f"{record.booking_date:%d%m}")
    put("Belegfeld 1", record.document_number or "", text=True)
    put("Buchungstext", record.text, text=True)
    for number, (kind, content) in enumerate(record.infos, start=1):
        put(f"Beleginfo - Art {number}", kind, text=True)
        put(f"Beleginfo - Inhalt {number}", content, text=True)
    put("Generalumkehr (GU)", "1" if record.reversal else "0")
    return ";".join(cells)


def write_bookings(header: ExportHeader, records: Sequence[Record], *, allowed_keys: Collection[str] | None = None) -> bytes:
    """A Buchungsstapel (category 21), validated (:func:`validate`)."""
    validate(header, records, allowed_keys=allowed_keys)
    return _encode([_header_line(header, BOOKINGS), ";".join(BOOKING_COLUMNS), *(_record_line(record) for record in records)])


def _label_lines(header: ExportHeader, labels: Sequence[Label]) -> list[str]:
    lines = [_header_line(header, ACCOUNT_LABELS), ";".join(LABEL_COLUMNS)]
    for label in labels:
        lines.append(";".join((label.number, _quoted(label.label[:_LABEL_LENGTH]), _quoted("de-DE"), _quoted(label.label))))
    return lines


def write_account_labels(header: ExportHeader, labels: Sequence[Label]) -> bytes:
    """Kontenbeschriftungen (category 20), validated (:func:`validate`). A label longer than
    the short column is cut there; the long column keeps all of it."""
    validate(header, labels=labels)
    return _encode(_label_lines(header, labels))


def write_personal_accounts(header: ExportHeader, personal: Sequence[Personal]) -> bytes:
    """Debitoren/Kreditoren (category 16), validated (:func:`validate`). Every account is
    written as a company; a name longer than its column is cut there; the VAT id goes out
    split into country and number if it is an EU one and is left out otherwise."""
    validate(header, personal=personal)
    lines = [_header_line(header, PERSONAL_ACCOUNTS), ";".join(PERSONAL_COLUMNS)]
    for account in personal:
        country, number = "", ""
        match = _EU_VAT_ID.fullmatch(account.vat_id or "")
        if match is not None and match.group(1) in _EU_COUNTRIES:
            country, number = match.group(1), match.group(2)
        cells = [
            account.number,
            _quoted(account.name[:_NAME_LENGTH]),
            _quoted(""),
            _quoted(""),
            _quoted(""),
            _quoted(""),
            _quoted(ADDRESSEE_COMPANY),
            _quoted(account.name[:_SHORT_NAME_LENGTH]),
            _quoted(country),
            _quoted(number),
        ]
        lines.append(";".join(cells))
    return _encode(lines)
