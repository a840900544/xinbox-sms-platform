# 信匣 · Xinbox Message Hub

一个可以独立部署的消息聚合网站，使用 **FastAPI + Vue 3 + SQLite**，采用 MIT 许可证。

将获授权的业务通知、短信来源消息和来电事件，通过标准 HTTP API 提交到统一收件箱，再按规则转发到通知渠道。网站本身不连接运营商，不会通过手机号登录无忧行，也不依赖私有协议、设备标识或第三方 SDK。

## 能做什么

- 用户端 `/` 与管理端 `/admin`；用户数据按归属隔离，管理员可管理全部用户及来源。
- 可开关自行注册；用户停用、来源分配、删除和恢复；站内公告。
- 多来源消息收件箱、搜索、日期筛选、已读和归档；敏感正文默认隐藏，主动查看留下操作记录。
- 来源接入密钥只显示一次、仅存哈希，可撤销；事件 ID 去重、限流和加密入库。
- 来电事件列表：展示提交方提供的呼入、接通、结束、未接状态，不接听、不录音。
- 12 类转发适配器：Telegram、企业微信机器人、企业微信应用、钉钉、飞书、Bark、Server酱、PushPlus、ntfy、Gotify、SMTP、自定义 HTTPS Webhook。
- 渠道弹窗配置、模板、来源分流、关键词筛选、敏感信息权限、重试队列和投递日志。
- 企业微信应用加密回调与只读查询；按渠道限制成员和数据范围。
- 可选合成演示数据，无需任何外部账号；创建演示记录不会触发转发。

## 快速部署

需要 Docker Engine 与 Docker Compose 插件、可拉取基础镜像及安装依赖的网络。

```sh
git clone https://github.com/a840900544/xinbox-sms-platform.git
cd xinbox-sms-platform
cp .env.example .env
docker compose up -d --build
docker compose exec web cat /data/initial-admin-password.txt
```

访问 **http://127.0.0.1:8787/**，用户名 `admin`，密码为最后一条命令显示的随机初始密码。管理端是 **http://127.0.0.1:8787/admin**。登录后建议修改密码。

默认只绑定宿主机回环地址。远程服务器可用：

```sh
ssh -N -L 18747:127.0.0.1:8787 USER@YOUR_SERVER_IP
```

然后在电脑访问 `http://127.0.0.1:18747/`。公网部署请参考 [部署说明](docs/DEPLOYMENT.md)，配置 HTTPS 后启用安全 Cookie。

### 不接入协议也能体验

1. 登录用户端，点击收件箱顶部的「创建演示数据」。
2. 查看 3 条合成消息、1 条合成未接事件；验证码示例为 `000000`。
3. 到「来源管理」添加自己的来源、生成接入密钥。
4. 按 [HTTP 接入说明](docs/API.md) 提交真实的已授权消息。
5. 配置并启用自己的转发渠道；新增渠道默认停用，只处理之后新增的事件。

**演示数据不是实际运营商收信，不代表本项目具有无忧行登录能力。** 外部渠道需要使用者自己的凭据；本仓库不提供共享凭据或外部服务。

## 项目结构

```text
backend/
  app.py                 账号、权限、收件箱、来源、管理端 API
  ingress.py             来源密钥、事件接入、幂等记录、合成演示
  forwarding.py          渠道适配、持久队列、规则和重试
  wecom_commands.py      企业微信签名与加密回调、只读指令
  requirements.lock      固定版本的运行依赖
frontend/
  src/App.vue            用户端入口和来源管理
  src/AdminPanel.vue     用户、归属和回收站
  src/IngressPanel.vue   接入密钥管理
  src/ForwardingPanel.vue 转发配置弹窗与日志
  src/PlatformSettings.vue 注册开关与公告
docs/                    API、部署、安全、架构说明
Dockerfile               前端构建与 Python 运行时
compose.yaml             单实例部署、持久卷、健康检查
```

## 开发与限制

详见 [开发及架构](docs/ARCHITECTURE.md)、[安全说明](SECURITY.md)、[贡献说明](CONTRIBUTING.md)。这是早期自托管项目，尚无独立安全审计或公开的规模压测结论。当前支持 SQLite 单实例部署，不提供 PostgreSQL/多副本模式；正文搜索最多扫描最近 5,000 条匹配记录。

外部转发属于至少一次尝试：断网或崩溃后可能重发，不能保证所有第三方渠道严格不重复。事件接入去重范围是同一接入密钥及其 `event_id`。渠道适配代码已包含在仓库中，实际可用性取决于服务方接口、账户权限、网络和额度；不声称所有渠道均已逐一实发验证。

当前版本不自动清除历史消息。来源删除是软删除，保留历史；在制定数据保留政策时必须考虑此限制。站点管理员能够访问全部用户数据；加密保护不覆盖已控制服务器的攻击者。

## 版本与许可

`main` 为独立网站版。原协议相关源码快照保存在 [`legacy-protocol-source`](https://github.com/a840900544/xinbox-sms-platform/tree/legacy-protocol-source) 分支，不属于本版本的运行依赖，不建议把旧数据库直接挂载到本版本。

本分支自有代码采用 [MIT](LICENSE)；Vue、Vite、FastAPI 等依赖按其各自许可证分发。本项目与运营商、通知平台及 OpenAI 不存在官方隶属关系。是否满足任何开源支持计划的条件，应以项目真实情况及计划方审核为准。
