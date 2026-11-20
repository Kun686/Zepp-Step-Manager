"""Zepp Life 注册、登录和微信绑定接口。"""
import re
import time
import uuid
from urllib.parse import urlencode, urlsplit, parse_qs

import requests
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.padding import PKCS7

APP = "com.xiaomi.hm.health"
REDIRECT = "https://s3-us-west-2.amazonaws.com/hm-registration/successsignin.html"
KEY = b"xeNtBVqzDc6tuNTh"
IV = b"MAAAYAAAAAAAAABg"


class ProtocolError(Exception):
    pass


class AuthExpired(ProtocolError):
    pass


def encrypt(raw):
    padder = PKCS7(128).padder()
    padded = padder.update(raw) + padder.finalize()
    encryptor = Cipher(algorithms.AES(KEY), modes.CBC(IV)).encryptor()
    return encryptor.update(padded) + encryptor.finalize()


def safe_host(host):
    return isinstance(host, str) and bool(
        re.fullmatch(r"[a-z0-9-]+\.(?:zepp|huami)\.com", host)
    )


class ZeppClient:
    def __init__(self):
        self.http = requests.Session()
        self.http.trust_env = False

    def close(self):
        self.http.close()

    def headers(self, state=None):
        headers = {
            "app_name": APP, "appname": APP, "cv": "6.14.0",
            "appplatform": "ios_phone", "country": "CN", "lang": "zh_CN",
            "timezone": "Asia/Shanghai", "Accept-Language": "zh-Hans-CN;q=1",
            "User-Agent": "ZeppLife/6.14.0 (iPhone; iOS 16.6; Scale/3.00)",
            "X-Request-Id": str(uuid.uuid4()),
        }
        if state:
            headers.update(
                apptoken=state["app_token"], cv="319_6.14.0",
                v="2.0", channel="appstore"
            )
        return headers

    def request(self, method, url, **kwargs):
        try:
            response = self.http.request(
                method, url, timeout=(6, 20), allow_redirects=False, **kwargs
            )
        except requests.RequestException:
            raise ProtocolError("网络请求失败或超时，请稍后重试。") from None
        if response.status_code == 429:
            raise ProtocolError("接口限流（HTTP 429），请稍后再试。")
        if response.status_code in (401, 403):
            raise AuthExpired("认证失效或请求被拒绝，请重新登录。")
        return response

    def captcha(self):
        response = self.request(
            "GET", "https://api-user.zepp.com/captcha/register",
            params={"r": str(uuid.uuid4()), "t": str(int(time.time() * 1000))},
            headers=self.headers()
        )
        key = response.headers.get("Captcha-Key")
        is_image = response.content.startswith((b"\xff\xd8", b"\x89PNG\r\n\x1a\n"))
        if response.status_code != 200 or not key or not is_image:
            raise ProtocolError("没有获取到验证码图片，请刷新后重试。")
        return key, response.content

    def access(self, account, password, challenge=None, code=None):
        fields = [
            ("client_id", "HuaMi"), ("country_code", "CN"),
            ("emailOrPhone", account), ("password", password),
            ("redirect_uri", REDIRECT), ("state", "REDIRECTION"),
            ("token", "refresh"), ("token", "access"),
        ]
        path = "tokens"
        if challenge is not None:
            path = "register"
            fields.extend([
                ("key", challenge), ("code", code),
                ("name", account.split("@")[0]),
                ("r", str(uuid.uuid4())),
                ("t", str(int(time.time() * 1000))),
            ])
        headers = self.headers()
        headers.update({
            "X-Hm-Ekv": "1", "shouldHookRedirection": "true",
            "Content-Type": "application/x-www-form-urlencoded",
        })
        response = self.request(
            "POST", "https://api-user.zepp.com/v2/registrations/" + path,
            headers=headers, data=encrypt(urlencode(fields).encode())
        )
        params = parse_qs(urlsplit(response.headers.get("Location", "")).query)
        if response.status_code != 303 or not params.get("access"):
            raise ProtocolError(
                f"账号请求未成功（HTTP {response.status_code}）。"
                "请检查账号、密码和验证码；已有账号请选择登录。"
            )
        return params["access"][0]

    def exchange(self, access, registration=False, account=""):
        data = {
            "app_name": APP, "app_version": "6.14.0", "code": access,
            "country_code": "CN", "device_id": str(uuid.uuid4()).upper(),
            "device_id_type": "uuid", "device_model": "phone",
            "dn": "api-user.zepp.com,api-mifit.zepp.com,api-analytics.zepp.com,account.zepp.com,api-watch.zepp.com,auth.zepp.com",
            "grant_type": "access_token", "lang": "zh_CN",
            "os_version": "1.5.0", "recommend_countries": "CN",
            "source": APP,
            "third_name": "huami" if registration else ("email" if "@" in account else "huami_phone"),
        }
        endpoint = "/v1/client/register" if registration else "/v2/client/login"
        response = self.request(
            "POST", "https://account.zepp.com" + endpoint,
            data=data, headers=self.headers()
        )
        try:
            body = response.json()
        except ValueError:
            body = None
        if response.status_code != 200 or not isinstance(body, dict) or body.get("result") != "ok":
            raise ProtocolError("获取 Zepp 登录凭证失败，请稍后使用已有账号登录。")
        tokens = body.get("token_info", {})
        if not tokens.get("app_token") or not tokens.get("user_id"):
            raise ProtocolError("登录响应缺少认证信息。")
        host = "api-mifit.zepp.com"
        for domain in body.get("domains", []):
            if domain.get("host") == "api-mifit.zepp.com" and domain.get("cnames"):
                host = domain["cnames"][0]
        if not safe_host(host):
            raise ProtocolError("登录响应中的区域地址不合法。")
        third = body.get("thirdparty_info") or {}
        return {
            "app_token": tokens["app_token"],
            "login_token": tokens.get("login_token", ""),
            "user_id": str(tokens["user_id"]),
            "host": host,
            "third_id": str(third.get("third_id") or ""),
        }

    def api(self, state, path, params):
        if not safe_host(state.get("host")):
            raise ProtocolError("账号区域配置不合法。")
        response = self.request(
            "GET", "https://" + state["host"] + path,
            params=dict(params, r=str(uuid.uuid4()), userid=state["user_id"]),
            headers=self.headers(state)
        )
        try:
            body = response.json()
        except ValueError:
            body = None
        if response.status_code != 200 or not isinstance(body, dict):
            raise ProtocolError(f"接口响应异常（HTTP {response.status_code}）。")
        if body.get("code") not in (0, 1) or body.get("message") not in ("success", "ok", None):
            raise ProtocolError("接口返回业务错误，请重新登录后再试。")
        return body.get("data")

    def binding(self, state):
        data = self.api(
            state, "/v1/weixin/user/infos.json",
            {"appname": APP, "thirdPartyId": "()"}
        )
        if not isinstance(data, dict) or type(data.get("isbind")) is not bool:
            raise ProtocolError("微信接口没有返回明确的绑定状态。")
        return {"bound": data["isbind"], "nickname": str(data.get("nickname") or "")}

    def qr(self, state):
        if not state.get("third_id"):
            raise ProtocolError("账号缺少第三方标识，请重新登录。")
        device = f"mifit_{state['user_id']}_huami_{state['third_id']}_thirdpartyId"
        data = self.api(
            state, "/huami.health.createwxqr.json",
            {"deviceid": device, "thirdPartyId": state["third_id"]}
        )
        value = data.get("list") if isinstance(data, dict) else None
        parsed = urlsplit(value or "")
        if parsed.hostname != "we.qq.com" or parsed.scheme not in ("http", "https"):
            raise ProtocolError("微信接口没有返回有效的绑定二维码。")
        return value
