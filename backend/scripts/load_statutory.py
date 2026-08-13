"""CLI: load statutory JSON packs into schema ``statutory`` (erp_owner only).

Usage (from backend/ with owner DB URL):
  python -m scripts.load_statutory
  python -m scripts.load_statutory --ay 2025-26
  python -m scripts.load_statutory --validate-only
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# Ensure backend root is on path when run as ``python -m scripts.load_statutory``.
_BACKEND = Path(__file__).resolve().parents[1]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from app.core.config import get_settings  # noqa: E402
from app.services.taxation.catalogue.loader import (  # noqa: E402
    PACK_ROOT,
    discover_ay_dirs,
    load_all_packs,
    load_pack,
)
from app.services.taxation.catalogue.pack_validator import (  # noqa: E402
    PackValidationError,
    validate_pack,
)


async def _session() -> async_sessionmaker[AsyncSession]:
    settings = get_settings()
    engine = create_async_engine(settings.alembic_url, pool_pre_ping=True)
    return async_sessionmaker(engine, expire_on_commit=False), engine


def _load_common() -> dict:
    return json.loads((PACK_ROOT / "_common.json").read_text(encoding="utf-8"))


def _validate_all() -> None:
    common = _load_common()
    for ay_dir in discover_ay_dirs():
        pack = json.loads((ay_dir / "pack.json").read_text(encoding="utf-8"))
        validate_pack(pack, common=common)
        print(f"OK  {ay_dir.name}")


async def _run(ay: str | None) -> None:
    factory, engine = await _session()
    try:
        async with factory() as db:
            if ay:
                common = _load_common()
                pack = json.loads((PACK_ROOT / ay / "pack.json").read_text(encoding="utf-8"))
                key = await load_pack(db, pack, common=common)
                await db.commit()
                print(f"loaded {key}")
            else:
                keys = await load_all_packs(db)
                await db.commit()
                print("loaded:", ", ".join(keys))
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description="Load statutory Income-tax Act packs")
    parser.add_argument("--ay", help="Load a single AY folder (e.g. 2025-26)")
    parser.add_argument("--validate-only", action="store_true", help="Validate packs without writing")
    args = parser.parse_args()
    try:
        if args.validate_only:
            _validate_all()
        else:
            asyncio.run(_run(args.ay))
    except PackValidationError as exc:
        print(f"VALIDATION FAILED: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
