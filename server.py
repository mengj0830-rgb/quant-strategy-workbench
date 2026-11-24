# -*- coding: utf-8 -*-
"""
量化策略工作台 —— 本地离线版后端
================================
把云端托管的量化策略工作台（https://quant-workbench-89986.app.workbuddy.host）
本地化成一个完全离线、可双击启动的版本。

设计要点
--------
* 前端 index.html 是云端版本的原文件（仅改了 1 行：指数条标注「离线快照」）。
* 本服务把云端约 30 个 /api/* 接口桥接到【内嵌快照数据 data.json】：
  - 读类型接口（资讯、复盘、滚动验证、对比、推荐、适配、覆盖、指数、回测、
    模拟盘列表、自定义列表）直接返回快照里的真实数据；
  - 今日结论（/api/verdict）由内嵌的样本外适配度排名（advice.ranking）合成；
  - 模拟盘 / 自定义策略的增删改持久化到 store.json（浏览器 localStorage 之外
    的本地落盘，刷新不丢）；
  - 依赖云端计算引擎的功能（预测、决策解释、参数曲面、时间轴、冻结段、影子盘、
    相关性、自然语言建策略、截图建策略、刷新重算）返回清晰的「离线不支持」提示，
    而不是假装算出一个错的数。

启动： python server.py   （然后浏览器打开 http://localhost:8753/ ）
依赖： 仅 Python 标准库。
"""
import json
import os
import copy
import time
import math
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_PATH = os.path.join(HERE, "data.json")
STORE_PATH = os.path.join(HERE, "store.json")
PORT = 8753

with open(DATA_PATH, encoding="utf-8") as f:
    DATA = json.load(f)


def load_store():
    if os.path.exists(STORE_PATH):
        try:
            return json.load(open(STORE_PATH, encoding="utf-8"))
        except Exception:
            pass
    return {"paper": [], "removed_paper": [], "custom": [], "removed_custom": []}


def save_store(s):
    tmp = STORE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(s, f, ensure_ascii=False)
    os.replace(tmp, STORE_PATH)


STORE = load_store()

OFFLINE_ERR = {
    "ok": False,
    "error": "离线快照：此功能依赖云端计算引擎，本地化版本暂未内置（可查看内置快照数据，但不能实时重算）。",
}


# ----------------------------------------------------------------------------
# 今日结论：用内嵌的样本外适配度排名合成一个可读的排名
# 说明：云端版本的总分是「赚/稳/真」六维实时综合分；离线版没有那个引擎，这里用
# advice.ranking 的 score（样本外 edge 强度）归一化成一个「离线适配指数」，
# 并补上每条策略真实的内嵌回测指标（年化 / 回撤），实盘证据与影子盘校验离线不可用。
# ----------------------------------------------------------------------------
def build_verdict():
    advice = DATA.get("advice", {})
    ranks = list(advice.get("ranking", [])) + list(advice.get("alternatives", []))
    bt = DATA.get("backtests", {})
    meta = DATA.get("meta", {})
    scores = [r.get("score") for r in ranks if isinstance(r.get("score"), (int, float))]
    maxs = max(scores) if scores else 1.0

    def grade_of(total):
        if total >= 80:
            return "A", "优秀"
        if total >= 65:
            return "B", "良好"
        if total >= 50:
            return "C", "及格"
        return "D", "不合格"

    rows = []
    for i, r in enumerate(ranks):
        sc = r.get("score") or 0
        total = int(round(95.0 * sc / maxs)) if maxs else 0
        g, gn = grade_of(total)
        sid = r.get("strategy_id")
        b = bt.get(sid, {}) or {}
        params = b.get("params", {}) or {}
        param_str = " · ".join(f"{k}={v}" for k, v in params.items()) if params else ""
        phrase = r.get("significance") or (
            "样本外适配" if r.get("verdict") == "适配" else "样本外未通过多重比较校正"
        )
        rows.append({
            "rank": i + 1,
            "name": r.get("strategy_name"),
            "strategy_id": sid,
            "param_str": param_str,
            "total": total,
            "grade": g,
            "grade_name": gn,
            "phrase": phrase,
            "weights": {},
            "weight_live": 0,
            "bt": {
                "cagr": b.get("cagr"),
                "mdd": b.get("mdd"),
                "total": b.get("total"),
                "sharpe": b.get("sharpe"),
            },
            "live": {"days": 0, "total": 0},
            "score": sc,
            "verdict": r.get("verdict"),
        })

    return {
        "ok": True,
        "offline": True,
        "rows": rows,
        "integrity": {"ok": False, "accounts": 0, "with_gaps": 0, "missing_days": 0},
        "final": {"date": meta.get("data_end"), "days_left": 0},
        "evidence_days": 0,
        "evidence_target": 60,
        "n_tests": advice.get("n_tests"),
        "method": advice.get("method"),
        "disclaimer": advice.get("disclaimer"),
        "note": "离线快照：本排名由内嵌的样本外适配度（advice.ranking）归一化生成，并非云端实时六维综合分；实盘证据 / 影子盘校验离线不可用。",
    }


