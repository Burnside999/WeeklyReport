# develop：独立服务器测试 PWA 主推送

本分支基于 `main` 的 `8b20ca2576225ca42c8cb01285982f7e847bb37d`。
仅在新的服务器部署，不修改生产服务器，不合并 main。第一版支持 Apple Web Push；Windows 继续使用网页，桌面托盘壳与国内 Android 厂商推送不在本次范围内。

## 1. 隔离部署

准备新的域名，例如 `weekly-test.example.com`，解析到新服务器。不要修改正在使用的域名解析，也不要让两个服务器轮流响应同一个域名。PWA、Cookie 和推送订阅都绑定站点来源。

```bash
git clone --branch develop --single-branch https://github.com/Burnside999/WeeklyReport.git WeeklyReport-develop
cd WeeklyReport-develop
cp .env.example .env
nano .env
```

私有仓库先用自己的 GitHub 鉴权方式登录。不要把 GitHub 令牌写进 clone URL 或配置文件。

`.env` 示例（密码和邮箱替换成真实值）：

```dotenv
COMPOSE_PROJECT_NAME=weeklyreport-develop
ADMIN_PASSWORD=replace-with-a-new-long-test-password
BIND_ADDRESS=127.0.0.1
PORT=8080
COOKIE_SECURE=true
SESSION_HOURS=24
WEB_PUSH_ENABLED=true
VAPID_SUBJECT=mailto:your-email@example.com
```

`VAPID_SUBJECT` 是推送服务遇到问题时的联系邮箱，不是接收邮箱，也不是 SMTP 密码。使用有效的可联系邮箱。默认 WEB_PUSH_ENABLED=false；关闭时仍可选择主推送、记录通知和发送邮件，但不创建新的设备投递任务。已排队且仍在有效期内的任务重新开启后会继续投递。

```bash
docker compose up -d --build
docker compose logs --tail=100
curl -fsS http://127.0.0.1:8080/healthz
```

不同 Compose 项目名会创建独立数据卷（通常为 `weeklyreport-develop_weeklyreport-data`）。本次建议使用全新数据，不复制生产数据库：复制会连同自动邮件规则一起带入，测试服务器也可能发送真实邮件。

首次开启推送自动在 `/data/vapid-private.pem` 创建权限 0600 的 P-256 私钥；重建容器复用同一数据卷、不会轮换密钥。不要删除密钥或将它提交 Git。备份时连同数据库备份整个数据卷；丢失或主动更换密钥后，各设备需重新开启通知。

## 2. HTTPS 与网络

必须通过有效 HTTPS 域名使用 iPhone 推送，不能使用普通服务器 IP 的 HTTP 地址或自签名证书。Nginx 在同一宿主机时可采用 `deploy/nginx/develop.example.conf`，修改域名、证书路径后验证并重载。该模板不会替换生产配置。

```bash
sudo nginx -t
sudo systemctl reload nginx
```

Nginx 将全部路径代理到 `127.0.0.1:8080`，包括 `/sw.js`、`/manifest.webmanifest`、`/static/`。不要对 `/sw.js` 或 HTML 配置强缓存，也不要让代理缓存登录与 API 响应。本应用在这些响应上设置 `Cache-Control: no-store`。

服务器需要通过 DNS 解析并以 HTTPS 443 访问订阅提供的 `*.push.apple.com` 地址；推送直接访问 Apple，不使用第三方聚合平台或 Google 服务。发送模块不读取环境 HTTP 代理，也不跟随 HTTP 重定向。订阅接口只接受 Apple 推送域名，拒绝私网地址、任意第三方地址和自定义端口。

## 3. 配置与安装

1. 使用新的 admin 帐号登录；为使用者创建自己的帐号和文档管理器，配置腾讯文档凭据与监听规则。
2. 测试阶段先把邮件接收人配置为自己的邮箱，避免重复提醒实际同事。
3. 在邮件模板中选中一条“设为主推送”。同一帐号跨所有文档最多一个，可取消。邮件仍按原配置发送，推送给模板所属帐号开启通知的所有设备，不按邮件接收人推断用户。
4. iPhone（iOS 16.4+）用 Safari 打开新的 HTTPS 网站，分享 → 添加到主屏幕。
5. 从桌面图标打开，重新登录。在首页展开“设备通知”，点击“开启通知”，允许系统权限。
6. 点击“发送测试通知”，它只测试当前设备，不发邮件，不执行文档规则。测试间隔至少 60 秒。
7. 设置中拒绝过通知权限的，需要先到 iOS 系统通知设置中重新允许。

通常需独立主屏幕安装；Safari 普通标签页不能替代 iPhone 主屏幕 Web App 的推送授权流程。PWA 无需 Apple Developer 付费会员、TestFlight 或描述文件。

## 4. 真机验收清单

