# extf

**extf** reads and writes files in the DATEV format (header marker `EXTF`): Buchungsstapel
(category 21), Kontenbeschriftungen (20), Debitoren/Kreditoren (16), plus the reader side for
trial balances and fixed-asset lists. Pure Python, no dependencies, Windows-1252, format
version 700, **byte-deterministic** output, strict validation.

extf works with files in the DATEV format; it is **not affiliated with or endorsed by DATEV eG**.
DATEV is a trademark of DATEV eG.

**Limits.** extf is a format library. It is **not tax advice**, has **no GoBD certification**,
and knows nothing about charts of accounts: account numbers are strings and their length comes
from the header. Whether a booking is right is the caller's question; whether a file is
well-formed is extf's.

```bash
pip install extf
```

## Writing

```python
from datetime import date, datetime
from decimal import Decimal

import extf

header = extf.ExportHeader(
    advisor_number=1234567,
    client_number=10001,
    fiscal_year_start=date(2026, 1, 1),
    account_length=4,
    origin="XT",
    exported_by="my software",
    chart="03",
    created=datetime(2026, 8, 3, 9, 15),  # stated, never the clock: same input, same bytes
    date_from=date(2026, 7, 1),
    date_to=date(2026, 7, 31),
)
records = [
    extf.Record(
        amount=Decimal("119.00"),
        account="4806",
        contra_account="70049",
        tax_key="9",
        booking_date=date(2026, 7, 3),
        document_number=extf.document_number("H 2026.07"),  # H-2026-07
        text=extf.clean_text("Wartung Juli"),
    )
]
data: bytes = extf.write_bookings(header, records, allowed_keys={"9"})
labels: bytes = extf.write_account_labels(header, [extf.Label("4806", "Wartungskosten")])
accounts: bytes = extf.write_personal_accounts(header, [extf.Personal("70049", "Muster Service GmbH")])
```

Every writer validates first. `extf.validate(header, records, labels, personal)` does so alone.
Either raises `extf.ExtfError`, whose `errors` name every bad line; nothing is written then.
Kontenbeschriftungen whose written file would be valid UTF-8 beyond ASCII, such as the label
`Ã¼ber` alone, are refused too: their text would be read back as UTF-8, which the reader refuses
(see Hostile input). The file as a whole decides, so one label such as `Büro` next to it is fine.
`Record` writes euro amounts only, on the debit side (`S`), with a Generalumkehr as `reversal=True`.
Tax keys `20` to `29` are refused: the reader takes them as the old notation of a Generalumkehr
(`29` cancels an entry with tax key 9), so they would not come back as written. State the key and
`reversal=True` instead; other keys, such as `2`, `200` or `2000`, are not affected.

## Reading

```python
booking_file = extf.read_bookings(data)
for booking in booking_file.bookings:
    print(booking.booking_date, booking.debit_account, booking.credit_account, booking.amount)

header, labels = extf.read_account_labels(some_bytes)
header, personal = extf.read_personal_accounts(some_bytes)  # category 16 or the Kanzlei-Rechnungswesen list
balances = extf.read_trial_balance(some_bytes, account_length=4)  # CSV lists have no header to say so
assets = extf.read_fixed_assets(some_bytes, account_length=4)
```

Inputs are `bytes`, outputs are frozen dataclasses: `Decimal` amounts, `date` dates, account numbers
as strings. A file with one bad line is refused as a whole with `extf.DatevFormatError`, a subclass
of `ExtfError`, naming up to 200 bad lines. Records with Skonto, for example, are refused rather
than rebuilt.

## Hostile input

Readers treat every input as hostile. The library does no network or file access, evaluates
nothing from the input and keeps no global state. Refused with `DatevFormatError`:

- more than `MAX_INPUT_BYTES` (64 MiB), `MAX_LINES` (1,000,000) lines, `MAX_COLUMNS` (512) columns
  in a line or `MAX_FIELD_LENGTH` (4096) characters in a field;
- a row of more than 4,195,841 characters, line ends included, before it is parsed. That is the
  longest row within those two limits, every cell quoted and every character a doubled quote, so
  this refuses no row they allow; the message names both limits, as the reader does not know which
  of them was broken;
- a CSV list without its header row within `MAX_HEADER_SEARCH` (1 MiB) characters, the header row
  included, where a separator and a line end count as one each. So a file without a header row is
  refused after a bounded amount of work, and a later violation of a limit is not reported;
- a DATEV-format file that is not Windows-1252, including one that is valid UTF-8, even if its
  bytes are also valid Windows-1252; `validate` keeps the writers from producing such a file;
- a wrong category, a format version outside `FORMAT_VERSIONS` (500 to 700), an account length
  outside 4 to 8, and fields longer than their column.

On any bytes, a reader returns a value or raises `DatevFormatError`; property tests hold it to
that. `parse_amount` is a plain helper on a `str` and raises `ValueError`.

Rows are read one at a time, and reading stops at the first limit or at the 200th bad line. The
input is still decoded and buffered as a whole, and the values of an accepted file are held until
they are returned. Peak memory to plan for, measured in `python:3.12-slim` with the interpreter
included. The decoded text takes twice the memory as soon as it holds one character outside
Latin-1, such as `€` or `–`, and four times as soon as it holds one beyond the Basic Multilingual
Plane, such as an emoji. Only a CSV list can do the latter, as it may be UTF-8; the DATEV format is
Windows-1252. A file in plain ASCII stays below the higher figures:

- for a file at `MAX_INPUT_BYTES` that is refused early or has no valid records, roughly 470 MiB in
  ASCII, roughly 590 MiB with characters such as `€` and roughly 850 MiB for a CSV list with a
  character beyond the Basic Multilingual Plane;
- roughly 900 MiB for an accepted file at `MAX_LINES` with short records; when its records also
  fill `MAX_INPUT_BYTES` with short fields, up to roughly 1.2 GiB in ASCII and roughly 1.4 GiB with
  such characters in those fields;
- as much as for an accepted file when a file is refused only near its end: what is valid up to
  there is held until then.

A caller who cannot afford this limits the input before calling.

## Stability

What `extf` exports at the package root is the public API, under
[semantic versioning](https://semver.org/); every other module is private. The bytes a writer
produces are part of the contract: golden-file tests guard each one.

## Verifying a release

Releases are built by GitHub Actions and published to PyPI by trusted publishing, with SLSA
provenance and a CycloneDX SBOM attested:

```bash
gh attestation verify extf-0.1.0-py3-none-any.whl --repo extdev-gmbh/extf
```

## License

Licensed under the [Apache License 2.0](LICENSE). Report security issues as described in
[SECURITY.md](SECURITY.md).
