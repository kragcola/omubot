# 空间日志插件（QZone Journal）— 立项 + 可行性考察

> 状态：**立项 · 可行性已实证 · 2026-06-16**
> 定位：插件级模块 `qzone_journal`。让 bot 像真人一样，把一天里"值得发的事"发到 QQ 空间（说说）。
> 关联：[Living Persona 系列](living-persona-part0-overview.md)（数据来源）、[Part B Generative Life](living-persona-partB-generative-life.md)（day_narrative/story_arc/经历洞察）、[Part C Social Narrative](living-persona-partC-social-narrative.md)（进阶版真人红线）
> 本轮交付：**仅本立项文档**（QZone 可行性考察 + 插件设计 + 红线）。**不写代码**。下一步按用户裁定回到 [A-M2](living-persona-partA-dialogue-climate.md)（living 情绪层先行）。
> 2026-07-15 状态校正：A-M2/M3/M4 已实现、提交、部署并在当前 effective config 中启用；QZone Journal 本身仍无代码，下一步是用户显式批准后启动基础版实现。

---

## 0. 一句话

`qzone_journal` 插件把 Living Persona 已经在产出的"今天过得怎样"（`schedule.day_narrative` / `story_arc.last_events` / Dream 经历洞察卡）选材、成稿为一条 QQ 空间说说。**基础版**只发 bot 自己的一天；**进阶版**联动世界书（story_arc）+ 群友共造的故事，发融合群友叙事的空间——真人严守 Part C 红线（化名 + 不虚构线下行为）。

---

## 1. 需求拆解（用户原话）

> bot 增加插件级模块，插件：空间日志。普通版本是对照一般人对一天中的事情，值得发送的发到 qq 空间。开启进阶配置后与世界书和群友共造故事联动，将发送融合群友故事的空间。

拆为两档：

| 档 | 行为 | 数据来源 | 真人 |
| --- | --- | --- | --- |
| **基础版** | 对照"一般人一天里值得发的事"，挑出值得发的，成稿发到 QQ 空间 | bot 自己的 `day_narrative` / `story_arc` / 经历洞察 | 不涉及 |
| **进阶版**（`advanced_enabled`） | 与世界书（story_arc）+ 群友共造故事联动，发融合群友故事的空间 | 基础版来源 + 群友叙事片段 | **化名 + 不虚构线下行为**（Part C 红线） |

---

## 2. 可行性考察（2026-06-16 本机 NapCat 4.15.0 实证）

**核心风险点**：标准 OneBot 11 协议**没有**发 QQ 空间（QZone 说说）的 action。能不能发，决定整个基础版是否落地。本轮已用本机 NapCat OneBot HTTP API（`localhost:29300`，无鉴权，仅本机开发环境）实测验证。

### 2.1 直接 action 探测：全不支持

探测了 `set_qzone_text` / `send_qzone` / `publish_qzone` / `qzone_publish` / `set_qzone_emotion` / `send_feed` / `get_qzone_list`、以及 `get_supported_actions`——**NapCat 4.15.0 全部返回 `不支持的Api`**。即 NapCat 没有封装的"发说说"OneBot action。

### 2.2 经典 cookie 路径：可行（已实证关键前置）

QZone 发说说的成熟非官方路径是"拿 QZone 域 cookie → 算 g_tk → POST QZone CGI"。三个前置全部实测通过：

| 前置 | 命令（本机 29300） | 实测结果 |
| --- | --- | --- |
| QZone 域 cookie | `POST /get_cookies {"domain":"user.qzone.qq.com"}`（须带 `Content-Type: application/json`） | `status:ok`，cookie len=486，**含 `p_skey=` / `skey=` / `uin=`** |
| CSRF token（g_tk/bkn） | `POST /get_csrf_token` | `status:ok`，token=（10 位数字） |
| bot uin | `POST /get_login_info` | `status:ok`，uin=384801062 |

**结论**：可行路径 = `get_cookies(user.qzone.qq.com)` 拿 `p_skey` → 算 `g_tk = bkn(p_skey)`（QZone 标准 hash）→ 以 cookie + g_tk POST QZone `emotion_cgi_publish_v6`（发文字/图片说说 CGI）。**纯 HTTP，全程不碰 NapCat 重启**（符合 CLAUDE.md NapCat 红线）。

### 2.3 风险（立项必须正面持有）

| # | 风险 | 等级 | 缓解 |
| --- | --- | --- | --- |
| Q1 | QZone CGI 是**非官方接口**，腾讯可能改版/失效 | 中 | 发布层抽象为单一 `QZonePublisher`，接口变更只改一处；失败降级为"留草稿不发" |
| Q2 | 频繁自动发说说可能触发**腾讯风控**（限频/临时封禁空间） | **高** | **事件触发**（用户裁定，见 §3）天然限频——平淡的日子不发；加硬频率上限（如 ≤1 条/天）+ 失败退避 |
| Q3 | `p_skey` 是**敏感凭证**，泄露=空间被控 | 高 | 运行时即取即用，不落盘、不入日志（按 key 名引用不打明文）；复用 NapCat 已登录态，不另存 |
| Q4 | 发空间是**公开、不可逆外发**动作 | 高 | 见 §6 发布闸门设计；默认关，灰度仅测试号 |
| Q5 | 进阶版把群友编入公开空间 → **隐私/虚构越界** | **高** | Part C 红线（§5）：化名 + 不虚构线下行为 + 私聊不入；公开空间红线**比群内叙事更严** |

---

## 3. 选材触发：事件触发（用户裁定）

用户选定 **"有事才发"**，不做每日定时。对照真人"有值得发的事才发空间"的直觉，也天然缓解 Q2 风控。

