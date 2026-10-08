# OptikLink 主站保活（eooce 基座版）

骨架复刻 `eooce/Auto-Renew-HidenCloud`（patchright 反检测 + CF 全套 + sing-box 代理 + TG 通知），平台逻辑按 2026-10-08 AgentScribe 录制 SOP（`optiklink.net/auth → Discord 静默 OAuth → /login 算术题 + Turnstile → 跳 /`）。

> 🧪 保活地址：<https://optiklink.net/auth>（免费用户每 3 天必须登录一次主站，否则离线，7 天删机；本脚本每 2 天跑一次）
>
> ⚠️ 主站只有 Discord OAuth 登录（无账密表单）。本脚本为 **Cookie 优先 + 浏览器重登兜底**：storageState 有效则直接进 `/` 即算保活；失效才走浏览器（算术题自动解，Discord 须静默授权）。

## 目录

- `app.py` — 唯一脚本：Cookie 登录 → 浏览器重登（算术题/Turnstile）→ TG 报告
- `.github/workflows/renew.yml` — CI：cron `12 4 */2 * *`，sing-box 与 eooce 同源
- `time.txt` — 防工作流停更的心跳文件
- 本地仅有（绝不提交）：`agentscribe-*`（录制，含真实 Cookie）、`slim-storage-state.json`、`storage-state*.json`

## Secrets 配置

进入仓库 `Settings` → `Secrets and variables` → `Actions`：

| Secret 名称 | 必填 | 说明 |
|------------|------|------|
| `OPTIKLINK_STATE` | ✅ | storageState 登录态 JSON 全文（须含 `optiklink.net` + `discord.com` 两个域会话；超长可转 base64，脚本自动识别） |
| `OPTIKLINK_ACCOUNT` | ❌ | 账号备注，仅 TG 展示用 |
| `NODE_LINK` | ❌ | 代理链接，不配则直连（与 eooce 同源 `setup_proxy.sh`） |
| `TG_BOT_TOKEN` / `TG_CHAT_ID` | ❌ | Telegram 通知 |

### OPTIKLINK_STATE 获取

1. 本地 Chrome **用同一个 profile 先登 Discord**（discord.com 保持登录态），再访问 `https://optiklink.net/auth` 点登录，完成算术题（人工一次过掉）；
2. 用 Playwright 导出 storageState：`await context.storageState({ path: 'state.json' })`；
3. JSON 全文贴进 Secret（或 `base64` 后贴入）。Cookie 失效时脚本 TG 告警，重抓一次即可。

> ⚠️ 导出时浏览器必须**同时登着 Discord**：storageState 缺 `discord.com` 会话会导致重登轨无法静默授权（脚本会明确告警，不硬闯）。录制导出的 `agentscribe-shield-auth-*.json` 经查只有 optiklink 域 cookie，Cookie 直进有效、重登自愈无效——建议重导一份含 Discord 会话的。

## 登录流程（已录制验证）

```
/auth 点登录 → discord.com/oauth2/authorize（prompt=none 静默）
  → /login：读 p.math-question strong → 计算 → 填 math_answer
  → Turnstile（一般自过，不过则自动点）→ 点 CONTINUE WITH LOGIN
  → POST /login → 跳 optiklink.net/（PHPSESSID 即登录态）
```

若落到 `discord.com/login` 账密页（Discord 会话过期），脚本直接失败 + TG 告警，不硬填账密。

## Telegram 通知示例

```
🎰 OptikLink 保活报告

✅ 保活成功
📧 账号: cookie-account
🌐 保活使用IP: 1.2.3.xx
🕒 保活时间：2026-10-08 12:14:21
```

## 常见问题

1. **保活失败 / Cookie 失效**：按上节重抓 storageState 并更新 Secret。
2. **Too Many Requests**：保持每 2 天一次，别手动频繁触发；可配代理换出口 IP。
3. **与 huanthree 版的区别**：那家用 Discord 账密 + 哈希类名选择器，又脆又易挂；本脚本 Cookie 优先、SOP 稳定选择器（`form.verification-form`），只在登录态失效时进浏览器。

## 免责声明

仅供学习与自动化运维研究，须遵守 OptikLink 服务条款。因使用本脚本导致的账号限制等问题，作者不承担责任。
