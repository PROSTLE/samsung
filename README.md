# Keel

An interruption-safe execution layer for real-time voice agents, built for the
Samsung PRISM Generative AI Hackathon, Theme 05 (Interruptible Real-Time Agents).

Status: phase 3 of 7 done (protocol, adapter, virtual clock, trace, tool data;
coordination kernel; manifest compiler). Measured numbers so far are in docs/reports/,
each with the command that produced it.

- Interface contract is provisional until the evaluation kit ships; see
  `docs/KIT_ASSUMPTIONS.md`.
- Every external fact and dataset is cited in `SOURCES.md`.

```
python -m pip install -e ".[dev]"
python -m pytest            # or: make test-all  (3.10, 3.11, 3.12 via uv)
python -m data.fetch.tau_bench && python -m data.fetch.bfcl
```
