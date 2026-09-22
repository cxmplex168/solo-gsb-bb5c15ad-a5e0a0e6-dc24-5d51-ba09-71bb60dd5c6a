"""替代链有向图分析：递归计算过敏原影响、检测循环并标记受影响路径。"""
import json


def get_ingredient_map(conn):
    """id -> {id, name, allergens}"""
    m = {}
    for r in conn.execute("SELECT id, name, allergens FROM ingredients"):
        m[r["id"]] = {
            "id": r["id"],
            "name": r["name"],
            "allergens": json.loads(r["allergens"] or "[]"),
        }
    return m


def build_adjacency(conn):
    """替代关系有向图邻接表：from_ingredient_id -> [to_ingredient_id, ...]"""
    adj = {}
    for r in conn.execute("SELECT from_ingredient_id, to_ingredient_id FROM substitutions"):
        adj.setdefault(r["from_ingredient_id"], []).append(r["to_ingredient_id"])
    return adj


def dfs_paths(adj, start, max_depth=25):
    """从 start 出发 DFS。

    返回 (paths, cycles)：
    - paths: 所有替代链路径，如 [A, B, C] 表示 A->B->C（长度 >= 2）；
    - cycles: 末节点重复出现的路径，如 [A, B, C, A]，表示检测到循环。
    遇到已在当前路径中的节点时不再深入，保证有限步内终止。
    """
    paths, cycles = [], []

    def dfs(path):
        if len(path) - 1 >= max_depth:
            return
        for nxt in adj.get(path[-1], []):
            if nxt in path:
                cycles.append(path + [nxt])
            else:
                paths.append(path + [nxt])
                dfs(path + [nxt])

    dfs([start])
    return paths, cycles


def extract_ring(cycle_path):
    """从循环路径中提取真正的环：末节点首次出现的位置到末尾。

    如 [牛奶, 豆奶, 杏仁奶, 燕麦奶, 豆奶] -> [豆奶, 杏仁奶, 燕麦奶, 豆奶]
    """
    i = cycle_path.index(cycle_path[-1])
    return cycle_path[i:]


def canonical_cycle(cycle_path):
    """将循环路径规范化（提取环并按最小 id 旋转），用于去重。"""
    ring = extract_ring(cycle_path)[:-1]
    i = ring.index(min(ring))
    return tuple(ring[i:] + ring[:i])


def _node_objs(path, ing_map):
    return [{"id": n, "name": ing_map[n]["name"]} for n in path]


def analyze_version(conn, version_id):
    """对某个配方版本做完整的过敏原影响分析。"""
    v = conn.execute(
        """SELECT rv.*, r.name AS recipe_name
           FROM recipe_versions rv JOIN recipes r ON r.id = rv.recipe_id
           WHERE rv.id = ?""",
        (version_id,),
    ).fetchone()
    if not v:
        return None

    items = conn.execute(
        """SELECT ri.ingredient_id, ri.quantity, ri.unit
           FROM recipe_items ri WHERE ri.version_id = ? ORDER BY ri.id""",
        (version_id,),
    ).fetchall()

    ing_map = get_ingredient_map(conn)
    adj = build_adjacency(conn)

    item_results = []
    direct_allergens = set()
    introduced_map = {}   # allergen -> [{from_id, from_name, for_ingredient_id, for_ingredient_name, path}]
    all_cycles = {}       # canonical -> cycle path (id 列表)

    for it in items:
        iid = it["ingredient_id"]
        if iid not in ing_map:
            continue
        info = ing_map[iid]
        direct_allergens.update(info["allergens"])

        paths, cycles = dfs_paths(adj, iid)

        # 每条替代链路径及其逐级引入的过敏原
        path_objs = []
        for p in paths:
            introduced = []
            for node in p[1:]:
                for a in ing_map[node]["allergens"]:
                    introduced.append(
                        {"allergen": a, "from_id": node, "from_name": ing_map[node]["name"]}
                    )
            path_objs.append({"path": _node_objs(p, ing_map), "introduced": introduced})

        # 汇总：每个可达替代品的过敏原，归因到到达它的最短路径
        best = {}
        for p in paths:
            node = p[-1]
            if node not in best or len(p) < len(best[node]):
                best[node] = p
        for node, p in best.items():
            for a in ing_map[node]["allergens"]:
                introduced_map.setdefault(a, []).append(
                    {
                        "from_id": node,
                        "from_name": ing_map[node]["name"],
                        "for_ingredient_id": iid,
                        "for_ingredient_name": info["name"],
                        "path": [ing_map[n]["name"] for n in p],
                    }
                )

        cyc_objs = [{"path": _node_objs(c, ing_map)} for c in cycles]
        for c in cycles:
            key = canonical_cycle(c)
            if key not in all_cycles:
                all_cycles[key] = list(key) + [key[0]]

        item_results.append(
            {
                "ingredient_id": iid,
                "name": info["name"],
                "quantity": it["quantity"],
                "unit": it["unit"],
                "direct_allergens": info["allergens"],
                "paths": path_objs,
                "cycles": cyc_objs,
                "reachable_substitutes": [
                    {"id": n, "name": ing_map[n]["name"]} for n in sorted(best, key=lambda x: ing_map[x]["name"])
                ],
            }
        )

    return {
        "version": {
            "id": v["id"],
            "recipe_id": v["recipe_id"],
            "recipe_name": v["recipe_name"],
            "version_label": v["version_label"],
            "notes": v["notes"],
        },
        "items": item_results,
        "summary": {
            "direct_allergens": sorted(direct_allergens),
            "introduced_allergens": sorted(introduced_map.keys()),
            "introduced_details": {a: introduced_map[a] for a in sorted(introduced_map)},
            "total_effective_allergens": sorted(direct_allergens | set(introduced_map)),
            "cycles": [_node_objs(c, ing_map) for c in all_cycles.values()],
        },
    }


def find_all_cycles(conn):
    """检测整张替代关系图中的所有循环（按环去重，以规范旋转形式返回）。"""
    adj = build_adjacency(conn)
    ing_map = get_ingredient_map(conn)
    found = {}
    for start in adj:
        _, cycles = dfs_paths(adj, start)
        for c in cycles:
            key = canonical_cycle(c)
            if key not in found:
                found[key] = list(key) + [key[0]]
    return [_node_objs(c, ing_map) for c in found.values()]
