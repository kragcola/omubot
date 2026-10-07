# 开发与检查

本页适用于公开新版源码快照。包含运行与构建输入及必要检查；完整开发测试、内部治理文档与审计记录仍在开发仓库，未上传。不要用旧实现的命令和 CI 验新版。

## 代码原则

每个可变状态有明确 owner，边界校验一次，内部不变量由 owner 建立。新增 guard、fallback、抽象、兼容分支或重试，须能指出真实合同或可触达风险。

权限、密钥、取消清理、数据一致性及外部动作门禁必须保留。不要用异常吞没、默认空结果或“成功”兜底掩盖内部违约，也不要为测试数量机械铺满分支。

新能力先找对应旧立项、修订与验收目标，再在当前三层实现。旧文档是行为参考，不能自动授予部署或外部操作权限。保留已有 WIP，不为方便测试而 reset、clean 或 stash 用户工作。

## 公开快照的检查

```sh
uv sync --locked --python 3.12
uv run ruff check src scripts/dev
uv run pyright
uv run python scripts/dev/check-boundaries.py
uv run python scripts/dev/demo-loop.py
uv build --out-dir .cache/dist
uv run python scripts/dev/check-package.py .cache/dist
```

前端源码修改后先运行 `npm --prefix web ci` 和 `npm --prefix web run build`；生产静态资源必须齐全。已提供 Web 合同导出与状态检查脚本。

完整开发仓库另有 Python pytest 回归、前端检查、继承与技能镜像门禁；本次没有上传完整 `tests/` 和内部资料，因此不能在公开快照复现那组全量数量。Status 中的 2445 是本地已验证记录，不能替换为公开 CI 通过。

按变更运行受影响的有意义检查，阶段交付运行适用完整门禁。没有新变更或失败，不重复全量测试。

## 发行与验收

管理端必须先构建，Python 包携带生产静态产物；缺产物明确失败。隔离环境安装发行包并核实际运行文件，源码导出按白名单核验，不包含实例、密钥、登录态、缓存和开发历史。

配置保存、实际运行、代码候选与权限 revision 分别核对。真实测试预先固定输入范围、期限、成本和保护；程序通过之后仍阅读实际收件内容，保留失败与缺证，不重放制造成功。

## 文档维护

`docs/wiki/` 是新版 Wiki 的维护源，`Home` 为首页，`_Sidebar` 为导航。正文使用同目录 `.md` 链接；发布到 GitHub Wiki 时转为页名链接。README 使用公开 Wiki 地址。

文档只陈述对应范围的事实，状态集中更新 Status。Wiki 发布使用独立文档提交，核对上传路径白名单，不携带开发 WIP。旧页通过 Git 历史保留，维护日志记录实际发布与回滚定位。
