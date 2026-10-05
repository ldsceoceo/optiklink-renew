#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import time
import re
import json
import base64
import platform
import logging
import requests
from datetime import datetime
from pathlib import Path
from typing import Optional

from seleniumbase import SB
from seleniumbase.common.exceptions import TimeoutException

# ================== 配置（Optiklink 适配：Discord OAuth，只有 Cookie 保活版） ==================
# TOS: 免费用户每 3 天必须登录一次 https://optiklink.com/auth，否则服务器离线，7 天删除。
# optiklink.com/auth 只有 "Sign in with DISCORD" 按钮（跳转 /login → discord OAuth），无账密表单。
# 保活动作 = 带有效 Cookie 访问 dashboard 即算 activity，无需点击 Renew 按钮。
OPTIKLINK_AUTH_URL = "https://optiklink.com/auth"
OPTIKLINK_LOGIN_URL = "https://optiklink.com/login"
DASHBOARD_CANDIDATES = [
    "https://optiklink.com/auth",
    "https://optiklink.com/dashboard",
    "https://optiklink.com/home",
]
OUTPUT_DIR = Path("output/screenshots")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("optiklink-keep")


# ================== 辅助函数 ==================
def is_linux() -> bool:
    return platform.system().lower() == "linux"


def mask_url(url: str) -> str:
    return re.sub(r'/servers/\d+', '/servers/***', url)


def mask_email(email: str) -> str:
    if '@' not in email:
        return email[:1] + "***"
    local, domain = email.split('@', 1)
    masked_local = local[:1] + "***" if local else "***"
    if '.' in domain:
        parts = domain.split('.')
        tld = parts[-1]
        first_char = domain[0]
        masked_domain = f"{first_char}***.{tld}" if len(parts) > 1 else f"{first_char}***"
    else:
        masked_domain = domain[:1] + "***"
    return f"{masked_local}@{masked_domain}"


def mask_server_id(server_id: str) -> str:
    if not server_id or server_id == "Unknown":
        return server_id
    if len(server_id) <= 2:
        return server_id + "***"
    return server_id[:2] + "***"


def setup_display():
    if is_linux() and not os.environ.get("DISPLAY"):
        try:
            from pyvirtualdisplay import Display
            display = Display(visible=False, size=(1920, 1080))
            display.start()
            os.environ["DISPLAY"] = display.new_display_var
            logger.info("虚拟显示已启动")
            return display
        except Exception as e:
            logger.error(f"虚拟显示启动失败: {e}")
            sys.exit(1)
    return None


def screenshot_path(name: str) -> str:
    return str(OUTPUT_DIR / f"{datetime.now().strftime('%H%M%S')}-{name}.png")


def safe_screenshot(sb, path: str):
    try:
        sb.save_screenshot(path)
        logger.info(f"📸 截图 → {Path(path).name}")
    except Exception as e:
        logger.warning(f"截图失败: {e}")


def notify_telegram(email: str, ok: bool, msg: str = "", screenshot_file: str = None):
    try:
        token = os.environ.get("TELEGRAM_BOT_TOKEN")
        chat_id = os.environ.get("TELEGRAM_CHAT_ID")
        if not token or not chat_id:
            return

        status = "✅ 保活成功" if ok else "❌ 保活失败"
        lines = [status, "", f"账号：{email}"]
        if msg:
            lines.append(f"信息：{msg}")
        lines.append(f"时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        lines.append("")
        lines.append("OptikLink Auto Keep Alive")
        text = "\n".join(lines)

        if screenshot_file and Path(screenshot_file).exists():
            with open(screenshot_file, "rb") as f:
                requests.post(
                    f"https://api.telegram.org/bot{token}/sendPhoto",
                    data={"chat_id": chat_id, "caption": text},
                    files={"photo": f},
                    timeout=60
                )
        else:
            requests.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat_id, "text": text, "disable_web_page_preview": True},
                timeout=30
            )
    except Exception as e:
        logger.warning(f"Telegram 通知失败: {e}")


def check_and_exit_on_rate_limit(sb, email: str) -> None:
    try:
        page_source = sb.get_page_source()
        if "Too Many Requests" in page_source or "20 per 1 hour" in page_source:
            logger.error("⛔ 检测到速率限制: Too Many Requests / 20 per 1 hour")
            sp = screenshot_path("rate-limit")
            safe_screenshot(sb, sp)

            notify_telegram(
                email=email,
                ok=False,
                msg="IP 已被限制（Too Many Requests, 20/h），脚本已停止",
                screenshot_file=sp
            )
            sys.exit(1)
    except Exception:
        pass


