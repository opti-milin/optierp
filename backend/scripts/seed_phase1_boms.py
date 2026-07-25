"""Create Phase 1 manual-test BOMs (phantom kit + nested parent)."""

from __future__ import annotations

import asyncio

import httpx

BASE = "http://127.0.0.1:8000/api/v1"


async def main() -> None:
    async with httpx.AsyncClient(base_url=BASE, timeout=60.0) as c:
        r = await c.post(
            "/auth/login",
            json={"email": "admin@example.com", "password": "ChangeMe!123"},
        )
        r.raise_for_status()
        h = {"Authorization": f"Bearer {r.json()['access_token']}"}
        items = {
            i["item_code"]: i
            for i in (await c.get("/items", headers=h, params={"page_size": 200})).json()["items"]
        }
        for code in ("PHANTOM-KIT", "RAW-A", "FG-NESTED"):
            if code not in items:
                raise SystemExit(f"missing item {code}")

        # already have submitted phantom?
        boms = (await c.get("/boms", headers=h, params={"page_size": 100})).json()["items"]
        for b in boms:
            detail = (await c.get(f"/boms/{b['id']}", headers=h)).json()
            if detail["production_item_code"] == "PHANTOM-KIT" and detail["docstatus"] == 1:
                print("phantom already submitted:", detail["name"], "is_phantom=", detail.get("is_phantom"))
                phantom_id = detail["id"]
                break
        else:
            r = await c.post(
                "/boms",
                headers=h,
                json={
                    "production_item_id": items["PHANTOM-KIT"]["id"],
                    "quantity": "1",
                    "is_default": True,
                    "is_phantom": True,
                    "operating_cost": "0",
                    "items": [{"item_id": items["RAW-A"]["id"], "qty": "2"}],
                },
            )
            r.raise_for_status()
            phantom = r.json()
            r = await c.post(f"/boms/{phantom['id']}/submit", headers=h)
            r.raise_for_status()
            phantom_id = phantom["id"]
            print("phantom submitted:", phantom["name"], "is_phantom=True")

        for b in boms:
            detail = (await c.get(f"/boms/{b['id']}", headers=h)).json()
            if detail["production_item_code"] == "FG-NESTED" and detail["docstatus"] == 1:
                print(
                    "parent already submitted:",
                    detail["name"],
                    "rm=",
                    detail["raw_material_cost"],
                    "total=",
                    detail["total_cost"],
                )
                break
        else:
            r = await c.post(
                "/boms",
                headers=h,
                json={
                    "production_item_id": items["FG-NESTED"]["id"],
                    "quantity": "1",
                    "is_default": True,
                    "is_phantom": False,
                    "operating_cost": "10",
                    "items": [{"item_id": items["PHANTOM-KIT"]["id"], "qty": "1"}],
                },
            )
            print("parent create", r.status_code, r.text[:400])
            r.raise_for_status()
            parent = r.json()
            r = await c.post(f"/boms/{parent['id']}/submit", headers=h)
            r.raise_for_status()
            print(
                "parent submitted:",
                parent["name"],
                "rm=",
                parent["raw_material_cost"],
                "total=",
                parent["total_cost"],
            )

        _ = phantom_id
        print("DONE — continue from Work Order on FG-NESTED BOM")


if __name__ == "__main__":
    asyncio.run(main())
