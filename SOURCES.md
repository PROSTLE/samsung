# Sources

Every external fact, dataset and license Keel relies on, with where it was
checked and when. Sections between `<!-- source:… -->` markers are rewritten
by the matching `data/fetch/` script; everything else is maintained by hand.

## Brief

### Theme 05 guide
- `docs/Theme_5_Guide.pdf` (v1.0.0, 3 pages), supplied by the organisers. Read in full 2026-09-23.
- The PDF is watermarked with a registrant's identity on every page, so it is
  git-ignored and must not be published with the repo.

## Prior art (checked for the "automatic interruption policy" claim)

### Pipecat — per-tool `@tool_options`
- https://docs.pipecat.ai/guides/learn/function-calling (fetched as `.md`, 2026-09-23)
- Quote: "`cancel_on_interruption` (default `True`): When `True`, the call is
  cancelled if the user interrupts. When `False`, the call is treated as
  **asynchronous**…" Also `cancellable_by_llm` (default `False`) and
  `timeout_secs`, all set by the developer via `@tool_options(...)` or
  `register_function(...)`.
- Finding: the policy is developer-configured per tool, and cancellation is
  triggered by *any* interruption, not by which slot changed.

### LiveKit Agents — `disallow_interruptions()`
- https://docs.livekit.io/agents/logic/tools/definition.md (fetched 2026-09-23)
- Quote: "By default, tools can be interrupted if the user speaks. A tool
  continues running in the background until it returns; interrupting the agent
  doesn't cancel the work." and "Call `context.disallow_interruptions()`
  (Python) or `ctx.disallowInterruptions()` (Node.js) at the start of any tool
  that mutates external state." There is also a `CANCELLABLE` tool flag that
  lets the LLM cancel a running tool.
- Finding: LiveKit's docs name the read-only vs mutating distinction but leave
  it to the developer to act on inside each tool.

## Standards

### Model Context Protocol tool annotations
- https://github.com/modelcontextprotocol/modelcontextprotocol/blob/main/schema/2025-06-18/schema.ts (fetched 2026-09-23)
- `ToolAnnotations` has `readOnlyHint` (default false), `destructiveHint`
  (default true, meaningful only when not read-only), `idempotentHint`
  (default false), `openWorldHint`.
- https://modelcontextprotocol.io/specification/2025-06-18/server/tools says
  clients must consider tool annotations untrusted unless they come from
  trusted servers.
- Use in Keel: the manifest compiler's first step honours these hints when
  present. The MCP default (`readOnlyHint=false`) matches Keel's rule that an
  unlabelled tool is treated as state-changing.

## Upstream code facts

### τ²-bench `ToolType` / `is_tool`
- https://github.com/sierra-research/tau2-bench/blob/main/src/tau2/environment/toolkit.py (fetched 2026-09-23)
- `ToolType` members READ, WRITE, THINK, GENERIC. `is_tool(tool_type=ToolType.READ, mutates_state=None)`;
  when `mutates_state` is None it is inferred as True for WRITE and False otherwise.

## Python dependencies

| Package | Use | License | Checked |
|---|---|---|---|
| pydantic | protocol models, validation | MIT | https://github.com/pydantic/pydantic (GitHub API, 2026-09-23) |
| tomli | TOML on Python 3.10 only | MIT | https://github.com/hukkin/tomli (GitHub API, 2026-09-23) |
| pytest | tests (dev only) | MIT | https://github.com/pytest-dev/pytest (GitHub API, 2026-09-23) |
| hypothesis | property tests (dev only) | MPL-2.0 | GitHub API reports NOASSERTION; https://github.com/HypothesisWorks/hypothesis/blob/master/LICENSE.txt says MPL 2.0, and package metadata `License-Expression: MPL-2.0` (6.168.0), 2026-09-23 |

MPL-2.0 is file-level copyleft. Hypothesis is a dev-only test dependency that
Keel neither modifies nor redistributes, so it places no obligations on Keel's
own code.

## Datasets

<!-- source:tau_bench -->
### τ-bench and τ²-bench tool schemas (Sierra Research)

- Repos: https://github.com/sierra-research/tau-bench @ `59a200c6d575d595120f1cb70fea53cef0632f6b`, https://github.com/sierra-research/tau2-bench @ `b7ea9074c1cba482b30687fecdb5c8425fd6f619`
- License: MIT (tau-bench), MIT (tau2-bench), per GitHub API; copies in `data/tools/LICENSES/`
- Retrieved: 2026-09-23 by `python -m data.fetch.tau_bench`
- Output: `data/tools/tau_bench.jsonl` (30 tools with JSON schemas),
  `data/tools/tau2_bench.jsonl` (99 tools with author labels: GENERIC=9, READ=45, WRITE=45)
- Labels come from τ²-bench's own `@is_tool(ToolType.…, mutates_state=…)` decorators, not from us.
- Parse failures: 0
<!-- /source:tau_bench -->

<!-- source:bfcl -->
### Berkeley Function Calling Leaderboard, multi-turn API docs

- Repo: https://github.com/ShishirPatil/gorilla @ `6ea57973c7a6097fd7c5915698c54c17c5b1b6c8`, path `berkeley-function-call-leaderboard/bfcl_eval/data/multi_turn_func_doc/`
- License: Apache-2.0, per GitHub API (repository root LICENSE; the subdirectory has none of its own); copy in `data/tools/LICENSES/`
- Retrieved: 2026-09-23 by `python -m data.fetch.bfcl`
- Output: `data/tools/bfcl.jsonl` (162 functions: gorilla_file_system=18, math_api=17, memory_kv=15, memory_rec_sum=5, memory_vector=12, message_api=10, posting_api=14, ticket_api=9, trading_bot=20, travel_booking=18, vehicle_control=22, web_search=2)
- No read/write labels upstream. Schema dialect is BFCL's (`"type": "dict"`, `"float"`), stored verbatim.
<!-- /source:bfcl -->
