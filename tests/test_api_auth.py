"""接口级安全回归：token 鉴权、横向越权拦截、登录限速、退出作废凭证。"""
import os
import tempfile

os.environ["MAPAOPAO_DB_URL"] = "sqlite:///" + os.path.join(
    tempfile.gettempdir(), "mapaopao_api_auth_test.db")

import pytest
from fastapi.testclient import TestClient

from server import db, main
from server.main import app

db.init_db()
c = TestClient(app)


@pytest.fixture(autouse=True)
def _isolate():
    """每个用例独立：开启鉴权、清限速窗口、重置数据库。"""
    main.AUTH_ENABLED = True
    main._attempts.clear()
    db.Base.metadata.drop_all(db.engine)
    db.Base.metadata.create_all(db.engine)
    yield
    main.AUTH_ENABLED = False


def _register(username: str):
    r = c.post("/v1/account/register", json={"username": username, "password": "run12345"})
    assert r.status_code == 200, r.text
    j = r.json()
    return j["user_id"], j["token"]


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_missing_and_bad_token_rejected():
    uid, token = _register("auther")
    # 不带 token → 401
    r = c.get(f"/v1/today?user_id={uid}")
    assert r.status_code == 401
    # 伪造 token → 401
    r = c.get(f"/v1/today?user_id={uid}", headers=_auth("forged-token-value"))
    assert r.status_code == 401
    # 合法 token 可通过鉴权（业务层报什么不重要，只要不是 401/403）
    r = c.post("/v1/user/city", json={"user_id": uid, "city": "成都", "lat": 30.57, "lon": 104.07},
               headers=_auth(token))
    assert r.status_code == 200, r.text


def test_cross_user_blocked():
    uid1, token1 = _register("alice01")
    uid2, _ = _register("bob0001")
    # query 里的 user_id 与 token 归属不一致 → 403
    r = c.get(f"/v1/today?user_id={uid2}", headers=_auth(token1))
    assert r.status_code == 403
    # JSON body 里的 user_id 不一致 → 403（同时验证中间件读后 body 仍能到端点）
    r = c.post("/v1/user/city", json={"user_id": uid2, "city": "成都", "lat": 30.57, "lon": 104.07},
               headers=_auth(token1))
    assert r.status_code == 403
    # 一致 → 放行
    r = c.post("/v1/user/city", json={"user_id": uid1, "city": "成都", "lat": 30.57, "lon": 104.07},
               headers=_auth(token1))
    assert r.status_code == 200


def test_login_rate_limit():
    _register("ratelim")
    results = [c.post("/v1/account/login", json={"username": "ratelim", "password": "wrong66"})
               for _ in range(11)]
    assert all(r.status_code == 401 for r in results[:10]), "前 10 次应正常返回 401"
    assert results[10].status_code == 429, "第 11 次应触发限速"
    # 换个用户名不受牵连
    _register("otheru1")
    r = c.post("/v1/account/login", json={"username": "otheru1", "password": "run12345"})
    assert r.status_code == 200


def test_login_unified_401_no_enumeration():
    """账号不存在和密码错误都返回 401，响应不区分两者。"""
    _register("enumchk")
    r1 = c.post("/v1/account/login", json={"username": "no_such_user", "password": "run12345"})
    r2 = c.post("/v1/account/login", json={"username": "enumchk", "password": "wrong66"})
    assert r1.status_code == 401 and r2.status_code == 401
    assert r1.json() == r2.json(), "两种失败响应必须一致"


def test_logout_invalidates_token():
    uid, token = _register("logoutu")
    r = c.post("/v1/account/logout", headers=_auth(token))
    assert r.status_code == 200
    r = c.get(f"/v1/today?user_id={uid}", headers=_auth(token))
    assert r.status_code == 401, "退出后旧 token 必须失效"


def test_login_rotates_token():
    _, token1 = _register("rotate1")
    r = c.post("/v1/account/login", json={"username": "rotate1", "password": "run12345"})
    token2 = r.json()["token"]
    assert token2 != token1
    r = c.get("/v1/today?user_id=x", headers=_auth(token1))
    assert r.status_code == 401, "重新登录后旧 token 必须作废"
