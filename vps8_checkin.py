#!/usr/bin/env python3
"""VPS8 auto-signin with AI captcha"""
import os, sys, time, json, re
from datetime import datetime
from pathlib import Path
from io import BytesIO

import requests
import base64
from PIL import Image
from seleniumbase import SB
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

BASE_URL    = os.environ.get("VPS8_BASE_URL", "https://vps8.zz.cd")
LOGIN_URL   = BASE_URL + "/login"
SIGNIN_URL  = BASE_URL + "/points/signin"

AI_API_KEY    = os.environ.get("AI_API_KEY", "")
AI_BASE_URL   = os.environ.get("AI_BASE_URL", "https://api.openai.com/v1")
AI_MODEL_NAME = os.environ.get("AI_MODEL_NAME", "gpt-4o")
VPS8_EMAIL    = os.environ.get("VPS8_EMAIL", "")
VPS8_PASSWORD = os.environ.get("VPS8_PASSWORD", "")
VPS8_COOKIES  = (os.environ.get("VPS8_COOKIES", "") or "").strip().replace("\r", "").replace("\n", "")
VPS8_API_KEY  = (os.environ.get("VPS8_API_KEY", "") or "").strip()
MY_CHAT_ID    = os.environ.get("MY_CHAT_ID", "")
TG_TOKEN      = os.environ.get("TELEGRAM_BOT_TOKEN", "")

OUT = Path(os.environ.get("GITHUB_WORKSPACE", ".")) / "output" / "vps8"
OUT.mkdir(parents=True, exist_ok=True)

logs = []
def L(msg):
    ts = datetime.now().strftime("%H:%M:%S")
    line = ts + " | " + str(msg)
    logs.append(line)
    print(line, flush=True, file=sys.stderr)

def tg(text=""):
    if not TG_TOKEN or not MY_CHAT_ID: return
    for i in range(0, max(1, len(text)), 4000):
        try:
            requests.post("https://api.telegram.org/bot" + TG_TOKEN + "/sendMessage",
                json={"chat_id": MY_CHAT_ID, "text": text[i:i+4000]}, timeout=30)
            time.sleep(0.5)
        except: pass

def tg_img(path):
    if not TG_TOKEN or not MY_CHAT_ID or not os.path.isfile(path): return
    try:
        with open(path, "rb") as f:
            requests.post("https://api.telegram.org/bot" + TG_TOKEN + "/sendPhoto",
                data={"chat_id": MY_CHAT_ID, "caption": Path(path).name},
                files={"photo": f}, timeout=30)
        time.sleep(0.5)
    except: pass

def save_b64(b64, name):
    try: (OUT / (name + ".png")).write_bytes(base64.b64decode(b64))
    except: pass

# ═══════════════════════════════════════════════════════════
# AI solve reCAPTCHA image grid
# ═══════════════════════════════════════════════════════════
def ai_solve(b64, question, rows, cols):
    if not AI_API_KEY: return []
    mx = rows * cols
    nl = []
    for r in range(rows):
        nl.append("Row" + str(r+1) + ": [" + ", ".join(str(r*cols+c+1) for c in range(cols)) + "]")
    prompt = (
        "Grid " + str(rows) + "x" + str(cols) + " pictures.\n"
        "Find: \"" + question + "\"\n\n"
        "Numbering:\n" + "\n".join(nl) + "\n\n"
        "Reply ONLY cell numbers comma separated.\n"
        "Example: 1, 4, 7. If none: -1.")
    try:
        img = Image.open(BytesIO(base64.b64decode(b64)))
        if max(img.size) > 1024:
            ratio = 1024.0 / max(img.size)
            img = img.resize((int(img.width*ratio), int(img.height*ratio)))
        buf = BytesIO()
        img.save(buf, format="PNG")
        sb64 = base64.b64encode(buf.getvalue()).decode()
        save_b64(sb64, "ai")
        r = requests.post(AI_BASE_URL + "/chat/completions",
            headers={"Authorization": "Bearer " + AI_API_KEY},
            json={"model": AI_MODEL_NAME, "messages": [
                {"role": "system", "content": "Return only cell numbers."},
                {"role": "user", "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": "data:image/png;base64," + sb64, "detail": "high"}}]}],
                "max_tokens": 50, "temperature": 0.1},
            timeout=60)
        ans = r.json()["choices"][0]["message"]["content"].strip()
        L("AI: " + ans)
        if "-1" in ans: return []
        return [int(n) for n in re.findall(r'\d+', ans) if 1 <= int(n) <= mx]
    except Exception as e:
        L("AI err: " + str(e))
        return []

