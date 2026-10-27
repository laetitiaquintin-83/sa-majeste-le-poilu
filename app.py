"""
Sa Majesté le Poilu — Boutique + Dashboard d'administration
Flask + stockage JSON local (data/boutique.json).

Lancement local : python app.py  →  http://127.0.0.1:5000
Production (Render) : gunicorn app:app (voir gunicorn.conf.py) — le port
provient de la variable d'environnement PORT, définie par la plateforme.
"""
import json
import csv
import threading
import os
import secrets
import smtplib
import unicodedata
import re
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from functools import wraps
from io import BytesIO, StringIO

from flask import (
    Flask, render_template, request, jsonify, session as flask_session,
    send_file, send_from_directory, abort, redirect,
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
                # Une variable vide déjà présente ne doit pas empêcher une
                # valeur configurée plus loin dans le fichier d'être chargée.
                if key and not os.environ.get(key):
                    os.environ[key] = value
    except FileNotFoundError:
        pass

_load_dotenv(os.path.join(APP_DIR, ".env"))

# Sur Render (production), DATA_DIR pointe vers un disque persistant pour que
# commandes/abonnés survivent aux redéploiements. En local, chemins par défaut.
DATA_DIR = os.environ.get("DATA_DIR", "").strip() or os.path.join(APP_DIR, "data")
UPLOAD_DIR = os.environ.get("UPLOAD_DIR", "").strip() or os.path.join(APP_DIR, "static", "uploads")
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


# Les uploads sont référencés par des URL /static/uploads/... Dans le
# déploiement Render, UPLOAD_DIR peut pointer vers un disque persistant hors
# de static/ : cette règle (plus spécifique que /static/<path:filename>) sert
# alors les fichiers depuis le bon répertoire. En local, elle sert le même
# dossier que Flask, sans changement de comportement.
@app.get("/static/uploads/<path:filename>")
def _servir_upload(filename):
    return send_from_directory(UPLOAD_DIR, filename)

CATEGORIES = [
    "Mugs",
    "T-shirts",
    "Vêtements Animaux",
    "Chats",
    "Chiens",
    "Accessoires",
    "Portraits Sur-Mesure",
]
LEGACY_CATEGORY_MAP = {"Paillassons": "Accessoires"}
PRODUCT_STATUSES = ["En stock", "Sur commande POD", "Indisponible"]
ORDER_STATUSES = ["Nouvelle", "En préparation", "Expédiée", "Livrée", "Payée"]

# ---------------------------------------------------------------- email
# SMTP transactionnel générique : variables MAIL_* du .env (Gmail par défaut),
# avec repli sur l'ancienne config Mailtrap pour compatibilité.
EMAIL_EXPEDITEUR = "Sa Majesté le Poilu <samajestelepoilu@gmail.com>"


def _smtp_params():
    """(serveur, port, tls, user, mot_de_passe) — Mailtrap en secours."""
    server = os.getenv("MAIL_SERVER", "").strip() or os.getenv(
        "MAILTRAP_SMTP_SERVER", "sandbox.smtp.mailtrap.io").strip()
    port = int(os.getenv("MAIL_PORT") or os.getenv("MAILTRAP_SMTP_PORT") or 587)
    tls_raw = os.getenv("MAIL_USE_TLS", "").strip().lower()
    use_tls = (tls_raw != "false") if tls_raw else True
    user = os.getenv("MAIL_USERNAME", "").strip() or os.environ.get("MAILTRAP_USER", "")
    password = os.getenv("MAIL_PASSWORD", "").strip() or os.environ.get("MAILTRAP_PASSWORD", "")
    return server, port, use_tls, user, password


def envoyer_email(email_client, sujet, html_corps):
    """Envoie un email transactionnel via le SMTP configuré (MAIL_*).

    Un email destinataire vide est ignoré (pas d'erreur bloquante pour le
    tunnel de commande).
    """
    if not email_client:
        print("Aucun email client fourni, envoi ignoré.")
        return False
    smtp_server, smtp_port, use_tls, smtp_user, smtp_password = _smtp_params()

    msg = MIMEMultipart('alternative')
    msg['Subject'] = sujet
    msg['From'] = EMAIL_EXPEDITEUR
    msg['To'] = email_client
    msg.attach(MIMEText(html_corps, 'html'))

    try:
        server = smtplib.SMTP(smtp_server, smtp_port, timeout=20)
        if use_tls:
            server.starttls()
        if smtp_user and smtp_password:
            server.login(smtp_user, smtp_password)
        server.sendmail(smtp_user or msg['From'], email_client, msg.as_string())
        server.quit()
        print(f"Email envoyé ({smtp_server}) : {sujet} -> {email_client}")
        return True
    except Exception as e:
        print(f"Erreur d'envoi SMTP ({smtp_server}) : {e}")
        return False


def envoyer_email_confirmation(email_client, nom_client, numero_commande, total):
    """Email de confirmation de commande (premier contact post-paiement)."""
    html = f"""
        <div style="font-family: Arial, sans-serif; color: #333; max-width: 600px; margin: 0 auto; padding: 20px; border: 1px solid #eee; border-radius: 8px;">
            <h2 style="color: #b45309;">Merci pour votre commande, {nom_client} !</h2>
            <p>Sa Majesté le Poilu a bien reçu votre commande <strong>#{numero_commande}</strong> d'un montant total de <strong>{total:.2f} €</strong>.</p>
            <p>Nos serviteurs préparent votre colis avec le plus grand soin.</p>
            <p>Vous recevrez un second message dès que le colis sera expédié, avec son numéro de suivi.</p>
            <p>Vous pouvez suivre votre commande à tout moment sur <a href="/suivi">la page « Où est ma commande ? »</a>.</p>
            <br>
            <p style="font-style: italic; color: #666;">À très vite sur la cour royale ! 🐾</p>
        </div>
    """
    return envoyer_email(
        email_client,
        f"👑 Commande #{numero_commande} confirmée !",
        html,
    )


def _transporteur_client(value):
    """Nom de transporteur affiché au client, sans marque de prestataire.

    Les transporteurs remontés par nos partenaires logistiques peuvent
    porter leur nom (ex. « CJ Packet ») : la boutique communique uniquement
    sous l'identité Sa Majesté le Poilu.
    """
    texte = str(value or "").strip()
    if not texte:
        return ""
    if re.search(r"\bCJ\b|CJ ?Packet|CJ ?Dropshipping|Gelato|Printify|Brevo",
                 texte, re.IGNORECASE):
        return ""
    return texte[:60]

def envoyer_email_suivi(email_client, nom_client, numero_commande,
                        transporteur, numero_suivi, url_suivi):
    """Email « votre colis est expédié » envoyé dès qu'un n° de suivi apparaît."""
    lien_suivi = url_suivi or (
        f"https://www.17track.net/fr/track?nums={numero_suivi}"
        if numero_suivi else "/suivi"
    )
    bouton = f"""
        <p style="text-align: center; margin: 24px 0;">
          <a href="{lien_suivi}" style="background: #b45309; color: #fff; padding: 12px 24px;
             border-radius: 999px; text-decoration: none; font-weight: bold;">📦 Suivre mon colis</a>
        </p>""" if numero_suivi else ""
    html = f"""
        <div style="font-family: Arial, sans-serif; color: #333; max-width: 600px; margin: 0 auto; padding: 20px; border: 1px solid #eee; border-radius: 8px;">
            <h2 style="color: #b45309;">Votre colis est en route, {nom_client} ! 🚚</h2>
            <p>La commande <strong>#{numero_commande}</strong> a quitté le royaume et est entre de bonnes mains.</p>
            {f'<p>Transporteur : <strong>{_transporteur_client(transporteur) or "Service de livraison"}</strong><br>Numéro de suivi : <strong>{numero_suivi}</strong></p>' if numero_suivi else '<p>Le numéro de suivi sera disponible très prochainement.</p>'}
            {bouton}
            <p style="font-style: italic; color: #666;">Livraison estimée sous 3 à 5 jours ouvrés pour les articles imprimés, sous 7 à 14 jours ouvrés pour les autres. Altesse guette le facteur. 🐾</p>
        </div>
    """
    return envoyer_email(
        email_client,
        f"🚚 Votre commande #{numero_commande} est expédiée !",
        html,
    )

# ---------------------------------------------------------------- données
DEFAULT_DATA = {
    "settings": {
        "hero_title": "Articles officiels pour animaux ROIS & leurs serviteurs dévoués",
        "hero_subtitle": "Altesse juge. Barnabé bave. Nous imprimons. Bienvenue dans le Royaume du Poilu.",
        "announcement": "🐾 Livraison offerte dès 49€ — Sa Majesté approuve",
        "shipping_fee": 4.90,
        "free_shipping_threshold": 49.0,
        "admin_password": DEFAULT_ADMIN_PASSWORD,
        "shipping_address": "Le Royaume est 100 % en ligne (aucun accueil du public).",
        "contact_email": "samajestelepoilu@gmail.com",
    },
    "products": [],
    "orders": [],
    "next_product_id": 1,
    "next_order_id": 1001,
}

_DB_LOCK = threading.Lock()  # sérialise l'accès au JSON (tâche de fond CJ incluse)

def _canonical_supplier(product):
    raw_supplier = str(
        product.get("supplier") or product.get("fournisseur") or ""
    ).strip()
    normalized = raw_supplier.casefold()
    if product.get("cj_product_id") or normalized in {
        "cj", "cj dropshipping", "cjdropshipping",
    }:
        return "CJ"
    if normalized == "gelato":
        return "Gelato"
    if normalized == "printify":
        return "Printify"
    return raw_supplier

def _apply_customization_policy(product):
    supplier = _canonical_supplier(product)
    changed = bool(supplier) and product.get("supplier") != supplier
    if supplier:
        product["supplier"] = supplier
    customizable = supplier in {"Gelato", "Printify"}
    if product.get("is_customizable") is not customizable:
        product["is_customizable"] = customizable
        changed = True
    return changed

def load_db():
    with _DB_LOCK:
        return _load_db_unlocked()

def _load_db_unlocked():
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
        if _apply_customization_policy(product):
            changed = True
    if changed:
        with open(DB_PATH, "w", encoding="utf-8") as f:
            json.dump(db, f, ensure_ascii=False, indent=2)
    return db

def save_db(db):
    with _DB_LOCK:
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
                 sizes, colors, status, emoji, hue, supplier=None):
    db["products"].append({
        "id": db["next_product_id"],
        "name": name, "price": price, "cost": cost,
        "category": category, "description": description,
        "sizes": sizes, "colors": colors, "status": status,
        "image": None, "emoji": emoji, "hue": hue,
        "created_at": datetime.now().isoformat(),
        "supplier": supplier,
        "is_customizable": supplier in {"Gelato", "Printify"},
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
        "Gelato",
    )
    _new_product(
        db,
        "T-shirt « Serviteur certifié »", 29.90, 9.80, "T-shirts",
        "Pour afficher fièrement votre rang de personnel de maison.",
        ["S", "M", "L", "XL"], ["Blanc", "Noir", "Rose"], "En stock", "🐕", "#EC4899",
        "Gelato",
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

@app.get("/api/cj/products")
def api_cj_products():
    """Route de test interne : recherche de produits CJ en JSON."""
    from cj_service import CJAPIError, search_products

    keyword = request.args.get("keyword", "").strip()
    if not keyword:
        return jsonify(ok=False, error="Le paramètre keyword est obligatoire."), 400
    try:
        data = search_products(
            keyword,
            page_num=request.args.get("page_num", default=1, type=int),
            page_size=request.args.get("page_size", default=20, type=int),
        )
    except ValueError as exc:
        return jsonify(ok=False, error=str(exc)), 400
    except CJAPIError as exc:
        return jsonify(ok=False, error=str(exc)), 502
    return jsonify(ok=True, data=data)

@app.get("/admin/cj-search")
def admin_cj_search():
    if not flask_session.get("is_admin") or flask_session.get("epoch") != _session_epoch():
        return redirect("/admin")
    return render_template("cj-search.html")

@app.post("/admin/cj-sync/run")
@app.post("/api/admin/cj/sync")
@login_required
def api_cj_sync_run():
    """Lance manuellement une passe de synchronisation stocks/prix CJ."""
    from cj_sync import sync_products
    try:
        report = sync_products()
    except CJAPIError as exc:
        return jsonify(ok=False, error=str(exc)), 502
    except Exception as exc:  # noqa: BLE001
        return jsonify(ok=False, error=f"Échec de la synchro : {exc}"), 500
    return jsonify(ok=True, report=report)


@app.get("/admin/cj-sync/status")
@app.get("/api/admin/cj/status")
@login_required
def api_cj_sync_status():
    """Rapport de la dernière passe de synchronisation CJ."""
    from cj_sync import load_state
    return jsonify(ok=True, state=load_state())


@app.get("/api/cj/import-preview")
@login_required
def api_cj_import_preview():
    """Pré-remplissage de la modale d'import : tout ce que l'import enrichi
    saurait calculer (titre FR, coût CJ, tailles/couleurs des variantes,
    description FR), sans rien écrire dans la boutique.

    La modale d'import (templates/cj-search.html) appelle cet endpoint au
    clic sur « Importer » — les champs CJ de la liste de recherche sont
    souvent vides, c'est ici qu'on obtient les vraies données.
    """
    from cj_service import (
        CJAPIError, extract_image_urls, extract_size_guide_url,
        extract_specs, get_product_details, get_variant_list,
        translate_product_title, translate_to_french,
    )

    cj_id = str(request.args.get("cj_pid") or "").strip()
    if not cj_id:
        return jsonify(ok=False, error="cj_pid est obligatoire"), 400
    try:
        product = get_product_details(cj_id)
    except ValueError as exc:
        return jsonify(ok=False, error=str(exc)), 400
    except CJAPIError as exc:
        return jsonify(ok=False, error=str(exc)), 502

    name_en = (product.get("productNameEn") or product.get("nameEn") or
               product.get("productName") or product.get("name") or "").strip()
    name = (translate_to_french(name_en) or translate_product_title(name_en) or name_en)

    html_desc = str(product.get("description") or "")
    text_desc = re.sub(r"<br\s*/?>", "\n", html_desc, flags=re.IGNORECASE)
    text_desc = re.sub(r"<[^>]+>", " ", text_desc)
    text_desc = re.sub(r"https?://\S+", " ", text_desc)
    description = re.sub(r"[\n ]{2,}", " ", text_desc).strip()
    if description:
        description = (translate_to_french(description[:4000]) or description)
        description = description.split("Image du produit")[0].strip(" :-\n")
        description = re.sub(r"\s{2,}", " ", description).strip()

    def _norm(value):
        text = unicodedata.normalize("NFD", str(value or "").lower())
        return "".join(c for c in text if unicodedata.category(c) != "Mn").strip()

    _UNIQUE_SIZE_HINTS = (
        "comme", "figure", "photo", "unique", "one size", "standard",
        "taille libre", "as shown",
    )

    def _fr_option(en_text, kind):
        """Traduit une option CJ EN→FR (MyMemory, secours dictionnaire)."""
        text = re.sub(r"\s+", " ", str(en_text or "")).strip()
        if not text:
            return None
        translated = translate_to_french(text)
        if not translated:
            translated = translate_product_title(text) or None
        if translated:
            translated = _norm(translated).capitalize()
            low = _norm(translated)
            if kind == "size" and any(h in low for h in _UNIQUE_SIZE_HINTS):
                return "Unique"
        return translated

    variant_entries = []
    try:
        variants_cj = get_variant_list(cj_id)
    except (CJAPIError, ValueError) as exc:
        print(f"Variantes CJ indisponibles pour {cj_id} : {exc}")
        variants_cj = []
    for v in variants_cj:
        vid = str(v.get("vid") or "").strip()
        if not vid:
            continue
        key = str(v.get("variantKey") or v.get("variantNameEn") or
                  v.get("variantName") or "").strip()
        parts = [p.strip() for p in key.split("-") if p.strip()]
        color_en = parts[0] if parts else ""
        size_en = parts[-1] if len(parts) > 1 else ""
        try:
            v_price = float(v.get("variantSellPrice") or 0) or None
        except (TypeError, ValueError):
            v_price = None
        variant_entries.append({
            "vid": vid,
            "sku": str(v.get("variantSku") or "").strip(),
            "color": _fr_option(color_en, "color") or "Standard",
            "size": _fr_option(size_en, "size") or "Unique",
            "price": v_price,
            "image": str(v.get("variantImage") or "").strip() or None,
        })
    sizes = sorted({e["size"] for e in variant_entries if e["size"]})
    colors = sorted({e["color"] for e in variant_entries if e["color"]})

    # Prix d'achat CJ : sellPrice du produit, sinon le plus bas des variantes.
    try:
        cj_cost_price = float(product.get("sellPrice") or
                              product.get("nowPrice") or
                              product.get("discountPrice") or 0)
    except (TypeError, ValueError):
        cj_cost_price = 0.0
    if cj_cost_price <= 0 and variant_entries:
        cj_cost_price = min((e["price"] or 0) for e in variant_entries) or 0

    specs = extract_specs(product)
    for spec in specs:
        spec["value"] = (translate_to_french(spec["value"]) or spec["value"])[:120]
    image_urls = extract_image_urls(product)

    return jsonify(ok=True, preview={
        "cj_pid": cj_id,
        "title": name[:200],
        "name": name[:200],
        "cost": round(cj_cost_price, 2) or None,
        "sizes": sizes or ["Unique"],
        "colors": colors or ["Standard"],
        "description": description[:5000],
        "image": image_urls[0] if image_urls else None,
        "images": image_urls,
        "variants": variant_entries,
        "specs": specs,
        "size_guide_url": extract_size_guide_url(product),
    })


@app.post("/admin/cj-import")
@app.post("/api/admin/cj/import")
@login_required
def api_cj_import():
    from cj_service import (
        CJAPIError, extract_image_urls, extract_size_guide_url,
        extract_specs, get_product_details, get_variant_list,
        translate_product_title, translate_to_french,
    )

    submitted = request.get_json(silent=True) or request.form
    cj_id = str(submitted.get("cj_pid") or "").strip()
    if not cj_id:
        return jsonify(ok=False, error="cj_pid est obligatoire"), 400
    try:
        sale_price = float(submitted.get("sale_price") or submitted.get("price") or 0)
    except (TypeError, ValueError):
        return jsonify(ok=False, error="Prix de vente invalide"), 400
    if sale_price <= 0:
        return jsonify(ok=False, error="Le prix de vente doit être positif"), 400
    sizes = [item.strip() for item in str(submitted.get("sizes") or "").split(",")
             if item.strip()]
    colors = [item.strip() for item in str(submitted.get("colors") or "").split(",")
              if item.strip()]

    db = load_db()
    if cj_id and any(str(item.get("cj_product_id")) == cj_id
                     for item in db["products"]):
        return jsonify(ok=False, error="Ce produit CJ est déjà importé"), 409

    try:
        product = get_product_details(cj_id)
    except ValueError as exc:
        return jsonify(ok=False, error=str(exc)), 400
    except CJAPIError as exc:
        return jsonify(ok=False, error=str(exc)), 502

    # CJ renvoie souvent le titre d'origine en chinois dans "productName" :
    # on part de la version anglaise puis on traduit réellement EN→FR.
    name_en = (product.get("productNameEn") or product.get("nameEn") or
               product.get("productName") or product.get("name") or "").strip()
    if not name_en:
        return jsonify(ok=False, error="Nom du produit CJ manquant"), 502
    edited_name = str(submitted.get("name") or "").strip()
    if edited_name:
        name = edited_name
    else:
        # Traduction réelle EN→FR (MyMemory) ; dictionnaire local en secours.
        name = translate_to_french(name_en) or translate_product_title(name_en)
    if not name:
        return jsonify(ok=False, error="Le titre du produit est obligatoire"), 400

    description = re.sub(
        r"<[^>]+>", " ",
        str(submitted.get("description") or "").strip(),
    )
    if not description:
        html_desc = str(product.get("description") or "")
        text_desc = re.sub(r"<br\s*/?>", "\n", html_desc, flags=re.IGNORECASE)
        text_desc = re.sub(r"<[^>]+>", " ", text_desc)
        # Les blocs <img> de la description CJ sont remplacés par la galerie
        # dédiée (extract_image_urls) : on retire leurs URLs du texte.
        text_desc = re.sub(r"https?://\S+", " ", text_desc)
        description = re.sub(r"[\n ]{2,}", " ", text_desc).strip()
    if description:
        # Traduction réelle EN→FR ; texte source conservé si indisponible.
        description = (translate_to_french(description[:4000]) or description)
    else:
        description = "Article de la Cour, sélectionné avec soin pour votre compagnon.",
    # En-tête résiduel du bloc « Product Image » de CJ (les images vont dans
    # la galerie dédiée) : on le retire de la description.
    description = description.split("Image du produit")[0].strip(" :-\n")
    description = re.sub(r"\s{2,}", " ", description).strip()

    # ---- Variantes CJ : couleurs/motifs + tailles, traduits en français ----
    try:
        variants_cj = get_variant_list(cj_id)
    except (CJAPIError, ValueError) as exc:
        print(f"Variantes CJ indisponibles pour {cj_id} : {exc}")
        variants_cj = []

    _UNIQUE_SIZE_HINTS = (
        "comme", "figure", "photo", "unique", "one size", "standard",
        "taille libre", "as shown",
    )

    def _fr_option(en_text, kind):
        """Traduit une option CJ EN→FR (MyMemory, secours dictionnaire)."""
        text = re.sub(r"\s+", " ", str(en_text or "")).strip()
        if not text:
            return None
        translated = translate_to_french(text)
        if not translated:
            translated = translate_product_title(text) or None
        if translated:
            translated = re.sub(r"\s+", " ", translated).strip().capitalize()
            low = translated.lower()
            if kind == "size" and any(h in low for h in _UNIQUE_SIZE_HINTS):
                return "Unique"
        return translated

    variant_entries = []
    for v in variants_cj:
        vid = str(v.get("vid") or "").strip()
        if not vid:
            continue
        key = str(v.get("variantKey") or v.get("variantNameEn") or
                  v.get("variantName") or "").strip()
        parts = [p.strip() for p in key.split("-") if p.strip()]
        color_en = parts[0] if parts else ""
        size_en = parts[-1] if len(parts) > 1 else ""
        try:
            v_price = float(v.get("variantSellPrice") or 0) or None
        except (TypeError, ValueError):
            v_price = None
        variant_entries.append({
            "vid": vid,
            "sku": str(v.get("variantSku") or "").strip(),
            "color": _fr_option(color_en, "color") or "Standard",
            "size": _fr_option(size_en, "size") or "Unique",
            "price": v_price,
            "image": str(v.get("variantImage") or "").strip() or None,
        })
    if not sizes:
        sizes = sorted({e["size"] for e in variant_entries if e["size"]})
    if not colors:
        colors = sorted({e["color"] for e in variant_entries if e["color"]})
    category = str(submitted.get("category") or "Accessoires").strip()
    if category not in CATEGORIES:
        return jsonify(ok=False, error="Catégorie invalide"), 400
    try:
        cj_cost_price = float(product.get("sellPrice") or
                              product.get("nowPrice") or
                              product.get("discountPrice") or 0)
    except (TypeError, ValueError):
        cj_cost_price = 0.0
    # Le "sellPrice" produit est souvent absent : le plus bas des prix de
    # variantes est le vrai plancher d'achat CJ.
    if cj_cost_price <= 0 and variant_entries:
        cj_cost_price = min((e["price"] or 0) for e in variant_entries) or 0
    submitted_cost = str(submitted.get("cost") or "").strip()
    if submitted_cost:
        try:
            cost_price = float(submitted_cost)
        except (TypeError, ValueError):
            return jsonify(ok=False, error="Prix d'achat invalide"), 400
    else:
        cost_price = cj_cost_price
    if cost_price < 0:
        return jsonify(ok=False, error="Le prix d'achat ne peut pas être négatif"), 400

    # Galerie complète CJ (productImageSet / productImage), guide des
    # tailles et spécifications structurées.
    image_urls = extract_image_urls(product)
    image = image_urls[0] if image_urls else None
    size_guide_url = extract_size_guide_url(product)
    specs = extract_specs(product)
    for spec in specs:
        spec["value"] = (translate_to_french(spec["value"]) or spec["value"])[:120]

    imported = {
        "id": db["next_product_id"],
        "name": name[:200],
        "price": sale_price,
        "cost": cost_price,
        "category": category,
        "description": description[:5000],
        "sizes": sizes or ["Unique"],
        "colors": colors or ["Standard"],
        "status": "En stock",
        "image": image,
        "images": image_urls,
        "emoji": "🐾",
        "hue": "#FBBF24",
        "created_at": datetime.now().isoformat(),
        "cj_product_id": cj_id,
        "supplier": "CJ",
        "is_customizable": False,
        "cj_variants": variant_entries,
        "cj_specs": specs,
        "size_guide_url": size_guide_url,
        "cj_synced_at": datetime.now().isoformat(timespec="seconds"),
        "cj_variant_id": (
            # produit monovariante : vid direct ; sinon résolu à la commande
            (variant_entries[0]["vid"] if len(variant_entries) == 1 else "")
            or str(submitted.get("cj_vid") or submitted.get("cj_variant_id") or "").strip()
        ) or None,
    }
    db["products"].append(imported)
    db["next_product_id"] += 1
    save_db(db)
    return jsonify(ok=True, product=imported), 201

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

# ---------------------------------------------------------------- Gazette (blog)
BLOG_PATH = os.path.join(DATA_DIR, "blog.json")


def load_blog():
    """Charge la Gazette ; fichier de secours auto-créé si absent."""
    if not os.path.exists(BLOG_PATH):
        default = {"posts": [], "next_id": 1}
        with open(BLOG_PATH, "w", encoding="utf-8") as f:
            json.dump(default, f, ensure_ascii=False, indent=2)
        return default
    with open(BLOG_PATH, encoding="utf-8") as f:
        b = json.load(f)
    b.setdefault("posts", [])
    b.setdefault("next_id", 1)
    return b


def _date_affichage(iso):
    """2026-10-16 -> 16 octobre 2026, pour l'affichage de cour."""
    mois = ["", "janvier", "février", "mars", "avril", "mai", "juin", "juillet",
            "août", "septembre", "octobre", "novembre", "décembre"]
    try:
        y, m, d = str(iso)[:10].split("-")
        return f"{int(d)} {mois[int(m)]} {y}"
    except (ValueError, IndexError):
        return str(iso)


def _produits_mis_en_avant(db, refs):
    """Résout featured_products : ids locaux (int) ou cj_product_id (str)."""
    produits = []
    for ref in (refs or []):
        if isinstance(ref, int):
            p = next((x for x in db["products"] if x["id"] == ref), None)
        else:
            ref = str(ref).strip()
            p = next((x for x in db["products"]
                      if str(x.get("cj_product_id") or "") == ref or x["id"] == ref), None)
        if p and p["status"] != "Indisponible" and all(x["id"] != p["id"] for x in produits):
            produits.append(p)
    return produits


@app.route("/gazette")
def gazette():
    db = load_db()
    posts = sorted(load_blog()["posts"], key=lambda x: str(x.get("date") or "")[:10], reverse=True)
    return render_template(
        "gazette.html",
        posts=posts,
        date_affichage=_date_affichage,
        settings=db["settings"],
        announcement=db["settings"]["announcement"],
    )


@app.route("/gazette/<slug>")
def gazette_article(slug):
    db = load_db()
    post = next((p for p in load_blog()["posts"] if p.get("slug") == slug), None)
    if not post:
        abort(404)
    produits = _produits_mis_en_avant(db, post.get("featured_products"))
    autres = [p for p in sorted(
        load_blog()["posts"], key=lambda x: str(x.get("date") or "")[:10], reverse=True
    ) if p.get("slug") != slug][:3]
    return render_template(
        "gazette_article.html",
        post=post,
        produits=produits,
        autres=autres,
        date_affichage=_date_affichage,
        settings=db["settings"],
        announcement=db["settings"]["announcement"],
    )

@app.route("/page/<nom>")
def page_contenu(nom):
    pages = {
        "a-propos": ("À propos — L'histoire d'Altesse & Barnabé", "a-propos"),
        "contact": ("Contact", "contact"),
        "mentions": ("Mentions légales", "mentions"),
        "cgv": ("Conditions générales de vente", "cgv"),
        "retractation": ("Droit de rétractation", "retractation"),
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
            "custom_image_url": (
                str(it.get("custom_image_url") or "")[:240]
                if p.get("is_customizable") else ""
            ),
            "pet_name": (
                str(it.get("pet_name") or "")[:80]
                if p.get("is_customizable") else ""
            ),
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

# ---------------------------------------------------------------- API newsletter
SUBSCRIBERS_PATH = os.path.join(DATA_DIR, "subscribers.json")

_EMAIL_RE = re.compile(r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9]"
                       r"(?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
                       r"(?:\.[A-Za-z]{2,})+$")


def load_subscribers():
    if not os.path.exists(SUBSCRIBERS_PATH):
        return {"subscribers": [], "next_id": 1}
    with open(SUBSCRIBERS_PATH, encoding="utf-8") as f:
        s = json.load(f)
    s.setdefault("subscribers", [])
    s.setdefault("next_id", 1)
    return s


def save_subscribers(s):
    with open(SUBSCRIBERS_PATH, "w", encoding="utf-8") as f:
        json.dump(s, f, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------- Brevo (optionnel)
# Contact Brevo (ex-Sendinblue) créé à chaque inscription quand BREVO_API_KEY
# est présent dans l'environnement (.env en local, Dashboard > Environment sur
# Render). Sans clé : zéro appel réseau, la sauvegarde locale suffit.
BREVO_CONTACTS_URL = "https://api.brevo.com/v3/contacts"


def brevo_configure():
    return bool(os.getenv("BREVO_API_KEY", "").strip())


def brevo_ajouter_contact(email):
    """Crée/met à jour le contact dans la liste Brevo (updateEnabled: true).

    Toute erreur est loguée sans remonter : l'inscription locale est déjà
    enregistrée, Brevo ne doit jamais faire échouer le formulaire.
    """
    api_key = os.getenv("BREVO_API_KEY", "").strip()
    if not api_key:
        return False
    try:
        list_id = int(os.getenv("BREVO_LIST_ID", "2") or 2)
    except ValueError:
        list_id = 2
    try:
        import requests
        r = requests.post(
            BREVO_CONTACTS_URL,
            headers={
                "accept": "application/json",
                # Nom exact attendu par Brevo : « api-key » (doc officielle),
                # et valeur de type xkeysib-… (les clés xsmtpsib- sont SMTP).
                "api-key": api_key,
                "Content-Type": "application/json",
            },
            json={"email": email, "listIds": [list_id], "updateEnabled": True},
            timeout=15,
        )
        if r.status_code in (200, 201):
            print(f"Brevo : contact enregistré (liste {list_id}) — {email}")
            return True
        print(f"Brevo : échec HTTP {r.status_code} — {r.text[:200]}")
    except Exception as exc:  # noqa: BLE001
        print(f"Brevo : erreur d'envoi — {exc}")
    return False


@app.post("/newsletter/subscribe")
def newsletter_subscribe():
    """Inscription Club Privé : email validé, dédoublonné, persisté.

    La copie locale (data/subscribers.json) reste la source de vérité ; si une
    clé BREVO_API_KEY est configurée, le contact est aussi poussé vers Brevo
    en tâche de fond (l'inscription ne dépend jamais de Brevo).
    """
    data = request.get_json(silent=True) or {}
    email = str(data.get("email") or "").strip().lower()

    if not email or len(email) > 254 or not _EMAIL_RE.match(email):
        return jsonify(ok=False, error="Adresse email invalide — Altesse exige mieux."), 400

    subscribers = load_subscribers()
    if any(x.get("email") == email for x in subscribers["subscribers"]):
        return jsonify(
            ok=True, duplicate=True,
            message="Ce serviteur est déjà membre du Club Privé. 🐾",
        )

    entry = {
        "id": subscribers["next_id"],
        "email": email,
        "date": datetime.now().isoformat(),
        "source": str(data.get("source") or "footer")[:32],
    }
    subscribers["subscribers"].append(entry)
    subscribers["next_id"] += 1
    save_subscribers(subscribers)
    print(f"Newsletter : nouveau membre du Club Privé — {email}")
    if brevo_configure():
        threading.Thread(
            target=brevo_ajouter_contact, args=(email,), daemon=True
        ).start()
    return jsonify(
        ok=True,
        message="Bienvenue dans le Club Privé ! Altesse vous accorde sa bienveillance. 👑",
    ), 201


# ------------------------------------------- abonnés : consultation & export
@app.get("/api/admin/newsletter")
@login_required
def api_newsletter_list():
    """Liste des abonnés Club Privé (dashboard admin, session requise)."""
    subs = load_subscribers()["subscribers"]
    subs = sorted(subs, key=lambda x: str(x.get("date") or ""), reverse=True)
    return jsonify(ok=True, subscribers=subs, count=len(subs),
                   brevo=brevo_configure())


@app.get("/api/admin/newsletter/export.csv")
@login_required
def api_newsletter_export_csv():
    """Export CSV des abonnés (BOM UTF-8 : ouvrable directement dans Excel)."""
    subs = load_subscribers()["subscribers"]
    subs = sorted(subs, key=lambda x: str(x.get("date") or ""), reverse=True)
    buffer = StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["id", "email", "date_inscription", "source"])
    for s in subs:
        writer.writerow([s.get("id", ""), s.get("email", ""),
                         s.get("date", ""), s.get("source", "")])
    data = buffer.getvalue().encode("utf-8-sig")  # \ufeff pour Excel
    return send_file(
        BytesIO(data),
        mimetype="text/csv",
        as_attachment=True,
        download_name="abonnes-club-prive.csv",
    )


# ---------------------------------------------------------------- API produits
def _produit_public(p):
    """Copie d'un produit exposée aux visiteurs : champs utiles à la
    boutique uniquement (aucun nom de prestataire, identifiant logistique,
    ni marge).
    """
    champs = ("id", "name", "price", "category", "description",
              "sizes", "colors", "status", "image", "emoji", "hue",
              "is_customizable")
    return {k: p.get(k) for k in champs}

@app.get("/api/products")
def api_products():
    """Catalogue JSON. Public = version anonymisée ; admin = complet."""
    db = load_db()
    visible = [p for p in db["products"] if p["status"] != "Indisponible"]
    est_admin = (flask_session.get("is_admin")
                 and flask_session.get("epoch") == _session_epoch())
    if not est_admin:
        visible = [_produit_public(p) for p in visible]
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
    supplier_url = (d.get("url_fournisseur") or "").strip()
    if supplier_url and not supplier_url.startswith(("http://", "https://")):
        return jsonify(ok=False, error="L'URL fournisseur doit commencer par http:// ou https://"), 400

    supplier = _canonical_supplier({
        "supplier": d.get("supplier"),
        "fournisseur": d.get("fournisseur"),
    })
    prod = {
        "id": db["next_product_id"],
        "name": name,
        "price": price,
        "cost": cost,
        "fournisseur_id": (d.get("fournisseur_id") or "").strip()[:120],
        "fournisseur": (d.get("fournisseur") or "").strip()[:120],
        "supplier": supplier,
        "is_customizable": supplier in {"Gelato", "Printify"},
        "url_fournisseur": supplier_url[:1000] or None,
        "category": category,
        "description": (d.get("description") or "").strip(),
        "sizes": sizes or ["Unique"],
        "colors": colors or ["Standard"],
        "status": status,
        "image": image_path,
        "emoji": (d.get("emoji") or "🐾").strip()[:8],
        "hue": (d.get("hue") or "#FBBF24").strip()[:7],
        "gelato_product_uid": (d.get("gelato_product_uid") or "").strip()[:120],
        "gelato_print_file_url": (d.get("gelato_print_file_url") or "").strip()[:500],
        "gelato_print_type": (d.get("gelato_print_type") or "default").strip()[:16],
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
    for key, limit in (("fournisseur_id", 120), ("fournisseur", 120)):
        if key in d:
            prod[key] = str(d.get(key) or "").strip()[:limit]
    # Mappage Gelato (impression POD) : productUid obligatoire à l'envoi,
    # fichier d'impression et zone d'impression optionnels.
    for key, limit in (("gelato_product_uid", 120),
                       ("gelato_print_file_url", 500),
                       ("gelato_print_type", 16)):
        if key in d:
            prod[key] = str(d.get(key) or "").strip()[:limit]
    if prod.get("gelato_print_type") not in ("default", "front", "back"):
        prod["gelato_print_type"] = "default"
    if "supplier" in d:
        prod["supplier"] = str(d.get("supplier") or "").strip()[:120]
    elif "fournisseur" in d:
        prod["supplier"] = str(d.get("fournisseur") or "").strip()[:120]
    _apply_customization_policy(prod)
    if "url_fournisseur" in d:
        supplier_url = str(d.get("url_fournisseur") or "").strip()
        if supplier_url and not supplier_url.startswith(("http://", "https://")):
            return jsonify(ok=False, error="L'URL fournisseur doit commencer par http:// ou https://"), 400
        prod["url_fournisseur"] = supplier_url[:1000] or None
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
            "cj_pid": str(p.get("cj_product_id") or ""),
            "cj_vid": str(p.get("cj_variant_id") or p.get("cj_vid") or ""),
            "size": str(it.get("size", "Unique"))[:32],
            "color": str(it.get("color", "Standard"))[:32],
            "qty": qty, "price": p["price"],
            "custom_image_url": (
                str(it.get("custom_image_url") or "")[:240]
                if p.get("is_customizable") else ""
            ),
            "pet_name": (
                str(it.get("pet_name") or "")[:80]
                if p.get("is_customizable") else ""
            ),
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
            "phone": (str(cust.get("phone") or "").strip())[:20],
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

def _resolve_variant(db, item):
    """Trouve un vid CJ correspondant au pid + couleur/taille de la ligne.

    CJ exige vid ou sku sur chaque article d'une commande. Interroge la liste
    de variantes CJ et matche la couleur/taille choisie si possible ;
    renvoie la première variante sinon, None si le produit n'en a pas.
    """
    from cj_service import CJAPIError, get_variant_list

    pid = str(item.get("cj_pid") or "").strip()
    if not pid:
        return None
    try:
        variants = get_variant_list(pid)
    except (CJAPIError, ValueError) as exc:
        print(f"Variantes CJ indisponibles pour {pid} : {exc}")
        return None
    if not variants:
        return None

    def _norm(value):
        import unicodedata
        text = unicodedata.normalize("NFD", str(value or "").lower())
        return "".join(c for c in text if unicodedata.category(c) != "Mn").strip()

    color, size = _norm(item.get("color")), _norm(item.get("size"))
    if color and color not in ("standard", "unique"):
        matched = [v for v in variants
                   if color in _norm(v.get("variantNameEn") or v.get("variantName"))
                   or color in _norm(v.get("variantSku"))]
        if matched:
            variants = matched
    if size and size not in ("standard", "unique") and len(variants) > 1:
        matched = [v for v in variants
                   if size in _norm(v.get("variantNameEn") or v.get("variantName"))
                   or size in _norm(v.get("variantSku"))]
        if matched:
            variants = matched
    return str(variants[0].get("vid") or "") or None

def _decouper_adresse(addr):
    """« 15 rue des Lilas, 69001 Lyon » -> (rue, code postal, ville).

    Dernier bloc qui ressemble à un code postal + nom de ville ; sinon la
    rue intégrale et des villes/codes vides, que l'API rejettera avec un
    message explicite sur la commande.
    """
    addr = str(addr or "").strip()
    match = re.search(r"\b(\d{5})\s+([^,]+)$", addr)
    if match:
        return addr[:match.start()].strip().strip(","), match.group(1), match.group(2).strip()
    return addr, "", ""

def envoyer_commande_cj(db, order):
    """Transmet à CJ les lignes payées qui proviennent de CJ.

    Une erreur CJ est conservée sur la commande locale afin de permettre un
    nouvel essai sans perdre la commande déjà payée.
    """
    if order.get("cj_order_id") or order.get("cj_dispatch_status") == "sent":
        return
    cj_items = [
        item for item in order.get("items", [])
        if item.get("cj_pid") or item.get("cj_vid")
    ]
    if not cj_items:
        return

    from cj_service import CJAPIError, create_order

    customer = order.get("customer") or {}
    address = str(customer.get("address") or "").strip()
    # CJ exige city/ZIP séparés ; une adresse sans ville suceptible de
    # produire le blocage "shippingCity must be not empty" du dashboard CJ.
    _street, shipping_zip, shipping_city = _decouper_adresse(address)
    payload = {
        "orderNumber": order["ref"],
        "shippingCustomerName": str(customer.get("name") or "Client").strip()[:50],
        "shippingPhone": str(customer.get("phone") or "").strip()[:20],
        "shippingAddress": _street[:500] if _street else address[:500],
        "email": str(customer.get("email") or "").strip()[:120],
        "shippingCountryCode": "FR",
        "shippingCountry": "France",
        "logisticName": os.getenv("CJ_LOGISTIC_NAME", "CJPacket"),
        "fromCountryCode": os.getenv("CJ_FROM_COUNTRY_CODE", "CN"),
        # CJ n'accepte plus "custom" (erreur 5027) : "api" = commande API
        # classique, visible dans Orders > Imported Orders du dashboard CJ.
        "platform": os.getenv("CJ_PLATFORM", "api"),
        "products": [],
    }
    if shipping_city:
        payload["shippingCity"] = shipping_city[:100]
    if shipping_zip:
        payload["shippingZip"] = shipping_zip[:20]
    for item in cj_items:
        product = {"quantity": int(item.get("qty", 1))}
        if item.get("cj_vid"):
            product["vid"] = item["cj_vid"]
        else:
            # CJ refuse une ligne sans vid ni sku ("vid and sku cannot both
            # be empty") : on résout la variante côté CJ à partir du pid,
            # en matchant la couleur/taille choisie par le client si possible.
            vid = _resolve_variant(db, item)
            if vid:
                product["vid"] = vid
                item["cj_vid"] = vid  # mémo pour les commandes suivantes
            else:
                product["pid"] = item["cj_pid"]
        payload["products"].append(product)

    try:
        result = create_order(payload) or {}
    except (CJAPIError, ValueError) as exc:
        order["cj_dispatch_status"] = "error"
        order["cj_error"] = str(exc)
        save_db(db)
        print(f"Commande {order['ref']} payée, envoi CJ échoué : {exc}")
        return

    if isinstance(result, dict):
        cj_order_id = (
            result.get("orderId") or result.get("orderNum") or
            result.get("orderNumber") or result.get("cjOrderId") or
            result.get("id")
        )
    else:
        cj_order_id = result
    if not cj_order_id:
        order["cj_dispatch_status"] = "error"
        order["cj_error"] = "CJ n'a pas renvoyé de numéro de commande."
        save_db(db)
        return
    order["cj_order_id"] = str(cj_order_id)
    order["cj_dispatch_status"] = "sent"
    order.pop("cj_error", None)
    save_db(db)


# ---------------------------------------------------------------- Gelato (impression à la demande)

def _url_absolue(path):
    """Convertit /static/uploads/… en URL publique que Gelato peut télécharger."""
    path = str(path or "").strip()
    if not path:
        return ""
    if path.startswith(("http://", "https://")):
        return path
    try:
        base = request.host_url.rstrip("/")  # hôte de la requête en cours
    except RuntimeError:  # hors contexte HTTP (script, webhook interne)
        base = str(os.getenv("SITE_URL", "") or "").strip().rstrip("/")
    return base + path if base else path


def _destinataire_gelato(customer):
    """shippingAddress attendu par l'API Gelato v4 (nom scindé prénom / nom)."""
    name = str(customer.get("name") or "Serviteur anonyme").strip()[:80]
    parties = name.split()
    prenom = parties[0] if parties else "Serviteur"
    nom = " ".join(parties[1:]) or prenom
    rue, code_postal, ville = _decouper_adresse(customer.get("address"))
    if not code_postal or not ville:
        raise ValueError(
            "Adresse de livraison incomplète (code postal / ville introuvables) : "
            + str(customer.get("address") or "(vide)")[:120]
        )
    return {
        "firstName": prenom[:50],
        "lastName": nom[:50],
        "addressLine1": rue[:500],
        "city": ville[:100],
        "postCode": code_postal[:20],
        "country": "FR",
        "email": str(customer.get("email") or "").strip()[:120],
        "phone": str(customer.get("phone") or "").strip()[:20],
    }


def envoyer_commande_gelato(db, order):
    """Transmet à Gelato les lignes POD payées (impression à la demande).

    Appelée dès la validation du paiement Stripe (voir _confirmer_via_stripe),
    aux côtés de envoyer_commande_cj. Même contrat : l'état est conservé sur la
    commande locale (gelato_dispatch_status / gelato_order_id / gelato_error)
    pour permettre un nouvel essai sans perdre la commande déjà payée. Sans
    GELATO_API_KEY, rien n'est envoyé — l'envoi reste tentable plus tard.
    """
    import gelato_service

    if (order.get("gelato_order_id")
            or order.get("gelato_dispatch_status") in ("sent", "not_applicable")):
        return
    if not gelato_service.configure():
        return

    # Lignes POD : article personnalisé ou fournisseur Gelato / Printify.
    pod_items = []
    for item in order.get("items", []):
        produit = next(
            (x for x in db["products"] if x["id"] == item.get("product_id")), None
        )
        if not produit:
            continue
        if (produit.get("is_customizable")
                or _canonical_supplier(produit) in ("Gelato", "Printify")):
            pod_items.append((item, produit))
    if not pod_items:
        order["gelato_dispatch_status"] = "not_applicable"
        save_db(db)
        return

    sans_uid = sorted({
        str(p.get("name") or "").strip()
        for _, p in pod_items if not str(p.get("gelato_product_uid") or "").strip()
    })
    if sans_uid:
        order["gelato_dispatch_status"] = "error"
        order["gelato_error"] = (
            "Aucun productUid Gelato pour : " + ", ".join(sans_uid) +
            " — renseignez gelato_product_uid (Admin > Produits)."
        )
        save_db(db)
        print(f"Commande {order['ref']} payée, envoi Gelato bloqué : {order['gelato_error']}")
        return

    lignes, sans_fichier = [], []
    for index, (item, produit) in enumerate(pod_items, start=1):
        type_zone = str(produit.get("gelato_print_type") or "default").strip()
        if type_zone not in gelato_service.PRINT_AREAS:
            type_zone = "default"
        fichier = _url_absolue(item.get("custom_image_url"))  # photo du client
        if not fichier:
            fichier = str(produit.get("gelato_print_file_url") or "").strip()
        if not fichier:
            sans_fichier.append(str(produit.get("name") or "").strip())
            continue
        try:
            qty = max(1, min(99, int(item.get("qty") or 1)))
        except (TypeError, ValueError):
            qty = 1
        lignes.append({
            "itemReferenceId": f"{order['ref']}-{index}",
            "productUid": str(produit.get("gelato_product_uid") or "").strip(),
            "files": [{"type": type_zone, "url": fichier}],
            "quantity": qty,
        })
    if not lignes:
        order["gelato_dispatch_status"] = "error"
        order["gelato_error"] = (
            "Aucun fichier d'impression pour : "
            + ", ".join(sorted(set(sans_fichier)))
            + " (photo client ou gelato_print_file_url manquant)."
        )
        save_db(db)
        print(f"Commande {order['ref']} payée, envoi Gelato bloqué : {order['gelato_error']}")
        return

    customer = order.get("customer") or {}
    try:
        shipping_address = _destinataire_gelato(customer)
    except ValueError as exc:
        order["gelato_dispatch_status"] = "error"
        order["gelato_error"] = str(exc)
        save_db(db)
        print(f"Commande {order['ref']} payée, envoi Gelato bloqué : {exc}")
        return

    payload = {
        "orderType": "order",
        "orderReferenceId": str(order["ref"]),
        "customerReferenceId": str(customer.get("email") or order["ref"])[:120],
        "currency": "EUR",
        "items": lignes,
        "shippingAddress": shipping_address,
    }
    mode_livraison = str(os.getenv("GELATO_SHIPMENT_METHOD", "") or "").strip()
    if mode_livraison:
        payload["shipmentMethodUid"] = mode_livraison

    try:
        result = gelato_service.create_order(payload) or {}
    except (gelato_service.GelatoAPIError, ValueError) as exc:
        order["gelato_dispatch_status"] = "error"
        order["gelato_error"] = str(exc)
        save_db(db)
        print(f"Commande {order['ref']} payée, envoi Gelato échoué : {exc}")
        return

    gelato_id = result.get("id") if isinstance(result, dict) else result
    if not gelato_id:
        order["gelato_dispatch_status"] = "error"
        order["gelato_error"] = "Gelato n'a pas renvoyé d'identifiant de commande."
        save_db(db)
        return
    order["gelato_order_id"] = str(gelato_id)
    order["gelato_dispatch_status"] = "sent"
    order.pop("gelato_error", None)
    save_db(db)
    print(f"Commande {order['ref']} transmise à Gelato (commande {gelato_id}).")

# ---------------------------------------------------------------- suivi de commande
# Statuts CJ (orderStatus texte) → libellé client.
CJ_STATUS_FR = {
    "CREATED": "Commande enregistrée",
    "IN_CART": "En attente de validation",
    "UNPAID": "En attente de règlement",
    "PENDING": "En préparation",
    "PROCESSING": "En préparation",
    "UNSHIPPED": "En préparation — pas encore expédiée",
    "SHIPPED": "Expédiée 🚚",
    "DELIVERED": "Livrée 📦",
    "CANCELLED": "Annulée",
}


def synchroniser_suivi_cj(db, order):
    """Met à jour le suivi d'une commande depuis l'API CJ (best-effort).

    Mémorise transporteur / n° de suivi / URL, passe la commande en
    « Expédiée » dès qu'un numéro de suivi apparaît, et envoie l'email
    « colis expédié » une seule fois. Renvoie la commande.
    """
    cj_order_id = str(order.get("cj_order_id") or "").strip()
    if not cj_order_id:
        return order

    from cj_service import CJAPIError, get_order_detail

    try:
        detail = get_order_detail(cj_order_id)
    except (CJAPIError, ValueError) as exc:
        print(f"Suivi CJ indisponible pour {order['ref']} : {exc}")
        return order

    statut = str(detail.get("orderStatus") or "").strip()
    transporteur = str(
        detail.get("logisticName") or detail.get("trackingProvider") or ""
    ).strip()
    numero_suivi = str(
        detail.get("trackNumber") or detail.get("trackingNumber") or ""
    ).strip()
    url_suivi = str(detail.get("trackingUrl") or "").strip()

    if statut:
        order["cj_status"] = statut
    if transporteur:
        order["tracking_provider"] = transporteur
    if url_suivi:
        order["tracking_url"] = url_suivi

    nouveau_suivi = bool(numero_suivi) and order.get("tracking_number") != numero_suivi
    if numero_suivi:
        order["tracking_number"] = numero_suivi

    passe_expedia = (
        (statut in ("SHIPPED", "DELIVERED") or nouveau_suivi)
        and order.get("status") not in ("Expédiée", "Livrée")
    )
    if passe_expedia:
        order["status"] = "Expédiée"
    if statut == "DELIVERED":
        order["status"] = "Livrée"

    save_db(db)

    # Email « expédié » : une seule fois, dès qu'un numéro de suivi existe.
    if nouveau_suivi and not order.get("suivi_email_sent"):
        customer = order.get("customer") or {}
        ok_mail = envoyer_email_suivi(
            customer.get("email"),
            customer.get("name"),
            order["ref"],
            transporteur,
            numero_suivi,
            url_suivi,
        )
        if ok_mail:
            order["suivi_email_sent"] = True
            save_db(db)
    return order


def _commande_par_ref_email(db, ref, email):
    """Trouve une commande par référence + email client (comparaison souple)."""
    ref = str(ref or "").strip().upper()
    email = str(email or "").strip().lower()
    if not ref or not email:
        return None
    for o in db.get("orders", []):
        if str(o.get("ref") or "").upper() != ref:
            continue
        if str((o.get("customer") or {}).get("email") or "").strip().lower() != email:
            continue
        return o
    return None


@app.route("/suivi")
@app.get("/suivi")
def suivi_commande():
    """Page client « Où est ma commande ? » : référence + email → suivi CJ."""
    db = load_db()
    ref = (request.args.get("ref") or "").strip()
    email = (request.args.get("email") or "").strip()
    commande = erreur = None
    transporteur = ""

    if ref or email:
        if not ref or not email:
            erreur = "Indiquez votre référence de commande ET l'email utilisé."
        else:
            commande = _commande_par_ref_email(db, ref, email)
            if not commande:
                erreur = (
                    "Aucune commande ne correspond à cette référence et cet email. "
                    "Vérifiez votre boîte mail : la référence ressemble à SMP-1042."
                )
            else:
                # Interrogation CJ à la volée (limitée à 1 req/s côté CJ,
                # un seul appel par visite suffit).
                commande = synchroniser_suivi_cj(db, commande)
                transporteur = _transporteur_client(
                    commande.get("tracking_provider"))

    return render_template(
        "suivi.html",
        commande=commande,
        transporteur=transporteur,
        erreur=erreur,
        ref_saisie=ref,
        email_saisi=email,
        statut_fr=CJ_STATUS_FR,
        settings=db["settings"],
        announcement=db["settings"]["announcement"],
    )

@app.post("/api/orders")
def api_create_order():
    """Commande directe sans Stripe."""
    db = load_db()
    d = request.get_json(silent=True) or {}
    if not d.get("cgv_accepted"):
        return jsonify(ok=False,
                       error="L'acceptation des CGV est requise pour commander."), 400
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
    if not d.get("cgv_accepted"):
        return jsonify(ok=False,
                       error="L'acceptation des CGV est requise pour commander."), 400
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
                        "phone": str(cust.get("phone") or "").strip(),
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
    envoyer_commande_cj(db, order)
    envoyer_commande_gelato(db, order)
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

def _demarrer_scheduler_cj():
    """Synchronisation quotidienne des stocks/prix CJ (daemon, idempotent).

    Intervalles configurables via CJ_SYNC_INTERVAL_HOURS ; toute erreur est
    absorbée pour que l'application démarre quoi qu'il arrive.
    """
    try:
        import cj_sync
        cj_sync.start_scheduler()
    except Exception as _sync_exc:  # noqa: BLE001 — l'app doit démarrer quoi qu'il arrive
        print(f"Scheduler CJ non démarré : {_sync_exc}")


# Sous gunicorn (Render), `__main__` n'est jamais exécuté : on démarre le
# scheduler à l'importation du module. Pour un lancement direct
# (`python app.py`), c'est le bloc `__main__` ci-dessous qui s'en charge,
# afin de ne pas lancer deux threads dans le parent du reloader Flask.
if __name__ != "__main__":
    _demarrer_scheduler_cj()


if __name__ == "__main__":
    # Synchronisation CJ : le reloader debug de Flask duplique le processus,
    # on n'ordonnance que dans le processus qui sert réellement les requêtes.
    if os.environ.get("WERKZEUG_RUN_MAIN") == "true" or not app.debug:
        _demarrer_scheduler_cj()
    # Port : PORT si fourni (Render), sinon 5000 en local.
    _port = int(os.environ.get("PORT") or 5000)
    _debug = os.getenv("FLASK_DEBUG", "true").strip().lower() not in ("0", "false", "no", "off")
    app.run(host="0.0.0.0", debug=_debug, port=_port)
