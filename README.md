# WeeklyReport · 周报填写检查

手机优先的腾讯文档在线表格监听器。密码登录后只有两个页面：**填写情况**与**监听管理**。默认每 5 分钟由服务器检查一次，关闭浏览器也会继续运行。

## Docker 部署

```bash
git clone https://github.com/Burnside999/WeeklyReport.git
cd WeeklyReport
cp .env.example .env
# 编辑 .env，设置 ADMIN_PASSWORD（至少 12 字符，不可保留示例值）
nano .env
docker compose up -d --build
```

访问 `http://服务器IP:8080`，输入 `.env` 中的密码。数据库、规则、腾讯 API 凭据和上次查询结果持久化在 Docker 命名卷 `weeklyreport-data`。无需 Node、数据库服务或额外定时任务。

预置文档地址：`https://docs.qq.com/sheet/DY3hoUWtYVGJOR1lz`。不会猜测工作表与列号；首次登录请添加实际规则。

公网部署请通过 Nginx/Caddy 提供 HTTPS，再设置 `COOKIE_SECURE=true`；仅通过 HTTPS 访问。若反向代理在宿主机，可设置 `BIND_ADDRESS=127.0.0.1`。直接 HTTP 访问时保持 `COOKIE_SECURE=false`，否则浏览器不会发送登录 Cookie。

修改密码后运行 `docker compose up -d --force-recreate`，已有会话随进程重启失效。密码只由服务器环境变量设置，管理页面不能修改或读取密码。

## 腾讯文档官方 API 授权（必须配置）

**公开访问文档不代表官方 Open API 可以匿名访问。** 本项目使用腾讯官方接口，不依赖 Cookie 抓取或未公开的网页内部接口。需要腾讯文档开放平台的应用与用户授权。

