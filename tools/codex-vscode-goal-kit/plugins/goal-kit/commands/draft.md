---
description: Draft a concise built-in /goal command from the current task and repo context
argument-hint: [extra context]
---

# Draft a Built-in Codex Goal

The user invoked this command with: $ARGUMENTS

## Instructions

Produce a concise built-in `/goal ...` command that Codex can keep pursuing over time.

1. Inspect the current user request, nearby repo context, and any explicit constraints.
2. Draft one recommended built-in command first in this exact shape:

```text
/goal <objective>
```

3. Keep the objective:
   - outcome-first,
   - durable across multiple turns,
   - explicit about boundaries when they matter,
   - short enough to paste directly into the composer.
4. After the primary recommendation, provide at most two alternatives only if they are meaningfully different.
5. If the task is too large for a single sentence, recommend a file-backed command instead:

```text
/goal follow the instructions in <path>
```

6. Do not claim to have set the goal. This command drafts the built-in `/goal` text for the user to run.
