---
name: goal-authoring
description: Use when the user wants help writing, refining, or troubleshooting Codex built-in goals such as /goal, including converting repo context or a document into a concise long-running objective.
---

# Goal Authoring

Built-in `/goal` is a Codex product feature, not a skill. Skills can help draft or refine a goal, but they do not register the bare `/goal` slash command.

## When to use

- The user wants a good built-in `/goal ...` objective.
- The user has a long spec and needs it compressed into a durable goal.
- The user says `/goal` is unavailable and wants to understand why.

## Workflow

1. Figure out whether the user wants:
   - a short built-in `/goal ...` line,
   - a file-backed goal like `/goal follow the instructions in docs/goal.md`,
   - or diagnosis for why `/goal` is missing.
2. Prefer goals that are:
   - outcome-oriented,
   - durable across many turns,
   - specific about constraints,
   - short enough to fit naturally in one line.
3. If the source material is long, reduce it to:
   - target outcome,
   - scope boundaries,
   - quality bar,
   - stop condition.
4. When the user is troubleshooting availability:
   - explain that built-in `/goal` depends on the Codex `goals` feature flag,
   - explain that plugin slash commands are namespaced (for example `/goal-kit:draft`) and cannot replace the bare built-in `/goal` command.

## Output patterns

### Short goal

Return one recommended built-in command first:

```text
/goal <objective>
```

Then provide up to two tighter alternatives if the tradeoff matters.

### File-backed goal

If a document should stay as the source of truth, prefer:

```text
/goal follow the instructions in <path>
```

### Diagnosis

State the likely cause plainly:

- built-in `/goal` is disabled because the `goals` feature flag is off, or
- the user is trying to create a custom slash command, which requires a plugin `commands/` entry rather than a skill.
