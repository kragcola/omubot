---
name: omubot-design-system
description: Omubot admin frontend design system enforcer (Calm Ops / 雾青控制台). Use when writing or refactoring any Vue file under `admin/frontend/src/`, especially views, common components, layouts, or styles. Provides the locked color/spacing/radius tokens, Naive UI themeOverrides reference, common-component API cheat sheet, anti-patterns to reject, and PR-level visual acceptance checklist. Invoke automatically when the task touches `.vue`, `uno.config.ts`, `global.css`, `stores/app.ts`, or `docs/admin-ui-style-guide.md`. Do NOT use this skill for marketing pages, landing pages, or anything outside the admin console — its rules deliberately reject bold/maximalist aesthetics.
---

# Omubot Design System

This skill enforces the Calm Ops / 雾青控制台 design system on `admin/frontend`. It is **not** a creative-frontend skill. It rejects bold typography, asymmetric layouts, decorative motion, gradient meshes, custom cursors, and other "AI slop" defaults that violate Omubot's established system.

Read [docs/admin-ui-style-guide.md](../../../docs/admin-ui-style-guide.md) and [docs/web-refactor-plan.md](../../../docs/web-refactor-plan.md) before working on a multi-page change. For single-component edits the cheat sheet below is enough.

## 0. Hard Rules — Never Negotiate

- **Color**: only the tokens in §1. Never introduce a new primary, never use purple/blue gradients, never use pure black backgrounds.
- **Radius**: only `8 / 12 / 16`. No 4px, no 20px, no 24px (`AppCard` already provides 16; nest `AppPanelSection` for inner 18→treat as exception only inside that component).
- **Spacing**: only `4 / 8 / 12 / 16 / 24 / 32`. No 6, 10, 14, 20, 28.
- **Typography**: system font stack. Do **not** import Google Fonts, Inter, Space Grotesk, JetBrains Mono (existing monospace inheritance is fine for log/code).
- **Inline styles**: max 5 per `.vue` file, each must have a comment explaining why class/token is insufficient.
- **`!important`**: forbidden in view files. Allowed only in `global.css` for the documented Naive UI deep-overrides that themeOverrides cannot reach.
- **Asymmetry/diagonal/grid-breaking**: forbidden. The product is a long-running ops console.
- **Decorative motion**: forbidden. Hover/transition durations only `160ms / 220ms / 240ms` per style guide §4.

If a task instruction conflicts with these rules, surface the conflict before writing code.

## 1. Token Reference (locked)

All tokens are defined in [admin/frontend/src/styles/global.css](../../../admin/frontend/src/styles/global.css). Use the variables, never the raw hex.

### Color (light)

```
--om-bg              #EEF2F4   page background base
--om-bg-soft         #F7FAFB   page background highlight
--om-surface         rgba(255,255,255,.88)  card surface (with backdrop blur)
--om-surface-solid   #FFFFFF   solid card surface (drawer/modal)
--om-surface-2       #F2F7F8   embedded panel / hover row
--om-surface-3       #E6EFF1   accent surface
--om-border          rgba(111,137,146,.22)
--om-border-strong   rgba(74,109,119,.35)   hover/focus border
--om-text-1          #1F2A30   primary text
--om-text-2          #607078   secondary text
--om-text-3          #8A979D   helper / hint
--om-success         #2E8F6B
--om-warning         #C58A2B
--om-danger          #B84C5C
--om-info            #4D7892
```

### Color (dark)

```
--om-bg              #10171A
--om-bg-soft         #132027
--om-surface         rgba(26,38,44,.86)
--om-surface-solid   #1A262C
--om-surface-2       #22323A
--om-surface-3       #2A3D45
--om-border          rgba(123,149,157,.22)
--om-border-strong   rgba(140,175,185,.38)
--om-text-1          #E7F0F2
--om-text-2          #9CB0B8
--om-text-3          #768B92
```

Primary color is referenced as `rgb(var(--primary-color))`. The RGB triplet flips between light (`49, 108, 114`) and dark (`99, 178, 186`).

### Shadow

```
--om-shadow-sm       cards (default elevation)
--om-shadow-md       hover / active card / sticky toolbar
--om-shadow-lg       drawer / modal
```

Never write `box-shadow: 0 4px 8px rgba(0,0,0,.1)` literally — always go through a token.

### UnoCSS shortcuts (already defined)

```
wh-full              w-full h-full
f-c-c / flex-center  flex justify-center items-center
flex-col             flex flex-col
card-border          border border-solid border-[var(--om-border)]
auto-bg              bg-[var(--om-surface)]
auto-bg-hover        hover:bg-[var(--om-surface-2)]
auto-bg-highlight    bg-[var(--om-surface-2)]
text-highlight       rounded-8 px-10 py-4 bg-[var(--om-surface-2)] text-[var(--om-text-2)]
card-shadow          shadow-[…light…] dark:shadow-[…dark…]
```

Prefer adding a new shortcut to [uno.config.ts](../../../admin/frontend/uno.config.ts) over repeating the same util chain in 3+ files.

## 2. Common Component API Cheat Sheet

