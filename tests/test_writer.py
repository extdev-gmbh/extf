"""The writers: golden files, the format of the bytes, validation, and the round trip."""

from __future__ import annotations

import csv
import io
import os
from collections.abc import Callable
from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal, localcontext
from pathlib import Path

import pytest

import extf
from extf import ExportHeader, ExtfError, Label, Personal, Record

GOLDEN = Path(__file__).resolve().parent / "golden"
ADVISOR, CLIENT = 1234567, 10001

HEADER = ExportHeader(
    advisor_number=ADVISOR,
    client_number=CLIENT,
    fiscal_year_start=date(2026, 1, 1),
    account_length=4,
    origin="XT",
    exported_by="extf tests",
    chart="03",
    created=datetime(2026, 8, 3, 9, 15, 0, 250000),
    date_from=date(2026, 7, 1),
    date_to=date(2026, 7, 31),
)
RECORDS = (
    Record(
        amount=Decimal("1190.00"), account="10028", contra_account="8400", tax_key=None, booking_date=date(2026, 7, 10),
        document_number="RE-2026-041", text="Nordlicht Handel AG RE-2026-041",
        infos=(("Buchungssatz", "11"), ("Archiv-ID", "5101"), ("Link", "https://books.example/bookings/document/7")),
    ),
    Record(
        amount=Decimal("1190.00"), account="1200", contra_account="10028", tax_key=None, booking_date=date(2026, 7, 20),
        document_number="RE-2026-041", text="Zahlung Nordlicht Handel AG", infos=(("Buchungssatz", "12"),),
    ),
    Record(
        amount=Decimal("119.00"), account="4806", contra_account="70049", tax_key="9", booking_date=date(2026, 7, 3),
        document_number="H-2026-07", text='Wartung "Juli" – Server', infos=(("Buchungssatz", "13"), ("Belegnummer", "H 2026.07")),
    ),
    Record(
        amount=Decimal("119.00"), account="70049", contra_account="4806", tax_key="9", booking_date=date(2026, 7, 28),
        document_number="H-2026-07", text='Storno Wartung "Juli" – Server', reversal=True,
        infos=(("Buchungssatz", "14"), ("Belegdatum", "03.07.2026")),
    ),
    Record(
        amount=Decimal("4.62"), account="4970", contra_account="1201", tax_key=None, booking_date=date(2026, 7, 31),
        document_number=None, text="Gebühr Überweisung", infos=(("Buchungssatz", "15"), ("Fremdwährung", "5,00 USD, Kurs 1,0822")),
    ),
)  # fmt: skip
LABELS = (
    Label("1200", "Bank"),
    Label("1201", "Tagesgeld"),
    Label("4806", "Wartungskosten für Hard- und Software"),
    Label("4970", "Nebenkosten des Geldverkehrs"),
    Label("8400", "Erlöse 19% USt"),
    Label("8401", "Eine sehr lange Beschriftung über mehr als vierzig Zeichen hinaus"),
)
PERSONAL = (
    Personal("10028", "Nordlicht Handel AG", "ATU00000000"),
    Personal("70049", "Muster Service GmbH", None),
    Personal("70050", "Ein Lieferant mit einem Namen, der länger ist als die fünfzig Zeichen", "CHE000000000"),
)
WRITTEN: dict[str, Callable[[], bytes]] = {
    "bookings.csv": lambda: extf.write_bookings(HEADER, RECORDS, allowed_keys={"9"}),
    "account_labels.csv": lambda: extf.write_account_labels(HEADER, LABELS),
    "personal_accounts.csv": lambda: extf.write_personal_accounts(HEADER, PERSONAL),
}


def rows(data: bytes) -> list[list[str]]:
    return list(csv.reader(io.StringIO(data.decode("cp1252"), newline=""), delimiter=";"))


@pytest.mark.parametrize("name", sorted(WRITTEN))
def test_the_bytes_are_the_golden_files(name: str) -> None:
    written = WRITTEN[name]()
    if os.environ.get("EXTF_UPDATE_GOLDEN"):
        (GOLDEN / name).write_bytes(written)
    assert written == (GOLDEN / name).read_bytes(), f"{name} differs from its golden file"


