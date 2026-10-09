#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# OptikLink 主站保活（eooce 基座版，单账号）
# 骨架复刻 eooce/Auto-Renew-HidenCloud：patchright 反检测 + CF 全套 + sing-box 代理 + TG 通知
# 平台逻辑按 2026-10-08 AgentScribe 录制 SOP（session 764f4445）：
#   /auth 点登录 → Discord 验证（静默回跳 / 点 Authorize）→ /login 算术题 + Turnstile → POST /login → 跳 / Dashboard
# TOS：免费用户每 3 天必须登录一次主站，否则离线，7 天删机。本脚本每 2 天跑一次，登录即保活。
# Secrets：OPTIKLINK_STATE 三选一：storageState 全文/base64（含 discord 会话则重登可自愈），
#          或单 cookie `PHPSESSID=xxx`/裸值（最简，F12 拷一个值就行；主站无 remember_web 机制）必填
#          OPTIKLINK_ACCOUNT（备注，仅 TG 展示）可选，TG_BOT_TOKEN/TG_CHAT_ID 可选，NODE_LINK 可选

import os, re, sys, time, json, base64, random, tempfile, requests
try:
    from patchright.sync_api import sync_playwright
    USING_PATCHRIGHT = True
except ImportError:
    from playwright.sync_api import sync_playwright
    USING_PATCHRIGHT = False

if sys.platform == 'win32':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except Exception:
        pass

# --- 环境变量 ---
OPTIKLINK_STATE   = os.environ.get('OPTIKLINK_STATE') or ""
OPTIKLINK_ACCOUNT = os.environ.get('OPTIKLINK_ACCOUNT') or "cookie-account"
DISCORD_TOKEN     = os.environ.get('DISCORD_TOKEN') or ""  # 可选：第三 fallback，静默 OAuth 失败时换 code
TG_CHAT_ID        = os.environ.get('TG_CHAT_ID') or ""
TG_BOT_TOKEN      = os.environ.get('TG_BOT_TOKEN') or ""

BASE_URL  = "https://optiklink.net"
AUTH_URL  = f"{BASE_URL}/auth"
LOGIN_URL = f"{BASE_URL}/login"

# Discord OAuth（录制实测 client_id；redirect 用 .com，服务端会跳回 .net）
DISCORD_CLIENT_ID    = "933437142254887052"
DISCORD_REDIRECT_URI = "https://optiklink.com/login"
DISCORD_OAUTH_URL    = (
    "https://discord.com/api/v9/oauth2/authorize"
    f"?client_id={DISCORD_CLIENT_ID}&response_type=code"
    "&scope=guilds+guilds.join+identify+email"
    f"&redirect_uri={DISCORD_REDIRECT_URI}"
)

# --- 代理配置（由工作流 setup_proxy.sh 写入 $GITHUB_ENV）---
IS_PROXY         = os.environ.get('IS_PROXY', 'false').lower() == 'true'
PROXY_SERVER     = os.environ.get('PROXY_SERVER') or "http://127.0.0.1:1081"
REQUESTS_PROXIES = {"http": PROXY_SERVER, "https": PROXY_SERVER} if IS_PROXY else None

# Cloudflare 整页挑战 / Turnstile 组件共用的 iframe 选择器
CF_IFRAME_SELECTOR = 'iframe[src*="challenges.cloudflare.com"]'

# SOP 选择器（录制 session 764f4445，Webflow 站点，类名稳定非哈希）
LOGIN_LINK_SELECTOR = 'div.auth_content a.hyperlink_abs.w-inline-block'
MATH_Q_SELECTOR     = 'form.verification-form p.math-question strong'
MATH_INPUT_SELECTOR = 'form.verification-form input[name="math_answer"]'
SUBMIT_SELECTOR     = 'form.verification-form button[type="submit"]'

if sys.platform == 'win32':
    DEFAULT_UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36'
else:
    DEFAULT_UA = 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36'