# ═══════════════════════════════════════════════════════════
# Captcha solving flow (called when logged into challenge iframe)
# ═══════════════════════════════════════════════════════════
def do_captcha_rounds(sb):
    d = sb.driver
    for rnd in range(1, 30):
        tiles = d.find_elements(By.CSS_SELECTOR, ".rc-imageselect-tile")
        total = len(tiles)
        if total == 16: rows, cols = 4, 4
        elif total == 12: rows, cols = 4, 3
        else: rows, cols = 3, 3
        
        q = d.execute_script(
            "var el=document.querySelector('.rc-imageselect-instructions');"
            "return el?(el.innerText||el.textContent||'').trim().substring(0,120):'';") or "the object"
        L("R" + str(rnd) + " " + str(rows) + "x" + str(cols) + " Q:" + q)
        
        gb = ""
        try: gb = d.find_element(By.CSS_SELECTOR, ".rc-imageselect-table").screenshot_as_base64
        except:
            try: gb = d.find_element(By.ID, "rc-imageselect").screenshot_as_base64
            except: pass
        if gb:
            save_b64(gb, "r" + str(rnd))
            nums = ai_solve(gb, q, rows, cols)
            if nums:
                L("Click: " + str(nums))
                for n in nums:
                    i = n - 1
                    if 0 <= i < len(tiles): tiles[i].click(); time.sleep(0.2)
        time.sleep(1)
        try: d.find_element(By.ID, "recaptcha-verify-button").click()
        except: return False
        time.sleep(5)
        
        # Check token (from parent context)
        parent_token = d.execute_script(
            "return document.getElementById('g-recaptcha-response') ? "
            "document.getElementById('g-recaptcha-response').value : '';")
        if len(parent_token) > 50:
            L("PASSED!")
            d.switch_to.default_content()
            return True
        
        # Also check from within iframe context
        token = d.execute_script(
            "return document.getElementById('g-recaptcha-response') ? "
            "document.getElementById('g-recaptcha-response').value : '';")
        if len(token) > 50:
            L("PASSED from inside!")
            d.switch_to.default_content()
            return True

        # Check if popup disappeared
        try: d.find_element(By.CSS_SELECTOR, ".rc-imageselect-table")
        except:
            d.switch_to.default_content()
            t = d.execute_script(
                "return document.getElementById('g-recaptcha-response') ? "
                "document.getElementById('g-recaptcha-response').value : '';")
            return len(t) > 50
    d.switch_to.default_content()
    return False

# ═══════════════════════════════════════════════════════════
# Full captcha flow
# ═══════════════════════════════════════════════════════════
def do_captcha(sb):
    d = sb.driver
    d.switch_to.default_content()
    L("Finding checkbox...")
    try:
        # 注意: CSS 属性匹配大小写敏感, iframe title 是 "reCAPTCHA"(大写),
        # 用小写 'recaptcha' 永远匹配不到, 改用 src 匹配 anchor iframe
        WebDriverWait(d, 15).until(
            EC.frame_to_be_available_and_switch_to_it((By.CSS_SELECTOR,
                "iframe[src*='api2/anchor'], iframe[title*='reCAPTCHA']")))
        d.find_element(By.ID, "recaptcha-anchor").click()
        d.switch_to.default_content()
        L("Checkbox clicked")
    except Exception as e:
        L("Checkbox err: " + str(e))
        d.switch_to.default_content()
        p = str(OUT / "checkbox_err.png")
        try:
            sb.save_screenshot(p)
            tg_img(p)
        except: pass
        return False
    sb.sleep(3)
    t = d.execute_script("return document.getElementById('g-recaptcha-response')?document.getElementById('g-recaptcha-response').value:'';")
    if len(t) > 50:
        L("Passed immediately!")
        return True

    L("Waiting challenge...")
    try:
        WebDriverWait(d, 20).until(
            EC.frame_to_be_available_and_switch_to_it((By.CSS_SELECTOR, "iframe[src*='bframe']")))
        d.find_element(By.CSS_SELECTOR, ".rc-imageselect-table")
        L("In challenge")
    except:
        return len(d.execute_script("return document.getElementById('g-recaptcha-response')?document.getElementById('g-recaptcha-response').value:'';")) > 50
    
    ok = do_captcha_rounds(sb)
    d.switch_to.default_content()
    return ok

