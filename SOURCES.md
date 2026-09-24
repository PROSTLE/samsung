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

## Conversation timing research (basis for config/keel.toml defaults)

### Roberts & Francis (2013): tolerance for silent gaps
- F. Roberts, A. L. Francis, "Identifying a temporal threshold of tolerance for
  silent gaps after requests", *J. Acoust. Soc. Am.* 133(6), EL471–EL477, 2013.
  doi:10.1121/1.4802900 (metadata verified via Crossref API, 2026-09-23;
  abstract read from https://web.ics.purdue.edu/~froberts/Threshold%202013%20JASA%20Roberts%20&%20Francis.pdf).
- Abstract: 380 participants rated responses after gaps of 200–1200 ms in
  100 ms steps; "There was a notable drop-off in ratings at 600 ms and a
  statistically significant difference in ratings between 700 and 800 ms."
- Used for: `fence.quiet_ms = 600` (provisional) and `floor.ack_after_ms = 300`
  (keeps the first spoken action well inside 600 ms).

### Jefferson: the ~1 s "standard maximum" silence
- G. Jefferson, "Notes on a possible metric which provides for a 'standard
  maximum' silence of approximately one second in conversation", in D. Roger &
  P. Bull (eds.), *Conversation: An Interdisciplinary Perspective*, Clevedon:
  Multilingual Matters. The Jefferson archive dates it 1988
  (https://liso-archives.liso.ucsb.edu/Jefferson/, checked 2026-09-23); it is
  often cited as 1989, the volume's publication year. We cite it as 1988.
- Used for: `fence.stale_turn_ms = 1000` and `floor.progress_after_ms = 1000`.

### Stivers et al. (2009): turn-taking gaps are short and universal
- T. Stivers et al., "Universals and cultural variation in turn-taking in
  conversation", *PNAS* 106(26):10587–10592, 2009. doi:10.1073/pnas.0903616106.
  Abstract via NCBI E-utilities (PMID 19553212), 2026-09-23.
- Abstract: all 10 languages show "a general avoidance of overlapping talk and a
  minimization of silence between conversational turns", with language
  averages "within a range of 250 ms from the cross-language mean".
- Used for: the argument that a correction arriving as a *new* turn comes
  quickly after the previous one, which the fence's quiet period covers.

### Blackmer & Mitton (1991): self-repairs are fast
- E. R. Blackmer, J. L. Mitton, "Theories of monitoring and the timing of
  repairs in spontaneous speech", *Cognition* 39(3):173–194, 1991.
  doi:10.1016/0010-0277(91)90052-6. Abstract via NCBI E-utilities (PMID
  1841032), 2026-09-23.
- Abstract: 1525 repairs from 61 radio call-in speakers; "Many of the
  cut-off-to-repair times observed were faster than would be predicted by any
  model in the literature."
- Used for: why most self-repairs land *inside* one turn, which the fence
  handles by waiting for end-of-turn. The exact interval distribution is not
  in the abstract (UNVERIFIED beyond it); phase 4 measures our own.

### RFC 9110 §9.2.1: safe methods
- https://www.rfc-editor.org/rfc/rfc9110.txt (fetched 2026-09-23).
- "Of the request methods defined by this specification, the GET, HEAD,
  OPTIONS, and TRACE methods are defined to be safe." and "The purpose of
  distinguishing between safe and unsafe methods is to allow automated
  retrieval processes (spiders) and cache performance optimization
  (pre-fetching) to work without fear of causing harm."
- Used for: Keel's rule that only read-only calls run speculatively, and the
  HTTP-method hint in the manifest compiler.

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
| jsonschema | argument validation (Draft 2020-12) | MIT | https://github.com/python-jsonschema/jsonschema (GitHub API, 2026-09-23); 4.26.0 installed |
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
- `impl_effects`: whether the reference implementation in `berkeley-function-call-leaderboard/bfcl_eval/eval_checker/multi_turn_eval/func_source_code/` mutates
  its state, by static analysis (`data/labels/ast_effects.py`). Functions with
  no matching implementation method: 0.
<!-- /source:bfcl -->