def find_custom(cid):
    for c in STORE.get("custom", []):
        if c.get("id") == cid:
            return c
    for c in DATA.get("custom", []):
        if c.get("id") == cid:
            return c
    return None


def ensure_custom(cid):
    for c in STORE.get("custom", []):
        if c.get("id") == cid:
            return c
    for c in DATA.get("custom", []):
        if c.get("id") == cid:
            nc = copy.deepcopy(c)
            STORE.setdefault("custom", []).append(nc)
            return nc
    return None


# ----------------------------------------------------------------------------
# 路由
# ----------------------------------------------------------------------------
def api_get(path):
    if path == "/api/ping":
        return {"ok": True, "local": True}
    if path == "/api/indices":
        return {
            "ok": True,
            "offline": True,
            "indices": DATA.get("indices", []),
            "trade_date": (DATA.get("meta") or {}).get("trade_date", ""),
        }
    if path == "/api/verdict":
        return build_verdict()
    if path == "/api/news":
        return {"ok": True, "digest": DATA.get("news", {})}
    if path == "/api/coverage":
        return DATA.get("coverage", {})
    if path == "/api/review":
        return {"ok": True, "review": DATA.get("review", {})}
    if path == "/api/ledger":
        return {"ok": True, "stats": (DATA.get("review") or {}).get("ledger", {})}
    if path == "/api/wf":
        wf = DATA.get("wf", {})
        result = list(wf.values())[0] if wf else {}
        return {"ok": True, "result": result}
    if path == "/api/advise":
        return DATA.get("advice", {})
    if path == "/api/advise/accounts":
        return {"ok": True, "items": []}
    if path == "/api/paper":
        removed = set(STORE.get("removed_paper", []))
        accts = [a for a in DATA.get("paper", []) if a.get("id") not in removed]
        accts += STORE.get("paper", [])
        return {"ok": True, "accounts": accts}
    # 云端计算类（只读）：离线不支持
    if path in ("/api/holdout", "/api/correlation", "/api/fleet"):
        return OFFLINE_ERR
    return OFFLINE_ERR


