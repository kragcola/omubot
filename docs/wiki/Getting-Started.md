# 开始使用

**新版源码已公开。** `kragcola/omubot` 的 `main` 对应当前受控运行候选，不包含个人实例与登录资料。可先离线验证；源码公开不表示生产验收通过。

## 离线准备

新版锁定 Python 3.12（当前包要求 `>=3.12,<3.13`），使用 uv 管理依赖。Node / npm 用于构建管理端；构建完成后的 Python 发行包运行时不需要常驻 Node 服务。

获取 `main` 并进入独立目录：

```sh
git clone https://github.com/kragcola/omubot.git
cd omubot
```

公开源码已包含预构建静态资源。直接运行下面的 uv / demo 命令即可离线验证；只有修改前端或核重新构建时需要 npm 两步：

```sh
uv sync --locked --python 3.12
npm --prefix web ci
npm --prefix web run build
uv run python scripts/dev/demo-loop.py
```

离线 demo 使用临时合成数据与 fake 端口，检查普通回复、时间工具及授权拒绝，结束清理；不会因此获得 QQ 或真实模型授权。

## 初始化独立实例

```sh
uv run omubot-new --init-instance instances/demo --name demo --bot-id 10001 --port 18081
uv run omubot-new --instance instances/demo
```

这是 offline 演示实例，`10001` 是示例身份。浏览器访问 `http://127.0.0.1:18081`，使用新生成的实例凭据。admin 用于管理，status 仅用于只读状态。不要把凭据、实例目录、数据库或 QQ 登录资料提交到 Git。

依次完成命名模型配置、保存/运行版本核对、离线试聊与权限检查。保存配置不会自动切换运行版本，也不会自动授予模型或 QQ 动作权限，详见 [Configuration](Configuration.md)。

## 进入真实接入前

真实账号应初始化匹配其身份的新实例，而不是把演示数据库改成真实账号。明确模型目的地、密钥环境变量、OneBot 接线、入站认证、账号与目标范围后，按受控部署方案验证。

账号在线、OneBot 可达、模型可用、功能启用、动作获准和收件人实际收到，需要分别检查。不要在现有已登录 NapCat 上套用旧项目的 Compose 启停命令。当前受控测试要求所有已有 NapCat 保持运行。

平台安装与第三阶段验收尚未完成，不提供一键生产部署承诺。继续阅读 [Deployment](Deployment.md) 和 [Status](Status.md)。
