# Working notes and open watch-outs

Working alongside Codex, and the open watch-outs list.

Moved unchanged from `CLAUDE.md` on 2026-10-06, when CLAUDE.md was cut to its core. Section headings below are the original ones.

---

## Working alongside another agent (Codex)

Both Claude and Codex edit this repo. To avoid collisions:
- **Work on a branch**, not `main` (e.g. `claude/<feature>`, `codex/<feature>`).
- `git pull` / rebase before a batch; merge to `main` between sessions.
- Expect to reconcile the shared **test builder** and **control-room roster
  count** when the other agent adds an `AgentContext` field or a specialist.

## Outstanding / watch-outs

- **Rotate the NVIDIA API key and Gmail app password** (both were pasted in chat;
  they live only in gitignored `.env`).
- GPU metrics now fall back from `nvidia-smi` to non-elevated Windows counters (GPU-01).
  Counters report the busiest **3D** engine per adapter LUID and dedicated memory usage;
  they do not measure compute/copy/video engines. DXGI names/capacity are matched by LUID;
  a powered-down or unmatched card keeps a generic name and unknown capacity. A cold
  Windows metrics read has unknown fields until the background refresh completes; stale
  reads keep the prior snapshot. Missing/localized counters degrade gracefully and log
  each cause once per process. Do not recommend running the whole app as administrator
  just to show GPU usage.
- `copilot.extract_keywords` keeps its own token pattern on purpose (it must preserve
  "node.js", "c++", "c#"). It is the one word-splitter outside `terms.py` — leave it there.
- The Chromium regression test rewrites `docs/review/desktop.png` / `mobile.png` on every run;
  discard those changes (`git checkout -- docs/review`) unless a review PR wants new evidence.
- The user keeps durable project memory in an Obsidian vault. **The vault root is
  `F:\obsidian\Claude mem-Obsidian main memory\Claude Mem`** — that is where `.obsidian`
  lives and what `OBSIDIAN_VAULT` is set to (53 notes). This project's notes are the ten
  in its `Personal AI Agent\` subfolder; keep those in sync when shipping features.
  Do **not** point `ObsidianVault` at that subfolder to audit it: wiki-links resolve
  vault-wide in Obsidian, so links into `Concepts\` and `Agent Memory\` are reported as
  broken when the tool only sees one folder. That mistake invented four broken links
  that were never broken.
