# 信匣 · 私有短信管理后台

Vue 3 + FastAPI + SQLite。提供真实短信管理、号码管理和独立常驻采集；来电采集仍需进一步验证托管电话的兼容性。

## 已实现

- 管理员密码登录、8 小时会话、退出、修改密码和登录失败限速。
- 短信列表、分页、未读、归档、按接收号码和日期筛选。
- 搜索短信正文、发送方、接收号码。为避免通过搜索推断验证码，敏感正文按脱敏后的内容搜索。
- 详情、复制、验证码默认脱敏、显式查看原文，以及查看审计。
- 主动添加授权号码、备注、归档、已有会话接入、托管查询、开启托管、接收启停和操作记录。
- 手机号验证码登录流程及人工滑块窗口；新号码的完整登录仍须实际验证，不承诺任意设备自动通过授信。
- 来电事件列表与 SDK 来电/未接回调存储。当前真实来电验证尚未成功，不代表完整通话历史查询。
- 数据源每 5 秒导入，页面每 10 秒刷新。同步异常会显示；不会假称采集器在线。
- 短信记录在平台数据库中重新加密，数据库与密钥分离。账号密码只保存 PBKDF2 哈希。
- 桌面、手机布局；主后台字体、脚本和图标由本机提供。仅主动打开人机验证窗口时加载验证服务脚本，隔离窗口不能读取后台会话或表单。

## 2026-10-20 号码管理升级

接收器已启用 SDK alive、定期 checkRequests、20 分钟 SIP 续注册、连接异常退出重试和开机启动。服务在线只表示 SDK 连接状态，不能证明每一条运营商短信或来电必达。凭据过期时需要重新完成正常登录，不自动索取验证码或绕过设备授信。

### 使用

1. 进入“号码管理”，点击“添加号码”，填写区号、号码和备注并确认授权。
2. “管理号码”提供两种接入：验证已有登录 token 与设备标识，或本人完成滑块、接收验证码并提交登录。
3. 登录会话先通过 getUser 核对号码，再申请本人电话 token 和 SIP 凭据。显示“已登录”后查询托管并启动接收。
4. 新设备可能返回设备验证或版本业务错误；后台显示失败业务码，不伪造验证结果。请在官方 App 按正常流程处理。
5. “停止接收”只停止服务器进程，不关闭运营商托管。归档只是资料分类，短信不删除。

当前已通过实际页面核对既有号码的托管查询，原始短信库及页面持续同步。第二个号码的独立登录、沙箱滑块的完整验签和多号码并发仍待使用者提供授权号码后验证。

### 主机组件

- `tools/control_worker.py`：受保护的主机任务执行器。任务载荷加密，完成或失败后清除 token/验证码载荷；中断任务不自动重放。
- `deploy/jego-control.service`：主机任务执行器服务。
- `deploy/jego-number@.service`：每个新增号码独立的 SDK 进程、配置和日志目录。
- `collector/`：接收器及加密入库源码。需要现有合法可用的 Linux SDK，不在本源码包内分发供应商二进制。
- `/opt/jego-sms-control/protocol.json` 与 `trusted.pem`：从已授权安装包及既有正常配置提取的协议参数和信任证书，未包含在源码包中。
- `/opt/jego-sms-control/accounts/<id>/session.enc`：加密保存 App 会话。
- 同目录 `sdk-login-input.txt`：SDK 启动需要的凭据文件，目录 0700、文件 0600；不会挂载给 Web 容器。
- `/opt/jego-sms-source/receiver-status.json`：接收状态快照，不包含会话凭据。

号码控制执行器读取平台数据库与密钥，必须与后台部署在同一可信主机，由专用受保护的服务运行。不要将 `/opt/jego-sms-control` 或后台数据目录设为公共下载目录。

### 新增 API

```text
GET/POST  /api/numbers
PATCH     /api/numbers/{id}
POST      /api/numbers/{id}/actions
GET       /api/jobs/{id}
GET       /api/calls?account=&page=1
GET       /login-captcha?nonce=<32位随机值>
```

动作包括 activate、send_code、login、status、enable_hosting、start、stop、restart；统一要求管理员会话，写操作要求 CSRF。任务结果不返回 token 或验证码。

新增表：managed_numbers（加密号码资料）、control_jobs（短期加密动作载荷）、call_events（加密来电事件）。来电从原始库 received_calls 增量同步，独立持久化游标，唯一指纹去重。

### 来电排查的证据边界