def log(message):
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {message}", flush=True)

STEALTH_JS = """
try {
    Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
} catch (e) {}
"""

def get_current_ip(proxy_server=None):
    """获取当前出口IP"""
    proxies = {"http": proxy_server, "https": proxy_server} if (proxy_server and IS_PROXY) else None
    try:
        resp = requests.get("https://api.ip.sb/ip", proxies=proxies, timeout=15)
        if resp.status_code == 200:
            return resp.text.strip()
        return "获取失败"
    except Exception as e:
        log(f"❌ 获取出口IP失败: {e}")
        return "获取失败"

def send_telegram_notification(status, current_ip="未知"):
    """发送 Telegram 通知（eooce 风格）"""
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        log("⚠️ Telegram 未配置，跳过通知")
        return False
    local_time = time.gmtime(time.time() + 8 * 3600)
    now = time.strftime("%Y-%m-%d %H:%M:%S", local_time)
    text = (
        f"🎰 OptikLink 保活报告\n\n"
        f"{status}\n"
        f"📧 账号: {OPTIKLINK_ACCOUNT}\n"
        f"🌐 保活使用IP: {current_ip}\n"
        f"🕒 保活时间：{now}"
    )
    url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
    try:
        resp = requests.post(url, json={"chat_id": TG_CHAT_ID, "text": text, "parse_mode": "HTML"},
                             timeout=10, proxies=REQUESTS_PROXIES)
        if resp.status_code == 200:
            log("✅ Telegram 通知发送成功")
            return True
        log(f"❌ Telegram 通知失败: {resp.text}")
        return False
    except Exception as e:
        log(f"❌ Telegram 通知异常: {e}")
        return False

# ================== Cloudflare 全套（eooce 原样复刻） ==================

def _get_turnstile_token(page):
    try:
        return page.evaluate(
            """
            () => {
                try {
                    if (window.turnstile && typeof window.turnstile.getResponse === 'function') {
                        let t = null;
                        try { t = window.turnstile.getResponse(); } catch (e) {}
                        if (t && t.length > 30) return t;
                        for (let i = 0; i < 5; i++) {
                            try { t = window.turnstile.getResponse(String(i)); } catch (e) { t = null; }
                            if (t && t.length > 30) return t;
                        }
                    }
                } catch (e) {}
                const inputs = document.querySelectorAll(
                    'input[name="cf-turnstile-response"], input[id$="_response"]'
                );
                for (const el of inputs) {
                    if (el.value && el.value.length > 30) return el.value;
                }
                return null;
            }
            """
        )
    except Exception:
        return None

def _find_visible_cf_frames(page):
    result = []
    try:
        for handle in page.query_selector_all(CF_IFRAME_SELECTOR):
            try:
                box = handle.bounding_box()
                if box and box['width'] > 10 and box['height'] > 10:
                    result.append(handle)
            except Exception:
                continue
    except Exception:
        pass
    return result

def _cf_checkbox_visible(page):
    for frame in page.frames:
        if 'challenges.cloudflare.com' not in (frame.url or ''):
            continue
        try:
            if frame.locator('input[type="checkbox"]:visible').count() > 0:
                return True
        except Exception:
            continue
    return False

def _click_cf_checkbox(page, frame_el=None):
    for frame in page.frames:
        if 'challenges.cloudflare.com' not in (frame.url or ''):
            continue
        try:
            frame.locator('input[type="checkbox"]').first.click(timeout=2500)
            return True
        except Exception:
            continue
    if frame_el is None:
        return False
    try:
        try:
            frame_el.scroll_into_view_if_needed(timeout=3000)
        except Exception:
            pass
        box = frame_el.bounding_box()
        if not box:
            return False
        x = box['x'] + random.uniform(18, 34)
        y = box['y'] + box['height'] / 2 + random.uniform(-4, 4)
        page.mouse.move(x - random.uniform(30, 60), y + random.uniform(-8, 8))
        time.sleep(random.uniform(0.15, 0.4))
        page.mouse.move(x, y)
        time.sleep(random.uniform(0.05, 0.2))
        page.mouse.down()
        time.sleep(random.uniform(0.04, 0.1))
        page.mouse.up()
        return True
    except Exception:
        return False