def load_storage_state() -> Optional[dict]:
    """Cookie 保活版：从 OPTIKLINK_STATE 读 storageState（Playwright JSON），本地回退读文件。"""
    raw = os.environ.get("OPTIKLINK_STATE", "").strip()
    if not raw:
        for name in ("slim-storage-state.json", "storage-state.json", "storage-state-optiklink.json"):
            p = Path(name)
            if p.exists():
                try:
                    logger.info(f"从本地文件加载登录态: {name}")
                    return json.loads(p.read_text(encoding="utf-8"))
                except Exception as e:
                    logger.warning(f"本地登录态解析失败 {name}: {e}")
        logger.error("未设置环境变量 OPTIKLINK_STATE（storageState JSON），且本地无 slim-storage-state.json")
        logger.error("获取方法：本地手动 Discord 登录一次 optiklink.com/auth → 导出 storageState → 填入 Secret")
        sys.exit(1)
    # 兼容 base64 存放（Secret 过大时）
    text = raw
    if text and len(text) > 100 and re.fullmatch(r"[A-Za-z0-9+/=\s]+", text) and "{" not in text:
        try:
            text = base64.b64decode(text).decode("utf-8")
        except Exception:
            pass
    try:
        state = json.loads(text)
        n = len(state.get("cookies", [])) if isinstance(state, dict) else 0
        logger.info(f"读取到登录态 Cookie 数: {n}")
        return state
    except Exception as e:
        logger.error(f"解析 OPTIKLINK_STATE 失败: {e}")
        sys.exit(1)


def inject_storage_state(sb, state: dict) -> int:
    """把 Playwright storageState 的 cookies 注入 Selenium。返回注入数量。"""
    cookies = state.get("cookies", []) if isinstance(state, dict) else []
    count = 0
    for c in cookies:
        try:
            cookie = {"name": c.get("name"), "value": c.get("value")}
            for k in ("path", "domain", "secure", "httpOnly", "expiry", "expires"):
                if k in c and c[k] is not None:
                    cookie["httpOnly" if k == "httpOnly" else k] = c[k]
            # expiry 归一化
            if "expires" in cookie and "expiry" not in cookie:
                try:
                    cookie["expiry"] = int(cookie.pop("expires"))
                except Exception:
                    cookie.pop("expires", None)
            if isinstance(cookie.get("domain"), str) and cookie["domain"].startswith("."):
                cookie["domain"] = cookie["domain"][1:]
            sb.driver.add_cookie(cookie)
            count += 1
        except Exception:
            continue
    logger.info(f"注入 Cookie: {count}/{len(cookies)}")
    return count


def is_logged_in(sb) -> bool:
    """判断是否已登录 Optiklink dashboard（Cookie 有效）。"""
    try:
        url = sb.get_current_url() or ""
        body = sb.execute_script("return document.body ? document.body.innerText : '';") or ""
        body_low = body.lower()
        # 未登录标志：仍在劝 Discord 登录
        if "please sign in with discord" in body_low and "logout" not in body_low:
            return False
        if "sign in with" in body and "discord" in body_low and ("login" in url or "auth" in url):
            # 可能是登录页，但已登录后 dashboard 也可能残留字样，用 URL + 登录态二次确认
            pass
        good_markers = ["logout", "log out", "dashboard", "my server", "my servers", "account", "profile", "premium"]
        if any(m in body_low for m in good_markers):
            return True
        if any(k in url for k in ("/dashboard", "/panel", "/account", "/servers", "/home")) and "login" not in url and "/auth" not in url:
            return True
        # auth 页登录后 URL 不变但 LOGIN 按钮消失、出现用户头像/菜单
        login_btn_gone = sb.execute_script('''
            var t = (document.body && document.body.innerText) || '';
            if (/Sign in with/i.test(t) && /DISCORD/i.test(t)) return false;
            return true;
        ''')
        if login_btn_gone and len(body) > 500:
            return True
        return False
    except Exception:
        return False


