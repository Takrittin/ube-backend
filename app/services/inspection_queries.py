"""Bounded history reads and database-side dashboard aggregation.

Date filters are inclusive calendar dates in Asia/Bangkok. Only a page or the
latest ten records loads model evidence; aggregate queries never load masks or
full measurement JSON into Python.
"""
from datetime import date, datetime, time, timedelta, timezone

from fastapi import HTTPException
from sqlalchemy import and_, case, func, or_, select, true

from app.models import AIResult, FinalResult, Inspection, Lot, Product, User

BANGKOK = timezone(timedelta(hours=7), "Asia/Bangkok")
CHECK_KEYS = ("upright", "wrap", "alignment", "layers", "label")


def _bounds(day: date, *, following=False):
    try:
        if following:
            day += timedelta(days=1)
        return datetime.combine(day, time.min, BANGKOK).astimezone(timezone.utc)
    except (OverflowError, ValueError):
        raise HTTPException(422, "Date range is outside the supported calendar") from None


def _conditions(*, search="", decision="all", date_from=None, date_to=None, material="", q=""):
    conditions = []
    if date_from:
        conditions.append(Inspection.created_at >= _bounds(date_from))
    if date_to:
        conditions.append(Inspection.created_at < _bounds(date_to, following=True))
    if material:
        conditions.append(Inspection.material_code == material)
    if decision in {"pass", "fail"}:
        conditions.append(FinalResult.decision == decision.upper())
    elif decision == "review":
        conditions.append(AIResult.decision == "REVIEW")
    elif decision != "all":
        raise HTTPException(422, "Unknown inspection decision filter")
    if q:
        # Escape SQL wildcard characters: the browser's old search was literal.
        conditions.append(or_(Lot.lot_code.icontains(q, autoescape=True),
                              Inspection.pallet_code.icontains(q, autoescape=True)))
    if search:
        inspector_name = func.coalesce(func.nullif(User.full_name, ""), User.username)
        conditions.append(or_(*(column.icontains(search, autoescape=True) for column in (
            Inspection.id, Lot.lot_code, Inspection.pallet_code, Inspection.material_code,
            Inspection.qr_raw, Product.name, inspector_name))))
    return conditions


def _joined(*columns):
    return (select(*columns).select_from(Inspection)
            .outerjoin(Lot, Lot.id == Inspection.lot_id)
            .outerjoin(Product, Product.id == Lot.product_id)
            .outerjoin(AIResult, AIResult.inspection_id == Inspection.id)
            .outerjoin(FinalResult, FinalResult.inspection_id == Inspection.id)
            .join(User, User.id == Inspection.inspector_id))


def _records(db, conditions, *, offset=0, limit=50):
    # The router retains its public serializer for the existing API contract.
    from app.routers.inspections import inspection_statement, serialize_inspection

    statement = (inspection_statement().outerjoin(Product, Product.id == Lot.product_id)
                 .where(*conditions).order_by(Inspection.created_at.desc(), Inspection.id)
                 .offset(offset).limit(limit))
    return [serialize_inspection(*row) for row in db.execute(statement)]


def inspection_page(db, offset=0, limit=50, search="", decision="all", date_from=None,
                    date_to=None, material="", q=""):
    conditions = _conditions(search=search, decision=decision, date_from=date_from,
                             date_to=date_to, material=material, q=q)
    total = db.scalar(_joined(func.count(Inspection.id)).where(*conditions))
    return {"items": _records(db, conditions, offset=offset, limit=limit),
            "total": total, "offset": offset, "limit": limit}


def _count_when(condition):
    return func.coalesce(func.sum(case((condition, 1), else_=0)), 0)


def _rate(count, total):
    return {"count": count, "total": total, "rate": count / total * 100 if total else None}