def api_post(path, body):
    body = body or {}
    if path == "/api/compare":
        return dict(ok=True, **DATA.get("compareDefault", {}))
    if path == "/api/recommend":
        return dict(ok=True, **DATA.get("recDefault", {}))
    if path == "/api/backtest":
        sid = body.get("strategy")
        bt = (DATA.get("backtests") or {}).get(sid)
        if bt:
            return dict(ok=True, offline=True,
                        note="离线快照：仅返回内置默认参数回测，自定义参数需云端引擎重算。",
                        **bt)
        return {"ok": False, "error": "该策略无内置回测快照"}
    if path == "/api/paper/create":
        return {"ok": False,
                "error": "离线快照：模拟盘需要云端重放引擎实时回放，暂仅可查看内置示例账户（2 个）。"}
    if path == "/api/paper/clear":
        STORE["paper"] = []
        STORE["removed_paper"] = []
        save_store(STORE)
        return {"ok": True}
    if path == "/api/paper/delete":
        did = body.get("id")
        STORE["paper"] = [a for a in STORE.get("paper", []) if a.get("id") != did]
        removed = STORE.setdefault("removed_paper", [])
        if did and did not in removed:
            removed.append(did)
        save_store(STORE)
        return {"ok": True}
    if path == "/api/custom":
        action = body.get("action")
        if action == "list":
            removed = set(STORE.get("removed_custom", []))
            items = [c for c in DATA.get("custom", []) if c.get("id") not in removed]
            items += STORE.get("custom", [])
            return {"ok": True, "items": items,
                    "template_sources": DATA.get("specCatalog", [])}
        if action == "duplicate":
            src = find_custom(body.get("id"))
            if not src:
                return {"ok": False, "error": "未找到源策略"}
            nc = copy.deepcopy(src)
            nc["id"] = src["id"] + "_cpy" + str(int(time.time()))[-6:]
            nc["name"] = src["name"] + "（副本）"
            STORE.setdefault("custom", []).append(nc)
            save_store(STORE)
            return {"ok": True}
        if action == "update":
            rec = ensure_custom(body.get("id"))
            if rec is None:
                return {"ok": False, "error": "未找到该策略"}
            rec.update(body.get("patch", {}) or {})
            save_store(STORE)
            return {"ok": True}
        if action == "toggle":
            rec = ensure_custom(body.get("id"))
            if rec is None:
                return {"ok": False, "error": "未找到该策略"}
            rec["enabled"] = body.get("enabled")
            save_store(STORE)
            return {"ok": True}
        if action == "delete":
            cid = body.get("id")
            STORE["custom"] = [c for c in STORE.get("custom", []) if c.get("id") != cid]
            removed = STORE.setdefault("removed_custom", [])
            if cid and cid not in removed:
                removed.append(cid)
            save_store(STORE)
            return {"ok": True}
        if action == "create":
            s = body.get("strategy")
            if not isinstance(s, dict):
                return {"ok": False, "error": "无效策略定义"}
            s = dict(s)
            s.setdefault("id", "cus_local_" + str(int(time.time()))[-6:])
            STORE.setdefault("custom", []).append(s)
            save_store(STORE)
            return {"ok": True}
        return {"ok": False, "error": "未知操作: " + str(action)}
    # 云端计算 / 写入类：离线不支持
    if path in ("/api/nl2strategy", "/api/screenshot", "/api/forecast",
                "/api/forecast/record",
                "/api/refresh/start", "/api/refresh/cancel", "/api/refresh/status",
                "/api/explain", "/api/surface", "/api/timeline"):
        return OFFLINE_ERR
    return OFFLINE_ERR


# ----------------------------------------------------------------------------
# HTTP 服务
# ----------------------------------------------------------------------------
class Handler(BaseHTTPRequestHandler):
    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if not n:
            return {}
        try:
            return json.loads(self.rfile.read(n).decode("utf-8"))
        except Exception:
            return {}

    def _send_json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        p = self.path.split("?")[0]
        if p in ("/", "/index.html"):
            try:
                with open(os.path.join(HERE, "index.html"), "rb") as f:
                    b = f.read()
            except Exception:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)
            return
        if p.startswith("/api/"):
            try:
                r = api_get(p)
            except Exception as e:
                r = {"ok": False, "error": "服务器错误: " + str(e)}
            self._send_json(r)
            return
        self.send_error(404)

    def do_POST(self):
        p = self.path.split("?")[0]
        if p.startswith("/api/"):
            try:
                r = api_post(p, self._body())
            except Exception as e:
                r = {"ok": False, "error": "服务器错误: " + str(e)}
            self._send_json(r)
            return
        self.send_error(404)

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print("量化策略工作台（本地离线版）已启动")
    print("在浏览器打开： http://localhost:%d/" % PORT)
    print("按 Ctrl+C 停止服务。")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n服务已停止。")
