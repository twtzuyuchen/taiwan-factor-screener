#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
台股五因子選股器 — 資料建置腳本

在 GitHub Actions（伺服器端）執行，不在瀏覽器執行，所以完全不受 CORS 限制。
流程：
  1. 從 TWSE / TPEx 官方 Open API 取得全市場最新一日快照（收盤價、成交金額、
     本益比、股價淨值比、殖利率）——免金鑰。
  2. 依成交金額篩出流動性前 N 名做為選股池。
  3. 對選股池內每一檔股票，逐檔向 FinMind API 查詢（帶 data_id，免費方案可用）：
       - 歷史股價 → 動能、波動度
       - 市值（查不到則以「收盤價 × 成交金額」做流動性代理值）
       - 財報 EPS → ROE、EPS 穩定度
       - 資產負債表 → 負債比
  4. 把每檔股票的「原始數值」（不是加權後的合成分數）寫進
     data/factors_latest.json。Z-Score 標準化與五因子加權，交給前端 JS
     即時計算，這樣使用者調整權重滑桿或篩選條件時不需要重新抓資料。

用法：
  FINMIND_TOKEN=xxx python scripts/build_factors.py \
      --universe-size 200 --momentum-lookback 120 --vol-lookback 60 \
      --market both --output data/factors_latest.json
