"""
Sa Majesté le Poilu — Boutique + Dashboard d'administration
Flask + stockage JSON local (data/boutique.json).
Lancement : python app.py  →  http://127.0.0.1:5000
"""
import json
import os
import secrets
import smtplib
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from functools import wraps
from io import BytesIO

from flask import (
    Flask, render_template, request, jsonify, session as flask_session,
    send_file, abort,
)
from werkzeug.utils import secure_filename

APP_DIR = os.path.dirname(os.path.abspath(__file__))

# ---------------------------------------------------------------- .env (minimal, sans dépendance)
def _load_dotenv(path):
    """Charge un fichier .env local (clé=valeur) dans os.environ.

    Les variables déjà présentes dans l'environnement ne sont pas écrasées.
    Le fichier .env est ignoré par git : il ne contient que des secrets locaux.
    """
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = value
    except FileNotFoundError:
        pass

_load_dotenv(os.path.join(APP_DIR, ".env"))

DATA_DIR = os.path.join(APP_DIR, "data")
UPLOAD_DIR = os.path.join(APP_DIR, "static", "uploads")
CUSTOM_PETS_DIR = os.path.join(UPLOAD_DIR, "custom_pets")
PRODUCTS_DIR = os.path.join(UPLOAD_DIR, "products")
GALLERY_DIR = os.path.join(UPLOAD_DIR, "gallery")
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(CUSTOM_PETS_DIR, exist_ok=True)
os.makedirs(PRODUCTS_DIR, exist_ok=True)
os.makedirs(GALLERY_DIR, exist_ok=True)

DB_PATH = os.path.join(DATA_DIR, "boutique.json")
GALLERY_DB_PATH = os.path.join(DATA_DIR, "gallery.json")
DEFAULT_ADMIN_PASSWORD = "poilu2026"  # changez-le dans Réglages du dashboard

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 8 * 1024 * 1024  # 8 Mo max par upload
app.secret_key = os.getenv("FLASK_SECRET_KEY", secrets.token_hex(32))

CATEGORIES = [
    "Mugs",
    "T-shirts",
    "Vêtements Animaux",
    "Accessoires",
    "Portraits Sur-Mesure",
]
LEGACY_CATEGORY_MAP = {"Paillassons": "Accessoires"}
PRODUCT_STATUSES = ["En stock", "Sur commande POD", "Indisponible"]
ORDER_STATUSES = ["Nouvelle", "En préparation", "Expédiée", "Livrée", "Payée"]

# ---------------------------------------------------------------- email
def envoyer_email_confirmation(email_client, nom_client, numero_commande, total):
    """Envoie l'email de confirmation de commande intercepté par Mailtrap."""
    if not email_client:
        print("Aucun email client fourni, envoi ignoré.")
        return False

    smtp_server = os.getenv("MAILTRAP_SMTP_SERVER", "sandbox.smtp.mailtrap.io")
    smtp_port = int(os.getenv("MAILTRAP_SMTP_PORT", 2525))
    # Credentials Mailtrap : passent obligatoirement par l'environnement
    # (.env local), aucun secret en dur dans le dépôt.
    smtp_user = os.environ.get("MAILTRAP_USER", "")
    smtp_password = os.environ.get("MAILTRAP_PASSWORD", "")
    
    msg = MIMEMultipart('alternative')
    msg['Subject'] = f"👑 Commande #{numero_commande} confirmée !"
    msg['From'] = "Sa Majesté le Poilu <contact@samajestelepoilu.fr>"
    msg['To'] = email_client

    html = f"""
        <div style="font-family: Arial, sans-serif; color: #333; max-width: 600px; margin: 0 auto; padding: 20px; border: 1px solid #eee; border-radius: 8px;">
            <h2 style="color: #b45309;">Merci pour votre commande, {nom_client} !</h2>
            <p>Sa Majesté le Poilu a bien reçu votre commande <strong>#{numero_commande}</strong> d'un montant total de <strong>{total:.2f} €</strong>.</p>
            <p>Nos serviteurs préparent votre colis avec le plus grand soin.</p>
            <br>
            <p style="font-style: italic; color: #666;">À très vite sur la cour royale ! 🐾</p>
        </div>
    """
    msg.attach(MIMEText(html, 'html'))

    try:
        server = smtplib.SMTP(smtp_server, smtp_port)
        server.starttls()
        server.login(smtp_user, smtp_password)
        server.sendmail(msg['From'], email_client, msg.as_string())
        server.quit()
        print(f"Email envoyé avec succès dans Mailtrap pour la commande #{numero_commande} !")
        return True
    except Exception as e:
        print(f"Erreur d'envoi Mailtrap : {e}")
        return False