SECURITY_TITLE_HINTS = ('security verification', 'just a moment', 'attention required', 'checking your browser', '请稍候', 'please wait', 'one more step')

def _is_security_check_page(page):
    try:
        title = (page.title() or '').lower()
        if any(h in title for h in SECURITY_TITLE_HINTS):
            return True
    except Exception:
        return True
    try:
        return page.evaluate(
            """
            () => {
                const t = document.body ? document.body.innerText.slice(0, 5000).toLowerCase() : '';
                return t.includes('verify you are human') || t.includes('checking your browser')
                    || t.includes('security verification');
            }
            """
        )
    except Exception:
        return True

def _has_interstitial_iframe(page):
    try:
        for handle in page.query_selector_all(CF_IFRAME_SELECTOR):
            if '/turnstile/' not in (handle.get_attribute('src') or ''):
                return True
    except Exception:
        pass
    return False

def handle_cloudflare(page, timeout=240):
    def challenge_active():
        return _has_interstitial_iframe(page) or _is_security_check_page(page)
    if not challenge_active():
        for _ in range(3):
            time.sleep(1)
            if challenge_active():
                break
        else:
            return True
    log("🔒 检测到 Cloudflare 安全验证...")
    time.sleep(5)
    try:
        page.screenshot(path="security_page.png")
    except Exception:
        pass
    effective_timeout = timeout + (180 if timeout > 60 else 0)
    start_time = time.time()
    last_click = 0
    clear_rounds = 0
    while time.time() - start_time < effective_timeout:
        if not challenge_active():
            clear_rounds += 1
            if clear_rounds >= 2:
                return True
            time.sleep(1)
            continue
        clear_rounds = 0
        if time.time() - last_click > 6:
            frames = _find_visible_cf_frames(page)
            if frames:
                log("🖱️ 点击 Turnstile 验证......")
                if _click_cf_checkbox(page, frames[-1]):
                    last_click = time.time()
                    time.sleep(random.uniform(3, 5))
                    continue
            elif _get_turnstile_token(page) and _is_security_check_page(page):
                last_click = time.time()
                time.sleep(random.uniform(2, 4))
                continue
        time.sleep(1)
    log("❌ 验证超时。")
    try:
        page.screenshot(path="security_timeout.png")
    except Exception:
        pass
    return False

def solve_modal_turnstile(page, timeout=90):
    log("🛡️ 开始处理 Turnstile 验证...")
    start = time.time()
    last_click = 0
    clicks = 0
    no_frame_seconds = 0
    while time.time() - start < timeout:
        if _get_turnstile_token(page):
            log("✅ Turnstile 验证通过！")
            return True
        frames = _find_visible_cf_frames(page)
        if not frames:
            no_frame_seconds += 1
            if no_frame_seconds >= 20 and clicks == 0:
                log("ℹ️ 未检测到 Turnstile 验证框，无需验证。")
                return True
            time.sleep(1)
            continue
        no_frame_seconds = 0
        if clicks > 0 and time.time() - last_click > 4 and not _cf_checkbox_visible(page):
            time.sleep(2)
            if not _cf_checkbox_visible(page):
                log("✅ Turnstile 复选框已消失，视为验证通过！")
                return True
        if time.time() - last_click > 6 and _cf_checkbox_visible(page):
            log(f"🖱️ 点击 Turnstile 复选框（第 {clicks + 1} 次）...")
            if _click_cf_checkbox(page, frames[-1]):
                clicks += 1
                last_click = time.time()
                time.sleep(random.uniform(3, 5))
                continue
        if clicks == 0 and time.time() - start > 20 and time.time() - last_click > 6:
            if _click_cf_checkbox(page, frames[-1]):
                clicks += 1
                last_click = time.time()
                time.sleep(random.uniform(3, 5))
                continue
        time.sleep(1)
    if _get_turnstile_token(page):
        log("✅ Turnstile 验证通过！")
        return True
    log("❌ Turnstile 验证超时。")
    return False