选材信号（复用 Living Persona 已产出的状态，不新造）：

- `story_arc` 有**重大进展 / setback**（`stage` 跃迁、`last_events` 出现高显著度事件）；
- 当天 `mood` / A-M1 tension 有**显著波动**（接 living 情绪层；这也是"A-M2 先行"的理由之一——情绪波动是选材信号源）；
- Dream 经历洞察卡里有**高情感强度**的一条。

平淡日（无上述信号）→ **不发**。事件触发判据做成可调阈值。

---

## 4. 数据来源（已就绪，无需新造）

进阶版要的"今天发生了什么"在 Living Persona 上线后已全部存在：

| 来源 | 字段/位置 | 用途 |
| --- | --- | --- |
| 日程叙事 | `schedule.day_narrative`（[plugins/schedule](../../plugins/schedule)） | 一天主线 |
| 剧情弧 | `story_arc.last_events` / `stage` / `next_day_seed`（[plugins/schedule/story_arc.py:47](../../plugins/schedule/story_arc.py#L47)） | 世界书联动（进阶版核心） |
| 经历洞察 | Dream `dream_reflection` memo 卡（[plugins/dream/plugin.py](../../plugins/dream/plugin.py)） | 情感强度 + 反思素材 |
| 伙伴状态 | `FictionPartnerState`（fiction-only） | 进阶版"伙伴共造故事" |

---

## 5. 进阶版真人红线（用户裁定：化名 + 不虚构，严守 Part C）

进阶版"融合群友故事"把群友编入 **公开** 空间，红线**比 Part C 群内叙事更严**：

- 真人只能以 **化名 / 代称** + **主观印象 / 共同经历** 入空间叙事；
- **不点名真实 QQ**、**不虚构其线下行为**、**私聊内容不进**；
- 数据层沿用 Part C 的 `kind` 分层：`fiction` 伙伴可自由演绎，`factual` 真人仅印象/共同经历；
- 公开外发额外约束：真人相关内容**默认更保守**，宁可只发 fiction 伙伴侧。

> 依赖：进阶版真人入叙事依赖 **Part C 主体**（真人状态画像 + 伦理红线深化），而 Part C 主体本就[搁置待独立调研](living-persona-part0-overview.md)。故**进阶版后置**，基础版（只发 bot 自己 + fiction 伙伴/arc）可先行。

---

## 6. 插件设计（草案，待实现期细化）

对标现有插件结构（`plugins/<name>/` = `plugin.py` + `config.default.json` + `config.schema.json` + `plugin.json` + `store.py`）：

```
plugins/qzone_journal/
  plugin.py            # AmadeusPlugin 子类；on_post_reply/on_tick 观察选材信号
  selector.py          # 事件触发判据：从 day_narrative/story_arc/mood/洞察 选材
  composer.py          # 成稿：LLM 把素材写成一条空间说说文案（人设语气）
  publisher.py         # QZonePublisher：get_cookies→g_tk→emotion_cgi_publish；失败降级
  store.py             # 待发/已发队列 + 频率护栏（≤N 条/天）+ 去重
  config.default.json  # enabled=false / advanced_enabled=false / 频率上限 / 触发阈值
```

**发布闸门**（Q4，本轮未让用户最终选定 §2.2 直发 vs 审核闸门 —— 实现期前需补确认）：
- 候选 A：生成后入 admin 待发列表，**人工点确认**才调 QZone（公开外发动作适合有闸门）；
- 候选 B：满足高置信触发 + 频率护栏后**自动发**（更"像真人"，但 Q2/Q4 风险高）。
- **建议默认 A**（人工审核闸门），灰度稳定后再议 B。

**开关**：`qzone_journal.enabled`（默认关）+ `advanced_enabled`（默认关）。flag 全关 = 插件不加载选材/发布，零行为变更。

---

## 7. 落地顺序（用户裁定）

1. **本轮**：只出本立项文档（✓ 完成）。
2. **下一步**：回到 [A-M2 全维情绪](living-persona-partA-dialogue-climate.md)（living 情绪层先行——情绪波动也是 §3 的选材信号源，先行有协同）。
3. **基础版**：A-M2 稳定后立项实现 `qzone_journal` 基础版（只发 bot 自己 + fiction 伙伴/arc，人工审核闸门，灰度测试号）。
4. **进阶版**：依赖 Part C 主体跑通后再立项（真人入叙事，化名+不虚构红线）。

---

## 8. 待确认（实现期前必须回答）

- [ ] 发布闸门 §6：人工审核闸门（建议默认）vs 自动发？
- [ ] 频率护栏具体阈值（≤1 条/天？事件触发置信阈值？）
- [ ] 灰度范围：先发哪个测试号的空间？（不可逆公开动作，需明确）
- [ ] g_tk 算法与 `emotion_cgi_publish_v6` 具体字段：实现期需抓一次真实请求核验（本轮只验证了 cookie/token 前置可拿到，未实发）。

---

## 附：核验命令留痕（D4）

```text
POST localhost:29300/get_version_info          → NapCat.Onebot v11 4.15.0
POST .../set_qzone_text|send_qzone|...          → 全部"不支持的Api"（无封装 action）
POST .../get_cookies {"domain":"user.qzone.qq.com"} -H json → ok，含 p_skey/skey/uin（len 486）
POST .../get_csrf_token                          → ok，g_tk token（10 位）
POST .../get_login_info                          → ok，uin 384801062
ls plugins/                                      → 24 插件，结构基线（schedule 为参照）
grep story_arc.last_events / day_narrative / dream_reflection → 数据来源已就绪
```

> 本轮**仅可行性考察 + 立项文档**，未写任何插件代码、未实发任何说说、未持久化任何 cookie。
