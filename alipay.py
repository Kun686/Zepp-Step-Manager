# -*- coding: utf-8 -*-
"""获取 Zepp Life 的支付宝授权入口并查询绑定状态。"""

import uuid
from getpass import getpass
from urllib.parse import urlparse
import requests

APP = "com.xiaomi.hm.health"
API_HOST = "api-mifit-cn3.zepp.com"
ALIPAY_APP_ID = "2017030806118105"
AUTH_PAGE = "https://authweb.alipay.com/auth"
HEADERS = {
    "User-Agent":"ZeppLife/6.14.0 (iPhone; iOS 16.6; Scale/3.00)",
    "appname":APP, "app_name":APP, "cv":"319_6.14.0", "v":"2.0",
    "channel":"appstore", "lang":"zh_CN", "country":"CN",
    "timezone":"Asia/Shanghai", "Accept-Language":"zh-Hans-CN;q=1",
    "appplatform":"ios_phone",
}

def login_for_token(account, password):
    from zepp import encrypt_login, normalize_account
    import urllib.parse
    account, is_phone = normalize_account(account)
    response = requests.post(
        "https://api-user.zepp.com/v2/registrations/tokens",
        data=encrypt_login(account, password),
        headers={"content-type":"application/x-www-form-urlencoded; charset=UTF-8",
                 "user-agent":"MiFit6.14.0 (Android 12)", "app_name":APP,
                 "appname":APP, "appplatform":"android_phone"},
        allow_redirects=False, timeout=20,
    )
    if response.status_code != 303:
        raise RuntimeError(f"Zepp 登录失败：HTTP {response.status_code}")
    access = urllib.parse.parse_qs(urllib.parse.urlparse(
        response.headers.get("Location", "")).query).get("access", [None])[0]
    if not access:
        raise RuntimeError("登录响应中没有 access token")
    result = requests.post(
        "https://account.zepp.com/v2/client/login",
        data={"app_name":APP, "app_version":"6.14.0", "code":access,
              "country_code":"CN", "device_id":str(uuid.uuid4()).upper(),
              "device_id_type":"uuid", "device_model":"phone",
              "grant_type":"access_token", "lang":"zh_CN",
              "os_version":"1.5.0", "source":APP,
              "third_name":"huami_phone" if is_phone else "email"},
        headers=HEADERS, timeout=20,
    ).json()
    if result.get("result") != "ok":
        raise RuntimeError("获取 Zepp Token 失败")
    info = result.get("token_info", {})
    if not info.get("app_token") or not info.get("user_id"):
        raise RuntimeError("登录响应缺少必要信息")
    return info["app_token"], str(info["user_id"])

class AlipayBinding:
    def __init__(self, app_token, user_id):
        self.app_token, self.user_id = app_token, str(user_id)
        self.session = requests.Session()

    def _headers(self):
        headers = dict(HEADERS)
        headers["apptoken"] = self.app_token
        headers["X-Request-Id"] = str(uuid.uuid4())
        return headers

    def status(self):
        response = self.session.get(
            f"https://{API_HOST}/thirdParties/oauth2/list",
            params={"r":str(uuid.uuid4()), "userid":self.user_id},
            headers=self._headers(), timeout=15,
        )
        response.raise_for_status()
        item = response.json().get(ALIPAY_APP_ID, {})
        return {"bound":item.get("state") == 1, "state":item.get("state")}

    def authorization_url(self):
        response = self.session.get(
            f"https://{API_HOST}/v1/thirdParties/auth.json",
            params={"r":str(uuid.uuid4()), "userid":self.user_id},
            headers=self._headers(), timeout=15,
        )
        response.raise_for_status()
        body = response.json()
        if body.get("code") != 1:
            raise RuntimeError(f"获取支付宝授权参数失败：{body.get('message')}")
        auth_info = (body.get("data") or {}).get("authInfo")
        if not auth_info:
            raise RuntimeError("响应中没有 authInfo")
        params = dict(part.split("=", 1) for part in auth_info.split("&") if "=" in part)
        params.setdefault("callbackScheme", f"alp{params.get('app_id', ALIPAY_APP_ID)}")
        url = AUTH_PAGE + "?v=h5&" + "&".join(f"{k}={v}" for k, v in params.items())
        if urlparse(url).netloc != "authweb.alipay.com":
            raise RuntimeError("授权链接域名不正确")
        return url

if __name__ == "__main__":
    account = input("Zepp 账号：").strip()
    password = getpass("Zepp 密码：")
    token, user_id = login_for_token(account, password)
    client = AlipayBinding(token, user_id)
    print("支付宝状态：", "已绑定" if client.status()["bound"] else "未绑定")
    print("请在手机支付宝中打开下面的授权入口：")
    print(client.authorization_url())
