PY ?= python

.PHONY: install data test test-all

install:
	$(PY) -m pip install -e ".[dev]"

# Re-fetch real tool schemas; rewrites the matching SOURCES.md sections.
data:
	$(PY) -m data.fetch.tau_bench
	$(PY) -m data.fetch.bfcl

test:
	$(PY) -m pytest

# Same suite on every Python version the guide allows (needs uv).
test-all:
	for v in 3.10 3.11 3.12; do uv run --isolated --python $$v --with-editable ".[dev]" pytest || exit 1; done