# ================== Cloudflare 处理 ==================
def is_cloudflare_interstitial(sb) -> bool:
    try:
        # Optiklink 登录态有效时直接就是 dashboard 内容，不判 CF
        if is_logged_in(sb):
            return False

        page_source = sb.get_page_source()
        title = sb.get_title().lower() if sb.get_title() else ""

        strong_indicators = [
            "Just a moment",
            "Verify you are human",
            "Checking your browser",
            "Checking if the site connection is secure",
        ]
        for indicator in strong_indicators:
            if indicator in page_source:
                return True

        if "just a moment" in title or "attention required" in title:
            return True

        body_text_len = sb.execute_script('''
            return (document.body && document.body.innerText)
                ? document.body.innerText.trim().length : 0;
        ''')
        if body_text_len < 100 and "challenges.cloudflare.com" in page_source:
            return True

        return False
    except:
        return False


def bypass_cloudflare_interstitial(sb, email: str, max_attempts: int = 3) -> bool:
    logger.info("检测到 Cloudflare 整页挑战，尝试绕过...")
    for attempt in range(max_attempts):
        logger.info(f"CF 绕过尝试 {attempt + 1}/{max_attempts}")
        try:
            sb.uc_gui_click_captcha()
            time.sleep(6)
            check_and_exit_on_rate_limit(sb, email)
            if not is_cloudflare_interstitial(sb):
                logger.info("✅ Cloudflare 挑战已通过")
                return True
        except Exception as e:
            logger.warning(f"CF 绕过尝试 {attempt + 1} 失败: {e}")
        time.sleep(3)

    logger.info("尝试刷新页面重新加载...")
    try:
        sb.uc_open_with_reconnect(OPTIKLINK_AUTH_URL, reconnect_time=10)
        time.sleep(5)
        check_and_exit_on_rate_limit(sb, email)
        if not is_cloudflare_interstitial(sb):
            logger.info("✅ 刷新后 Cloudflare 挑战已消失")
            return True
    except:
        pass
    return False


def wait_for_turnstile_success(sb, timeout: int = 30) -> bool:
    logger.info("等待 Turnstile 验证...")
    start = time.time()
    while time.time() - start < timeout:
        try:
            success = sb.execute_script('''
                var resp = document.querySelector('input[name="cf-turnstile-response"]');
                if (resp && resp.value && resp.value.length > 20) return true;
                var grecap = document.querySelector('textarea[name="g-recaptcha-response"]');
                if (grecap && grecap.value && grecap.value.length > 20) return true;
                var iframe = document.querySelector('iframe[src*="challenges.cloudflare.com"]');
                if (iframe && iframe.getAttribute("data-state") === "solved") return true;
                return false;
            ''')
            if success:
                logger.info("✅ Turnstile 验证成功")
                return True
        except:
            pass
        time.sleep(1)
    logger.warning("⏰ Turnstile 验证超时")
    return False


# ================== 登录流程（Cookie 保活版，不做 Discord 账密自动登录） ==================
def handle_initial_page(sb, email: str, state: dict) -> Optional[str]:
    """Cookie 注入 + 访问 dashboard。返回 already_logged / None（Cookie 失效）。"""
    logger.info("访问 Optiklink（建立 Cookie 域）...")
    sb.uc_open_with_reconnect(OPTIKLINK_AUTH_URL, reconnect_time=8)
    time.sleep(3)
    check_and_exit_on_rate_limit(sb, email)

    try:
        sb.delete_all_cookies()
    except Exception:
        pass
    injected = inject_storage_state(sb, state)

    logger.info("Cookie 注入后刷新 dashboard（保活动作）...")
    sb.uc_open_with_reconnect(OPTIKLINK_AUTH_URL, reconnect_time=8)
    time.sleep(5)
    check_and_exit_on_rate_limit(sb, email)

    sp = screenshot_path("01-auth-with-cookie")
    safe_screenshot(sb, sp)
    logger.info(f"当前URL: {mask_url(sb.get_current_url() or '')}")

    if is_cloudflare_interstitial(sb):
        logger.info("检测到 Cloudflare 整页挑战")
        if not bypass_cloudflare_interstitial(sb, email):
            sp = screenshot_path("02-cf-failed")
            safe_screenshot(sb, sp)
            return None
        time.sleep(3)
        sb.uc_open_with_reconnect(OPTIKLINK_AUTH_URL, reconnect_time=8)
        time.sleep(5)

    if is_logged_in(sb):
        logger.info(f"✅ Cookie 有效（注入 {injected} 个），已登录 dashboard")
        return "already_logged"

    # 轮询一次 dashboard 候选页，避免单页误判
    for url in DASHBOARD_CANDIDATES[1:]:
        try:
            sb.uc_open_with_reconnect(url, reconnect_time=8)
            time.sleep(4)
            if is_logged_in(sb):
                logger.info(f"✅ 在 {url} 确认已登录")
                return "already_logged"
        except Exception:
            continue

    sp = screenshot_path("02-cookie-expired")
    safe_screenshot(sb, sp)
    logger.error("Cookie 失效：仍停留在 Discord 登录页，请本地重新 Discord 登录后更新 OPTIKLINK_STATE")
    return None


