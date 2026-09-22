/* 配方替代过敏原追踪 — 前端逻辑（原生 JS） */

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => [...document.querySelectorAll(sel)];

let ingredients = [];
let versions = [];

/* ---------------- 工具函数 ---------------- */

async function api(path, opts = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...opts,
  });
  if (!res.ok) {
    let msg = `请求失败 (${res.status})`;
    try {
      const data = await res.json();
      if (data.detail) msg = typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail);
    } catch (_) {}
    throw new Error(msg);
  }
  return res.json();
}

function toast(msg, isError = false) {
  const el = $("#toast");
  el.textContent = msg;
  el.className = "show" + (isError ? " error" : "");
  setTimeout(() => (el.className = ""), 2600);
}

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function badges(list, cls = "") {
  if (!list || !list.length) return '<span class="muted">无</span>';
  return list.map((a) => `<span class="badge ${cls}">${esc(a)}</span>`).join("");
}

function chainHtml(pathNodes, isCycle = false) {
  const names = pathNodes.map((n) => `<span class="node">${esc(n.name)}</span>`);
  return `<div class="path-chain${isCycle ? " cycle-path" : ""}">${names.join('<span class="arrow">→</span>')}</div>`;
}

/* ---------------- 标签页 ---------------- */

$$(".tab-btn").forEach((btn) =>
  btn.addEventListener("click", () => {
    $$(".tab-btn").forEach((b) => b.classList.toggle("active", b === btn));
    $$(".tab-panel").forEach((p) => p.classList.toggle("active", p.id === "tab-" + btn.dataset.tab));
    if (btn.dataset.tab === "analysis" || btn.dataset.tab === "diff") refreshVersionSelects();
    if (btn.dataset.tab === "substitutions") loadSubstitutions();
  })
);

/* ---------------- 原料管理 ---------------- */

async function loadIngredients() {
  ingredients = await api("/api/ingredients");
  const tbody = $("#ing-table tbody");
  $("#ing-count").textContent = `(${ingredients.length})`;
  tbody.innerHTML = ingredients.length
    ? ingredients
        .map(
          (i) => `<tr>
        <td><strong>${esc(i.name)}</strong></td>
        <td>${badges(i.allergens, "direct")}</td>
        <td>${esc(i.notes)}</td>
        <td>
          <button class="btn small" onclick="editIngredient(${i.id})">编辑</button>
          <button class="btn small danger" onclick="deleteIngredient(${i.id}, '${esc(i.name)}')">删除</button>
        </td>
</tr>`
        )
        .join("")
    : '<tr><td colspan="4" class="empty">暂无原料，请先添加。</td></tr>';
  // 同步替代关系下拉
  const opts = ingredients.map((i) => `<option value="${i.id}">${esc(i.name)}</option>`).join("");
  $("#sub-from").innerHTML = opts;
  $("#sub-to").innerHTML = opts;
}

function resetIngredientForm() {
  $("#ing-id").value = "";
  $("#ing-name").value = "";
  $("#ing-allergens").value = "";
  $("#ing-notes").value = "";
  $("#ing-form-title").textContent = "新增原料";
  $("#ing-cancel").hidden = true;
}

window.editIngredient = (id) => {
  const i = ingredients.find((x) => x.id === id);
  if (!i) return;
  $("#ing-id").value = i.id;
  $("#ing-name").value = i.name;
  $("#ing-allergens").value = i.allergens.join(", ");
  $("#ing-notes").value = i.notes;
  $("#ing-form-title").textContent = `编辑原料 #${i.id}`;
  $("#ing-cancel").hidden = false;
  $("#ing-name").focus();
};

window.deleteIngredient = async (id, name) => {
  if (!confirm(`确定删除原料「${name}」？相关配方行与替代关系会一并删除。`)) return;
  try {
    await api(`/api/ingredients/${id}`, { method: "DELETE" });
    toast("已删除");
    await Promise.all([loadIngredients(), loadRecipes(), loadSubstitutions()]);
  } catch (e) {
    toast(e.message, true);
  }
};