def test_the_buchungsstapel_is_windows_1252_with_crlf() -> None:
    content = extf.write_bookings(HEADER, RECORDS)
    assert b"\r\n" in content and b"\n" not in content.replace(b"\r\n", b"")
    assert "–".encode("cp1252") in content and "Ü".encode("cp1252") in content
    with pytest.raises(UnicodeDecodeError):
        content.decode("utf-8")
    parsed = rows(content)
    assert parsed[0][:5] == ["EXTF", "700", "21", "Buchungsstapel", "13"]
    assert parsed[0][7:9] == ["XT", "extf tests"]
    assert parsed[0][10:16] == [str(ADVISOR), str(CLIENT), "20260101", "4", "20260701", "20260731"]
    assert parsed[0][16] == "Buchungen 07/2026" and parsed[0][20] == "0" and parsed[0][26] == "03"
    assert len(parsed[1]) == 125 and all(len(row) == 125 for row in parsed[2:])


def test_the_header_states_what_the_caller_gives() -> None:
    header = replace(HEADER, date_from=date(2026, 7, 2), date_to=date(2026, 7, 9), locked=True, chart="04", origin="A1", account_length=5)
    records = [replace(RECORDS[0], account="100280", contra_account="84000", booking_date=date(2026, 7, 5))]
    parsed = rows(extf.write_bookings(header, records))
    assert parsed[0][7] == "A1" and parsed[0][13] == "5" and parsed[0][16] == "Buchungen 02.07.-09.07.2026"
    assert parsed[0][20] == "1" and parsed[0][26] == "04"
    labels = rows(extf.write_account_labels(HEADER, LABELS))
    assert labels[0][14:16] == ["", ""] and labels[0][16] == "Kontenbeschriftungen"


def test_the_writers_round_trip_through_the_readers() -> None:
    parsed = extf.read_bookings(extf.write_bookings(HEADER, RECORDS), "export")
    assert (parsed.header.date_from, parsed.header.date_to, parsed.header.account_length) == (date(2026, 7, 1), date(2026, 7, 31), 4)
    assert not parsed.header.locked
    assert [(b.amount, b.debit_account, b.credit_account, b.tax_key, b.reversal, b.document_number, b.text, b.booking_date) for b in parsed.bookings] == [
        (r.amount, r.account, r.contra_account, r.tax_key, r.reversal, r.document_number, r.text, r.booking_date) for r in RECORDS
    ]
    _, labels = extf.read_account_labels(extf.write_account_labels(HEADER, LABELS))
    assert [(label.number, label.label) for label in labels] == [(label.number, label.label) for label in LABELS]
    _, personal = extf.read_personal_accounts(extf.write_personal_accounts(HEADER, PERSONAL))
    assert [(row.number, row.name, row.vat_id) for row in personal] == [
        ("10028", "Nordlicht Handel AG", "ATU00000000"),
        ("70049", "Muster Service GmbH", None),
        # The name is cut at its column; a VAT id that is no EU one stays out.
        ("70050", PERSONAL[2].name[:50].strip(), None),
    ]


def test_the_same_input_gives_the_same_bytes() -> None:
    for write in WRITTEN.values():
        assert write() == write()


