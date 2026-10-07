# omubot-new

**Omubot 的独立原生重构版：面向持续角色聊天，把记忆、关系、角色生活和表达组织接入同一条可追踪、可撤权的对话链。**

> **源码公开进度 · 2026-10-07**：`main` 已切换为当前运行候选对应的新版源码，包含 Python 核心、前端源码与静态资源、依赖锁、通用配置和必要构建检查。个人运行资料、完整开发测试与内部审计记录不在此快照内。新版正在进行第三阶段受控实机测试，**尚未通过上线验收**。

[阅读 Wiki](https://github.com/kragcola/omubot/wiki) · [当前能力与限制](https://github.com/kragcola/omubot/wiki/Status) · [新旧项目关系](https://github.com/kragcola/omubot/wiki/Migration)

## 为什么重构

旧 Omubot 已积累聊天、人格、记忆学习、角色识别、故事与管理功能，也提供了实际使用的体验基准。随着功能增长，代码和状态归属逐渐复杂，配置、任务生命周期、授权与外部发送之间更难保持一致。继续叠加规则，既增加维护成本，也难以可靠解释“为什么没回复、用了哪条记忆、消息究竟有没有发出”。

新项目继承经过确认的行为目标与经验，在独立仓库重新实现。目标是同时保留角色聊天的自然感、建立可靠的运行边界，并让后续改进有真实效果证据；代码更整齐并不等于体验已经超过旧版。

## 有哪些不同

| 方向 | 新版做法 | 带来的价值 |
| --- | --- | --- |
| 架构 | 原生 Python 单体，核心—服务—应用三层职责，显式装配；直接接入必要的 OneBot 协议面 | 按实际状态和生命周期拆分，不再围绕完整插件平台预建骨架 |
| 权限与发送 | 统一 Policy / Actions 出口，保留来源、期限、撤权、真实回执和未知结果 | 模型的建议、关系变化或功能开关不能自行获得发送权限 |
| 聊天调度 | 有界会话并发、轮次版本、取消与部分可见记录 | 旧轮次迟到结果不得继续生效；已发送的内容按真实事实保留 |
| 配置与管理 | Vue 管理端，已保存与正在运行的版本分开，版本冲突明确拒绝 | 能看清“保存了什么、实际用了什么”，避免多个配置来源互相覆盖 |
| 记忆与角色 | 固定人格、真人事实、表达偏好、临时状态、事项与虚构生活分域 | 可以学习和纠正，避免把一句临时情绪或模型编造写成永久事实 |
| 表达与主动行为 | 从回应目的组织正文与节拍；主动联系同时核用户与群的独立授权 | 自然感属于有边界的产品行为，不靠提高频率或绕过保护获得 |
| 交付 | 核心原生运行优先，Web 静态资源随 Python 包交付，外部组件独立部署 | 运行核心不要求容器编排；跨平台仍按实际环境分别验收 |

这些是已采用的结构与设计取向，不是更快、更省内存或更好聊天的无条件性能承诺。

## 能力方向

- **角色对话**：人格与固定设定、群聊上下文、Thinker 判断、工具使用、分段表达、打断与失效保护。
- **记忆与学习**：事实、黑话、表达、纠正与审核、知识检索、当前事项；学习范围与来源资格受控。
- **角色连续性**：关系与好感度、当前关切、日程与故事、明确标记的虚构梦境；真人事实与角色生活分开。
- **情感表达**：依据本轮事件、角色和关系组织回应，持久情绪状态只影响获准的表达消费；效果需要实际验证。
- **有理由的主动联系**：每次都有真实对象和来源，用户与群双授权，窗口与频率上限不因好感度或情绪自动扩大。
- **工具与管理**：多种模型协议适配、网页与知识工具、富媒体相关能力，以及配置、权限、用量、诊断和恢复管理。

完整清单不代表全部已启用或验收；请以 [Wiki 能力状态](https://github.com/kragcola/omubot/wiki/Status) 为准。新版不是通用插件市场，也不承诺任意第三方插件热安装。

## 当前推进到哪里

| 阶段 | 当前状态 |
| --- | --- |
| 一 · 核心能力代码 | 当前已批准范围完成本地实现与必要检查 |
| 二 · 附加能力代码 | 当前已批准范围完成隔离验证与候选发行；不等于全部旧功能迁移 |
| 三 · 测试与上线检测 | **进行中，未通过**。已有有限 QQ 文字实收与人工阅读检查；固定候选长窗仍在进行 |
| 四 · 真实使用反馈 | 尚未开始，待第三阶段与用户验收后按获准范围推进 |

当前仍需处理主动联系候选缺证、图片实际发送失败、部分回复质量问题，以及富输入、外部服务、多日行为、资源和 Linux / Windows 原生验收。字词碎片式分段的本地实现通过，不代表模型已在真实聊天中自然选择。经历抽取质量保持未通过，不放宽证据与隐私要求。

## 文档导航

| 想了解什么 | 入口 |
| --- | --- |
| 整体目标与阅读路线 | [Wiki 首页](https://github.com/kragcola/omubot/wiki/Home) |
| 核心、服务、应用如何协作 | [架构](https://github.com/kragcola/omubot/wiki/Architecture) |
| 配置、人格与日常管理 | [配置](https://github.com/kragcola/omubot/wiki/Configuration) · [管理端](https://github.com/kragcola/omubot/wiki/Web-Console) |
| 聊天、分段、记忆与学习 | [对话](https://github.com/kragcola/omubot/wiki/Conversation) · [人格与记忆](https://github.com/kragcola/omubot/wiki/Persona-and-Memory) |
| 情感如何衔接其他组件 | [情感与角色连续性](https://github.com/kragcola/omubot/wiki/Emotion-and-Continuity) |
| 主动联系的边界 | [主动联系](https://github.com/kragcola/omubot/wiki/Autonomous-Contact) |
| 如何安装与保护既有环境 | [开始使用](https://github.com/kragcola/omubot/wiki/Getting-Started) · [部署](https://github.com/kragcola/omubot/wiki/Deployment) |
| 权限、失败与未知结果 | [权限与安全边界](https://github.com/kragcola/omubot/wiki/Permissions-and-Safety) |

## 获取源码与离线验证

```sh
git clone https://github.com/kragcola/omubot.git
cd omubot
uv sync --locked --python 3.12
uv run python scripts/dev/demo-loop.py
```

源码包含与当前候选一致的预构建 Web 静态资源，首次离线运行无需启动 NapCat。修改前端时再运行 `npm --prefix web ci` 与 `npm --prefix web run build`。正式账号接入和上线仍须单独授权与验收，见 [开始使用](https://github.com/kragcola/omubot/wiki/Getting-Started)。

旧实现保留在 Git 历史中，旧部署命令不能用于新版。沿用本仓库的 [MIT License](https://github.com/kragcola/omubot/blob/main/LICENSE)。
