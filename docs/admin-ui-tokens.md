# Admin UI Token Cheat Sheet

Omubot 管理端 Calm Ops / 雾青风格的 token 快速查表。实现位置见：

- `admin/frontend/src/styles/global.css` — CSS 变量
- `admin/frontend/src/stores/app.ts` — Naive UI `themeOverrides`
- `admin/frontend/uno.config.ts` — UnoCSS shortcuts

完整规范见 [admin-ui-style-guide.md](admin-ui-style-guide.md)。

## 1. 色板

### 主色

| 用途 | 值 |
|------|------|
| 主色 | `#316C72` |
| 主色 hover | `#3C7B82` |
| 主色 pressed | `#274E53` |
| 深色模式主色 | `#63B2BA` |

在 CSS 里引用：`rgb(var(--primary-color))` 或 `color-mix(in srgb, rgb(var(--primary-color)) 12%, transparent)`。

### 背景与表面

| 变量 | 浅色 | 深色 | 用途 |
|------|------|------|------|
| `--om-bg` | `#EEF2F4` | `#10171A` | 页面背景 |
| `--om-bg-soft` | `#F7FAFB` | `#132027` | 页面渐变终点 |
| `--om-surface` | `rgba(255,255,255,.88)` | `rgba(26,38,44,.86)` | 卡片表面（含 blur） |
| `--om-surface-solid` | `#FFFFFF` | `#1A262C` | 实色卡片（drawer/modal） |
| `--om-surface-2` | `#F2F7F8` | `#22323A` | 嵌入面板、hover 行 |
| `--om-surface-3` | `#E6EFF1` | `#2A3D45` | 强调表面 |

### 边框

| 变量 | 浅色 | 深色 |
|------|------|------|
| `--om-border` | `rgba(111,137,146,.22)` | `rgba(123,149,157,.22)` |
| `--om-border-strong` | `rgba(74,109,119,.35)` | `rgba(140,175,185,.38)` |

### 文字

| 变量 | 浅色 | 深色 | 层级 |
|------|------|------|------|
| `--om-text-1` | `#1F2A30` | `#E7F0F2` | 一级 |
| `--om-text-2` | `#607078` | `#9CB0B8` | 二级（说明） |
| `--om-text-3` | `#8A979D` | `#768B92` | 三级（提示） |

### 状态色

| 变量 | 值 |
|------|------|
| `--om-success` | `#2E8F6B` |
| `--om-warning` | `#C58A2B` |
| `--om-danger` | `#B84C5C` |
| `--om-info` | `#4D7892` |

## 2. 阴影

| 变量 | 用途 |
|------|------|
| `--om-shadow-sm` | 卡片默认阴影 |
| `--om-shadow-md` | hover / active 卡片 / sticky 工具栏 |
| `--om-shadow-lg` | drawer / modal |

## 3. 间距

允许值：`4 / 8 / 12 / 16 / 24 / 32`。UnoCSS 直接用 `p-16 mt-24 gap-12`。

禁用值：6 / 10 / 14 / 20 / 28（style guide §2.4）。

## 4. 圆角

允许值：`8 / 12 / 16 / 999`（999 表示胶囊）。UnoCSS 直接用 `rounded-12 rounded-999`。

禁用值：4 / 20 / 24（`AppCard`/`AppPanelSection` 内部的 18 是既有例外）。

## 5. 字号字重

| 场景 | 字号/字重 |
|------|----------|
| 页面标题 | `24 / 600` |
| 区块标题 | `18 / 600` |
| 卡片标题 | `16 / 600` |
| 正文 | `14 / 400` |
| 辅助文字 | `12 / 400` |
| 数据大数字 | `28 / 700` |

等宽字体：日志 / ID / 命令场景用 `font-mono`（系统等宽）。

## 6. UnoCSS Shortcuts

### 布局工具

| shortcut | 展开 |
|----------|------|
| `wh-full` | `w-full h-full` |
| `f-c-c` / `flex-center` | `flex justify-center items-center` |
| `flex-col` | `flex flex-col` |

### 表面

| shortcut | 展开 |
|----------|------|
| `card-border` | `border border-solid border-[var(--om-border)]` |
| `auto-bg` | `bg-[var(--om-surface)]` |
| `auto-bg-hover` | `hover:bg-[var(--om-surface-2)]` |
| `auto-bg-highlight` | `bg-[var(--om-surface-2)]` |
| `card-shadow` | 浅/深自适应阴影 |
| `panel` | `rounded-12 bg-[var(--om-surface)] border card-border p-16` |

### 排版

| shortcut | 展开 |
|----------|------|
| `section-title` | `text-16 font-600 text-[var(--om-text-1)]` |
| `section-hint` | `text-12 text-[var(--om-text-3)] leading-relaxed` |
| `metric-num` | `text-28 font-700 text-[var(--om-text-1)] tracking-tight` |
| `text-highlight` | `rounded-8 px-10 py-4 bg-[var(--om-surface-2)] text-[var(--om-text-2)]` |

### 元素

| shortcut | 展开 |
|----------|------|
| `chip` | `inline-flex items-center gap-4 px-8 py-2 rounded-999 text-12 border card-border` |
| `toolbar-row` | `flex items-center justify-between gap-12 py-12` |

## 7. Naive UI `themeOverrides`

主配置在 `src/stores/app.ts` `buildThemeOverrides()`，同时配置浅 / 深两套：

- `common.primaryColor / infoColor / successColor / warningColor / errorColor` — 状态色
- `common.textColorBase / textColor1/2/3` — 文字
- `common.bodyColor / cardColor / modalColor / popoverColor` — 表面
- `common.hoverColor / pressedColor / actionColor` — 交互反馈
- `common.borderColor / dividerColor / inputColor / tableColor` — 边框与输入
- `common.borderRadius / borderRadiusSmall` — `16 / 12`
- `common.placeholderColor / iconColor` — 占位符与图标
- `Tag.colorBordered / textColor / borderColor` — 标签
- `DataTable.thColor / tdColor / borderColor` — 数据表

**添加新覆盖**：优先在 `buildThemeOverrides()` 里加，不要往 `global.css` 写 `!important`。浅深两套同时更新。

## 8. 动效时长

| 场景 | 时长 |
|------|------|
| 卡片 hover | `160ms` |
| 抽屉 / 弹层 | `220ms` |
| 页面切换 | `240ms` |

禁用夸张弹跳、大位移动画。

## 9. 反面清单

- 不用 `Inter / Roboto / Space Grotesk / JetBrains Mono` — 用系统字体栈
- 不用紫色渐变、纯黑背景、对角不对称
- 不写 `style="..."` 内联样式（≤ 5 处且带注释例外）
- 不写 `!important`（view 文件里 0，`global.css` 里只保留 `@audit keep` 注释的例外）
- 不裸用 `n-card size="small"` — 用 `AppPanelSection`
- 不裸用 `n-statistic` 当 KPI — 用 `MetricCard`
- 不写 `<div class="empty">暂无数据</div>` — 用 `EmptyState`