`getCallStatus` 是托管开关查询，不是通话清单 API。通用 SDK 来电模块初始化成功后，第一次实际拨打未产生回调；目前不能宣称来电查询已打通。已发现并修正与原 App 不同的初始化顺序：在登录电话 SDK 之前创建媒体和来电模块，并在登录前开启事件存储，以免漏掉登录期间的未接通知。是否解决托管电话的来电兼容性还需实际回调确认。

还修复了 SDK 构造函数未初始化电话接入模式的问题，并通过原生接口把 directConnectEnable 设置为 false，实际回读为 0。仅赋值登录结构体字段并未改变原生有效配置，因此保留了原生配置回读记录；这仍不等于真实来电已验证。

## 目录

```text
backend/app.py               FastAPI、认证、只读同步、查询与审计
backend/requirements.txt     后端锁定依赖
frontend/src/                Vue 页面和样式
frontend/dist/               构建产物
tools/prepare_server.py      创建 SMS 专用目录、管理员初始密码
tools/source_bridge.py       把研究接收库的密文增量复制到 SMS 专用目录
deploy/sms-console-source.service
compose.yaml
Dockerfile
```

## 数据流

```text
现有接收器 → /opt/jego-research/research-sms.sqlite3
           → source_bridge（仅复制密文）
           → /opt/jego-sms-source（只有短信库、其密钥与同步状态）
           → Web 容器只读挂载 /source
           → /opt/jego-sms-web/data/platform.sqlite3（独立加密保存）
           → 认证后的网页
```

原始接收器的 token、登录输入文件和 SDK 日志不会挂载进 Web 容器。归档和已读状态只改变平台数据库。

## 部署

服务器需 Docker / Docker Compose 和 Python 3。上传本目录（包含 `frontend/dist`）至 `/opt/jego-sms-web`，然后：

```sh
cd /opt/jego-sms-web
python3 tools/prepare_server.py
install -m 0644 deploy/sms-console-source.service /etc/systemd/system/sms-console-source.service
systemctl daemon-reload
systemctl enable --now sms-console-source.service
docker compose up -d --build
```

本版本只绑定服务器 `127.0.0.1:8787`。不开放公网 HTTP 访问。Windows 用 SSH 隧道：

```powershell
ssh -N -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 -L 18747:127.0.0.1:8787 root@YOUR_SERVER_IP
```

保持该连接，然后访问 `http://127.0.0.1:18747`。账号 `admin`；初始密码只保存在服务器 `admin-initial-password.txt`，不要提交到版本库。初始密码只在首次创建用户时生效，修改该文件不会重置已有密码。

后续配置域名和 HTTPS 时，使用反向代理连接回环地址，并设置 `SMS_SECURE_COOKIE=true`。不要直接把 8787 端口暴露到公网。

## 本地构建

```sh
cd frontend
pnpm install --frozen-lockfile
pnpm run build
```

服务端不需要 Node，避免在小内存 VPS 上构建前端。没有添加自动化测试。

### 本次服务器的离线安装方式

当前 VPS 无法连接 Docker Hub，访问 Python 软件源也较慢，因此实际部署使用了服务器已有的 `docker.m.daocloud.io/library/python:3.12-slim-bookworm` 镜像和离线 wheelhouse。

`backend/requirements.lock.txt` 固定了 17 个 Linux amd64 / CPython 3.12 依赖包及其 SHA-256。离线包包含 `wheelhouse/`；普通源码包不包含这些二进制包。在包含 wheelhouse 的部署目录中配置：

```dotenv
PYTHON_IMAGE=docker.m.daocloud.io/library/python:3.12-slim-bookworm
SMS_DOCKERFILE=deploy/Dockerfile.offline
```

再运行 `docker compose up -d --build`。离线镜像安装使用 `--no-index --require-hashes`。正常网络环境可以使用默认 Dockerfile；修改部署模式不会自动修改已有管理员密码或删除短信。

## 数据结构

- `messages`：源记录 ID、源名称、接收账号 HMAC、加密完整记录、时间、已读与归档状态。`(source,source_id)` 唯一，重复同步不会增加记录。
- `users`：用户名、PBKDF2 密码哈希。
- `sessions`：会话 token 哈希、用户、CSRF token、过期时间；不在数据库保存明文会话 cookie。
- `audit`：登录、查看原文、已读、归档、密码修改；不保存短信正文或验证码。
- `login_failures`：短期登录失败计数，IP 使用带密钥哈希。