These live in [admin/frontend/src/components/common/](../../../admin/frontend/src/components/common/) and are auto-imported via `unplugin-vue-components`.

### `AppPage`

Page-level wrapper. Every view's root must be `AppPage`.

```vue
<AppPage
  :title="..."           <!-- defaults to route.meta.title -->
  :description="..."     <!-- defaults to route.meta.description -->
  :eyebrow="..."         <!-- default 'Omubot Console' -->
  :back="false"
  :show-header="true"
>
  <template #title-suffix>...</template>
  <template #action>...</template>     <!-- right-aligned action buttons -->
  <!-- default slot: page body -->
</AppPage>
```

Forbidden: writing your own page-header div with title + description + action — `AppPage` owns this region.

### `AppCard`

Generic surface. Use directly only when `AppPanelSection` doesn't fit.

```vue
<AppCard bordered elevated />        <!-- main surface card -->
<AppCard embedded />                 <!-- inner / nested block -->
<AppCard bordered interactive />     <!-- hover lifts -->
```

Boolean props: `bordered`, `elevated`, `embedded`, `interactive`. Nothing else.

### `AppPanelSection`

Section block with eyebrow / title / description / aside. Use this instead of `n-card size="small"` for any sub-block.

```vue
<AppPanelSection
  eyebrow="状态"
  title="今日表情学习"
  description="..."
>
  <template #aside><NButton>...</NButton></template>
  <!-- default slot: section body -->
</AppPanelSection>
```

### `MetricCard`

Dashboard / overview KPI card. Use it for any number-driven summary tile.

```vue
<MetricCard
  title="今日入库"
  :value="42"
  hint="较昨日 +5"
  :icon="StatsIcon"
  accent="primary"  <!-- 'primary' | 'success' | 'warning' | 'info' -->
/>
```

The accent stripe and icon color come from the accent prop. Do **not** wrap `n-statistic` in your own card; use `MetricCard`.

### `PageToolbar`

Filter / action bar that sits between the page hero and content.

```vue
<PageToolbar>
  <template #left>
    <NSelect ... />
    <NDatePicker ... />
  </template>
  <template #right>
    <NButton ...>...</NButton>
  </template>
</PageToolbar>
```

Height auto, padding 14px/16px, surface-2 background. Do **not** stack two toolbars vertically — combine inside.

### `EmptyState`

Empty list / no results state. Replaces lone "暂无数据" text.

```vue
<EmptyState
  :icon="DocumentIcon"
  title="还没有数据"
  description="启用插件后，这里会出现今日的处理记录。"
  :compact="false"
>
  <NButton>添加第一条</NButton>
</EmptyState>
```

### `AppDrawerLayout` / `AppDrawerHeader`

Drawer skeleton. All drawers must have header + scrollable body + footer action bar.

### Naive UI direct usage

Use Naive components directly when no wrapper exists: `NButton`, `NDataTable`, `NTabs`, `NTag`, `NIcon`, `NInput`, `NSelect`, `NDrawer`, `NModal`, `NSwitch`, `NPopover`, `NTooltip`. Do not build a parallel wrapper layer (no `AppButton` etc.).

## 3. Naive UI themeOverrides (single source of truth)

Theme overrides live in [admin/frontend/src/stores/app.ts](../../../admin/frontend/src/stores/app.ts) `buildThemeOverrides()`. Both light and dark are exhaustively configured: text/body/card/modal/popover/table/hover/pressed/border/divider/input/code/tab/scrollbar/shadow.

### Add a new global override

