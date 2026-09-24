# Phase 3 report: the manifest compiler

Status: complete. Every tool in a manifest is compiled into its own
interruption policy, and the classification accuracy is **measured** on
held-out real tools.

```
uv run --isolated --python 3.10|3.11|3.12 --with-editable ".[dev]" pytest -o addopts="" -q
  -> 115 passed (3.10: 5.29s, 3.11: 4.75s, 3.12: 4.88s)
python -m data.labels.build
  -> gold: 263 tools, 19 domains, {'tau2_bench': 99, 'tau_bench': 2, 'bfcl': 162}, {'state_changing': 123, 'read_only': 140}
  -> labeller vs tau2-bench authors on shared tools: 28/28
python -m eval.compiler_eval --json reports/compiler_eval.json      (full output below)
python -m eval.grid_self_repair
  -> synthetic grid: runs=3072 invariant_violations=0 double_bookings=0 false_success_claims=0
```

## What the compiler produces per tool

| Output | How | File |
|---|---|---|
| Argument validator | Manifest parameter schema, normalised (BFCL's `dict`/`float`/`tuple`/`any` → JSON Schema), Draft 2020-12 via `jsonschema`. The kernel checks arguments **before** a call leaves; if they're invalid it asks about the slot behind the bad argument. | `keel/compiler/schema.py` |
| Safety class | 1. explicit hints (MCP `readOnlyHint`, `read_only`/`side_effects`/`mutates_state`, τ²-style `tool_type`, HTTP method per RFC 9110) → 2. lexical classifier if ≥ `compiler.min_confidence` sure → 3. `unknown` (treated as write). Evidence logged per tool. | `keel/compiler/manifest.py`, `classify.py` |
| Slot dependency map | argument → session slot of the same name (a goal binding can override it) | `manifest.py` |
| Acknowledgment template | From the tool's own name; reads: "Let me check the reservation details for {reservation_id}."; writes, spoken only once the call has actually gone out: "I'm sending the book reservation request now." A test checks that no template for any of the 263 real tools says done/booked/completed/confirmed/success. | `manifest.py` |
| Status probe | For an in-doubt write: a read-only tool sharing its object words whose required arguments the write already has; the verdict is read structurally from the result (bool flag / matching record / empty collection). | `keel/compiler/probe.py` |

Measured cost: loading the model takes 4.0 ms; compiling 263 tools takes
21.3 ms per manifest (81 µs per tool). There's no network and no warm-up.

## Where the gold labels come from (not from us)

1. **τ²-bench authors' own labels** (`@is_tool(ToolType.READ/WRITE…)`): 99 tools.
2. **Reference-implementation analysis** for τ-bench v1 and BFCL
   (`data/labels/ast_effects.py`). It uses static taint analysis: does the
   tool's code mutate its environment? It distinguishes environment objects
   from fresh containers holding them, and follows mutating methods across
   classes. **Validated before use:** it agrees with the τ²-bench authors on
   28/28 tools the two benchmarks share.
3. Two documented Keel overrides (hand-off / contacting a person →
   state-changing) and one documented label/meaning tension
   (`get_flight_cost`), in `data/labels/POLICY.md`.

## Measured results (leave-one-domain-out, 15 domain groups, 263 tools)

Each domain is held out completely, so every tool is scored as *unseen*.
"Unsafe" = a write treated as read-only, i.e. it could run speculatively.

```
threshold  accuracy  unsafe  read_recall  tau2 acc  bfcl acc
      0.5     0.833       9        0.750     0.960     0.753
     0.55     0.852       1        0.729     0.960     0.784
      0.6     0.806       1        0.643     0.950     0.728
      0.8     0.684       1        0.414     0.768     0.642     <- shipped (pre-registered)
     0.95     0.494       0        0.050     0.586     0.444
pre-registered t=0.8   accuracy 0.684  unsafe 1  read_recall 0.414
nested-CV threshold    accuracy 0.517  unsafe 1  read_recall 0.100
baseline everything_state_changing  accuracy 0.468  unsafe 0  read_recall 0.000
baseline verb_list                  accuracy 0.802  unsafe 1  read_recall 0.636
```

How to read this honestly:

* **The only unsafe error at every threshold ≥ 0.55 is `get_flight_cost`.**
  Its gold label is "write" only because the BFCL implementation caches a
  cost that `book_flight` later reads; the model is confident it's a read.
* **At the shipped threshold, Keel speculates 41% of genuinely read-only
  unseen tools.** It's conservative: an uncertain tool is fenced like a
  write, which costs latency but never correctness.
* **The simple verb-list baseline beats the shipped setting on read
  recall** (0.636 vs 0.414). The learned model beats the baseline on
  every metric at t = 0.55 (0.852 / 1 / 0.729). We did not switch to 0.55,
  because 0.8 was fixed before the evaluation, and picking the best-looking
  row afterwards would be tuning on the test set.
* **The nested-CV rule we tried (lowest threshold with zero unsafe inner
  errors) failed.** A single noisy label (`get_flight_cost`) forces it to
  0.95 whenever that tool is in the inner data. We report it as a failed
  method rather than hide it.

## Decisions and why

1. **Deterministic learned classifier by default, LLM optional.** No local
   model or API key is available here, whether hosted models are reachable
   during evaluation is unknown, and a classifier must be measurable.
   Logistic regression is offline, reproducible, ~0.1 ms/tool, and explains
   itself by its top features. `PromptClassifier` (fixed prompt, few-shot
   from τ²-bench, strict JSON schema, failure → fallback) is ready but
   unmeasured, so we claim nothing about it.
2. **IDF weighting of description words.** It removed benchmark boilerplate
   ("This tool belongs to the … system") as fake evidence; accuracy at
   t = 0.5 went from 0.772 to 0.833 (60 → 44 errors). It's a standard, general
   technique, not aimed at any one error.
3. **Near-duplicate domains are held out together** (BFCL's three `memory_*`
   APIs share tool names) to avoid leakage inflating the numbers.
4. **Pre-registered threshold kept** (see above).
5. **Probe derivation is structural**, with no per-tool knowledge; the kernel
   now uses it by default, so "did it go through?" works for any manifest
   with a related read tool.
6. **Arguments are validated before dispatch.** A malformed call can't
   leave Keel; the user is asked about the slot behind the bad argument.
7. **One filler per turn.** When a correction acknowledgment is pending, a
   tool's dispatch acknowledgment is suppressed (the guide penalises
   "excessive fillers").

## How to use it

```
python -m data.fetch.tau_bench && python -m data.fetch.bfcl    # real schemas + implementation effects
python -m data.labels.build                                     # gold labels (+ labeller validation)
python -m keel.compiler.train                                   # retrain keel/compiler/model.json
python -m eval.compiler_eval --json reports/compiler_eval.json  # the table above
```

```python
from keel.compiler.manifest import default_compiler
policies = default_compiler(0.8)(manifest.tools)   # name -> ToolPolicy(safety, evidence, ack_template, ...)
policies["get_reservation_details"].validate({"reservation_id": 7})   # -> ["reservation_id: 7 is not of type 'string'"]
```

## Known gaps

- Read recall on unseen domains is modest (0.41 at the shipped setting).
  More labelled public tools, or a measured LLM second opinion, would raise it.
- The commit-conflict replan (from phase 2) still needs the slow path: phase 4.