$("#ing-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const id = $("#ing-id").value;
  const body = {
    name: $("#ing-name").value.trim(),
    allergens: $("#ing-allergens").value.split(/[,，]/).map((s) => s.trim()).filter(Boolean),
    notes: $("#ing-notes").value.trim(),
  };
  try {
    if (id) await api(`/api/ingredients/${id}`, { method: "PUT", body: JSON.stringify(body) });
    else await api("/api/ingredients", { method: "POST", body: JSON.stringify(body) });
    toast("已保存");
    resetIngredientForm();
    await Promise.all([loadIngredients(), loadSubstitutions()]);
  } catch (err) {
    toast(err.message, true);
  }
});
$("#ing-cancel").addEventListener("click", resetIngredientForm);

/* ---------------- 配方与版本 ---------------- */

async function loadRecipes() {
  const recipes = await api("/api/recipes");
  const box = $("#recipe-list");
  if (!recipes.length) {
    box.innerHTML = '<p class="empty">暂无配方。</p>';
    return;
  }
  box.innerHTML = recipes
    .map(
      (r) => `<div class="recipe-block">
      <div class="recipe-head">
        <div>
          <span class="recipe-name">${esc(r.name)}</span>
          <span class="muted">${esc(r.description || "")}</span>
        </div>
        <div>
          <button class="btn small" onclick="openVersionForm(${r.id}, '${esc(r.name)}')">+ 新建版本</button>
          <button class="btn small danger" onclick="deleteRecipe(${r.id}, '${esc(r.name)}')">删除配方</button>
        </div>
      </div>
      ${
        r.versions.length
          ? r.versions
              .map(
                (v) => `<div class="version-row">
            <span><strong>${esc(v.version_label)}</strong>
              <span class="muted">· ${v.item_count} 种原料 · ${esc(v.created_at)}</span>
              ${v.notes ? `<span class="muted">· ${esc(v.notes)}</span>` : ""}
            </span>
            <span class="actions">
              <button class="btn small" onclick="showVersion(${v.id})">查看</button>
              <button class="btn small" onclick="editVersion(${v.id})">编辑</button>
              <button class="btn small" onclick="analyzeVersionById(${v.id})">分析</button>
              <button class="btn small danger" onclick="deleteVersion(${v.id}, '${esc(v.version_label)}')">删除</button>
            </span>
          </div>`
              )
              .join("")
          : '<p class="empty">尚无版本。</p>'
      }
    </div>`
    )
    .join("");
}

$("#recipe-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  try {
    await api("/api/recipes", {
      method: "POST",
      body: JSON.stringify({ name: $("#recipe-name").value.trim(), description: $("#recipe-desc").value.trim() }),
    });
    toast("配方已创建");
    $("#recipe-name").value = "";
    $("#recipe-desc").value = "";
    await loadRecipes();
  } catch (err) {
    toast(err.message, true);
  }
});

window.deleteRecipe = async (id, name) => {
  if (!confirm(`确定删除配方「${name}」及其全部版本？`)) return;
  try {
    await api(`/api/recipes/${id}`, { method: "DELETE" });
    toast("已删除");
    await loadRecipes();
  } catch (e) {
    toast(e.message, true);
  }
};

/* ---- 版本表单 ---- */

function addItemRow(item = {}) {
  const row = document.createElement("div");
  row.className = "item-row";
  const opts =
    '<option value="">选择原料…</option>' +
    ingredients.map((i) => `<option value="${i.id}">${esc(i.name)}</option>`).join("");
  row.innerHTML = `
    <select class="item-ing" required>${opts}</select>
    <input class="item-qty" type="number" step="any" min="0" placeholder="用量" value="${item.quantity ?? ""}">
    <input class="item-unit" placeholder="单位" value="${esc(item.unit ?? "g")}">
    <button type="button" class="btn small danger" title="删除行">×</button>`;
  if (item.ingredient_id) row.querySelector(".item-ing").value = item.ingredient_id;
  row.querySelector("button").addEventListener("click", () => row.remove());
  $("#version-items").appendChild(row);
}

window.openVersionForm = (recipeId, recipeName) => {
  $("#version-id").value = "";
  $("#version-recipe-id").value = recipeId;
  $("#version-recipe-name").value = recipeName;
  $("#version-label").value = "";
  $("#version-notes").value = "";
  $("#version-items").innerHTML = "";
  addItemRow();
  $("#version-form-title").textContent = `新建版本 — ${recipeName}`;
  $("#version-form-card").hidden = false;
  $("#version-form-card").scrollIntoView({ behavior: "smooth" });
};

