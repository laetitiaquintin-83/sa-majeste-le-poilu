/* === Sa Majesté le Poilu — theme.js =====================================
   1. Compteur panier (Ajax /cart/add.js — sans recharger la page)
   2. Conserve l'esprit front-only du site statique d'origine.
   ===================================================================== */
(function () {
  "use strict";

  var routes = (window.SMP && window.SMP.routes) || {};
  var ADD_URL = routes.cart_add_url || "/cart/add";
  var CART_URL = routes.cart_url || "/cart";

  function updateCounter(count) {
    var nodes = document.querySelectorAll("[data-cart-count]");
    nodes.forEach(function (n) { n.textContent = count; });
    var aria = document.querySelector("a.cart");
    if (aria) { aria.setAttribute("aria-label", "Panier — " + count + " article(s)"); }
  }

  function flash(btn, label) {
    var original = btn.textContent;
    btn.textContent = label;
    btn.disabled = true;
    setTimeout(function () { btn.textContent = original; btn.disabled = false; }, 1200);
  }

  function refreshCartCount() {
    fetch(CART_URL + ".js", { headers: { "Accept": "application/json" } })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (cart) { if (cart) updateCounter(cart.item_count); })
      .catch(function () { /* silencieux : pas de casse si offline */ });
  }

  document.addEventListener("submit", function (e) {
    var form = e.target.closest("form.product-form, form[action*='/cart/add']");
    if (!form) return;
    e.preventDefault();

    var btn = form.querySelector("[type=submit]");
    if (!btn) return;
    var original = btn.textContent;
    flash(btn, "✓ Ajouté !");

    fetch(ADD_URL + ".js", {
      method: "POST",
      headers: { "Content-Type": "application/json", "Accept": "application/json" },
      body: JSON.stringify({
        id: form.querySelector("input[name=id]").value,
        quantity: 1
      })
    })
      .then(function (r) {
        if (!r.ok) throw new Error("Cart add failed (" + r.status + ")");
        return r.json();
      })
      .then(function () { refreshCartCount(); })
      .catch(function (err) {
        console.warn("[SMP] Ajout panier échoué, fallback form natif :", err);
        form.submit();  // fallback : navigation classique vers /cart/add
      });
  });

  // Initialisation : compte réel du panier au chargement
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", refreshCartCount);
  } else {
    refreshCartCount();
  }
})();