`data/messages.key` 和 `data/platform.sqlite3` 必须一起备份并分别保护。源库对应 `/opt/jego-sms-source/sms-storage.key`。丢失密钥无法恢复正文。

## 主要 API

| 路径 | 方法 | 用途 |
|---|---|---|
| `/api/auth/login` | POST | 用户名、密码登录 |
| `/api/auth/me` | GET | 用户和当前 CSRF token |
| `/api/auth/logout` | POST | 撤销当前会话 |
| `/api/auth/password` | POST | 修改密码、撤销全部会话 |
| `/api/overview` | GET | 统计、号码与来源状态 |
| `/api/messages` | GET | q、account、status、start、end、page、page_size |
| `/api/messages/{id}` | GET | 脱敏详情 |
| `/api/messages/{id}/reveal` | POST | 审计后返回原文 |
| `/api/messages/batch` | POST | ids + action(read/unread/archive/restore) |
| `/api/sync` | POST | 手动同步 |
| `/api/audit` | GET | 最近 100 条操作 |
| `/api/health` | GET | 不含敏感信息的进程健康状态 |

除登录和健康状态外，API 需要会话 cookie；修改请求还需要 `X-CSRF-Token`。同源浏览器访问，不启用跨域放行。一次最多批量修改 100 条。

## 来电记录

- 已在当前号码实测来电到达及挂断事件入库。
- `/api/calls` 按账号和网关通话标识合并分页，每通包含事件时间线及事件数；没有标识的事件不擅自合并。
- 仅在观察到接通状态时显示已接通。只有结束、没有接通证据的记录单独标注，不推断为已接听。
- 首次升级会迁移加密历史事件的通话分组键，保留原始事件。
- 不自动接听、拨号或录音，不能代表完整运营商历史记录。

## 当前边界

- 号码管理支持授权号码建档、验证码登录及已有会话接入。敏感会话由服务器加密保存，浏览器不持久化 token；安全验证由用户亲自完成。
- 当前为单管理员。支持新增和管理多个号码；第二个号码的完整登录和接收链路尚未实测，多用户授权尚未实现。
- 正文加密时暂不建立明文全文索引。带关键词搜索最多检查符合其他筛选条件的最近 5,000 条，并在界面提示范围；无关键词列表正常分页。
- 已实现配置后启用的短信转发；暂无自动删除或保留期清理。
- `source_bridge` 只是数据库同步服务，不是短信采集器；采集器离线时，页面仍可查看历史短信，但不会因此声称正在实时收信。
- 源库需要保持 ID 连续递增，重置原始数据库前需规划数据源迁移，不能复用已有源 ID。

## 排障与维护

```sh
cd /opt/jego-sms-web
docker compose ps
docker compose logs --tail 50 web
systemctl status sms-console-source --no-pager
journalctl -u sms-console-source -n 30 --no-pager
```

- 网页打不开：确认 SSH 隧道保持连接，8787 只在服务器回环监听。
- 账号密码不对：使用 Web 管理员密码，不是 Ubuntu root 密码或无忧行验证码；连续错误 8 次后需等待 15 分钟。
- 无新增短信：先确认采集器确实收到并写入原始数据库，然后查看来源同步状态；“数据源可读”不等于“采集器在线”。
- 数据源出错：检查原库、专用源库对应的密钥一致，查看 `/opt/jego-sms-source/bridge-status.json`；不要把原始文件内容或密钥贴入日志。
- 升级前：备份 `data/`、专用源库及各自密钥。保留 `data/` 再重新构建镜像；不要运行删除数据卷的命令。

## 消息转发

后台“消息转发”提供 Telegram、企业微信机器人、钉钉机器人、飞书机器人、Bark、Server酱 Turbo/3、PushPlus、ntfy、Gotify、SMTP、自定义 HTTPS Webhook 共11类适配器。

新增渠道默认停用。填写配置、规则和模板后勾选启用并保存，只处理启用期间新同步入库的短信，不补发历史数据。凭据整体加密存储，不回显。修改时空字段保留原凭据；如需清空某项可选凭据，停用旧渠道并新增。