window.editVersion = async (vid) => {
  try {
    const v = await api(`/api/versions/${vid}`);
    $("#version-id").value = v.id;
    $("#version-recipe-id").value = v.recipe_id;
    $("#version-recipe-name").value = v.recipe_name;
    $("#version-label").value = v.version_label;
    $("#version-notes").value = v.notes;
    $("#version-items").innerHTML = "";
    v.items.forEach((it) => addItemRow(it));
    if (!v.items.length) addItemRow();
    $("#version-form-title").textContent = `编辑版本 — ${v.recipe_name} / ${v.version_label}`;
    $("#version-form-card").hidden = false;
    $("#version-form-card").scrollIntoView({ behavior: "smooth" });
  } catch (e) {
    toast(e.message, true);
  }
};

$("#add-item-row").addEventListener("click", () => addItemRow());
$("#version-cancel").addEventListener("click", () => ($("#version-form-card").hidden = true));

$("#version-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const vid = $("#version-id").value;
  const recipeId = $("#version-recipe-id").value;
  const items = $$("#version-items .item-row")
    .map((row) => ({
      ingredient_id: Number(row.querySelector(".item-ing").value),
      quantity: Number(row.querySelector(".item-qty").value) || 0,
      unit: row.querySelector(".item-unit").value.trim() || "g",
    }))
    .filter((it) => it.ingredient_id);
  const body = {
    version_label: $("#version-label").value.trim(),
    notes: $("#version-notes").value.trim(),
    items,
  };
  try {
    if (vid) await api(`/api/versions/${vid}`, { method: "PUT", body: JSON.stringify(body) });
    else await api(`/api/recipes/${recipeId}/versions`, { method: "POST", body: JSON.stringify(body) });
    toast("版本已保存");
    $("#version-form-card").hidden = true;
    await loadRecipes();
  } catch (err) {
    toast(err.message, true);
  }
});

window.showVersion = async (vid) => {
  try {
    const v = await api(`/api/versions/${vid}`);
    $("#version-detail-title").textContent = `${v.recipe_name} / ${v.version_label}`;
    $("#version-detail-body").innerHTML = v.items.length
      ? `<table class="table"><thead><tr><th>原料</th><th>用量</th><th>直接过敏原</th></tr></thead><tbody>
          ${v.items
            .map(
              (it) => `<tr><td>${esc(it.ingredient_name)}</td>
            <td>${it.quantity} ${esc(it.unit)}</td>
            <td>${badges(it.allergens, "direct")}</td></tr>`
            )
            .join("")}</tbody></table>`
      : '<p class="empty">该版本暂无原料。</p>';
    $("#version-detail-card").hidden = false;
    $("#version-detail-card").scrollIntoView({ behavior: "smooth" });
  } catch (e) {
    toast(e.message, true);
  }
};

window.deleteVersion = async (vid, label) => {
  if (!confirm(`确定删除版本「${label}」？`)) return;
  try {
    await api(`/api/versions/${vid}`, { method: "DELETE" });
    toast("已删除");
    await loadRecipes();
  } catch (e) {
    toast(e.message, true);
  }
};

window.analyzeVersionById = (vid) => {
  $$(".tab-btn").forEach((b) => b.classList.toggle("active", b.dataset.tab === "analysis"));
  $$(".tab-panel").forEach((p) => p.classList.toggle("active", p.id === "tab-analysis"));
  refreshVersionSelects().then(() => {
    $("#analysis-version").value = vid;
    runAnalysis();
  });
};

/* ---------------- 替代关系 ---------------- */

