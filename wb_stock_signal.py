import argparse
import os
import sys
import time
from datetime import datetime, timedelta

import pandas as pd
import requests

STATS_URL = "https://statistics-api.wildberries.ru/api/v1/supplier"

EXCLUDED_WAREHOUSES = set()

URGENT_DAYS = 7
PREPARE_DAYS = 14
SALES_WINDOW_DAYS = 14

RED = "Грузить срочно"
YELLOW = "Готовить поставку"
GREEN = "Норма"


def api_get(path, token, params, retries=5):
    headers = {"Authorization": token}
    for attempt in range(retries):
        r = requests.get(f"{STATS_URL}/{path}", headers=headers, params=params, timeout=60)
        if r.status_code == 429:
            time.sleep(65)
            continue
        r.raise_for_status()
        return r.json()
    raise RuntimeError(f"Too many retries for {path}")


def load_stocks(token):
    date_from = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    data = api_get("stocks", token, {"dateFrom": date_from})
    df = pd.DataFrame(data)
    if df.empty:
        return df
    if EXCLUDED_WAREHOUSES:
        df = df[~df["warehouseName"].isin(EXCLUDED_WAREHOUSES)]
    return df


def load_sales(token, days):
    date_from = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    data = api_get("sales", token, {"dateFrom": date_from, "flag": 0})
    df = pd.DataFrame(data)
    if df.empty:
        return df
    df = df[~df["isCancel"]] if "isCancel" in df.columns else df
    if EXCLUDED_WAREHOUSES:
        df = df[~df["warehouseName"].isin(EXCLUDED_WAREHOUSES)]
    return df


def classify(days_left, urgent, prepare):
    if days_left <= urgent:
        return RED
    if days_left <= prepare:
        return YELLOW
    return GREEN


def build_report(token, cabinet, days, urgent, prepare):
    stocks = load_stocks(token)
    time.sleep(61)
    sales = load_sales(token, days)

    if stocks.empty:
        return pd.DataFrame()

    stock_agg = (
        stocks.groupby(["supplierArticle", "nmId"], as_index=False)
        .agg(stock=("quantity", "sum"), in_way_to=("inWayToClient", "sum"), in_way_from=("inWayFromClient", "sum"))
    )

    if sales.empty:
        sales_agg = pd.DataFrame(columns=["supplierArticle", "sold"])
    else:
        sales_agg = sales.groupby("supplierArticle", as_index=False).agg(sold=("nmId", "count"))

    df = stock_agg.merge(sales_agg, on="supplierArticle", how="left")
    df["sold"] = df["sold"].fillna(0).astype(int)
    df["per_day"] = (df["sold"] / days).round(2)
    df["days_left"] = df.apply(
        lambda r: round(r["stock"] / r["per_day"], 1) if r["per_day"] > 0 else 999.0, axis=1
    )
    df["signal"] = df["days_left"].apply(lambda d: classify(d, urgent, prepare))
    df["cabinet"] = cabinet

    order = {RED: 0, YELLOW: 1, GREEN: 2}
    df["_o"] = df["signal"].map(order)
    df = df.sort_values(["_o", "days_left"]).drop(columns="_o").reset_index(drop=True)
    return df[["cabinet", "supplierArticle", "nmId", "stock", "in_way_to", "in_way_from", "sold", "per_day", "days_left", "signal"]]


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--days", type=int, default=SALES_WINDOW_DAYS)
    p.add_argument("--urgent", type=int, default=URGENT_DAYS)
    p.add_argument("--prepare", type=int, default=PREPARE_DAYS)
    p.add_argument("--out", default=f"wb_stock_signal_{datetime.now():%Y-%m-%d}.xlsx")
    args = p.parse_args()

    tokens = {}
    for i in (1, 2):
        t = os.environ.get(f"WB_TOKEN_{i}")
        if t:
            tokens[f"cabinet_{i}"] = t
    if not tokens:
        sys.exit("Set WB_TOKEN_1 (and optionally WB_TOKEN_2)")

    frames = []
    for name, token in tokens.items():
        df = build_report(token, name, args.days, args.urgent, args.prepare)
        if not df.empty:
            frames.append(df)
        time.sleep(61)

    if not frames:
        sys.exit("No data")

    result = pd.concat(frames, ignore_index=True)
    result.to_excel(args.out, index=False)
    print(result["signal"].value_counts().to_string())
    print(f"Saved: {args.out}")


if __name__ == "__main__":
    main()