# ═══════════════════════════════════════════════════════════
# Cookie 注入: 有效 cookie 可直接签到, 完全绕开登录和 reCAPTCHA
# ═══════════════════════════════════════════════════════════
def inject_cookies(sb):
    if not VPS8_COOKIES:
        L("VPS8_COOKIES not set, skip cookie injection")
        return False
    sb.open(BASE_URL)
    sb.sleep(2)
    count = 0
    injected = {}
    for pair in VPS8_COOKIES.split(";"):
        pair = pair.strip()
        if "=" not in pair:
            continue
        name, _, value = pair.partition("=")
        try:
            sb.driver.add_cookie({"name": name.strip(), "value": value.strip(), "path": "/"})
            injected[name.strip()] = value.strip()
            count += 1
        except Exception as e:
            L("Cookie inject err [" + name.strip() + "]: " + str(e))
    # 诊断: 打印注入的 cookie 名和值前8位, 便于核对 Secret 内容是否正确
    for n, v in injected.items():
        L("  inject: " + n + " = " + v[:8] + "... (len " + str(len(v)) + ")")
    L("Injected " + str(count) + " cookies")
    return injected

def dump_browser_cookies(sb, injected):
    """诊断: 对比浏览器当前 cookie 和注入值, 判断会话是被服务端拒绝还是丢失"""
    try:
        for c in (sb.driver.get_cookies() or []):
            n = c.get("name", "")
            v = c.get("value", "")
            mark = ""
            if isinstance(injected, dict) and n in injected:
                mark = " [SAME]" if v == injected[n] else " [CHANGED! server reissued]"
            L("  browser: " + n + " = " + v[:8] + "... (len " + str(len(v)) + ")" + mark)
    except Exception as e:
        L("dump cookies err: " + str(e))

def is_login_page(sb):
    """判断当前是否登录页: 站点已改版为中文登录页(hCaptcha), 不能只认英文文案"""
    try:
        cur = sb.get_current_url()
        if "/login" in cur:
            return True
        src = sb.get_page_source()
        if "Login to your account" in src:
            return True
        if "hcaptcha" in src.lower() and "password" in src.lower():
            return True
    except Exception:
        pass
    return False

# ═══════════════════════════════════════════════════════════
# Login
# ═══════════════════════════════════════════════════════════
def do_login(sb):
    L("Navigating to login URL: " + LOGIN_URL)
    sb.open(LOGIN_URL)
    sb.sleep(5)

    # Take screenshot before anything
    p = str(OUT / "login_start.png")
    try:
        sb.save_screenshot(p)
        tg_img(p)
    except: pass

    # Type credentials
    try:
        sb.type("#email", VPS8_EMAIL)
        sb.type("#password", VPS8_PASSWORD)
        L("Credentials typed")
    except Exception as e:
        L("Type creds err: " + str(e))
        return False

    # Solve captcha
    if not do_captcha(sb):
        L("Captcha failed")
        p2 = str(OUT / "captcha_fail.png")
        try:
            sb.save_screenshot(p2)
            tg_img(p2)
        except: pass
        return False

    # Submit login
    L("Submitting login...")
    
    # Try multiple approaches
    submitted = False
    
    # Method 1: JS click the submit button
    try:
        sb.driver.execute_script(
            "var b=document.querySelector('form button[type=\"submit\"]');"
            "if(b){b.click();}")
        submitted = True
        L("JS click submit button")
    except Exception as e:
        L("JS click err: " + str(e))
    
    # Method 2: If JS didn't work, try SeleniumBase click
    if not submitted:
        try:
            sb.click('button[type="submit"]')
            submitted = True
            L("SeleniumBase click submit")
        except Exception as e:
            L("SB click err: " + str(e))
    
    # Wait for navigation
    L("Waiting for page to load...")
    sb.sleep(12)

    # Take result screenshot
    cur = sb.get_current_url()
    L("After submit URL: " + cur)
    p3 = str(OUT / "login_end.png")
    try:
        sb.save_screenshot(p3)
        tg_img(p3)
    except: pass
    
    # Check page state
    src = sb.get_page_source()
    if "Login to your account" in src:
        L("STILL on login page!")
        # Check for error messages on page
        try:
            body_text = sb.driver.find_element(By.TAG_NAME, "body").text
            for keyword in ["Invalid", "incorrect", "error", "wrong", "failed"]:
                if keyword.lower() in body_text.lower():
                    L("Found '" + keyword + "' in page body")
                    # Extract nearby text
                    idx = body_text.lower().find(keyword.lower())
                    L("Context: ..." + body_text[max(0,idx-50):idx+100] + "...")
                    break
        except:
            pass
        return False
    else:
        L("LOGIN SUCCESS! Navigated to: " + cur)
        return True


