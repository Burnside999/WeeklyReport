# WeeklyReport · 周报填写检查

手机优先的腾讯文档在线表格监听器。密码登录后有五个页面：**填写情况**、**监听管理**、**邮件模板**、**设置**与**变量表**。默认每 5 分钟由服务器检查一次，关闭浏览器也会继续运行。

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
2. 按[官方 OAuth 授权流程](https://docs.qq.com/open/document/app/oauth2/)让有权访问目标表格的账号授权，获取对应的 `user_id`（Open ID）、Access Token、Refresh Token。授权及授权码兑换在腾讯平台完成；本应用设置页接收已有凭据，不提供 OAuth 回调页面。
3. 申请 `scope.sheet.readonly`（读取表格）与 `scope.drive.exportable`（读取真实合并结构）。自动将分享链接转换为 File ID 还需 `scope.drive.file.metadata.readonly` 或转换接口文档列出的其他许可。如果已知官方 File ID，可直接填写并跳过转换接口。
4. 登录本应用，打开 **设置 → 高级设置**，填写 Client ID、Open ID、Access Token 并保存。
5. 推荐同时填写 Refresh Token 与 Client Secret，服务会按返回的 `expires_in` 提前续期并保存最新令牌。只有 Access Token 时也可使用，但过期后需要手动更新。Refresh Token 被撤销或失效时仍须重新授权。
6. 在“设置 → 选择数据源”（默认折叠）中读取工作表列表，选择名单所在 Sheet、姓名列与起止行，保存读取。默认全选，可取消勾选休假同事后保存统计范围。
7. 添加监听规则，选择实际 Sheet、责任人列、一个或多个待填列及起止行。首次检查会导出一次合并结构；填写内容仍走 V3 实时读取。

密钥字段保存后不回显，留空表示保留原值。明确勾选“清除已保存的全部令牌和 Secret”才会清除。凭据保存在权限受限的 SQLite 数据库中（不是加密保险库），不要公开数据卷和备份。

### 使用的官方接口

| 用途 | 接口 |
| --- | --- |
| 分享 ID 转 File ID | `GET /openapi/drive/v2/util/converter?type=2&value=...` |
| 工作表元数据 | `GET /openapi/spreadsheet/v3/files/{fileId}` |
| 单元格范围 | `GET /openapi/spreadsheet/v3/files/{fileId}/{sheetId}/{range}` |
| 刷新令牌 | `GET /oauth/v2/token?grant_type=refresh_token&...` |

文档：[ID 转换](https://docs.qq.com/open/document/app/openapi/v2/file/util/converter.html)、[工作表信息](https://docs.qq.com/open/document/app/openapi/v3/sheet/get/get_sheet.html)、[范围内容](https://docs.qq.com/open/document/app/openapi/v3/sheet/get/get_range.html)、[令牌刷新](https://docs.qq.com/open/document/app/oauth2/refresh_token.html)。采用官方 V3 `gridData.rows[].values[].cellValue` 数据结构；逐列按 1000 行分页，遵守每次最多 1000 行、200 列、10000 单元格的限制。

## 名单、任务与统计口径（新版）

在 **设置 → 选择数据源** 中配置当前文档内任意 Sheet 的一列、第 X–Y 行。Sheet 按 ID 选择，不依赖名称、位置或排序；名单一行一个名字，忽略空行、去掉首尾空白并去重。每轮自动更新名单，保存数据源时也会立即读取。

- 首次默认全选。取消勾选休假同事后点击“保存统计范围”；刷新/重启后保留取消状态，新同事默认勾选。可全不选，此时无人需要统计。
- 同名人共用一个名字；责任人匹配严格采用 `Name in 责任人原文`，不删除括号、不拆分分隔符。因此“张三（协助李四）”同时匹配张三和李四，“张三”也会匹配“张三丰”，这是指定的子串匹配口径。
- 每条监听规则可设置多个列，例如 `D,F,H` 或 `4,6,8`。旧规则的单列会自动作为一个元素处理，原规则不会丢失。
- 责任人单元格纵向合并的整个区域算一个任务，未合并的每一行各算一个任务。任务覆盖的任一监听列、任一行有非空文字就通过。共同责任人共享该任务填写状态。
- 同一个人另一个任务完全没有文字，仍然列为未填；绝不按姓名将独立任务的填写状态合并。
- 空白字符串、只有空格/换行以及纯数字、布尔值、图片均不算文字。文本 `0`、`无`、`-` 和超链接的非空显示文字算已填写。不在本地计算公式，按 API 返回文本判断。
- 责任人空白且不属于实际合并区域的行忽略，不猜测向下填充。责任人列横向合并时读取左上角姓名。
- 监听起止行必须包含完整的责任人合并区域；截断会显示错误。填写列合并跨越多个责任人任务也会显示错误，避免错误归属。
- 明细按人 + Sheet + 任务行范围 + 监听列集合去重，首页按人去重；显示具体任务行范围。
- 每条规则最多 10000 行、20 个监听列，最多 50 条规则。表头由起始行跳过，超出结束行的新增任务需扩大范围。
- 所有规则成功后才发布结果；失败保留上次完整结果并明确标记过期。首次失败人数未知。升级前旧统计口径的结果不作为新口径结果使用。

### 合并结构同步与腾讯限制

官方 V3 范围接口返回单元格内容，但文档未提供可单独查询完整合并区域的接口。因此通过官方 `POST /openapi/drive/v2/files/{fileID}/async-export` 与 `GET /openapi/drive/v2/files/{fileID}/export-progress` 获取 XLSX，解析 `mergeCells`。不保留导出的单元格内容，也不会把导出时的旧文本用于定时检查。

**腾讯官方规定每用户每天最多导出 9 次。** 本应用最多 8 次/滚动 24 小时，合并结构每六小时同步；“刷新合并结构”按钮可立即同步并消耗一次额度。外部工具使用同一用户的额度也可能影响导出。定时文本仍每五分钟实时读取。结构同步失败时自动重试间隔为一小时，未取得结构或结构已过期时不生成假正常结果。

**插入/删除行、重新合并/拆分单元格后，请点击“刷新合并结构”。** 缓存有效期内无法发现所有结构变动；设置页显示最后同步时间。同步过程以当前 Sheet ID 与唯一标题对应导出的工作表，不以 Sheet 位置猜测；更换文档会清空名单配置与合并结构，需重新选择数据源。

首次部署或升级需补充 `scope.drive.exportable` 权限。没有导出权限时会明确报错，不能仅凭空行判断合并关系。导出最大 20 MiB，解压总大小最大 100 MiB。

官方文档：[导出文档及配额](https://docs.qq.com/open/document/app/openapi/v2/file/export/async_export.html)、[导出进度](https://docs.qq.com/open/document/app/openapi/v2/file/export/export_progress.html)。

### 邮件发送配置

高级设置保留发送邮箱、发送端口、发送密码/授权码、发送名称、SMTP 服务器和 SSL/TLS / STARTTLS。接收邮箱在各模板中维护（1–5 个），升级时移除旧的全局接收邮箱及接收名称，不自动创建模板。

密钥不在接口回显，留空保留、勾选清除后删除。保存 SMTP 设置本身不会发送邮件。`app/mail.py` 使用 Python 标准库 `smtplib` 在后台线程发送纯文本邮件，校验 TLS 证书，SMTP 操作超时 30 秒。SMTP 接受邮件后显示“发送成功”，不代表已进入收件箱；服务器可能继续投递或退信。

## 设置

| 设置 | 默认 | 位置 |
| --- | --- | --- |
| 访问密码 | 必填、至少 12 字符 | `.env` 的 `ADMIN_PASSWORD` |
| 对外端口 | 8080 | `.env` 的 `PORT` |
| 绑定地址 | 0.0.0.0 | `.env` 的 `BIND_ADDRESS` |
| HTTPS Cookie | false | `.env` 的 `COOKIE_SECURE` |
| 会话有效期 | 24 小时，最大 168 | `.env` 的 `SESSION_HOURS` |
| 文档地址 / File ID | 预置分享链接 / 自动转换 | 设置页顶部 / 设置 → 高级设置 |
| 检查间隔 / 超时 | 300 秒 / 20 秒 | 设置 → 高级设置 |
| 腾讯 API 凭据 | 未配置 | 设置 → 高级设置 |

更换表格时也应更新/清空 File ID，重新配置名单数据源，并重新选择每条规则的 Sheet ID。规则不会凭 Sheet 名称自动映射至另一份文档。

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

测试覆盖鉴权、CSRF 请求校验、限流、CRUD、凭据脱敏、文本口径、多人子串匹配、名单排除、独立任务与合并边界、多列与坐标、导出配额和缓存、邮件配置不发送、令牌轮换、旧结果保留、后台调度与并发防重。`.github/workflows/ci.yml` 会进一步运行手机视口的邮件编辑与触发浏览器测试（模拟 SMTP），构建 Docker 镜像并进行容器健康检查。

开发交付时未提供腾讯 API 凭据，无法对实际文档做授权 API 联调；接口适配依据上述官方文档，使用模拟响应验证。请配置凭据后通过“读取工作表”和“立即查询”完成真实连通性验证。

页面依次为“填写情况”“监听管理”“邮件模板”“设置”和“变量表”。监听管理仅编辑规则；设置页顶部展示腾讯表格地址，其后依次为默认折叠的选择数据源、合并单元格结构刷新和高级设置。顶部“保存设置”与“保存高级设置”均保存地址及高级配置。


## 变量表与邮件模板接口

“变量表”以变量名、类型、描述、值四列展示数据，变量名显示为行内代码。手机可横向滑动表格。页面打开时读取，之后每 30 秒刷新，也可以点“刷新变量”。这些操作仅计算本地变量，不调用腾讯 API；需要最新填写结果时，请在首页“立即查询”。邮件模板可直接引用这些变量。

日期和时间统一使用北京时间（Asia/Shanghai，UTC+8），每周从周一到周日；日期为 `YYYY-MM-DD`，时间为 `HH:mm:ss`，查询时间含 `+08:00` 时区。34 个全局变量：

- `global.time`、`global.date`、`global.week.now`（星期一至星期日）。
- `global.week`、`global.preweek`、`global.postweek` 下均有 `monday`、`tuesday`、`wednesday`、`thursday`、`friday`、`saturday`、`sunday` 和 `duration`。日期区间格式为 `YYYY-MM-DD ~ YYYY-MM-DD`，包含首尾日期。
- `global.listencount`、`global.listenenable`、`global.url`、`global.personcount`、`global.personlist`、`global.healthy`、`global.lastquery`。

每条规则可在监听管理中编辑“变量名”，首次自动分配 `listenerX`；旧规则升级时自动补全并持久化，编辑、重启不会重新编号。命名以英文字母开头，仅含英文字母和数字，长度 1–64；不区分大小写检查唯一性，保留 `global`。引用名称使用保存时的大小写。重命名后旧引用失效。

每个规则前缀下提供 `name`、`sheetname`、`sheeturl`、`personcol`、`taskcol`、`startrow`、`endrow`、`enable`、`personcount`、`personlist`。列号用字母，多列用英文逗号连接；人数和姓名列表均去重，即使多个规则覆盖同一任务，也分别保留每个规则的未交名单。

`global.healthy` 为“正常”“查询中”“异常”或“等待首次查询”。`global.lastquery` 为最后一次查询的开始时间，包含失败查询。初次查询前、查询失败、配置修改后尚未重查的统计返回 `null`，界面显示“未知”；停用规则的未交统计也返回 `null`。有效查询确实无人未交时才返回人数 `0` 和名单空字符串。升级前的旧快照未保存逐规则名单时，逐规则统计需要等一次成功查询。

后续模板模块可直接调用 `app.variables.build_variables(store, running=False)`，或通过受密码登录保护的 `GET /api/variables` 获取：

- `rows`：四列定义，包含 `name`、`type`、`description`、`value`。
- `values`：完整变量名到原生 JSON 值的映射，布尔值和整数保留类型；未知值为 `null`。
- `generated_at`、`timezone`、`query_running`、`results_available`：生成时间和结果状态。

模板渲染器应按完整变量名查找 `values`，明确处理未知值；不要使用 `eval`。变量接口只公开本节列出的业务数据，不公开密码、令牌、SMTP 凭据。


## 邮件模板与触发

模板初始为空，通过“邮件模板 → 新建模板”配置接收人、标题、正文和触发模式。标题与正文输入 `{{global.date}}` 等变量后，合法变量显示蓝色，未知变量红色，也可用下拉菜单插入。只支持变量替换，不执行表达式、脚本或 HTML。保存时检查变量名；发送时用同一份变量快照渲染，未知值、无效变量、标题换行会阻止发送。

### 手动触发

保存后，卡片右下角有“发送邮件”。SMTP 接受后变为“发送成功”并在当前页面禁用，刷新页面后可再次发送。服务端记录上次触发时间（发送尝试开始时间，包含失败）；5 分钟内再次触发返回确认要求，前端提示具体时间。确认绑定上一次尝试的标识，其他页面若已再次触发，旧确认不再生效。编辑时校验模板版本，避免发送过期配置。

### 自动触发

- **固定日期时间**：例如 `2026-09-25T09:00`，按北京时间解释；可通过 API 提交含时区的 ISO 日期时间。
- **日期变量 + 时刻**：例如 `global.week.friday` + `09:00`，即本周五上午九点。变量可写裸名或 `{{变量名}}`，必须为 `date` 或 `datetime`。`date` 搭配时刻；`datetime` 已包含时刻，忽略时刻输入。只有时分秒的 `global.time` 不能作为完整触发时间点。
- **触发条件**：选择 `integer`、`boolean`、`date`、`time` 或 `datetime` 变量，关系为大于、小于、等于。整数比较数值；布尔值写 `true`/`false`（false 小于 true）；日期、时间按对应格式比较。字符串和 URL 不参与条件比较。

保存即启用自动触发，服务端每 15 秒检查一次，不依赖浏览器。时间到达后才比较条件；未满足或变量未知则等待。涉及未交统计的条件或邮件内容，必须等时间点之后的一次完整成功查询，且结果不超过“查询间隔 + 60 秒”；查询中也等待。不会因旧的“0 人”结果发出完成通知。表格仍按配置间隔查询，邮件调度不会额外消耗腾讯 API 配额。

每个解析后的时间点仅发送一次，成功后按钮为“重置自动触发”。手动重置、模板配置实际变化（接收人、标题、正文、模式、时间点、条件），或时间变量解析结果变化，都会重新等待触发；原样保存不会重置。例如周一到来后，`global.week.friday` 变为本周新的周五，自动进入新一轮。`global.date` 表示每日重新等待；如果使用 `global.lastquery`，每次查询时间变化也会新开一轮。重置后若时间和条件已满足，可能立即发送。

### 持久化与发送异常

模板、配置版本、当前时间周期、发送状态、上次触发和成功时间均存入 SQLite。发送前先持久化“发送中”，成功后再记为已发送；进程重启不会再次发送已完成周期。发送期间拒绝模板变更和重复发送。

SMTP 不提供严格的端到端“恰好一次”保证：部分收件人拒收、DATA 后断线或进程崩溃时，邮件可能已经送达。因此自动发送异常后停止本轮重试，卡片显示错误并允许“重置自动触发”；重启时未完成的发送标记为结果不确定，须核实后重置或重发，避免重复邮件。全新日期周期仍按配置自动开始。此应用按单进程、单容器运行，请勿多个实例共用同一数据卷。

接口（均须登录；写入须 `X-Requested-With: WeeklyReport`）：

- `GET/POST /api/templates`：列出/创建。
- `PUT/DELETE /api/templates/{id}`：更新/删除，须带 `revision`。
- `POST /api/templates/{id}/send`：手动发送，须带 `revision`；短期重复需带服务端提供的 `confirm_attempt`。
- `POST /api/templates/{id}/reset`：重置自动触发，须带 `revision`。

自动触发和模板测试全部使用模拟 SMTP，不向真实邮箱发送。部署后先配置发送服务，再创建模板。
