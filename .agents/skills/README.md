# Agent skills

Skills that Codex (and other agents following the Agent Skills layout) load from
`.agents/skills/`. Claude Code reads `.claude/skills/` instead, where a short `SKILL.md`
points back here, so there is one copy of each skill.

| Skill | What it is for | Source |
|---|---|---|
| `nemotron-speech/` | NVIDIA's guide to Riva / Nemotron Speech: ASR, TTS and translation, hosted on build.nvidia.com or self-hosted. Use it when changing the app's Parakeet speech recognition (`tools/transcribe.py`), Magpie voice (`voice.py`) or translation (`tools/translate.py`). | [NVIDIA/skills](https://github.com/NVIDIA/skills), `skills/nemotron-speech/` at commit `0e0d506f4eb67204a62586ac5f19df3cb7ad9b1f` |

## Copied unchanged

Every file under `nemotron-speech/` is byte-for-byte the upstream file at that commit, so
`skill.oms.sig` still verifies them. Do not edit them here: write project-specific findings
in `docs/design/` or `CLAUDE.md`, and update the copy by fetching a newer commit whole.

Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. The skill's documentation is licensed
under CC BY 4.0 (`LICENSE-CC-BY-4.0`) and its code (`scripts/main.py`) under Apache 2.0
(`LICENSE-APACHE`), both copied from the same commit.

## What we measured that the skill tells you to check

The skill says to look these up rather than trust its text; for this app's NVIDIA key on
2026-10-06 they were:

- Magpie TTS (`magpie-tts-multilingual`) and `riva-translate-1.6b` answer on
  `grpc.nvcf.nvidia.com:443`. `riva-translate-4b-instruct-v2` ignored its target language and
  `megatron-1b-nmt` was not callable.
- Translation has no source-language detection: an empty or `auto` source is refused.
- The skill warns that **function IDs rotate per release**. The app pins one per model and,
  when it answers NOT_FOUND, looks the model up by name once (`src/laptop_agent/nvcf.py`);
  `RIVA_ASR_FUNCTION_ID`, `RIVA_TTS_FUNCTION_ID` and `RIVA_NMT_FUNCTION_ID` still override.
