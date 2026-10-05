# OptikLink 自动保活脚本（由 Lunes 保活脚本适配）

# ⭐ **觉得有用？给个 Star 支持一下！**

> 🧪 保活地址：[https://optiklink.com/auth](https://optiklink.com/auth)（免费用户每 3 天必须登录一次，否则服务器离线，7 天删除；只认主 dashboard 登录，游戏面板登录不算）
>
> ⚠️ optiklink.com/auth 只有 Discord OAuth 登录（Sign in with DISCORD），无账密表单。本脚本为 Cookie 保活版：本地手动 Discord 登录一次导出 storageState，CI 带 Cookie 访问 dashboard 即算保活。

定时带 Cookie 访问 OptikLink dashboard，保持账号活跃。复用原 Lunes 脚本骨架（Cloudflare 绕过、Telegram 通知、代理、截图），只换了登录/保活逻辑。

---

## ✨ 功能特性

- ✅ 单账号保活（Cookie 版，无需账密）  
- ✅ Cookie 注入后访问 dashboard 即算保活（满足 3 天登录要求）  
- ✅ Cloudflare 整页挑战自动绕过（`uc_gui_click_captcha`）  
- ✅ Cookie 失效自动识别并告警（TG 通知换 Cookie，不硬闯 Discord）  
- ✅ 内建速率限制保护（Too Many Requests 自动停止）  
- ✅ 支持 sing-box 多协议代理（沿用 `setup_proxy.sh`）  
- ✅ Telegram 通知（带截图）  
- ✅ 定时 / 手动多种触发方式  

---

## 📋 前置要求

### 1. GitHub Secrets 配置

进入仓库 `Settings` → `Secrets and variables` → `Actions`，添加以下 Secrets：

| Secret 名称 | 必填 | 说明 | 示例 |
|------------|------|------|------|
| `OPTIKLINK_STATE` | ✅ | storageState 登录态 JSON（本地 Discord 手动登录后导出全文，超长可转 base64 存放） | `{ "cookies": [...], "origins": [...] }` |
| `OPTIKLINK_ACCOUNT` | ❌ | 账号备注（仅 TG 展示用，不做登录） | `my-discord-name` |
| `NODE_LINK` | ❌ | 代理链接（不配置则直连） | `如 vless:// vmess:// tuic:// hysteria2:// anttls:// socks5://` |
| `TG_BOT_TOKEN` | ❌ | Telegram Bot Token | `123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11` |
| `TG_CHAT_ID` | ❌ | Telegram Chat ID | `123456789` |

### 2. OPTIKLINK_STATE 获取方法（Cookie 保活版核心）

1. 本地 Chrome 手动访问 `https://optiklink.com/auth`，点 **Sign in with DISCORD** 完成登录（含算数验证+CF 验证，人工一次过掉）  
2. 用 Playwright 导出 storageState（示例）：
   ```bash
   npx playwright open https://optiklink.com/auth   # 在打开的浏览器里手动登录
   # 或代码：await context.storageState({ path: 'slim-storage-state.json' })
   ```
3. 把 JSON 全文贴进 Secret `OPTIKLINK_STATE`（内容过长可先 `base64` 再贴，脚本自动识别解码）  
4. Cookie 失效时脚本会 TG 告警“请更新 OPTIKLINK_STATE”，重复本节即可

> ⚠️ 仅支持一个账号。如需多账号管理，请创建多个工作流或仓库。  
> ⚠️ `tmp/`、`slim-storage-state.json`、`storage-state*.json`、`agentscribe-*` 永不进仓库（见 `.gitignore`）。

### 3. 代理格式（确认在v2rayN里使用正常的节点）

`NODE_LINK` 支持以下任意一种代理协议的完整分享链接（不配置则直连）：

- **VLESS**：`vless://uuid@server:port?security=reality&sni=...&type=ws&...`
- **VMess**：`vmess://base64encoded...`
- **Trojan**：`trojan://password@server:port?sni=...&type=ws&...`
- **tuic**：`tuic://uuid:password@server:port...`
- **anytls**：`anytls://uuid@server:port...`
- **hysteria2**：`hysteria2://base64@server:port...`
- **SOCKS5**：`socks5://user:pass@server:port` 或 `socks://user:pass@server:port`

### 4. Telegram 通知配置（可选）

1. 创建 Bot：向 [@BotFather](https://t.me/BotFather) 发送 `/newbot`，获取 Token  
2. 获取 Chat ID：向 [@userinfobot](https://t.me/userinfobot) 发消息或直接与你的 Bot 对话后访问 `https://api.telegram.org/bot<YourToken>/getUpdates`  
3. 将 Bot Token 和 Chat ID 填入 Secrets

---

## 🚀 使用方法

### 方法 1：手动触发（GitHub 网页）

1. 进入仓库 `Actions` 页  
2. 选择 **OptikLink 自动保活** 工作流  
3. 点击 `Run workflow` → `Run workflow`（绿色按钮）  
4. 运行完成后可在日志和 Telegram 中查看结果

### 方法 2：API 调用

#### 使用 GitHub API 手动触发

```bash
curl -X POST \
  -H "Authorization: Bearer ghp_XXXXXXXXXXXXXXXXXXXXXXXXX" \
  -H "Accept: application/vnd.github.v3+json" \
  https://api.github.com/repos/你的用户名/你的仓库名/actions/workflows/Lunes-Keep.yml/dispatches \
  -d '{"ref":"main"}'
```

> 替换 `ghp_xxx` 为你自己的 Personal Access Token，需要 `workflow` 权限。

### 方法 3：定时任务

工作流已内置定时执行：

```yaml
schedule:
  # TOS 要求免费用户每 3 天登录一次；每 2 天跑一次留 1 天余量（北京时间 12:12）
  - cron: '12 4 */2 * *'
```

> ⚠️ 切勿改回 10 天一次（原 `1,11,21`）：违反 3 天规则会导致服务器离线、7 天被删。

---

## 📱 Telegram 通知示例

### 保活成功
```
✅ 保活成功
账号：cookie-account
信息：dashboard 保活成功 (dashboard)
时间：2026-06-28 12:14:21

OptikLink Auto Keep Alive
```
*附带 dashboard 截图*

### 保活失败（Cookie 失效）
```
❌ 保活失败
账号：cookie-account
信息：Cookie 失效或 CF 绕过失败，请更新 OPTIKLINK_STATE
时间：2026-06-28 12:14:21

OptikLink Auto Keep Alive
```
*附带登录页截图，按 README 第 2 节重抓登录态*

### 保活失败（速率限制）
```
❌ 保活失败
账号：cookie-account
信息：IP 已被限制（Too Many Requests, 20/h），脚本已停止
时间：2026-06-28 12:14:21

OptikLink Auto Keep Alive
```
*附带速率限制页面截图*

---

## 🔧 高级配置

### 启用截图保存到 Artifacts

默认不上传截图。如需保留截图用于调试，取消 workflow 文件中以下部分的注释：

```yaml
#      - name: 上传截图
#        uses: actions/upload-artifact@v4
#        if: always()
#        with:
#          name: screenshots-${{ github.run_number }}
#          path: output/screenshots/
#          retention-days: 3
```

### 调整保活频率

修改 workflow 中的 `schedule` 字段即可。注意下限是 TOS 的 3 天，上限建议不高于每天一次，避免触发频率限制。

### 增加 Cloudflare 绕过重试次数

编辑 `scripts/Lunes-Keep.py`，找到 `bypass_cloudflare_interstitial` 调用，修改 `max_attempts` 参数。

---

## 🐛 常见问题

### 1. 提示 Cookie 失效 / 卡在 Discord 登录页

**原因**：  
- `OPTIKLINK_STATE` 过期（Discord 会话被踢/过期）  
- 算数验证仅支持人工过，脚本不自动解  

**解决**：  
- 按第 2 节重抓 storageState 并更新 Secret  
- 在工作流日志中查看截图确认  

### 2. 触发速率限制（Too Many Requests）

**原因**：  
短时间内多次运行脚本，被限制 20 次/小时/ip。  

**解决**：  
- 保持每 2 天一次，不要手动频繁触发  
- 使用代理避免与其他人共享 IP

### 3. Telegram 未收到通知

**原因**：  
- Bot Token 或 Chat ID 不正确  
- 未与 Bot 开始对话  

**解决**：  
- 在 Telegram 里向 Bot 发送 `/start`  
- 验证 Secrets 中 `TG_BOT_TOKEN` 和 `TG_CHAT_ID`  
- 查看 Actions 日志中的 `Telegram 通知` 错误提示

---

## 🔒 安全建议

1. ✅ **所有敏感信息存储在 GitHub Secrets** 中，不会打印到日志  
2. ✅ **登录态过期后及时更新 `OPTIKLINK_STATE`**（过期只告警不硬闯）  
3. ✅ **限制 Personal Access Token 权限**（仅 `workflow` 或 `repo`）  
4. ✅ **仓库设置为私有** 避免配置文件泄露  
5. ✅ **定期检查 Actions 运行日志**，确保一切正常  

---

## 📄 许可证

MIT License

## 🤝 贡献

欢迎提交 Issue 和 Pull Request！

---

**⚠️ 免责声明**：本脚本仅供学习与自动化运维研究，使用者须遵守 OptikLink 的服务条款（免费用户每 3 天登录一次，7 天不登录删机）。因使用本脚本导致的账号限制或其他问题，作者不承担任何责任。