- 按号码分流，包含关键词任意匹配、排除关键词优先，多个启用渠道各自独立发送。
- 验证码等敏感短信默认隐藏整个正文，可对单渠道明确授权发送原文；另可遮蔽号码中间四位。敏感识别为规则匹配，不能保证识别所有类型的秘密，请谨慎授权接收端。
- 新短信与 outbox 任务在同一数据库事务提交，渠道与短信组合唯一。串行异步工作器，首次失败30秒后重试，指数退避，上限1小时，最多8次；失败可主动重新发送。
- 修改或停用渠道取消尚未发送的旧任务，正在发送的请求不可撤回。重启时恢复未完成发送；网络结果未知时可能重复，不宣称恰好一次。Webhook 接收端可按 payload.id 或 Idempotency-Key 去重。
- 日志只存任务状态/尝试次数/固定错误码，不存正文、URL或token，最近100条在网页展示。
- 为避免渠道成为内网访问入口，HTTP类仅允许公网HTTPS，禁重定向，DNS结果校验并固定连接地址，TLS证书正常验证。内网自建推送地址暂不支持。
- SMTP只支持465隐式TLS或587 STARTTLS；拒绝明文回退。SMTP也须公网主机。
- 长文本超过渠道限制时明确标注截断；完整正文保留在后台。普通自定义Webhook收到JSON：{id,title,text}，可选Bearer Token。
- 当前适配为代码实现与服务部署完成；未配置真实渠道凭据前，不能宣称各平台已实际投递成功。没有自动发送测试短信。

API：GET/POST /api/forwarding，PUT /api/forwarding/{id}，POST /api/forwarding/jobs/{id}/retry。沿用管理员登录、CSRF和访问审计。

官方接口参考：
- Telegram https://core.telegram.org/bots/api#sendmessage
- 企业微信 https://developer.work.weixin.qq.com/document/path/91770
- 钉钉 https://open.dingtalk.com/document/robots/custom-robot-access
- 飞书 https://open.feishu.cn/document/client-docs/bot-v3/add-custom-bot
- Bark https://github.com/Finb/bark-server/blob/master/docs/API_V2.md
- Server酱 https://sct.ftqq.com/docs/integrations/coze/
- PushPlus https://www.pushplus.plus/doc/guide/api.html
- ntfy https://docs.ntfy.sh/publish/
- Gotify https://gotify.net/docs/pushmsg

### 企业微信两种渠道

- 企业微信 Webhook（群机器人）：填写群机器人的完整 Webhook URL，适配器 ID 保持 wecom，既有配置兼容。
- 企业微信自建应用：适配器 ID wecom_app，填写 CorpID、AgentId、应用 Secret，以及成员 UserID / 部门 ID / 标签 ID（至少一种，多个用 | 分隔）。不默认使用 @all。
- 按 gettoken 返回的有效期缓存 access_token，提前120秒更新；令牌无效/过期时刷新后重试一次。缓存只在内存中，Secret仍在加密渠道配置中。
- 部分接收人无效时停止自动重试，日志提示其他人可能已收到，避免无谓重复投递。接收人应在应用可见范围内，并按企业微信后台要求配置可信IP。
- 官方文档：https://developer.work.weixin.qq.com/document/path/91039 、https://developer.work.weixin.qq.com/document/path/90236 。本环境未提供真实企业应用凭据，实际投递尚待验证。

### 来电转发

所有渠道复用短信发送适配器并支持来电通知。渠道可单独选择 sms/call，分别使用短信模板与来电模板。既有渠道保持仅短信，需编辑后勾选来电通知并保存；不补发历史记录。

来电导入和通知任务同事务提交，使用当前账号+通话标识按渠道去重。优先通知来电到达，未观察到到达时可通知首次观察到的未接/结束事件；正常已通知的通话挂断不再重复通知。无网关通话标识时无法可靠跨事件关联。

来电沿用号码分流、号码脱敏、重试和发送日志。关键词匹配事件名称、主叫号码、接收号码。模板额外支持 {kind}、{event_label}。日志明确区分短信与来电事件。升级需先暂停 control worker，Web 完成数据库迁移后再恢复 worker；原生短信/来电接收器不需要重启。

### 独立托管 deviceId 配置

号码管理提供独立“托管设备标识”配置，使用 save_device_id 动作，仅保存设备标识文本到加密会话 hosting_device_id，不调用无忧行接口、不重新登录、不重启接收器，也不切换托管。无需先有登录token即可保存。列表只显示部分标识与修改时间；原始值不写入审计日志。

查询和开启托管优先使用 hosting_device_id，旧会话仍可回退原 device_id。验证码登录的设备登记标识由系统生成，与托管配置分离；已有托管标识在重新登录时保留。token接入不再要求同时填写deviceId，仍先核对号码归属。独立设置不是获取授信的手段，未经官方授权的设备可能被正常拒绝。