def _metrics(db, conditions):
    approved = and_(Inspection.status == "CONFIRMED", FinalResult.decision.is_not(None))
    eligible = and_(approved, AIResult.decision.in_(("PASS", "FAIL")))
    row = db.execute(_joined(
        func.count(Inspection.id),
        _count_when(and_(approved, FinalResult.decision == "PASS")),
        _count_when(approved),
        _count_when(and_(eligible, FinalResult.decision == AIResult.decision)),
        _count_when(eligible),
        _count_when(and_(eligible, FinalResult.decision != AIResult.decision)),
    ).where(*conditions)).one()
    return {"inspected": row[0], "pass": _rate(row[1], row[2]),
            "agreement": _rate(row[3], row[4]), "overrides": row[5]}


def _defects(db, conditions):
    # Both supported databases expose array elements as a table-valued function.
    # Deduplicate per inspection to preserve the frontend's includes() semantics.
    if db.get_bind().dialect.name == "postgresql":
        elements = func.json_array_elements_text(FinalResult.defects).table_valued("value").render_derived()
    else:
        elements = func.json_each(FinalResult.defects).table_valued("value")
    unique = (_joined(Inspection.id.label("inspection_id"), elements.c.value.label("name"))
              .join(elements, true()).where(*conditions).distinct().subquery())
    counts = (select(unique.c.name, func.count().label("count"))
              .group_by(unique.c.name).order_by(func.count().desc(), unique.c.name))
    return [{"name": name, "count": count} for name, count in db.execute(counts)]


def _materials(db, conditions):
    approved = and_(Inspection.status == "CONFIRMED", FinalResult.decision.is_not(None))
    statement = (_joined(Inspection.material_code, func.count(Inspection.id),
                         _count_when(and_(approved, FinalResult.decision == "PASS")),
                         _count_when(approved))
                 .where(*conditions, Inspection.material_code != "")
                 .group_by(Inspection.material_code))
    rows = [{"code": code, "inspected": inspected, **_rate(passed, total)}
            for code, inspected, passed, total in db.execute(statement)]
    return sorted(rows, key=lambda row: (row["rate"] if row["rate"] is not None else float("inf"), row["code"]))


def dashboard_analytics(db, date_from: date, date_to: date, material="", q=""):
    if date_from > date_to:
        raise HTTPException(422, "date_from must not be after date_to")
    try:
        days = (date_to - date_from).days + 1
        previous_to = date_from - timedelta(days=1)
        previous_from = date_from - timedelta(days=days)
        conditions = _conditions(date_from=date_from, date_to=date_to, material=material, q=q)
        previous_conditions = _conditions(date_from=previous_from, date_to=previous_to, material=material, q=q)
    except (OverflowError, ValueError):
        raise HTTPException(422, "Date range is outside the supported calendar") from None
    current = _metrics(db, conditions)
    previous = _metrics(db, previous_conditions)
    current_rate, previous_rate = current["pass"]["rate"], previous["pass"]["rate"]
    defects = _defects(db, conditions)
    material_options = list(db.scalars(select(Inspection.material_code).where(
        Inspection.material_code != "").distinct().order_by(Inspection.material_code)))
    return {
        "range": {"start": date_from.isoformat(), "end": date_to.isoformat()},
        "previousRange": {"start": previous_from.isoformat(), "end": previous_to.isoformat()},
        "inspected": current["inspected"], "previousInspected": previous["inspected"],
        "currentPass": current["pass"], "previousPass": previous["pass"],
        "delta": None if current_rate is None or previous_rate is None else current_rate - previous_rate,
        "agreement": current["agreement"], "overrides": current["overrides"],
        # The live inspection contract has no five-topic check_results. Keep its
        # existing unknown/empty semantics instead of inferring unsupported checks.
        "checks": {key: {**_rate(0, 0), "review": 0} for key in CHECK_KEYS},
        "defects": defects, "maxDefects": max((item["count"] for item in defects), default=1),
        "latest": _records(db, conditions, limit=10), "materials": _materials(db, conditions),
        "materialOptions": material_options,
    }