1. Edit `buildThemeOverrides` directly. Light and dark are sibling palettes — **always update both**.
2. The `common.*` block at the bottom is shared (primary/info/success/warning/error + radius + fontWeight).
3. Do **not** add `.dark .n-xxx { --n-xxx: ... !important }` to `global.css` if the token name exists in [naive-ui's GlobalThemeOverrides](https://www.naiveui.com/en-US/os-theme/docs/customize-theme).

### When `!important` is justifiable

Only when Naive's themeOverrides API does not expose the property. Today the documented exceptions (kept in `global.css`) are:

- `.dark .n-button:not(.n-button--primary-type):not(...)` — secondary button colors
- `.dark .n-menu` deep selectors — sidebar menu colors

Any new `!important` must include a comment with the exact reason it cannot use themeOverrides.

## 4. Common Anti-Patterns — Reject on Sight

| Anti-pattern | Found in | Replacement |
|---|---|---|
| `<div style="padding: 24px; background: white;">` | many views | `<AppCard bordered elevated class="p-24">` |
| `style="margin-top: 16px"` | many views | UnoCSS class `mt-16` |
| `style="font-size: 18px; font-weight: 600"` | section titles | shortcut `section-title` (add to uno.config if missing) |
| `<n-card size="small" :title="...">` | section blocks | `<AppPanelSection title="..." />` |
| `<n-statistic :value="x" />` alone | KPI tiles | `<MetricCard title=... :value=... accent="..." />` |
| `<div class="empty">暂无数据</div>` | tables/lists | `<EmptyState title="..." description="..." />` |
| `style="color: #666"` | hint text | UnoCSS `text-[var(--om-text-3)]` or class `muted-text` |
| `border-radius: 20px` | cards | `16px` (or `12px` for inner) |
| `.dark .my-thing { color: white !important }` | view scoped CSS | use token `var(--om-text-1)` and let theme switch |
| Two filter rows stacked | logs/memory | merge into one `PageToolbar` with wrap |
| Custom shadow `0 4px 12px rgba(0,0,0,.1)` | cards | `var(--om-shadow-sm)` or shortcut `card-shadow` |
| Importing Inter/Space Grotesk/Roboto | any | system font stack only |
| `n-button size="tiny"` chains | toolbars | medium (default), use icon button if space tight |

## 5. Visual Acceptance Checklist (PR self-review)

Before submitting any view-touching PR, verify:

- [ ] Inline `style="..."` count ≤ 5 in changed files (each justified by comment)
- [ ] No new `!important` in view-scoped CSS
- [ ] No new color hex literals — only tokens or shortcuts
- [ ] All radii are 8/12/16
- [ ] All spacing values are 4/8/12/16/24/32
- [ ] Light + dark mode both rendered without text contrast loss
- [ ] At 1280 / 1440 / 1920 widths nothing wraps awkwardly; at 900 narrow the toolbar collapses
- [ ] Empty states use `EmptyState`
- [ ] Section blocks use `AppPanelSection`, not `n-card size="small"`
- [ ] KPI tiles use `MetricCard`, not raw `n-statistic`
- [ ] Console has no new warnings (theme override hot-reload, prop validation)
- [ ] If a new shared pattern appeared 3+ times, it's been hoisted to `components/common/` or `uno.config.ts`

Paste this checklist into the PR description and tick each item.

## 6. When Building a New View

Follow this skeleton:

```vue
<script setup lang="ts">
// 1. data refs / computed / watch
// 2. event handlers
// 3. lifecycle (onMounted)
</script>

<template>
  <AppPage title="..." description="...">
    <template #action>
      <NButton type="primary">主操作</NButton>
    </template>

    <!-- KPI row (if applicable) -->
    <div class="grid grid-cols-4 gap-16">
      <MetricCard ... />
      <MetricCard ... />
      <MetricCard ... />
      <MetricCard ... />
    </div>

    <!-- Toolbar -->
    <PageToolbar class="mt-24">
      <template #left>...</template>
      <template #right>...</template>
    </PageToolbar>

    <!-- Section blocks -->
    <AppPanelSection class="mt-16" title="..." description="...">
      <template #aside>...</template>
      <NDataTable ... />
    </AppPanelSection>

    <!-- Drawer (teleport-attached) -->
    <NDrawer v-model:show="..." :width="560">
      <AppDrawerLayout>
        <template #header><AppDrawerHeader title="..." /></template>
        <!-- body -->
        <template #footer>...</template>
      </AppDrawerLayout>
    </NDrawer>
  </AppPage>
</template>

<style scoped>
/* only when UnoCSS classes genuinely cannot express it */
</style>
```

## 7. When Refactoring a Large View (>1000 lines)

Per [docs/web-refactor-plan.md](../../../docs/web-refactor-plan.md) §6.3:

1. **PR A — Skeleton migration**: replace inline styles, swap to `AppPage`/`AppPanelSection`/`MetricCard`/`EmptyState`/`PageToolbar`. Don't touch JS logic, API calls, or data shapes. Diff stays in template + style blocks.
2. **PR B — Component extraction** (only for 1000+ line views): split repeated blocks (3+ occurrences) into `views/<area>/components/Xxx.vue`. Target main view file ≤ 600 lines.
3. **PR C — Visual polish**: implement style guide §5 page-level recommendations (e.g., Dashboard hero, Logs terminal panel, Login double-layer composition).

Each PR must pass §5 checklist independently.

## 8. Do NOT Do

- Do not propose switching frameworks (React, Tailwind, shadcn, ChakraUI).
- Do not add new top-level CSS files. Extend `global.css` or `uno.config.ts`.
- Do not add a CSS-in-JS solution (styled-components, emotion).
- Do not import icon packs other than `@vicons/ionicons5` (already installed). For new icons request specific Ionicons5 names.
- Do not regenerate `package-lock.json` unless explicitly asked.
- Do not commit `node_modules/` or `admin/static/assets/` (per refactor plan §3).
- Do not write `:deep()` style escape hatches when a Naive themeOverride covers it.
- Do not invent new tokens like `--om-accent-2`. Pick an existing one.
- Do not leave inline `console.log` in committed code.

## 9. References

- Style guide: [docs/admin-ui-style-guide.md](../../../docs/admin-ui-style-guide.md)
- Refactor plan: [docs/web-refactor-plan.md](../../../docs/web-refactor-plan.md)
- Agent UI guidelines: [docs/agent-ui-guidelines.md](../../../docs/agent-ui-guidelines.md)
- Companion skill (broader scope): `omubot-admin-console`

This skill is the **strict version**. When the task is "build something", load both `omubot-admin-console` (workflow) and `omubot-design-system` (visual rules). When the task is just "fix a class/spacing/color", `omubot-design-system` alone is enough.
