---
description: Turn a spec file into a recommended built-in /goal command
argument-hint: <path-to-file>
allowed-tools: [Read, Glob, Grep]
---

# Build a Codex Goal from a File

The user invoked this command with: $ARGUMENTS

## Instructions

1. Treat `$ARGUMENTS` as the path to the source document.
2. If no path is provided, say that this command expects a file path and show one example:

```text
/goal-kit:from-file docs/goal.md
```

3. Read the target file.
4. Decide whether the best built-in goal is:
   - a direct file-backed command, or
   - a shorter distilled objective.
5. Prefer the file-backed form when the document should remain the source of truth:

```text
/goal follow the instructions in <path>
```

6. Prefer a distilled one-line goal only when the file is simple enough to compress without losing critical constraints.
7. Return:
   - one recommended built-in command,
   - one brief explanation of why that form is the better fit.
8. Do not claim to have set the goal. This command drafts the built-in `/goal` text for the user to run.
