"""设备查询、步数提交和支付宝授权。"""
import re
import time
import uuid
from datetime import datetime
from urllib.parse import parse_qsl, urlencode, urlsplit, quote
from zoneinfo import ZoneInfo

import requests

ALIPAY_HOST = "api-mifit-cn3.zepp.com"
ALIPAY_APP_ID = "2017030806118105"
ALIPAY_AUTH_PAGE = "https://authweb.alipay.com/auth"
TEMPLATE_URL = "https://raw.githubusercontent.com/TonyJiangWJ/mimotion/master/util/zepp_helper.py"
TIMEOUT = 20


def get_devices(client, state):
    response = client.request(
        "GET", "https://" + state["host"] + "/v1/device/binds.json",
        params={"userid": state["user_id"]},
        headers={"apptoken": state["app_token"], "User-Agent": "ZeppLife/6.14.0"}
    )
    if response.status_code != 200:
        raise RuntimeError(f"读取设备失败：HTTP {response.status_code}")
    body = response.json()
    items = body.get("items", []) if isinstance(body, dict) else []
    devices = []
    for item in items:
        if not isinstance(item, dict):
            continue
        device_id = str(item.get("deviceId") or item.get("mac") or "").replace(":", "").upper()
        name = str(item.get("productName") or item.get("name") or "Zepp 设备")
        wearable = item.get("deviceType") == 0 or any(
            word in name.lower() for word in ("band", "watch", "手环", "手表")
        )
        if device_id and wearable:
            devices.append({
                "device_id": device_id,
                "name": name,
                "device_type": item.get("deviceType"),
            })
    if not devices:
        raise RuntimeError("没有找到已绑定的手环或手表，请先在 Zepp Life 中绑定设备。")
    return devices


def get_template():
    try:
        response = requests.get(TEMPLATE_URL, timeout=TIMEOUT)
        response.raise_for_status()
    except requests.RequestException:
        raise RuntimeError("读取步数模板失败，请检查网络后重试。") from None
    match = re.search(
        r"""data_json\s*=\s*(['"])(%5B%7B%22data_hr%22.*?%5D)\1""",
        response.text, re.S
    )
    if not match:
        raise RuntimeError("步数模板格式发生变化，暂时无法提交。")
    return match.group(2)


def submit_steps(client, state, device_id, steps):
    if isinstance(steps, bool) or not isinstance(steps, int) or not 0 <= steps <= 200000:
        raise ValueError("步数请输入 0 到 200000 之间的整数。")
    device_id = str(device_id).replace(":", "").upper()
    if not re.fullmatch(r"[0-9A-F]{12,32}", device_id):
        raise ValueError("设备 ID 格式不正确。")

    data = get_template()
    today = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d")
    data, date_count = re.subn(
        r'(date%22%3A%22).*?(%22%2C%22data)',
        lambda m: m.group(1) + today + m.group(2), data, count=1
    )
    data, step_count = re.subn(
        r'(ttl%5C%22%3A).*?(%2C%5C%22dis)',
        lambda m: m.group(1) + str(steps) + m.group(2), data, count=1
    )
    # 模板中的设备标识只替换成当前账号实际绑定的设备 ID。
    data, device_count = re.subn(
        r'(did%22%3A%22)[0-9A-Fa-f]{12,32}',
        lambda m: m.group(1) + device_id, data, count=1
    )
    if (date_count, step_count, device_count) != (1, 1, 1):
        raise RuntimeError("步数模板字段不完整，未提交数据。")

    body = (
        f"userid={state['user_id']}&last_sync_data_time=1597306380"
        f"&device_type=0&last_deviceid={device_id}&data_json={data}"
    )
    response = client.request(
        "POST", "https://" + state["host"] + "/v1/data/band_data.json",
        params={"t": str(round(time.time() * 1000)), "r": str(uuid.uuid4())},
        data=body,
        headers={"apptoken": state["app_token"],
                 "Content-Type": "application/x-www-form-urlencoded"}
    )
    if response.status_code != 200:
        raise RuntimeError(f"提交失败：HTTP {response.status_code}")
    try:
        result = response.json()
    except ValueError:
        raise RuntimeError("提交接口返回了无法识别的数据。") from None
    if result.get("message") != "success":
        raise RuntimeError(f"提交失败：{result.get('message') or result}")
    return result


def alipay_status(client, state):
    response = client.request(
        "GET", f"https://{ALIPAY_HOST}/thirdParties/oauth2/list",
        params={"r": str(uuid.uuid4()), "userid": state["user_id"]},
        headers=client.headers(state)
    )
    if response.status_code != 200:
        raise RuntimeError(f"查询支付宝状态失败：HTTP {response.status_code}")
    item = response.json().get(ALIPAY_APP_ID) or {}
    return {"bound": item.get("state") == 1, "state": item.get("state")}


def alipay_authorization_url(client, state):
    response = client.request(
        "GET", f"https://{ALIPAY_HOST}/v1/thirdParties/auth.json",
        params={"r": str(uuid.uuid4()), "userid": state["user_id"]},
        headers=client.headers(state)
    )
    if response.status_code != 200:
        raise RuntimeError(f"获取支付宝授权入口失败：HTTP {response.status_code}")
    body = response.json()
    if body.get("code") != 1:
        raise RuntimeError(f"获取支付宝授权入口失败：{body.get('message') or '未知错误'}")
    auth_info = (body.get("data") or {}).get("authInfo")
    if not auth_info:
        raise RuntimeError("支付宝接口没有返回授权参数。")
    params = dict(parse_qsl(auth_info, keep_blank_values=True))
    params.setdefault("callbackScheme", f"alp{params.get('app_id', ALIPAY_APP_ID)}")
    url = ALIPAY_AUTH_PAGE + "?v=h5&" + urlencode(params)
    if urlsplit(url).hostname != "authweb.alipay.com":
        raise RuntimeError("支付宝授权地址校验失败。")
    scheme = "alipays://platformapi/startApp?appId=20000067&scheme=" + quote(url, safe="")
    return {"url": url, "scheme": scheme}