def _extract_server_id(url: str) -> str:
    match = re.search(r'/servers/(\d+)', url)
    return match.group(1) if match else "dashboard"


def navigate_to_server(sb, email: str) -> tuple[bool, str, Optional[str]]:
    """Optiklink 保活 = 已登录状态下访问一次 dashboard（TOS 只要求 login activity）。"""
    time.sleep(2)
    sp_dash = screenshot_path("06-dashboard")
    safe_screenshot(sb, sp_dash)
    check_and_exit_on_rate_limit(sb, email)
    current_url = sb.get_current_url() or ""
    logger.info(f"仪表盘URL: {mask_url(current_url)}")
    if is_logged_in(sb):
        logger.info("✅ 保活成功：dashboard 访问有效（满足 3 天登录要求）")
        return True, _extract_server_id(current_url), sp_dash
    logger.error("保活失败：登录态丢失")
    return False, "COOKIE_EXPIRED", sp_dash


# ================== 主保活函数 ==================
def betadash_login(
    email: str,
    state: dict,
    proxy: Optional[str] = None,
    max_retries: int = 1,
) -> dict:
    """Cookie 保活：只跑一次，不重试，避免触发风控。"""
    result = {"success": False, "message": "", "screenshot": None}

    logger.info("=" * 50)
    logger.info(f"OptikLink 保活 | 账号: {mask_email(email)}")
    logger.info("=" * 50)

    for attempt in range(max_retries):
        if attempt > 0:
            logger.warning("已进行过一次尝试，不再重试")
            break

        try:
            sb_kwargs = dict(
                uc=True,
                test=True,
                locale="en",
                headed=not is_linux(),
                user_data_dir=None,
                chromium_arg="--disable-blink-features=AutomationControlled",
            )
            if proxy:
                sb_kwargs["proxy"] = proxy

            with SB(**sb_kwargs) as sb:
                init_status = handle_initial_page(sb, email, state)
                if init_status is None:
                    result["message"] = "Cookie 失效或 CF 绕过失败，请更新 OPTIKLINK_STATE"
                    result["screenshot"] = screenshot_path("02-cookie-expired")
                    logger.warning(result["message"])
                    break

                nav_ok, server_id, screenshot = navigate_to_server(sb, email)

                if nav_ok:
                    result.update(
                        success=True,
                        message=f"dashboard 保活成功 ({server_id})",
                        screenshot=screenshot,
                    )
                    logger.info("✅ 保活成功！")
                    return result
                else:
                    result.update(
                        message=f"保活失败: {server_id}",
                        screenshot=screenshot,
                    )
                    logger.error(f"保活失败 (code={server_id})，不再重试")
                    return result

        except Exception as e:
            logger.exception(f"执行异常: {e}")
            result["message"] = str(e)

    return result


# 兼容旧函数名（workflow/外部调用仍可用）
optiklink_keepalive = betadash_login


# ================== 主程序 ==================
def main():
    state = load_storage_state()
    email = os.environ.get("OPTIKLINK_ACCOUNT", "cookie-account").strip() or "cookie-account"
    proxy = os.environ.get("PROXY_SERVER")
    display = setup_display()

    try:
        result = betadash_login(email, state, proxy, max_retries=1)

        notify_telegram(
            email=email,
            ok=result["success"],
            msg=result["message"],
            screenshot_file=result["screenshot"],
        )

        if result["success"]:
            logger.info("✅ 保活流程完成")
            sys.exit(0)
        else:
            logger.error("❌ 保活失败")
            sys.exit(1)

    finally:
        if display:
            display.stop()


if __name__ == "__main__":
    main()
