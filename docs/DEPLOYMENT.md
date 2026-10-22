# 部署、升级与维护

## Docker

按 README 的 `docker compose up -d --build` 启动。首次使用全新数据卷会生成独立加密密钥、数据库和随机管理员密码。构建阶段需要 Node/pnpm/Python 包仓库网络，运行阶段不需要 Node。

```sh
docker compose ps
docker compose logs --tail=100 web
curl http://127.0.0.1:8787/api/health
```

容器使用 UID/GID 10001，SQLite 数据放在命名卷。若改成宿主机 bind mount，预先创建目录并赋予 10001 写权限；不要用放宽到 777 的方式解决权限问题。单实例、单 Uvicorn worker，禁止多个容器共享同一 SQLite 卷。

## 公网 HTTPS

默认端口只监听本机，不直接暴露互联网。用 Nginx/Caddy 等反向代理对外提供 HTTPS，代理到 `127.0.0.1:8787`。保留原 Host 请求头，设置请求体限制 128 KiB，并配置连接/请求速率限制。

`.env` 设置 `XINBOX_SECURE_COOKIE=true`，然后 `docker compose up -d`。此后通过 HTTP 浏览器访问不能正常携带安全 Cookie，应统一使用 HTTPS。

Nginx 代理位置示例（需先配置自己的证书与 server_name）：

```nginx
location / {
    client_max_body_size 128k;
    proxy_pass http://127.0.0.1:8787;
    proxy_set_header Host $http_host;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_connect_timeout 5s;
    proxy_read_timeout 30s;
}
```

默认服务不信任客户端提交的 X-Real-IP/X-Forwarded-For。反向代理场景中登录限流按代理地址聚合，可能使同站用户共享失败配额；可在代理层配置按真实连接地址限流。不要直接信任任意请求头来绕过这个限制。

## 数据与备份

`/data` 包含：

- `platform.sqlite3` 及可能存在的 WAL/SHM 文件。
- `messages.key`：消息、来源资料、渠道凭据的加密密钥，也是稳定索引密钥的来源。
- `initial-admin-password.txt`：仅首次初始化使用的密码；修改密码后文件内容不会更新，可在安全保存凭据后删除。

**数据库与 messages.key 必须一起备份**。丢失密钥不能恢复正文；数据库和密钥同时泄露可解密数据。备份也应加密并限制访问。

最简单的一致性备份：停止服务，在停机状态备份整个命名卷，再启动。卷名称可从 `docker volume ls` 查找，Compose 默认项目下通常为 `xinbox_xinbox_data`。

```sh
docker compose stop web
# 此时使用自己的备份工具保存整个数据卷；保留权限和文件内容。
docker compose start web
```

不要在未掌握 SQLite 在线备份/WAL 语义时只复制一个正在写入的 db 文件。恢复时停止服务、恢复整套数据并确保 UID10001 可写，再启动。

## 更新

```sh
# 先备份数据卷并记录当前 Git commit。
git pull --ff-only
docker compose up -d --build
```

不要挂载旧协议版数据库来尝试升级；两个版本应使用不同的 Compose 项目名、端口和数据卷。现有独立版升级会保留卷内数据；`docker compose down -v` 会删除命名卷，不能作为普通重启手段。

## 故障排查

| 现象 | 排查 |
| --- | --- |
| 首页 503 | 本地开发尚未构建前端，或 SMS_STATIC_DIR 错误 |
| 容器构建失败 | 检查镜像/包仓库网络、依赖版本、构建日志；不要复制其他安装的 SDK |
| 无法登录 | 读取初始密码，确认是否已修改；检查 HTTPS 与 Secure Cookie 是否匹配 |
| 来源没有消息 | 本版本等待上游 HTTP 提交；仅添加来源不会从运营商拉取消息 |
| 提交 401 | 检查密钥、用户状态、来源删除/转移情况；新建密钥不会恢复旧密钥 |
| 提交 409 | 同一 event_id 内容变化，或重复创建来源；保持重试内容不变 |
| 转发不执行 | 渠道是否启用、消息是否在创建渠道之后、事件类型及来源/关键词规则是否匹配 |
| destination_not_public | 目标解析到非公网 IP；私网/环回服务被 SSRF 防护拒绝 |
| 企业微信查询失败 | 回调 URL/Token/AESKey、应用与企业 ID、成员白名单、来源归属、渠道启用状态 |
| 磁盘增长 | 当前无自动历史清理；监控卷容量，按真实保留政策安排维护 |

## 容量

Compose 的运行内存上限为 384 MiB，是部署限制而非压测结论；前端构建需要额外内存。SQLite 与加密搜索适合小型单机自托管，不承诺大规模租户吞吐。每密钥接入限流不等于全站容量保证。部署者应监控内存、磁盘、队列积压及外部渠道限额。