# ════════════════════════════════════════════════════════
# API Key 通道: FOSSBilling 官方 Basic Auth(client:APIKEY),
# 外部 API 调用无需登录/验证码/CSRF, 也不受会话 IP 限制
# ════════════════════════════════════════════════════════
def parse_api_response(status, body):
    try:
        j = json.loads(body)
        if j.get("error"):
            msg = str(j["error"].get("message", ""))
            if "already" in msg.lower() or "已签" in msg:
                return "already_signed_in"
            return "api_error:" + msg
        if "result" in j and j["result"] is not None:
            return "success:" + json.dumps(j["result"], ensure_ascii=False)[:200]
    except Exception:
        pass
    return "status_" + str(status)

def api_signin_requests():
    """requests 直连签到接口; 连接层被站点防护掤断时返回 None 交给浏览器通道"""
    url = BASE_URL + "/api/client/points/signin"
    try:
        r = requests.post(url, auth=("client", VPS8_API_KEY),
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"},
            timeout=30)
        L("API(requests) " + str(r.status_code) + ": " + r.text[:300])
        return parse_api_response(r.status_code, r.text)
    except Exception as e:
        L("API(requests) conn err: " + str(e))
        return None

def api_signin_browser(sb):
    """requests 被掤断时的兑底: 在浏览器里带 Basic Auth 调 API"""
    sb.open(BASE_URL)
    sb.sleep(2)
    js = (
        "var key = arguments[0];"
        "var done = arguments[arguments.length - 1];"
        "fetch('/api/client/points/signin', {"
        "  method: 'POST',"
        "  headers: {'Authorization': 'Basic ' + btoa('client:' + key)}"
        "}).then(function(r){return r.text().then(function(t){done(r.status + '|' + t);});})"
        ".catch(function(e){done('ERR|' + e);});")
    try:
        sb.driver.set_script_timeout(30)
        resp = str(sb.driver.execute_async_script(js, VPS8_API_KEY) or "")
    except Exception as e:
        L("API(browser) crash: " + str(e))
        return "api_err:" + str(e)
    L("API(browser) resp: " + resp[:500])
    if resp.startswith("ERR|"):
        return "api_err:" + resp[4:]
    status, _, body = resp.partition("|")
    return parse_api_response(status, body)