async function loadSubstitutions() {
  const [subs, cyc] = await Promise.all([api("/api/substitutions"), api("/api/substitutions/cycles")]);
  $("#sub-count").textContent = `(${subs.length})`;
  $("#sub-table tbody").innerHTML = subs.length
    ? subs
        .map(
          (s) => `<tr>
        <td><strong>${esc(s.from_name)}</strong></td><td class="muted">→</td>
        <td><strong>${esc(s.to_name)}</strong></td>
        <td>${esc(s.reason)}</td>
        <td><button class="btn small danger" onclick="deleteSub(${s.id})">删除</button></td>
      </tr>`
        )
        .join("")
    : '<tr><td colspan="5" class="empty">暂无替代关系。</td></tr>';

  const banner = $("#cycle-banner");
  if (cyc.cycles.length) {
    banner.innerHTML = `<div class="cycle-banner">
      <h3>⚠️ 检测到 ${cyc.cycles.length} 个替代循环</h3>
      ${cyc.cycles.map((c) => chainHtml(c, true)).join("")}
      <p class="muted">循环替代会导致过敏原影响沿环反复传播，请确认是否有意为之。</p>
    </div>`;
  } else {
    banner.innerHTML = "";
  }
}

$("#sub-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  try {
    await api("/api/substitutions", {
      method: "POST",
      body: JSON.stringify({
        from_ingredient_id: Number($("#sub-from").value),
        to_ingredient_id: Number($("#sub-to").value),
        reason: $("#sub-reason").value.trim(),
      }),
    });
    toast("替代关系已添加");
    $("#sub-reason").value = "";
    await loadSubstitutions();
  } catch (err) {
    toast(err.message, true);
  }
});

window.deleteSub = async (id) => {
  try {
    await api(`/api/substitutions/${id}`, { method: "DELETE" });
    toast("已删除");
    await loadSubstitutions();
  } catch (e) {
    toast(e.message, true);
  }
};

/* ---------------- 过敏原分析 ---------------- */

async function refreshVersionSelects() {
  versions = await api("/api/versions");
  const opts = versions.length
    ? versions.map((v) => `<option value="${v.id}">${esc(v.recipe_name)} / ${esc(v.version_label)}</option>`).join("")
    : '<option value="">（暂无版本）</option>';
  $("#analysis-version").innerHTML = opts;
  $("#diff-a").innerHTML = opts;
  $("#diff-b").innerHTML = opts;
  if (versions.length > 1) $("#diff-b").value = versions[1].id;
}

async function runAnalysis() {
  const vid = $("#analysis-version").value;
  if (!vid) return toast("请先选择配方版本", true);
  const box = $("#analysis-result");
  box.innerHTML = '<p class="muted">分析中…</p>';
  try {
    const r = await api(`/api/versions/${vid}/allergen-analysis`);
    const s = r.summary;
    let html = `<div class="card">
      <h2>分析结果：${esc(r.version.recipe_name)} / ${esc(r.version.version_label)}</h2>
      <div class="summary-cards">
        <div class="summary-card"><h3>直接过敏原（${s.direct_allergens.length}）</h3>${badges(s.direct_allergens, "direct")}</div>
        <div class="summary-card"><h3>替代链引入过敏原（${s.introduced_allergens.length}）</h3>${badges(s.introduced_allergens, "introduced")}</div>
        <div class="summary-card"><h3>合计潜在过敏原（${s.total_effective_allergens.length}）</h3>${badges(s.total_effective_allergens)}</div>
        <div class="summary-card"><h3>替代循环（${s.cycles.length}）</h3>
          ${s.cycles.length ? s.cycles.map((c) => chainHtml(c, true)).join("") : '<span class="badge neutral">无循环</span>'}
        </div>
      </div>`;

    if (Object.keys(s.introduced_details).length) {
      html += `<h3>引入过敏原归因</h3><ul class="compact">`;
      for (const [allergen, sources] of Object.entries(s.introduced_details)) {
        html += `<li><span class="badge introduced">${esc(allergen)}</span> 来自 `;
        html += sources
          .map((x) => `<strong>${esc(x.from_name)}</strong>（替代链：${x.path.map(esc).join(" → ")}）`)
          .join("；");
        html += `</li>`;
      }
      html += `</ul>`;
    }
    html += `</div>`;

    // 每个原料的详细路径
    for (const item of r.items) {
      html += `<div class="item-analysis">
        <h4>${esc(item.name)} <span class="muted">${item.quantity} ${esc(item.unit)}</span></h4>
        <div>直接过敏原：${badges(item.direct_allergens, "direct")}</div>`;
      if (item.paths.length) {
        html += `<div class="sub-section"><div class="sub-title">受影响替代路径（${item.paths.length} 条）</div>`;
        for (const p of item.paths) {
          html += chainHtml(p.path);
          if (p.introduced.length) {
            const grouped = {};
            p.introduced.forEach((x) => {
              (grouped[x.allergen] = grouped[x.allergen] || new Set()).add(x.from_name);
            });
            html += `<div class="muted" style="margin:-2px 0 6px 18px">引入：` +
              Object.entries(grouped)
                .map(([a, froms]) => `<span class="badge introduced">${esc(a)}</span><small>（来自 ${[...froms].map(esc).join("、")}）</small>`)
                .join(" ") + `</div>`;
          }
        }
        html += `</div>`;
      } else {
        html += `<div class="muted" style="margin-top:6px">该原料没有替代链，无额外过敏原影响。</div>`;
      }
      if (item.cycles.length) {
        html += `<div class="sub-section"><div class="sub-title" style="color:var(--danger)">⚠️ 涉及循环路径（${item.cycles.length} 条）</div>`;
        item.cycles.forEach((c) => (html += chainHtml(c.path, true)));
        html += `</div>`;
      }
      html += `</div>`;
    }
    box.innerHTML = html;
  } catch (e) {
    box.innerHTML = "";
    toast(e.message, true);
  }
}
$("#run-analysis").addEventListener("click", runAnalysis);