# ---------------------------------------------------------------- données
DEFAULT_DATA = {
    "settings": {
        "hero_title": "Articles officiels pour animaux ROIS & leurs serviteurs dévoués",
        "hero_subtitle": "Altesse juge. Barnabé bave. Nous imprimons. Bienvenue dans le Royaume du Poilu.",
        "announcement": "🐾 Livraison offerte dès 49€ — Sa Majesté approuve",
        "shipping_fee": 4.90,
        "free_shipping_threshold": 49.0,
        "admin_password": DEFAULT_ADMIN_PASSWORD,
        "shipping_address": "Sa Majesté le Poilu, 12 allée des Croquettes, 75001 Paris",
        "contact_email": "contact@samajestelepoilu.fr",
    },
    "products": [],
    "orders": [],
    "next_product_id": 1,
    "next_order_id": 1001,
}

def load_db():
    if not os.path.exists(DB_PATH):
        with open(DB_PATH, "w", encoding="utf-8") as f:
            json.dump(DEFAULT_DATA, f, ensure_ascii=False, indent=2)
    with open(DB_PATH, encoding="utf-8") as f:
        db = json.load(f)
    changed = False
    for product in db.get("products", []):
        category = LEGACY_CATEGORY_MAP.get(product.get("category"))
        if category:
            product["category"] = category
            changed = True
    if changed:
        save_db(db)
    return db