- 开启通知后发送测试，确认系统通知显示；页面的“Apple 已接受通知”只表示上游受理，不表示用户已看见。
- 回到桌面并锁屏，通过另一台设备手动触发主推送，确认标题变量已替换。
- 关闭 PWA 后再触发一次；真机观察通知中心和锁屏表现。
- 点击通知进入正确文档；登录失效时先登录，再回到对应文档。
- 普通模板只发邮件，主推送额外发通知；取消/切换主推送后不补发历史通知。
- 手动触发、固定时刻及每周检查都验证一次。自动规则仍需满足原来的时间、条件和数据新鲜度要求。
- 在另一台 iPhone 登录同一帐号并开启通知，验证多设备；“关闭本设备通知”不影响另一设备。
- 退出登录后不再接收该浏览器的新投递；换账号后必须显式重新开启通知。已经投递给 Apple 的消息无法撤回。
- 断网时显示离线提示，不提供离线编辑；恢复连接后重新打开。浏览器缓存仅存公共离线页面与样式，不缓存文档数据/令牌/管理接口。

网络、通知授权、专注模式、系统调度可能影响显示；不承诺每条即时到达。锁屏/后台表现必须以真实 iPhone 测试为准，桌面模拟测试不能替代。

## 5. 投递与异常

- SMTP 使用原有状态机，不新增自动重试，保留“结果不确定时人工核实”的行为。
- 邮件触发声明与主推送通知/任务通常同一 SQLite 事务提交；通知模块异常时记服务器错误，邮件仍按原逻辑执行。
- 通知只在触发时生成。切换主推送、首次安装、重连都不补推历史记录。
- Web Push 使用独立持久化队列，每次最多处理 20 项；临时连接错误、429、5xx 最多尝试 5 次，重试间隔 30/60/120/240 秒。每条通知有效期 1 小时，发给 Apple 的 TTL 为剩余有效期。
- 404/410 清理失效订阅；其他永久性错误停止重试，设备入口显示最近投递状态。处理配置后可重新发送测试通知。
- 重启恢复未完成投递，沿用同一通知 ID 和系统 tag；网络不确定时可能重投，不宣称严格恰好一次。不同触发有不同 ID。
- 设备预览受 Web Push 大小限制，极长标题会截短并加省略号；完整标题保留在通知查询接口。
- 通知历史保留 30 天；每个帐号最多 10 个有效设备订阅。密码/权限变更会撤销旧版本订阅，需重新开启。
- 推送订阅端点、加密参数和 VAPID 私钥不写入日志。

## 6. 接口

均须登录；写入携带 `Content-Type: application/json` 与 `X-Requested-With: WeeklyReport`。以下接口按帐号隔离，不需要 `X-Document-ID`。

| 接口 | 内容 |
| --- | --- |
| `GET /api/me/primary-trigger` | 当前选择与文档/模板标题 |
| `PUT /api/me/primary-trigger` | `{"document_id":"…","trigger_id":"…"}`；两者 null 取消 |
| `GET /api/push/config` | 是否启用、公钥、当前设备登记及最近投递状态 |
| `POST /api/push/subscriptions` | 浏览器 `PushSubscription.toJSON()`；先读取 config 建立 HttpOnly 设备 Cookie |
| `DELETE /api/push/subscriptions/{id}` | 仅删除本帐号的指定订阅 |
| `POST /api/push/test` | 当前设备测试，60 秒限流 |
| `GET /api/notifications?after=0` | 最多 100 条本人业务通知，返回 `next_cursor`；不包含测试消息 |

通知列表只用于同步/查历史，当前 PWA 不通过轮询此列表弹出通知。通知状态 `accepted` 指 Apple 受理，没有实现已读回执。

## 7. 更新与撤销测试

```bash
# 只更新测试目录的 develop
git pull --ff-only origin develop
docker compose up -d --build
```

不使用强制页面刷新，正常重新打开页面加载新版；Service Worker 自动更新公共离线资源。更新前先保存正在编辑的表单。

需要停用推送时，把 `WEB_PUSH_ENABLED=false` 后重建容器。需要结束测试时：

```bash
docker compose down
```

不会影响另一台生产服务器。不要加 `-v`，除非确定连测试数据与 VAPID 密钥也要删除。不要让 main 回退版本直接操作测试版数据库；回退应使用升级前备份或全新数据卷。

## 8. 开发验证

```bash
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
node --check app/static/pwa.js
node --check app/static/sw.js
# 与现有浏览器测试共用 Playwright 配置
PLAYWRIGHT_NODE_MODULES=/path/to/node_modules node tests/browser_pwa.cjs
node tests/test_service_worker.cjs
```

自动化使用假 SMTP 和推送传输，不发送真实通知。实际 Apple 端到端送达需按上面的真机清单验收。
