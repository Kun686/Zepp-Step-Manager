# -*- coding: utf-8 -*-
"""小米运动 / Zepp Life 步数提交工具。"""

import re
import time
import uuid
import urllib.parse
from getpass import getpass
from datetime import datetime
from zoneinfo import ZoneInfo

import requests
from Crypto.Cipher import AES

APP = "com.xiaomi.hm.health"
TIMEOUT = 20
TEMPLATE_URL = "https://raw.githubusercontent.com/TonyJiangWJ/mimotion/master/util/zepp_helper.py"

def normalize_account(account):
    account = account.strip()
    if account.isdigit() and len(account) == 11:
        return "+86" + account, True
    return account, account.startswith("+") and account[1:].isdigit()

def encrypt_login(account, password):
    key, iv = b"xeNtBVqzDc6tuNTh", b"MAAAYAAAAAAAAABg"
    query = urllib.parse.urlencode({
        "emailOrPhone": account, "password": password,
        "state": "REDIRECTION", "client_id": "HuaMi",
        "country_code": "CN", "token": "access",
        "redirect_uri": "https://s3-us-west-2.amazonaws.com/hm-registration/successsignin.html",
    }).encode()
    pad = 16 - len(query) % 16
    return AES.new(key, AES.MODE_CBC, iv).encrypt(query + bytes([pad]) * pad)

def login(account, password):
    account, is_phone = normalize_account(account)
    response = requests.post(
        "https://api-user.zepp.com/v2/registrations/tokens",
        data=encrypt_login(account, password),
        headers={"content-type":"application/x-www-form-urlencoded; charset=UTF-8",
                 "user-agent":"MiFit6.14.0 (Android 12)", "app_name":APP,
                 "appname":APP, "appplatform":"android_phone"},
        allow_redirects=False, timeout=TIMEOUT,
    )
    if response.status_code != 303:
        raise RuntimeError(f"Zepp 登录失败：HTTP {response.status_code}")
    location = response.headers.get("Location", "")
    access = urllib.parse.parse_qs(urllib.parse.urlparse(location).query).get("access", [None])[0]
    if not access:
        raise RuntimeError("登录响应中没有 access token")

    result = requests.post(
        "https://account.zepp.com/v2/client/login",
        data={"app_name":APP, "app_version":"6.14.0", "code":access,
              "country_code":"CN", "device_id":str(uuid.uuid4()).upper(),
              "device_id_type":"uuid", "device_model":"phone",
              "grant_type":"access_token", "lang":"zh_CN", "os_version":"1.5.0",
              "source":APP, "third_name":"huami_phone" if is_phone else "email"},
        headers={"app_name":APP, "appname":APP, "appplatform":"android_phone",
                 "cv":"50818_6.14.0", "v":"2.0"},
        timeout=TIMEOUT,
    ).json()
    if result.get("result") != "ok":
        raise RuntimeError(f"获取 Zepp Token 失败：{result.get('error_code', '未知错误')}")
    info = result.get("token_info", {})
    if not info.get("app_token") or not info.get("user_id"):
        raise RuntimeError("登录响应缺少必要信息")
    return info["app_token"], str(info["user_id"])

def get_device_id(app_token, user_id):
    response = requests.get(
        "https://api-mifit-cn.huami.com/v1/device/binds.json",
        params={"userid":user_id},
        headers={"apptoken":app_token, "User-Agent":"MiFit6.14.0 (Android 12)"},
        timeout=TIMEOUT,
    )
    response.raise_for_status()
    items = response.json().get("items", [])
    for item in items:
        value = item.get("deviceId") or item.get("mac")
        if value and (item.get("deviceType") == 0 or any(
            word in str(item.get("productName", "")).lower()
            for word in ("band", "watch", "手环", "手表")
        )):
            return str(value).replace(":", "").upper()
    raise RuntimeError("没有找到已绑定的手环或手表，请先在 Zepp Life 中绑定设备")

def get_template():
    response = requests.get(TEMPLATE_URL, timeout=TIMEOUT)
    response.raise_for_status()
    match = re.search(r"""data_json\s*=\s*(['"])(%5B%7B%22data_hr%22.*?%5D)\1""", response.text, re.S)
    if not match:
        raise RuntimeError("上游脚本中的步数模板格式有变化")
    return match.group(2)

def submit_steps(step, app_token, user_id, device_id):
    if not isinstance(step, int) or step < 0:
        raise ValueError("步数必须是大于或等于 0 的整数")
    data = get_template()
    today = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d")
    data, a = re.subn(r"(date%22%3A%22).*?(%22%2C%22data)",
                      lambda m:m.group(1)+today+m.group(2), data, count=1)
    data, b = re.subn(r"(ttl%5C%22%3A).*?(%2C%5C%22dis)",
                      lambda m:m.group(1)+str(step)+m.group(2), data, count=1)
    data, c = re.subn(r"(did%22%3A%22)[0-9A-Fa-f]{16}",
                      lambda m:m.group(1)+device_id, data, count=1)
    if (a, b, c) != (1, 1, 1):
        raise RuntimeError("步数模板格式有变化，无法安全替换数据")
    body = (f"userid={user_id}&last_sync_data_time=1597306380&device_type=0"
            f"&last_deviceid={device_id}&data_json={data}")
    response = requests.post(
        "https://api-mifit-cn.huami.com/v1/data/band_data.json",
        params={"t":str(round(time.time()*1000)), "r":str(uuid.uuid4())},
        data=body,
        headers={"apptoken":app_token, "Content-Type":"application/x-www-form-urlencoded"},
        timeout=TIMEOUT,
    )
    response.raise_for_status()
    result = response.json()
    if result.get("message") != "success":
        raise RuntimeError(f"步数提交失败：{result.get('message', result)}")
    return result

def main():
    print("Zepp Life 步数提交")
    account = input("小米运动账号（手机号或邮箱）：").strip()
    password = getpass("密码：")
    try:
        step = int(input("要提交的步数：").strip())
    except ValueError:
        raise SystemExit("步数请输入整数")
    if not account or not password:
        raise SystemExit("账号和密码不能为空")
    token, user_id = login(account, password)
    device_id = get_device_id(token, user_id)
    print("已找到账号下的设备")
    submit_steps(step, token, user_id, device_id)
    print(f"提交成功：{step} 步")

if __name__ == "__main__":
    main()
