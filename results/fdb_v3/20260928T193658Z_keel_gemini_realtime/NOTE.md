# Invalid run: the machine lost its network connection

This run is not a measurement of the agent and claims no score.

From about 19:47 UTC the agent log shows the worker losing LiveKit ("failed to
connect to livekit, retrying", then "failed to fetch region urls" and "signal
connection failed" from 20:16), and FDB-v3's inference script failed for 47 of the
100 recordings. Of the 53 it streamed, 32 got no response. The reports in this
folder (14/100) reflect the outage.

The same code and configuration were run again straight afterwards, with the
machine kept awake and connected: `results/fdb_v3/20260928T211335Z_keel_gemini_realtime/`.
