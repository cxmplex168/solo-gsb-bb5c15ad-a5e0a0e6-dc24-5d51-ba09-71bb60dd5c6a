"""配方替代过敏原追踪 — FastAPI 后端。

数据模型：
- ingredients      原料及其直接过敏原
- recipes          配方
- recipe_versions  配方版本
- recipe_items     版本中的原料行（用量）
- substitutions    原料替代关系（有向边 from -> to）
"""
import json
import os
import sqlite3
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .analysis import analyze_version, find_all_cycles

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("DB_PATH", os.path.join(BASE_DIR, "..", "data", "app.db"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS ingredients (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    allergens TEXT NOT NULL DEFAULT '[]',
    notes TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS recipes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    description TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS recipe_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    recipe_id INTEGER NOT NULL REFERENCES recipes(id) ON DELETE CASCADE,
    version_label TEXT NOT NULL,
    notes TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(recipe_id, version_label)
);
CREATE TABLE IF NOT EXISTS recipe_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    version_id INTEGER NOT NULL REFERENCES recipe_versions(id) ON DELETE CASCADE,
    ingredient_id INTEGER NOT NULL REFERENCES ingredients(id) ON DELETE CASCADE,
    quantity REAL NOT NULL DEFAULT 0,
    unit TEXT NOT NULL DEFAULT 'g',
    UNIQUE(version_id, ingredient_id)
);
CREATE TABLE IF NOT EXISTS substitutions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    from_ingredient_id INTEGER NOT NULL REFERENCES ingredients(id) ON DELETE CASCADE,
    to_ingredient_id INTEGER NOT NULL REFERENCES ingredients(id) ON DELETE CASCADE,
    reason TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(from_ingredient_id, to_ingredient_id),
    CHECK(from_ingredient_id != to_ingredient_id)
);
"""


def get_conn():
    os.makedirs(os.path.dirname(os.path.abspath(DB_PATH)), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@asynccontextmanager
async def lifespan(app):
    conn = get_conn()
    conn.executescript(SCHEMA)
    conn.commit()
    conn.close()
    yield


app = FastAPI(title="配方替代过敏原追踪", version="1.0.0", lifespan=lifespan)


# ----------------------------- Pydantic 模型 -----------------------------

class IngredientIn(BaseModel):
    name: str
    allergens: list[str] = []
    notes: str = ""


class RecipeIn(BaseModel):
    name: str
    description: str = ""


class ItemIn(BaseModel):
    ingredient_id: int
    quantity: float = 0
    unit: str = "g"


class VersionIn(BaseModel):
    version_label: str
    notes: str = ""
    items: list[ItemIn] = []


class SubstitutionIn(BaseModel):
    from_ingredient_id: int
    to_ingredient_id: int
    reason: str = ""


class ImportIngredient(BaseModel):
    name: str
    allergens: list[str] = []
    notes: str = ""


class ImportSubstitution(BaseModel):
    from_: str = Field(alias="from")
    to: str
    reason: str = ""


class ImportItem(BaseModel):
    ingredient: str
    quantity: float = 0
    unit: str = "g"


class ImportVersion(BaseModel):
    version_label: str
    notes: str = ""
    items: list[ImportItem] = []


class ImportRecipe(BaseModel):
    name: str
    description: str = ""
    versions: list[ImportVersion] = []


class ImportPayload(BaseModel):
    ingredients: list[ImportIngredient] = []
    substitutions: list[ImportSubstitution] = []
    recipes: list[ImportRecipe] = []


# ----------------------------- 工具函数 -----------------------------

def norm_allergens(allergens):
    seen, out = set(), []
    for a in allergens:
        a = (a or "").strip()
        if a and a not in seen:
            seen.add(a)
            out.append(a)
    return out


def ingredient_obj(r):
    return {
        "id": r["id"],
        "name": r["name"],
        "allergens": json.loads(r["allergens"] or "[]"),
        "notes": r["notes"],
        "created_at": r["created_at"],
    }


def get_ingredient_or_404(conn, iid):
    r = conn.execute("SELECT * FROM ingredients WHERE id = ?", (iid,)).fetchone()
    if not r:
        raise HTTPException(404, f"原料 id={iid} 不存在")
    return r


def version_detail(conn, vid):
    v = conn.execute(
        """SELECT rv.*, r.name AS recipe_name FROM recipe_versions rv
           JOIN recipes r ON r.id = rv.recipe_id WHERE rv.id = ?""",
        (vid,),
    ).fetchone()
    if not v:
        raise HTTPException(404, f"配方版本 id={vid} 不存在")
    items = conn.execute(
        """SELECT ri.id, ri.ingredient_id, i.name AS ingredient_name, i.allergens,
                  ri.quantity, ri.unit
           FROM recipe_items ri JOIN ingredients i ON i.id = ri.ingredient_id
           WHERE ri.version_id = ? ORDER BY ri.id""",
        (vid,),
    ).fetchall()
    return {
        "id": v["id"],
        "recipe_id": v["recipe_id"],
        "recipe_name": v["recipe_name"],
        "version_label": v["version_label"],
        "notes": v["notes"],
        "created_at": v["created_at"],
        "items": [
            {
                "id": it["id"],
                "ingredient_id": it["ingredient_id"],
                "ingredient_name": it["ingredient_name"],
                "allergens": json.loads(it["allergens"] or "[]"),
                "quantity": it["quantity"],
                "unit": it["unit"],
            }
            for it in items
        ],
    }


def validate_items(conn, items):
    ids = [it.ingredient_id for it in items]
    if len(ids) != len(set(ids)):
        raise HTTPException(400, "同一版本中原料不能重复")
    for iid in ids:
        get_ingredient_or_404(conn, iid)


def replace_items(conn, vid, items):
    conn.execute("DELETE FROM recipe_items WHERE version_id = ?", (vid,))
    for it in items:
        conn.execute(
            "INSERT INTO recipe_items (version_id, ingredient_id, quantity, unit) VALUES (?,?,?,?)",
            (vid, it.ingredient_id, it.quantity, it.unit or "g"),
        )


# ----------------------------- 健康检查 -----------------------------

@app.get("/api/health")
def health():
    return {"status": "ok"}


# ----------------------------- 原料 -----------------------------

@app.get("/api/ingredients")
def list_ingredients():
    conn = get_conn()
    rows = conn.execute("SELECT * FROM ingredients ORDER BY name").fetchall()
    conn.close()
    return [ingredient_obj(r) for r in rows]


@app.post("/api/ingredients", status_code=201)
def create_ingredient(payload: IngredientIn):
    name = payload.name.strip()
    if not name:
        raise HTTPException(400, "原料名称不能为空")
    conn = get_conn()
    try:
        cur = conn.execute(
            "INSERT INTO ingredients (name, allergens, notes) VALUES (?,?,?)",
            (name, json.dumps(norm_allergens(payload.allergens), ensure_ascii=False), payload.notes.strip()),
        )
        conn.commit()
    except sqlite3.IntegrityError:
        conn.close()
        raise HTTPException(409, f"原料「{name}」已存在")
    row = conn.execute("SELECT * FROM ingredients WHERE id = ?", (cur.lastrowid,)).fetchone()
    conn.close()
    return ingredient_obj(row)


@app.put("/api/ingredients/{iid}")
def update_ingredient(iid: int, payload: IngredientIn):
    name = payload.name.strip()
    if not name:
        raise HTTPException(400, "原料名称不能为空")
    conn = get_conn()
    get_ingredient_or_404(conn, iid)
    try:
        conn.execute(
            "UPDATE ingredients SET name=?, allergens=?, notes=? WHERE id=?",
            (name, json.dumps(norm_allergens(payload.allergens), ensure_ascii=False), payload.notes.strip(), iid),
        )
        conn.commit()
    except sqlite3.IntegrityError:
        conn.close()
        raise HTTPException(409, f"原料「{name}」已存在")
    row = conn.execute("SELECT * FROM ingredients WHERE id = ?", (iid,)).fetchone()
    conn.close()
    return ingredient_obj(row)


@app.delete("/api/ingredients/{iid}")
def delete_ingredient(iid: int):
    conn = get_conn()
    get_ingredient_or_404(conn, iid)
    conn.execute("DELETE FROM ingredients WHERE id = ?", (iid,))
    conn.commit()
    conn.close()
    return {"deleted": iid}


# ----------------------------- 配方与版本 -----------------------------

@app.get("/api/recipes")
def list_recipes():
    conn = get_conn()
    recipes = []
    for r in conn.execute("SELECT * FROM recipes ORDER BY name"):
        versions = conn.execute(
            """SELECT rv.id, rv.version_label, rv.notes, rv.created_at,
                      (SELECT COUNT(*) FROM recipe_items ri WHERE ri.version_id = rv.id) AS item_count
               FROM recipe_versions rv WHERE rv.recipe_id = ? ORDER BY rv.created_at, rv.id""",
            (r["id"],),
        ).fetchall()
        recipes.append(
            {
                "id": r["id"],
                "name": r["name"],
                "description": r["description"],
                "created_at": r["created_at"],
                "versions": [dict(v) for v in versions],
            }
        )
    conn.close()
    return recipes


@app.post("/api/recipes", status_code=201)
def create_recipe(payload: RecipeIn):
    name = payload.name.strip()
    if not name:
        raise HTTPException(400, "配方名称不能为空")
    conn = get_conn()
    try:
        cur = conn.execute(
            "INSERT INTO recipes (name, description) VALUES (?,?)",
            (name, payload.description.strip()),
        )
        conn.commit()
    except sqlite3.IntegrityError:
        conn.close()
        raise HTTPException(409, f"配方「{name}」已存在")
    row = conn.execute("SELECT * FROM recipes WHERE id = ?", (cur.lastrowid,)).fetchone()
    conn.close()
    return {"id": row["id"], "name": row["name"], "description": row["description"], "created_at": row["created_at"]}


@app.delete("/api/recipes/{rid}")
def delete_recipe(rid: int):
    conn = get_conn()
    if not conn.execute("SELECT 1 FROM recipes WHERE id = ?", (rid,)).fetchone():
        conn.close()
        raise HTTPException(404, f"配方 id={rid} 不存在")
    conn.execute("DELETE FROM recipes WHERE id = ?", (rid,))
    conn.commit()
    conn.close()
    return {"deleted": rid}


@app.post("/api/recipes/{rid}/versions", status_code=201)
def create_version(rid: int, payload: VersionIn):
    label = payload.version_label.strip()
    if not label:
        raise HTTPException(400, "版本标签不能为空")
    conn = get_conn()
    if not conn.execute("SELECT 1 FROM recipes WHERE id = ?", (rid,)).fetchone():
        conn.close()
        raise HTTPException(404, f"配方 id={rid} 不存在")
    validate_items(conn, payload.items)
    try:
        cur = conn.execute(
            "INSERT INTO recipe_versions (recipe_id, version_label, notes) VALUES (?,?,?)",
            (rid, label, payload.notes.strip()),
        )
        replace_items(conn, cur.lastrowid, payload.items)
        conn.commit()
        vid = cur.lastrowid
    except sqlite3.IntegrityError:
        conn.close()
        raise HTTPException(409, f"版本标签「{label}」在该配方下已存在")
    result = version_detail(conn, vid)
    conn.close()
    return result


@app.get("/api/versions")
def list_versions():
    conn = get_conn()
    rows = conn.execute(
        """SELECT rv.id, rv.recipe_id, r.name AS recipe_name, rv.version_label, rv.created_at
           FROM recipe_versions rv JOIN recipes r ON r.id = rv.recipe_id
           ORDER BY r.name, rv.created_at, rv.id"""
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@app.get("/api/versions/{vid}")
def get_version(vid: int):
    conn = get_conn()
    result = version_detail(conn, vid)
    conn.close()
    return result


@app.put("/api/versions/{vid}")
def update_version(vid: int, payload: VersionIn):
    label = payload.version_label.strip()
    if not label:
        raise HTTPException(400, "版本标签不能为空")
    conn = get_conn()
    if not conn.execute("SELECT 1 FROM recipe_versions WHERE id = ?", (vid,)).fetchone():
        conn.close()
        raise HTTPException(404, f"配方版本 id={vid} 不存在")
    validate_items(conn, payload.items)
    try:
        conn.execute(
            "UPDATE recipe_versions SET version_label=?, notes=? WHERE id=?",
            (label, payload.notes.strip(), vid),
        )
        replace_items(conn, vid, payload.items)
        conn.commit()
    except sqlite3.IntegrityError:
        conn.close()
        raise HTTPException(409, f"版本标签「{label}」在该配方下已存在")
    result = version_detail(conn, vid)
    conn.close()
    return result


@app.delete("/api/versions/{vid}")
def delete_version(vid: int):
    conn = get_conn()
    if not conn.execute("SELECT 1 FROM recipe_versions WHERE id = ?", (vid,)).fetchone():
        conn.close()
        raise HTTPException(404, f"配方版本 id={vid} 不存在")
    conn.execute("DELETE FROM recipe_versions WHERE id = ?", (vid,))
    conn.commit()
    conn.close()
    return {"deleted": vid}


# ----------------------------- 替代关系 -----------------------------

@app.get("/api/substitutions")
def list_substitutions():
    conn = get_conn()
    rows = conn.execute(
        """SELECT s.id, s.from_ingredient_id, f.name AS from_name,
                  s.to_ingredient_id, t.name AS to_name, s.reason, s.created_at
           FROM substitutions s
           JOIN ingredients f ON f.id = s.from_ingredient_id
           JOIN ingredients t ON t.id = s.to_ingredient_id
           ORDER BY f.name, t.name"""
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@app.post("/api/substitutions", status_code=201)
def create_substitution(payload: SubstitutionIn):
    if payload.from_ingredient_id == payload.to_ingredient_id:
        raise HTTPException(400, "不能建立原料到自身的替代关系")
    conn = get_conn()
    get_ingredient_or_404(conn, payload.from_ingredient_id)
    get_ingredient_or_404(conn, payload.to_ingredient_id)
    try:
        cur = conn.execute(
            "INSERT INTO substitutions (from_ingredient_id, to_ingredient_id, reason) VALUES (?,?,?)",
            (payload.from_ingredient_id, payload.to_ingredient_id, payload.reason.strip()),
        )
        conn.commit()
    except sqlite3.IntegrityError:
        conn.close()
        raise HTTPException(409, "该替代关系已存在")
    row = conn.execute(
        """SELECT s.id, s.from_ingredient_id, f.name AS from_name,
                  s.to_ingredient_id, t.name AS to_name, s.reason, s.created_at
           FROM substitutions s
           JOIN ingredients f ON f.id = s.from_ingredient_id
           JOIN ingredients t ON t.id = s.to_ingredient_id
           WHERE s.id = ?""",
        (cur.lastrowid,),
    ).fetchone()
    conn.close()
    return dict(row)


@app.delete("/api/substitutions/{sid}")
def delete_substitution(sid: int):
    conn = get_conn()
    if not conn.execute("SELECT 1 FROM substitutions WHERE id = ?", (sid,)).fetchone():
        conn.close()
        raise HTTPException(404, f"替代关系 id={sid} 不存在")
    conn.execute("DELETE FROM substitutions WHERE id = ?", (sid,))
    conn.commit()
    conn.close()
    return {"deleted": sid}


@app.get("/api/substitutions/cycles")
def substitution_cycles():
    """检测整张替代关系图中的所有循环。"""
    conn = get_conn()
    cycles = find_all_cycles(conn)
    conn.close()
    return {"cycles": cycles}


# ----------------------------- 过敏原分析 -----------------------------

@app.get("/api/versions/{vid}/allergen-analysis")
def allergen_analysis(vid: int):
    conn = get_conn()
    result = analyze_version(conn, vid)
    conn.close()
    if result is None:
        raise HTTPException(404, f"配方版本 id={vid} 不存在")
    return result


# ----------------------------- 版本对比 -----------------------------

@app.get("/api/diff")
def diff_versions(a: int = Query(...), b: int = Query(...)):
    if a == b:
        raise HTTPException(400, "请选择两个不同的版本进行对比")
    conn = get_conn()
    va, vb = version_detail(conn, a), version_detail(conn, b)
    ana, anb = analyze_version(conn, a), analyze_version(conn, b)

    map_a = {it["ingredient_id"]: it for it in va["items"]}
    map_b = {it["ingredient_id"]: it for it in vb["items"]}

    added = [map_b[i] for i in map_b.keys() - map_a.keys()]
    removed = [map_a[i] for i in map_a.keys() - map_b.keys()]
    changed = [
        {
            "ingredient_id": i,
            "ingredient_name": map_b[i]["ingredient_name"],
            "from_quantity": map_a[i]["quantity"],
            "from_unit": map_a[i]["unit"],
            "to_quantity": map_b[i]["quantity"],
            "to_unit": map_b[i]["unit"],
        }
        for i in map_a.keys() & map_b.keys()
        if map_a[i]["quantity"] != map_b[i]["quantity"] or map_a[i]["unit"] != map_b[i]["unit"]
    ]

    def allergen_sets(analysis):
        return (
            set(analysis["summary"]["direct_allergens"]),
            set(analysis["summary"]["introduced_allergens"]),
        )

    da, ia = allergen_sets(ana)
    db, ib = allergen_sets(anb)
    conn.close()
    return {
        "a": {"id": va["id"], "recipe_name": va["recipe_name"], "version_label": va["version_label"]},
        "b": {"id": vb["id"], "recipe_name": vb["recipe_name"], "version_label": vb["version_label"]},
        "added_items": sorted(added, key=lambda x: x["ingredient_name"]),
        "removed_items": sorted(removed, key=lambda x: x["ingredient_name"]),
        "changed_items": sorted(changed, key=lambda x: x["ingredient_name"]),
        "allergens": {
            "a_direct": sorted(da),
            "b_direct": sorted(db),
            "a_introduced": sorted(ia),
            "b_introduced": sorted(ib),
            "direct_added": sorted(db - da),
            "direct_removed": sorted(da - db),
            "introduced_added": sorted(ib - ia),
            "introduced_removed": sorted(ia - ib),
        },
    }


# ----------------------------- 导入 / 导出 -----------------------------

@app.get("/api/export")
def export_all():
    conn = get_conn()
    ingredients = [
        {"name": r["name"], "allergens": json.loads(r["allergens"] or "[]"), "notes": r["notes"]}
        for r in conn.execute("SELECT * FROM ingredients ORDER BY name")
    ]
    substitutions = [
        {"from": r["from_name"], "to": r["to_name"], "reason": r["reason"]}
        for r in conn.execute(
            """SELECT f.name AS from_name, t.name AS to_name, s.reason
               FROM substitutions s
               JOIN ingredients f ON f.id = s.from_ingredient_id
               JOIN ingredients t ON t.id = s.to_ingredient_id
               ORDER BY f.name, t.name"""
        )
    ]
    recipes = []
    for r in conn.execute("SELECT * FROM recipes ORDER BY name"):
        versions = []
        for v in conn.execute(
            "SELECT * FROM recipe_versions WHERE recipe_id = ? ORDER BY created_at, id", (r["id"],)
        ):
            items = [
                {"ingredient": it["name"], "quantity": it["quantity"], "unit": it["unit"]}
                for it in conn.execute(
                    """SELECT i.name, ri.quantity, ri.unit FROM recipe_items ri
                       JOIN ingredients i ON i.id = ri.ingredient_id
                       WHERE ri.version_id = ? ORDER BY ri.id""",
                    (v["id"],),
                )
            ]
            versions.append({"version_label": v["version_label"], "notes": v["notes"], "items": items})
        recipes.append({"name": r["name"], "description": r["description"], "versions": versions})
    conn.close()
    payload = {
        "format": "allergen-tracker-export",
        "version": 1,
        "ingredients": ingredients,
        "substitutions": substitutions,
        "recipes": recipes,
    }
    return JSONResponse(
        payload,
        headers={"Content-Disposition": 'attachment; filename="allergen-tracker-export.json"'},
    )


@app.post("/api/import")
def import_all(payload: ImportPayload):
    conn = get_conn()
    counts = {"ingredients": 0, "substitutions": 0, "recipes": 0, "versions": 0, "items": 0}
    warnings = []
    try:
        ing_id = {}
        for ing in payload.ingredients:
            name = ing.name.strip()
            if not name:
                warnings.append("跳过一条名称为空的原料")
                continue
            conn.execute(
                """INSERT INTO ingredients (name, allergens, notes) VALUES (?,?,?)
                   ON CONFLICT(name) DO UPDATE SET allergens=excluded.allergens, notes=excluded.notes""",
                (name, json.dumps(norm_allergens(ing.allergens), ensure_ascii=False), ing.notes.strip()),
            )
            counts["ingredients"] += 1
        for r in conn.execute("SELECT id, name FROM ingredients"):
            ing_id[r["name"]] = r["id"]

        for s in payload.substitutions:
            f, t = s.from_.strip(), s.to.strip()
            if f not in ing_id or t not in ing_id:
                warnings.append(f"替代关系 {f} -> {t} 引用了不存在的原料，已跳过")
                continue
            if f == t:
                warnings.append(f"替代关系 {f} -> {t} 为自引用，已跳过")
                continue
            conn.execute(
                """INSERT INTO substitutions (from_ingredient_id, to_ingredient_id, reason) VALUES (?,?,?)
                   ON CONFLICT(from_ingredient_id, to_ingredient_id) DO UPDATE SET reason=excluded.reason""",
                (ing_id[f], ing_id[t], s.reason.strip()),
            )
            counts["substitutions"] += 1

        for rec in payload.recipes:
            rname = rec.name.strip()
            if not rname:
                warnings.append("跳过一条名称为空的配方")
                continue
            conn.execute(
                """INSERT INTO recipes (name, description) VALUES (?,?)
                   ON CONFLICT(name) DO UPDATE SET description=excluded.description""",
                (rname, rec.description.strip()),
            )
            counts["recipes"] += 1
            rid = conn.execute("SELECT id FROM recipes WHERE name = ?", (rname,)).fetchone()["id"]
            for v in rec.versions:
                label = v.version_label.strip()
                if not label:
                    warnings.append(f"配方「{rname}」下有一个版本标签为空，已跳过")
                    continue
                conn.execute(
                    """INSERT INTO recipe_versions (recipe_id, version_label, notes) VALUES (?,?,?)
                       ON CONFLICT(recipe_id, version_label) DO UPDATE SET notes=excluded.notes""",
                    (rid, label, v.notes.strip()),
                )
                vid = conn.execute(
                    "SELECT id FROM recipe_versions WHERE recipe_id = ? AND version_label = ?",
                    (rid, label),
                ).fetchone()["id"]
                conn.execute("DELETE FROM recipe_items WHERE version_id = ?", (vid,))
                seen = set()
                for it in v.items:
                    iname = it.ingredient.strip()
                    if iname not in ing_id:
                        warnings.append(f"配方「{rname}」版本「{label}」引用了不存在的原料「{iname}」，已跳过")
                        continue
                    if ing_id[iname] in seen:
                        warnings.append(f"配方「{rname}」版本「{label}」中原料「{iname}」重复，已去重")
                        continue
                    seen.add(ing_id[iname])
                    conn.execute(
                        "INSERT INTO recipe_items (version_id, ingredient_id, quantity, unit) VALUES (?,?,?,?)",
                        (vid, ing_id[iname], it.quantity, it.unit or "g"),
                    )
                    counts["items"] += 1
                counts["versions"] += 1
        conn.commit()
    except Exception as e:
        conn.rollback()
        conn.close()
        raise HTTPException(400, f"导入失败：{e}")
    conn.close()
    return {"ok": True, "counts": counts, "warnings": warnings}


# ----------------------------- 静态页面 -----------------------------

@app.get("/", include_in_schema=False)
def index():
    return FileResponse(os.path.join(BASE_DIR, "static", "index.html"))


app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")
