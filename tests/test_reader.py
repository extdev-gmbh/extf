"""The readers: bookings, labels, personal accounts, trial balances and fixed assets."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
import synthetic as f

import extf
from extf import DatevFormatError


def stapel() -> bytes:
    return f.buchungsstapel([f.booking("1,00", "S", "1200", "1360", "0101")])


def test_a_buchungsstapel_is_read_with_sides_dates_and_texts() -> None:
    record = f.booking("1.234,50", "H", "70001", "4806", "3112", key="9", number="H-1", text='Füße; "Spezial"')
    data = f.buchungsstapel([record], fiscal_year_start=date(2025, 1, 1), date_from=date(2025, 12, 1), date_to=date(2025, 12, 31))
    read = extf.read_bookings(data, "stapel.csv")
    assert read.header.fiscal_year == 2025
    assert (read.header.date_from, read.header.date_to) == (date(2025, 12, 1), date(2025, 12, 31))
    (booking,) = read.bookings
    assert booking.amount == Decimal("1234.50")
    # H: the Konto is credited, the Gegenkonto debited.
    assert (booking.debit_account, booking.credit_account, booking.contra_account) == ("4806", "70001", "4806")
    assert booking.tax_key == "9"
    assert booking.booking_date == date(2025, 12, 31)
    assert booking.text == 'Füße; "Spezial"'
    assert (booking.file, booking.line) == ("stapel.csv", 3)


def test_belegdatum_takes_the_year_of_the_period_across_a_year_end() -> None:
    data = f.buchungsstapel(
        [f.booking("1,00", "S", "1200", "1360", "0501")],
        fiscal_year_start=date(2025, 7, 1),
        date_from=date(2025, 7, 1),
        date_to=date(2026, 6, 30),
    )
    assert extf.read_bookings(data).bookings[0].booking_date == date(2026, 1, 5)


def test_every_bad_line_is_named_and_nothing_is_read() -> None:
    data = f.buchungsstapel(
        [
            f.booking("12.5", "S", "1200", "1360", "0101"),
            f.booking("1,00", "X", "1200", "1360", "0101"),
            f.booking("1,00", "S", "1200", "1360", "3102"),
            f.booking("1,00", "S", "1200", "1200", "0101"),
            f.booking("1,00", "S", "1200", "1360", "0101"),
        ]
    )
    with pytest.raises(DatevFormatError) as refused:
        extf.read_bookings(data, "bad.csv")
    lines = {error.line: error.message for error in refused.value.errors}
    assert sorted(lines) == [3, 4, 5, 6]
    assert "decimal comma" in lines[3]
    assert "neither S nor H" in lines[4]
    assert "no day of the fiscal year 2026-01-01 – 2026-12-31" in lines[5]
    assert "both 1200" in lines[6]
    assert all(error.file == "bad.csv" for error in refused.value.errors)


@pytest.mark.parametrize(
    ("data", "message"),
    [
        (f.kontenbeschriftungen([("1200", "Bank")]), "expected 21"),
        (f.buchungsstapel([], account_length=9), "Sachkontenlänge"),
        (f.buchungsstapel([], account_length=3), "Sachkontenlänge"),
        (b"Konto;Name\r\n", "no DATEV header"),
        (f.buchungsstapel([], columns=("Umsatz (ohne Soll/Haben-Kz)", "Konto")), "column(s) missing"),
        ("ä".encode() + b"\x81", "Windows-1252"),
        (b"", "the file is empty"),
        (stapel().replace(b"\r\n", b"", 1).split(b'"EUR"')[0], "line 2"),
    ],
)
def test_a_file_that_is_no_buchungsstapel_is_refused(data: bytes, message: str) -> None:
    with pytest.raises(DatevFormatError, match=message.replace("(", r"\(").replace(")", r"\)")):
        extf.read_bookings(data)


@pytest.mark.parametrize("version", [b"499", b"701", b"", b"7_0_0", b"x"])
def test_a_format_version_outside_the_range_is_refused(version: bytes) -> None:
    with pytest.raises(DatevFormatError, match="format version"):
        extf.read_bookings(stapel().replace(b'"EXTF";700;', b'"EXTF";' + version + b";", 1))


def test_the_account_length_comes_from_the_header() -> None:
    record = f.booking("1,00", "S", "120000", "1360", "0101")
    (booking,) = extf.read_bookings(f.buchungsstapel([record], account_length=6)).bookings
    assert (booking.debit_account, booking.credit_account) == ("120000", "001360")
    _, labels = extf.read_account_labels(f.kontenbeschriftungen([("120000", "Bank")], account_length=6))
    assert labels[0].number == "120000"


def test_a_foreign_currency_needs_its_euro_amount() -> None:
    usd = f.booking("110,00", "S", "4806", "1200", "1501", key="", currency="USD")
    with pytest.raises(DatevFormatError, match="Basis-Umsatz"):
        extf.read_bookings(f.buchungsstapel([usd]))
    usd = f.booking("110,00", "S", "4806", "1200", "1501", currency="USD", base="100,00", rate="1,1")
    booking = extf.read_bookings(f.buchungsstapel([usd])).bookings[0]
    assert (booking.amount, booking.currency, booking.foreign_amount, booking.fx_rate) == (
        Decimal("100.00"),
        "USD",
        Decimal("110.00"),
        "1.1",
    )


def test_account_labels_personal_accounts_trial_balance_and_assets_are_read() -> None:
    _, labels = extf.read_account_labels(f.kontenbeschriftungen([("1201", "Tagesgeld"), ("730", "Darlehen")]))
    assert [(label.number, label.label) for label in labels] == [("1201", "Tagesgeld"), ("0730", "Darlehen")]
    _, personal = extf.read_personal_accounts(f.debitoren_kreditoren([("70001", "Muster Service AG", "AT", "U00000000")]))
    assert [(item.number, item.name, item.vat_id) for item in personal] == [("70001", "Muster Service AG", "ATU00000000")]
    balances = extf.read_trial_balance(f.saldenliste([("1200", "1.000,50", ""), ("0800", "", "1.000,50")]))
    assert [(line.account, line.balance) for line in balances] == [("1200", Decimal("1000.50")), ("0800", Decimal("-1000.50"))]
    signed = extf.read_trial_balance(b"Konto;Saldo;S/H\n1200;10,00;S\n0800;10,00;H\n")
    assert [line.balance for line in signed] == [Decimal("10.00"), Decimal("-10.00")]
    assets = extf.read_fixed_assets(f.anlagenverzeichnis([("A1", "Notebook", "0420", "15.03.2024", "2499,00", "3", "833,00")]))
    assert assets[0].acquired_on == date(2024, 3, 15)
    assert (assets[0].acquisition_cost, assets[0].useful_life_years, assets[0].book_value) == (
        Decimal("2499.00"),
        3,
        Decimal("833.00"),
    )


def test_the_lists_take_the_account_length_from_the_caller() -> None:
    balances = extf.read_trial_balance(f.saldenliste([("120000", "1,00", "")]), account_length=6)
    assert balances[0].account == "120000"
    with pytest.raises(DatevFormatError, match="is no account of 4 or 5 digits"):
        extf.read_trial_balance(f.saldenliste([("120000", "1,00", "")]))
    assets = f.anlagenverzeichnis([("A1", "Notebook", "420", "15.03.2024", "2499,00", "3", "833,00")])
    assert extf.read_fixed_assets(assets, account_length=6)[0].account == "000420"
    _, accounts = extf.read_personal_accounts(f.debitoren_kreditoren_liste([("1000140", "Muster AG", "", "")]), account_length=6)
    assert accounts[0].number == "1000140"


def test_a_trial_balance_line_with_debit_and_credit_is_refused() -> None:
    with pytest.raises(DatevFormatError, match="debit and a credit"):
        extf.read_trial_balance(f.saldenliste([("1200", "1,00", "2,00")]))


def test_a_generalumkehr_carries_its_side_and_the_old_notation_swaps() -> None:
    original, column, digit = extf.read_bookings(
        f.buchungsstapel(
            [
                f.booking("119,00", "H", "70001", "4806", "0101", key="9"),
                # The cancellation with the side it books, marked GU.
                f.booking("119,00", "S", "70001", "4806", "0201", key="9", reversal="1"),
                f.booking("119,00", "H", "70001", "4806", "0301", key="29"),
            ]
        )
    ).bookings
    assert (original.debit_account, original.credit_account, original.reversal) == ("4806", "70001", False)
    assert (column.debit_account, column.credit_account, column.tax_key, column.reversal) == ("70001", "4806", "9", True)
    # Correction digit 2 in front of the tax key (unproven): swapped, key 9.
    assert (digit.debit_account, digit.credit_account, digit.tax_key, digit.reversal) == ("70001", "4806", "9", True)
    (no_key,) = extf.read_bookings(f.buchungsstapel([f.booking("10,00", "S", "1200", "1360", "0101", key="20")])).bookings
    assert (no_key.debit_account, no_key.tax_key, no_key.reversal) == ("1360", None, True)


def test_skonto_and_an_unclear_generalumkehr_are_bad_lines() -> None:
    data = f.buchungsstapel(
        [
            f.booking("119,00", "S", "70001", "1200", "0101", discount="2,38"),
            f.booking("119,00", "H", "70001", "4806", "0101", key="29", reversal="1"),
            f.booking("119,00", "H", "70001", "4806", "0101", reversal="2"),
            f.booking("119,00", "S", "70001", "1200", "0101", discount="0,00"),
        ]
    )
    with pytest.raises(DatevFormatError) as refused:
        extf.read_bookings(data, "gu.csv")
    lines = {error.line: error.message for error in refused.value.errors}
    assert sorted(lines) == [3, 4, 5]
    assert "Skonto is not supported" in lines[3]
    assert "both mark a Generalumkehr" in lines[4]
    assert "neither 0 nor 1" in lines[5]


def test_the_total_rows_of_a_trial_balance_are_skipped() -> None:
    data = f.saldenliste(
        [("1200", "1.000,50", ""), ("0800", "", "1.000,50")], totals=[("Summe Klasse 0", "", "1.000,50"), ("Summe", "1.000,50", "1.000,50")]
    )
    assert [(line.account, line.balance) for line in extf.read_trial_balance(data)] == [
        ("1200", Decimal("1000.50")),
        ("0800", Decimal("-1000.50")),
    ]


def test_a_kanzlei_trial_balance_reads_the_side_of_each_amount() -> None:
    data = f.saldenliste_kanzlei(
        [
            ("730", "Darlehen", "20.000,00", "20.060,00", "HH"),
            ("1200", "Bank", "1.000,00", "13.725,56", "SS"),
            ("1776", "Umsatzsteuer 19%", "", "8.077,41", "-H"),
            ("9000", "Saldenvorträge", "", "0,00", "--"),
            ("70002", "Sammellieferant", "467,28", "325,03", "HH"),
        ]
    )
    read = {line.account: (line.balance, line.opening, line.label) for line in extf.read_trial_balance(data)}
    assert read == {
        "0730": (Decimal("-20060.00"), Decimal("-20000.00"), "Darlehen"),
        "1200": (Decimal("13725.56"), Decimal("1000.00"), "Bank"),
        "1776": (Decimal("-8077.41"), Decimal(0), "Umsatzsteuer 19%"),
        "9000": (Decimal(0), Decimal(0), "Saldenvorträge"),
        "70002": (Decimal("-325.03"), Decimal("-467.28"), "Sammellieferant"),
    }


def test_a_kanzlei_trial_balance_line_without_a_clear_side_is_refused() -> None:
    data = f.saldenliste_kanzlei(
        [
            ("1200", "Bank", "", "100,00", "--"),
            ("1201", "Tagesgeld", "", "100,00", "-B"),
            ("1360", "Geldtransit", "", "", "S-"),
            ("1400", "Forderungen", "", "5,00", "-S"),
        ]
    )
    with pytest.raises(DatevFormatError) as refused:
        extf.read_trial_balance(data, "susa.csv")
    messages = [error.message for error in refused.value.errors]
    assert len(messages) == 3
    assert "Saldo of account 1200 100,00 is marked neither S nor H" in messages[0]
    assert "Saldo of account 1201 100,00 is marked S and H" in messages[1]
    assert "EB-Wert of account 1360: a side mark without an amount" in messages[2]


def test_a_late_entry_is_read_on_its_day_of_the_fiscal_year() -> None:
    april = f.buchungsstapel([f.booking("1,00", "S", "1200", "1360", "0202")], date_from=date(2026, 4, 1), date_to=date(2026, 4, 30))
    assert extf.read_bookings(april).bookings[0].booking_date == date(2026, 2, 2)
    # A fiscal year from 01.07.: the year of the day is the one that puts it into the fiscal year.
    shifted = f.buchungsstapel(
        [f.booking("1,00", "S", "1200", "1360", "0508"), f.booking("1,00", "S", "1200", "1360", "0203")],
        fiscal_year_start=date(2025, 7, 1),
        date_from=date(2026, 4, 1),
        date_to=date(2026, 4, 30),
    )
    assert [b.booking_date for b in extf.read_bookings(shifted).bookings] == [date(2025, 8, 5), date(2026, 3, 2)]
    leap = f.buchungsstapel(
        [f.booking("1,00", "S", "1200", "1360", "2902")], fiscal_year_start=date(2025, 7, 1), date_from=date(2026, 2, 1), date_to=date(2026, 2, 28)
    )
    with pytest.raises(DatevFormatError, match="no day of the fiscal year 2025-07-01 – 2026-06-30"):
        extf.read_bookings(leap)


def test_the_kanzlei_list_of_personal_accounts_is_read() -> None:
    header, accounts = extf.read_personal_accounts(
        f.debitoren_kreditoren_liste([("10014", "Muster Verband", "DE 000 000 000", ""), ("70026", "Mustermann, Erika", "", "K-7")])
    )
    assert header is None
    assert [(a.number, a.name, a.vat_id, a.customer_number) for a in accounts] == [
        ("10014", "Muster Verband", "DE000000000", None),
        ("70026", "Mustermann, Erika", None, "K-7"),
    ]
    with pytest.raises(DatevFormatError, match=r"no DATEV header .* and no header row with the column Konto"):
        extf.read_personal_accounts(b"Name;Ort\r\nA;B\r\n")


def test_is_datev_format_and_fiscal_year_end() -> None:
    assert extf.is_datev_format(stapel()) and extf.is_datev_format(b'\xef\xbb\xbf"DTVF";700')
    assert not extf.is_datev_format(b"Konto;Name")
    assert extf.fiscal_year_end(date(2026, 1, 1)) == date(2026, 12, 31)
    assert extf.fiscal_year_end(date(2024, 2, 29)) == date(2025, 2, 28)
    assert extf.fiscal_year_end(date(9996, 2, 29)) == date(9997, 2, 28)
    assert extf.fiscal_year_end(date(9998, 12, 31)) == date(9999, 12, 30)
    assert extf.fiscal_year_end(date(9999, 1, 1)) == date(9999, 12, 31)


@pytest.mark.parametrize("start", [date(9999, 1, 2), date(9999, 7, 1), date(9999, 12, 31)])
def test_fiscal_year_end_refuses_a_year_ending_after_year_9999(start: date) -> None:
    with pytest.raises(ValueError, match=rf"starting on {start.isoformat()} would end after year 9999"):
        extf.fiscal_year_end(start)


def test_a_record_of_a_fiscal_year_ending_after_year_9999_is_a_bad_line() -> None:
    start = date(9999, 7, 1)
    data = f.buchungsstapel([f.booking("1,00", "S", "1200", "1360", "0107")], fiscal_year_start=start, date_from=start, date_to=date.max)
    with pytest.raises(DatevFormatError, match=r"line 3: the fiscal year starting on 9999-07-01 would end after year 9999") as caught:
        extf.read_bookings(data, "stapel.csv")
    assert type(caught.value) is DatevFormatError


def test_parse_amount() -> None:
    assert extf.parse_amount("1.234,56") == Decimal("1234.56")
    assert extf.parse_amount("-1,00", signed=True) == Decimal("-1.00")
    for bad in ("-1,00", "1,234", "1.2", "", "1e3", "NaN", "999999999999999,00"):
        with pytest.raises(ValueError):
            extf.parse_amount(bad)
