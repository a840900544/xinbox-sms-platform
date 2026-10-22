# HTTP 接入及管理 API

## 1. 创建来源与接入密钥

推荐在网页「来源管理」添加来源，填写全站唯一的来源标识、备注，并确认管理授权。标识可以是业务系统名、邮箱或已授权号码，最多 128 字符。来源只用于归属及展示，不自动验证外部号码所有权，也不会登录外部平台。

打开「管理来源 → HTTP 接入密钥」生成密钥，明文只在此次响应中出现。每来源最多 10 个有效密钥；不提供找回明文接口。密钥只允许提交事件，不能读取收件箱、创建账号或管理来源。

## 2. 提交消息

```sh
# 在本地环境输入，不把密钥提交到 Git。
read -r -s XINBOX_TOKEN
export XINBOX_TOKEN
curl --fail-with-body http://127.0.0.1:8787/api/ingest/events \
  -H "Authorization: Bearer $XINBOX_TOKEN" \
  -H 'Content-Type: application/json' \
  --data '{"event_id":"order-001","kind":"message","sender":"Order service","body":"订单状态已更新","received_at":"2026-10-22T12:00:00+08:00"}'
unset XINBOX_TOKEN
```

生产使用 HTTPS。成功响应为 `{"id":1,"kind":"message","duplicate":false}`。

| 字段 | 约束 |
| --- | --- |
| event_id | 必填，1–128 字符；来源程序持久保存的唯一事件 ID |
| kind | `message` 或 `call`，默认 `message` |
| sender | 必填，1–128 字符；发送方或主叫标识 |
| body | 消息正文，非空，最多 16,000 字符 |
| received_at | 可选，ISO 8601 字符串或秒/毫秒时间戳字符串，最长 64 字符；建议带时区 |
| call_id | 来电可选，最多 128 字符；同一通电话的多个事件使用相同值 |
| event | 来电状态：`incoming`、`answered`、`ended`、`missed`；默认 `incoming` |

未知字段会被拒绝。省略时间使用服务器 UTC 时间；不带时区按 UTC 处理。数据库统一存 UTC，网页及转发按北京时间显示。

### 幂等与失败处理

- 同一密钥、同一 `event_id`、相同规范化输入重复提交：200，`duplicate:true`，不会重复入库或新建转发任务。
- 同一密钥、同一 ID、不同输入：409。重试时请保留同样的时间及内容。
- 更换密钥后是新的去重空间；轮换时应由发送方维护投递进度，避免跨密钥重复提交。
- 401：密钥无效、已撤销、来源已删除、用户被停用，或来源已转移归属。
- 413：请求体大于 128 KiB；422：字段或时间不合法。
- 429：每密钥每分钟最多 120 条新事件；请退避后重试原始事件。幂等重试不占新的事件配额。
- 请求超时不能证明未入库；重试相同 ID 即可。入库、幂等记录和转发队列在同一 SQLite 事务内提交。

## 3. 提交来电事件

```json
{
  "event_id": "call-001-incoming",
  "kind": "call",
  "sender": "+8613800000000",
  "call_id": "call-001",
  "event": "incoming",
  "received_at": "2026-10-22T12:01:00+08:00"
}
```

后续接通、结束使用不同 `event_id` 和相同 `call_id`。分组范围包含来源和接入密钥；多个采集程序应统一使用同一密钥及 call_id 规则。列表按事件入库顺序归并；乱序到达可能影响展示，应由上游按顺序提交。

启用来电转发后，每渠道每通电话只创建一次通知，取首次可通知的呼入/未接/结束事件；后续状态仍保留在列表，不保证每次变化均另发通知。网站不负责电话路由、接听或录音。

## 4. 网页会话 API

登录：`POST /api/auth/login`，JSON `{username,password}`。返回 `csrf` 并设置 HttpOnly 会话 Cookie。之后的写请求必须带 `X-CSRF-Token` 和 Cookie；读取亦必须登录。浏览器来源请求检查 Origin。

| 方法与路径 | 功能 |
| --- | --- |
| GET /api/health | 无需登录，仅服务状态与版本类型 |
| GET /api/site | 是否允许注册 |
| POST /api/auth/register | 注册，须管理员已开启注册 |
| GET /api/auth/me | 当前账号、角色、CSRF |
| POST /api/auth/logout | 登出 |
| POST /api/auth/password | current_password、new_password；修改后撤销全部会话 |
| GET /api/overview | 当前范围汇总 |
| GET /api/messages | q/account/status/start/end/page/page_size 筛选 |
| GET /api/messages/{id} | 默认脱敏详情 |
| POST /api/messages/{id}/reveal | 审计后查看原文 |
| POST /api/messages/batch | ids 数组及 action=read/unread/archive/restore |
| GET, POST /api/numbers | 列出来源；创建传 address、label、authorized=true |
| PATCH /api/numbers/{id} | label、archived |
| DELETE /api/numbers/{id} | 软删除并撤销接入密钥 |
| GET, POST /api/numbers/{id}/ingress-tokens | 列出密钥元数据；创建传 name |
| DELETE /api/numbers/{id}/ingress-tokens/{token_id} | 撤销密钥 |
| GET /api/calls | account、page 筛选来电 |
| POST /api/demo | 为当前用户创建一次合成演示，不转发 |
| GET /api/forwarding | 渠道、提供商字段、最近 100 条日志 |
| POST /api/forwarding | 新建渠道 |
| PUT /api/forwarding/{id} | 更新渠道；空白凭据保留原值 |
| POST /api/forwarding/jobs/{id}/retry | 人工重试启用渠道的失败任务 |
| GET /api/audit | 当前范围最近操作 |
| GET /api/announcements | 用户公告 |
| POST /api/announcements/{id}/read | 公告已读 |

管理员接口需管理员角色及 `X-Console-Scope: admin`；此头本身不授予管理员权限。包括 `/api/admin/overview`、`/api/admin/users`、`/api/admin/users/{id}`、`/api/admin/numbers/{id}/owner`、`/api/admin/numbers/{id}/restore`、`/api/admin/site` 及公告维护接口。管理端 UI 提供对应操作。

保留 `/numbers` 和事件类型 `sms` 等历史内部命名，网页统称「来源」及「消息」。接入事件 `kind=message` 在转发规则中对应 `events=["sms"]`，来电对应 `events=["call"]`。

## 5. 自定义出站 Webhook

配置 HTTPS URL，可选 Bearer Token。接收 JSON：`{"id":"持久任务ID","title":"通知标题","text":"模板渲染后的文本"}`，并带 `Idempotency-Key`。接收方可用 ID 去重；不要假设其他通知平台识别此头。

## 6. 企业微信应用指令

在转发配置中选择企业微信自建应用，设置 CorpID、AgentId、Secret 和接收范围。开启指令查询，填写明确的成员 UserID，并设置回调 Token、EncodingAESKey。将网页显示的 `https://你的域名/wecom/channels/渠道ID` 填到企业微信后台，Token 与 AESKey 必须两边一致。

支持「帮助」「来源列表」（兼容「号码列表」）、「状态 来源标识」「消息 来源标识」「来电 来源标识」。历史消息/来电最多 5 条。成员权限、来源范围、敏感正文权限继承渠道；查询不修改外部平台状态。回调需可公开访问的 HTTPS，服务器需满足企业微信应用的网络及可信 IP 要求。