def main():
    L("=50")
    L("VPS8 SIGNIN")
    L("AI: " + AI_MODEL_NAME)
    L("Start: " + datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    L("=30")

    result = ""
    ok = False

    # 首选 API Key 通道: 先试 requests 直连, 成功则完全不用开浏览器
    if VPS8_API_KEY:
        L("API key set, trying direct API signin...")
        r = api_signin_requests()
        if r is not None:
            result = r
            ok = ("success" in result) or ("already" in result)
            L("RESULT: " + str(result))
            L("OK: " + str(ok))
            finish(ok, result)
            return
        L("Direct API blocked, falling back to browser fetch...")

    try:
        with SB(headed=False, locale="en",
                chromium_arg=["--disable-blink-features=AutomationControlled",
                    "--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage",
                    "--window-size=1280,900"]) as sb:
            
            if VPS8_API_KEY:
                # API Key 浏览器兑底通道, 不需要登录态
                result = api_signin_browser(sb)
                ok = ("success" in result) or ("already" in result)
                L("RESULT: " + str(result))
                L("OK: " + str(ok))
                finish(ok, result)
                return

            # 先注入 cookie(若已配置), 有效则直接签到, 不碰验证码
            injected = inject_cookies(sb)
            sb.open(SIGNIN_URL)
            sb.sleep(3)
            src = sb.get_page_source()
            L("After open signin, URL: " + sb.get_current_url())
            
            # Check if logged in (用 URL/页面特征判断, 登录页已改版为中文)
            if is_login_page(sb):
                if VPS8_COOKIES:
                    L("Cookie expired/invalid, falling back to login...")
                    # 诊断: 看注入的 cookie 是否还在/是否被服务端换掉
                    dump_browser_cookies(sb, injected)
                    p_ck = str(OUT / "cookie_fail.png")
                    try:
                        sb.save_screenshot(p_ck)
                        tg_img(p_ck)
                    except: pass
                L("Not logged in, logging in...")
                if do_login(sb):
                    L("Login ok, doing signin...")
                    sb.open(SIGNIN_URL)
                    sb.sleep(3)
                    src2 = sb.get_page_source()
                    result = check_and_signin(sb, src2)
                    ok = ("success" in result) or ("already" in result)
                else:
                    result = "login_failed"
            else:
                L("Cookie valid, signin directly...")
                result = check_and_signin(sb, src)
                ok = ("success" in result) or ("already" in result)
            
            L("RESULT: " + str(result))
            L("OK: " + str(ok))

            # 无论成败都留一张最终截图, 保证 Artifact 里有现场可查
            p_final = str(OUT / "final.png")
            try:
                sb.open(SIGNIN_URL)
                sb.sleep(2)
                sb.save_screenshot(p_final)
                tg_img(p_final)
            except: pass

    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        L("CRASH:")
        L(tb)
        result = "crash:" + str(e)
    
    finish(ok, result)

def finish(ok, result):
    """统一收尾: TG 通知 + 日志 + GITHUB_OUTPUT + 退出码"""
    L("=30")
    L("FINAL: ok=" + str(ok) + " result=" + str(result))
    icon = "[OK]" if ok else "[FAIL]"
    tg(icon + " Signin\n\nResult: " + str(result) + "\n\nTime: " + datetime.now().strftime("%Y-%m-%d %H:%M"))
    for ss in sorted(OUT.glob("*.png")):
        tg_img(str(ss))
    time.sleep(1)
    tg("LOG:\n" + "\n".join(logs[-100:]))
    
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a") as f:
            f.write("success=" + ("true" if ok else "false") + "\n")
            f.write("result=" + str(result) + "\n")
    sys.exit(0 if ok else 1)

def check_and_signin(sb, src):
    """Check signin page and sign in if possible"""
    
    # Check already signed in
    if "今日已签" in src or "已经签到" in src or "已签到" in src:
        return "already_signed_in"
    
    # Check if cookie expired
    if is_login_page(sb):
        return "cookie_expired"
    
    # Get CSRF
    m = re.search(r'name="CSRFToken"\s+value="(\w+)"', src)
    if not m:
        m = re.search(r'name="csrf-token"\s+content="(\w+)"', src)
    if not m:
        return "no_csrf"
    
    csrf = m.group(1)
    L("CSRF=" + csrf[:20])
    L("Calling signin API via browser fetch...")
    
    # 用浏览器内 fetch 调签到接口: 自带浏览器 TLS 指纹和会话,
    # requests 直连会被站点防护採断(Connection aborted)
    js = (
        "var done = arguments[arguments.length - 1];"
        "fetch('/api/client/points/signin', {"
        "  method: 'POST',"
        "  headers: {'Content-Type': 'application/x-www-form-urlencoded',"
        "            'X-Requested-With': 'XMLHttpRequest'},"
        "  body: 'CSRFToken=" + csrf + "',"
        "  credentials: 'include'"
        "}).then(function(r){return r.text().then(function(t){done(r.status + '|' + t);});})"
        ".catch(function(e){done('ERR|' + e);});")
    try:
        sb.driver.set_script_timeout(30)
        resp = str(sb.driver.execute_async_script(js) or "")
    except Exception as e:
        L("Fetch crash: " + str(e))
        return "api_err:" + str(e)
    L("API resp: " + resp[:500])
    if resp.startswith("ERR|"):
        return "api_err:" + resp[4:]
    status, _, body = resp.partition("|")
    try:
        j = json.loads(body)
        if j.get("error"):
            msg = j["error"].get("message", "")
            if "already" in msg.lower() or "已签" in msg:
                return "already_signed_in"
            return "api_error:" + msg
        if "result" in j and j["result"] is not None:
            return "success:" + json.dumps(j["result"], ensure_ascii=False)[:200]
    except Exception:
        pass
    if status in ("200", "302"):
        sb.open(SIGNIN_URL)
        sb.sleep(3)
        s2 = sb.get_page_source()
        if "已签到" in s2 or "今日已签" in s2:
            return "already_signed_in"
        return "success"
    return "status_" + status

if __name__ == "__main__":
    main()
