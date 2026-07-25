"""One-shot: create Phase 1 manual-test items and receive RAW-A / RAW-B into Main Store."""

from __future__ import annotations

import asyncio
from datetime import date

import httpx

BASE = "http://127.0.0.1:8000/api/v1"
EMAIL = "admin@example.com"
PASSWORD = "ChangeMe!123"

ITEMS = [
    ("RAW-A", "Phase1 Raw A", "50"),
    ("RAW-B", "Phase1 Raw B (alternate)", "50"),
    ("SCRAP-OFFCUT", "Phase1 Scrap Offcut", "0"),
    ("PHANTOM-KIT", "Phase1 Phantom Kit", "0"),
    ("SUB-ASSY", "Phase1 Stocked Sub-Assembly", "0"),
    ("FG-NESTED", "Phase1 Nested Finished Good", "0"),
    ("FG-TOP", "Phase1 Top FG (stocked sub)", "0"),
    ("FG-SCRAP", "Phase1 FG with Scrap", "0"),
    ("FG-ALT", "Phase1 FG with Alternate", "0"),
]

RECEIVE = {
    "RAW-A": ("200", "50"),
    "RAW-B": ("50", "50"),
}


async def main() -> None:
    async with httpx.AsyncClient(base_url=BASE, timeout=60.0) as c:
        r = await c.post("/auth/login", json={"email": EMAIL, "password": PASSWORD})
        r.raise_for_status()
        token = r.json()["access_token"]
        h = {"Authorization": f"Bearer {token}"}

        me = (await c.get("/auth/me", headers=h)).json()
        print("logged in", me.get("email"))

        whs = (await c.get("/warehouses", headers=h, params={"page_size": 100})).json()
        if isinstance(whs, dict):
            whs = whs["items"]
        main = next((w for w in whs if w["warehouse_name"] == "Main Store"), None)
        if main is None:
            r = await c.post("/warehouses", headers=h, json={"warehouse_name": "Main Store"})
            r.raise_for_status()
            main = r.json()
            print("created Main Store")
        else:
            print("Main Store ok")

        existing = (await c.get("/items", headers=h, params={"page_size": 200})).json()["items"]
        by_code = {i["item_code"]: i for i in existing}

        created: dict = {}
        for code, name, rate in ITEMS:
            if code in by_code:
                created[code] = by_code[code]
                print(f"exists {code}")
                continue
            r = await c.post(
                "/items",
                headers=h,
                json={
                    "item_code": code,
                    "item_name": name,
                    "standard_rate": rate,
                    "valuation_rate": rate,
                    "default_warehouse_id": main["id"],
                },
            )
            if r.status_code not in (200, 201):
                raise SystemExit(f"FAIL create {code}: {r.status_code} {r.text}")
            created[code] = r.json()
            print(f"created {code}")

        today = date.today().isoformat()
        for code, (qty, rate) in RECEIVE.items():
            item = created[code]
            bal = (
                await c.get(
                    "/reports/stock-balance",
                    headers=h,
                    params={"item_id": item["id"], "warehouse_id": main["id"]},
                )
            ).json()
            on_hand = float(bal[0]["actual_qty"]) if bal else 0.0
            need = float(qty) - on_hand
            if need <= 0:
                print(f"{code} already has {on_hand} — skip receive")
                continue
            r = await c.post(
                "/stock-entries",
                headers=h,
                json={
                    "purpose": "Material Receipt",
                    "posting_date": today,
                    "to_warehouse_id": main["id"],
                    "items": [{"item_id": item["id"], "qty": str(need), "basic_rate": rate}],
                },
            )
            r.raise_for_status()
            entry = r.json()
            r = await c.post(f"/stock-entries/{entry['id']}/submit", headers=h)
            r.raise_for_status()
            print(f"received {need:g} {code} @ {rate} → Main Store ({entry['name']})")

        print("\nStock in Main Store:")
        for code in RECEIVE:
            bal = (
                await c.get(
                    "/reports/stock-balance",
                    headers=h,
                    params={"item_id": created[code]["id"], "warehouse_id": main["id"]},
                )
            ).json()
            q = bal[0]["actual_qty"] if bal else "0"
            print(f"  {code}: {q}")


if __name__ == "__main__":
    asyncio.run(main())
