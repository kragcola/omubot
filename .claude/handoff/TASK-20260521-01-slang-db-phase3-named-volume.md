# TASK-20260521-01 slang.db 全栈治本 Phase 3 — storage 切到 Docker named volume

## 状态

- [x] 起草（2026-05-21）
- [ ] 执行中 @ 分支 task-20260521-01
- [ ] 已合并 @ commit
- [ ] 24h 观察期通过

## 背景

`storage/slang.db` 在 2026-05-11（3 次）、2026-05-17（1 次）、2026-05-20（1 次）反复物理损坏。
根因分析见 `.claude/plans/modular-forging-allen.md`：**macOS Docker bind mount + SQLite 在
checkpoint / restart 时存在 fsync 排序漏洞**。

- Phase 1（commit `100c7d1`）已落 `close_with_checkpoint` + 主机脚本守卫
- Phase 2（commit `bc41331` + `fc7e591`）已切 slang `journal_mode=DELETE` + hourly quick_check
- Phase 3 = **真正的根因消除**：把 `./storage:/app/storage` bind mount 换成 Docker named
  volume。volume 走 Docker VM 内部 ext4，fsync 语义和 Linux 原生一致

**前置条件**：Phase 1+2 已合入，并且**稳定运行 ≥ 24 小时无 corruption / quick_check 告警**。

## 目标

1. `docker-compose.yml` 把 bot 的 `./storage:/app/storage` 改为 named volume `omubot-storage`
2. 主机侧任何还要碰 `storage/` 的脚本都得切到 `docker exec qq-bot ...`
3. 一次性把现有 `./storage/` 数据迁进 named volume（约 5min bot 服务停机；napcat 全程不动）
4. 24h 观察期内 quick_check 全绿、无 corruption alarm
5. 30 天内不再出现 `database disk image is malformed`（这是真正的成功标准）

## 约束

- **napcat 全程不动**：只 `docker compose stop bot`，不 `docker compose down`，不带 napcat 重启
- **只动 bot 服务**：`./config:/app/config:rw` 保持 bind mount（人手编辑 + 热重载需要）；
  `./admin/static:/app/admin/static:ro` 保持 bind mount（铁律 D6：只 `npm run build` 即生效）
