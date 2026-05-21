---
name: omubot-admin-console
description: Omubot-specific workflow for admin/frontend page refactors, Calm Ops style unification, architecture/wiki/code audits, and surgical project changes. Use when working in this repository on `admin/frontend` views or common components, `docs/` or `docs/wiki/`, `admin/routes/api/`, `services/`, `plugins/`, maintenance notes, or project docs; especially when the task involves reviewing current architecture, extracting findings from old docs/logs/code, or restyling/admin console pages while preserving existing business behavior.
---

# Omubot Admin Console

Use this skill to keep Omubot work aligned with the project's actual constraints:

- Rebuild admin pages in the established `Calm Ops / 雾青控制台` style
- Audit old wiki, docs, logs, and code without reading the whole repo blindly
- Make small, evidence-based changes instead of speculative refactors
- Keep `maintenance-log.md` updated when a task creates durable project changes or new delivery/process milestones

Read these project docs first when the task matches:

- `docs/agent-ui-guidelines.md`
- `docs/admin-ui-style-guide.md`

Then choose the workflow below.

## Maintenance Log Policy

When a task changes repo behavior or team coordination in a way that future sessions may rely on, update `maintenance-log.md` in the same turn before you finish.

Common triggers:

- deployment, runtime, config, routing, API, or storage behavior changes
- admin/frontend milestone progress worth handing off to the next session
- process, docs, skill, or workflow changes that affect how Codex should work in this repo

Log discipline:

- append the new entry near the top in reverse chronological order
- keep the existing structure: title, change type, content, impact, and handoff/deploy notes when relevant
- if the task is only exploratory or produces no persistent repo change, you may skip the log

## Workflow A: Admin Web Refactor

Use this workflow when the task touches `admin/frontend`.

1. Read the page's existing implementation and the relevant API route before designing changes.
2. Identify the page role: overview, list, editor, monitor, or detail-heavy console.
3. State brief design decisions before editing:
   - page role
   - information hierarchy
   - which shared components to reuse
   - what should remain unchanged in business behavior
4. Reuse the established common components first:
   - `admin/frontend/src/components/common/AppPage.vue`
   - `admin/frontend/src/components/common/AppCard.vue`
   - `admin/frontend/src/components/common/MetricCard.vue`
   - `admin/frontend/src/components/common/EmptyState.vue`
   - `admin/frontend/src/components/common/PageToolbar.vue`
5. Use already unified pages as visual anchors when relevant:
   - `login`, `dashboard`, `system`, `logs`
   - `groups`, `memory`, `plugins`, `knowledge`, `usage`
6. Preserve the Omubot admin style:
   - calm, technical, slightly companionable
   - no purple SaaS gradients
   - no emoji-as-icon
   - no fake data or decorative fluff
   - prefer clearer hierarchy over louder visuals
7. Prefer structural cleanup over cosmetic churn:
   - replace scattered inline styles
   - introduce clearer hero, toolbar, surface, and empty states
   - do not rewrite business logic unless the task requires it
8. Verify frontend changes in `admin/frontend`:
   - `./node_modules/.bin/vue-tsc --noEmit`
   - `npm run build`
9. Report what changed, what stayed intentionally unchanged, and what was verified.

## Workflow B: Architecture, Wiki, and Code Audit

Use this workflow when the task is to understand the project, review legacy docs, or produce findings.

1. Start from high-level docs:
   - `docs/architecture.md`
   - `docs/operations.md`
   - `docs/project-info.md`
   - `docs/setup-guide.md`
2. Then read the old wiki selectively:
   - `docs/wiki/Home.md`
   - `docs/wiki/Architecture.md`
   - `docs/wiki/Configuration.md`
   - `docs/wiki/Plugins.md`
3. Only after that, drill into implementation:
   - `admin/routes/api/`
   - `services/`
   - `plugins/`
   - `admin/frontend/src/views/`
4. Read in layers, not all at once:
   - locate likely directories first
   - read summary files
   - read only relevant code or doc slices
5. Keep conclusions evidence-based:
   - conclusion
   - supporting files
   - uncertainty
   - next files worth checking
6. If the user asks for a review, present findings first and order them by severity.

## Workflow C: Incremental Omubot Changes

Use this workflow for any code or doc change in the repo.

1. Think before coding:
   - do not silently choose between multiple interpretations
   - state assumptions when they matter
2. Keep the solution small:
   - no speculative abstractions
   - no “future flexibility” unless requested
3. Make surgical edits:
   - touch only files directly related to the task
   - do not clean unrelated code just because you saw it
4. Preserve project truth:
   - if docs and implementation disagree, call it out
   - if you touch maintenance or project-status docs, keep them consistent
5. Close with verification:
   - run the smallest meaningful checks
   - say explicitly if something could not be verified
6. Update `maintenance-log.md` when the change crosses the maintenance-log policy threshold above.

## Quick Triggers

Invoke this skill for prompts like:

- “审查旧 wiki 和当前代码，判断现在架构”
- “统一后台某个页面的风格”
- “给新加的管理端页面做审计和修复”
- “继续按现有 Omubot 风格重构 admin/frontend”
- “从 docs、日志、接口和页面里整理当前正在做的事情”

Do not invoke this skill for:

- generic one-off coding outside Omubot
- pure image generation tasks
- video presentation production tasks
- backend-only work with no Omubot repo context

## Output Standard

End every task with:

- what was changed or concluded
- what evidence or verification supports it
- what risks or open questions remain
