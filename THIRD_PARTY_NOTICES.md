# Third-Party Notices

LZAgent itself is released under the MIT License (see `LICENSE`). This file
lists every upstream project that LZAgent **either vendors source code from
or whose design it directly references**, together with the corresponding
licenses and copyright notices.

The MIT-licensed projects below permit the use, copy, modification and
redistribution carried out in this repository **provided the original
copyright notice and permission notice are preserved**. This file fulfils
that requirement. For the design-pattern references where no source code is
vendored, attribution is provided here as a courtesy to the original authors
even where licensing does not strictly require it.

---

## 1. Hermes Agent

* **Project**: Hermes Agent
* **Upstream**: https://github.com/NousResearch/hermes-agent
* **License**: MIT
* **Copyright**: Copyright (c) 2025 Nous Research

### What LZAgent vendors from Hermes (verbatim source code)

* `backend/gateways/_vendor/weixin_ilink.py` — a focused port of the iLink
  Bot protocol implementation in Hermes's `gateway/platforms/weixin.py`.
  Only the QR-login + credential persistence + text-send + long-poll loop
  are kept; the Hermes-specific framework wrappers are stripped. The MIT
  license text and original copyright notice are preserved at the top of
  the file.

### What LZAgent references in design (re-implemented in Python, no copied code)

* **Long-term memory lifecycle** — the four-stage write / merge / decay /
  retrieval pipeline and the `<memory-context>` system-prompt fence, used in
  `backend/memory/`.
* **Tool-call loop guardrail** — three pathological-pattern detector
  (`backend/agent/tool_guardrails.py`), ported and simplified from Hermes's
  equivalent module.
* **Trajectory compression** — two-phase tool-result trimming + middle-round
  summary collapse strategy, mirrored in `backend/agent/trajectory.py`.
* **Tool-context propagation** — Hermes uses `HERMES_SESSION_*` env vars to
  pass current-chat identity into tool calls; LZAgent uses `contextvars` for
  the same purpose in `backend/agent/tool_context.py`.
* **Skill-manager workflow** — six-action skill CRUD surface plus the
  `metadata.hermes.created_by` / `metadata.hermes.tags` SKILL.md schema is
  inherited from Hermes (`backend/tools/builtins/skill_manage.py`, seed
  skills under `workspace/skills/`).
* **Cron + skill composition pattern** — the "agent first authors a skill,
  then schedules it via cron" workflow, in
  `backend/tools/builtins/cron_manage.py`.
* **MCP discovery & systematic-debugging skills** — `workspace/skills/mcp/
  mcp-discovery/` and `workspace/skills/coding/systematic-debugging/` are
  adapted from Hermes's same-named optional skills.

### MIT License text

```
MIT License

Copyright (c) 2025 Nous Research

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in
all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
THE SOFTWARE.
```

---

## 2. OpenClaw

* **Project**: OpenClaw
* **Upstream**: https://github.com/steipete/openclaw
* **License**: MIT
* **Copyright**: Copyright (c) 2025 Peter Steinberger

### Relation to LZAgent

OpenClaw is a TypeScript codebase; LZAgent does **not** copy any source code
from it. LZAgent references the following architectural ideas and
re-implements them independently in Python / asyncio:

* **Three-axis tool metadata** — every tool is tagged with `read_only`,
  `concurrency_safe`, and `destructive` flags, in
  `backend/tools/base.py` and consumed by
  `backend/agent/tool_permissions.py`.
* **Permission pipeline** — the `safe` / `confirm` / `deny` ladder and its
  per-call override mechanism in `backend/agent/tool_permissions.py` mirrors
  OpenClaw's permission model.
* **MCP install safety pipeline** — `--ignore-scripts` by default, an
  argument allow-list, and a subprocess timeout when running
  `npm` / `pip` / `uvx` to install MCP servers, in `backend/mcp/install.py`.

### MIT License text

```
MIT License

Copyright (c) 2025 Peter Steinberger

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in
all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
THE SOFTWARE.
```

---

## 3. nvk/llm-wiki (design-pattern reference)

* **Project**: LLM Wiki — "a wiki for LLMs / a wiki by LLMs"
* **Upstream**: https://github.com/nvk/llm-wiki  *(public design notes by @nvk;
  please verify the exact URL — it may also live as a GitHub gist)*
* **License**: Public design notes; no source code is copied into LZAgent

### Relation to LZAgent

LZAgent does **not** copy any source code from llm-wiki. It re-implements
several of llm-wiki's structural ideas in Python so that the wiki cache
behaves as a knowledge-precipitation layer rather than a flat key-value
cache. Specifically:

* **Atomic-fact crystallization ("compounding from exploration")** — the
  pattern of distilling 3-5 self-contained facts out of an LLM answer and
  storing each as its own retrievable row, in
  `backend/wiki/crystallizer.py`.
* **Wiki v2 frontmatter fields on every cache row** — `confidence`,
  `sources`, `aliases`, `superseded_by`, `last_confirmed_at`, and the
  `crystal_kind` discriminator (`"answer"` vs `"atomic_fact"`), declared on
  `WikiEntry` in `backend/db/models.py`.
* **Confidence bands and forgetting curve** — the qualitative low / medium
  / high breakpoints (low<0.5, medium 0.5-0.8, high>0.8), the +0.05-per-hit
  reinforcement, and the stance that *forgetting is gradual deprioritization,
  not deletion*. See `backend/wiki/store.py`.
* **Skills → wiki tree consolidation** — the periodic emission of a
  `workspace/knowledge/wiki/` markdown tree (concept pages +
  `[[wikilinks]]`) compatible with the standalone `llm_wiki` desktop app,
  in `backend/skills/consolidator.py`.

In-tree comments and docstrings carry inline `nvk/llm-wiki` / `LLM Wiki v2`
citations next to the relevant code so a reader can trace each idea back to
its source.

---

## 4. andrej-karpathy-skills

* **Project**: Karpathy-Inspired Claude Code Guidelines
* **Upstream**: https://github.com/forrestchang/andrej-karpathy-skills
* **License**: MIT
* **Compiled by**: Forrest Chang, distilled from
  [Andrej Karpathy's public observations](https://x.com/karpathy/status/2015883857489522876)
  on LLM coding pitfalls.

### Relation to LZAgent

LZAgent embeds a Chinese translation / adaptation of the four core
principles from this project as the `KARPATHY_CODING_PRINCIPLES` constant in
`backend/agent/loop_prompts.py`, which is spliced into both
`SYSTEM_PROMPT_DM` and `SKILL_REVIEW_PROMPT`. The four principles are:

1. Think Before Coding
2. Simplicity First
3. Surgical Changes
4. Goal-Driven Execution

No source code is copied; the prompt fragment is an original Chinese
rendering of the principles described in the upstream README.

### MIT License

The upstream `LICENSE` file simply reads `MIT` (see the upstream repo for
the full text). The standard MIT license terms apply.

---

## Future additions

Any additional third-party code or prose vendored into this repository must
be appended here with the same level of detail (upstream URL, license,
copyright, and a description of what is borrowed).
