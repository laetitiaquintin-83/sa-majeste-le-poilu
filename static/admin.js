/* === Sa Majesté le Poilu — admin.js ==================================
   Dashboard : login, stats, CRUD produits, commandes, réglages.
   ===================================================================== */
(function () {
  "use strict";

  const $ = (s, r) => (r || document).querySelector(s);
  const $$ = (s, r) => Array.from((r || document).querySelectorAll(s));
  const euro = new Intl.NumberFormat("fr-FR", { style: "currency", currency: "EUR" });

  const CATEGORIES = [
    "Mugs", "T-shirts", "Vêtements Animaux", "Accessoires", "Portraits Sur-Mesure"
  ];
  const PRODUCT_STATUSES = ["En stock", "Sur commande POD", "Indisponible"];
  const ORDER_STATUSES = ["Nouvelle", "En préparation", "Expédiée", "Livrée"];

  let products = [];
  let orders = [];
  let editingId = null;

  /* ---------------- Helpers ---------------- */
  async function api(url, opts = {}) {
    const r = await fetch(url, {
      headers: { "Content-Type": "application/json" },
      ...opts,
    });
    const data = await r.json().catch(() => ({}));
    if (!r.ok || data.ok === false) throw new Error(data.error || `HTTP ${r.status}`);
    return data;
  }

  function toast(msg, isErr) {
    let region = $(".toast-region");
    if (!region) {
      region = document.createElement("div");
      region.className = "toast-region";
      document.body.appendChild(region);
    }
    const t = document.createElement("div");
    t.className = "toast " + (isErr ? "err" : "ok");
    t.textContent = msg;
    region.appendChild(t);
    setTimeout(() => t.remove(), 3500);
  }

  function badge(status, kind) {
    const cls = { "En stock": "mint", "Sur commande POD": "blue", Indisponible: "red",
                  Nouvelle: "pink", "En préparation": "", Expédiée: "blue", Livrée: "mint" }[status] || "";
    return `<span class="badge ${cls}">${status}</span>`;
  }

  /* ---------------- Navigation onglets ---------------- */
  $$(".admin-tab").forEach((btn) => {
    btn.addEventListener("click", () => {
      $$(".admin-tab").forEach((b) => b.classList.remove("is-active"));
      btn.classList.add("is-active");
      $$(".admin-panel").forEach((p) => p.classList.remove("is-active"));
      $(`#panel-${btn.dataset.panel}`).classList.add("is-active");
      const labels = { stats: "Tableau de bord", produits: "Catalogue", commandes: "Commandes", galerie: "Galerie / Lookbook", reglages: "Réglages" };
      $("#tab-label").textContent = labels[btn.dataset.panel] || "";
    });
  });

  /* ---------------- Statistiques ---------------- */
  async function loadStats() {
    try {
      const s = await api("/api/admin/stats");
      $("#stat-ca").textContent = euro.format(s.ca_mois);
      $("#stat-orders").textContent = s.commandes_mois;
      $("#stat-orders-total").textContent = s.commandes_total;
      $("#stat-best").textContent = s.meilleur_produit;
      $("#stat-margin").textContent = euro.format(s.marge_mois);
      $("#stat-month").textContent = s.mois_couvert || "";
    } catch (e) { toast("Stats : " + e.message, true); }
  }

  /* ---------------- Catalogue produits ---------------- */
  const EU = (n) => euro.format(n || 0);

  function renderProducts() {
    const statusFilter = $("#prod-filter-status").value;
    const catFilter = $("#prod-filter-cat").value;
    let list = products;
    if (statusFilter !== "all") list = list.filter((p) => p.status === statusFilter);
    if (catFilter !== "all") list = list.filter((p) => p.category === catFilter);

    const wrap = $("#products-table-body");
    wrap.innerHTML = "";
    if (!list.length) {
      wrap.innerHTML = `<tr><td colspan="7">Aucun produit pour ces filtres. 🐾</td></tr>`;
      return;
    }
    for (const p of list) {
      const tr = document.createElement("tr");
      tr.innerHTML = `
        <td class="table-emoji" style="background:${p.hue || "#FBBF24"}22">${p.image ? `<img src="${p.image}" width="34" style="border-radius:8px;border:2px solid #1E1E1E">` : (p.emoji || "🐾")}</td>
        <td><strong>${p.name}</strong><br><small>${p.description || ""}</small></td>
        <td>${p.category}</td>
        <td>${EU(p.price)}<br><small>Coût : ${EU(p.cost)} · Marge : ${EU((p.price - p.cost))}</small></td>
        <td>${(p.sizes || []).join(", ")}${(p.colors || []).length ? "<br>" + (p.colors || []).join(", ") : ""}</td>
        <td>${badge(p.status)}</td>
        <td><div class="tbl-actions">
          <button class="tbl-btn primary" data-edit="${p.id}">Modifier</button>
          <button class="tbl-btn danger" data-del="${p.id}">Supprimer</button>
        </div></td>`;
      wrap.appendChild(tr);
    }
  }

  function fillProductForm(p) {
    editingId = p ? p.id : null;
    $("#pf-title").textContent = p ? `✏️ Modifier : ${p.name}` : "➕ Nouveau produit royal";
    $("#pf-id").value = p ? p.id : "";
    $("#pf-name").value = p ? p.name : "";
    $("#pf-price").value = p ? p.price : "";
    $("#pf-cost").value = p ? p.cost : "";
    $("#pf-category").value = p ? p.category : CATEGORIES[0];
    $("#pf-description").value = p ? p.description : "";
    $("#pf-sizes").value = p ? (p.sizes || []).join(", ") : "S, M, L, XL";
    $("#pf-colors").value = p ? (p.colors || []).join(", ") : "Blanc";
    $("#pf-status").value = p ? p.status : PRODUCT_STATUSES[0];
    $("#pf-emoji").value = p ? p.emoji : "🐾";
    $("#pf-hue").value = p ? p.hue : "#FBBF24";
    $("#pf-image").value = p && p.image ? p.image : "";
    $("#pf-image-file").value = "";
    const preview = $("#pf-image-preview");
    const previewImage = preview.querySelector("img");
    if (p && p.image) {
      previewImage.src = p.image;
      preview.hidden = false;
    } else {
      previewImage.removeAttribute("src");
      preview.hidden = true;
    }
    $("#pf-submit").textContent = p ? "💾 Enregistrer les modifications" : "👑 Créer le produit";
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  async function loadProducts() {
    const d = await api("/api/products");
    // L'endpoint public masque les Indisponibles : on recharge via le même endpoint
    // (simple démo : produits indisponibles restent éditables via leur ID mémorisé)
    products = d.products;
    renderProducts();
  }

  async function ensureAdminProducts() {
    try { await loadProducts(); }
    catch (e) { toast("Catalogue : " + e.message, true); }
  }

  $("#pf-image-file").addEventListener("change", (e) => {
    const file = e.target.files[0];
    const preview = $("#pf-image-preview");
    const previewImage = preview.querySelector("img");
    if (!file) {
      preview.hidden = true;
      return;
    }
    previewImage.src = URL.createObjectURL(file);
    preview.hidden = false;
  });

  async function submitProductForm(formData, url, method) {
    const response = await fetch(url, { method, body: formData });
    const data = await response.json().catch(() => ({}));
    if (!response.ok || data.ok === false) {
      throw new Error(data.error || `HTTP ${response.status}`);
    }
    return data;
  }

  $("#product-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const fd = new FormData(e.target);
    try {
      if (editingId) {
        await submitProductForm(fd, `/api/products/${editingId}`, "PUT");
        toast("Produit modifié 👑");
      } else {
        await submitProductForm(fd, "/api/products", "POST");
        toast("Produit créé 🎉");
      }
      fillProductForm(null);
      await loadProducts();
    } catch (err) { toast(err.message, true); }
  });

  $("#pf-reset").addEventListener("click", (e) => { e.preventDefault(); fillProductForm(null); });

  $("#products-table-body").addEventListener("click", async (e) => {
    const editBtn = e.target.closest("[data-edit]");
    if (editBtn) {
      const p = products.find((x) => x.id === +editBtn.dataset.edit);
      if (p) fillProductForm(p);
      return;
    }
    const delBtn = e.target.closest("[data-del]");
    if (delBtn && confirm("Supprimer ce produit ? Sa Majesté devra réapprouver.")) {
      try {
        await api(`/api/products/${delBtn.dataset.del}`, { method: "DELETE" });
        toast("Produit supprimé 🗑");
        await loadProducts();
      } catch (err) { toast(err.message, true); }
    }
  });

  ["#prod-filter-status", "#prod-filter-cat"].forEach(
    (sel) => $(sel).addEventListener("change", renderProducts)
  );

  /* ---------------- Commandes ---------------- */
  async function loadOrders() {
    try {
      const d = await api("/api/orders");
      orders = d.orders;
      renderOrders();
    } catch (err) { toast(err.message, true); }
  }

  function renderOrders() {
    const f = $("#order-filter-status").value;
    let list = orders;
    if (f !== "all") list = list.filter((o) => o.status === f);
    const wrap = $("#orders-table-body");
    wrap.innerHTML = "";
    if (!list.length) {
      wrap.innerHTML = `<tr><td colspan="5">Aucune commande pour ce filtre. 🕊️</td></tr>`;
      return;
    }
    for (const o of list) {
      const tr = document.createElement("tr");
      tr.innerHTML = `
        <td><strong>${o.ref}</strong><br><small>${new Date(o.date).toLocaleDateString("fr-FR")} ${new Date(o.date).toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit" })}</small></td>
        <td>${o.customer.name}<br><small>${o.customer.email || ""}</small></td>
        <td>${o.items.map((i) => `${i.qty}× ${i.name} <small>(${i.size}, ${i.color})</small>`).join("<br>")}</td>
        <td><strong>${EU(o.total)}</strong><br><small>+ livraison ${EU(o.shipping)}</small></td>
        <td>
          ${badge(o.status)}
          <div class="tbl-actions" style="margin-top:6px">
            <button class="tbl-btn" data-order-details="${o.id}">Détails</button>
            ${o.status === "Nouvelle" ? `<button class="tbl-btn primary" data-order-advance="${o.id}" data-next="En préparation">→ Préparation</button>` : ""}
            ${o.status === "En préparation" ? `<button class="tbl-btn primary" data-order-advance="${o.id}" data-next="Expédiée">📦 Expédier</button>` : ""}
            ${o.status === "Expédiée" ? `<button class="tbl-btn primary" data-order-advance="${o.id}" data-next="Livrée">✅ Livrée</button>` : ""}
          </div>
        </td>`;
      wrap.appendChild(tr);
    }
  }

  $("#orders-table-body").addEventListener("click", async (e) => {
    const adv = e.target.closest("[data-order-advance]");
    if (adv) {
      try {
        await api(`/api/orders/${adv.dataset.orderAdvance}`, {
          method: "PUT",
          body: JSON.stringify({ status: adv.dataset.next }),
        });
        toast(`Commande → ${adv.dataset.next} 📦`);
        await loadOrders(); await loadStats();
      } catch (err) { toast(err.message, true); }
      return;
    }
    const det = e.target.closest("[data-order-details]");
    if (det) {
      const o = orders.find((x) => x.id === +det.dataset.orderDetails);
      if (o) {
        alert(
          `Commande ${o.ref}\nClient : ${o.customer.name}\nE-mail : ${o.customer.email}\nAdresse : ${o.customer.address}\n\nArticles :\n` +
          o.items.map((i) => `- ${i.qty}× ${i.name} (${i.size}/${i.color}) — ${EU(i.price)}`).join("\n") +
          `\n\nSous-total : ${EU(o.subtotal)}\nLivraison : ${EU(o.shipping)}\nTOTAL : ${EU(o.total)}`
        );
      }
    }
  });

  $("#order-filter-status").addEventListener("change", renderOrders);

  /* ---------------- Galerie / Lookbook ---------------- */
  let galleryItems = [];

  function renderGalleryAdmin() {
    const wrap = $("#gallery-grid-admin");
    if (!wrap) return;
    $("#gal-count").textContent = galleryItems.length;
    wrap.innerHTML = "";
    if (!galleryItems.length) {
      wrap.innerHTML = `<p class="empty-note">Aucun visuel téléversé pour l'instant. 🐾</p>`;
      return;
    }
    for (const it of galleryItems) {
      const fig = document.createElement("figure");
      fig.className = "gallery-admin-item";
      fig.innerHTML = `
        <img src="${it.image}" alt="" loading="lazy">
        <strong>${it.title}</strong>
        ${it.category ? `<small class="gallery-admin-cat">${it.category}</small>` : ""}
        <button class="tbl-btn danger" data-gal-del="${it.id}">Supprimer</button>`;
      wrap.appendChild(fig);
    }
  }

  async function loadGallery() {
    try {
      const d = await api("/api/gallery");
      galleryItems = d.items || [];
      renderGalleryAdmin();
    } catch (err) { toast("Galerie : " + err.message, true); }
  }

  $("#gallery-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const note = $("#gallery-note");
    const btn = $("#gf-submit");
    btn.disabled = true;
    note.textContent = "Téléversement en cours… 🚀";
    note.className = "form-note";
    try {
      await fetch("/api/gallery", { method: "POST", body: new FormData(e.target) })
        .then(async (r) => {
          const d = await r.json().catch(() => ({}));
          if (!r.ok || d.ok === false) throw new Error(d.error || `HTTP ${r.status}`);
          return d;
        });
      e.target.reset();
      toast("Visuel publié sur le mur des majestés ✨");
      note.textContent = "";
      await loadGallery();
    } catch (err) {
      note.textContent = "⚠️ " + err.message;
      note.className = "form-note err";
    } finally { btn.disabled = false; }
  });

  $("#gallery-grid-admin").addEventListener("click", async (e) => {
    const del = e.target.closest("[data-gal-del]");
    if (del && confirm("Retirer ce visuel du mur des majestés ?")) {
      try {
        await api(`/api/gallery/${del.dataset.galDel}`, { method: "DELETE" });
        toast("Visuel retiré 🗑");
        await loadGallery();
      } catch (err) { toast(err.message, true); }
    }
  });

  /* ---------------- Réglages ---------------- */
  async function loadSettings() {
    try {
      // Récupérés depuis le rendu serveur (board.html qui passe settings)
      const s = window.SMP_ADMIN_SETTINGS;
      if (!s) return;
      $("#set-announcement").value = s.announcement || "";
      $("#set-herotitle").value = s.hero_title || "";
      $("#set-herosub").value = s.hero_subtitle || "";
      $("#set-fee").value = s.shipping_fee;
      $("#set-threshold").value = s.free_shipping_threshold;
    } catch { /* noop */ }
  }

  $("#settings-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const fd = new FormData(e.target);
    const payload = {
      announcement: fd.get("announcement"),
      hero_title: fd.get("hero_title"),
      hero_subtitle: fd.get("hero_subtitle"),
      shipping_fee: parseFloat(fd.get("shipping_fee") || "0"),
      free_shipping_threshold: parseFloat(fd.get("free_shipping_threshold") || "0"),
    };
    if (fd.get("new_password")) payload.new_password = fd.get("new_password");
    try {
      await api("/api/admin/settings", { method: "POST", body: JSON.stringify(payload) });
      toast("Réglages sauvegardés 💾");
      $("#set-new-password").value = "";
    } catch (err) { toast(err.message, true); }
  });

  /* ---------------- Login ---------------- */
  function showDashboard() {
    $("#admin-login").hidden = true;
    $("#admin-board").hidden = false;
    ensureAdminProducts();
    loadOrders(); loadStats(); loadSettings(); loadGallery();
  }

  $("#login-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const btn = $("#login-btn");
    btn.disabled = true;
    try {
      await api("/api/admin/login", {
        method: "POST",
        body: JSON.stringify({ password: $("#login-password").value }),
      });
      showDashboard();
    } catch (err) {
      $("#login-error").textContent = "⚠️ " + err.message;
      $("#login-error").className = "form-note err";
    } finally { btn.disabled = false; }
  });

  $("#logout-btn").addEventListener("click", async () => {
    try { await api("/api/admin/logout", { method: "POST" }); } catch { /* noop */ }
    location.reload();
  });

  /* ---------------- Init ---------------- */
  api("/api/admin/session").then((d) => {
    if (d.is_admin) showDashboard();
  }).catch(() => {});
})();