"""

import argparse
import json
import math
import os
import sys
import time
from datetime import datetime, timedelta, timezone

import requests

TWSE_STOCK_DAY_ALL = "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"
TWSE_BWIBBU_D = "https://openapi.twse.com.tw/v1/exchangeReport/BWIBBU_d"
TPEX_DAILY_CLOSE = "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes"
TPEX_PERATIO = "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_peratio_analysis"
FINMIND_API = "https://api.finmindtrade.com/api/v4/data"

# 候選欄位名稱（依官方文件推斷；欄位名稱如有變動，改這裡即可，
# 不用大改程式邏輯）
TWSE_FIELDS = {
    "code": ["Code", "證券代號"],
    "name": ["Name", "證券名稱"],
    "close": ["ClosingPrice", "收盤價"],
    "trade_value": ["TradeValue", "成交金額"],
    "per": ["PEratio", "PERatio", "PriceEarningRatio", "本益比"],
    "pbr": ["PBratio", "PBRatio", "PriceBookRatio", "股價淨值比"],
    "yield": ["DividendYield", "殖利率(%)", "殖利率"],
}
TPEX_FIELDS = {
    "code": ["SecuritiesCompanyCode", "Code", "代號"],
    "name": ["CompanyName", "Name", "名稱"],
    "close": ["Close", "收盤價"],
    "trade_value": ["TradingMoney", "TransactionAmount", "成交金額"],
    "per": ["PriceEarningRatio", "PEratio", "本益比"],
    "pbr": ["PriceBookRatio", "PBratio", "股價淨值比"],
    "yield": ["DividendYieldRatio", "DividendYield", "殖利率"],
}


def log(msg):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def pick(row, candidates):
    for k in candidates:
        v = row.get(k)
        if v not in (None, ""):
            return v
    return None


def to_num(v):
    if v is None:
        return None
    try:
        return float(str(v).replace(",", "").strip())
    except (ValueError, TypeError):
        return None


def http_get_json(url, session, retries=3, timeout=30):
    last_err = None
    for attempt in range(1, retries + 1):
        try:
            resp = session.get(url, timeout=timeout)
            if resp.status_code == 429:
                log(f"⏳ 速率限制，等待 5 秒後重試：{url}")
                time.sleep(5)
                continue
            resp.raise_for_status()
            return resp.json()
        except Exception as e:  # noqa: BLE001
            last_err = e
            log(f"⚠ 請求失敗（第 {attempt} 次）：{url} — {e}")
            time.sleep(1.5)
    log(f"❌ 放棄：{url} — {last_err}")
    return None


def fetch_twse_snapshot(session):
    price_rows = http_get_json(TWSE_STOCK_DAY_ALL, session) or []
    per_rows = http_get_json(TWSE_BWIBBU_D, session) or []
    per_map = {}
    for r in per_rows:
        code = pick(r, TWSE_FIELDS["code"])
        if not code:
            continue
        per_map[code] = {
            "per": to_num(pick(r, TWSE_FIELDS["per"])),
            "pbr": to_num(pick(r, TWSE_FIELDS["pbr"])),
            "yield": to_num(pick(r, TWSE_FIELDS["yield"])),
        }
    out = []
    for r in price_rows:
        code = pick(r, TWSE_FIELDS["code"])
        close = to_num(pick(r, TWSE_FIELDS["close"]))
        if not code or not close or close <= 0:
            continue
        p = per_map.get(code, {})
        out.append({
            "stock_id": code,
            "name": pick(r, TWSE_FIELDS["name"]),
            "market": "TWSE",
            "close": close,
            "trade_value": to_num(pick(r, TWSE_FIELDS["trade_value"])) or 0,
            "per": p.get("per"),
            "pbr": p.get("pbr"),
            "div_yield": p.get("yield"),
        })
    return out


def fetch_tpex_snapshot(session):
    price_rows = http_get_json(TPEX_DAILY_CLOSE, session) or []
    per_rows = http_get_json(TPEX_PERATIO, session) or []
    per_map = {}
    for r in per_rows:
        code = pick(r, TPEX_FIELDS["code"])
        if not code:
            continue
        per_map[code] = {
            "per": to_num(pick(r, TPEX_FIELDS["per"])),
            "pbr": to_num(pick(r, TPEX_FIELDS["pbr"])),
            "yield": to_num(pick(r, TPEX_FIELDS["yield"])),
        }
    out = []
    for r in price_rows:
        code = pick(r, TPEX_FIELDS["code"])
        close = to_num(pick(r, TPEX_FIELDS["close"]))
        if not code or not close or close <= 0:
            continue
        p = per_map.get(code, {})
        out.append({
            "stock_id": code,
            "name": pick(r, TPEX_FIELDS["name"]),
            "market": "TPEx",
            "close": close,
            "trade_value": to_num(pick(r, TPEX_FIELDS["trade_value"])) or 0,
            "per": p.get("per"),
            "pbr": p.get("pbr"),
            "div_yield": p.get("yield"),
        })
    return out


class FinMind:
    def __init__(self, token, session, delay_sec=0.35):
        self.token = token
        self.session = session
        self.delay = delay_sec

    def get(self, dataset, **params):
        q = {"dataset": dataset, **{k: v for k, v in params.items() if v not in (None, "")}}
        if self.token:
            q["token"] = self.token
        try:
            resp = self.session.get(FINMIND_API, params=q, timeout=30)
            if resp.status_code == 429:
                log(f"⏳ FinMind 速率限制，等待 5 秒（{dataset} data_id={params.get('data_id')}）")
                time.sleep(5)
                resp = self.session.get(FINMIND_API, params=q, timeout=30)
            data = resp.json()
            if data.get("status") not in (200, None):
                log(f"⚠ FinMind {dataset} 回應 status={data.get('status')} msg={data.get('msg')}")
            return data.get("data", [])
        except Exception as e:  # noqa: BLE001
            log(f"⚠ FinMind 請求失敗 {dataset} data_id={params.get('data_id')}: {e}")
            return []
        finally:
            time.sleep(self.delay)


def mean(xs):
    return sum(xs) / len(xs) if xs else 0.0


def stdev(xs):
    if len(xs) < 2:
        return 0.0
    m = mean(xs)
    return math.sqrt(mean([(x - m) ** 2 for x in xs]))


def extract_eps_series(rows):
    out = []
    for r in rows:
        if str(r.get("type", "")).upper() == "EPS":
            try:
                out.append({"date": r["date"], "value": float(r["value"])})
            except (KeyError, ValueError, TypeError):
                continue
    out.sort(key=lambda r: r["date"])
    return out


def extract_balance_ratio(rows):
    liab_names = {"liabilities", "liabilitiestotal", "totalliabilities"}
    asset_names = {"totalassets", "assets", "assetstotal"}

    def find_val(names):
        candidates = [r for r in rows if str(r.get("type", "")).lower() in names]
        candidates.sort(key=lambda r: r.get("date", ""), reverse=True)
        if not candidates:
            return None
        try:
            return float(candidates[0]["value"])
        except (KeyError, ValueError, TypeError):
            return None

    liab = find_val(liab_names)
    assets = find_val(asset_names)
    if liab is None or assets in (None, 0):
        return None
    return liab / assets


def latest_market_value(rows):
    if not rows:
        return None
    sorted_rows = sorted(rows, key=lambda r: r.get("date", ""), reverse=True)
    v = sorted_rows[0].get("market_value", sorted_rows[0].get("MarketValue"))
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def build(args):
    session = requests.Session()
    session.headers.update({"User-Agent": "taiwan-factor-screener/1.0"})

    log("步驟 1/4：取得全市場快照（TWSE + TPEx 官方 Open API）...")
    universe = []
    if args.market in ("both", "twse"):
        twse = fetch_twse_snapshot(session)
        log(f"TWSE 上市：{len(twse)} 檔")
        universe += twse
    if args.market in ("both", "tpex"):
        tpex = fetch_tpex_snapshot(session)
        log(f"TPEx 上櫃：{len(tpex)} 檔")
        universe += tpex

    if not universe:
        log("❌ 無法取得任何全市場快照資料，中止。")
        sys.exit(1)

    log("步驟 2/4：依成交金額篩出流動性前段股票...")
    universe = [r for r in universe if r["close"] > 0]
    universe.sort(key=lambda r: r.get("trade_value") or 0, reverse=True)
    if args.exclude_loss:
        universe = [r for r in universe if r["per"] is None or r["per"] > 0]
    candidates = universe[: args.universe_size]
    log(f"篩出 {len(candidates)} 檔（目標 {args.universe_size} 檔）")

    log(f"步驟 3/4：逐檔向 FinMind 取得歷史股價 / 市值 / 財報（共 {len(candidates)} 檔）...")
    fm = FinMind(args.token, session, delay_sec=args.delay)

    today = datetime.now(timezone.utc).date()
    price_start = (today - timedelta(days=int(max(args.momentum_lookback, args.vol_lookback) * 1.6) + 10)).isoformat()
    fin_start = (today - timedelta(days=760)).isoformat()
    mv_start = (today - timedelta(days=30)).isoformat()
    end_date = today.isoformat()

    rows = []
    for i, c in enumerate(candidates, 1):
        if i % 25 == 0 or i == len(candidates):
            log(f"  進度 {i}/{len(candidates)}")

        price_hist = fm.get("TaiwanStockPrice", data_id=c["stock_id"], start_date=price_start, end_date=end_date)
        mv_hist = fm.get("TaiwanStockMarketValue", data_id=c["stock_id"], start_date=mv_start)
        fin_hist = fm.get("TaiwanStockFinancialStatements", data_id=c["stock_id"], start_date=fin_start)
        bal_hist = fm.get("TaiwanStockBalanceSheet", data_id=c["stock_id"], start_date=fin_start)

        closes = [float(r["close"]) for r in price_hist if to_num(r.get("close"))]
        closes = [v for v in closes if v > 0]
        if len(closes) < min(20, args.momentum_lookback) and args.exclude_thin:
            continue

        last_close = closes[-1] if closes else c["close"]
        mom_idx = max(0, len(closes) - 1 - args.momentum_lookback)
        momentum = (last_close / closes[mom_idx] - 1) if closes and len(closes) > mom_idx else None

        vol_slice = closes[-args.vol_lookback:] if closes else []
        daily_returns = [math.log(vol_slice[k] / vol_slice[k - 1]) for k in range(1, len(vol_slice)) if vol_slice[k - 1] > 0]
        volatility = stdev(daily_returns) * math.sqrt(252) if len(daily_returns) > 5 else None

        eps_series = extract_eps_series(fin_hist)
        eps_ttm = sum(e["value"] for e in eps_series[-4:])
        eps_vals = [e["value"] for e in eps_series[-8:]]
        eps_cv = (stdev(eps_vals) / (abs(mean(eps_vals)) or 1)) if len(eps_vals) >= 4 else None

        debt_ratio = extract_balance_ratio(bal_hist)

        per = c["per"]
        pbr = c["pbr"]
        div_yield = c["div_yield"]

        bvps = (last_close / pbr) if pbr and pbr > 0 else None
        roe = (eps_ttm / bvps) if (bvps and eps_series) else None

        market_value = latest_market_value(mv_hist)
        mv_available = market_value is not None
        if not mv_available:
            market_value = last_close * (c.get("trade_value") or 0)

        rows.append({
            "stock_id": c["stock_id"],
            "name": c.get("name") or "",
            "market": c["market"],
            "close": last_close,
            "per": per, "pbr": pbr, "div_yield": div_yield,
            "roe": roe, "debt_ratio": debt_ratio, "eps_cv": eps_cv,
            "momentum": momentum, "volatility": volatility,
            "market_value": market_value, "mv_available": mv_available,
        })

    log(f"步驟 4/4：寫出 {len(rows)} 筆到 {args.output} ...")
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "params": {
            "universe_size_requested": args.universe_size,
            "momentum_lookback": args.momentum_lookback,
            "vol_lookback": args.vol_lookback,
            "market": args.market,
            "exclude_loss": args.exclude_loss,
        },
        "note": "TWSE/TPEx 全市場快照僅提供產生當下最新一個交易日的資料，沒有回溯查詢。",
        "row_count": len(rows),
        "rows": rows,
    }
    os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
    log("完成。")


def parse_args():
    p = argparse.ArgumentParser(description="台股五因子選股器 — 資料建置腳本")
    p.add_argument("--token", default=os.environ.get("FINMIND_TOKEN", ""), help="FinMind API token（預設讀 FINMIND_TOKEN 環境變數）")
    p.add_argument("--universe-size", type=int, default=int(os.environ.get("UNIVERSE_SIZE", 200)))
    p.add_argument("--momentum-lookback", type=int, default=int(os.environ.get("MOMENTUM_LOOKBACK", 120)))
    p.add_argument("--vol-lookback", type=int, default=int(os.environ.get("VOL_LOOKBACK", 60)))
    p.add_argument("--market", choices=["both", "twse", "tpex"], default=os.environ.get("MARKET_SCOPE", "both"))
    p.add_argument("--delay", type=float, default=float(os.environ.get("REQUEST_DELAY", 0.35)), help="FinMind 每次請求間隔秒數")
    p.add_argument("--exclude-loss", action="store_true", default=os.environ.get("EXCLUDE_LOSS", "true").lower() == "true")
    p.add_argument("--exclude-thin", action="store_true", default=os.environ.get("EXCLUDE_THIN", "true").lower() == "true")
    p.add_argument("--output", default="data/factors_latest.json")
    return p.parse_args()


if __name__ == "__main__":
    build(parse_args())
