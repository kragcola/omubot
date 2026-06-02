# PJSK 角色批量录入（多形态）D3 实施清单 — 2026-06-01

> 承接 [issue17-character-recognition-2026-06-01.md](issue17-character-recognition-2026-06-01.md) 收尾的「下一步：批量录入 PJSK 同作品全 OC + V 家角色」。
> **核心设计问题（用户提出）**：PJSK 一个角色通常有三种形态 —— ① 正比立绘 ② 表情包 Q 版（chibi/SD）③ My Sekai 3D 豆腐人。三者视觉域差异大，担心混入一个 charpack 后 CCIP 均值向量互相稀释。
> **结论（实测推翻稀释假设）**：同角色四形态（正比+chibi+stamp+MySEKAI 正面）CCIP 互距 ≤0.12，跨角色 ≥0.36，阈值 0.1785 卡在宽缝里。**单 centroid 池化不稀释**，反而把侧/背面 3D 豆腐人从「漏」拉回「命中」。无需改 sidecar/registry 代码，只改批量录入脚本拉多形态。

## 0. 目标与不做

**做**：`tools/batch_enroll_pjsk.py` 从「只拉正比 cutout」扩成「拉正比 cutout + chibi-circle + MySEKAI 3D 四朝向」，全部喂给 `/build-pack` 池化成单 centroid；录入 24 个 PJSK 角色（22 roster + Emu/Mizuki 此前已录）。

**不做**：不改 sidecar `_build_pack`（单 centroid 池化已验证够用）；不改 registry DB / recognizer（识别链路 issue17 已落地）；不碰 napcat（D6）；不拉 stamp 表情包（`Stamp####` 命名不带角色、只能从噪声大的分页 `Images of X` 捞，且实测正比 centroid 对 stamp 已 0.05~0.08 命中，性价比低）。

## 1. 改动清单（四列：旧 → 新 / 文件 / 类型 / 回归）

| # | 旧 | 新 | 文件 | 类型 | 回归 |
| --- | --- | --- | --- | --- | --- |
| 1 | 只拉 `Category:Cutouts of <Full Name>` 正比立绘 | 三形态拉取：cutout（正比）+ `<Given>-chibi-circle.png`（表情 Q 版）+ `MySEKAI <given> front/left/right/back.png`（3D 豆腐人四朝向） | tools/batch_enroll_pjsk.py | 改逻辑 | 干跑 resolve_urls 三形态 URL 正确解析 ✅ |
| 2 | curl `-6`（教育网假设，IPv4 被劫持） | curl 去 `-6`、加 `-e Referer`（static.wikitide.net 防盗链，cutout 无 Referer 直接 403） | tools/batch_enroll_pjsk.py | 改请求 | IPv4/IPv6 双通 200；cutout 带 Referer 下载成功 ✅ |
| 3 | ROSTER 4 列（full/zh/cid/rel） | 加第 5 列 given-name（chibi/3D 用名）；MYSEKAI_VIEWS 常量 | tools/batch_enroll_pjsk.py | 改数据 | 20 人物 given-name 映射全 ✓；VS 6 角色 plain-name 缺失静默跳过 ✅ |
| 4 | 无 `--force` | 加 `--force`（已录角色也重录，pilot 用）；collect_images 分形态计数打印 | tools/batch_enroll_pjsk.py | 新增 flag | pilot `--force --only` 重录 tsukasa 成功 ✅ |

