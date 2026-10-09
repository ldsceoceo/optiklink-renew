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
| `OPTIKLINK_STATE` | 二选一 | 三选一：storageState JSON 全文 / base64（含 discord 会话则重登可自愈），或**单 cookie** `PHPSESSID=xxx`/裸值（最简） |
| `DISCORD_TOKEN` | 二选一 | 第三 fallback：Cookie+静默全挂时换 OAuth code（**未实跑验证**，且有封号风险，尽量别用） |
| `OPTIKLINK_ACCOUNT` | ❌ | 账号备注，仅 TG 展示用 |
| `NODE_LINK` | ❌ | 代理链接，不配则直连（与 eooce 同源 `setup_proxy.sh`） |
| `TG_BOT_TOKEN` / `TG_CHAT_ID` | ❌ | Telegram 通知 |

### OPTIKLINK_STATE 获取（推荐单 cookie，最简）

主站会话就是 `PHPSESSID`（无 remember_web 机制，CF 走浏览器活解，不靠拷 clearance）：

1. 本地 Chrome 登录状态下按 F12 → Application → Cookies → `https://optiklink.net` → 复制 `PHPSESSID` 的值；
2. Secret 填 `PHPSESSID=xxx` 或裸值，二选一贴进去就行。

> 需要重登自愈（Cookie 过期后浏览器自动走 Discord 静默重登）才用 storageState 全文：导出时浏览器 profile 须**同时登着 Discord**（storageState 须含 `discord.com` 会话），超长可 base64。否则缺 discord 会话时重登轨只会明确告警，不硬闯。

## 登录流程（已录制验证）

```
/auth 点登录 → Discord 验证（已授权则 prompt=none 静默回跳，否则滚动条款点 Authorize）
  → /login：读 p.math-question strong → 计算 → 填 math_answer
  → Turnstile（一般自过，不过则自动点）→ 点 CONTINUE WITH LOGIN
  → POST /login → 跳 optiklink.net/ Dashboard（Welcome…to your Dashboard + Sincerely + Logout 即成功）
```

若卡在 Discord 授权/账密页，先试 Token 重登（配了 `DISCORD_TOKEN` 的话），仍失败则 TG 告警，不硬填账密。

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