def save_db(db):
    with open(DB_PATH, "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False, indent=2)

def load_gallery():
    if not os.path.exists(GALLERY_DB_PATH):
        save_gallery({"items": [], "next_id": 1})
    with open(GALLERY_DB_PATH, encoding="utf-8") as f:
        g = json.load(f)
    g.setdefault("items", [])
    g.setdefault("next_id", 1)
    return g

def save_gallery(g):
    with open(GALLERY_DB_PATH, "w", encoding="utf-8") as f:
        json.dump(g, f, ensure_ascii=False, indent=2)

def _new_product(db, name, price, cost, category, description,
                 sizes, colors, status, emoji, hue):
    db["products"].append({
        "id": db["next_product_id"],
        "name": name, "price": price, "cost": cost,
        "category": category, "description": description,
        "sizes": sizes, "colors": colors, "status": status,
        "image": None, "emoji": emoji, "hue": hue,
        "created_at": datetime.now().isoformat(),
    })
    db["next_product_id"] += 1

def seed_if_empty():
    db = load_db()
    if db["products"]:
        return
    _new_product(
        db,
        "Mug « Altesse méprise ton café »", 24.90, 7.50, "Mugs",
        "Le mug officiel du mépris matinal. Altesse l'a validé d'un regard distrait.",
        ["Conique", "Classic"], ["Blanc", "Noir"], "En stock", "👑", "#FBBF24",
    )
    _new_product(
        db,
        "T-shirt « Serviteur certifié »", 29.90, 9.80, "T-shirts",
        "Pour afficher fièrement votre rang de personnel de maison.",
        ["S", "M", "L", "XL"], ["Blanc", "Noir", "Rose"], "En stock", "🐕", "#EC4899",
    )
    _new_product(
        db,
        "Paillasson « On s'essuie les pattes »", 34.90, 12.00, "Accessoires",
        "Ordre n°1 du protocole royal, signé Altesse Premier du nom.",
        ["60×40"], ["Bleu"], "Sur commande POD", "🚪", "#3B82F6",
    )
    _new_product(
        db,
        "Médaille « Altesse Iʳᵉ »", 16.90, 5.20, "Accessoires",
        "Identification gravée de la dynastie poilue de votre chat.",
        ["Unique"], ["Or", "Argent"], "En stock", "🎖️", "#F59E0B",
    )
    db["orders"].append({
        "id": db["next_order_id"],
        "ref": f"SMP-{db['next_order_id']}",
        "date": datetime.now().isoformat(),
        "status": "Nouvelle",
        "customer": {
            "name": "Laetitia la Servante dévouée",
            "email": "laetitia@example.fr",
            "address": "1 rue du Réveil Grumpy, 69003 Lyon",
        },
        "items": [{
            "product_id": 1,
            "name": "Mug « Altesse méprise ton café »",
            "size": "Classic", "color": "Blanc", "qty": 1, "price": 24.90,
        }],
        "subtotal": 24.90, "shipping": 4.90, "total": 29.80,
    })
    db["next_order_id"] += 1
    save_db(db)

seed_if_empty()

# ---------------------------------------------------------------- auth admin
def _session_epoch():
    import threading
    if not hasattr(_session_epoch, "lock"):
        _session_epoch.lock = threading.Lock()
    with _session_epoch.lock:
        path = os.path.join(DATA_DIR, "admin_epoch.txt")
        try:
            with open(path, "r", encoding="utf-8") as f:
                return int(f.read().strip() or "0")
        except (FileNotFoundError, ValueError):
            return 0

def bump_session_epoch():
    n = _session_epoch() + 1
    path = os.path.join(DATA_DIR, "admin_epoch.txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write(str(n))

def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if (not flask_session.get("is_admin")
                or flask_session.get("epoch") != _session_epoch()):
            return jsonify(ok=False, error="Session admin requise"), 401
        return f(*args, **kwargs)
    return wrapper

# ---------------------------------------------------------------- pages publiques
@app.route("/")
def accueil():
    db = load_db()
    produits = [p for p in db["products"] if p["status"] != "Indisponible"]
    return render_template(
        "index.html",
        products=produits[:8],
        settings=db["settings"],
        announcement=db["settings"]["announcement"],
    )

@app.route("/catalogue")
def catalogue():
    db = load_db()
    produits = [p for p in db["products"] if p["status"] != "Indisponible"]
    return render_template(
        "catalogue.html",
        products=produits,
        categories=CATEGORIES,
        settings=db["settings"],
    )

@app.route("/produit/<int:pid>")
def produit(pid):
    db = load_db()
    p = next((x for x in db["products"] if x["id"] == pid), None)
    if not p or p["status"] == "Indisponible":
        abort(404)
    autres = [x for x in db["products"]
              if x["id"] != pid and x["status"] != "Indisponible"][:3]
    return render_template(
        "produit.html",
        product=p,
        others=autres,
        settings=db["settings"],
    )

@app.route("/checkout")
def checkout_page():
    db = load_db()
    return render_template(
        "checkout.html",
        title="Commande royale — Checkout",
        settings=db["settings"],
        announcement=db["settings"]["announcement"],
    )

@app.route("/galerie")
def galerie():
    db = load_db()
    g = load_gallery()
    items = sorted(g["items"], key=lambda x: x.get("id", 0), reverse=True)
    return render_template("galerie.html", items=items, settings=db["settings"])

@app.route("/page/<nom>")
def page_contenu(nom):
    pages = {
        "a-propos": ("À propos — L'histoire d'Altesse & Barnabé", "a-propos"),
        "contact": ("Contact", "contact"),
        "mentions": ("Mentions légales", "mentions"),
    }
    if nom not in pages:
        abort(404)
    db = load_db()
    return render_template(
        f"page-{pages[nom][1]}.html",
        title=pages[nom][0],
        settings=db["settings"],
        announcement=db["settings"]["announcement"],
    )

# ---------------------------------------------------------------- admin
def admin_board():
    db = load_db()
    return render_template("admin.html", settings=db["settings"])

app.add_url_rule("/admin", "admin_board", admin_board)

# ---------------------------------------------------------------- quote panier
@app.post("/api/quote")
def api_quote():
    db = load_db()
    d = request.get_json(silent=True) or {}
    lines, subtotal = [], 0.0
    for it in (d.get("items") or []):
        p = next((x for x in db["products"] if x["id"] == it.get("product_id")), None)
        if not p or p["status"] == "Indisponible":
            continue
        qty = max(1, min(99, int(it.get("qty", 1))))
        subtotal += p["price"] * qty
        lines.append({
            "product_id": p["id"], "name": p["name"], "price": p["price"],
            "qty": qty, "size": str(it.get("size", "Unique"))[:32],
            "color": str(it.get("color", "Standard"))[:32],
            "image": p.get("image"), "emoji": p.get("emoji"), "hue": p.get("hue"),
            "custom_image_url": str(it.get("custom_image_url") or "")[:240],
            "pet_name": str(it.get("pet_name") or "")[:80],
            "status": p["status"],
        })
    try:
        fee = float(db["settings"]["shipping_fee"])
        threshold = float(db["settings"]["free_shipping_threshold"])
    except (TypeError, ValueError):
        fee, threshold = 4.90, 49.0
    shipping = 0.0 if (not lines or subtotal >= threshold) else fee
    return jsonify(
        ok=True, lines=lines,
        subtotal=round(subtotal, 2),
        shipping=round(shipping, 2),
        free_shipping_threshold=threshold,
        total=round(subtotal + shipping, 2),
    )

@app.post("/api/upload-pet")
def api_upload_pet():
    uploaded = request.files.get("file")
    if not uploaded or not uploaded.filename:
        return jsonify(success=False, error="Aucune image n'a été fournie."), 400

    safe_name = secure_filename(uploaded.filename)
    extension = os.path.splitext(safe_name)[1].lower()
    allowed_extensions = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
    if extension not in allowed_extensions:
        return jsonify(
            success=False,
            error="Format d'image non pris en charge."
        ), 400

    filename = f"pet_{secrets.token_hex(8)}{extension}"
    uploaded.save(os.path.join(CUSTOM_PETS_DIR, filename))
    return jsonify(
        success=True,
        image_url=f"/static/uploads/custom_pets/{filename}",
    )

# ---------------------------------------------------------------- API galerie
GALLERY_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp"}

@app.get("/api/gallery")
def api_gallery_list():
    g = load_gallery()
    items = sorted(g["items"], key=lambda x: x.get("id", 0), reverse=True)
    return jsonify(ok=True, items=items)

@app.post("/api/gallery")
@login_required
def api_gallery_upload():
    f = request.files.get("image")
    if not f or not f.filename:
        return jsonify(ok=False, error="Aucune image n'a été fournie"), 400

    extension = os.path.splitext(secure_filename(f.filename))[1].lower()
    if extension not in GALLERY_EXTENSIONS:
        return jsonify(
            ok=False,
            error="Format d'image non pris en charge (jpg, png, gif, webp uniquement)",
        ), 400

    d = request.form
    title = ((d.get("title") or "").strip() or "Visuel royal")[:120]
    category = (d.get("category") or "").strip()[:60]

    filename = f"gallery_{secrets.token_hex(8)}{extension}"
    f.save(os.path.join(GALLERY_DIR, filename))

    g = load_gallery()
    item = {
        "id": g["next_id"],
        "title": title,
        "category": category,
        "image": f"/static/uploads/gallery/{filename}",
        "link": "/catalogue",
        "created_at": datetime.now().isoformat(),
    }
    g["items"].append(item)
    g["next_id"] += 1
    save_gallery(g)
    return jsonify(ok=True, item=item), 201

@app.delete("/api/gallery/<int:gid>")
@login_required
def api_gallery_delete(gid):
    g = load_gallery()
    item = next((x for x in g["items"] if x["id"] == gid), None)
    if not item:
        return jsonify(ok=False, error="Visuel introuvable"), 404
    try:
        fname = item["image"].rsplit("/", 1)[-1]
        if fname.startswith("gallery_") and os.path.basename(fname) == fname:
            os.remove(os.path.join(GALLERY_DIR, fname))
    except OSError:
        pass
    g["items"] = [x for x in g["items"] if x["id"] != gid]
    save_gallery(g)
    return jsonify(ok=True)

# ---------------------------------------------------------------- API produits
@app.get("/api/products")
def api_products():
    db = load_db()
    visible = [p for p in db["products"] if p["status"] != "Indisponible"]
    return jsonify(products=visible, categories=CATEGORIES)

def _save_product_image(uploaded, product_id):
    if not uploaded or not uploaded.filename:
        return None
    safe_name = secure_filename(uploaded.filename)
    extension = os.path.splitext(safe_name)[1].lower()
    if extension not in {".jpg", ".jpeg", ".png", ".gif", ".webp"}:
        raise ValueError("Format d'image produit non pris en charge.")
    filename = f"product_{product_id}_{secrets.token_hex(6)}{extension}"
    uploaded.save(os.path.join(PRODUCTS_DIR, filename))
    return f"/static/uploads/products/{filename}"

@app.post("/api/products")
@login_required
def api_create_product():
    db = load_db()
    d = request.form if request.form else (request.json or {})
    try:
        image_path = _save_product_image(
            request.files.get("image_file"), db["next_product_id"]
        )
    except ValueError as error:
        return jsonify(ok=False, error=str(error)), 400
    if not image_path:
        image_path = (d.get("image") or "").strip() or None

    sizes = [s.strip() for s in (d.get("sizes") or "").split(",") if s.strip()]
    colors = [c.strip() for c in (d.get("colors") or "").split(",") if c.strip()]

    try:
        price = float(d.get("price") or 0)
        cost = float(d.get("cost") or 0)
    except (TypeError, ValueError):
        return jsonify(ok=False, error="Prix invalide"), 400

    name = (d.get("name") or "").strip()
    if not name:
        return jsonify(ok=False, error="Le nom du produit est requis"), 400
    if price <= 0:
        return jsonify(ok=False, error="Le prix de vente doit être positif"), 400

    category = d.get("category") if d.get("category") in CATEGORIES else "Accessoires"
    status = d.get("status") if d.get("status") in PRODUCT_STATUSES else "En stock"

    prod = {
        "id": db["next_product_id"],
        "name": name,
        "price": price,
        "cost": cost,
        "category": category,
        "description": (d.get("description") or "").strip(),
        "sizes": sizes or ["Unique"],
        "colors": colors or ["Standard"],
        "status": status,
        "image": image_path,
        "emoji": (d.get("emoji") or "🐾").strip()[:8],
        "hue": (d.get("hue") or "#FBBF24").strip()[:7],
        "created_at": datetime.now().isoformat(),
    }
    db["products"].append(prod)
    db["next_product_id"] += 1
    save_db(db)
    return jsonify(ok=True, product=prod), 201

@app.put("/api/products/<int:pid>")
@login_required
def api_update_product(pid):
    db = load_db()
    prod = next((x for x in db["products"] if x["id"] == pid), None)
    if not prod:
        return jsonify(ok=False, error="Produit introuvable"), 404

    d = request.form if request.form else (request.get_json(silent=True) or {})
    try:
        uploaded_image = _save_product_image(request.files.get("image_file"), pid)
    except ValueError as error:
        return jsonify(ok=False, error=str(error)), 400
    if uploaded_image:
        prod["image"] = uploaded_image
    elif "image" in d:
        prod["image"] = (d.get("image") or "").strip() or None
    for key in ("name", "description", "emoji", "hue"):
        if key in d and isinstance(d[key], str) and d[key].strip():
            prod[key] = d[key].strip()
    if "category" in d and d["category"] in CATEGORIES:
        prod["category"] = d["category"]
    if "status" in d and d["status"] in PRODUCT_STATUSES:
        prod["status"] = d["status"]
    for key in ("price", "cost"):
        if key in d:
            try:
                prod[key] = float(d[key])
            except (TypeError, ValueError):
                pass
    if "sizes" in d:
        prod["sizes"] = [s.strip() for s in d["sizes"].split(",") if s.strip()] or ["Unique"]
    if "colors" in d:
        prod["colors"] = [c.strip() for c in d["colors"].split(",") if c.strip()] or ["Standard"]
    save_db(db)
    return jsonify(ok=True, product=prod)

@app.delete("/api/products/<int:pid>")
@login_required
def api_delete_product(pid):
    db = load_db()
    before = len(db["products"])
    db["products"] = [x for x in db["products"] if x["id"] != pid]
    if len(db["products"]) == before:
        return jsonify(ok=False, error="Produit introuvable"), 404
    save_db(db)
    return jsonify(ok=True)

# ---------------------------------------------------------------- API commandes
def construire_lignes(db, d):
    """Reconstruit les lignes de commande à partir du panier client."""
    lines = []
    for it in (d.get("items") or []):
        p = next((x for x in db["products"] if x["id"] == it.get("product_id")), None)
        if not p or p["status"] == "Indisponible":
            continue
        try:
            qty = max(1, min(99, int(it.get("qty", 1))))
        except (TypeError, ValueError):
            qty = 1
        lines.append({
            "product_id": p["id"], "name": p["name"],
            "size": str(it.get("size", "Unique"))[:32],
            "color": str(it.get("color", "Standard"))[:32],
            "qty": qty, "price": p["price"],
            "custom_image_url": str(it.get("custom_image_url") or "")[:240],
            "pet_name": str(it.get("pet_name") or "")[:80],
        })
    if not lines:
        return None, "Panier vide ou produits indisponibles"
    return lines, None

def frais_de_port(db, subtotal):
    try:
        fee = float(db["settings"]["shipping_fee"])
        threshold = float(db["settings"]["free_shipping_threshold"])
    except (TypeError, ValueError):
        fee, threshold = 4.90, 49.0
    return 0.0 if subtotal >= threshold else fee

def creer_commande(db, lines, cust):
    """Persiste la commande sans envoyer d'email."""
    subtotal = sum(i["qty"] * i["price"] for i in lines)
    shipping = frais_de_port(db, subtotal)
    order = {
        "id": db["next_order_id"],
        "ref": f"SMP-{db['next_order_id']}",
        "date": datetime.now().isoformat(),
        "status": "Nouvelle",
        "customer": {
            "name": (str(cust.get("name") or "Serviteur anonyme").strip())[:80],
            "email": (str(cust.get("email") or "").strip())[:120],
            "address": (str(cust.get("address") or "").strip())[:240],
        },
        "items": lines,
        "subtotal": round(subtotal, 2),
        "shipping": shipping,
        "total": round(subtotal + shipping, 2),
    }
    db["orders"].append(order)
    db["next_order_id"] += 1
    save_db(db)
    return order

def confirmer_et_notifier(db, order):
    """Paiement validé : commande 'Payée' + email de confirmation."""
    order["status"] = "Payée"
    save_db(db)
    envoyer_email_confirmation(
        email_client=order["customer"]["email"],
        nom_client=order["customer"]["name"],
        numero_commande=order["ref"],
        total=order["total"]
    )

@app.post("/api/orders")
def api_create_order():
    """Commande directe sans Stripe."""
    db = load_db()
    d = request.get_json(silent=True) or {}
    lines, err = construire_lignes(db, d)
    if err:
        return jsonify(ok=False, error=err), 400
    order = creer_commande(db, lines, d.get("customer") or {})
    confirmer_et_notifier(db, order)
    return jsonify(ok=True, order={"ref": order["ref"], "total": order["total"]}), 201

# ---------------------------------------------------------------- Stripe
import stripe

STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY", "").strip()
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "").strip()

stripe.api_key = STRIPE_SECRET_KEY

def stripe_actif():
    return (
        STRIPE_SECRET_KEY.startswith("sk_test_") or STRIPE_SECRET_KEY.startswith("rk_test_")
    ) and "VOTRE_CLE_ICI" not in STRIPE_SECRET_KEY

@app.post("/create-checkout-session")
def create_checkout_session():
    db = load_db()
    if not stripe_actif():
        return jsonify(ok=False, error="Stripe non configuré"), 503

    d = request.get_json(silent=True) or {}
    lines, err = construire_lignes(db, d)
    if err:
        return jsonify(ok=False, error=err), 400
    subtotal = sum(i["qty"] * i["price"] for i in lines)
    shipping = frais_de_port(db, subtotal)

    line_items = [{
        "quantity": i["qty"],
        "price_data": {
            "currency": "eur",
            "unit_amount": int(round(i["price"] * 100)),
            "product_data": {
                "name": f"{i['name']} ({i['size']} / {i['color']})",
                "description": (f"Personnalisé pour {i['pet_name']}" if i["pet_name"] else None),
            },
        },
    } for i in lines]
    if shipping > 0:
        line_items.append({
            "quantity": 1,
            "price_data": {
                "currency": "eur",
                "unit_amount": int(round(shipping * 100)),
                "product_data": {"name": "Livraison royale 🚚"},
            },
        })

    cust = d.get("customer") or {}
    try:
        checkout_session = stripe.checkout.Session.create(
            mode="payment",
            # NB : payment_method_types n'est plus supporté par l'API Checkout
            # (dynamic payment methods gérés dans le Dashboard Stripe). Les moyens
            # différés (Klarna/Bancontact) basculent en "unpaid" -> webhook
            # async_payment_succeeded + garde payment_status ci-dessous.
            line_items=line_items,
            success_url=request.host_url.rstrip("/") +
                "/commande-confirmee?session_id={CHECKOUT_SESSION_ID}",
            cancel_url=request.host_url.rstrip("/") + "/checkout?annule=1",
            customer_email=(str(cust.get("email") or "").strip() or None),
            metadata={
                "objet": "smp_commande",
                "payload": json.dumps({
                    "items": [{
                        "product_id": i["product_id"], "size": i["size"],
                        "color": i["color"], "qty": i["qty"],
                        "custom_image_url": i["custom_image_url"],
                        "pet_name": i["pet_name"],
                    } for i in lines],
                    "customer": {
                        "name": str(cust.get("name") or "").strip(),
                        "email": str(cust.get("email") or "").strip(),
                        "address": str(cust.get("address") or "").strip(),
                    },
                }, ensure_ascii=False),
            },
        )
    except (stripe.error.StripeError, ValueError) as e:
        print(f"Erreur création session Stripe : {e}")
        return jsonify(ok=False, error="Erreur Stripe : " + str(e)), 502

    return jsonify(ok=True, checkout_url=checkout_session.url, session_id=checkout_session.id)

def _confirmer_via_stripe(session_id):
    if not stripe_actif():
        return None
    try:
        cs = stripe.checkout.Session.retrieve(session_id)
    except Exception as e:
        print(f"Erreur retrieve session Stripe : {e}")
        return None

    db = load_db()
    cs_id = str(getattr(cs, "id", session_id))
    dedupe_key = "stripe_session_" + cs_id

    for o in db.get("orders", []):
        if o.get("dedupe") == dedupe_key:
            return o

    # Garde-fou : on ne crée/valide la commande que si le paiement est
    # réellement encaissé. Bancontact/Klarna/SEPA laissent la session en
    # "unpaid" (Pending) en attente de confirmation différée.
    payment_status = str(getattr(cs, "payment_status", "") or "")
    if payment_status not in ("paid", "no_payment_required"):
        print(f"Session Stripe {cs_id} non payée (payment_status={payment_status}) — commande non créée.")
        return None

    # stripe-python >= 12 renvoie un StripeObject pour metadata : dict()
    # direct échoue silencieusement -> payload perdu -> "Paiement en attente"
    # persistant même après un paiement réussi (bug vu en production).
    raw_meta_obj = getattr(cs, "metadata", None)
    try:
        raw_meta = raw_meta_obj.to_dict() if hasattr(raw_meta_obj, "to_dict") else dict(raw_meta_obj or {})
    except Exception:
        raw_meta = {}

    payload_str = raw_meta.get("payload", "{}") if raw_meta.get("objet") == "smp_commande" else "{}"

    try:
        payload = json.loads(payload_str)
    except Exception:
        payload = {}

    lines, err = construire_lignes(db, {"items": payload.get("items") or []})
    if err:
        print(f"Session Stripe payée mais panier introuvable : {cs_id}")
        return None

    order = creer_commande(db, lines, payload.get("customer") or {})
    order["dedupe"] = dedupe_key
    order["stripe_session_id"] = cs_id
    save_db(db)
    confirmer_et_notifier(db, order)
    return order

@app.route("/commande-confirmee")
def commande_confirmee():
    session_id = request.args.get("session_id") or ""
    order = _confirmer_via_stripe(session_id) if session_id else None
    db = load_db()
    return render_template(
        "commande-confirmee.html",
        order=order,
        session_id=session_id,
        settings=db["settings"],
        announcement=db["settings"]["announcement"],
    )

@app.post("/webhook-stripe")
def webhook_stripe():
    if STRIPE_WEBHOOK_SECRET.startswith("whsec_") and "VOTRE_SECRET_ICI" not in STRIPE_WEBHOOK_SECRET:
        try:
            event = stripe.Webhook.construct_event(
                request.data, request.headers.get("Stripe-Signature", ""), STRIPE_WEBHOOK_SECRET
            )
        except (ValueError, stripe.error.SignatureVerificationError):
            return jsonify(ok=False), 400
    else:
        try:
            event = json.loads(request.data)
        except (ValueError, TypeError):
            return jsonify(ok=False), 400

    ev_type = event.get("type") if isinstance(event, dict) else getattr(event, "type", "")
    obj = (event.get("data") or {}).get("object") or {}

    if ev_type in ("checkout.session.completed", "checkout.session.async_payment_succeeded"):
        # async_payment_succeeded : paiement différé (Klarna/Bancontact) validé après coup.
        _confirmer_via_stripe(obj.get("id") or "")
    elif ev_type == "checkout.session.async_payment_failed":
        print(f"Paiement différé échoué pour la session {obj.get('id')} — laisser à l'utilisateur retenter.")
    return jsonify(ok=True)

@app.put("/api/orders/<int:oid>")
@login_required
def api_update_order(oid):
    db = load_db()
    o = next((x for x in db["orders"] if x["id"] == oid), None)
    if not o:
        return jsonify(ok=False, error="Commande introuvable"), 404
    new_status = (request.get_json(silent=True) or {}).get("status")
    if new_status not in ORDER_STATUSES:
        return jsonify(ok=False, error="Statut invalide"), 400
    o["status"] = new_status
    save_db(db)
    return jsonify(ok=True, order=o)

@app.get("/api/orders")
@login_required
def api_list_orders():
    db = load_db()
    status = request.args.get("status")
    orders = db["orders"]
    if status in ORDER_STATUSES:
        orders = [o for o in orders if o["status"] == status]
    return jsonify(orders=orders)

# ---------------------------------------------------------------- stats
@app.get("/api/admin/stats")
@login_required
def api_stats():
    db = load_db()
    import calendar
    now = datetime.now()
    month_key = now.strftime("%Y-%m")
    month_orders = [o for o in db["orders"] if o["date"][:7] == month_key]
    ca = sum(o["total"] for o in month_orders)
    marge = 0.0
    ventes = {}
    for o in month_orders:
        for it in o["items"]:
            p = next((x for x in db["products"] if x["id"] == it["product_id"]), None)
            cost = p["cost"] if p else 0
            marge += (it["price"] - cost) * it["qty"]
            ventes[it["name"]] = ventes.get(it["name"], 0) + it["qty"]
    best = max(ventes, key=ventes.get) if ventes else "—"
    return jsonify(
        ca_mois=round(ca, 2),
        commandes_mois=len(month_orders),
        commandes_total=len(db["orders"]),
        meilleur_produit=best,
        marge_mois=round(marge, 2),
        mois_couvert=calendar.month_name[now.month],
    )

# ---------------------------------------------------------------- auth & réglages
@app.post("/api/admin/login")
def api_admin_login():
    d = request.get_json(silent=True) or {}
    db = load_db()
    if d.get("password") == db["settings"]["admin_password"]:
        bump_session_epoch()
        flask_session["is_admin"] = True
        flask_session["epoch"] = _session_epoch()
        return jsonify(ok=True)
    return jsonify(ok=False, error="Mot de passe du greffier incorrect"), 401

@app.post("/api/admin/logout")
def api_admin_logout():
    bump_session_epoch()
    flask_session.clear()
    return jsonify(ok=True)

@app.get("/api/admin/session")
def api_admin_session():
    return jsonify(is_admin=bool(flask_session.get("is_admin")))

@app.post("/api/admin/settings")
@login_required
def api_admin_settings():
    db = load_db()
    d = request.get_json(silent=True) or {}
    for key in ("hero_title", "hero_subtitle", "announcement",
                "shipping_address", "contact_email", "admin_password"):
        if key in d and isinstance(d[key], str) and d[key].strip():
            db["settings"][key] = d[key].strip()
    for key in ("shipping_fee", "free_shipping_threshold"):
        if key in d:
            try:
                db["settings"][key] = float(d[key])
            except (TypeError, ValueError):
                pass
    save_db(db)
    return jsonify(ok=True, settings=db["settings"])

# Handler pour la page 404 (ultra simple, sans dépendance)
@app.errorhandler(404)
def page_not_found(e):
    return render_template('404.html'), 404

if __name__ == "__main__":
    app.run(debug=True, port=5000)