def test_every_field_that_breaks_the_format_is_named_and_nothing_is_written() -> None:
    bad = Record(
        amount=Decimal("1.005"), account="12", contra_account="12", tax_key="77", booking_date=date(2026, 8, 1),
        document_number="A B", text="x" * 61, infos=(("Art ist viel zu lang für das Feld", "ř"),),
    )  # fmt: skip
    header = replace(HEADER, advisor_number=12, client_number=0, origin="toolong", chart="SKR", exported_by="x" * 26)

    with pytest.raises(ExtfError) as caught:
        extf.write_bookings(header, [*RECORDS, bad], allowed_keys={"9"})

    errors = caught.value.errors
    assert {error.line for error in errors} == {1, 8}
    messages = [error.message for error in errors]
    for expected in (
        "Beraternummer 12 lies outside 1001–9999999",
        "Mandantennummer 0 lies outside 1–99999",
        "Herkunft 'toolong'",
        "the chart 'SKR'",
        "Exportiert von has 26 characters, at most 25",
        "Umsatz 1.005 has more than 10 digits before or 2 after the comma",
        "Konto '12' is neither",
        "Konto and Gegenkonto are both 12",
        "BU-Schlüssel '77' is no allowed tax key",
        "Belegdatum 2026-08-01 lies outside the period",
        "Belegfeld 1 'A B' holds characters the field does not take",
        "Buchungstext has 61 characters, at most 60",
        "Beleginfo - Art has 33 characters, at most 20",
        "Beleginfo - Inhalt holds characters outside Windows-1252",
    ):
        assert any(message.startswith(expected) for message in messages), expected


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"amount": Decimal("NaN")}, "no finite Decimal"),
        ({"amount": Decimal("sNaN")}, "no finite Decimal"),
        ({"amount": Decimal("Infinity")}, "no finite Decimal"),
        ({"amount": 1.5}, "no finite Decimal"),
        ({"amount": Decimal("0")}, "above zero"),
        ({"amount": Decimal("-1.00")}, "above zero"),
        ({"amount": Decimal("10000000000.00")}, "more than 10 digits"),
        ({"amount": Decimal("1E+5000")}, "more than 10 digits"),
        ({"account": "120²"}, "is neither"),
        ({"account": "0000"}, "is neither"),
        ({"tax_key": "9\n"}, "no allowed tax key"),
        ({"document_number": "A\n"}, "does not take"),
        ({"document_number": "A" * 37}, "at most 36"),
        ({"text": ""}, "Buchungstext is empty"),
        ({"text": "   "}, "Buchungstext is empty"),
        ({"text": "two\nlines"}, "line break"),
        ({"text": "a\x00b"}, "control character"),
        ({"infos": (("a", "b"),) * 9}, "more than 8 Beleginfo"),
    ],
)
def test_a_bad_record_is_refused(change: dict[str, object], message: str) -> None:
    record = replace(RECORDS[0], **change)  # type: ignore[arg-type]
    with pytest.raises(ExtfError, match=message) as caught:
        extf.write_bookings(HEADER, [record])
    assert caught.value.errors[0].file == "Buchungsstapel" and caught.value.errors[0].line == 3


REVERSAL_KEYS = [f"2{digit}" for digit in range(10)]


@pytest.mark.parametrize("key", REVERSAL_KEYS)
def test_a_key_in_the_old_notation_of_a_generalumkehr_is_refused(key: str) -> None:
    with pytest.raises(ExtfError) as caught:
        extf.validate(HEADER, [replace(RECORDS[0], tax_key=key)])
    message = caught.value.errors[0].message
    assert len(caught.value.errors) == 1 and repr(key) in message and "reversal=True" in message
    with pytest.raises(ExtfError, match="reversal=True"):
        extf.write_bookings(HEADER, [replace(RECORDS[0], tax_key=key)])


@pytest.mark.parametrize("key", REVERSAL_KEYS)
@pytest.mark.parametrize("reversal", [False, True])
@pytest.mark.parametrize("allowed_keys", [None, {"9"}, set(REVERSAL_KEYS)])
def test_the_old_notation_is_refused_whatever_else_the_record_and_the_caller_state(key: str, reversal: bool, allowed_keys: set[str] | None) -> None:
    with pytest.raises(ExtfError, match="reversal=True"):
        extf.validate(HEADER, [replace(RECORDS[0], tax_key=key, reversal=reversal)], allowed_keys=allowed_keys)


@pytest.mark.parametrize("key", ["0", "2", "02", "19", "30", "94", "100", "200", "2000", "2020"])
def test_only_the_two_digit_form_is_reserved(key: str) -> None:
    written = extf.write_bookings(HEADER, [replace(RECORDS[0], tax_key=key)], allowed_keys={key})
    assert extf.read_bookings(written).bookings[0].tax_key == key


