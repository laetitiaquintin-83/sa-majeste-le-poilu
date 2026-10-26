(function () {
  "use strict";

  var CART_KEY = "smp_cart";
  var euro = new Intl.NumberFormat("fr-FR", {
    style: "currency",
    currency: "EUR"
  });
  var cart = readCart();

  function toggleCartDrawer(open = true) {
    var drawer = document.getElementById("cart-drawer");
    var overlay = document.getElementById("cart-overlay");
    if (!drawer || !overlay) {
      return;
    }
    var shouldOpen = open !== false;
    drawer.hidden = !shouldOpen;
    overlay.hidden = !shouldOpen;
    drawer.classList.toggle("is-open", shouldOpen);
    overlay.classList.toggle("is-open", shouldOpen);
    drawer.setAttribute("aria-hidden", String(!shouldOpen));
  }

  function readCart() {
    try {
      var saved = JSON.parse(localStorage.getItem(CART_KEY));
      return normalizeCart(saved);
    } catch (error) {
      return [];
    }
  }

  /* Unifie les deux formats historiques : {id,…} (grille catalogue / app.js)
     et {product_id,…} (ancien script inline produit.html). */
  function normalizeCart(raw) {
    return (Array.isArray(raw) ? raw : []).map(function (item) {
      var pid = Number(item.product_id || item.id);
      if (!Number.isInteger(pid) || pid < 1) {
        return null;
      }
      return {
        id: pid,
        product_id: pid,
        size: String(item.size || "Unique"),
        color: String(item.color || "Standard"),
        qty: Math.max(1, Math.min(99, Number(item.qty) || 1)),
        custom_image_url: String(item.custom_image_url || ""),
        pet_name: String(item.pet_name || "")
      };
    }).filter(Boolean);
  }

  function saveCart() {
    localStorage.setItem(CART_KEY, JSON.stringify(cart));
  }

  function updateCheckoutLink() {
    var link = document.getElementById("checkout-link");
    if (!link) {
      return;
    }
    var empty = cart.length === 0;
    link.disabled = empty;
    link.style.pointerEvents = empty ? "none" : "auto";
    link.style.opacity = empty ? "0.5" : "1";
    link.href = empty ? "#" : "/checkout";
    link.setAttribute("aria-disabled", String(empty));
  }

  function updateCount() {
    document.querySelectorAll("[data-cart-count], .cart-count").forEach(function (element) {
      element.textContent = cart.reduce(function (total, item) {
        return total + (Number(item.qty) || 0);
      }, 0);
    });
  }

  async function uploadPetPhoto(file) {
    var formData = new FormData();
    formData.append("file", file);
    var response = await fetch("/api/upload-pet", {
      method: "POST",
      body: formData
    });
    var data = await response.json();
    if (!response.ok || !data.success || !data.image_url) {
      throw new Error(data.error || "Le téléchargement de la photo a échoué.");
    }
    return data.image_url;
  }

  async function addToCart(button) {
    var id = Number(button.dataset.id || button.dataset.productId);
    if (!Number.isInteger(id) || id < 1) {
      return;
    }
    var sizeOption = document.querySelector("#option-sizes .chip.is-active");
    var colorOption = document.querySelector("#option-colors .chip.is-active");
    var size = sizeOption ? sizeOption.dataset.size :
      (button.dataset.size || "Unique");
    var color = colorOption ? colorOption.dataset.color :
      (button.dataset.color || "Standard");
    var quantityInput = document.getElementById("pdp-qty");
    var requestedQty = quantityInput ? quantityInput.value : button.dataset.qty;
    var qty = Math.max(1, Math.min(99, Number(requestedQty || 1)));
    var customImageUrl = button.dataset.customImageUrl || "";
    var petName = button.dataset.petName || "";
    var photo = document.getElementById("pet-photo");
    var nameInput = document.getElementById("pet-name");
    if (photo && photo.files && photo.files[0]) {
      button.disabled = true;
      try {
        customImageUrl = await uploadPetPhoto(photo.files[0]);
      } catch (error) {
        window.alert(error.message);
        return;
      } finally {
        button.disabled = false;
      }
    }
    if (nameInput) {
      petName = nameInput.value.trim().slice(0, 80);
    }
    var existing = cart.find(function (item) {
      return Number(item.product_id || item.id) === id && item.size === size && item.color === color;
    });
    if (existing) {
      existing.qty = Math.min(99, (Number(existing.qty) || 0) + qty);
      if (customImageUrl) {
        existing.custom_image_url = customImageUrl;
      }
      if (petName) {
        existing.pet_name = petName;
      }
    } else {
      cart.push({
        id: id, product_id: id, size: size, color: color, qty: qty,
        custom_image_url: customImageUrl, pet_name: petName
      });
    }
    saveCart();
    window.dispatchEvent(new Event("cart-updated"));
    toggleCartDrawer(true);
  }

  function removeFromCart(id, size, color) {
    cart = cart.filter(function (item) {
      return !(Number(item.product_id || item.id) === Number(id) &&
        item.size === size && item.color === color);
    });
    saveCart();
    window.dispatchEvent(new Event("cart-updated"));
  }

  function setText(selector, value) {
    document.querySelectorAll(selector).forEach(function (element) {
      element.textContent = value;
    });
  }

  function renderCart(lines) {
    var containers = document.querySelectorAll("#cart-items, #checkout-items");
    containers.forEach(function (container) {
      container.innerHTML = "";
      lines.forEach(function (line) {
        var item = document.createElement("div");
        item.className = "cart-item";
        var info = document.createElement("div");
        info.className = "cart-item-info";
        if (line.custom_image_url) {
          var customImage = document.createElement("img");
          customImage.className = "cart-item-custom-image";
          customImage.src = line.custom_image_url;
          customImage.alt = line.pet_name
            ? "Photo de " + line.pet_name
            : "Photo personnalisée";
          info.appendChild(customImage);
        }
        var name = document.createElement("span");
        name.textContent = line.name;
        var meta = document.createElement("span");
        meta.className = "cart-item-meta";
        meta.textContent = "Taille : " + line.size + " · Couleur : " + line.color +
          " · Quantité : " + line.qty;
        if (line.pet_name) {
          meta.textContent += " · Animal : " + line.pet_name;
        }
        info.append(name, meta);
        var price = document.createElement("strong");
        price.textContent = euro.format(line.price * line.qty);
        item.append(info, price);
        if (container.id === "cart-items") {
          var remove = document.createElement("button");
          remove.type = "button";
          remove.className = "cart-item-remove";
          remove.dataset.remove = line.product_id;
          remove.dataset.size = line.size;
          remove.dataset.color = line.color;
          remove.textContent = "🗑";
          remove.setAttribute("aria-label", "Supprimer cet article");
          remove.title = "Supprimer cet article";
          item.appendChild(remove);
        }
        container.appendChild(item);
      });
      if (lines.length === 0) {
        var empty = document.createElement("p");
        empty.className = "cart-empty";
        empty.textContent = "Votre panier est vide.";
        container.appendChild(empty);
      }
    });
  }

  async function syncQuote() {
    // Relecture du localStorage : couvre aussi les scripts externes qui
    // poussent dans smp_cart puis déclenchent "cart-updated".
    cart = readCart();
    updateCount();
    updateCheckoutLink();
    if (cart.length === 0) {
      renderCart([]);
      setText("[data-cart-subtotal], .cart-subtotal", "0,00 €");
      setText("[data-cart-shipping], .cart-shipping", "—");
      setText("[data-cart-total], .cart-total", "0,00 €");
      return;
    }
    try {
      var response = await fetch("/api/quote", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          items: cart.map(function (item) {
            return {
              product_id: item.id,
              size: item.size,
              color: item.color,
              qty: item.qty,
              custom_image_url: item.custom_image_url || "",
              pet_name: item.pet_name || ""
            };
          })
        })
      });
      if (!response.ok) {
        throw new Error("La quote du panier a échoué.");
      }
      var data = await response.json();
      renderCart(data.lines || []);
      setText("[data-cart-subtotal], .cart-subtotal", euro.format(data.subtotal));
      setText("[data-cart-shipping], .cart-shipping",
        data.shipping === 0 ? "Offerte" : euro.format(data.shipping));
      setText("[data-cart-total], .cart-total", euro.format(data.total));
    } catch (error) {
      renderCart([]);
      var message = document.querySelector("#cart-items");
      if (message) {
        message.innerHTML = "";
        var errorText = document.createElement("p");
        errorText.className = "cart-empty";
        errorText.textContent = "Impossible de synchroniser le panier. Réessayez.";
        message.appendChild(errorText);
      }
    }
  }

  document.addEventListener("click", function (event) {
    var target = event.target;
    var addButton = target && target.closest
      ? target.closest(".add-to-cart-btn")
      : null;
    if (addButton) {
      addToCart(addButton);
      return;
    }
    var removeButton = event.target.closest(".cart-item-remove");
    if (removeButton) {
      removeFromCart(removeButton.dataset.remove, removeButton.dataset.size,
        removeButton.dataset.color);
    }
  });

  function panierPayload(form) {
    var items = cart.map(function (item) {
      return {
        product_id: item.id,
        size: item.size,
        color: item.color,
        qty: item.qty,
        custom_image_url: item.custom_image_url || "",
        pet_name: item.pet_name || ""
      };
    });
    return {
      items: items,
      cgv_accepted: !!(form.elements.cgv && form.elements.cgv.checked),
      customer: {
        name: form.elements.name.value.trim(),
        email: form.elements.email.value.trim(),
        phone: (form.elements.phone ? form.elements.phone.value.trim() : ""),
        address: form.elements.address.value.trim()
      }
    };
  }

  async function submitCheckoutOrder(form, note) {
    var payload = panierPayload(form);
    if (payload.items.length === 0) {
      note.textContent = "Votre panier est vide.";
      note.className = "checkout-note err";
      return;
    }
    // Case CGV obligatoire (le `required` HTML ne suffit pas si la
    // soumission est déclenchée hors constraint validation).
    var cgvBox = form.elements.cgv;
    if (cgvBox && !cgvBox.checked) {
      note.textContent = "Acceptez les CGV pour poursuivre votre commande.";
      note.className = "checkout-note err";
      cgvBox.focus();
      return;
    }
    var button = form.querySelector("#checkout-submit");
    button.disabled = true;
    note.textContent = "Ouverture du paiement sécurisé Stripe… 💳";
    note.className = "checkout-note";
    try {
      // Tunnel Stripe Checkout : le serveur calcule le total réel et renvoie
      // l'URL de la page de paiement stripe.com de la session.
      var response = await fetch("/create-checkout-session", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
      });
      var data = await response.json();
      if (!response.ok || data.ok === false) {
        throw new Error(data.error || "Le paiement n'a pas pu être initialisé.");
      }
      window.location.href = data.checkout_url;
    } catch (error) {
      note.textContent = "⚠️ " + error.message;
      note.className = "checkout-note err";
      button.disabled = false;
    }
  }

  function normalizeSearchValue(value) {
    var normalized = String(value || "").trim().toLocaleLowerCase("fr-FR")
      .normalize("NFD").replace(/[\u0300-\u036f]/g, "");
    normalized = normalized.replace(/[\s_-]+/g, " ");
    return normalized.split(" ").map(function (word) {
      return word.length > 3 && word.endsWith("s") ? word.slice(0, -1) : word;
    }).join(" ");
  }

  function initCatalogueFilters() {
    var filters = document.getElementById("catalogue-filters");
    var searchInput = document.getElementById("catalogue-search-input");
    var grid = document.getElementById("catalogue-grid");
    var empty = document.getElementById("catalogue-empty");
    var results = document.getElementById("catalogue-results");
    if (!filters || !searchInput || !grid || !empty || !results) {
      return;
    }

    var cards = Array.from(grid.querySelectorAll("[data-product-card]"));
    var selectedCategory = "all";

    function applyFilters() {
      var search = normalizeSearchValue(searchInput.value.trim());
      var visibleCount = 0;
      cards.forEach(function (card) {
        var category = normalizeSearchValue(
          card.dataset.category || card.dataset.cat
        );
        var name = normalizeSearchValue(card.dataset.name);
        var visible = (normalizeSearchValue(selectedCategory) === "all" ||
          category === normalizeSearchValue(selectedCategory)) &&
          (!search || name.indexOf(search) !== -1);
        card.hidden = !visible;
        if (visible) {
          visibleCount += 1;
        }
      });
      empty.hidden = visibleCount !== 0;
      results.textContent = visibleCount + " produit" +
        (visibleCount > 1 ? "s" : "") + " trouvé" +
        (visibleCount > 1 ? "s" : "");
    }

    filters.querySelectorAll("[data-cat], [data-category]").forEach(function (button) {
      button.addEventListener("click", function () {
        selectedCategory = button.dataset.category || button.dataset.cat;
        filters.querySelectorAll("[data-cat], [data-category]").forEach(function (other) {
          other.classList.toggle("is-active", other === button);
        });
        applyFilters();
      });
    });
    searchInput.addEventListener("input", applyFilters);
    applyFilters();
  }

  document.addEventListener("DOMContentLoaded", function () {
    // Tout ajout dans le panier (grille, fiche produit, scripts externes)
    // notifie via "cart-updated" : un seul point de resynchronisation.
    window.addEventListener("cart-updated", syncQuote);

    // Commande validée et payée sur Stripe (page de confirmation) : on vide
    // le panier local dans TOUS les cas — la page de confirmation n'a pas de
    // #order-form, l'ancien code placé sous "if (orderForm)" ne s'exécutait
    // donc jamais ici.
    if (window.location.pathname === "/commande-confirmee") {
      cart = [];
      localStorage.removeItem(CART_KEY);
      updateCount();
      updateCheckoutLink();
      renderCart([]);
      setText("[data-cart-subtotal], .cart-subtotal", "0,00 €");
      setText("[data-cart-shipping], .cart-shipping", "—");
      setText("[data-cart-total], .cart-total", "0,00 €");
    }

    var orderForm = document.getElementById("order-form");
    if (orderForm) {
      orderForm.addEventListener("submit", function (event) {
        event.preventDefault();
        submitCheckoutOrder(orderForm,
          document.getElementById("checkout-note"));
      });
    }

    var cartOpen = document.getElementById("cart-open");
    var cartClose = document.getElementById("cart-close");
    var cartOverlay = document.getElementById("cart-overlay");
    if (cartOpen) {
      cartOpen.addEventListener("click", function () {
        toggleCartDrawer(true);
      });
    }
    if (cartClose) {
      cartClose.addEventListener("click", function () {
        toggleCartDrawer(false);
      });
    }
    if (cartOverlay) {
      cartOverlay.addEventListener("click", function () {
        toggleCartDrawer(false);
      });
    }

    document.querySelectorAll("[data-qty-step]").forEach(function (button) {
      button.addEventListener("click", function () {
        var input = document.getElementById("pdp-qty");
        if (input) {
          input.value = Math.max(1, Math.min(99,
            Number(input.value) + Number(button.dataset.qtyStep)));
        }
      });
    });
    var petPhoto = document.getElementById("pet-photo");
    var petPreview = document.getElementById("pet-photo-preview");
    if (petPhoto && petPreview) {
      petPhoto.addEventListener("change", function () {
        var file = petPhoto.files && petPhoto.files[0];
        if (!file) {
          petPreview.hidden = true;
          return;
        }
        var previewImage = petPreview.querySelector("img");
        previewImage.src = URL.createObjectURL(file);
        petPreview.hidden = false;
      });
    }
    initCatalogueFilters();
    initNewsletterForms();
    document.querySelectorAll(".chip").forEach(function (chip) {
      chip.addEventListener("click", function () {
        chip.parentElement.querySelectorAll(".chip").forEach(function (other) {
          other.classList.toggle("is-active", other === chip);
        });
      });
    });
    syncQuote();
  });

  /* ---------------------------------------------------------------- newsletter
     Inscription au Club Privé : fetch POST /newsletter/subscribe, message
     de confirmation fluide affiché sous le formulaire (succès, doublon ou
     erreur), jamais de rechargement de page. */
  function initNewsletterForms() {
    document.querySelectorAll(".newsletter-form").forEach(function (form) {
      form.addEventListener("submit", function (event) {
        event.preventDefault();
        var input = form.querySelector("input[type=email]");
        var note = form.parentElement.querySelector(".newsletter-note");
        var label = form.querySelector(".newsletter-btn-label");
        var loading = form.querySelector(".newsletter-btn-loading");
        var email = (input.value || "").trim();

        form.classList.remove("is-error");
        if (!email || !/^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/.test(email)) {
          form.classList.add("is-error");
          note.textContent = "❌ Altesse juge cette adresse peu royale — vérifiez-la.";
          note.className = "newsletter-note is-error";
          return;
        }

        if (loading) loading.hidden = false;
        if (label) label.hidden = true;
        var button = form.querySelector("button[type=submit]");
        if (button) button.disabled = true;

        fetch("/newsletter/subscribe", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            email: email,
            source: String(input.id || "").replace("newsletter-email-", "") || "home"
          })
        })
          .then(function (response) { return response.json().then(
            function (data) { return { status: response.status, data: data }; });
          })
          .then(function (result) {
            var ok = result.status < 400 && result.data.ok !== false;
            form.classList.toggle("is-error", !ok);
            note.textContent = ok
              ? "✅ " + result.data.message
              : "⚠️ " + (result.data.error || "L'inscription a échoué — réessayez.");
            note.className = "newsletter-note" + (ok ? " is-ok" : " is-error");
            if (ok && !result.data.duplicate) {
              form.classList.add("is-success");
              input.value = "";
            }
          })
          .catch(function () {
            form.classList.add("is-error");
            note.textContent = "⚠️ Le pigeonnier royal est momentanément saturé — réessayez.";
            note.className = "newsletter-note is-error";
          })
          .then(function () {
            if (loading) loading.hidden = true;
            if (label) label.hidden = false;
            if (button) button.disabled = false;
          });
      });
    });
  }
})();