/* ---------------- 版本对比 ---------------- */

$("#run-diff").addEventListener("click", async () => {
  const a = $("#diff-a").value;
  const b = $("#diff-b").value;
  if (!a || !b) return toast("请先选择两个版本", true);
  const box = $("#diff-result");
  box.innerHTML = '<p class="muted">对比中…</p>';
  try {
    const d = await api(`/api/diff?a=${a}&b=${b}`);
    const itemLine = (it, sign) =>
      `<li>${sign} <strong>${esc(it.ingredient_name)}</strong> ${it.quantity} ${esc(it.unit)} ${badges(it.allergens, "direct")}</li>`;
    let html = `<div class="card">
      <h2>${esc(d.a.recipe_name)} / ${esc(d.a.version_label)} &nbsp;⟶&nbsp; ${esc(d.b.recipe_name)} / ${esc(d.b.version_label)}</h2>
      <div class="diff-grid">
        <div class="summary-card"><h3>新增原料（${d.added_items.length}）</h3>
          ${d.added_items.length ? `<ul class="compact">${d.added_items.map((i) => itemLine(i, "＋")).join("")}</ul>` : '<span class="muted">无</span>'}</div>
        <div class="summary-card"><h3>移除原料（${d.removed_items.length}）</h3>
          ${d.removed_items.length ? `<ul class="compact">${d.removed_items.map((i) => itemLine(i, "－")).join("")}</ul>` : '<span class="muted">无</span>'}</div>
        <div class="summary-card"><h3>用量变化（${d.changed_items.length}）</h3>
          ${d.changed_items.length
            ? `<ul class="compact">${d.changed_items
                .map((c) => `<li><strong>${esc(c.ingredient_name)}</strong>：${c.from_quantity} ${esc(c.from_unit)} → ${c.to_quantity} ${esc(c.to_unit)}</li>`)
                .join("")}</ul>`
            : '<span class="muted">无</span>'}</div>
      </div>
      <h3 style="margin-top:16px">过敏原变化</h3>
      <div class="diff-grid">
        <div class="summary-card"><h3>直接过敏原 · 新增</h3>${badges(d.allergens.direct_added, "direct")}</div>
        <div class="summary-card"><h3>直接过敏原 · 减少</h3>${badges(d.allergens.direct_removed, "neutral")}</div>
        <div class="summary-card"><h3>替代链引入 · 新增</h3>${badges(d.allergens.introduced_added, "introduced")}</div>
        <div class="summary-card"><h3>替代链引入 · 减少</h3>${badges(d.allergens.introduced_removed, "neutral")}</div>
      </div>
      <p class="muted" style="margin-top:10px">
        版本 A 合计：直接 ${d.allergens.a_direct.length} 种 / 引入 ${d.allergens.a_introduced.length} 种；
        版本 B 合计：直接 ${d.allergens.b_direct.length} 种 / 引入 ${d.allergens.b_introduced.length} 种。
      </p>
    </div>`;
    box.innerHTML = html;
  } catch (e) {
    box.innerHTML = "";
    toast(e.message, true);
  }
});

