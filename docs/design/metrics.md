# GPU and system metrics

GPU counters, labels and their review follow-ups.

Moved unchanged from `CLAUDE.md` on 2026-10-06, when CLAUDE.md was cut to its core. Section headings below are the original ones.

---

### GPU-01 review follow-up (2026-10-01)

GPU-01 review: one-shot system status and briefing use force=True for fresh data; the
polled HTTP path alone serves stale snapshots. Fallback bars are explicitly labelled 3D.
Unknown dedicated usage is n/a, even when capacity is known.


## GPU adapter labels — 2026-10-02

GPU labels follow-up: Overview and status drawer share gpuLabel. Keep the full matched adapter name, preserve existing generic names, use GPU <index> for missing/blank names, and append (3D) only for the counter source. Escape names before HTML assembly.
