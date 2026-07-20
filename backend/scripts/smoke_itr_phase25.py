"""API smoke for Income Tax Phases 2–5 against a running backend.

Requires Phase 0–1 masters (rate table + adjustment category) and admin login.
Leaves one submitted computation for AY 2024-25 so export can be re-tested in UI.
"""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8000/api/v1"
EMAIL = "admin@example.com"
PASSWORD = "ChangeMe!123"
AY = "2024-25"


def req(
    method: str,
    path: str,
    token: str | None = None,
    body: dict | None = None,
    *,
    raw: bool = False,
) -> tuple[int, dict | list | str | bytes]:
    data = None if body is None else json.dumps(body).encode()
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    r = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(r, timeout=60) as resp:
            raw_bytes = resp.read()
            if raw:
                return resp.status, raw_bytes
            text = raw_bytes.decode()
            return resp.status, json.loads(text) if text else {}
    except urllib.error.HTTPError as e:
        raw_bytes = e.read()
        if raw:
            return e.code, raw_bytes
        text = raw_bytes.decode()
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            parsed = text
        return e.code, parsed


def ok(label: str, cond: bool, detail: object = "") -> None:
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {label}" + (f" — {detail}" if detail else ""))
    if not cond:
        raise SystemExit(1)


def main() -> None:
    code, login = req("POST", "/auth/login", body={"email": EMAIL, "password": PASSWORD})
    ok("login", code == 200 and isinstance(login, dict) and "access_token" in login, code)
    assert isinstance(login, dict)
    token = login["access_token"]

    code, rates = req("GET", "/registry/income-tax-rate-table?page_size=50", token)
    ok("list rate tables", code == 200)
    assert isinstance(rates, dict)
    items = rates.get("items") or []
    rate = next((i for i in items if i.get("assessment_year") == "2025-26"), items[0] if items else None)
    ok("have rate table", rate is not None, rates)
    assert rate is not None
    rate_id = rate["id"]

    code, cats = req("GET", "/registry/tax-adjustment-category?page_size=50", token)
    ok("list adjustment categories", code == 200)
    assert isinstance(cats, dict)
    cat_items = cats.get("items") or []
    ok("have adjustment category", bool(cat_items), cats)
    cat_id = cat_items[0]["id"]

    # Phase 3 — advance tax calendar
    code, cal = req(
        "GET",
        f"/income-tax-computations/advance-tax-calendar?assessment_year={AY}&total_tax=100000",
        token,
    )
    ok("advance-tax calendar", code == 200 and isinstance(cal, dict), cal)
    assert isinstance(cal, dict)
    instalments = cal.get("instalments") or []
    ok("four instalments", len(instalments) == 4, instalments)
    ok(
        "suggested amounts filled",
        all(i.get("suggested_amount") is not None for i in instalments),
        instalments,
    )

    # Cancel prior submitted for AY; reuse draft if present
    code, comps = req("GET", "/income-tax-computations?page_size=50", token)
    ok("list computations", code == 200)
    assert isinstance(comps, dict)
    for row in comps.get("items") or []:
        if row.get("assessment_year") == AY and row.get("docstatus") == 1:
            req("POST", f"/income-tax-computations/{row['id']}/cancel", token)

    code, comps = req("GET", "/income-tax-computations?page_size=50", token)
    assert isinstance(comps, dict)
    existing = next(
        (
            r
            for r in (comps.get("items") or [])
            if r.get("assessment_year") == AY and r.get("docstatus") == 0
        ),
        None,
    )

    if existing:
        doc_id = existing["id"]
        code, doc = req(
            "PUT",
            f"/income-tax-computations/{doc_id}",
            token,
            {
                "rate_table_id": rate_id,
                "advance_tax_paid": "1000",
                "adjustments": [
                    {
                        "category_id": cat_id,
                        "description": "phase25 add-back",
                        "direction": "Add",
                        "amount": "10000",
                    }
                ],
            },
        )
        ok("update draft for export", code == 200, doc)
    else:
        code, doc = req(
            "POST",
            "/income-tax-computations",
            token,
            {
                "assessment_year": AY,
                "from_date": "2023-04-01",
                "to_date": "2024-03-31",
                "rate_table_id": rate_id,
                "advance_tax_paid": "1000",
                "seed_from_books": True,
                "adjustments": [
                    {
                        "category_id": cat_id,
                        "description": "phase25 add-back",
                        "direction": "Add",
                        "amount": "10000",
                    }
                ],
            },
        )
        ok("POST computation", code == 201, doc)
        assert isinstance(doc, dict)
        doc_id = doc["id"]

    code, doc = req("POST", f"/income-tax-computations/{doc_id}/submit", token)
    ok("SUBMIT for export", code == 200 and isinstance(doc, dict) and doc.get("docstatus") == 1, doc)
    assert isinstance(doc, dict)

    # Phase 2 — ITR-6 JSON + CSV
    code, pack = req("GET", f"/income-tax-computations/{doc_id}/itr-6", token)
    ok("ITR-6 JSON", code == 200 and isinstance(pack, dict) and pack.get("form") == "ITR-6", pack)
    assert isinstance(pack, dict)
    ok("ITR-6 payload keys", isinstance(pack.get("payload"), dict), pack.get("payload"))

    code, csv_body = req(
        "GET", f"/income-tax-computations/{doc_id}/itr-6.csv", token, raw=True
    )
    ok("ITR-6 CSV", code == 200 and isinstance(csv_body, (bytes, bytearray)), code)
    text = csv_body.decode() if isinstance(csv_body, (bytes, bytearray)) else str(csv_body)
    ok("CSV has header rows", "assessment_year" in text.lower() or "taxable" in text.lower(), text[:200])

    # Phase 5 — 26AS reconcile
    code, recon = req(
        "POST",
        "/income-tax-computations/26as/reconcile",
        token,
        {
            "computation_id": doc_id,
            "form26as": {
                "tds": [
                    {
                        "deductor_name": "ACME Pvt Ltd",
                        "tan": "MUMM12345A",
                        "section": "194C",
                        "tds": str(doc.get("tds_credit") or "0"),
                    }
                ]
            },
        },
    )
    ok("26AS reconcile", code == 200 and isinstance(recon, dict), recon)
    assert isinstance(recon, dict)
    summary = recon.get("summary") or {}
    ok("26AS summary present", "status" in summary, summary)

    # Phase 6 — e-file degrade then sandbox
    code, env = req("POST", f"/income-tax-computations/{doc_id}/itr/efile", token)
    ok("efile none -> generated", code == 200 and isinstance(env, dict) and env.get("status") == "generated", env)

    code, settings = req("GET", "/income-tax-settings", token)
    ok("get settings for efile", code == 200 and isinstance(settings, dict), settings)
    assert isinstance(settings, dict)
    settings["itr_efile_provider"] = "sandbox"
    code, settings = req("PUT", "/income-tax-settings", token, settings)
    ok("set sandbox provider", code == 200 and settings.get("itr_efile_provider") == "sandbox", settings)

    code, env = req("POST", f"/income-tax-computations/{doc_id}/itr/efile", token)
    ok(
        "efile sandbox -> pushed",
        code == 200
        and isinstance(env, dict)
        and env.get("status") == "pushed"
        and (env.get("result") or {}).get("ack_no", "").startswith("SANDBOX-ACK-"),
        env,
    )

    # Phase 4 lean — entity pack via /itr (Company → ITR-6)
    code, pack2 = req("GET", f"/income-tax-computations/{doc_id}/itr", token)
    ok("entity /itr pack", code == 200 and isinstance(pack2, dict) and pack2.get("form") == "ITR-6", pack2)

    # reset provider to none so demos stay JSON-only
    settings["itr_efile_provider"] = None
    req("PUT", "/income-tax-settings", token, settings)

    print(f"\nSmoke Phases 2-6: ALL PASSED (submitted computation id={doc_id})")


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001
        print(f"[FAIL] unexpected: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