# ================== OptikLink 平台逻辑（按录制 SOP） ==================

def _write_state_file():
    """OPTIKLINK_STATE 三种形态归一化后落盘，供 new_context 注入：
    1) storageState JSON / base64（含多域会话，重登轨可自愈）；
    2) 单 cookie：`PHPSESSID=xxx` 或裸值（主站会话即 PHPSESSID，无 remember_web 机制，CF 走浏览器活解）。
    兼容浏览器直接导出的 cookie 形状：expirationDate→expires，非法 sameSite 丢弃，未知键剔除。"""
    raw = (OPTIKLINK_STATE or "").strip().strip('"').strip("'")
    if not raw:
        return None
    try:
        if raw.lstrip().startswith("{"):
            data = raw
        elif len(raw) > 500:
            # 长串非 JSON → 视为 base64 的 storageState
            data = base64.b64decode(raw).decode("utf-8")
        else:
            # 短串 → 单 cookie（PHPSESSID=xxx 或裸值）
            val = raw.split("=", 1)[1].strip() if "=" in raw else raw
            val = val.strip().strip('"').strip("'").split(";")[0].strip()
            if not val:
                log("❌ OPTIKLINK_STATE 单 cookie 值为空")
                return None
            norm = {"cookies": [{
                "name": "PHPSESSID", "value": val, "domain": "optiklink.net", "path": "/",
                "expires": -1, "httpOnly": False, "secure": False, "sameSite": "None",
            }], "origins": []}
            return _dump_state_file(norm)
        state = json.loads(data)
        norm_cookies = []
        for c in state.get("cookies", []) if isinstance(state, dict) else []:
            if not isinstance(c, dict) or not c.get("name"):
                continue
            exp = c.get("expires", c.get("expirationDate", -1))
            try:
                exp = int(exp)
            except Exception:
                exp = -1
            ss = str(c.get("sameSite") or "None")
            ss = {"lax": "Lax", "strict": "Strict", "none": "None"}.get(ss.lower(), "None")
            norm_cookies.append({
                "name": c.get("name"), "value": c.get("value", ""),
                "domain": c.get("domain"), "path": c.get("path", "/"),
                "expires": exp, "httpOnly": bool(c.get("httpOnly", False)),
                "secure": bool(c.get("secure", False)), "sameSite": ss,
            })
        norm_cookies = [c for c in norm_cookies if c.get("domain")]
        if not norm_cookies:
            log("❌ OPTIKLINK_STATE 里没有可用 cookie")
            return None
        return _dump_state_file({"cookies": norm_cookies,
                                 "origins": state.get("origins", []) if isinstance(state, dict) else []})
    except Exception as e:
        log(f"❌ OPTIKLINK_STATE 解析失败: {e}")
        return None


def _dump_state_file(norm):
    fd, path = tempfile.mkstemp(suffix=".json", prefix="optiklink-state-")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(norm, f)
    log(f"📇 登录态已载入 {len(norm['cookies'])} 个 cookie")
    return path

def open_browser(p, state_path=None):
    """启动浏览器（eooce 原样，storageState 注入登录态）"""
    proxy_arg = {"server": PROXY_SERVER} if IS_PROXY else None
    if USING_PATCHRIGHT:
        browser = p.chromium.launch(channel="chrome", headless=False,
                                    args=['--disable-infobars'], proxy=proxy_arg)
        ctx = browser.new_context(viewport=None, proxy=proxy_arg, storage_state=state_path)
        return browser, ctx.new_page()
    log("⚠️ 未安装 patchright（建议 pip install patchright），退回原生 playwright，过 Cloudflare 能力较弱")
    browser = p.chromium.launch(channel="chrome", headless=False,
                                args=['--no-sandbox', '--disable-blink-features=AutomationControlled', '--disable-infobars'])
    ctx = browser.new_context(viewport={'width': 1920, 'height': 1080}, user_agent=DEFAULT_UA,
                              proxy=proxy_arg, storage_state=state_path)
    page = ctx.new_page()
    page.add_init_script(STEALTH_JS)
    return browser, page