/* ---------------- 导入 / 导出 ---------------- */

$("#import-file").addEventListener("change", (e) => {
  const file = e.target.files[0];
  if (!file) return;
  const reader = new FileReader();
  reader.onload = () => ($("#import-text").value = reader.result);
  reader.readAsText(file);
});

$("#run-import").addEventListener("click", async () => {
  let payload;
  try {
    payload = JSON.parse($("#import-text").value);
  } catch (_) {
    return toast("JSON 解析失败，请检查格式", true);
  }
  try {
    const r = await api("/api/import", { method: "POST", body: JSON.stringify(payload) });
    const c = r.counts;
    let html = `<p style="margin-top:10px">✅ 导入完成：原料 ${c.ingredients}、替代关系 ${c.substitutions}、配方 ${c.recipes}、版本 ${c.versions}、配方行 ${c.items}。</p>`;
    if (r.warnings.length) {
      html += `<ul class="compact" style="color:var(--danger)">${r.warnings.map((w) => `<li>${esc(w)}</li>`).join("")}</ul>`;
    }
    $("#import-result").innerHTML = html;
    toast("导入完成");
    await Promise.all([loadIngredients(), loadRecipes(), loadSubstitutions()]);
  } catch (e) {
    toast(e.message, true);
  }
});

$("#load-sample").addEventListener("click", () => {
  $("#import-text").value = JSON.stringify(SAMPLE_DATA, null, 2);
  toast("已填入示例数据，点击「执行导入」");
});

const SAMPLE_DATA = {
  ingredients: [
    { name: "全脂牛奶", allergens: ["牛奶", "乳糖"], notes: "基础乳制品" },
    { name: "豆奶", allergens: ["大豆"], notes: "植物基替代" },
    { name: "杏仁奶", allergens: ["坚果"], notes: "" },
    { name: "燕麦奶", allergens: ["麸质"], notes: "可能含微量麸质" },
    { name: "黄油", allergens: ["牛奶"], notes: "" },
    { name: "人造黄油", allergens: ["大豆"], notes: "" },
    { name: "小麦粉", allergens: ["麸质", "小麦"], notes: "" },
    { name: "鸡蛋", allergens: ["鸡蛋"], notes: "" },
    { name: "亚麻籽蛋", allergens: [], notes: "鸡蛋素替代" },
  ],
  substitutions: [
    { from: "全脂牛奶", to: "豆奶", reason: "无乳配方" },
    { from: "豆奶", to: "杏仁奶", reason: "无大豆需求" },
    { from: "杏仁奶", to: "燕麦奶", reason: "坚果过敏客群" },
    { from: "燕麦奶", to: "豆奶", reason: "示例：形成循环" },
    { from: "黄油", to: "人造黄油", reason: "降成本" },
    { from: "鸡蛋", to: "亚麻籽蛋", reason: "纯素版本" },
  ],
  recipes: [
    {
      name: "经典蛋糕",
      description: "门店基础款",
      versions: [
        {
          version_label: "v1.0",
          notes: "原始配方",
          items: [
            { ingredient: "全脂牛奶", quantity: 200, unit: "ml" },
            { ingredient: "黄油", quantity: 100, unit: "g" },
            { ingredient: "小麦粉", quantity: 300, unit: "g" },
            { ingredient: "鸡蛋", quantity: 3, unit: "个" },
          ],
        },
        {
          version_label: "v2.0",
          notes: "无乳改版",
          items: [
            { ingredient: "豆奶", quantity: 200, unit: "ml" },
            { ingredient: "人造黄油", quantity: 100, unit: "g" },
            { ingredient: "小麦粉", quantity: 320, unit: "g" },
            { ingredient: "鸡蛋", quantity: 3, unit: "个" },
          ],
        },
      ],
    },
  ],
};

/* ---------------- 初始化 ---------------- */

(async function init() {
  try {
    await Promise.all([loadIngredients(), loadRecipes(), loadSubstitutions(), refreshVersionSelects()]);
  } catch (e) {
    toast("初始化失败：" + e.message, true);
  }
})();
