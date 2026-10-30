# Changelog

All notable changes to extf are listed here. The project follows
[Semantic Versioning](https://semver.org/): what the package root exports is the public API;
a breaking change to it needs a major version and an entry here.

## [Unreleased]

### Added

- `MAX_HEADER_SEARCH`, the bound on the search for the header row of a CSV list.

### Changed

- `validate`, and so `write_account_labels`, refuses labels and a header whose written file is
  valid UTF-8 beyond ASCII, such as the label `Ã¼ber` alone, with `ExtfError`: its text would be
  read back as UTF-8 and `read_account_labels` refuses such a file, so no writer returns a file
  its own reader refuses. The file as a whole decides, as written; every file still accepted
  keeps its bytes.
- The readers go through the rows lazily and stop at the first limit or at the 200th bad line, so
  a refused file at `MAX_INPUT_BYTES` peaks at about 470 MiB, or 590 MiB with characters such as
  `€` and 850 MiB in a CSV list with a character beyond the Basic Multilingual Plane, instead of
  about 1.6 GiB. A file is still refused as a whole. A file refused at its 200th bad line, for a
  bad header or for missing columns no longer reports a later limit violation, which used to win.
  A limit violation after fewer than 200 bad lines still wins, and those bad lines are not
  reported.
- The README states the peak memory to plan for.
- `fiscal_year_end` returns 31.12.9999 for the start 01.01.9999 and raises `ValueError` with a
  message of its own for every other start in year 9999, whose fiscal year would end after year
  9999. `validate` and `write_bookings` refuse such a start with `ExtfError` on the header instead
  of writing a file that `read_bookings` refuses, and a file with the start 01.01.9999 reads back.
- The CSV lists search `MAX_HEADER_SEARCH` characters for their header row, so a list without one
  is refused at a cost that does not grow with the file: the worst case, a file of empty cells at
  `MAX_INPUT_BYTES`, takes under 1 s instead of 35 s. A list whose header row ends more than 1 MiB into the file, separators and line ends
  included, is refused like one without a header row, and a list without a header row no longer
  reports a limit violation past that point.
- A row of more than 4,195,841 characters, the longest row within `MAX_COLUMNS` and
  `MAX_FIELD_LENGTH`, is refused before it is parsed, so such a row at `MAX_INPUT_BYTES` takes
  about 0.3 s and 460 MiB instead of up to 1.4 s and 980 MiB, and no file accepted so far is
  refused. Its message changes to `the line has more than 512 columns or a field of more than 4096
  characters`, in place of the message for too many columns, too many lines or unreadable CSV.

## [0.1.0] - 2026-10-30

First release.

### Added

- Readers: `read_bookings` (category 21), `read_account_labels` (20), `read_personal_accounts`
  (16 and the Kanzlei-Rechnungswesen list), `read_trial_balance`, `read_fixed_assets`, with
  `is_datev_format`, `parse_amount` and `fiscal_year_end`.
- Writers: `write_bookings`, `write_account_labels`, `write_personal_accounts` from the neutral
  `ExportHeader`, `Record`, `Label` and `Personal`, byte-deterministic and validated;
  `validate`, `clean_text`, `document_number`, `amount_de`. `validate` refuses the tax keys `20`
  to `29`, which `read_bookings` takes as the old notation of a Generalumkehr; a `Record` states
  one with `reversal=True`.
- `ExtfError` and its subclass `DatevFormatError`, each naming every bad line (`LineError`).
- Limits on hostile input: `MAX_INPUT_BYTES`, `MAX_LINES`, `MAX_COLUMNS`, `MAX_FIELD_LENGTH`,
  format versions 500 to 700, refusal of UTF-8 where Windows-1252 is due.
