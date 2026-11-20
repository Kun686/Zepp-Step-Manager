"""本地运行的 Zepp Life 网页助手。"""
import base64
import hashlib
import hmac
import io
import json
import os
import re
import secrets
import sqlite3
import threading
import time
from contextlib import closing
from pathlib import Path

import qrcode
from cryptography.fernet import Fernet
from flask import Flask, jsonify, request, session, render_template_string
from waitress import serve

from zepp_client import ZeppClient, ProtocolError, AuthExpired
from features import get_devices, submit_steps, alipay_status, alipay_authorization_url
from ui import PAGE


def create_app(data_dir=None, client_factory=ZeppClient):
    root = Path(data_dir or os.getenv("REG_DATA_DIR") or Path(__file__).parent / "instance")
    root.mkdir(parents=True, exist_ok=True)
    key_file = root / "credential.key"
    if not key_file.exists():
        with key_file.open("xb") as file:
            file.write(Fernet.generate_key())
        key_file.chmod(0o600)
    cipher = Fernet(key_file.read_bytes().strip())
    db_path = root / "accounts.sqlite3"
    with closing(sqlite3.connect(db_path)) as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS accounts "
            "(id INTEGER PRIMARY KEY, account TEXT UNIQUE NOT NULL, secret BLOB NOT NULL, updated REAL NOT NULL)"
        )
        conn.commit()

    app = Flask(__name__)
    app.config.update(
        SECRET_KEY=hashlib.sha256(key_file.read_bytes() + b"web-session").digest(),
        MAX_CONTENT_LENGTH=16384,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Strict",
        SESSION_COOKIE_NAME="zepp_step_manager",
        SESSION_COOKIE_SECURE=os.getenv("REG_HTTPS") == "1",
    )
    admin_password = os.getenv("REG_ADMIN_PASSWORD", "")
    lock = threading.RLock()
    challenges = {}
    failed_unlocks = {}

    def save(account, state):
        encrypted = cipher.encrypt(json.dumps(state).encode("utf-8"))
        with closing(sqlite3.connect(db_path)) as conn:
            conn.execute(
                "INSERT INTO accounts(account,secret,updated) VALUES(?,?,?) "
                "ON CONFLICT(account) DO UPDATE SET secret=excluded.secret,updated=excluded.updated",
                (account, encrypted, time.time()),
            )
            conn.commit()
            return conn.execute("SELECT id FROM accounts WHERE account=?", (account,)).fetchone()[0]

    def read(account_id):
        with closing(sqlite3.connect(db_path)) as conn:
            row = conn.execute(
                "SELECT account,secret FROM accounts WHERE id=?", (account_id,)
            ).fetchone()
        if not row:
            raise ProtocolError("账号不存在。")
        return row[0], json.loads(cipher.decrypt(row[1]))

    def refresh(client, account, state):
        password = state.get("password")
        if not password:
            raise AuthExpired("登录状态已失效，请重新登录。")
        access = client.access(account, password)
        state = dict(client.exchange(access, False, account), password=password)
        save(account, state)
        return state

    @app.before_request
    def guard():
        if "csrf" not in session:
            session["csrf"] = secrets.token_urlsafe(32)
        if not request.path.startswith("/api/"):
            return
        if request.method == "POST" and not hmac.compare_digest(
            request.headers.get("X-CSRF-Token", ""), session["csrf"]
        ):
            return jsonify(error="页面已过期，请刷新后重试。"), 403
        if request.path != "/api/unlock" and admin_password and not session.get("authorized"):
            return jsonify(error="请先输入管理密码。"), 401

    @app.after_request
    def private_response(response):
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    @app.errorhandler(ProtocolError)
    def protocol_error(exc):
        return jsonify(error=str(exc)), 400

    @app.errorhandler(RuntimeError)
    def runtime_error(exc):
        return jsonify(error=str(exc)), 400

    @app.get("/")
    def index():
        return render_template_string(
            PAGE, csrf=session["csrf"],
            locked=bool(admin_password and not session.get("authorized"))
        )

    @app.post("/api/unlock")
    def unlock():
        address = request.remote_addr or "unknown"
        now = time.time()
        attempts = [t for t in failed_unlocks.get(address, []) if now - t < 300]
        if len(attempts) >= 10:
            return jsonify(error="尝试次数过多，请五分钟后再试。"), 429
        supplied = str((request.get_json(silent=True) or {}).get("password", ""))
        if not hmac.compare_digest(supplied.encode(), admin_password.encode()):
            failed_unlocks[address] = attempts + [now]
            return jsonify(error="管理密码错误。"), 401
        failed_unlocks.pop(address, None)
        session["authorized"] = True
        return jsonify(ok=True)

    @app.get("/api/accounts")
    def accounts():
        with closing(sqlite3.connect(db_path)) as conn:
            rows = conn.execute(
                "SELECT id,account,updated FROM accounts ORDER BY updated DESC"
            ).fetchall()
        return jsonify(accounts=[{"id": row[0], "account": row[1], "updated": row[2]} for row in rows])

    @app.post("/api/captcha")
    def captcha():
        with lock:
            now = time.time()
            for sid in list(challenges):
                if now - challenges[sid]["created"] > 600:
                    challenges.pop(sid)["client"].close()
            sid = session["csrf"]
            previous = challenges.pop(sid, None)
            if previous:
                previous["client"].close()
            if len(challenges) >= 64:
                raise ProtocolError("验证码请求较多，请稍后再试。")
            client = client_factory()
            try:
                key, image = client.captcha()
            except Exception:
                client.close()
                raise
            challenge_id = secrets.token_urlsafe(24)
            challenges[sid] = {
                "client": client, "key": key, "id": challenge_id, "created": now
            }
        mime = "image/png" if image.startswith(b"\x89PNG") else "image/jpeg"
        return jsonify(
            image="data:" + mime + ";base64," + base64.b64encode(image).decode(),
            challenge_id=challenge_id
        )

    @app.post("/api/account")
    def account_action():
        payload = request.get_json(silent=True) or {}
        account = str(payload.get("account", "")).strip()
        password = str(payload.get("password", ""))
        registration = payload.get("mode") == "register"
        if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", account) or len(account) > 254:
            raise ProtocolError("请输入有效的 Zepp 邮箱账号。当前注册流程使用邮箱和图片验证码。")
        if not 8 <= len(password) <= 128:
            raise ProtocolError("密码长度需为 8 到 128 位。")
        with lock:
            challenge = None
            if registration:
                challenge = challenges.get(session["csrf"])
                if (
                    not challenge
                    or challenge["id"] != payload.get("challenge_id")
                    or time.time() - challenge["created"] > 600
                ):
                    raise ProtocolError("验证码已失效，请刷新图片后重新输入。")
                code = str(payload.get("code", "")).strip()
                if not code or len(code) > 16:
                    raise ProtocolError("请输入图片验证码。")
                challenges.pop(session["csrf"])
                client = challenge["client"]
            else:
                client = client_factory()
            try:
                access = client.access(
                    account, password,
                    challenge["key"] if challenge else None,
                    code if registration else None
                )
                if registration:
                    # 先保存注册成功凭据，避免换取 Token 失败后用户重复注册。
                    account_id = save(account, {"pending_access": access, "password": password})
                try:
                    state = client.exchange(access, registration, account)
                except ProtocolError:
                    if registration:
                        return jsonify(
                            id=account_id, created=True,
                            warning="账号可能已经注册成功，但登录凭证获取失败。请切换到已有账号登录，不要重复注册。"
                        )
                    raise
                state["password"] = password
                account_id = save(account, state)
                return jsonify(id=account_id, created=registration)
            finally:
                client.close()

    @app.post("/api/accounts/<int:account_id>/wechat")
    def wechat(account_id):
        want_qr = bool((request.get_json(silent=True) or {}).get("qr"))
        with lock:
            account, state = read(account_id)
            client = client_factory()
            try:
                if not state.get("app_token"):
                    state = refresh(client, account, state)
                try:
                    result = client.binding(state)
                except AuthExpired:
                    state = refresh(client, account, state)
                    result = client.binding(state)
                if want_qr and not result["bound"]:
                    value = client.qr(state)
                    image = qrcode.make(value)
                    output = io.BytesIO()
                    image.save(output, format="PNG")
                    result["qr_image"] = (
                        "data:image/png;base64," + base64.b64encode(output.getvalue()).decode()
                    )
                return jsonify(result)
            finally:
                client.close()

    @app.get("/api/accounts/<int:account_id>/devices")
    def devices(account_id):
        with lock:
            account, state = read(account_id)
            client = client_factory()
            try:
                if not state.get("app_token") or not state.get("host"):
                    state = refresh(client, account, state)
                try:
                    result = get_devices(client, state)
                except AuthExpired:
                    state = refresh(client, account, state)
                    result = get_devices(client, state)
                return jsonify(devices=result)
            finally:
                client.close()

    @app.post("/api/accounts/<int:account_id>/steps")
    def steps(account_id):
        payload = request.get_json(silent=True) or {}
        try:
            count = int(payload.get("steps"))
        except (TypeError, ValueError):
            raise ProtocolError("步数请输入整数。")
        device_id = str(payload.get("device_id") or "")
        with lock:
            account, state = read(account_id)
            client = client_factory()
            try:
                if not state.get("app_token") or not state.get("host"):
                    state = refresh(client, account, state)
                try:
                    result = submit_steps(client, state, device_id, count)
                except AuthExpired:
                    state = refresh(client, account, state)
                    result = submit_steps(client, state, device_id, count)
                return jsonify(ok=True, steps=count, message=result.get("message", "success"))
            finally:
                client.close()

    @app.post("/api/accounts/<int:account_id>/alipay")
    def alipay(account_id):
        action = str((request.get_json(silent=True) or {}).get("action") or "status")
        if action not in ("status", "authorize"):
            raise ProtocolError("不支持的支付宝操作。")
        with lock:
            account, state = read(account_id)
            client = client_factory()
            try:
                if not state.get("app_token") or not state.get("host"):
                    state = refresh(client, account, state)
                try:
                    if action == "status":
                        return jsonify(alipay_status(client, state))
                    return jsonify(**alipay_authorization_url(client, state))
                except AuthExpired:
                    state = refresh(client, account, state)
                    if action == "status":
                        return jsonify(alipay_status(client, state))
                    return jsonify(**alipay_authorization_url(client, state))
            finally:
                client.close()

    return app


if __name__ == "__main__":
    host = os.getenv("REG_HOST", "127.0.0.1")
    port = int(os.getenv("REG_PORT", "8010"))
    if host not in ("127.0.0.1", "localhost", "::1") and len(os.getenv("REG_ADMIN_PASSWORD", "")) < 12:
        raise SystemExit("对外监听前请设置至少 12 位的 REG_ADMIN_PASSWORD，并配置 HTTPS。")
    print(f"Zepp Step Manager: http://{host}:{port}", flush=True)
    serve(create_app(), host=host, port=port, threads=4)
