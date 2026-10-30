# Security policy

extf parses files that come from outside and writes files that go into accounting systems.
It treats every input as hostile; see [Limits](README.md#hostile-input).

## Supported versions

Security fixes are made for the latest released minor version. Please update to the
latest release before reporting.

## Reporting a vulnerability

Please do **not** open a public issue for a vulnerability. Report it privately to:

**security@ext.dev**

Include what you found, how to reproduce it, and which version you use. **Never send real
bookings, customer data or tax numbers** — a description or a made-up example is enough.

We acknowledge a report within a few working days, keep you informed about the fix, and
credit you in the changelog if you wish.

## Scope

In scope: the code in this repository — for example input that makes a reader raise anything
but `ExtfError`, use unbounded memory or time, or that makes a writer produce a file other
than what `validate` allows.

Out of scope: accounting software that reads the files extf writes, and the correctness of
bookings in tax terms.