def test_a_period_lies_in_one_fiscal_year() -> None:
    for start, end, message in (
        (date(2026, 12, 1), date(2027, 1, 31), "one fiscal year"),
        (date(2026, 7, 31), date(2026, 7, 1), "one fiscal year"),
        (date(2025, 12, 1), date(2026, 1, 31), "before the fiscal year"),
    ):
        with pytest.raises(ExtfError, match=message):
            extf.validate(replace(HEADER, date_from=start, date_to=end), [])
    with pytest.raises(ExtfError, match="needs its period"):
        extf.write_bookings(replace(HEADER, date_from=None), [])
    # A fiscal year that is not the calendar year.
    extf.validate(replace(HEADER, fiscal_year_start=date(2025, 7, 1), date_from=date(2025, 12, 1), date_to=date(2026, 1, 31)), [])


def test_a_period_in_the_last_month_of_the_calendar_is_written() -> None:
    header = replace(HEADER, fiscal_year_start=date(9999, 1, 1), date_from=date(9999, 12, 1), date_to=date(9999, 12, 31))
    record = replace(RECORDS[0], booking_date=date(9999, 12, 10))
    assert b'"Buchungen 12/9999"' in extf.write_bookings(header, [record])
    for day in (date(9999, 12, 10), date(9999, 12, 31)):
        (booking,) = extf.read_bookings(extf.write_bookings(header, [replace(record, booking_date=day)])).bookings
        assert booking.booking_date == day


@pytest.mark.parametrize("start", [date(9999, 1, 2), date(9999, 7, 1), date(9999, 12, 31)])
def test_a_fiscal_year_ending_after_year_9999_is_refused(start: date) -> None:
    header = replace(HEADER, fiscal_year_start=start, date_from=date(9999, 12, 31), date_to=date(9999, 12, 31))
    record = replace(RECORDS[0], booking_date=date(9999, 12, 31))
    for call in (lambda: extf.validate(header, [record]), lambda: extf.write_bookings(header, [record])):
        with pytest.raises(ExtfError) as caught:
            call()
        (error,) = caught.value.errors
        assert (error.file, error.line) == ("header", 1)
        assert f"starting on {start.isoformat()} would end after year 9999" in error.message


@pytest.mark.parametrize(
    ("start", "end", "title"),
    [
        (date(2026, 2, 1), date(2026, 2, 28), "Buchungen 02/2026"),
        (date(2024, 2, 1), date(2024, 2, 29), "Buchungen 02/2024"),
        (date(2026, 12, 1), date(2026, 12, 31), "Buchungen 12/2026"),
        (date(2026, 2, 1), date(2026, 2, 27), "Buchungen 01.02.-27.02.2026"),
        (date(2026, 2, 2), date(2026, 2, 28), "Buchungen 02.02.-28.02.2026"),
    ],
)
def test_the_title_names_a_whole_month_by_the_month(start: date, end: date, title: str) -> None:
    header = replace(HEADER, fiscal_year_start=date(start.year, 1, 1), date_from=start, date_to=end)
    record = replace(RECORDS[0], booking_date=start)
    assert f'"{title}"'.encode() in extf.write_bookings(header, [record])


@pytest.mark.parametrize("label", ["   ", "\t", "\xa0"])
def test_a_blank_label_is_refused(label: str) -> None:
    with pytest.raises(ExtfError, match="the label is empty"):
        extf.write_account_labels(HEADER, [Label("4000", label)])


@pytest.mark.parametrize("name", ["   ", " " * 50 + "the visible part is cut off"])
def test_a_name_blank_in_the_written_field_is_refused(name: str) -> None:
    with pytest.raises(ExtfError, match="the name is empty"):
        extf.write_personal_accounts(HEADER, [Personal("70000", name)])


def test_a_label_that_would_be_read_back_as_utf8_is_refused() -> None:
    with pytest.raises(ExtfError, match="read back as UTF-8") as caught:
        extf.write_account_labels(HEADER, [Label("1200", "Bank"), Label("4000", "Ã¼ber")])
    assert [(error.file, error.line) for error in caught.value.errors] == [("Kontenbeschriftungen", 4)]
    with pytest.raises(ExtfError, match="read back as UTF-8"):
        extf.validate(HEADER, labels=[Label("4000", "Ã¼ber")])


