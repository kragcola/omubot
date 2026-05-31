# Admin Web 运行态交互约定

本文是 `admin/frontend`（Vue 3 + Naive UI SPA）运行态交互的统一约定，由 UI/UX 审计 Phase D（2026-05-29）落地。覆盖三件事：**实时刷新该用什么**（timer / polling / SSE 选型）、**keepAlive 页面的缓存准则**、**复杂控制台页的 composable 拆分规则**。

> 审计依据：`docs/audits/admin-web-ui-ux-full-audit-2026-05-29.md` §4.8 / §4.10 / §4.12 Phase D。P2 结论：SSE 已抽象成单例，但各页仍自己写 `setInterval` / polling，节奏与清理习惯不统一；keepAlive 接线合理但「是否缓存」纯靠人工 meta，缓存语义可能漂移。

姊妹文档：可访问性下限见 `docs/admin-a11y-checklist.md`。

## 1. 实时刷新选型（先选机制，再写代码）

按数据来源和刷新动机选，**不要默认 `setInterval`**：

| 场景 | 用什么 | 理由 |
|------|--------|------|
| 后端主动推送的运行态（日志流、群消息、群活跃度、缓存命中、block_trace） | **SSE 单例**（`composables/useSSE.ts`） | 全站一条 `EventSource`，零客户端轮询，断线自动重连 |
| 纯前端的「相对时间」重渲染（刚刚 / N 分钟前） | **`useNowTick()`**（`composables/useNowTick.ts`） | 不发请求，只推进一个时间 ref；keepAlive 友好 |
| 拉一个有限、会结束的后端任务进度（autopilot run-all） | **局部 `setInterval` + 必清理**（见 §2.3） | 任务态轮询，必须挂 unmount/deactivate 兜底 |
| 一次性数据加载 / 手动刷新 | `onMounted` 里 `await` + 显式「刷新」按钮 | 后台控制台可接受手动刷新，不必自动轮询 |

硬规则：

- **能 SSE 就不要 polling。** 新的「服务端状态变化要反映到前端」需求，优先在 `useSSE.ts` 加一个事件类型 + 订阅 helper，而不是在页面里起 `setInterval` 拉接口。
- **相对时间统一走 `useNowTick()`。** 不要在页面里手写 `setInterval(() => tick++)`。
- **裸 `setInterval` 仅用于「有终点的任务态轮询」**，且必须遵守 §2.3 的清理约定。

## 2. 定时器与清理约定

### 2.1 单例 SSE（已就位，照用即可）

`composables/useSSE.ts` 是模块级单例：一条 `EventSource('/api/admin/events')` 被所有 `useSSE()` 调用方共享，引用计数挂在 `onMounted`/`onUnmounted`，最后一个订阅者卸载才断开，`onerror` 后 5s 自动重连。

消费方约定：

- 只读日志流：`const { logs, connected } = useSSE()` 即可，清理由单例引用计数兜底。
- 订阅具体事件：`onGroupMessage` / `onGroupActivity` / `onCachePipelines` / `onBlockTrace` 都返回 **unsubscribe 函数**，必须在 `onUnmounted` 里调用（keepAlive 页见 §3）。
- 新事件类型：在 `_connect()` 内 `addEventListener`，转发到 `eventBus`，再导出一个 `onXxx(handler): () => void` 订阅 helper。保持「事件解析在单例、业务在页面」的边界。

### 2.2 相对时间 ticker：`useNowTick()`

唯一指定的 wall-clock 刷新方式。基于 VueUse `useIntervalFn`（scope dispose 自动 clear），并且 **keepAlive 友好**：`onDeactivated` 暂停、`onActivated` 立即补一次并恢复。

```ts
import { useNowTick } from '@/composables/useNowTick'

// 默认 30s；需要亚分钟新鲜度（如时间轴当前段）才用 20s
const { now } = useNowTick(20_000)
// 在 computed / 模板里读 now.value 即订阅；纯订阅可写 void now.value
```

cadence 选择：相对时间标签分钟粒度 → 30s 足够；只有可见的亚分钟进度（dashboard 当天时间轴当前段高亮）才用 20s。**不要更密**，后台不是秒表。

### 2.3 任务态轮询：裸 setInterval 的清理铁律

仅当「轮询一个会结束的后端任务进度」时才允许裸 `setInterval`（当前唯一正例：`LearningViewV2.vue` 的 autopilot `run-all` 进度）。必须同时满足：

1. **句柄提到组件作用域**，不要藏在 `async` 函数局部变量里——否则一旦函数还在 `await`，外部无法清理。
2. **任务正常结束清一次**（`await runPromise` 之后 `clearInterval`）。
3. **挂 unmount + deactivate 兜底**：`onUnmounted(stop)` + `onDeactivated(stop)`，保证 mid-run 离开页面（含 keepAlive 缓存）时定时器不泄漏。

```ts
let poll: ReturnType<typeof setInterval> | null = null
function stopPoll() { if (poll) { clearInterval(poll); poll = null } }
onUnmounted(stopPoll)
onDeactivated(stopPoll)   // keepAlive 页缺这行 = 缓存后台泄漏
// ...任务循环里：poll = setInterval(...); const data = await runPromise; stopPoll()
```

