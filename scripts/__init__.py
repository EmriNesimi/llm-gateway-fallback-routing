"""Operator tooling, run as modules: `python -m scripts.<name>`.

Not `python scripts/<name>.py`. These import from `app`, and running the file
directly puts `scripts/` on sys.path instead of the repo root, so `app` is
not importable. Running as a module puts the root first. Each has a Makefile
target so nobody has to remember that.

What lives here: things an operator runs by hand against real stores —
reconcile, purge. What does not: anything the gateway itself imports, which
belongs under `app/`, and anything a test needs, which belongs under `tests/`.
The demo shell script and the k6 load test sit here too because they are
also "run by a person, against a running gateway".
"""
