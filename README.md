# Keel

An interruption-safe execution layer for real-time voice agents, built for the
Samsung PRISM Generative AI Hackathon, Theme 05 (Interruptible Real-Time Agents).

Status: phase 1 of 7 (protocol models, kit adapter, virtual clock, trace
logger, tool-schema collection). No performance numbers are reported yet.

- Interface contract is provisional until the evaluation kit ships; see
  `docs/KIT_ASSUMPTIONS.md`.
- Every external fact and dataset is cited in `SOURCES.md`.

```
python -m pip install -e ".[dev]"
python -m pytest            # or: make test-all  (3.10, 3.11, 3.12 via uv)
python -m data.fetch.tau_bench && python -m data.fetch.bfcl
```
