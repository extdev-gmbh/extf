"""Hand-written files in the DATEV format (EXTF 700) and the CSV lists that come with them.
Every account, amount, number and name here is invented."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date

#: The columns of a Buchungsstapel the reader needs, in the order of the format (a full file has 125).
BOOKING_COLUMNS = (
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
    "Generalumkehr (GU)",
)


def _quoted(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def header(
    category: int,
    name: str,
    *,
    fiscal_year_start: date = date(2026, 1, 1),
    account_length: int = 4,
    date_from: date | None = None,
    date_to: date | None = None,
) -> str:
    fields = [
        _quoted("EXTF"),
        "700",
        str(category),
        _quoted(name),
        "13",
        "20260115120000000",
        "",
        _quoted("RE"),
        _quoted(""),
        _quoted(""),
        "1234567",
        "10001",
        fiscal_year_start.strftime("%Y%m%d"),
        str(account_length),
        "" if date_from is None else date_from.strftime("%Y%m%d"),
        "" if date_to is None else date_to.strftime("%Y%m%d"),
        _quoted("Buchungen"),
        _quoted("RI"),
        "1",
        "0",
        "1",
        _quoted("EUR"),
    ]
    return ";".join(fields)


def booking(
    amount: str,
    side: str,
    account: str,
    contra: str,
    day: str,
    *,
    key: str = "",
    number: str = "",
    text: str = "",
    currency: str = "",
    base: str = "",
    rate: str = "",
    discount: str = "",
    reversal: str = "",
) -> list[str]:
    """One record: ``amount`` with decimal comma, ``day`` as ``DDMM``."""
    return [
        amount,
        _quoted(side),
        _quoted(currency),
        rate,
        base,
        _quoted("EUR" if base else ""),
        account,
        contra,
        _quoted(key),
        day,
        _quoted(number),
        _quoted(""),
        discount,
        _quoted(text),
        _quoted(reversal),
    ]


def buchungsstapel(
    records: Sequence[list[str]],
    *,
    fiscal_year_start: date = date(2026, 1, 1),
    date_from: date = date(2026, 1, 1),
    date_to: date = date(2026, 6, 30),
    account_length: int = 4,
    columns: Sequence[str] = BOOKING_COLUMNS,
) -> bytes:
    lines = [
        header(
            21,
            "Buchungsstapel",
            fiscal_year_start=fiscal_year_start,
            account_length=account_length,
            date_from=date_from,
            date_to=date_to,
        ),
        ";".join(columns),
        *(";".join(record) for record in records),
    ]
    return ("\r\n".join(lines) + "\r\n").encode("cp1252")


def kontenbeschriftungen(labels: Sequence[tuple[str, str]], *, account_length: int = 4) -> bytes:
    lines = [
        header(20, "Kontenbeschriftungen", account_length=account_length),
        "Konto;Kontenbeschriftung;Sprach-ID;Kontenbeschriftung lang",
        *(f'{number};{_quoted(label[:40])};"de-DE";{_quoted(label)}' for number, label in labels),
    ]
    return ("\r\n".join(lines) + "\r\n").encode("cp1252")


def debitoren_kreditoren(accounts: Sequence[tuple[str, str, str, str]]) -> bytes:
    """``accounts``: number, company name, EU country, VAT number."""
    lines = [
        header(16, "Debitoren/Kreditoren"),
        "Konto;Name (Adressattyp Unternehmen);Unternehmensgegenstand;Name (Adressattyp natürl. Person);"
        "Vorname (Adressattyp natürl. Person);Name (Adressattyp keine Angabe);Adressattyp;Kurzbezeichnung;EU-Land;EU-UStID",
        *(
            f'{number};{_quoted(name)};"";"";"";"";"2";"";{_quoted(country)};{_quoted(vat)}'
            for number, name, country, vat in accounts
        ),
    ]
    return ("\r\n".join(lines) + "\r\n").encode("cp1252")


def saldenliste(
    balances: Sequence[tuple[str, str, str]], *, title: bool = True, totals: Sequence[tuple[str, str, str]] = ()
) -> bytes:
    """``balances``: account, Saldo Soll, Saldo Haben (decimal comma, empty for none);
    ``totals``: label, debit, credit of total rows without an account, as an export has them."""
    lines = (["Summen- und Saldenliste;30.06.2026"] if title else []) + [
        "Konto;Beschriftung;Saldo Soll;Saldo Haben",
        *(f"{account};Konto {account};{debit};{credit}" for account, debit, credit in balances),
        *(f";{label};{debit};{credit}" for label, debit, credit in totals),
    ]
    return ("\n".join(lines) + "\n").encode("utf-8")


def anlagenverzeichnis(assets: Sequence[tuple[str, str, str, str, str, str, str]]) -> bytes:
    """``assets``: number, name, account, acquired (DD.MM.YYYY), cost, life (years), book value."""
    lines = [
        "Inventarnummer;Bezeichnung;Konto;Anschaffungsdatum;Anschaffungskosten;Nutzungsdauer;AfA-Art;Buchwert",
        *(f"{n};{name};{account};{day};{cost};{life};linear;{value}" for n, name, account, day, cost, life, value in assets),
    ]
    return ("\n".join(lines) + "\n").encode("cp1252")


def saldenliste_kanzlei(balances: Sequence[tuple[str, str, str, str, str]]) -> bytes:
    """A trial balance as Kanzlei-Rechnungswesen exports it ("Summen und Salden (pro
    Monat)"): a title line, then ``Konto;Beschriftung;EB-Wert;S;H;Saldo;S;H`` and the month
    columns, the side of each amount marked in the ``S`` or ``H`` column after it.
    ``balances``: account, label, EB-Wert, Saldo (decimal comma, empty for none) and the
    marks as two letters, the EB-Wert's and the Saldo's (``S``, ``H`` or ``-`` for none),
    e.g. ``"-H"``; a mark ``B`` puts it in both columns."""

    def marked(amount: str, mark: str) -> str:
        debit = "S" if mark in ("S", "B") else ""
        credit = "H" if mark in ("H", "B") else ""
        return f"{amount};{debit};{credit}"

    lines = [
        "Kanzlei-Rechnungswesen - RW - 1234567/10001/2026 / Musterbuch GmbH - Summen- und Saldenliste - "
        "Summen und Salden (pro Monat) Juni;;;;;;;;;;;",
        'Konto;Beschriftung;EB-Wert;S;H;Saldo;S;H;"Jun 2026 \r\nSoll";" \r\nHaben";"kum. Werte \r\nSoll";" \r\nHaben"',
        *(
            f"{account};{label};{marked(opening, marks[0])};{marked(balance, marks[1])};0,00;0,00;0,00;0,00"
            for account, label, opening, balance, marks in balances
        ),
    ]
    return ("\r\n".join(lines) + "\r\n").encode("cp1252")


def debitoren_kreditoren_liste(accounts: Sequence[tuple[str, str, str, str]]) -> bytes:
    """The personal accounts as Kanzlei-Rechnungswesen lists them (Debitoren-/Kreditoren-
    stammdaten, no DATEV header). ``accounts``: number, Beschriftung, USt-IdNr., Kunden-/Lief.-Nr."""
    lines = [
        "Kanzlei-Rechnungswesen - 1234567/10001/2026 / Musterbuch GmbH - Debitoren- / Kreditorenstammdaten WJ 2026;;;;;;",
        "Konto;K/I;Beschriftung;Ort;USt-IdNr.;Kurzbezeichnung;Kunden-/Lief.-Nr.",
        *(f"{number};I;{name};Musterstadt;{vat};{name[:15]};{customer}" for number, name, vat, customer in accounts),
    ]
    return ("\r\n".join(lines) + "\r\n").encode("cp1252")
