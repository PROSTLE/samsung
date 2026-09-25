PY ?= python

.PHONY: install data labels train compiler-eval test test-all grid showcase fdb fdb-smoke show-and-fix

install:
	$(PY) -m pip install -e ".[dev]"

# Re-fetch real tool schemas + implementation-derived effects; rewrites SOURCES.md sections.
data:
	$(PY) -m data.fetch.tau_bench
	$(PY) -m data.fetch.bfcl

# Gold read/write labels (data/labels/POLICY.md).
labels:
	$(PY) -m data.labels.build

# Retrain the shipped lexical safety model on all gold labels.
train:
	$(PY) -m keel.compiler.train

# Leave-one-domain-out evaluation of the compiler's safety classifier.
compiler-eval:
	$(PY) -m eval.compiler_eval --json reports/compiler_eval.json

test:
	$(PY) -m pytest

# Synthetic timing/fault grid around one self-repair; exits non-zero on any violation.
grid:
	$(PY) -m eval.grid_self_repair

# Five synthetic sessions rendered in the trace console: traces/console.html
showcase:
	$(PY) -m eval.showcase

# Same suite on every Python version the guide allows (needs uv).
test-all:
	for v in 3.10 3.11 3.12; do uv run --isolated --python $$v --with-editable ".[dev]" pytest || exit 1; done

# Full-Duplex-Bench v3 against Keel, end to end (needs .env; see README).
fdb:
	scripts/reproduce_fdb_v3.sh

fdb-smoke:
	scripts/reproduce_fdb_v3.sh --example travel_10

# The extension use case, in LiveKit dev mode (needs .env).
show-and-fix:
	$(PY) -m extension.show_and_fix.agent dev
