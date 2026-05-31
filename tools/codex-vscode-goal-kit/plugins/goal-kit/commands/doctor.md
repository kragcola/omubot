---
description: Diagnose why built-in /goal is unavailable and explain the real custom slash-command path
---

# Diagnose Codex Goal Availability

## Instructions

Help the user understand why built-in `/goal` may not be working in Codex.

1. Explain the two different systems clearly:
   - built-in `/goal` is a Codex product feature,
   - custom slash commands come from plugin `commands/*.md` entries and are namespaced, for example `/goal-kit:draft`.
2. Tell the user to check whether the Codex `goals` feature flag is enabled.
3. If shell access is available, recommend this exact command:

```bash
codex features list | rg '^goals'
```

4. If `goals` is disabled, recommend enabling it:

```bash
codex features enable goals
```

5. Mention that enabling the feature may require reloading the VS Code window or reopening the Codex panel.
6. Be explicit that a skill cannot register the bare `/goal` command, and a plugin cannot override the built-in command name.