> sidecar `_build_pack`（[ccip-sidecar/server.py:319](../../ccip-sidecar/server.py#L319)）的均值池化、registry DB（[character_registry_db.py:143](../../services/media/character_registry_db.py#L143) character_id 去重）、recognizer catalog 均**零改动** —— 多形态只是喂更丰富的输入图，落点不变。

## 2. CCIP 实测证据（推翻稀释假设的关键数据）

**同角色（天马司）跨形态 CCIP difference 矩阵**（阈值 0.1785，越小越像）：

| | 正比art | chibi | stamp | 3D正面 | 3D侧面 |
| --- | --- | --- | --- | --- | --- |
| 正比art↔ | 0 | 0.071 | 0.063~0.080 | 0.057 | 0.229 |
| 跨角色（↔Emu） | 0.49 | 0.36 | 0.37~0.42 | 0.45 | 0.45 |

**centroid 池化策略对比**（distance 池化中心→各形态，*=命中）：

| 策略 | 正比 | chibi | stamp | 3D正面 | 3D侧面 | Emu(控制) |
| --- | --- | --- | --- | --- | --- | --- |
| A 只正比（旧） | 0.014* | 0.082* | 0.064~0.083* | 0.070* | **0.221✗** | 0.43~0.50 拒✅ |
| C 四形态全池化（采用） | 0.028* | 0.029* | 0.029* | 0.023* | **0.154*** | 0.39~0.43 拒✅ |

结论：① 池化不稀释（Emu 始终 ≥0.39 远拒）；② 加 chibi/stamp/3D 反而收紧这些形态（chibi 0.082→0.029）；③ 侧/背面 3D 是唯一旧策略漏的，池化拉回命中。

## 3. D4 完成证据（外部可观察）

**① 同模式扫描（D1）**：脚本只此一处批量录入入口；`/build-pack` 池化逻辑、registry character_id 去重已 issue17 落地，本轮不重复实现。

**② 录入结果**：全量 `enrolled=20 skipped=4(pilot) failed=0`；20 人物+Miku 每角 15 图（cutout10+chibi1+mysekai4），Rin/Len/Luka/MEIKO/KAITO 每角 11 图（VS 的 3D/chibi 多为 unit-prefixed，plain-name 缺失静默跳过，正比+chibi 已覆盖 2D 形态）。

**③ sidecar /health（全量后）**：`pack_count=26 character_count=26 registry_version=63796da9788d status=ok`。

**④ registry DB（admin API）**：26 行，`known=24 / self=1(凤笑梦) / friend=1(晓山瑞希)`，26 个全有样例缩略图。

**⑤ /identify 留出验证（全部用未参与录入的留出图）**：
- pilot 期：Tsukasa 留出 trained-cutout→tenma_tsukasa(0.042)、留出 stamp（未录形态）→tenma_tsukasa(0.048)、Kohane 3D正面→kohane(0.085)、Kanade 3D侧面→kanade(0.076)，Tsukasa 3D背面→miss(0.191，无脸预期内)。
- **假阳性自纠实证**：未录的 Saki chibi 一度误命中 xiaoshanruixi(0.128)；录入 Saki 后同图翻正 tenma_saki(0.0425) —— argmin 自纠，证明全 roster 录满后每角自匹配胜出。
- 全量 26 角竞争留出抽查：An→shiraishi_an(0.026)、Ena→shinonome_ena(0.031)、Rui→kamishiro_rui(0.029)、Nene→kusanagi_nene(0.074)、Miku 3D侧面→hatsune_miku(0.099)，**5/5 零互串**。

**⑥ 门禁**：`ruff check tools/batch_enroll_pjsk.py` All passed。

**⑦ napcat（D6）**：全程只调 `/api/admin/characters/build`（HTTP 录入），未碰 napcat/任何容器生命周期。

## 4. 回滚路径

- 脚本回滚：`git checkout tools/batch_enroll_pjsk.py`（或删未跟踪文件）。
- 录入回滚（如需清掉 PJSK 角色）：删 `config/character_packs/<id>.charpack/` 对应目录 → `POST /api/admin/characters/reload`；registry DB 行需手动 `DELETE`（scan_and_sync 只 insert 不删 orphan，issue17 已记的已知小缺口）。
- flag 关（`vision.character_recognition.enabled=false`）即整条识别链路旁路，录入数据留库不生效。

## 5. 残余风险 / 边界

- **roster 外角色假阳性**：nearest-neighbor 固有，非 24-roster 内角色的图可能误落某角阈值内（如未录前的 Saki）。缓解：roster 已录满主要 PJSK 角色；阈值 0.1785 已是收紧值。
- **VS（虚拟歌手）形态偏少**：Rin/Len/Luka/MEIKO/KAITO 仅 cutout+chibi（11 图），3D 豆腐人是 unit-prefixed（如 `MySEKAI 25ji miku`），本轮 plain-name 拉不到。如需补，可后续按 unit 前缀枚举。Miku 因有 default-unit plain-name，拿到全 15 图。
- **3D 纯侧/背面**：单张侧/背面豆腐人仍可能漏（无脸），但池化后已大幅改善；群聊里正面/2D 形态占绝大多数。
