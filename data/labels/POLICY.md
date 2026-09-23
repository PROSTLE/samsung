# Gold labels for the manifest compiler

Keel's safety class has two gold values: `read_only` and `state_changing`.
Gold labels come from someone other than us wherever possible.

## Sources, in order of authority

1. **τ²-bench author labels** (`@is_tool(ToolType.…, mutates_state=…)`):
   `WRITE` or `mutates_state=True` → `state_changing`; `READ`, `THINK`, and
   `GENERIC` with `mutates_state=False` → `read_only`.
2. **Reference-implementation analysis** (`data/labels/ast_effects.py`) for
   τ-bench v1 and BFCL: `state_changing` iff the implementation mutates its
   environment.
   *Validation:* on the 28 tools that τ-bench v1 and τ²-bench share, the
   analyser agrees with the τ²-bench authors on 28/28 (`python -m data.labels.build`
   prints this).

## Keel overrides (applied after the above, listed in full)

A tool whose purpose is to **hand the conversation to, or contact, a person
or third party** is `state_changing`, even if the mock implementation only
returns a string. Such an action is visible outside the session and can't be
taken back, so it must never run speculatively.

| Tool | Source | Upstream / implementation | Keel gold |
|---|---|---|---|
| `transfer_to_human_agents` | τ²-bench (all domains) | GENERIC, `mutates_state=False` | `state_changing` |
| `contact_customer_support` | BFCL travel_booking | implementation returns a string | `state_changing` |

## Known label/meaning tension (kept as the implementation says)

| Tool | Gold | Why |
|---|---|---|
| BFCL `get_flight_cost` | `state_changing` | Reads like a quote, but the implementation writes `_flight_cost_lookup`, which `book_flight` later reads. Speculatively calling it with the wrong arguments changes a later booking in that environment. |
| BFCL `cd` | `state_changing` | Changes the working directory other calls resolve against. |
