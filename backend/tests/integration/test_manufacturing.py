"""Manufacturing module integration: BOM costing, Work Order → Manufacture, stock + GL
reconciliation, partial finish, material availability + shortfall MR, and cancel-reverts.

Runs against Postgres (skipped unless TEST_DATABASE_URL is set). The seeded company uses
perpetual inventory with an India COA, so the Manufacture Stock Entry posts real GL.
"""

import pytest

from tests.integration.conftest import coa_account

pytestmark = pytest.mark.asyncio

API = "/api/v1"


async def make_warehouse(client, headers, name: str) -> dict:
    resp = await client.post(f"{API}/warehouses", json={"warehouse_name": name}, headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()


async def make_item(client, headers, code: str, warehouse_id: str, **extra) -> dict:
    payload = {
        "item_code": code,
        "standard_rate": "0",
        "valuation_rate": "0",
        "default_warehouse_id": warehouse_id,
        **extra,
    }
    resp = await client.post(f"{API}/items", json=payload, headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()


async def receive(client, headers, item_id: str, warehouse_id: str, qty: str, rate: str, on: str):
    resp = await client.post(
        f"{API}/stock-entries",
        json={
            "purpose": "Material Receipt", "posting_date": on, "to_warehouse_id": warehouse_id,
            "items": [{"item_id": item_id, "qty": qty, "basic_rate": rate}],
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    entry = resp.json()
    resp = await client.post(f"{API}/stock-entries/{entry['id']}/submit", headers=headers)
    assert resp.status_code == 200, resp.text


async def stock_balance(client, headers, item_id: str, warehouse_id: str) -> dict | None:
    resp = await client.get(
        f"{API}/reports/stock-balance",
        params={"item_id": item_id, "warehouse_id": warehouse_id},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    rows = resp.json()
    return rows[0] if rows else None


async def gl_is_balanced(client, headers) -> bool:
    resp = await client.get(f"{API}/fiscal-years", headers=headers)
    fy = resp.json()["items"][0]
    resp = await client.get(
        f"{API}/reports/trial-balance", params={"fiscal_year_id": fy["id"]}, headers=headers
    )
    body = resp.json()
    rows = body["rows"] if isinstance(body, dict) else body
    leaves = [r for r in rows if not r.get("is_group")]
    total_dr = sum(float(r.get("debit", 0) or 0) for r in leaves)
    total_cr = sum(float(r.get("credit", 0) or 0) for r in leaves)
    return abs(total_dr - total_cr) < 0.01


async def on_hand(client, headers, item_id: str, warehouse_id: str) -> float:
    """Actual qty at a warehouse — 0 when the report omits the emptied bin."""
    bal = await stock_balance(client, headers, item_id, warehouse_id)
    return float(bal["actual_qty"]) if bal else 0.0


async def account_balance(client, headers, account_id: str) -> float:
    resp = await client.get(
        f"{API}/reports/general-ledger",
        params={"account_id": account_id, "from_date": "2026-01-01", "to_date": "2026-12-31"},
        headers=headers,
    )
    return float(resp.json()["closing_balance"])


async def _combo_bom(client, headers, wh):
    """cooler @4000 + stand @500; FG combo; a submitted BOM '1 combo = 1 cooler + 1 stand +
    ₹200 labour'. Returns (cooler, stand, combo, bom)."""
    cooler = await make_item(client, headers, "COOLER", wh["id"], valuation_rate="4000")
    stand = await make_item(client, headers, "STAND", wh["id"], valuation_rate="500")
    combo = await make_item(client, headers, "AC-COMBO", wh["id"])
    resp = await client.post(
        f"{API}/boms",
        json={
            "production_item_id": combo["id"], "quantity": "1", "operating_cost": "200",
            "is_default": True,
            "items": [
                {"item_id": cooler["id"], "qty": "1"},
                {"item_id": stand["id"], "qty": "1"},
            ],
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    bom = resp.json()
    return cooler, stand, combo, bom


# --- Phase 1: BOM costing ------------------------------------------------------------


async def test_bom_costing(ctx):
    client, company, headers = ctx
    wh = await make_warehouse(client, headers, "Main Store")
    _cooler, _stand, _combo, bom = await _combo_bom(client, headers, wh)

    assert float(bom["raw_material_cost"]) == pytest.approx(4500)
    assert float(bom["total_cost"]) == pytest.approx(4700)
    assert float(bom["cost_per_unit"]) == pytest.approx(4700)
    assert bom["is_default"] is True
    assert bom["docstatus"] == 0

    resp = await client.post(f"{API}/boms/{bom['id']}/submit", headers=headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["docstatus"] == 1
    assert resp.json()["is_active"] is True


# --- Phase 2: Work Order → Manufacture reconciles stock + GL --------------------------


async def test_work_order_manufacture_reconciles(ctx):
    client, company, headers = ctx
    wh = await make_warehouse(client, headers, "Main Store")
    cooler, stand, combo, bom = await _combo_bom(client, headers, wh)
    await client.post(f"{API}/boms/{bom['id']}/submit", headers=headers)

    # stock the raws
    await receive(client, headers, cooler["id"], wh["id"], "10", "4000", "2026-06-01")
    await receive(client, headers, stand["id"], wh["id"], "10", "500", "2026-06-01")

    op_acct = await coa_account(client, company, headers, "Expenses Included In Valuation")

    # Work Order: make 10 combos
    resp = await client.post(
        f"{API}/work-orders",
        json={
            "bom_id": bom["id"], "qty": "10",
            "source_warehouse_id": wh["id"], "fg_warehouse_id": wh["id"],
            "operating_cost_account_id": op_acct["id"],
            "planned_start_date": "2026-06-02",
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    wo = resp.json()
    assert float(wo["operating_cost"]) == pytest.approx(2000)  # 200 * 10
    assert len(wo["items"]) == 2
    assert {float(i["required_qty"]) for i in wo["items"]} == {10.0}

    resp = await client.post(f"{API}/work-orders/{wo['id']}/submit", headers=headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "Not Started"

    # Finish all 10
    resp = await client.post(
        f"{API}/work-orders/{wo['id']}/finish",
        json={"qty": "10", "posting_date": "2026-06-03"},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    result = resp.json()
    assert result["status"] == "Completed"
    assert float(result["produced_qty"]) == 10

    # raws consumed to zero (the report omits an emptied bin)
    assert await on_hand(client, headers, cooler["id"], wh["id"]) == 0
    assert await on_hand(client, headers, stand["id"], wh["id"]) == 0

    # FG produced 10 @ 4700 (raws 4500 + 200 labour) = 47000
    fg = await stock_balance(client, headers, combo["id"], wh["id"])
    assert float(fg["actual_qty"]) == 10
    assert float(fg["valuation_rate"]) == pytest.approx(4700)
    assert float(fg["stock_value"]) == pytest.approx(47000)

    # operating cost capitalised: Expenses Included In Valuation credited 2000
    assert await account_balance(client, headers, op_acct["id"]) == pytest.approx(-2000)

    assert await gl_is_balanced(client, headers)

    # the Manufacture Stock Entry is visible + linked to the WO
    resp = await client.get(
        f"{API}/stock-entries", params={"purpose": "Manufacture"}, headers=headers
    )
    entries = resp.json()["items"]
    assert len(entries) == 1
    assert entries[0]["id"] == result["stock_entry_id"]

    # WO now reads Completed with produced_qty 10
    resp = await client.get(f"{API}/work-orders/{wo['id']}", headers=headers)
    body = resp.json()
    assert body["status"] == "Completed"
    assert all(float(i["consumed_qty"]) == 10 for i in body["items"])


async def test_partial_finish_and_over_produce_block(ctx):
    client, company, headers = ctx
    wh = await make_warehouse(client, headers, "Main Store")
    cooler, stand, combo, bom = await _combo_bom(client, headers, wh)
    await client.post(f"{API}/boms/{bom['id']}/submit", headers=headers)
    await receive(client, headers, cooler["id"], wh["id"], "10", "4000", "2026-06-01")
    await receive(client, headers, stand["id"], wh["id"], "10", "500", "2026-06-01")
    op_acct = await coa_account(client, company, headers, "Expenses Included In Valuation")

    resp = await client.post(
        f"{API}/work-orders",
        json={
            "bom_id": bom["id"], "qty": "10", "source_warehouse_id": wh["id"],
            "fg_warehouse_id": wh["id"], "operating_cost_account_id": op_acct["id"],
        },
        headers=headers,
    )
    wo = resp.json()
    await client.post(f"{API}/work-orders/{wo['id']}/submit", headers=headers)

    # finish 4 → In Process
    resp = await client.post(
        f"{API}/work-orders/{wo['id']}/finish",
        json={"qty": "4", "posting_date": "2026-06-03"},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "In Process"
    assert float(resp.json()["produced_qty"]) == 4

    # cannot over-produce (only 6 left)
    resp = await client.post(
        f"{API}/work-orders/{wo['id']}/finish",
        json={"qty": "7", "posting_date": "2026-06-03"},
        headers=headers,
    )
    assert resp.status_code == 422, resp.text

    # finish the remaining 6 → Completed
    resp = await client.post(
        f"{API}/work-orders/{wo['id']}/finish",
        json={"qty": "6", "posting_date": "2026-06-04"},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "Completed"

    fg = await stock_balance(client, headers, combo["id"], wh["id"])
    assert float(fg["actual_qty"]) == 10
    assert float(fg["valuation_rate"]) == pytest.approx(4700)
    assert await gl_is_balanced(client, headers)


# --- Phase 3: material availability + shortfall Material Request ----------------------


async def test_material_availability_and_shortfall_mr(ctx):
    client, company, headers = ctx
    wh = await make_warehouse(client, headers, "Main Store")
    cooler, stand, combo, bom = await _combo_bom(client, headers, wh)
    await client.post(f"{API}/boms/{bom['id']}/submit", headers=headers)
    # only 6 coolers, 10 stands → can build 6 combos
    await receive(client, headers, cooler["id"], wh["id"], "6", "4000", "2026-06-01")
    await receive(client, headers, stand["id"], wh["id"], "10", "500", "2026-06-01")

    resp = await client.post(
        f"{API}/work-orders",
        json={
            "bom_id": bom["id"], "qty": "10", "source_warehouse_id": wh["id"],
            "fg_warehouse_id": wh["id"],
        },
        headers=headers,
    )
    wo = resp.json()
    await client.post(f"{API}/work-orders/{wo['id']}/submit", headers=headers)

    resp = await client.get(f"{API}/work-orders/{wo['id']}/material-availability", headers=headers)
    assert resp.status_code == 200, resp.text
    avail = resp.json()
    assert float(avail["can_finish_qty"]) == pytest.approx(6)
    by_item = {r["item_id"]: r for r in avail["rows"]}
    assert float(by_item[cooler["id"]]["shortfall_qty"]) == pytest.approx(4)
    assert float(by_item[stand["id"]]["shortfall_qty"]) == 0

    # raise a Material Request for the shortfall
    resp = await client.post(f"{API}/work-orders/{wo['id']}/material-request", headers=headers)
    assert resp.status_code == 201, resp.text
    mr = resp.json()
    assert mr["material_request_type"] == "Purchase"
    assert len(mr["items"]) == 1
    assert float(mr["items"][0]["qty"]) == pytest.approx(4)


# --- cancel a Manufacture entry rolls back the Work Order ----------------------------


async def test_manufacture_cancel_reverts_work_order(ctx):
    client, company, headers = ctx
    wh = await make_warehouse(client, headers, "Main Store")
    cooler, stand, combo, bom = await _combo_bom(client, headers, wh)
    await client.post(f"{API}/boms/{bom['id']}/submit", headers=headers)
    await receive(client, headers, cooler["id"], wh["id"], "10", "4000", "2026-06-01")
    await receive(client, headers, stand["id"], wh["id"], "10", "500", "2026-06-01")
    op_acct = await coa_account(client, company, headers, "Expenses Included In Valuation")

    resp = await client.post(
        f"{API}/work-orders",
        json={
            "bom_id": bom["id"], "qty": "10", "source_warehouse_id": wh["id"],
            "fg_warehouse_id": wh["id"], "operating_cost_account_id": op_acct["id"],
        },
        headers=headers,
    )
    wo = resp.json()
    await client.post(f"{API}/work-orders/{wo['id']}/submit", headers=headers)
    resp = await client.post(
        f"{API}/work-orders/{wo['id']}/finish",
        json={"qty": "10", "posting_date": "2026-06-03"},
        headers=headers,
    )
    se_id = resp.json()["stock_entry_id"]

    # WO can't be cancelled while stock is produced
    resp = await client.post(f"{API}/work-orders/{wo['id']}/cancel", headers=headers)
    assert resp.status_code == 422, resp.text

    # cancel the Manufacture Stock Entry → raws restored, FG removed, WO rolled back
    resp = await client.post(f"{API}/stock-entries/{se_id}/cancel", headers=headers)
    assert resp.status_code == 200, resp.text

    assert float((await stock_balance(client, headers, cooler["id"], wh["id"]))["actual_qty"]) == 10
    assert float((await stock_balance(client, headers, stand["id"], wh["id"]))["actual_qty"]) == 10
    fg = await stock_balance(client, headers, combo["id"], wh["id"])
    assert fg is None or float(fg["actual_qty"]) == 0

    resp = await client.get(f"{API}/work-orders/{wo['id']}", headers=headers)
    body = resp.json()
    assert float(body["produced_qty"]) == 0
    assert body["status"] == "Not Started"

    # now the WO can be cancelled
    resp = await client.post(f"{API}/work-orders/{wo['id']}/cancel", headers=headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "Cancelled"
    assert await gl_is_balanced(client, headers)


# --- guards --------------------------------------------------------------------------


async def test_bom_cancel_blocked_while_work_order_uses_it(ctx):
    client, company, headers = ctx
    wh = await make_warehouse(client, headers, "Main Store")
    cooler, stand, combo, bom = await _combo_bom(client, headers, wh)
    await client.post(f"{API}/boms/{bom['id']}/submit", headers=headers)
    resp = await client.post(
        f"{API}/work-orders",
        json={
            "bom_id": bom["id"], "qty": "5", "source_warehouse_id": wh["id"],
            "fg_warehouse_id": wh["id"],
        },
        headers=headers,
    )
    wo = resp.json()
    await client.post(f"{API}/work-orders/{wo['id']}/submit", headers=headers)

    resp = await client.post(f"{API}/boms/{bom['id']}/cancel", headers=headers)
    assert resp.status_code == 422, resp.text


# --- Phase 4: reports ----------------------------------------------------------------


async def test_reports(ctx):
    client, company, headers = ctx
    wh = await make_warehouse(client, headers, "Main Store")
    cooler, stand, combo, bom = await _combo_bom(client, headers, wh)
    await client.post(f"{API}/boms/{bom['id']}/submit", headers=headers)
    await receive(client, headers, cooler["id"], wh["id"], "6", "4000", "2026-06-01")
    await receive(client, headers, stand["id"], wh["id"], "10", "500", "2026-06-01")

    resp = await client.post(
        f"{API}/work-orders",
        json={
            "bom_id": bom["id"], "qty": "5", "source_warehouse_id": wh["id"],
            "fg_warehouse_id": wh["id"],
        },
        headers=headers,
    )
    wo = resp.json()
    await client.post(f"{API}/work-orders/{wo['id']}/submit", headers=headers)

    # production register
    resp = await client.get(f"{API}/manufacturing-reports/production-register", headers=headers)
    assert resp.status_code == 200, resp.text
    reg = resp.json()
    assert len(reg) == 1
    assert reg[0]["name"] == wo["name"]
    assert float(reg[0]["qty"]) == 5
    assert float(reg[0]["pending_qty"]) == 5
    assert float(reg[0]["estimated_cost"]) == pytest.approx(23500)  # 4700 * 5

    # BOM where-used for the cooler
    resp = await client.get(
        f"{API}/manufacturing-reports/bom-where-used",
        params={"item_id": cooler["id"]}, headers=headers,
    )
    assert resp.status_code == 200, resp.text
    wu = resp.json()
    assert len(wu) == 1
    assert wu[0]["bom_name"] == bom["name"]
    assert float(wu[0]["qty_per_batch"]) == 1

    # BOM stock report: can I build 10? (only 6 coolers on hand → buildable 6)
    resp = await client.get(
        f"{API}/manufacturing-reports/bom-stock",
        params={"bom_id": bom["id"], "for_qty": "10"}, headers=headers,
    )
    assert resp.status_code == 200, resp.text
    rep = resp.json()
    assert float(rep["buildable_qty"]) == pytest.approx(6)
    by_item = {r["item_id"]: r for r in rep["rows"]}
    assert float(by_item[cooler["id"]]["required_qty"]) == 10
    assert float(by_item[cooler["id"]]["shortfall_qty"]) == 4


# --- Repack (BOM-less conversion) ------------------------------------------------------


async def test_repack_single_output(ctx):
    """Consume two components, produce one bundle: FG valued at consumed + additional
    cost, additional-cost account credited, GL balanced."""
    client, company, headers = ctx
    wh = await make_warehouse(client, headers, "Main Store")
    cooler = await make_item(client, headers, "COOLER", wh["id"])
    stand = await make_item(client, headers, "STAND", wh["id"])
    bundle = await make_item(client, headers, "BUNDLE", wh["id"])
    await receive(client, headers, cooler["id"], wh["id"], "2", "4000", "2026-06-01")
    await receive(client, headers, stand["id"], wh["id"], "2", "500", "2026-06-01")
    op_acct = await coa_account(client, company, headers, "Expenses Included In Valuation")

    resp = await client.post(
        f"{API}/stock-entries",
        json={
            "purpose": "Repack", "posting_date": "2026-06-02",
            "from_warehouse_id": wh["id"], "to_warehouse_id": wh["id"],
            "operating_cost": "100", "operating_cost_account_id": op_acct["id"],
            "items": [
                {"item_id": cooler["id"], "qty": "2"},
                {"item_id": stand["id"], "qty": "2"},
                {"item_id": bundle["id"], "qty": "2", "is_finished_item": True},
            ],
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    entry = resp.json()
    resp = await client.post(f"{API}/stock-entries/{entry['id']}/submit", headers=headers)
    assert resp.status_code == 200, resp.text

    # consumed to zero; bundle = (2*4000 + 2*500 + 100) / 2 = 4550/unit
    assert await on_hand(client, headers, cooler["id"], wh["id"]) == 0
    assert await on_hand(client, headers, stand["id"], wh["id"]) == 0
    fg = await stock_balance(client, headers, bundle["id"], wh["id"])
    assert float(fg["actual_qty"]) == 2
    assert float(fg["valuation_rate"]) == pytest.approx(4550)
    assert float(fg["stock_value"]) == pytest.approx(9100)
    assert await account_balance(client, headers, op_acct["id"]) == pytest.approx(-100)
    assert await gl_is_balanced(client, headers)

    # cancel restores everything
    resp = await client.post(f"{API}/stock-entries/{entry['id']}/cancel", headers=headers)
    assert resp.status_code == 200, resp.text
    assert await on_hand(client, headers, cooler["id"], wh["id"]) == 2
    assert await on_hand(client, headers, bundle["id"], wh["id"]) == 0
    assert await gl_is_balanced(client, headers)


async def test_repack_multi_output_weighted_split(ctx):
    """Split one consumed pool across two outputs by value weight (qty × basic_rate)."""
    client, company, headers = ctx
    wh = await make_warehouse(client, headers, "Main Store")
    kit = await make_item(client, headers, "KIT", wh["id"])
    part_a = await make_item(client, headers, "PART-A", wh["id"])
    part_b = await make_item(client, headers, "PART-B", wh["id"])
    await receive(client, headers, kit["id"], wh["id"], "10", "100", "2026-06-01")  # pool 1000

    resp = await client.post(
        f"{API}/stock-entries",
        json={
            "purpose": "Repack", "posting_date": "2026-06-02",
            "from_warehouse_id": wh["id"], "to_warehouse_id": wh["id"],
            "items": [
                {"item_id": kit["id"], "qty": "10"},
                # weights: A = 5×300 = 1500, B = 5×100 = 500 → A gets 750, B gets 250
                {"item_id": part_a["id"], "qty": "5", "basic_rate": "300", "is_finished_item": True},
                {"item_id": part_b["id"], "qty": "5", "basic_rate": "100", "is_finished_item": True},
            ],
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    entry = resp.json()
    resp = await client.post(f"{API}/stock-entries/{entry['id']}/submit", headers=headers)
    assert resp.status_code == 200, resp.text

    a = await stock_balance(client, headers, part_a["id"], wh["id"])
    b = await stock_balance(client, headers, part_b["id"], wh["id"])
    assert float(a["stock_value"]) == pytest.approx(750)
    assert float(a["valuation_rate"]) == pytest.approx(150)
    assert float(b["stock_value"]) == pytest.approx(250)
    assert float(b["valuation_rate"]) == pytest.approx(50)
    # value conserved: outputs together carry exactly the consumed pool
    assert float(a["stock_value"]) + float(b["stock_value"]) == pytest.approx(1000)
    assert await gl_is_balanced(client, headers)


async def test_repack_validation_guards(ctx):
    client, company, headers = ctx
    wh = await make_warehouse(client, headers, "Main Store")
    item_a = await make_item(client, headers, "A", wh["id"])
    item_b = await make_item(client, headers, "B", wh["id"])

    # no finished row
    resp = await client.post(
        f"{API}/stock-entries",
        json={
            "purpose": "Repack", "posting_date": "2026-06-02",
            "from_warehouse_id": wh["id"], "to_warehouse_id": wh["id"],
            "items": [{"item_id": item_a["id"], "qty": "1"}],
        },
        headers=headers,
    )
    assert resp.status_code == 422, resp.text

    # no consumed row
    resp = await client.post(
        f"{API}/stock-entries",
        json={
            "purpose": "Repack", "posting_date": "2026-06-02",
            "from_warehouse_id": wh["id"], "to_warehouse_id": wh["id"],
            "items": [{"item_id": item_b["id"], "qty": "1", "is_finished_item": True}],
        },
        headers=headers,
    )
    assert resp.status_code == 422, resp.text

    # operating cost is Repack-only
    resp = await client.post(
        f"{API}/stock-entries",
        json={
            "purpose": "Material Receipt", "posting_date": "2026-06-02",
            "to_warehouse_id": wh["id"], "operating_cost": "50",
            "items": [{"item_id": item_a["id"], "qty": "1", "basic_rate": "10"}],
        },
        headers=headers,
    )
    assert resp.status_code == 422, resp.text


# --- guards: BOM recursion + whole-number qty -------------------------------------------


async def test_bom_recursion_blocked(ctx):
    client, company, headers = ctx
    wh = await make_warehouse(client, headers, "Main Store")
    combo = await make_item(client, headers, "AC-COMBO", wh["id"])
    resp = await client.post(
        f"{API}/boms",
        json={
            "production_item_id": combo["id"], "quantity": "1",
            "items": [{"item_id": combo["id"], "qty": "1"}],  # itself!
        },
        headers=headers,
    )
    assert resp.status_code == 422, resp.text
    assert "cannot contain itself" in resp.json()["detail"]


async def test_whole_number_qty_guard(ctx):
    client, company, headers = ctx
    # register "Nos" as a whole-number UOM (the seed does this in a real install)
    resp = await client.post(
        f"{API}/uoms", json={"uom_name": "Nos", "must_be_whole_number": True}, headers=headers
    )
    assert resp.status_code in (200, 201), resp.text

    wh = await make_warehouse(client, headers, "Main Store")
    cooler, stand, combo, bom = await _combo_bom(client, headers, wh)
    await client.post(f"{API}/boms/{bom['id']}/submit", headers=headers)
    await receive(client, headers, cooler["id"], wh["id"], "10", "4000", "2026-06-01")
    await receive(client, headers, stand["id"], wh["id"], "10", "500", "2026-06-01")

    # fractional Work Order qty for a Nos item is rejected
    resp = await client.post(
        f"{API}/work-orders",
        json={
            "bom_id": bom["id"], "qty": "2.5",
            "source_warehouse_id": wh["id"], "fg_warehouse_id": wh["id"],
        },
        headers=headers,
    )
    assert resp.status_code == 422, resp.text
    assert "whole number" in resp.json()["detail"]

    # fractional finish is rejected too
    resp = await client.post(
        f"{API}/work-orders",
        json={
            "bom_id": bom["id"], "qty": "3",
            "source_warehouse_id": wh["id"], "fg_warehouse_id": wh["id"],
        },
        headers=headers,
    )
    wo = resp.json()
    await client.post(f"{API}/work-orders/{wo['id']}/submit", headers=headers)
    resp = await client.post(
        f"{API}/work-orders/{wo['id']}/finish",
        json={"qty": "1.5", "posting_date": "2026-06-03"},
        headers=headers,
    )
    assert resp.status_code == 422, resp.text


# --- Manufacturing Settings: defaults + over-production ---------------------------------


async def test_settings_defaults_and_overproduction(ctx):
    client, company, headers = ctx
    wh = await make_warehouse(client, headers, "Main Store")
    cooler, stand, combo, bom = await _combo_bom(client, headers, wh)
    await client.post(f"{API}/boms/{bom['id']}/submit", headers=headers)
    await receive(client, headers, cooler["id"], wh["id"], "20", "4000", "2026-06-01")
    await receive(client, headers, stand["id"], wh["id"], "20", "500", "2026-06-01")

    # set company defaults + a 10% over-production allowance
    resp = await client.put(
        f"{API}/manufacturing/settings",
        json={
            "default_source_warehouse_id": wh["id"],
            "default_fg_warehouse_id": wh["id"],
            "over_production_percentage": "10",
        },
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["default_fg_warehouse_id"] == wh["id"]

    # Work Order without warehouses falls back to the defaults
    op_acct = await coa_account(client, company, headers, "Expenses Included In Valuation")
    resp = await client.post(
        f"{API}/work-orders",
        json={"bom_id": bom["id"], "qty": "10", "operating_cost_account_id": op_acct["id"]},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    wo = resp.json()
    assert wo["fg_warehouse_id"] == wh["id"]
    assert wo["source_warehouse_id"] == wh["id"]
    await client.post(f"{API}/work-orders/{wo['id']}/submit", headers=headers)

    # 10% allowance: an 10-unit order may finish 11...
    resp = await client.post(
        f"{API}/work-orders/{wo['id']}/finish",
        json={"qty": "11", "posting_date": "2026-06-03"},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "Completed"
    assert float(resp.json()["produced_qty"]) == 11
    # ...but not more
    resp = await client.post(
        f"{API}/work-orders/{wo['id']}/finish",
        json={"qty": "1", "posting_date": "2026-06-03"},
        headers=headers,
    )
    assert resp.status_code == 422, resp.text
    assert await gl_is_balanced(client, headers)


# --- cross-Work-Order material shortage --------------------------------------------------


async def test_material_shortage_report(ctx):
    client, company, headers = ctx
    wh = await make_warehouse(client, headers, "Main Store")
    cooler, stand, combo, bom = await _combo_bom(client, headers, wh)
    await client.post(f"{API}/boms/{bom['id']}/submit", headers=headers)
    # 6 coolers, 20 stands on hand; two open orders needing 9 of each in total
    await receive(client, headers, cooler["id"], wh["id"], "6", "4000", "2026-06-01")
    await receive(client, headers, stand["id"], wh["id"], "20", "500", "2026-06-01")

    names = []
    for qty in ("5", "4"):
        resp = await client.post(
            f"{API}/work-orders",
            json={
                "bom_id": bom["id"], "qty": qty,
                "source_warehouse_id": wh["id"], "fg_warehouse_id": wh["id"],
            },
            headers=headers,
        )
        wo = resp.json()
        names.append(wo["name"])
        await client.post(f"{API}/work-orders/{wo['id']}/submit", headers=headers)

    resp = await client.get(f"{API}/manufacturing-reports/material-shortage", headers=headers)
    assert resp.status_code == 200, resp.text
    rows = {r["item_id"]: r for r in resp.json()}
    assert float(rows[cooler["id"]]["pending_qty"]) == pytest.approx(9)
    assert float(rows[cooler["id"]]["available_qty"]) == pytest.approx(6)
    assert float(rows[cooler["id"]]["shortfall_qty"]) == pytest.approx(3)
    assert set(rows[cooler["id"]]["work_orders"]) == set(names)
    assert float(rows[stand["id"]]["shortfall_qty"]) == 0

    # only_short filter keeps just the cooler
    resp = await client.get(
        f"{API}/manufacturing-reports/material-shortage",
        params={"only_short": "true"}, headers=headers,
    )
    short = resp.json()
    assert len(short) == 1
    assert short[0]["item_id"] == cooler["id"]


# --- review fixes: valuation, GL and validation hardening --------------------------------


async def test_repack_into_negative_bin_balances_gl(ctx):
    """Producing into a negative-stock bin resets the moving-average rate; the residual
    must land on Stock Adjustment so the voucher still balances (was a hard submit fail)."""
    client, company, headers = ctx
    wh = await make_warehouse(client, headers, "Main Store")
    kit = await make_item(client, headers, "KIT", wh["id"])
    bundle = await make_item(client, headers, "BUNDLE", wh["id"])

    # allow negative stock, then oversell BUNDLE: bin qty −5, value −500 (rate 100)
    resp = await client.put(
        f"{API}/settings",
        json={"key": "allow_negative_stock", "value": True, "company_id": company["id"]},
        headers=headers,
    )
    assert resp.status_code in (200, 201), resp.text
    await receive(client, headers, bundle["id"], wh["id"], "5", "100", "2026-06-01")
    resp = await client.post(
        f"{API}/stock-entries",
        json={
            "purpose": "Material Issue", "posting_date": "2026-06-02",
            "from_warehouse_id": wh["id"],
            "items": [{"item_id": bundle["id"], "qty": "10"}],
        },
        headers=headers,
    )
    issue = resp.json()
    resp = await client.post(f"{API}/stock-entries/{issue['id']}/submit", headers=headers)
    assert resp.status_code == 200, resp.text

    # repack 10 KIT (worth 2000) into 10 BUNDLE: incoming 200/unit; the bin crosses from
    # −5 → +5 with a rate reset, so the ledger moves 1500 while the pool is 2000
    await receive(client, headers, kit["id"], wh["id"], "10", "200", "2026-06-03")
    resp = await client.post(
        f"{API}/stock-entries",
        json={
            "purpose": "Repack", "posting_date": "2026-06-04",
            "from_warehouse_id": wh["id"], "to_warehouse_id": wh["id"],
            "items": [
                {"item_id": kit["id"], "qty": "10"},
                {"item_id": bundle["id"], "qty": "10", "is_finished_item": True},
            ],
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    entry = resp.json()
    resp = await client.post(f"{API}/stock-entries/{entry['id']}/submit", headers=headers)
    assert resp.status_code == 200, resp.text

    fg = await stock_balance(client, headers, bundle["id"], wh["id"])
    assert float(fg["actual_qty"]) == 5
    assert float(fg["valuation_rate"]) == pytest.approx(200)
    assert await gl_is_balanced(client, headers)
    # the repack voucher posted its 500 residual as Dr Stock Adjustment. Account total:
    # receipts credit adjustment (−500 −2000), the over-issue debits it (+1000), and the
    # repack residual debits it (+500) → −1000. Without the residual leg the repack
    # voucher itself would have been out of balance and submit would have failed.
    adj = await coa_account(client, company, headers, "Stock Adjustment")
    assert await account_balance(client, headers, adj["id"]) == pytest.approx(-1000)


async def test_repack_weight_ignores_uom_factor(ctx):
    """The value weight is qty × basic_rate in the LINE UOM — a pack-UOM finished row must
    not have its share inflated by its conversion factor."""
    client, company, headers = ctx
    wh = await make_warehouse(client, headers, "Main Store")
    bulk = await make_item(client, headers, "BULK", wh["id"])
    boxed = await make_item(
        client, headers, "BOXED", wh["id"], purchase_uom="Box", purchase_uom_factor="12"
    )
    loose = await make_item(client, headers, "LOOSE", wh["id"])
    await receive(client, headers, bulk["id"], wh["id"], "10", "20", "2026-06-01")  # pool 200

    resp = await client.post(
        f"{API}/stock-entries",
        json={
            "purpose": "Repack", "posting_date": "2026-06-02",
            "from_warehouse_id": wh["id"], "to_warehouse_id": wh["id"],
            "items": [
                {"item_id": bulk["id"], "qty": "10"},
                # equal LINE amounts (1×100 vs 1×100) → 50/50 despite the Box factor 12
                {"item_id": boxed["id"], "qty": "1", "uom": "Box", "basic_rate": "100",
                 "is_finished_item": True},
                {"item_id": loose["id"], "qty": "1", "basic_rate": "100",
                 "is_finished_item": True},
            ],
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    entry = resp.json()
    resp = await client.post(f"{API}/stock-entries/{entry['id']}/submit", headers=headers)
    assert resp.status_code == 200, resp.text

    a = await stock_balance(client, headers, boxed["id"], wh["id"])
    b = await stock_balance(client, headers, loose["id"], wh["id"])
    assert float(a["stock_value"]) == pytest.approx(100)  # not 12/13 of the pool
    assert float(b["stock_value"]) == pytest.approx(100)
    assert float(a["actual_qty"]) == 12  # 1 Box = 12 stock units


async def test_repack_validation_hardening(ctx):
    """Mixed weights, missing cost account, and non-expense cost accounts are all rejected
    at CREATE (drafts have no edit/delete endpoint, so submit-time failures would strand)."""
    client, company, headers = ctx
    wh = await make_warehouse(client, headers, "Main Store")
    item_a = await make_item(client, headers, "A", wh["id"])
    item_b = await make_item(client, headers, "B", wh["id"])
    item_c = await make_item(client, headers, "C", wh["id"])

    # mixed weights: one finished row weighted, the other blank → 422
    resp = await client.post(
        f"{API}/stock-entries",
        json={
            "purpose": "Repack", "posting_date": "2026-06-02",
            "from_warehouse_id": wh["id"], "to_warehouse_id": wh["id"],
            "items": [
                {"item_id": item_a["id"], "qty": "2"},
                {"item_id": item_b["id"], "qty": "1", "basic_rate": "50", "is_finished_item": True},
                {"item_id": item_c["id"], "qty": "1", "is_finished_item": True},
            ],
        },
        headers=headers,
    )
    assert resp.status_code == 422, resp.text
    assert "value weight" in resp.json()["detail"]

    # additional cost without an account → 422 at create
    resp = await client.post(
        f"{API}/stock-entries",
        json={
            "purpose": "Repack", "posting_date": "2026-06-02",
            "from_warehouse_id": wh["id"], "to_warehouse_id": wh["id"],
            "operating_cost": "50",
            "items": [
                {"item_id": item_a["id"], "qty": "1"},
                {"item_id": item_b["id"], "qty": "1", "is_finished_item": True},
            ],
        },
        headers=headers,
    )
    assert resp.status_code == 422, resp.text

    # a non-expense account (Sales income) can NOT take the additional-cost credit
    sales = await coa_account(client, company, headers, "Sales")
    resp = await client.post(
        f"{API}/stock-entries",
        json={
            "purpose": "Repack", "posting_date": "2026-06-02",
            "from_warehouse_id": wh["id"], "to_warehouse_id": wh["id"],
            "operating_cost": "1000000", "operating_cost_account_id": sales["id"],
            "items": [
                {"item_id": item_a["id"], "qty": "1"},
                {"item_id": item_b["id"], "qty": "1", "is_finished_item": True},
            ],
        },
        headers=headers,
    )
    assert resp.status_code == 422, resp.text
    assert "Expense account" in resp.json()["detail"]


async def test_repack_document_shows_actual_values(ctx):
    """After submit the document rows carry the ACTUAL moved values (not raw weights/zeros)."""
    client, company, headers = ctx
    wh = await make_warehouse(client, headers, "Main Store")
    cooler = await make_item(client, headers, "COOLER", wh["id"])
    stand = await make_item(client, headers, "STAND", wh["id"])
    bundle = await make_item(client, headers, "BUNDLE", wh["id"])
    await receive(client, headers, cooler["id"], wh["id"], "2", "4000", "2026-06-01")
    await receive(client, headers, stand["id"], wh["id"], "2", "500", "2026-06-01")
    op_acct = await coa_account(client, company, headers, "Expenses Included In Valuation")

    resp = await client.post(
        f"{API}/stock-entries",
        json={
            "purpose": "Repack", "posting_date": "2026-06-02",
            "from_warehouse_id": wh["id"], "to_warehouse_id": wh["id"],
            "operating_cost": "100", "operating_cost_account_id": op_acct["id"],
            "items": [
                {"item_id": cooler["id"], "qty": "2"},
                {"item_id": stand["id"], "qty": "2"},
                {"item_id": bundle["id"], "qty": "2", "is_finished_item": True},
            ],
        },
        headers=headers,
    )
    entry = resp.json()
    resp = await client.post(f"{API}/stock-entries/{entry['id']}/submit", headers=headers)
    assert resp.status_code == 200, resp.text
    doc = resp.json()
    assert float(doc["total_amount"]) == pytest.approx(9100)  # 8000 + 1000 + 100
    assert float(doc["operating_cost"]) == pytest.approx(100)
    by_item = {r["item_id"]: r for r in doc["items"]}
    assert float(by_item[bundle["id"]]["amount"]) == pytest.approx(9100)
    assert float(by_item[bundle["id"]]["basic_rate"]) == pytest.approx(4550)
    assert float(by_item[cooler["id"]]["amount"]) == pytest.approx(8000)  # actual consumed value
    assert float(by_item[stand["id"]]["amount"]) == pytest.approx(1000)


async def test_explicit_null_source_not_overridden_by_settings(ctx):
    """An explicitly-null source warehouse means 'no order-level source' — the settings
    default must not silently re-apply."""
    client, company, headers = ctx
    wh = await make_warehouse(client, headers, "Main Store")
    cooler, stand, combo, bom = await _combo_bom(client, headers, wh)
    await client.post(f"{API}/boms/{bom['id']}/submit", headers=headers)
    resp = await client.put(
        f"{API}/manufacturing/settings",
        json={"default_source_warehouse_id": wh["id"], "default_fg_warehouse_id": wh["id"]},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text

    # omitted source → default applies
    resp = await client.post(f"{API}/work-orders", json={"bom_id": bom["id"], "qty": "1"}, headers=headers)
    assert resp.status_code == 201, resp.text
    assert resp.json()["source_warehouse_id"] == wh["id"]

    # explicit null source → honored (no order-level source)
    resp = await client.post(
        f"{API}/work-orders",
        json={"bom_id": bom["id"], "qty": "1", "source_warehouse_id": None},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["source_warehouse_id"] is None


async def test_settings_json_tampering_is_sanitized(ctx):
    """Garbage written through the generic PUT /settings must not 500 Work Order flows —
    reads re-validate: bad UUIDs → None, out-of-range/non-numeric pct → clamped/0."""
    client, company, headers = ctx
    wh = await make_warehouse(client, headers, "Main Store")
    cooler, stand, combo, bom = await _combo_bom(client, headers, wh)
    await client.post(f"{API}/boms/{bom['id']}/submit", headers=headers)
    await receive(client, headers, cooler["id"], wh["id"], "30", "4000", "2026-06-01")
    await receive(client, headers, stand["id"], wh["id"], "30", "500", "2026-06-01")

    resp = await client.put(
        f"{API}/settings",
        json={
            "key": "manufacturing_settings", "company_id": company["id"],
            "value": {
                "default_fg_warehouse_id": "junk-not-a-uuid",
                "over_production_percentage": 100000000,
            },
        },
        headers=headers,
    )
    assert resp.status_code in (200, 201), resp.text

    # settings read sanitizes: junk warehouse → null, pct clamped to 100
    resp = await client.get(f"{API}/manufacturing/settings", headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["default_fg_warehouse_id"] is None
    assert float(body["over_production_percentage"]) == 100

    # WO create with the junk default falls through cleanly (fg comes from the payload)
    op_acct = await coa_account(client, company, headers, "Expenses Included In Valuation")
    resp = await client.post(
        f"{API}/work-orders",
        json={
            "bom_id": bom["id"], "qty": "10", "source_warehouse_id": wh["id"],
            "fg_warehouse_id": wh["id"], "operating_cost_account_id": op_acct["id"],
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    wo = resp.json()
    await client.post(f"{API}/work-orders/{wo['id']}/submit", headers=headers)
    # even a tampered pct can't exceed the 100% clamp: finishing 21 of 10 is blocked
    resp = await client.post(
        f"{API}/work-orders/{wo['id']}/finish",
        json={"qty": "21", "posting_date": "2026-06-03"},
        headers=headers,
    )
    assert resp.status_code == 422, resp.text
    # ...while 20 (the clamped 100% allowance) is fine
    resp = await client.post(
        f"{API}/work-orders/{wo['id']}/finish",
        json={"qty": "20", "posting_date": "2026-06-03"},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text


async def test_work_order_rejects_non_expense_cost_account(ctx):
    client, company, headers = ctx
    wh = await make_warehouse(client, headers, "Main Store")
    cooler, stand, combo, bom = await _combo_bom(client, headers, wh)
    await client.post(f"{API}/boms/{bom['id']}/submit", headers=headers)
    sales = await coa_account(client, company, headers, "Sales")
    resp = await client.post(
        f"{API}/work-orders",
        json={
            "bom_id": bom["id"], "qty": "1", "source_warehouse_id": wh["id"],
            "fg_warehouse_id": wh["id"], "operating_cost_account_id": sales["id"],
        },
        headers=headers,
    )
    assert resp.status_code == 422, resp.text
    assert "Expense account" in resp.json()["detail"]