1. 在[腾讯文档开放平台](https://docs.qq.com/open/)创建应用，获取 Client ID / Client Secret。
2. 按[官方 OAuth 授权流程](https://docs.qq.com/open/document/app/oauth2/)让有权访问目标表格的账号授权，获取对应的 `user_id`（Open ID）、Access Token、Refresh Token。授权及授权码兑换在腾讯平台完成；本应用管理页接收已有凭据，不提供 OAuth 回调页面。
3. 申请 `scope.sheet.readonly`（读取表格）。自动将分享链接转换为 File ID 还需 `scope.drive.file.metadata.readonly` 或转换接口文档列出的其他许可。如果已知官方 File ID，可直接填写并跳过转换接口。
4. 登录本应用，打开 **监听管理 → 高级设置**，填写 Client ID、Open ID、Access Token 并保存。
5. 推荐同时填写 Refresh Token 与 Client Secret，服务会按返回的 `expires_in` 提前续期并保存最新令牌。只有 Access Token 时也可使用，但过期后需要手动更新。Refresh Token 被撤销或失效时仍须重新授权。
6. 添加规则，点击“从腾讯文档读取工作表”，选择对应 Sheet，配置责任人列、待填列、起止行并保存。

密钥字段保存后不回显，留空表示保留原值。明确勾选“清除已保存的全部令牌和 Secret”才会清除。凭据保存在权限受限的 SQLite 数据库中（不是加密保险库），不要公开数据卷和备份。

### 使用的官方接口

| 用途 | 接口 |
| --- | --- |
| 分享 ID 转 File ID | `GET /openapi/drive/v2/util/converter?type=2&value=...` |
| 工作表元数据 | `GET /openapi/spreadsheet/v3/files/{fileId}` |
| 单元格范围 | `GET /openapi/spreadsheet/v3/files/{fileId}/{sheetId}/{range}` |
| 刷新令牌 | `GET /oauth/v2/token?grant_type=refresh_token&...` |

文档：[ID 转换](https://docs.qq.com/open/document/app/openapi/v2/file/util/converter.html)、[工作表信息](https://docs.qq.com/open/document/app/openapi/v3/sheet/get/get_sheet.html)、[范围内容](https://docs.qq.com/open/document/app/openapi/v3/sheet/get/get_range.html)、[令牌刷新](https://docs.qq.com/open/document/app/oauth2/refresh_token.html)。采用官方 V3 `gridData.rows[].values[].cellValue` 数据结构；逐列按 1000 行分页，遵守每次最多 1000 行、200 列、10000 单元格的限制。

## 规则与统计口径

例如：工作表“研发周报”，责任人在 B 列，本周总结在 F 列，第 3–100 行。

- 添加名称“本周总结”、选择 Sheet、责任人列 `B` 或 `2`、检查列 `F` 或 `6`，起止行 `3` 和 `100`。
- B8 为“张三”、F8 为空时，首页显示“张三 / 本周总结 / 研发周报”，并标注 F8。
- 每条规则监听一个列；同一 Sheet 的多个必填列可添加多条规则。
- 责任人为空的行忽略；不会向下填充合并单元格中的责任人。请使用每一条数据行都有责任人的表结构，或设置对应起止行。
- `null`、空字符串、仅空白字符算未填写。数字 `0`、布尔值 `false`、图片、位置等非空值算已填写；占位文字如“无”“-”也算已填。公式以 API 返回的单元格值为准，不在本地计算公式。
- 人数按去除首尾空白后的责任人名称去重；同名人员会合并。条目数按 Sheet + 行 + 列去重，重复规则不会重复计数。
- 起止行都包含在内，表头由起始行跳过；每条规则最多 10000 行，最多 50 条规则。新增人员超出结束行不会自动纳入，需调整规则。
- 可暂停、编辑、删除规则。保存后重新查询；查询进行中拒绝配置修改，避免把新旧规则混在同一次结果中。
- 所有启用规则都读取成功才发布新结果。任一失败会保留上次完整结果、显示“检查异常”和旧结果时间；首次失败显示未知人数，不显示“0 人”。
- 首页每 5 秒读取服务器检查状态；此动作不会触发腾讯 API。手动查询与自动检查互斥，不重复启动任务。
- 默认一轮完成后 300 秒开始下一轮；耗时过长时不会重叠执行。高级设置可改为 60–86400 秒。
- “告知”通过首页人数、状态与未填明细展示。本版本不向微信、邮件或他人发送消息。

## 设置

| 设置 | 默认 | 位置 |
| --- | --- | --- |
| 访问密码 | 必填、至少 12 字符 | `.env` 的 `ADMIN_PASSWORD` |
| 对外端口 | 8080 | `.env` 的 `PORT` |
| 绑定地址 | 0.0.0.0 | `.env` 的 `BIND_ADDRESS` |
| HTTPS Cookie | false | `.env` 的 `COOKIE_SECURE` |
| 会话有效期 | 24 小时，最大 168 | `.env` 的 `SESSION_HOURS` |
| 文档地址 / File ID | 预置分享链接 / 自动转换 | 管理 → 高级设置 |
| 检查间隔 / 超时 | 300 秒 / 20 秒 | 管理 → 高级设置 |
| 腾讯 API 凭据 | 未配置 | 管理 → 高级设置 |

更换表格时也应更新/清空 File ID，并重新选择每条规则的 Sheet ID。规则不会凭 Sheet 名称自动映射至另一份文档。

## 维护

```bash
# 日志 / 健康检查
docker compose logs -f --tail=100
curl http://127.0.0.1:8080/healthz

# 更新
git pull --ff-only origin main
docker compose up -d --build

# 停止（不删除数据卷）
docker compose down
```

不要运行 `docker compose down -v`，除非确定需要删除全部配置和历史结果。备份时先停止服务，然后备份命名卷；恢复时使用同一卷。保持单实例运行，不要在同一数据库上部署多个调度器。登录失败按连接来源限流，反向代理下多个访问者可能共享同一个限流窗口（10 次 / 15 分钟），不会盲目信任客户端提供的转发头。

## 本地开发与验证

Python 3.12+：

```bash
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
export ADMIN_PASSWORD='replace-with-your-long-password'
python -m app.main
```

```bash
python -m unittest discover -s tests -v
node --check app/static/app.js
node --check app/static/login.js
```

测试覆盖鉴权、CSRF 请求校验、限流、CRUD、凭据脱敏、空单元格、去重、分页与坐标、令牌轮换、旧结果保留、后台调度与并发防重。`.github/workflows/ci.yml` 会进一步构建 Docker 镜像并进行容器健康检查。

开发交付时未提供腾讯 API 凭据，无法对实际文档做授权 API 联调；接口适配依据上述官方文档，使用模拟响应验证。请配置凭据后通过“读取工作表”和“立即查询”完成真实连通性验证。
