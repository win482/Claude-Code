# TNA 10X - machine M2 (cloud routine)

Installed 2026-10-01 from the Drive folder `TNA-10X-MACHINE-PACKAGE`
(id `1vYtLG6uKEwxH1i3f2G6V5j--fnGDfRHN`). Drive stays the source of truth -
the routine downloads both files from Drive each run. These copies are a
versioned snapshot.

| File | Purpose |
|---|---|
| `TNA-10X-SENDER-M2.md` | The sender procedure, identity block filled for M2 |
| `tna10x_pipeline.py` | Deterministic pipeline (ingest, gate, match, review, draft, schedule, payloads, backfill) |
| `routine-prompt.md` | Prompt the daily routine fires with |

Identity: M2, win@topnotchacquisitions.com, Sending Accounts row
`recDD4KwhfsJ4Lx7J`, Make poller scenario `6129832`, base `appa68g31bjkD7QfA`.

Routine: "TNA 10X - M2 daily send", daily 14:20 UTC (10:20 EDT), fresh session
per fire, connectors Airtable, HigherGov, Gmail (win@), Make, Google Drive.

**Daylight time ends 2026-11-01** - move the routine to 15:20 UTC that day.
