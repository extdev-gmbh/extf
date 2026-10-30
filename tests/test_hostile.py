"""Hostile input: the limits, and properties over random bytes and random records."""

from __future__ import annotations

import csv
import tracemalloc
from collections.abc import Callable, Iterator
from dataclasses import replace
from datetime import date, datetime, timedelta
from decimal import Decimal, localcontext
from typing import Any

import pytest
import synthetic as f
from hypothesis import HealthCheck, example, given, settings
from hypothesis import strategies as st

import extf
import extf._reader
from extf import DatevFormatError, ExportHeader, ExtfError, Label, Personal, Record

READERS: tuple[Callable[[bytes], Any], ...] = (
    extf.read_bookings,
    extf.read_account_labels,
    extf.read_personal_accounts,
    extf.read_trial_balance,
    extf.read_fixed_assets,
)
READER_IDS = ["bookings", "labels", "personal", "trial_balance", "assets"]
HEADER = ExportHeader(
    advisor_number=1234567,
    client_number=10001,
    fiscal_year_start=date(2026, 1, 1),
    account_length=4,
    origin="XT",
    exported_by="extf tests",
    chart="03",
    created=datetime(2026, 8, 3, 9, 15),
    date_from=date(2026, 1, 1),
    date_to=date(2026, 12, 31),
)


def stapel(count: int = 1) -> bytes:
    return f.buchungsstapel([f.booking("1,00", "S", "1200", "1360", "0101")] * count)


