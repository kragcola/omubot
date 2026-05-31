# Admin Web 最小无障碍 Checklist

本文是 `admin/frontend`（Vue 3 + Naive UI SPA）的最小可访问性约束，由 UI/UX 审计 Phase C（2026-05-29）落地。目标不是一次性达到 WCAG AA 全量合规，而是**给全站定一条可执行的下限**，让后续每个新增页面/组件都能照着自查。

> 完整 WCAG 合规需要辅助技术实测 + 专家评审，本 checklist 只覆盖代码层可静态保证的部分。

## 1. 图标按钮（icon-only）

- 任何只有图标、没有可见文字的可点击控件，**必须**有可访问名称。
- 优先级：可见文字 > `aria-label` > `title`。`title` 只是 tooltip，不能作为唯一可访问名称。
- 裸 `<NIcon @click>` 不可聚焦、键盘不可达——**不要**直接给 `<NIcon>` 挂 `@click`。改用 `<button class="om-iconbtn" aria-label="...">` 包裹图标（`om-iconbtn` 见 `global.css`，自带 focus-visible 环 + hover 反馈）。
- 已经是 `<button>` / `<NButton>` 的图标按钮，补 `aria-label` 即可。

## 2. 键盘可达与焦点

- 所有交互元素必须能 Tab 到，且 Tab 顺序与视觉顺序一致。
- 全站统一 `:focus-visible` 焦点环（`global.css`），不要在局部 `outline: none` 后不补替代。
- 自定义可展开控件加 `:aria-expanded`（参考 `MenuCollapse.vue`、`ItemRow.vue`、`ConfigObjectGroup.vue`）。

## 3. 地标与跳转

- 应用外壳提供 skip link「跳到主内容」（`layouts/normal/index.vue`），指向 `#main-content`。
- 主导航用 `<nav aria-label="主导航">`，主内容区用 `<main>`（`AppPage` 已渲染 `<main>`，外壳的 `#main-content` 是聚焦目标容器）。
- 新页面统一走 `AppPage`，自动获得 `<main>` 和 `<h1>` 标题层级，不要自造裸 `<div>` 当页面根。

## 4. 状态不只靠颜色

- 成功/警告/错误等状态不能只用颜色表达，必须叠加图标或文字（参考 LogPanel 行级 level、`已暂停` 文案）。
- 错误/警告信息要能被读屏宣告：用 `role="alert"`（登录页明文传输警告是正例）或 `aria-live`。

## 5. 实时区域与图表

- 流式日志容器用 `role="log"` + `aria-live="polite"` + `aria-relevant="additions"`，暂停时 `aria-busy="false"`（`LogPanel.vue` 已内建）。
- 实时流提供 pause / resume 控件（`LogsView` 正例）。
- 纯视觉图表（CSS/SVG 柱状、sparkline）：
  - 给绘图容器 `role="img"` + 概述性 `aria-label`（把数据写成一句话，作为 data-table fallback）。
  - 装饰性的柱子/图元加 `aria-hidden="true"`，避免读屏逐个朗读。
  - 参考 `TrendChart.vue`：`role="img"` + `chartSummary` 文本替代。

## 6. 标签页与单选段（tablist / radiogroup）

先分清语义，别一律套 `tablist`：

- **真·标签页**（每个选项切换一块互斥内容面板）→ 用 WAI-ARIA tab 模式：
  - 容器 `role="tablist"` + `aria-label`；
  - 每个按钮 `role="tab"` + `:aria-selected` + `:aria-controls="<panel-id>"` + 漫游 `:tabindex`（选中项 0、其余 -1）；
  - 每块面板 `role="tabpanel"` + `id` + `aria-labelledby="<tab-id>"` + `tabindex="0"`；
  - 容器 `@keydown` 接 `onRovingKeydown`（`utils/a11y.ts`）：←→/↑↓ 移动并激活、Home/End 跳首尾。
  - 参考 `PluginsView.vue` 的 `detail-tabs`、`plugin-tabs`。
- **单选筛选段**（一组互斥选项，但只筛同一份内容、没有"每项一个面板"）→ 用 `role="radiogroup"` + `role="radio"` + `:aria-checked`，**不要**用 `tablist`（没有 tabpanel 可指）。同样接 `onRovingKeydown` + 漫游 tabindex。
  - 参考 `LogsView.vue` 的 `logs-segment`（日志级别筛选）。
- 判断口径：**点了之后是"换一块内容面板"还是"过滤当前这块内容"**——前者 tab，后者 radiogroup。

## 7. 表单

- 每个输入都要有可访问名称：`<label>` 或 `aria-label`，不要只靠 placeholder。
- Naive UI 的 `NFormItem` 会关联 label，沿用即可；脱离 `NFormItem` 的裸 input 需手动补。

## 8. 装饰性图标

- 纯装饰图标加 `aria-hidden="true"`，避免被朗读成「image」。

---

## 复用资产

| 资产 | 位置 | 用途 |
|------|------|------|
| `.sr-only` | `global.css` | 视觉隐藏但读屏可读（屏幕阅读器专用文本） |
| `.om-skip-link` | `global.css` | skip link 样式，聚焦时滑入 |
| `.om-iconbtn` | `global.css` | 可聚焦图标按钮（替代裸 `<NIcon @click>`），自带 hover + focus-visible |
| `:focus-visible` | `global.css` | 全站键盘焦点环（鼠标点击不触发） |
| `onRovingKeydown` | `utils/a11y.ts` | tablist / radiogroup 的方向键漫游 + 激活（←→↑↓ / Home / End） |

## 验收口径

新增/改动管理端 UI 时，至少自查：

- [ ] 图标按钮有可访问名称
- [ ] 键盘能 Tab 到所有交互项，焦点环可见
- [ ] 状态不只靠颜色
- [ ] 实时区/图表有文本替代或 live region
- [ ] 表单控件有 label / aria-label