def _dashboard_body(page):
    """取当前页正文（登录判定与用户名提取共用）"""
    try:
        return page.locator("body").inner_text(timeout=8000) or ""
    except Exception:
        return ""

def _is_logged_in(page):
    """阳性判定（录制实测 grounding）：
    networkEvents[63] GET / 200 响应体即登录态 Dashboard，含 "to your Dashboard"（Welcome 行）、
    "Sincerely provided by"（页脚）、"Logout"/"Login to Panel"（侧栏）；
    匿名 /home 对四者全是 0 命中。要求 A 组 × B 组各中其一，零误报。
    匿名访问 / 会 302 到 /home，纯 URL 判定一律视为未登录。"""
    try:
        url = page.url or ''
        if 'optiklink.net' not in url and 'optiklink.com' not in url:
            return False
        if '/login' in url or '/auth' in url or _is_security_check_page(page):
            return False
        body = _dashboard_body(page)
        return (("to your Dashboard" in body or "Sincerely provided by" in body)
                and ("Logout" in body or "Login to Panel" in body))
    except Exception:
        return False

def _get_dashboard_user(page):
    """右上角 Discord 用户名（如 lds924494#0），用于 TG 实名展示；拿不到返回空"""
    try:
        m = re.search(r'([A-Za-z0-9_.]{2,32}#\d+)', _dashboard_body(page))
        return m.group(1) if m else ""
    except Exception:
        return ""

def _dismiss_ads(page):
    """关广告遮罩 + Google Funding Choices 同意弹窗（SOP Step 1；后者会盖住登录链接，不阻塞）"""
    for sel in ('#ad_position_box', '[aria-label="Close ad"]', '.close-ad'):
        try:
            el = page.locator(sel).first
            if el.count() > 0 and el.is_visible():
                el.click(timeout=2000)
                log("🖱️ 已关闭广告遮罩")
                break
        except Exception:
            continue
    for text in ("Consent", "同意", "Accept all", "全部接受", "Accept", "接受", "Allow all"):
        try:
            btn = page.locator(f'button:has-text("{text}"):visible').first
            if btn.count() > 0:
                btn.click(timeout=3000)
                log(f"🖱️ 已关闭同意弹窗（{text}）")
                time.sleep(1)
                return
        except Exception:
            continue