@pytest.mark.parametrize("read", READERS, ids=READER_IDS)
def test_an_input_above_the_size_limit_is_refused_unread(read: Callable[[bytes], Any], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("extf._reader.MAX_INPUT_BYTES", 8)
    with pytest.raises(DatevFormatError, match="at most 8"):
        read(b'"EXTF";700;21;' * 2)


def test_the_real_size_limit_is_enforced() -> None:
    with pytest.raises(DatevFormatError, match="bytes, at most"):
        extf.read_trial_balance(b"x" * (extf.MAX_INPUT_BYTES + 1))


def test_too_many_lines_are_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("extf._reader.MAX_LINES", 10)
    with pytest.raises(DatevFormatError, match="more than 10 lines"):
        extf.read_bookings(stapel(20))
    assert len(extf.read_bookings(stapel(8)).bookings) == 8


def test_too_many_columns_and_overlong_fields_are_refused() -> None:
    with pytest.raises(DatevFormatError, match=f"more than {extf.MAX_COLUMNS} columns"):
        extf.read_trial_balance(b"Konto;Saldo\n" + b";" * extf.MAX_COLUMNS + b"\n")
    with pytest.raises(DatevFormatError, match=f"more than {extf.MAX_FIELD_LENGTH} characters"):
        extf.read_trial_balance(b"Konto;Saldo\n1200;" + b"1" * (extf.MAX_FIELD_LENGTH + 1) + b"\n")
    # Beyond csv's own field limit, too.
    with pytest.raises(DatevFormatError):
        extf.read_trial_balance(b"Konto;Saldo\n1200;" + b"1" * 200_000 + b"\n")


@pytest.mark.parametrize(
    ("data", "message"),
    [
        (f.buchungsstapel([f.booking("1,00", "S", "1200", "1360", "0101", text="x" * 61)]), "Buchungstext has more than 60"),
        (f.buchungsstapel([f.booking("1,00", "S", "1200", "1360", "0101", number="1" * 37)]), "Belegfeld 1 has more than 36"),
    ],
)
def test_a_field_beyond_its_column_is_a_bad_line(data: bytes, message: str) -> None:
    with pytest.raises(DatevFormatError, match=message):
        extf.read_bookings(data)


def test_utf8_is_not_read_as_windows_1252() -> None:
    utf8 = stapel().decode("cp1252").replace('"Buchungen"', '"Büchungen"').replace("Schlüssel", "Schluessel").encode("utf-8")
    with pytest.raises(DatevFormatError, match="is UTF-8"):
        extf.read_bookings(utf8)
    with pytest.raises(DatevFormatError, match="byte order mark"):
        extf.read_bookings(b"\xef\xbb\xbf" + stapel())
    with pytest.raises(DatevFormatError, match="not Windows-1252"):
        extf.read_bookings(stapel().replace(b"\r\n", b"\r\n\x81", 1))


def test_an_unterminated_quote_is_refused_as_a_format_error() -> None:
    with pytest.raises(DatevFormatError):
        extf.read_bookings(stapel() + b'"open')


def test_the_number_of_errors_is_bounded() -> None:
    data = f.buchungsstapel([f.booking("x", "S", "1200", "1360", "0101")] * 1000)
    with pytest.raises(DatevFormatError) as refused:
        extf.read_bookings(data)
    assert len(refused.value.errors) == 200


@pytest.fixture
def rows_read(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """Counts the rows the reader takes from the file: one entry per row."""
    counted: list[int] = []
    rows = extf._reader._rows

    def counting(text: str, file: str) -> Iterator[tuple[int, list[str]]]:
        for line, row in rows(text, file):
            counted.append(line)
            yield line, row

    monkeypatch.setattr("extf._reader._rows", counting)
    return counted


BAD_BOOKING = f.booking("x", "S", "1200", "1360", "0101")
#: 1000 bad records of each reader, with what comes before the first of them in the file.
BAD_FILES: list[tuple[Callable[[bytes], Any], bytes, int]] = [
    (extf.read_bookings, f.buchungsstapel([BAD_BOOKING] * 1000), 2),
    (extf.read_account_labels, f.kontenbeschriftungen([("x", "Kasse")] * 1000), 2),
    (extf.read_personal_accounts, f.debitoren_kreditoren([("x", "Name", "", "")] * 1000), 2),
    (extf.read_personal_accounts, b"Konto;Beschriftung\n" + b"x;Name\n" * 1000, 1),
    (extf.read_trial_balance, b"Konto;Saldo\n" + b"x;1,00\n" * 1000, 1),
    (extf.read_fixed_assets, b"Konto;Inventarnummer;Bezeichnung;Anschaffungsdatum;Anschaffungskosten\n" + b"x;1;Name;01.01.2026;1,00\n" * 1000, 1),
]
BAD_FILE_IDS = ["bookings", "labels", "personal", "personal_list", "trial_balance", "assets"]


@pytest.mark.parametrize(("read", "data", "before"), BAD_FILES, ids=BAD_FILE_IDS)
def test_reading_stops_at_the_200th_bad_line(read: Callable[[bytes], Any], data: bytes, before: int, rows_read: list[int]) -> None:
    with pytest.raises(DatevFormatError) as refused:
        read(data)
    assert len(refused.value.errors) == 200
    assert len(rows_read) == before + 200


@pytest.mark.parametrize(("read", "data", "before"), BAD_FILES, ids=BAD_FILE_IDS)
def test_what_follows_the_200th_bad_line_is_not_looked_at(read: Callable[[bytes], Any], data: bytes, before: int) -> None:
    with pytest.raises(DatevFormatError) as refused:
        read(data + b'"open')
    assert len(refused.value.errors) == 200
    assert refused.value.errors[-1].line == before + 200


def test_blank_rows_are_skipped_on_the_way_and_are_no_bad_lines(rows_read: list[int]) -> None:
    data = b"Konto;Saldo\n" + b"; ;\n\nx;1,00\n" * 1000
    with pytest.raises(DatevFormatError) as refused:
        extf.read_trial_balance(data)
    assert [error.line for error in refused.value.errors] == list(range(4, 604, 3))
    assert len(rows_read) == 1 + 3 * 200


def test_a_list_without_a_header_row_is_not_held_in_memory() -> None:
    """The worst case: every row of the search is read, none is kept. Its cells would take
    about four times the peak of the decoded text and its buffer."""
    data = (b"xx;" * 511 + b"xx\n") * 700
    tracemalloc.start()
    try:
        with pytest.raises(DatevFormatError, match="no header row"):
            extf.read_trial_balance(data)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < 8 * len(data)


_NO_HEADER_ROW = "no header row with the column Konto"
#: The readers of a list, with a list each reads and what each refuses a list without a header row with.
LISTS: list[tuple[Callable[[bytes], Any], bytes, str]] = [
    (extf.read_trial_balance, b"Konto;Saldo\n1200;1,00\n", _NO_HEADER_ROW),
    (
        extf.read_fixed_assets,
        b"Konto;Inventarnummer;Bezeichnung;Anschaffungsdatum;Anschaffungskosten\n0420;1;Name;01.01.2026;1,00\n",
        _NO_HEADER_ROW,
    ),
    (
        extf.read_personal_accounts,
        b"Konto;Beschriftung\n70001;Name\n",
        f"no DATEV header (line 1 starts with neither EXTF nor DTVF) and {_NO_HEADER_ROW}",
    ),
]
LIST_IDS = ["trial_balance", "assets", "personal"]
#: Lists without a header row, far beyond the search, each costly in its own way.
NO_HEADER_ROW: list[bytes] = [
    (b"xx;" * 511 + b"xx\n") * 2000,
    (b"xxxxxx;" * 511 + b"xxxxxx\n") * 1000,
    (b";".join([b"-" * extf.MAX_FIELD_LENGTH] * 512) + b"\n") * 2,
    (b";" * 511 + b"\n") * 2000,
    b";\n" * extf.MAX_HEADER_SEARCH,
]
NO_HEADER_ROW_IDS = ["short_cells", "longer_cells", "longest_cells", "empty_cells", "two_empty_cells"]


def _other_rows(characters: int) -> bytes:
    """Rows of exactly ``characters`` characters, line ends included, none naming ``Konto``."""
    rows, rest = divmod(characters, 1024)
    return (b"x" * 1023 + b"\n") * rows + (b"x" * (rest - 1) + b"\n" if rest else b"")


@pytest.fixture
def normalized(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Every cell that goes through ``_norm``, in order."""
    cells: list[str] = []
    norm = extf._reader._norm

    def counting(name: str) -> str:
        cells.append(name)
        return norm(name)

    monkeypatch.setattr("extf._reader._norm", counting)
    return cells


@pytest.mark.parametrize(("read", "_data", "message"), LISTS, ids=LIST_IDS)
@pytest.mark.parametrize("hostile", NO_HEADER_ROW, ids=NO_HEADER_ROW_IDS)
def test_the_search_for_the_header_row_is_bounded(
    read: Callable[[bytes], Any], _data: bytes, message: str, hostile: bytes, normalized: list[str]
) -> None:
    with pytest.raises(DatevFormatError) as refused:
        read(hostile)
    assert [error.message for error in refused.value.errors] == [message]
    assert sum(len(cell) + 1 for cell in normalized) <= extf.MAX_HEADER_SEARCH


@pytest.mark.parametrize(("read", "data", "message"), LISTS, ids=LIST_IDS)
def test_a_header_row_ending_at_the_limit_is_found_and_one_character_later_is_not(
    read: Callable[[bytes], Any], data: bytes, message: str
) -> None:
    header_row = data.index(b"\n") + 1
    assert read(_other_rows(extf.MAX_HEADER_SEARCH - header_row) + data)
    with pytest.raises(DatevFormatError) as refused:
        read(_other_rows(extf.MAX_HEADER_SEARCH - header_row + 1) + data)
    assert [error.message for error in refused.value.errors] == [message]


#: A violation of a limit, written after the good records, and what it is refused with.
LATE_VIOLATIONS: list[tuple[bytes, str]] = [
    (b";" * extf.MAX_COLUMNS, "columns"),
    (b"1" * (extf.MAX_FIELD_LENGTH + 1), "characters"),
    (b'"open', "not readable as CSV"),
]
LATE_VIOLATION_IDS = ["columns", "field", "quote"]


@pytest.mark.parametrize(("violation", "message"), LATE_VIOLATIONS, ids=LATE_VIOLATION_IDS)
@pytest.mark.parametrize("good", [0, 1, 500])
def test_a_limit_after_good_rows_refuses_a_datev_file_as_a_whole(violation: bytes, message: str, good: int) -> None:
    with pytest.raises(DatevFormatError, match=message) as refused:
        extf.read_bookings(stapel(good) + violation)
    assert [error.line for error in refused.value.errors] == [good + 3]


@pytest.mark.parametrize(("violation", "message"), LATE_VIOLATIONS, ids=LATE_VIOLATION_IDS)
@pytest.mark.parametrize("good", [0, 1, 500])
def test_a_limit_after_good_rows_refuses_a_list_as_a_whole(violation: bytes, message: str, good: int) -> None:
    rows = b"".join(b"%04d;1,00\n" % (number + 1) for number in range(good))
    with pytest.raises(DatevFormatError, match=message) as refused:
        extf.read_trial_balance(b"Konto;Saldo\n" + rows + violation)
    assert [error.line for error in refused.value.errors] == [good + 2]


@pytest.mark.parametrize(("violation", "message"), LATE_VIOLATIONS, ids=LATE_VIOLATION_IDS)
@pytest.mark.parametrize("bad", [1, 5, 199])
def test_a_limit_after_fewer_than_200_bad_lines_is_what_is_reported(violation: bytes, message: str, bad: int) -> None:
    with pytest.raises(DatevFormatError, match=message) as refused:
        extf.read_bookings(f.buchungsstapel([BAD_BOOKING] * bad).rstrip(b"\r\n") + b"\r\n" + violation)
    assert [error.line for error in refused.value.errors] == [bad + 3]
    with pytest.raises(DatevFormatError, match=message) as refused:
        extf.read_trial_balance(b"Konto;Saldo\n" + b"x;1,00\n" * bad + violation)
    assert [error.line for error in refused.value.errors] == [bad + 2]


@pytest.mark.parametrize("good", [1, 8])
def test_too_many_lines_after_good_rows_refuse_a_file_as_a_whole(good: int, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("extf._reader.MAX_LINES", good + 2)
    with pytest.raises(DatevFormatError, match=f"more than {good + 2} lines"):
        extf.read_bookings(stapel(good + 1))
    rows = b"".join(b"%04d;1,00\n" % (number + 1) for number in range(good + 2))
    with pytest.raises(DatevFormatError, match=f"more than {good + 2} lines"):
        extf.read_trial_balance(b"Konto;Saldo\n" + rows)


_ROW_REFUSED = f"the line has more than {extf.MAX_COLUMNS} columns or a field of more than {extf.MAX_FIELD_LENGTH} characters"
#: Every path through ``_rows``, with what comes before a row and the line that row starts on.
BOUND_PATHS: list[tuple[Callable[[bytes], Any], bytes, int]] = [
    (extf.read_bookings, stapel(1), 4),
    (extf.read_account_labels, f.kontenbeschriftungen([("1200", "Bank")]), 4),
    (extf.read_personal_accounts, f.debitoren_kreditoren([("70001", "Name", "", "")]), 4),
    (extf.read_personal_accounts, b"Konto;Beschriftung\n70001;Name\n", 3),
    (extf.read_trial_balance, b"Konto;Saldo\n1200;1,00\n", 3),
    (extf.read_fixed_assets, b"Konto;Inventarnummer;Bezeichnung;Anschaffungsdatum;Anschaffungskosten\n0420;1;Name;01.01.2026;1,00\n", 3),
]
BOUND_PATH_IDS = ["bookings", "labels", "personal", "personal_list", "trial_balance", "assets"]
_LONGEST_CELL = '"' + '""' * extf.MAX_FIELD_LENGTH + '"'
_LONGEST_ROW = ";".join([_LONGEST_CELL] * extf.MAX_COLUMNS) + "\r\n"
#: Each over the bound in its own way: on one line, or spread over many.
OVER_THE_BOUND: list[bytes] = [
    b";" * (extf._reader._MAX_ROW_CHARS + 1),
    b'"";' * extf._reader._MAX_ROW_CHARS,
    b'"\n";' * extf._reader._MAX_ROW_CHARS,
    b"a" * (extf._reader._MAX_ROW_CHARS + 1),
]
OVER_THE_BOUND_IDS = ["separators", "empty_quoted", "lines_in_quotes", "long_cell"]


@pytest.fixture
def handed_to_csv(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """The length of every line ``csv.reader`` is handed."""
    lengths: list[int] = []
    reader = csv.reader

    def counting(lines: Iterator[str], **options: Any) -> Any:
        def counted() -> Iterator[str]:
            for line in lines:
                lengths.append(len(line))
                yield line

        return reader(counted(), **options)

    monkeypatch.setattr("extf._reader.csv.reader", counting)
    return lengths


def test_the_bound_on_a_row_is_the_longest_row_within_the_limits() -> None:
    assert extf._reader._MAX_ROW_CHARS == 4_195_841 == len(_LONGEST_ROW)


def test_the_longest_valid_row_reads_as_before() -> None:
    assert list(extf._reader._rows(_LONGEST_ROW, "f")) == [(1, ['"' * extf.MAX_FIELD_LENGTH] * extf.MAX_COLUMNS)]


def test_a_list_with_a_row_near_the_bound_is_read() -> None:
    names = ";".join(f"Notiz {number}" for number in range(extf.MAX_COLUMNS - 2))
    row = ";".join([_LONGEST_CELL] * (extf.MAX_COLUMNS - 2))
    balances = extf.read_trial_balance(f"Konto;Saldo;{names}\r\n1200;1,00;{row}\r\n".encode())
    assert [(balance.account, balance.balance) for balance in balances] == [("1200", Decimal("1.00"))]


@pytest.mark.parametrize(("read", "before", "line"), BOUND_PATHS, ids=BOUND_PATH_IDS)
def test_one_character_over_the_bound_is_refused_on_the_line_of_the_row(read: Callable[[bytes], Any], before: bytes, line: int) -> None:
    with pytest.raises(DatevFormatError) as refused:
        read(before + b"x" + _LONGEST_ROW.encode())
    assert [(error.line, error.message) for error in refused.value.errors] == [(line, _ROW_REFUSED)]


@pytest.mark.parametrize(("read", "before", "line"), BOUND_PATHS, ids=BOUND_PATH_IDS)
def test_the_bound_is_exact_on_a_row_over_several_lines(read: Callable[[bytes], Any], before: bytes, line: int) -> None:
    def row(characters: int) -> bytes:
        """Many short cells, each with a line end: a single long cell would stop at csv's own limit."""
        cells, rest = divmod(characters - 2, len(b'"xxxxxx\n";'))
        return b";" * rest + b'"xxxxxx\n";' * cells + b"\r\n"

    with pytest.raises(DatevFormatError) as refused:
        read(before + row(extf._reader._MAX_ROW_CHARS))
    assert [error.message for error in refused.value.errors] == [f"the line has more than {extf.MAX_COLUMNS} columns"]
    with pytest.raises(DatevFormatError) as refused:
        read(before + row(extf._reader._MAX_ROW_CHARS + 1))
    assert [(error.line, error.message) for error in refused.value.errors] == [(line, _ROW_REFUSED)]


def test_the_bound_counts_per_row() -> None:
    """Many rows, each under the bound and together far over it."""
    cell = b"x" * extf.MAX_FIELD_LENGTH
    rows = b"".join(b"%04d;1,00;%s\n" % (number + 1000, cell) for number in range(2 * extf._reader._MAX_ROW_CHARS // len(cell)))
    assert len(extf.read_trial_balance(b"Konto;Saldo;Notiz\n" + rows)) == 2 * extf._reader._MAX_ROW_CHARS // len(cell)


@pytest.mark.parametrize("hostile", OVER_THE_BOUND, ids=OVER_THE_BOUND_IDS)
@pytest.mark.parametrize(("read", "before", "line"), BOUND_PATHS, ids=BOUND_PATH_IDS)
def test_the_parser_stops_at_the_bound(
    read: Callable[[bytes], Any], before: bytes, line: int, hostile: bytes, handed_to_csv: list[int]
) -> None:
    with pytest.raises(DatevFormatError) as refused:
        read(before + hostile)
    assert [(error.line, error.message) for error in refused.value.errors] == [(line, _ROW_REFUSED)]
    assert sum(handed_to_csv) <= len(before) + extf._reader._MAX_ROW_CHARS


@settings(max_examples=300, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(rows=st.lists(st.lists(st.text('";a\r\n', max_size=3), min_size=1, max_size=3), min_size=1, max_size=4))
def test_the_bound_refuses_no_row_within_the_limits(rows: list[list[str]], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("extf._reader.MAX_COLUMNS", 3)
    monkeypatch.setattr("extf._reader.MAX_FIELD_LENGTH", 3)
    monkeypatch.setattr("extf._reader._MAX_ROW_CHARS", 3 * (2 * 3 + 3) + 1)
    text = "".join(";".join('"' + cell.replace('"', '""') + '"' for cell in row) + "\r\n" for row in rows)
    assert [row for _line, row in extf._reader._rows(text, "f")] == rows


def _survives(read: Callable[[bytes], Any], data: bytes) -> None:
    try:
        read(data)
    except ExtfError as exc:
        assert exc.errors
        assert str(exc)


@pytest.mark.parametrize("read", READERS, ids=READER_IDS)
@settings(max_examples=300, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(data=st.binary(max_size=2048))
def test_random_bytes_never_raise_anything_but_extf_error(read: Callable[[bytes], Any], data: bytes) -> None:
    _survives(read, data)


#: Lines close to the format, so that the parsers get past the first check.
_CELL = st.one_of(
    st.sampled_from(["", "0", "1", "S", "H", "EUR", "USD", "1,00", "-1,00", "1.234,56", "0101", "2902", "1200", "99999",
                     "20260101", "29.02.2024", "99", "9999999999999999999999", "1" * 5000, "²", "Konto", "Saldo"]),
    st.text(alphabet=st.characters(codec="cp1252"), max_size=8),
)  # fmt: skip
_ROWS = st.lists(st.lists(_CELL, max_size=20).map(";".join), max_size=8)


def _with_header(category: int, lines: list[str], columns: str) -> bytes:
    start = f.header(category, "x", date_from=date(2026, 1, 1), date_to=date(2026, 12, 31))
    return "\r\n".join([start, columns, *lines]).encode("cp1252", errors="replace")


@settings(max_examples=300, deadline=None)
@given(lines=_ROWS)
def test_near_valid_datev_files_never_raise_anything_but_extf_error(lines: list[str]) -> None:
    columns = ";".join(f.BOOKING_COLUMNS)
    _survives(extf.read_bookings, _with_header(21, lines, columns))
    _survives(extf.read_account_labels, _with_header(20, lines, "Konto;Kontenbeschriftung;Sprach-ID;Kontenbeschriftung lang"))
    _survives(extf.read_personal_accounts, _with_header(16, lines, "Konto;Name (Adressattyp Unternehmen);EU-Land;EU-UStID"))


@settings(max_examples=300, deadline=None)
@given(lines=_ROWS)
def test_near_valid_lists_never_raise_anything_but_extf_error(lines: list[str]) -> None:
    for columns in (
        "Konto;Beschriftung;Saldo Soll;Saldo Haben",
        "Konto;Beschriftung;EB-Wert;S;H;Saldo;S;H",
        "Konto;Saldo;S/H",
        "Inventarnummer;Bezeichnung;Konto;Anschaffungsdatum;Anschaffungskosten;Nutzungsdauer;AfA-Art;Buchwert",
    ):
        data = "\n".join([columns, *lines]).encode("cp1252", errors="replace")
        _survives(extf.read_trial_balance, data)
        _survives(extf.read_fixed_assets, data)


#: Text that survives the reader as it is: no blanks at the ends, not empty. Its non-ASCII
#: letters never form UTF-8, which the writer and the reader refuse: none of them is a
#: continuation byte. ``_UTF8_TEXT`` adds ``Ã``, and the ``¼`` that is one.
_ALPHABET = 'abcXYZ019 ;"-äöüÄÖÜß'
_TEXT = st.text(alphabet=_ALPHABET, min_size=1, max_size=60).map(str.strip).filter(bool)
_UTF8_TEXT = st.text(alphabet=_ALPHABET + "Ã¼", min_size=1, max_size=60).map(str.strip).filter(bool)
_ACCOUNT = st.integers(min_value=1, max_value=99999).map(lambda n: str(n).zfill(4))
_AMOUNT = st.decimals(min_value=Decimal("0.01"), max_value=Decimal("9999999999.99"), places=2, allow_nan=False, allow_infinity=False)
#: The old notation of a Generalumkehr, which the reader takes as one and the writer refuses.
_REVERSAL_KEYS = frozenset(f"2{digit}" for digit in range(10))
_TAX_KEY = st.from_regex(r"[0-9]{1,4}", fullmatch=True).filter(lambda key: key not in _REVERSAL_KEYS)
_RECORD = st.builds(
    Record,
    amount=_AMOUNT,
    account=_ACCOUNT,
    contra_account=_ACCOUNT,
    tax_key=st.one_of(st.none(), _TAX_KEY),
    booking_date=st.integers(min_value=0, max_value=364).map(lambda n: date(2026, 1, 1) + timedelta(days=n)),
    document_number=st.one_of(st.none(), st.text(alphabet="AZ09$&%*+-/", min_size=1, max_size=36)),
    text=_TEXT,
    reversal=st.booleans(),
)


@settings(max_examples=200, deadline=None)
@given(records=st.lists(_RECORD, max_size=10).filter(lambda rows: all(row.account != row.contra_account for row in rows)))
def test_any_valid_records_round_trip(records: list[Record]) -> None:
    written = extf.write_bookings(HEADER, records)
    assert written == extf.write_bookings(HEADER, records)
    read = extf.read_bookings(written).bookings
    assert [(b.amount, b.debit_account, b.credit_account, b.tax_key, b.booking_date, b.document_number, b.text, b.reversal) for b in read] == [
        (r.amount, r.account, r.contra_account, r.tax_key, r.booking_date, r.document_number, r.text, r.reversal) for r in records
    ]


@settings(max_examples=200, deadline=None)
@given(record=_RECORD, key=st.sampled_from(sorted(_REVERSAL_KEYS)), allowed_keys=st.sampled_from([None, frozenset(_REVERSAL_KEYS)]))
def test_a_key_in_the_old_notation_of_a_generalumkehr_is_always_refused(record: Record, key: str, allowed_keys: frozenset[str] | None) -> None:
    with pytest.raises(ExtfError) as caught:
        extf.validate(HEADER, [replace(record, tax_key=key)], allowed_keys=allowed_keys)
    assert any(repr(key) in error.message and "reversal=True" in error.message for error in caught.value.errors)


@settings(max_examples=100, deadline=None)
@given(
    labels=st.dictionaries(st.integers(1000, 9999).map(str), _TEXT, max_size=8),
    names=st.dictionaries(st.integers(10000, 99999).map(str), _TEXT.map(lambda text: text[:50].strip()).filter(bool), max_size=8),
)
def test_any_valid_labels_and_personal_accounts_round_trip(labels: dict[str, str], names: dict[str, str]) -> None:
    _, read_labels = extf.read_account_labels(extf.write_account_labels(HEADER, [Label(*item) for item in labels.items()]))
    assert {label.number: label.label for label in read_labels} == labels
    _, read_personal = extf.read_personal_accounts(extf.write_personal_accounts(HEADER, [Personal(*item) for item in names.items()]))
    assert {account.number: account.name for account in read_personal} == names


@settings(max_examples=200, deadline=None)
@given(labels=st.dictionaries(st.integers(1000, 9999).map(str), _UTF8_TEXT, min_size=1, max_size=4))
@example(labels={"4000": "Ã¼ber"})
@example(labels={"4000": "Ã¼ber", "4001": "Büro"})
@example(labels={"4000": "x" * 39 + "Ã¼ber"})
def test_labels_are_refused_on_writing_or_read_back_as_written(labels: dict[str, str]) -> None:
    try:
        written = extf.write_account_labels(HEADER, [Label(*item) for item in labels.items()])
    except ExtfError as caught:
        assert caught.errors and all("read back as UTF-8" in error.message for error in caught.errors)
        return
    assert {label.number: label.label for label in extf.read_account_labels(written)[1]} == labels


#: The amounts a list can hold, in cents; zero and the extremes are the cases that matter.
_CENTS = st.one_of(st.sampled_from([0, 1, 10**16 - 1]), st.integers(0, 10**16 - 1))


def _rows_of_a_balance(amount: str) -> dict[str, tuple[bytes, bool]]:
    """The ways to write ``amount`` in a trial balance, with whether each makes it a credit."""
    return {
        "debit column": (f"Konto;Saldo Soll;Saldo Haben\n1200;{amount};\n".encode(), False),
        "credit column": (f"Konto;Saldo Soll;Saldo Haben\n1200;;{amount}\n".encode(), True),
        "S/H debit": (f"Konto;Saldo;S/H\n1200;{amount};S\n".encode(), False),
        "S/H credit": (f"Konto;Saldo;S/H\n1200;{amount};H\n".encode(), True),
        "signed": (f"Konto;Saldo;S/H\n1200;-{amount};\n".encode(), True),
        "signed and credit": (f"Konto;Saldo;S/H\n1200;-{amount};H\n".encode(), False),
        "marked debit": (f"Konto;Saldo;S;H\n1200;{amount};S;\n".encode(), False),
        "marked credit": (f"Konto;Saldo;S;H\n1200;{amount};;H\n".encode(), True),
    }


@settings(max_examples=200, deadline=None)
@given(cents=_CENTS)
def test_an_amount_read_is_exact_and_no_negative_zero_in_any_decimal_context(cents: int) -> None:
    whole, rest = divmod(cents, 100)
    text = f"{whole},{rest:02d}"
    magnitude = Decimal(f"{whole}.{rest:02d}")

    def expected(credit: bool) -> Decimal:
        return magnitude.copy_negate() if credit and cents else magnitude

    def assert_same(read: Decimal | None, wanted: Decimal) -> None:
        # The tuple tells -0.00 from 0.00 and 1.00 from 1.0, which == does not.
        assert read is not None and read.as_tuple() == wanted.as_tuple()

    with localcontext(prec=6):
        assert_same(extf.parse_amount(text), magnitude)
        assert_same(extf.parse_amount(text, signed=True), magnitude)
        assert_same(extf.parse_amount(f"-{text}", signed=True), expected(True))
        for data, credit in _rows_of_a_balance(text).values():
            assert_same(extf.read_trial_balance(data)[0].balance, expected(credit))
        for marks, credit in ((f"{text};S;", False), (f"{text};;H", True)):
            line = extf.read_trial_balance(f"Konto;EB-Wert;S;H;Saldo;S;H\n1200;{marks};1,00;S;\n".encode())[0]
            assert_same(line.opening, expected(credit))