操作顺序：号码管理 → 管理号码 → 仅保存设备标识 → 等待保存完成 → 查询托管 / 开启托管。更换标识后清空之前的托管查询缓存，避免显示旧身份查询结果。

托管设备标识不校验UUID结构，允许非UUID文本，最多4096字符，拒绝空值和控制字符；首尾空白去除。此为本地输入兼容性，不能保证上游接受。
## 用户端与管理端

- `/app`（以及 `/`）：用户登录、自行注册、本人号码和短信/来电、转发配置。
- `/admin`：管理员入口，用户列表、创建/启停账号、号码归属分配、回收站、操作记录。
- 旧账号升级为管理员；既有号码、历史记录和转发渠道保留并归原管理员。
- 新注册和后台创建的账号固定为普通用户。角色由服务端决定；用户 API 按号码归属过滤，管理 API 要求管理员角色。
- 最短密码 6 位，允许纯数字，无强制复杂度；修改密码后撤销该账号的全部会话。
- 删除号码是可恢复的本地删除：移入回收站、排队停止并禁用接收服务、保留加密会话和历史记录，不调用无忧行托管关闭接口。
- 回收站显示停止任务状态，失败可重试。恢复后需主动启动接收。
- 号码分配同时转移历史短信和来电的访问权。转发渠道仍属于原用户；投递前复核号码和渠道归属，删除或转移后的待处理消息不会继续发给旧归属渠道。已经发出的第三方请求无法撤回。
- 注册限流：每个来源 IP 每小时最多 5 次尝试，平台每小时最多 100 次尝试。
- 当前采用同一服务内的角色和数据权限隔离，两端共用登录会话，并非两个独立服务器。

## iOS 8.9.8 适配

新验证码登录采用已分析的 8.9.8 流程，复用本号码已保存设备标识进行 enroll，并将 enrollId 用于本次登录；托管开启使用 iOS 对应的非 sec/body 外壳请求。已有会话保留 legacy 配置，避免无故改变正常接收链路。版本依据与静态分析范围见 outputs/ipa898/适配说明.md。实际服务端授信结果仍需授权账号完成正常验证码登录后确认。

## 企业微信应用指令（渠道内配置）

在「消息转发 → 企业微信自建应用 → 配置」启用「应用指令查询」，填写明确的允许查询成员UserID；成员列表不支持@all、部门或标签，不能默认为推送接收人拥有查询权限。

- 查询与渠道相同的所属网站用户、接收号码范围、短信/来电类型、敏感正文权限和号码脱敏规则。不选号码表示该渠道所属用户的全部未删除号码。关键词只用于自动转发。
- 停用渠道、停用所属网站用户、移除允许成员都会阻止后续查询；转移或删除号码后不再可查。同一应用的同一查询成员不能同时用于多个启用渠道，以避免归属歧义。
- 已接入平台现有回调的应用可直接使用；其他企业或应用在渠道配置回调Token/EncodingAESKey，保存后将显示的 `/wecom/channels/<channel_id>` URL配置到其企业微信后台「接收消息」。仅填写推送Secret不足以接收指令。
- 专用回调验证Token签名、AES解密、CorpID、AgentID与时间窗口，返回加密被动文本回复；每个渠道使用自己的凭据。现有平台回调通过受HMAC保护的本机桥接查询，不改动原有青龙指令。
- 指令：帮助、号码列表、状态 <号码或尾号>、短信 <号码或尾号>、来电 <号码或尾号>。最多5条短信或5通来电，时间北京时间；尾号冲突使用更长尾号。
- 查询仅显示已有状态和数据，不调用托管开关或重连接收。敏感原文是否显示由渠道的「允许敏感原文」决定，允许成员将能查询范围内历史记录。
- 不再使用独立绑定卡片或绑定码，旧绑定API移除，旧绑定数据不参与授权。新增查询权限默认关闭，需渠道管理员明确开启。
- 共享桥接配置 `/etc/wecom-sms-command-bridge.json` 仅在服务器保存，不要打包；`/internal/` 被Nginx禁止公网访问。回复缓存加密保存24小时，按配置和号码归属变化失效；每成员每分钟最多20个新指令。

共享桥接配置文件权限：属主为回调运行用户wecomcallback，属组root，模式0440。网站容器移除了CAP_DAC_OVERRIDE，因此即使容器uid为0也需要root组读权限；不要使用0400仅允许回调用户读取。密钥不对其他用户开放。