def solve_math_question(page):
    """解 /login 页算术题：读 p.math-question strong → 计算 → 填 math_answer"""
    try:
        q_el = page.locator(MATH_Q_SELECTOR).first
        q_el.wait_for(state="visible", timeout=60000)
        q_text = (q_el.inner_text() or "").strip()
        log(f"🧮 题目: {q_text}")
        m = re.search(r'(\d+)\s*([+\-×*/xX÷])\s*(\d+)', q_text)
        if not m:
            log(f"❌ 无法解析题目: {q_text}")
            return False
        a, op, b = int(m.group(1)), m.group(2), int(m.group(3))
        if op in ("/", "÷") and b != 0 and a % b != 0:
            ans = round(a / b, 2)  # 非整除兜底
        else:
            ans = {"+": a + b, "-": a - b, "*": a * b, "x": a * b, "X": a * b,
                   "×": a * b, "/": a // b if b else 0, "÷": a // b if b else 0}[op]
        log(f"🧮 计算: {a} {op} {b} = {ans}")
        page.locator(MATH_INPUT_SELECTOR).first.fill(str(ans), timeout=10000)
        time.sleep(1)
        return True
    except Exception as e:
        log(f"❌ 算术题处理失败: {e}")
        return False

def _discord_oauth_authorize(page, timeout=120):
    """Discord 授权页处理（正常流程第 2 步）：
    已授权过的会话走 prompt=none 静默回跳；没点过授权的会话停在授权页，需滚动条款到底并点击 Authorize。
    代理链路慢，各步都等足。"""
    try:
        page.wait_for_url(re.compile(r'optiklink\.(net|com)/login'), timeout=45000)
        log("✅ Discord 已静默授权，无需手动点击")
        return True
    except Exception:
        pass
    if '/oauth2/' not in (page.url or ''):
        return False
    log("🔘 Discord 授权页，尝试点击 Authorize...")
    try:
        page.evaluate("""() => {
            const s = document.querySelector('[class*=scroller]') || document.querySelector('[class*=scrollerBase]');
            if (s) s.scrollTop = s.scrollHeight;
            window.scrollTo(0, document.body.scrollHeight);
        }""")
        time.sleep(1)
    except Exception:
        pass
    for sel in ('button:has-text("Authorize")', 'button:has-text("授权")', 'button[type="submit"]'):
        try:
            btn = page.locator(sel).first
            if btn.count() > 0 and btn.is_visible():
                btn.click(timeout=8000)
                log(f"✅ 已点击 Discord 授权按钮（{sel}）")
                break
        except Exception:
            continue
    try:
        page.wait_for_url(re.compile(r'optiklink\.(net|com)/login'), timeout=timeout)
        log("✅ Discord 授权完成，已回跳验证页")
        return True
    except Exception:
        log("❌ Discord 授权后未回跳")
        return False

def discord_token_login(page):
    """第三 fallback：DISCORD_TOKEN 调 Discord OAuth 授权接口换 code，跟回调把 optiklink 会话写入 context。
    思路来自废仓 v1（纯 HTTP 版），此处用 Playwright request（与浏览器同 cookie  jar）。
    注意：token 等同密码，日志绝不打印全文；Discord 可能因风控/2FA 拒绝，失败即回 False。"""
    if not DISCORD_TOKEN:
        log("❌ 未配置 DISCORD_TOKEN，无法重登（请更新 OPTIKLINK_STATE 或配置 token）")
        return False
    log("🔄 正在通过 Discord Token 执行 OAuth 重登...")
    try:
        api = page.context.request
        r = api.post(
            DISCORD_OAUTH_URL,
            headers={"Authorization": DISCORD_TOKEN, "Content-Type": "application/json",
                      "User-Agent": DEFAULT_UA},
            data={"permissions": "0", "authorize": True},
            timeout=25000,
        )
        if r.status != 200:
            log(f"❌ Discord 授权失败: HTTP {r.status}（token 失效/被风控/需人工验证）")
            return False
        try:
            loc = r.json().get("location")
        except Exception:
            loc = None
        if not loc:
            log("❌ Discord 未返回回调 location")
            return False
        url = loc
        for _ in range(10):
            try:
                g = api.get(url, headers={"User-Agent": DEFAULT_UA}, timeout=25000)
            except Exception as e:
                log(f"❌ 回调跳转异常: {e}")
                return False
            nxt = g.headers.get("location")
            if g.status in (301, 302, 303, 307, 308) and nxt:
                url = nxt
                continue
            break
        log("✅ Discord Token 回调完成，会话已写入 context")
        return True
    except Exception as e:
        log(f"❌ Discord Token 重登异常: {e}")
        return False

def _do_verification(page):
    """验证页尾巴（各路复用）：等题目 → 算术 → Turnstile → 提交 → 轮询 Dashboard"""
    if '/login' not in (page.url or ''):
        # 回调跳转经代理可能慢，给 60s 再判；可能已直接登录成功
        try:
            page.wait_for_url(re.compile(r'optiklink\.(net|com)/login'), timeout=60000)
        except Exception:
            pass
        if '/login' not in (page.url or ''):
            if _is_logged_in(page):
                log("✅ 已直接登录成功")
                return True
            log(f"❌ 未到达验证页，当前: {page.url}")
            return False
    log(f"✅ 到达验证页: {page.url}")

    if not solve_math_question(page):
        page.screenshot(path="math_fail.png")
        return False

    if not solve_modal_turnstile(page, timeout=90):
        log("⚠️ Turnstile 未确认通过，仍将尝试提交...")

    log("🖱️ 点击 CONTINUE WITH LOGIN...")
    page.locator(SUBMIT_SELECTOR).first.click(timeout=10000)
    try:
        page.wait_for_url(re.compile(r'optiklink\.(net|com)/(#.*)?$'), timeout=60000)
    except Exception:
        pass
    # 提交后会话落定可能滞后，轮询标记最长 60s 再判
    logged = False
    for _ in range(20):
        time.sleep(3)
        if _is_logged_in(page):
            logged = True
            break
    if logged:
        log("✅ 登录成功！")
        return True
    log(f"❌ 提交后未登录，当前: {page.url}")
    page.screenshot(path="login_fail.png")
    return False

def login(page):
    # 1. Cookie 登录尝试（storageState 已在 context 注入）
    log("📇 尝试 Cookie 登录...")
    try:
        page.goto(f"{BASE_URL}/", wait_until="domcontentloaded", timeout=60000)
        handle_cloudflare(page, timeout=120)
        time.sleep(2)
        if _is_logged_in(page):
            log("✅ Cookie 登录成功，dashboard 保活完成")
            return True
        log("⚠️ Cookie 未直接登录（可能过期），转浏览器重登...")
    except Exception as e:
        log(f"⚠️ Cookie 登录尝试异常: {e}")

    # 2. Token 直换（有 token 时优先：纯 API 换 code，不点页面、不碰 discord.com 页面）
    if DISCORD_TOKEN:
        log("🎫 有 DISCORD_TOKEN，走纯 API 换 code（跳过页面点击）...")
        if discord_token_login(page):
            try:
                page.goto(f"{BASE_URL}/", wait_until="domcontentloaded", timeout=60000)
                time.sleep(3)
            except Exception as e:
                log(f"⚠️ 回跳后访问异常: {e}")
            if _is_logged_in(page):
                log("✅ Token 换会话后已直接登录")
                return True
            try:
                page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=60000)
            except Exception as e:
                log(f"⚠️ 打开验证页异常: {e}")
                return False
            return _do_verification(page)
        log("⚠️ Token 换 code 失败，转页面点击流程...")

    # 3. 页面点击流程：/auth → Discord 验证（静默/点 Authorize）→ 验证页
    try:
        log("🔑 打开 OptikLink 登录页...")
        page.goto(AUTH_URL, wait_until="domcontentloaded", timeout=60000)
        handle_cloudflare(page, timeout=240)
        time.sleep(2)
        _dismiss_ads(page)

        log("📤 点击登录（Sign in with Discord）...")
        clicked = False
        for attempt in range(4):
            try:
                _dismiss_ads(page)
                link = page.locator(LOGIN_LINK_SELECTOR).first
                link.wait_for(state="visible", timeout=10000)
                try:
                    link.click(timeout=6000, no_wait_after=True)
                except Exception:
                    link.click(timeout=6000, force=True, no_wait_after=True)
                clicked = True
                break
            except Exception as e:
                log(f"⚠️ 第 {attempt + 1} 次点击未完成，重试...（{str(e)[:100]}）")
                time.sleep(2)
        if not clicked:
            log("❌ 多次点击登录链接失败（广告跳转干扰）")
            page.screenshot(path="login_fail.png")
            return False
        # 点击后等跳转：盯主页面 URL + 新弹出的 popup（站点的 Discord 跳转可能开新页）
        # 广告页可能吞点击/跳广告页再回来；代理链路慢，等足 90s
        log("⏳ 等待跳转（最长 90s）...")
        jumped = False
        for _ in range(30):
            time.sleep(3)
            u = page.url or ''
            if '/login' in u or 'discord.com' in u or '/auth' not in u:
                jumped = True
                break
            try:
                if len(page.context.pages) > 1:
                    for p in page.context.pages:
                        if p != page and ('discord.com' in (p.url or '') or 'login' in (p.url or '')):
                            log(f"📑 检测到弹出页：{p.url}，切换过去")
                            page = p
                            jumped = True
                            break
                    if jumped:
                        break
            except Exception:
                pass
        if not jumped:
            log("❌ 点击后未跳转（仍在 /auth），点击可能被广告吞掉")
            page.screenshot(path="login_fail.png")
            return False

        # 正常流程第 2 步：Discord 验证（静默回跳 → 点 Authorize → 账密页走 token）
        if 'discord.com' in (page.url or ''):
            authed = False
            if '/oauth2/' in (page.url or ''):
                authed = _discord_oauth_authorize(page)
            if not authed:
                log("⚠️ Discord 未完成授权，尝试 Token 重登...")
                try:
                    page.screenshot(path="discord_login_required.png")
                except Exception:
                    pass
                if not discord_token_login(page):
                    return False
                page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=60000)
                # 回调落地 + 验证页渲染经代理可能很慢，等题目出现
                try:
                    page.locator(MATH_Q_SELECTOR).first.wait_for(state="visible", timeout=60000)
                except Exception:
                    pass
                time.sleep(2)
        if '/login' not in (page.url or ''):
            # 回调跳转经代理可能慢，给 60s 再判；可能已直接登录成功
            try:
                page.wait_for_url(re.compile(r'optiklink\.(net|com)/login'), timeout=60000)
            except Exception:
                pass
        return _do_verification(page)
    except Exception as e:
        log(f"❌ 登录异常: {e}")
        try:
            page.screenshot(path="login_fail.png")
        except Exception:
            pass
        return False

