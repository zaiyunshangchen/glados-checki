#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
2026 GLaDOS 自动签到（积分增强版）

相对原版的改动（仅签到部分，步数模拟代码原样保留、一行未改）：
1. 签到没成功时以非 0 退出 —— Actions 会变红并触发 "Retry on Failure"，
   不再出现「日志全绿、其实没签到」的假成功。
2. 明确识别 GLaDOS 返回 {"code":-2}（Cookie 失效）与 {"code":4,"reason":"device-mismatch"}
   （UA 被判定为机器人），都会给出明确原因。
3. Cookie 名兼容：GLaDOS 已把 koa:sess 改名为 gld:sess，旧的 koa 名字不再被识别，
   脚本会自动把 koa:sess 改写为 gld:sess。
4. {"code":1,"message":"Today's observation logged..."} = 今日已签到，算成功，不误报失败。
5. 失败退出码放在步数代码「之后」，签到失败不会影响步数任务执行。

Secret：GLADOS_COOKIE（必需）、PUSHPLUS_TOKEN（可选）
"""

import requests
import json
import os
import sys
import smtplib
from datetime import datetime
from email.mime.text import MIMEText
from email.header import Header
from email.utils import formataddr

if sys.platform.startswith('win'):
    sys.stdout.reconfigure(encoding='utf-8')

# ================= 配置 =================

DOMAINS = [
    "https://glados.cloud",
    "https://glados.rocks",
    "https://glados.network",
]

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36',
    'Content-Type': 'application/json;charset=UTF-8',
    'Accept': 'application/json, text/plain, */*',
    'Accept-Language': 'zh-CN,zh;q=0.9',
}

# ================= 工具函数 =================

def log(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def extract_cookie(raw: str):
    """提取 Cookie，支持 Cookie-Editor 导出格式

    GLaDOS 已把 Cookie 名从 koa:sess 改成 gld:sess，用旧名字会一律返回
    {"code":-2,"message":"没有权限"}，所以这里统一改写为新名字。
    """
    if not raw:
        return None
    raw = raw.strip().strip('"').strip("'").strip()

    if raw.startswith('{'):
        try:
            raw = 'gld:sess=' + json.loads(raw).get('token', '')
        except Exception:
            pass

    # 只给了 sess 的值（没有等号），自动补上名字
    if '=' not in raw and len(raw) > 40:
        raw = 'gld:sess=' + raw

    # koa:sess -> gld:sess（先换 .sig，避免被前缀替换影响）
    raw = raw.replace('koa:sess.sig', 'gld:sess.sig').replace('koa:sess', 'gld:sess')
    return raw


def get_cookies():
    raw = os.environ.get("GLADOS_COOKIE", "")
    if not raw:
        log("❌ 未配置 GLADOS_COOKIE")
        return []
    sep = '\n' if '\n' in raw else '&'
    cookies = [extract_cookie(c) for c in raw.split(sep) if c.strip()]

    # 指纹自检：只打印特征，不打印 Cookie 本身，用来区分「格式错」还是「已过期」
    for i, c in enumerate(cookies, 1):
        has_sess = ('gld:sess=' in c) or ('koa:sess=' in c)
        has_sig = ('gld:sess.sig=' in c) or ('koa:sess.sig=' in c)
        has_quote = ('"' in c) or ("'" in c)
        log(f"🔍 Cookie{i} 长度={len(c)} sess={has_sess} sig={has_sig} 含引号={has_quote}")
        if not has_sess:
            log("   ❌ 缺少 gld:sess，请确认复制的是 gld:sess（不是别的名字）")
        if not has_sig:
            log("   ❌ 缺少 gld:sess.sig，两个值都必须复制")
    return cookies


# ================= 核心逻辑 =================

class GLaDOS:
    def __init__(self, cookie):
        self.cookie = cookie
        self.domain = DOMAINS[0]
        self.email = "?"
        self.left_days = "?"
        self.points = "?"
        self.points_change = "?"
        self.exchange_info = ""
        self.last_error = ""
        self.auth_error = ""
        self.dead = set()

    def req(self, method, path, data=None):
        # 优先用上次成功的域名，已判定连不上的域名直接跳过，避免重复等超时
        order = [self.domain] + [d for d in DOMAINS if d != self.domain]
        for d in order:
            if d in self.dead:
                continue
            try:
                url = f"{d}{path}"
                h = HEADERS.copy()
                h['Cookie'] = self.cookie
                h['Origin'] = d
                h['Referer'] = f"{d}/console/checkin"

                if method == 'GET':
                    resp = requests.get(url, headers=h, timeout=10)
                else:
                    resp = requests.post(url, headers=h, json=data, timeout=10)

                if resp.status_code == 200:
                    try:
                        j = resp.json()
                    except ValueError:
                        self.last_error = f"返回非 JSON: {resp.text[:120]}"
                        log(f"⚠️ {d} {self.last_error}")
                        continue

                    # GLaDOS 认证失败时同样返回 HTTP 200，
                    # 必须在此识别并继续尝试下一个域名，否则会卡在第一个域名上。
                    if isinstance(j, dict) and j.get('code') == -2:
                        self.auth_error = j.get('message', '没有权限')
                        self.last_error = self.auth_error
                        log(f"⚠️ {d} 认证失败: {self.auth_error}，换下一个域名")
                        continue

                    self.domain = d
                    self.auth_error = ""
                    return j

                self.last_error = f"HTTP {resp.status_code}"
                log(f"⚠️ {d} 返回 {resp.status_code}")
            except Exception as e:
                self.dead.add(d)
                self.last_error = str(e)
                log(f"⚠️ {d} 请求失败: {e}")
                continue
        return None

    def get_status(self):
        res = self.req('GET', '/api/user/status')
        if res and 'data' in res:
            d = res['data']
            self.email = d.get('email', 'Unknown')
            self.left_days = str(d.get('leftDays', '?')).split('.')[0]
            return True
        return False

    def get_points(self):
        res = self.req('GET', '/api/user/points')
        if res and 'points' in res:
            self.points = str(res.get('points', '0')).split('.')[0]
            history = res.get('history', [])
            if history:
                change = str(history[0].get('change', '0')).split('.')[0]
                if not change.startswith('-'):
                    change = '+' + change
                self.points_change = change
            plans = res.get('plans', {})
            pts = int(self.points)
            lines = []
            for _, plan_data in plans.items():
                need = plan_data['points']
                days = plan_data['days']
                if pts >= need:
                    lines.append(f"✅ {need}分→{days}天 (可兑换)")
                else:
                    lines.append(f"❌ {need}分→{days}天 (差{need - pts}分)")
            self.exchange_info = "<br>".join(lines)
            return True
        return False

    def checkin(self):
        return self.req('POST', '/api/user/checkin', {'token': 'glados.cloud'})


# ================= 推送 =================

def pushplus(token, title, content):
    if not token:
        return
    try:
        requests.get("http://www.pushplus.plus/send",
                     params={'token': token, 'title': title,
                             'content': content, 'template': 'html'},
                     timeout=5)
        log("✅ PushPlus 推送成功")
    except Exception:
        log("❌ PushPlus 推送失败")


def send_mail(subject, body):
    """SMTP 发信，仅在签到失败时调用。缺配置则静默跳过。"""
    user = os.environ.get("MAIL_USER", "").strip()
    pwd = os.environ.get("MAIL_PASS", "").strip()
    to = os.environ.get("MAIL_TO", "").strip() or user
    host = os.environ.get("MAIL_HOST", "smtp.163.com").strip() or "smtp.163.com"
    try:
        port = int(os.environ.get("MAIL_PORT", "465") or 465)
    except ValueError:
        port = 465

    if not user or not pwd or not to:
        log("⚠️ 未配置 MAIL_USER / MAIL_PASS，跳过邮件通知")
        return

    try:
        msg = MIMEText(body, "plain", "utf-8")
        msg["Subject"] = Header(subject, "utf-8")
        msg["From"] = formataddr((str(Header("GLaDOS 签到机器人", "utf-8")), user))
        msg["To"] = to

        with smtplib.SMTP_SSL(host, port, timeout=25) as s:
            s.login(user, pwd)
            s.sendmail(user, [to], msg.as_string())
        log(f"📧 失败通知邮件已发送: {to}")
    except Exception as e:
        log(f"❌ 邮件发送失败: {e}")


# ================= 主程序 =================
# 注意：main() 改为返回 bool，不再内部 sys.exit，
#       以免签到失败时中断后面那段步数任务。

def main() -> bool:
    log("🚀 2026 GLaDOS Checkin Starting...")
    cookies = get_cookies()
    if not cookies:
        return False

    results = []
    summary = []
    success_cnt = 0

    for i, cookie in enumerate(cookies, 1):
        g = GLaDOS(cookie)

        res = g.checkin()
        if res is None:
            # 认证失败优先报出来，不要被后面域名的网络超时盖掉
            msg = "没有权限（Cookie 失效）" if g.auth_error else f"Network Error ({g.last_error})"
            ok = False
        else:
            code = res.get('code')
            msg = res.get('message') or res.get('reason') or 'Failure'
            if code == -2:
                msg = "没有权限（Cookie 失效）"
                ok = False
            elif code == 4:
                msg = f"设备校验未通过（{res.get('reason', 'device-mismatch')}）—— GLaDOS 把本次请求判定为机器人"
                ok = False
            elif code in (0, 1) or "Checkin" in msg:
                # code:0 Checkin! / Checkin Repeats!
                # code:1 Today's observation logged. Return tomorrow for more points.（今日已签到）
                ok = True
            else:
                ok = False

        g.get_status()
        g.get_points()

        log(f"用户: {g.email} | 积分: {g.points} | 天数: {g.left_days} | 结果: {msg} | {'成功' if ok else '失败'}")
        summary.append(f"账号{i}: {g.email} | 积分 {g.points} | 剩余 {g.left_days} 天 | 结果: {msg}")
        if ok:
            success_cnt += 1
        else:
            log(f"❌ 账号 {i} 签到未成功：{msg}")

        results.append(f"""
