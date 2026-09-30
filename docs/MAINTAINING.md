# 维护指南

## 版本与发布

`app/release.json` 是版本号的唯一编辑入口。它与用于安装 PWA 的 `app/static/manifest.webmanifest` 各司其职：前者管发布版本，后者管名称、图标和启动方式。

- `application.version`：服务器逻辑与配套网页的版本。
- `clients.windows.version`：Windows 安装包版本，独立于服务器。
- `clients`：客户端登记表；Web 和 iOS 由服务器提供界面，不另编安装包版本。
- `client_protocol`：原生壳向网页报告身份和版本的接口协议。

本次统一以 **1.0.0** 为正式版本基线。此前的 1.0.1/1.0.2 属于开发阶段安装包，不修改已有 Git 标签或 Release。页面不会把老客户端伪装成新版本；未提供版本接口的旧壳显示“Windows client 版本未知”。

发布步骤：

1. 修改 manifest 中发生变化的版本，遵循 `主版本.次版本.修订号`。
2. 在仓库根目录运行 `python scripts/release.py`，同步 Windows 的 `package.json` 与 `package-lock.json`。
3. 运行 `python scripts/release.py --check` 和相关回归测试，提交包括生成的 package 文件。
4. 服务器改动通过 Docker 重新构建部署；Windows 改动通过构建工作流产出安装包，由维护者上传 [Release 页面](https://github.com/Burnside999/WeeklyReport/releases)。用户下载只指向 Release。

版本号随 HTML 渲染，`GET /api/version` 也可读取；不需要登录或选文档，不返回配置与用户信息。在线资源禁止缓存，Service Worker 的离线资源缓存随应用版本更新；不强制刷新正在编辑的页面。离线页显示最后缓存的服务版本，重新联网后以服务器为准。

## 各端显示

| 环境 | 示例 |
| --- | --- |
| 普通网页 | `weeklyreport v1.0.0` |
| iPhone / iPad 网页及主屏幕应用 | `weeklyreport (iOS version) v1.0.0` |
| Windows 壳 | `weeklyreport v1.0.0 (Windows client v1.0.0)` |
| Windows 连接设置页（还没连上服务器） | `Windows client v1.0.0` |

正文底部的版本文字不固定、不覆盖导航，也不会弹出更新提示。应用升级后，Windows 显示的两个版本可以不同，这是正常情况。

## 新增客户端

新增原生壳时，在 manifest 的 `clients` 中登记 `kind: "native"`、名称和包版本，并让打包工具使用对应版本。壳通过受隔离的桥接对象提供：

```js
window.weeklyReportClient = {
  protocolVersion: 1,
  getInfo: async () => ({id: 'linux', name: 'Linux', version: '1.0.0'})
};
```

上面只是接口示例，目前没有 Linux 安装包。`id` 使用小写字母、数字、短横线，最长 32 字符；`name` 为最长 32 字符的英文显示名；版本使用语义版本格式。网页自动显示该客户端名称与版本，不必添加平台判断。读取失败只影响版本文字，不影响页面操作。

Windows 中该接口经 preload → IPC → `app.getVersion()` 读取实际安装包版本；仅允许已配置网站的主窗口顶层页面调用。新增壳也应限制调用来源，不能开放任意命令或文件访问。通知桥接 `weeklyReportDesktop` 保持兼容，版本接口独立于通知实现。

## 代码结构

| 位置 | 职责 |
| --- | --- |
| `app/main.py` | 组装应用、启动和关闭后台任务 |
| `app/monitor.py` | 定时检查、名单读取、原子发布查询结果 |
| `app/web/security.py` | 会话鉴权、文档隔离、写请求互斥与安全响应头 |
| `app/web/sessions.py` | 加密登录、记住帐号、恢复与退出会话 |
| `app/web/workspace.py` | 单个文档的查询、规则、设置、模板 HTTP 接口 |
| `app/web/assets.py` | 页面渲染、公开/私有资源白名单、版本接口 |
| `app/accounts.py` / `workspaces.py` | 帐号与数据持久化、文档运行实例及管理接口 |
| `app/templates.py` / `template_language.py` | 邮件触发调度、模板语法与变量替换 |
| `app/tencent.py` / `merge_layout.py` | 腾讯 API 和合并区域解析 |
| `app/static/tooltip.js` / `tips.js` | 通用提示交互 / 少量字段提示文案 |
| `app/static/tour.js` / `tour-steps.js` | 引导定位和场景 / 20 步教程文案 |
| `app/static/style.css` / `help.css` / `tour.css` | 应用通用 / 帮助手册 / 引导样式 |
| `app/static/version.js` | 各客户端版本文字，不参与业务判断 |
| `desktop/` | Electron 主进程、通知、沙箱桥接和安装设置页 |

新增静态文件必须登记到 `assets.py` 的相应白名单，不公开整个静态目录。公开资源不得含用户数据或配置。改动 HTTP 层时保留加密登录、文档隔离和写请求互斥；业务算法和数据库结构无需随模块整理而变更。

Tooltip 只解释容易误解的选项，完整操作放进帮助页。桌面问号为 18px、悬停 300ms 后出现、移开立即关闭；触屏使用 28px 点击区域，再次点击、点其他地方或滚动都会收起。不要给整个标签或输入框加悬停触发，避免妨碍正常填表。

## 验证

```bash
python scripts/release.py --check
python -m unittest discover -s tests -v
node --test desktop/tests/*.test.cjs
node tests/test_version.cjs
node tests/test_service_worker.cjs
node tests/test_display_time.cjs
```

浏览器测试使用本地假腾讯/SMTP 服务，不发送真实邮件。各 `tests/browser_*.cjs` 需**逐个执行**，因为共用 18081 端口。`browser_version.cjs` 覆盖各页版本、移动端提示和独立客户端版本；Windows 工作流额外验证已安装 EXE 的版本 IPC、登录和托盘通知。