@pytest.mark.parametrize("labels", [[Label("4000", "Konto")], []])
def test_a_header_that_would_be_read_back_as_utf8_is_refused(labels: list[Label]) -> None:
    with pytest.raises(ExtfError, match="read back as UTF-8") as caught:
        extf.write_account_labels(replace(HEADER, exported_by="Ã¼ber"), labels)
    assert [error.line for error in caught.value.errors] == [1]


def test_the_other_writers_take_what_would_be_utf8_in_a_label_file() -> None:
    header = replace(HEADER, exported_by="Ã¼ber")
    assert len(extf.read_bookings(extf.write_bookings(header, RECORDS)).bookings) == len(RECORDS)
    _, accounts = extf.read_personal_accounts(extf.write_personal_accounts(header, [Personal("70000", "Ã¼ber")]))
    assert [account.name for account in accounts] == ["Ã¼ber"]
    extf.validate(header)


def test_another_label_keeps_the_file_from_being_utf8() -> None:
    labels = [Label("4000", "Ã¼ber"), Label("4001", "Büro")]
    _, read = extf.read_account_labels(extf.write_account_labels(HEADER, labels))
    assert [label.label for label in read] == ["Ã¼ber", "Büro"]


def test_a_label_cut_in_the_short_column_is_judged_by_its_written_bytes() -> None:
    # Cut between its two characters, the UTF-8 sequence does not survive in the short column.
    label = "x" * 39 + "Ã¼ber"
    written = extf.write_account_labels(HEADER, [Label("4000", label)])
    assert '"' + "x" * 39 + 'Ã";' in written.decode("cp1252")
    assert extf.read_account_labels(written)[1][0].label == label
    # Cut behind them, it does.
    with pytest.raises(ExtfError, match="read back as UTF-8"):
        extf.write_account_labels(HEADER, [Label("4000", "x" * 38 + "Ã¼ber")])
    # Only the cut-off part breaks the UTF-8.
    assert extf.write_account_labels(HEADER, [Label("4000", "x" * 38 + "Ã¼" + "ü")])


def test_a_character_outside_windows_1252_leaves_no_file_to_judge() -> None:
    with pytest.raises(ExtfError) as caught:
        extf.write_account_labels(HEADER, [Label("4000", "Ã¼ber"), Label("4001", "ř")])
    assert [error.message for error in caught.value.errors] == ["Kontenbeschriftung holds characters outside Windows-1252"]


def test_labels_and_personal_accounts_are_validated() -> None:
    with pytest.raises(ExtfError) as caught:
        extf.write_account_labels(HEADER, [Label("12000", "x"), Label("1200", ""), Label("1200", "y"), Label("1201", "z" * 301)])
    assert [error.line for error in caught.value.errors] == [3, 4, 5, 6]
    with pytest.raises(ExtfError) as caught:
        extf.write_personal_accounts(HEADER, [Personal("1000", "x"), Personal("10000", ""), Personal("10000", "ř")])
    assert [error.line for error in caught.value.errors] == [3, 4, 5, 5]
    assert all(error.file == "Debitoren/Kreditoren" for error in caught.value.errors)



def test_the_amounts_do_not_depend_on_the_callers_decimal_context() -> None:
    with localcontext() as context:
        context.prec = 3
        assert extf.write_bookings(HEADER, RECORDS) == (GOLDEN / "bookings.csv").read_bytes()
        assert extf.amount_de(Decimal("123456.785")) == "123456,79"


def test_document_numbers_and_texts_are_made_fit_for_the_format() -> None:
    assert extf.document_number("RE 2026.001_ä") == "RE-2026-001-ae"
    assert extf.document_number("A/1+2%") == "A/1+2%"
    assert extf.document_number("  ") is None
    assert extf.clean_text("Dvořák\n€ 5 —  ok") == "Dvorák € 5 — ok"
    assert extf.clean_text("日本") == "??"
    assert extf.clean_text(None) == ""
    assert extf.amount_de(Decimal("1234.5")) == "1234,50"