<div style="border:2px solid #333; padding:15px; margin-bottom:15px; border-radius:10px; background:#fff;">
    <h3 style="margin:0 0 15px 0; color:#333; border-bottom:2px solid #333; padding-bottom:8px;">👤 {g.email}</h3>
    <p style="margin:8px 0; color:#000; font-size:16px;"><b>当前积分:</b> <span style="color:#e74c3c; font-size:22px; font-weight:bold;">{g.points}</span> <span style="color:#27ae60; font-weight:bold;">({g.points_change})</span></p>
    <p style="margin:8px 0; color:#000; font-size:16px;"><b>剩余天数:</b> <span style="font-weight:bold;">{g.left_days} 天</span></p>
    <p style="margin:8px 0; color:#000; font-size:16px;"><b>签到结果:</b> {msg}</p>
    <div style="margin-top:15px; padding:12px; background:#f0f0f0; border-radius:8px; border:1px solid #ccc;">
        <p style="margin:0 0 8px 0; color:#333; font-weight:bold; font-size:15px;">🎁 兑换选项:</p>
        <p style="margin:0; color:#000; font-size:14px; line-height:1.8;">{g.exchange_info}</p>
    </div>
</div>
""")

    ptoken = os.environ.get("PUSHPLUS_TOKEN")
    if ptoken:
        title = f"GLaDOS签到: 成功{success_cnt}/{len(cookies)}"
        content = "".join(results)
        content += f"<br><small>时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</small>"
        pushplus(ptoken, title, content)

    if success_cnt == 0:
        log("❌ 所有账号均未签到成功")
        log("👉 若上方指纹显示 sess=True 且 sig=True，说明格式没问题，是 Cookie 已过期，"
            "请重新从 glados.cloud 获取并更新 GLADOS_COOKIE")
        log("👉 若结果是「设备校验未通过」，说明本次请求的 User-Agent 不像浏览器，"
            "检查是否被中间层改写了 UA")
        send_mail(
            f"【GLaDOS】签到失败 {datetime.now().strftime('%Y-%m-%d %H:%M')}",
            "本次签到没有成功。\n\n"
            + "\n".join(summary)
            + "\n\n可能原因：GLADOS_COOKIE 已过期。\n"
              "处理：登录 https://glados.cloud → Console → Checkin 页面，"
              "用 Cookie-Editor 复制 gld:sess 与 gld:sess.sig，"
              "更新仓库 Secret GLADOS_COOKIE。\n\n"
            + f"运行时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        )
        return False
    return True


if __name__ == '__main__':
    checkin_ok = main()

    # ================= 步数模拟（逻辑不变，账号密码改为从 Secrets 读取） =================
    import random

    def get_headers(header_raw):
        return dict(line.split(": ", 1) for line in header_raw.split("\n") if line != '')

    headers_str = '''
accept: application/json, text/javascript, */*; q=0.01
accept-language: zh-CN,zh;q=0.9
content-type: application/x-www-form-urlencoded;charset=UTF-8
Accept-Encoding: gzip, deflate
Connection: keep-alive
User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36
'''
    headers = get_headers(headers_str)

    # 账号密码改为从 GitHub Secrets 读取（STEP_USER / STEP_PASSWORD），不再写死在代码里
    step_user = os.environ.get("STEP_USER", "").strip()
    step_password = os.environ.get("STEP_PASSWORD", "").strip()

    if not step_user or not step_password:
        log("⚠️ 未配置 STEP_USER / STEP_PASSWORD，跳过步数模拟")
    else:
        message_dict = {step_user: step_password}

        for key, value in message_dict.items():
            step = random.randint(2000, 8600)
            data = {
                "referrer": "http://bs.yanwan.store/",
                "referrerPolicy": "strict-origin-when-cross-origin",
                "method": "POST",
                "mode": "cors",
                "credentials": "omit",
                "user": "%s" % key,
                "password": "%s" % value,
                "step": "%s" % step,
                "ver": "cxydzsv3.2"
            }
            try:
                response = requests.post(url="http://yanwan.store/run4/mi.php", headers=headers, data=data, timeout=15)
                log(f"🚶 步数提交: {key} -> {step} 步 | HTTP {response.status_code}")
            except Exception as e:
                log(f"❌ 步数提交失败: {e}")
    # ================= 步数模拟结束 =================

    # 签到结果放到最后判断：即使签到失败，上面的步数任务也已经跑完了
    if not checkin_ok:
        sys.exit(1)