def main():
    log(f"🔍 凭证检测: OPTIKLINK_STATE={'已配置' if OPTIKLINK_STATE else '未配置'}, "
        f"DISCORD_TOKEN={'已配置' if DISCORD_TOKEN else '未配置'}")
    if not OPTIKLINK_STATE and not DISCORD_TOKEN:
        log("❌ 缺少登录凭证（OPTIKLINK_STATE / DISCORD_TOKEN 至少配一个）")
        sys.exit(1)

    state_path = _write_state_file() if OPTIKLINK_STATE else None
    if OPTIKLINK_STATE and not state_path:
        sys.exit(1)

    with sync_playwright() as p:
        browser = None
        try:
            log("🌐 直连模式（未使用代理）" if not IS_PROXY else f"⚙️ 代理已启用: {PROXY_SERVER}")
            current_ip = get_current_ip(PROXY_SERVER)
            log(f"🎯 当前出口IP: {current_ip}")

            log("🚀 启动反检测内核浏览器...")
            browser, page = open_browser(p, state_path)

            ok = login(page)
            try:
                page.screenshot(path="dashboard.png")
            except Exception:
                pass
            who = _get_dashboard_user(page) if ok else ""
            if ok:
                status = f"✅ 保活成功（Dashboard：{who or OPTIKLINK_ACCOUNT}）"
            else:
                status = "❌ 保活失败（未检测到 Dashboard，请更新 OPTIKLINK_STATE）"
            log(status)
            send_telegram_notification(status, current_ip)
            sys.exit(0 if ok else 1)
        except Exception as e:
            log(f"❌ 运行异常: {e}")
            send_telegram_notification(f"❌ 脚本异常: {e}")
            sys.exit(1)
        finally:
            if browser:
                try:
                    browser.close()
                except Exception:
                    pass
            try:
                os.remove(state_path)
            except Exception:
                pass

if __name__ == "__main__":
    main()