反模式（Phase D 前的实际 bug）：`const pollInterval = setInterval(...)` 声明在 `triggerAutopilotAll()` 内，只在 `await runPromise` 之后清理。若组件在请求 pending 期间卸载/被缓存，这个 3s 轮询会一直跑下去。

## 3. keepAlive 缓存准则

### 3.1 核心陷阱：`onUnmounted` 在缓存页不触发

`App.vue` 通过 `route.meta.keepAlive` 决定是否 `<KeepAlive>` 包裹。被缓存的页面切走时**不会触发 `onUnmounted` / `onBeforeUnmount`**，只触发 `onDeactivated`；切回触发 `onActivated`（不是 `onMounted`）。

后果：keepAlive 页里**任何**只在 `onUnmounted` 清理的 `setInterval` / 订阅，切到别的页后会在后台继续跑——这正是审计 P2「缓存语义不可预测」的根因。Phase D 前 dashboard / groups / learning 三个 `keepAlive: true` 页的相对时间 ticker 全部在后台空转。

### 3.2 规则

- **keepAlive 页的所有定时器/订阅，清理必须覆盖 `onDeactivated`，不能只写 `onUnmounted`。** 优先用已封装好的 `useNowTick()`（内建 deactivate 暂停）和单例 SSE（引用计数），它们已处理这点；裸定时器按 §2.3 双挂 `onUnmounted` + `onDeactivated`。
- **`onActivated` 负责「补刷新」**：切回缓存页时，若数据可能已过期，在 `onActivated` 里触发一次静默刷新（而不是依赖 `onMounted`，它不会再触发）。
- **`meta.keepAlive` 的取舍**（人工维护，给个判断口径）：
  - **该缓存**：状态重、重建成本高、用户会频繁来回切的工作台（groups / config / plugins / learning / knowledge / memory）。
  - **不该缓存**：详情页（如 `plugin-detail` 已显式 `keepAlive: false`）、一次性向导、状态应随每次进入重置的页面。
  - 加 `keepAlive: true` 前先确认：这个页有没有定时器/订阅？若有，是否已按 §3.2 覆盖 `onDeactivated`？没确认就别加。

## 4. 复杂控制台页的 composable 拆分规则

### 4.1 现状与阈值

审计 §4.10：全局 Pinia 用得克制（对的），问题在**单文件视图状态规模过大**。当前 `.vue` 行数 top（仅作参考，非强制重构清单）：GroupsView 2544、ConfigView 2223、DashboardView 1867、PluginsView 1709、LogsView 1337、PersonaImporterView 1235。

判断「该拆」的信号（命中 2 条以上就考虑抽 composable）：

- 单文件 > ~800 行，且 `<script setup>` 里 ref/computed/函数密集；
- 同一组 `ref` + 定时器/订阅 + 清理逻辑在多个页面**重复出现**（如相对时间 ticker——已抽成 `useNowTick`）；
- 一段逻辑有**自己的生命周期**（mount 起、unmount/deactivate 清理），适合连状态带清理一起搬走。

### 4.2 怎么拆

- **优先抽「带生命周期的运行态逻辑」**，而不是纯展示。运行态逻辑（定时器、SSE 订阅、轮询、它们的 ref 和清理）最适合做成 composable，因为 composable 能自己用 `onUnmounted`/`onDeactivated` 收尾，调用方一行接入。`useNowTick` / `useSSE` 就是范例。
- **composable 命名 `useXxx`，放 `src/composables/`**，单一职责，返回 ref/方法对象。事件解析/连接管理留在 composable，业务处理留在页面。
- **子视图容器拆分**（learning v2 已是范例）：把一个 tab/模式拆成独立子组件 + 一个共享 `useXxxConsole()`（见 `views/learning/v2/useLearningConsole.ts`），状态在 composable、各 tab 是薄渲染层。
- **不为拆而拆**：纯展示模板、一次性页面、行数高但逻辑线性的页面，拆了只增加跳转成本。先抽「重复出现的」和「带生命周期的」，其余留观察。

### 4.3 已沉淀的复用资产

| 资产 | 位置 | 用途 |
|------|------|------|
| `useSSE()` + `onXxx` 订阅 helper | `composables/useSSE.ts` | 单例 SSE，服务端推送的运行态 |
| `useNowTick(intervalMs?)` | `composables/useNowTick.ts` | keepAlive 友好的相对时间 ticker |
| `useLearningConsole()` | `views/learning/v2/useLearningConsole.ts` | 控制台多 tab 共享状态的范例 |

## 5. 验收口径

- 新增「实时刷新」前，先按 §1 表格选机制，SSE 优先于 polling。
- 不手写相对时间 `setInterval`，统一 `useNowTick()`。
- 裸 `setInterval` 只用于任务态轮询，且句柄在组件作用域、`onUnmounted` + `onDeactivated` 双清理。
- 给 `keepAlive: true` 的页加/改定时器/订阅时，确认清理覆盖 `onDeactivated`。
- 重复出现或带生命周期的运行态逻辑，抽成 `composables/useXxx.ts`，不为拆而拆。
