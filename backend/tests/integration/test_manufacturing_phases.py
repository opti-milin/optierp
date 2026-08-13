"""Manufacturing Phases 2–7 regression: Job Card / consume / WIP, Production Plan,
Subcontract Job, Quality Inspection, Phase 6 reports + module flag, Phase 7.0 CTP.

Shares helpers with test_manufacturing.py. Requires TEST_DATABASE_URL.
"""

import pytest

from tests.integration.conftest import coa_account
from tests.integration.test_manufacturing import (
    API,
    gl_is_balanced,
    make_item,
    make_warehouse,
    on_hand,
    receive,
    stock_balance,
    _combo_bom,
)

pytestmark = pytest.mark.asyncio


# --- Phase 2: shop floor --------------------------------------------------------------


async def test_bom_operations_create_job_cards_and_consume(ctx):
    """BOM with operations → Job Cards on WO submit; mid-process consume then Finish."""
    client, company, headers = ctx
    wh = await make_warehouse(client, headers, "Main Store")
    cooler = await make_item(client, headers, "COOLER", wh["id"], valuation_rate="4000")
    stand = await make_item(client, headers, "STAND", wh["id"], valuation_rate="500")
    combo = await make_item(client, headers, "AC-COMBO", wh["id"])

    op = (
        await client.post(
            f"{API}/registry/operation",
            json={"operation_name": "Assemble", "default_hour_rate": "600"},
            headers=headers,
        )
    ).json()
    ws = (
        await client.post(
            f"{API}/registry/workstation",
            json={"workstation_name": "Bench-1", "hour_rate": "600", "working_hours": "8"},
            headers=headers,
        )
    ).json()

    resp = await client.post(
        f"{API}/boms",
        json={
            "production_item_id": combo["id"],
            "quantity": "1",
            "is_default": True,
            "operating_cost": "0",
            "items": [
                {"item_id": cooler["id"], "qty": "1"},
                {"item_id": stand["id"], "qty": "1"},
            ],
            "operations": [
                {
                    "operation_id": op["id"],
                    "workstation_id": ws["id"],
                    "time_in_mins": "60",
                    "hour_rate": "600",
                }
            ],
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    bom = resp.json()
    assert float(bom["operating_cost"]) == pytest.approx(600)
    await client.post(f"{API}/boms/{bom['id']}/submit", headers=headers)

    await receive(client, headers, cooler["id"], wh["id"], "10", "4000", "2026-06-01")
    await receive(client, headers, stand["id"], wh["id"], "10", "500", "2026-06-01")
    op_acct = await coa_account(client, company, headers, "Expenses Included In Valuation")

    resp = await client.post(
        f"{API}/work-orders",
        json={
            "bom_id": bom["id"],
            "qty": "2",
            "source_warehouse_id": wh["id"],
            "fg_warehouse_id": wh["id"],
            "operating_cost_account_id": op_acct["id"],
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    wo = resp.json()
    assert len(wo["operations"]) == 1
    assert float(wo["operating_cost"]) == pytest.approx(1200)

    resp = await client.post(f"{API}/work-orders/{wo['id']}/submit", headers=headers)
    assert resp.status_code == 200, resp.text

    resp = await client.get(
        f"{API}/job-cards", params={"work_order_id": wo["id"]}, headers=headers
    )
    assert resp.status_code == 200, resp.text
    cards = resp.json()["items"]
    assert len(cards) == 1
    jc_id = cards[0]["id"]

    resp = await client.post(f"{API}/job-cards/{jc_id}/start", headers=headers)
    assert resp.status_code == 200, resp.text

    resp = await client.post(
        f"{API}/job-cards/{jc_id}/complete",
        json={"completed_qty": "2"},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text

    resp = await client.post(
        f"{API}/work-orders/{wo['id']}/consume",
        json={"qty": "1", "posting_date": "2026-06-03"},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    wo2 = (await client.get(f"{API}/work-orders/{wo['id']}", headers=headers)).json()
    by_item = {i["item_id"]: i for i in wo2["items"]}
    assert float(by_item[cooler["id"]]["consumed_qty"]) == pytest.approx(1)

    resp = await client.post(
        f"{API}/work-orders/{wo['id']}/finish",
        json={"qty": "2", "posting_date": "2026-06-04"},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "Completed"
    assert await on_hand(client, headers, cooler["id"], wh["id"]) == pytest.approx(8)
    assert await on_hand(client, headers, combo["id"], wh["id"]) == pytest.approx(2)
    assert await gl_is_balanced(client, headers)


async def test_wip_transfer_then_finish(ctx):
    client, company, headers = ctx
    src = await make_warehouse(client, headers, "Source Store")
    wip = await make_warehouse(client, headers, "WIP Store")
    fg_wh = await make_warehouse(client, headers, "FG Store")
    cooler, stand, combo, bom = await _combo_bom(client, headers, src)
    await client.post(f"{API}/boms/{bom['id']}/submit", headers=headers)
    await receive(client, headers, cooler["id"], src["id"], "10", "4000", "2026-06-01")
    await receive(client, headers, stand["id"], src["id"], "10", "500", "2026-06-01")
    op_acct = await coa_account(client, company, headers, "Expenses Included In Valuation")

    resp = await client.post(
        f"{API}/work-orders",
        json={
            "bom_id": bom["id"],
            "qty": "4",
            "source_warehouse_id": src["id"],
            "wip_warehouse_id": wip["id"],
            "fg_warehouse_id": fg_wh["id"],
            "skip_transfer": False,
            "operating_cost_account_id": op_acct["id"],
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    wo = resp.json()
    assert wo["skip_transfer"] is False
    await client.post(f"{API}/work-orders/{wo['id']}/submit", headers=headers)

    resp = await client.post(
        f"{API}/work-orders/{wo['id']}/transfer",
        json={"qty": "2", "posting_date": "2026-06-02"},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    assert await on_hand(client, headers, cooler["id"], src["id"]) == pytest.approx(8)
    assert await on_hand(client, headers, cooler["id"], wip["id"]) == pytest.approx(2)

    resp = await client.post(
        f"{API}/work-orders/{wo['id']}/finish",
        json={"qty": "2", "posting_date": "2026-06-03"},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    assert float(resp.json()["produced_qty"]) == pytest.approx(2)
    assert await on_hand(client, headers, cooler["id"], wip["id"]) == pytest.approx(0)
    assert await on_hand(client, headers, combo["id"], fg_wh["id"]) == pytest.approx(2)
    assert await gl_is_balanced(client, headers)


# --- Phase 3: Production Plan ---------------------------------------------------------


async def test_production_plan_from_sales_order(ctx):
    client, _company, headers = ctx
    wh = await make_warehouse(client, headers, "Main Store")
    cooler, stand, combo, bom = await _combo_bom(client, headers, wh)
    await client.post(f"{API}/boms/{bom['id']}/submit", headers=headers)
    await receive(client, headers, cooler["id"], wh["id"], "2", "4000", "2026-06-01")
    await receive(client, headers, stand["id"], wh["id"], "10", "500", "2026-06-01")

    cust = (
        await client.post(
            f"{API}/customers", json={"customer_name": "Plan Buyer"}, headers=headers
        )
    ).json()
    resp = await client.post(
        f"{API}/sales-orders",
        json={
            "customer_id": cust["id"],
            "posting_date": "2026-06-01",
            "delivery_date": "2026-06-15",
            "items": [{"item_id": combo["id"], "qty": "5", "rate": "5000"}],
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    so = resp.json()
    resp = await client.post(f"{API}/sales-orders/{so['id']}/submit", headers=headers)
    assert resp.status_code == 200, resp.text

    resp = await client.post(
        f"{API}/production-plans",
        json={
            "posting_date": "2026-06-01",
            "from_date": "2026-06-01",
            "to_date": "2026-06-30",
            "fg_warehouse_id": wh["id"],
            "source_warehouse_id": wh["id"],
            "get_items_from": "Sales Order",
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    plan = resp.json()

    resp = await client.post(f"{API}/production-plans/{plan['id']}/get-items", headers=headers)
    assert resp.status_code == 200, resp.text
    plan = resp.json()
    assert len(plan["items"]) >= 1
    assert float(plan["items"][0]["planned_qty"]) == pytest.approx(5)

    resp = await client.post(
        f"{API}/production-plans/{plan['id']}/get-raw-materials",
        params={"only_shortfall": "true"},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text

    resp = await client.post(f"{API}/production-plans/{plan['id']}/submit", headers=headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["docstatus"] == 1

    resp = await client.post(
        f"{API}/production-plans/{plan['id']}/create-work-orders", headers=headers
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["count"] >= 1

    resp = await client.post(f"{API}/production-plans/{plan['id']}/cancel", headers=headers)
    assert resp.status_code == 422, resp.text

    resp = await client.post(
        f"{API}/production-plans/{plan['id']}/create-work-orders", headers=headers
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["count"] == 0


async def test_production_plan_manual_and_cancel_before_wo(ctx):
    client, _company, headers = ctx
    wh = await make_warehouse(client, headers, "Main Store")
    _cooler, _stand, combo, bom = await _combo_bom(client, headers, wh)
    await client.post(f"{API}/boms/{bom['id']}/submit", headers=headers)

    resp = await client.post(
        f"{API}/production-plans",
        json={
            "posting_date": "2026-06-01",
            "get_items_from": "Manual",
            "fg_warehouse_id": wh["id"],
            "source_warehouse_id": wh["id"],
            "items": [{"item_id": combo["id"], "bom_id": bom["id"], "planned_qty": "3"}],
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    plan = resp.json()
    assert len(plan["items"]) == 1

    resp = await client.post(f"{API}/production-plans/{plan['id']}/submit", headers=headers)
    assert resp.status_code == 200, resp.text
    resp = await client.post(f"{API}/production-plans/{plan['id']}/cancel", headers=headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "Cancelled"


# --- Phase 4: Subcontracting ----------------------------------------------------------


async def test_subcontract_send_and_receive(ctx):
    client, company, headers = ctx
    src = await make_warehouse(client, headers, "Main Store")
    supplier_wh = await make_warehouse(client, headers, "Job Work")
    fg_wh = await make_warehouse(client, headers, "Finished Goods")
    cooler, stand, combo, bom = await _combo_bom(client, headers, src)
    await client.post(f"{API}/boms/{bom['id']}/submit", headers=headers)
    await receive(client, headers, cooler["id"], src["id"], "10", "4000", "2026-06-01")
    await receive(client, headers, stand["id"], src["id"], "10", "500", "2026-06-01")
    op_acct = await coa_account(client, company, headers, "Expenses Included In Valuation")
    supplier = (
        await client.post(
            f"{API}/suppliers", json={"supplier_name": "JobWork Co"}, headers=headers
        )
    ).json()

    resp = await client.post(
        f"{API}/subcontract-jobs",
        json={
            "bom_id": bom["id"],
            "supplier_id": supplier["id"],
            "qty": "2",
            "posting_date": "2026-06-01",
            "source_warehouse_id": src["id"],
            "supplier_warehouse_id": supplier_wh["id"],
            "fg_warehouse_id": fg_wh["id"],
            "service_cost": "200",
            "service_cost_account_id": op_acct["id"],
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    job = resp.json()
    assert len(job["items"]) == 2

    resp = await client.post(
        f"{API}/subcontract-jobs/{job['id']}/receive",
        json={"qty": "2", "posting_date": "2026-06-03"},
        headers=headers,
    )
    assert resp.status_code == 422, resp.text

    resp = await client.post(f"{API}/subcontract-jobs/{job['id']}/submit", headers=headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "Open"

    resp = await client.post(
        f"{API}/subcontract-jobs/{job['id']}/send",
        json={"qty": "2", "posting_date": "2026-06-02"},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["job"]["status"] in ("Materials Sent", "In Process")
    assert await on_hand(client, headers, cooler["id"], src["id"]) == pytest.approx(8)
    assert await on_hand(client, headers, cooler["id"], supplier_wh["id"]) == pytest.approx(2)

    resp = await client.post(
        f"{API}/subcontract-jobs/{job['id']}/receive",
        json={"qty": "2", "posting_date": "2026-06-04"},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["job"]["status"] == "Completed"
    assert await on_hand(client, headers, cooler["id"], supplier_wh["id"]) == pytest.approx(0)
    fg = await stock_balance(client, headers, combo["id"], fg_wh["id"])
    assert float(fg["actual_qty"]) == pytest.approx(2)
    assert float(fg["valuation_rate"]) == pytest.approx(4600)
    assert await gl_is_balanced(client, headers)


# --- Phase 5: Quality Inspection ------------------------------------------------------


async def test_quality_inspection_blocks_finish_until_accepted(ctx):
    client, company, headers = ctx
    wh = await make_warehouse(client, headers, "Main Store")
    cooler, stand, combo, bom = await _combo_bom(client, headers, wh)
    await client.post(f"{API}/boms/{bom['id']}/submit", headers=headers)
    await receive(client, headers, cooler["id"], wh["id"], "10", "4000", "2026-06-01")
    await receive(client, headers, stand["id"], wh["id"], "10", "500", "2026-06-01")

    resp = await client.patch(
        f"{API}/items/{combo['id']}",
        json={"inspection_required": True},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["inspection_required"] is True

    op_acct = await coa_account(client, company, headers, "Expenses Included In Valuation")
    resp = await client.post(
        f"{API}/work-orders",
        json={
            "bom_id": bom["id"],
            "qty": "2",
            "source_warehouse_id": wh["id"],
            "fg_warehouse_id": wh["id"],
            "operating_cost_account_id": op_acct["id"],
        },
        headers=headers,
    )
    wo = resp.json()
    await client.post(f"{API}/work-orders/{wo['id']}/submit", headers=headers)

    resp = await client.post(
        f"{API}/work-orders/{wo['id']}/finish",
        json={"qty": "2", "posting_date": "2026-06-03"},
        headers=headers,
    )
    assert resp.status_code == 422, resp.text
    detail = str(resp.json().get("detail", ""))
    code = resp.json().get("code", "")
    assert "QI" in detail.upper() or "inspect" in detail.lower() or code == "ERR_QI_REQUIRED"

    resp = await client.post(
        f"{API}/quality-inspections",
        json={
            "reference_type": "Work Order",
            "reference_id": wo["id"],
            "item_id": combo["id"],
            "qty": "2",
            "inspection_date": "2026-06-02",
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    qi = resp.json()

    resp = await client.post(f"{API}/quality-inspections/{qi['id']}/reject", headers=headers)
    assert resp.status_code == 200, resp.text
    resp = await client.post(
        f"{API}/work-orders/{wo['id']}/finish",
        json={"qty": "2", "posting_date": "2026-06-03"},
        headers=headers,
    )
    assert resp.status_code == 422, resp.text

    resp = await client.post(
        f"{API}/quality-inspections",
        json={
            "reference_type": "Work Order",
            "reference_id": wo["id"],
            "item_id": combo["id"],
            "qty": "2",
            "inspection_date": "2026-06-02",
        },
        headers=headers,
    )
    qi2 = resp.json()
    resp = await client.post(f"{API}/quality-inspections/{qi2['id']}/accept", headers=headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "Accepted"

    resp = await client.post(
        f"{API}/work-orders/{wo['id']}/finish",
        json={"qty": "2", "posting_date": "2026-06-03"},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "Completed"
    assert await gl_is_balanced(client, headers)


# --- Phase 6: reports + module flag ---------------------------------------------------


async def test_work_order_summary_and_module_flag(ctx):
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
            "bom_id": bom["id"],
            "qty": "3",
            "source_warehouse_id": wh["id"],
            "fg_warehouse_id": wh["id"],
            "operating_cost_account_id": op_acct["id"],
        },
        headers=headers,
    )
    wo = resp.json()
    await client.post(f"{API}/work-orders/{wo['id']}/submit", headers=headers)
    resp = await client.post(
        f"{API}/work-orders/{wo['id']}/finish",
        json={"qty": "3", "posting_date": "2026-06-05"},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text

    resp = await client.get(f"{API}/manufacturing-reports/work-order-summary", headers=headers)
    assert resp.status_code == 200, resp.text
    rows = {r["status"]: r for r in resp.json()}
    assert "Completed" in rows
    assert rows["Completed"]["count"] >= 1
    assert float(rows["Completed"]["total_produced_qty"]) >= 3

    resp = await client.get(
        f"{API}/manufacturing-reports/production-analytics", headers=headers
    )
    assert resp.status_code == 200, resp.text
    assert isinstance(resp.json(), list)

    resp = await client.get(f"{API}/settings/module-flags", headers=headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["manufacturing"] is True

    resp = await client.put(
        f"{API}/settings/module-flags",
        json={"manufacturing": False},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["manufacturing"] is False
    resp = await client.put(
        f"{API}/settings/module-flags",
        json={"manufacturing": True},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["manufacturing"] is True


# --- Phase 7.0: capable-to-promise ----------------------------------------------------


async def test_capable_to_promise_lead_time(ctx):
    client, _company, headers = ctx
    wh = await make_warehouse(client, headers, "Main Store")
    steel = await make_item(
        client, headers, "RAW-LT", wh["id"], valuation_rate="50", lead_time_days=5
    )
    fg = await make_item(client, headers, "FG-LT", wh["id"], lead_time_days=0)
    op = (
        await client.post(
            f"{API}/registry/operation",
            json={"operation_name": "Cut", "default_hour_rate": "100"},
            headers=headers,
        )
    ).json()

    resp = await client.post(
        f"{API}/boms",
        json={
            "production_item_id": fg["id"],
            "quantity": "1",
            "is_default": True,
            "items": [{"item_id": steel["id"], "qty": "2"}],
            "operations": [
                {"operation_id": op["id"], "time_in_mins": "48", "hour_rate": "100"}
            ],
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    bom = resp.json()
    await client.post(f"{API}/boms/{bom['id']}/submit", headers=headers)

    # qty 10 → 480 mins = 1 manufacturing day; no stock → +5 procurement
    resp = await client.get(
        f"{API}/manufacturing-reports/capable-to-promise",
        params={
            "item_id": fg["id"],
            "qty": "10",
            "as_of": "2026-07-01",
            "warehouse_id": wh["id"],
        },
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["procurement_days"] == 5
    assert body["manufacturing_days"] == 1
    assert body["total_days"] == 6
    assert body["earliest_promise_date"] == "2026-07-07"

    await receive(client, headers, steel["id"], wh["id"], "20", "50", "2026-06-01")
    resp = await client.get(
        f"{API}/manufacturing-reports/capable-to-promise",
        params={
            "item_id": fg["id"],
            "qty": "10",
            "as_of": "2026-07-01",
            "warehouse_id": wh["id"],
        },
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["procurement_days"] == 0
    assert body["manufacturing_days"] == 1
    assert body["earliest_promise_date"] == "2026-07-02"


async def test_reverse_schedule_and_procurement_dates(ctx):
    """Phase 7.1: reverse schedule from delivery date + latest order dates for shortfalls."""
    client, _company, headers = ctx
    wh = await make_warehouse(client, headers, "Main Store")
    steel = await make_item(
        client, headers, "RAW-RS", wh["id"], valuation_rate="50", lead_time_days=5
    )
    fg = await make_item(client, headers, "FG-RS", wh["id"], lead_time_days=0)
    op = (
        await client.post(
            f"{API}/registry/operation",
            json={"operation_name": "Weld", "default_hour_rate": "100"},
            headers=headers,
        )
    ).json()

    resp = await client.post(
        f"{API}/boms",
        json={
            "production_item_id": fg["id"],
            "quantity": "1",
            "is_default": True,
            "items": [{"item_id": steel["id"], "qty": "2"}],
            "operations": [
                {"operation_id": op["id"], "time_in_mins": "48", "hour_rate": "100"}
            ],
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    bom = resp.json()
    await client.post(f"{API}/boms/{bom['id']}/submit", headers=headers)

    # No stock: earliest promise = 2026-07-07 (5+1). Delivery 2026-07-20 → on time.
    resp = await client.get(
        f"{API}/manufacturing-reports/reverse-schedule",
        params={
            "item_id": fg["id"],
            "qty": "10",
            "as_of": "2026-07-01",
            "delivery_date": "2026-07-20",
            "warehouse_id": wh["id"],
        },
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["on_time"] is True
    assert body["earliest_promise_date"] == "2026-07-07"
    assert body["manufacturing_start_date"] == "2026-07-19"  # 20 − 1 mfg day
    assert body["materials_ready_by"] == "2026-07-19"
    assert body["slack_days"] == 13
    assert len(body["procurement"]) == 1
    assert body["procurement"][0]["item_code"] == "RAW-RS"
    assert body["procurement"][0]["latest_order_date"] == "2026-07-14"  # 19 − 5
    assert body["procurement"][0]["days_until_order"] == 13

    # Tight delivery before earliest promise → late
    resp = await client.get(
        f"{API}/manufacturing-reports/reverse-schedule",
        params={
            "item_id": fg["id"],
            "qty": "10",
            "as_of": "2026-07-01",
            "delivery_date": "2026-07-05",
            "warehouse_id": wh["id"],
        },
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    late = resp.json()
    assert late["on_time"] is False
    assert late["slack_days"] < 0

    # delivery before as_of → 422
    resp = await client.get(
        f"{API}/manufacturing-reports/reverse-schedule",
        params={
            "item_id": fg["id"],
            "qty": "1",
            "as_of": "2026-07-10",
            "delivery_date": "2026-07-01",
        },
        headers=headers,
    )
    assert resp.status_code == 422, resp.text


async def test_pegging_timeline_with_sales_order_demand(ctx):
    """Phase 7.2: open SO demand + stock → pegging rows and CTP for shortfall."""
    client, _company, headers = ctx
    wh = await make_warehouse(client, headers, "Main Store")
    steel = await make_item(
        client, headers, "RAW-PEG", wh["id"], valuation_rate="50", lead_time_days=5
    )
    fg = await make_item(client, headers, "FG-PEG", wh["id"], lead_time_days=2)
    resp = await client.post(
        f"{API}/boms",
        json={
            "production_item_id": fg["id"],
            "quantity": "1",
            "is_default": True,
            "items": [{"item_id": steel["id"], "qty": "1"}],
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    await client.post(f"{API}/boms/{resp.json()['id']}/submit", headers=headers)

    cust = (
        await client.post(
            f"{API}/customers", json={"customer_name": "Peg Buyer"}, headers=headers
        )
    ).json()
    resp = await client.post(
        f"{API}/sales-orders",
        json={
            "customer_id": cust["id"],
            "posting_date": "2026-07-01",
            "delivery_date": "2026-07-25",
            "items": [{"item_id": fg["id"], "qty": "8", "rate": "100"}],
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    so = resp.json()
    assert (
        await client.post(f"{API}/sales-orders/{so['id']}/submit", headers=headers)
    ).status_code == 200

    resp = await client.get(
        f"{API}/manufacturing-reports/pegging",
        params={"item_id": fg["id"], "as_of": "2026-07-01", "warehouse_id": wh["id"]},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert float(body["demand_qty"]) == pytest.approx(8)
    assert float(body["net_shortfall"]) == pytest.approx(8)
    assert any(r["source_type"] == "Sales Order" for r in body["rows"])
    assert body["earliest_promise_date"] is not None
    assert body["ctp"] is not None


async def test_what_if_ctp_extra_stock_and_lead_override(ctx):
    """Phase 7.4: what-if CTP pretends extra stock / different lead times without persisting."""
    client, _company, headers = ctx
    wh = await make_warehouse(client, headers, "Main Store")
    steel = await make_item(
        client, headers, "RAW-WI", wh["id"], valuation_rate="50", lead_time_days=10
    )
    fg = await make_item(client, headers, "FG-WI", wh["id"], lead_time_days=0)
    op = (
        await client.post(
            f"{API}/registry/operation",
            json={"operation_name": "Mill", "default_hour_rate": "100"},
            headers=headers,
        )
    ).json()
    resp = await client.post(
        f"{API}/boms",
        json={
            "production_item_id": fg["id"],
            "quantity": "1",
            "is_default": True,
            "items": [{"item_id": steel["id"], "qty": "2"}],
            "operations": [
                {"operation_id": op["id"], "time_in_mins": "48", "hour_rate": "100"}
            ],
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    bom = resp.json()
    await client.post(f"{API}/boms/{bom['id']}/submit", headers=headers)

    # Baseline: no stock → 10d procurement + 1d mfg
    base = await client.get(
        f"{API}/manufacturing-reports/capable-to-promise",
        params={"item_id": fg["id"], "qty": "5", "as_of": "2026-07-01", "warehouse_id": wh["id"]},
        headers=headers,
    )
    assert base.status_code == 200, base.text
    assert base.json()["procurement_days"] == 10
    assert base.json()["earliest_promise_date"] == "2026-07-12"

    # Extra stock covers shortfall → procurement 0
    resp = await client.post(
        f"{API}/manufacturing-reports/what-if-ctp",
        json={
            "item_id": fg["id"],
            "qty": "5",
            "as_of": "2026-07-01",
            "warehouse_id": wh["id"],
            "extra_stock": [{"item_id": steel["id"], "extra_qty": "20"}],
            "lead_time_overrides": [],
        },
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["procurement_days"] == 0
    assert body["manufacturing_days"] == 1
    assert body["earliest_promise_date"] == "2026-07-02"
    assert any("What-if" in n for n in body["notes"])

    # Lead override shortens wait without stock
    resp = await client.post(
        f"{API}/manufacturing-reports/what-if-ctp",
        json={
            "item_id": fg["id"],
            "qty": "5",
            "as_of": "2026-07-01",
            "warehouse_id": wh["id"],
            "extra_stock": [],
            "lead_time_overrides": [{"item_id": steel["id"], "lead_time_days": 2}],
        },
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["procurement_days"] == 2
    assert body["earliest_promise_date"] == "2026-07-04"


async def test_demand_forecast_and_capacity_board(ctx):
    """Phase 7.3 + 7.5: forecast returns rows; capacity board lists workstations."""
    client, _company, headers = ctx
    wh = await make_warehouse(client, headers, "Main Store")
    fg = await make_item(client, headers, "FG-FC", wh["id"])

    # Capacity board works even with no workstations
    resp = await client.get(
        f"{API}/manufacturing-reports/capacity-board",
        params={"as_of": "2026-07-01"},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    assert "rows" in resp.json()
    assert "notes" in resp.json()

    ws = (
        await client.post(
            f"{API}/registry/workstation",
            json={
                "workstation_name": "CNC-FC",
                "working_hours": "8",
                "hour_rate": "100",
            },
            headers=headers,
        )
    ).json()
    assert "id" in ws

    resp = await client.get(
        f"{API}/manufacturing-reports/capacity-board",
        params={"as_of": "2026-07-01"},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    names = [r["workstation_name"] for r in resp.json()["rows"]]
    assert "CNC-FC" in names

    resp = await client.get(
        f"{API}/manufacturing-reports/demand-forecast",
        params={
            "item_id": fg["id"],
            "lookback_months": 3,
            "horizon_months": 2,
            "as_of": "2026-07-15",
        },
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["lookback_months"] == 3
    assert body["horizon_months"] == 2
    assert len(body["rows"]) == 5  # 3 history + 2 forecast
    assert sum(1 for r in body["rows"] if r["is_forecast"]) == 2