- **不改任何 store 代码**：services/storage/sqlite.py、services/slang/*.py、services/messages/log.py
  等 0 diff
- **不改 BackupConfig / BackupScheduler**：Phase 2 已落地，不动
- **回滚必须可走**：保留 `storage.bind-mount-snapshot-*` 至少 30 天再删
- 禁止引入新 Python 依赖

## 动的文件

- 修改：`docker-compose.yml`
- 修改：`scripts/backup-databases.sh` — 改成 `docker exec qq-bot uv run python -m services.storage.backup create --host-mode`
- 修改：`scripts/dev/_bot_guard.py`（Phase 1 已建）— 扩展 named volume 检测：当检测到 named
  volume 时，强制走 `docker exec` 模式，主机 `sqlite3.connect(...)` 路径直接拒绝
- 新建：`scripts/dev/storage_export.sh` — 封装
  `docker run --rm -v omubot-storage:/src -v "$PWD/storage-export":/dst alpine sh -c "cp -a /src/. /dst/"`
- 修改：`maintenance-log.md` — 顶部追加 Phase 3 条目（变更类型 `infra-hard`）

## 不准动

- 任何 `services/**/*.py`
- 任何 `kernel/**/*.py`
- 任何 `plugins/**/*.py`
- 任何 `admin/**`（前端、后端 API 都不动）
- 任何 `tests/**`
- `pyproject.toml` / `uv.lock`
- `.claude/plans/` / `docs/`
- napcat 配置 / `napcat/*`

## 实施步骤

### A. 代码改动（不停机即可做完）

1. 读 `docker-compose.yml`，把 bot 的 `volumes` 改成：
   ```yaml
       volumes:
         - omubot-storage:/app/storage    # was: ./storage:/app/storage
         - ./config:/app/config:rw         # 不变
         - ./admin/static:/app/admin/static:ro  # 不变
   ```
   并在文件末尾追加：
   ```yaml
   volumes:
     omubot-storage:
       driver: local
   ```
2. 改 `scripts/backup-databases.sh`：把直接调 `uv run` 的部分换成
   `docker exec qq-bot uv run python -m services.storage.backup create --host-mode`，
   不再依赖主机 PATH 里有 uv
3. 扩展 `scripts/dev/_bot_guard.py`：
   - 加一个 `_storage_is_named_volume() -> bool` 检测函数（解析 `docker compose config` 或
     `docker inspect omubot-storage`）
   - 修改现有 `assert_bot_stopped(...)` 入口：如果 `_storage_is_named_volume()` 为 True，
     即使 bot 已停，也拒绝主机 `sqlite3.connect`（因为路径根本不再有数据）
4. 新建 `scripts/dev/storage_export.sh`，内容大致：
   ```bash
   #!/usr/bin/env bash
   set -euo pipefail
   mkdir -p storage-export
   docker run --rm \
     -v omubot-storage:/src \
     -v "$PWD/storage-export":/dst \
     alpine sh -c "cp -a /src/. /dst/"
   echo "exported to: $PWD/storage-export"
   ```
   `chmod +x scripts/dev/storage_export.sh`

### B. 一次性数据迁移（约 5min bot 停机；napcat 全程不动）

**这一步需要人手在 shell 里跑，不能纯靠 codex**。spec 已在「用户复制命令段」里准备好。

1. `docker compose stop bot`（**不带 napcat**，单服务停机）
2. `cp -a storage storage.bind-mount-snapshot-$(date +%Y%m%d-%H%M%S)`
3. `docker volume create omubot-storage`
4. `docker run --rm -v "$PWD/storage":/src -v omubot-storage:/dst alpine sh -c "cp -a /src/. /dst/"`
5. `docker compose up bot -d --build`（不带 napcat）
6. 观察 startup 日志：5 个 store init 成功 + 第一次 SSE `cache_pipelines` / `block_trace`
   推送 + slang plugin 不再 `disabled`

### C. maintenance-log

顶部追加条目：变更类型 `infra-hard`，记录 named volume 迁移命令 + 24h 观察期约定 + 回滚路径。
明确写「napcat 全程未动，符合铁律 D6」。

## 验收

**每条命令行可跑、能 0/非 0 判断**：

```bash
cd /Users/kragcola/OmubotWorkspace/omubot

# 1. docker-compose.yml 已切到 named volume
grep -q '^  omubot-storage:' docker-compose.yml && echo "OK-volume-declared"
grep -q 'omubot-storage:/app/storage' docker-compose.yml && echo "OK-bot-uses-named-volume"
! grep -q '\./storage:/app/storage' docker-compose.yml && echo "OK-bind-mount-removed"

# 2. config / admin/static 仍是 bind mount（铁律 D6 不能动）
grep -q '\./config:/app/config' docker-compose.yml && echo "OK-config-still-bindmount"
grep -q '\./admin/static:/app/admin/static' docker-compose.yml && echo "OK-static-still-bindmount"

# 3. backup-databases.sh 已切 docker exec 模式
grep -q 'docker exec qq-bot' scripts/backup-databases.sh && echo "OK-backup-script-uses-exec"

# 4. _bot_guard.py 含 named volume 检测
grep -q 'named_volume\|omubot-storage' scripts/dev/_bot_guard.py && echo "OK-guard-volume-aware"

# 5. storage_export.sh 已建且可执行
test -x scripts/dev/storage_export.sh && echo "OK-export-script-exists"

# 6. 测试与类型检查照常通过
uv run ruff check 2>&1 | tail -1 | grep -qE 'Found 26 errors|All checks passed' && echo "OK-ruff-no-regression"
uv run pytest -q 2>&1 | tail -3 | grep -qE '1216 passed|passed' && echo "OK-pytest"
uv run pyright 2>&1 | tail -1 | grep -qE '0 errors' && echo "OK-pyright"
```

### 迁移后运行时验证（人手）

```bash
# named volume 是否存在
docker volume inspect omubot-storage | grep -E '"Name"|"Mountpoint"'

# 容器内能列出所有 db
docker exec qq-bot uv run python -c "from pathlib import Path; print(sorted(p.name for p in Path('/app/storage').glob('*.db')))"

# slang 仍是 DELETE 模式（Phase 2 配置在 named volume 上同样生效）
docker exec qq-bot sqlite3 storage/slang.db "PRAGMA quick_check; PRAGMA journal_mode;"
# 期望输出 ok / delete

# admin 黑话页面能拿数据；BlockTrace SSE 推送正常
curl -s -H "Authorization: Bearer $ADMIN_TOKEN" http://localhost:8081/api/admin/slang/extract/runs?limit=5
```

### 24h 观察期

**部署后 24h 内**：
- 每小时 quick_check tick 全绿（admin 「运行时错误」面板无新红条）
- backup_scheduler 正常滚动（`storage/backups/` 内有新增）
- 无 `database disk image is malformed` 错误

**24h 通过后**：勾掉 `[ ] 24h 观察期通过`。

**30 天后**：删除 `storage.bind-mount-snapshot-*` 备份。

## 回滚路径

如果 24h 内出现严重回归：

```bash
cd /Users/kragcola/OmubotWorkspace/omubot

# 1. 停 bot（不停 napcat）
docker compose stop bot

# 2. revert docker-compose.yml
git checkout HEAD~1 -- docker-compose.yml  # 或手工反向 patch

# 3. 把 named volume 里的最新数据导回 host
./scripts/dev/storage_export.sh
rm -rf storage
mv storage-export storage

# 4. 重新起 bot
docker compose up bot -d --build

# 5. 删 named volume（确认数据已导出后）
docker volume rm omubot-storage
```

回滚成本可控；唯一不可逆的是 named volume 期间产生的写入需要先 export。

## 用户复制命令段

### 1. 建分支（含 dirty-worktree 保护）

```bash
cd /Users/kragcola/OmubotWorkspace/omubot

git stash push -u -m "pre-task-20260521-01" 2>&1
git checkout -b task-20260521-01

echo "branch ready; HEAD=$(git rev-parse --short HEAD)"
```

### 2. 交给 codex 执行（仅代码改动 A 段）

```bash
codex 'cd /Users/kragcola/OmubotWorkspace/omubot && 严格按照 .claude/handoff/TASK-20260521-01-slang-db-phase3-named-volume.md 执行 A 段代码改动（动 docker-compose.yml + scripts/backup-databases.sh + scripts/dev/_bot_guard.py + 新建 scripts/dev/storage_export.sh + maintenance-log.md 顶部追加 Phase 3 条目）。不要做 B 段数据迁移（那是人手部署）。完成后 git status 报告改了哪些文件，然后跑 spec 验收段 1-6 条命令。'
```

### 3. 本地验证 spec（期望全部输出 OK-*）

```bash
cd /Users/kragcola/OmubotWorkspace/omubot

# 验收命令见 ## 验收 段 1-6
```

### 4. 把 diff 给 Claude 审查

```bash
cd /Users/kragcola/OmubotWorkspace/omubot
git diff HEAD | pbcopy
# 贴给 Claude 说 "审 TASK-20260521-01"
```

### 5. Claude 审核通过后提交（**仍未部署，只是代码合入**）

```bash
cd /Users/kragcola/OmubotWorkspace/omubot

git add docker-compose.yml scripts/backup-databases.sh scripts/dev/_bot_guard.py scripts/dev/storage_export.sh maintenance-log.md
git commit -m "$(cat <<'EOF'
feat(infra): Phase 3 — storage migrate to docker named volume

Phase 3 of slang.db corruption mitigation. macOS Docker bind mount fsync
ordering hazard 真正根因消除。bot 服务 storage volume 切 named volume
omubot-storage（Docker VM ext4），config / admin/static 保持 bind mount。

主机脚本（backup-databases.sh / _bot_guard.py）切 docker exec 模式；
新增 scripts/dev/storage_export.sh 给开发者导出入口。

详见 .claude/handoff/TASK-20260521-01-slang-db-phase3-named-volume.md
EOF
)"
```

### 6. 选低峰部署窗口跑数据迁移（B 段，人手）

> **铁律**：napcat 全程不动；只 `docker compose stop bot` 单服务停机。

```bash
cd /Users/kragcola/OmubotWorkspace/omubot

# 1) 停 bot
docker compose stop bot

# 2) 主机最后一份 bind mount 快照
cp -a storage "storage.bind-mount-snapshot-$(date +%Y%m%d-%H%M%S)"
ls -la storage.bind-mount-snapshot-* | tail -3

# 3) 创 named volume + 灌数据
docker volume create omubot-storage
docker run --rm \
  -v "$PWD/storage":/src \
  -v omubot-storage:/dst \
  alpine sh -c "cp -a /src/. /dst/"

# 4) 应用 docker-compose.yml 改动后起 bot（不带 napcat）
dot_clean . 2>/dev/null
docker compose up bot -d --build 2>&1 | tail -10

# 5) 验证容器内
docker exec qq-bot uv run python -c "from pathlib import Path; print(sorted(p.name for p in Path('/app/storage').glob('*.db')))"
docker exec qq-bot sqlite3 storage/slang.db "PRAGMA quick_check; PRAGMA journal_mode;"

# 6) 浏览器验收
# 打开 http://localhost:8081/admin/，看 dashboard / slang / system 页面
# admin 「运行时错误」面板应无新红条
```

### 7. 24h 观察期通过后清理

```bash
# 24h 内确认 quick_check 全绿 + 无 corruption alarm 后，回到 spec 勾掉 [ ] 24h 观察期通过

# 30 天后删除快照（不要立刻删）
ls -la storage.bind-mount-snapshot-*
# rm -rf storage.bind-mount-snapshot-YYYYMMDD-HHMMSS  # 30 天后
```

### 8. 如果迁移出问题要回滚

```bash
cd /Users/kragcola/OmubotWorkspace/omubot

docker compose stop bot

# revert docker-compose.yml
git revert <Phase3-commit>

# 把 named volume 数据导回 host
./scripts/dev/storage_export.sh
rm -rf storage
mv storage-export storage

docker compose up bot -d --build

# 确认 bot 起来后再删 named volume
docker volume rm omubot-storage
```

## 审查要点（给 Claude 看 diff 时过一遍）

- [ ] `docker-compose.yml`：bot 的 storage 已切 named volume；config + admin/static 仍 bind mount；
      napcat 区段 0 diff
- [ ] `docker-compose.yml` 顶级 `volumes:` 段已声明 `omubot-storage`（driver: local）
- [ ] `scripts/backup-databases.sh`：使用 `docker exec qq-bot uv run ...`，不再依赖主机 PATH
- [ ] `scripts/dev/_bot_guard.py`：含 named volume 检测分支；现有 bot-running 检测逻辑保留
- [ ] `scripts/dev/storage_export.sh`：可执行权限（`ls -la` 看到 `-rwxr-xr-x`）
- [ ] `maintenance-log.md`：顶部追加 Phase 3 条目；变更类型 `infra-hard`；写明
      「napcat 全程未动，符合铁律 D6」、Phase 1+2 关系
- [ ] 「不准动」列表里的文件 0 diff（pyproject.toml / services/ / kernel/ / plugins/ /
      admin/ / tests/ / docs/）
- [ ] 没引入新 Python 依赖

## 备注

### 为什么 Phase 3 单独成 spec

- Phase 1 / Phase 2 已合入并稳定运行
- Phase 3 是 infra-hard 改动（涉及 ~5min 服务停机 + Docker volume 创建 + 数据迁移）
- 代码改动（A 段）和数据迁移（B 段）应分开走：A 段 codex 可以做，B 段必须人手
- 24h 观察期 + 30 天 corruption 验证 = 这个 spec 的真正成功标准

### Phase 1+2+3 全栈关系

| Phase | 修复层级 | 防御对象 |
|---|---|---|
| Phase 1 (`100c7d1`) | 代码层 | close 时 `wal_checkpoint(TRUNCATE)` 把 WAL 帧塞回 main db |
| Phase 2 (`fc7e591`+`bc41331`) | 数据层 | slang `journal_mode=DELETE` + `synchronous=FULL` 完全规避 WAL；hourly quick_check 早发现 |
| Phase 3 (本 spec) | 基础设施层 | bind mount → named volume，从根本消除 macOS Docker fsync 排序漏洞 |

三层叠加；任一 phase 单独存在都不足以根治反复损坏。

### 与 napcat 的边界（铁律 D6）

- 此 spec **绝不动 napcat 服务**
- napcat 自己的 volume（`./napcat/config`、`./napcat/data`）保持 bind mount，因为 napcat
  设备指纹存在文件里、改动等于触发 QQ 风控
- 任何"为了一致性也把 napcat 切 named volume"的提议都要拒绝

### 为什么 config / admin/static 不切 named volume

- `./config:/app/config:rw`：人手 `vi config/config.toml` 改完热重载，named volume 看不见
  主机文件
- `./admin/static:/app/admin/static:ro`：铁律 D6 — 前端 `npm run build` 直出，无需 docker
  rebuild。切 named volume 等于每次前端改动都要重建容器
