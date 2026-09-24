PY ?= python

.PHONY: install data labels train compiler-eval test test-all grid

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

# Same suite on every Python version the guide allows (needs uv).
test-all:
	for v in 3.10 3.11 3.12; do uv run --isolated --python $$v --with-editable ".[dev]" pytest || exit 1; done
