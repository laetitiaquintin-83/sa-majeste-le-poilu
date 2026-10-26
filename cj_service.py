"""Client minimal pour l'API CJ Dropshipping v2.0.

Les secrets sont lus depuis l'environnement :
    CJ_EMAIL    Adresse de compte CJ (configuration/documentation)
    CJ_API_KEY  Clé API CJ

Le module ne journalise jamais la clé API ni les tokens.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
import unicodedata
from typing import Any

import requests


# Session partagée : réutilisation des connexions TLS (important pour les
# appels en série de la synchronisation, -2/3 du temps par requête).
_session = requests.Session()

CJ_API_BASE_URL = "https://developers.cjdropshipping.com/api2.0/v1"
REQUEST_TIMEOUT = (5, 30)

# CJ limite l'API à 1 requête/seconde : cadence UNIQUE globale, appliquée
# à chaque appel (_get/_post). Synchro de fond, imports et commandes
# partagent ce même garde-fou — plus de « Too Many Requests ».
_QPS_LOCK = threading.Lock()
_QPS_LAST = 0.0
_QPS_INTERVAL = float(os.getenv("CJ_QPS_INTERVAL", "1.05"))


def _respect_qps() -> None:
    """Espace les appels CJ d'au moins CJ_QPS_INTERVAL secondes."""
    global _QPS_LAST
    with _QPS_LOCK:
        wait = _QPS_LAST + _QPS_INTERVAL - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        _QPS_LAST = time.monotonic()


TR_TIMEOUT = (5, 15)


class CJAPIError(RuntimeError):
    """Erreur explicite renvoyée par l'API ou par le client CJ."""

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


_token_lock = threading.Lock()
_cached_token: str | None = None
_token_expires_at = 0.0

_FRENCH_TO_ENGLISH = {
    "fournitures animaux": "pet supplies",
    "accessoires animaux": "pet accessories",
    "animal de compagnie": "pet",
    "animaux de compagnie": "pets",
    "nourriture pour chat": "cat food",
    "nourriture pour chien": "dog food",
    "jouet pour chat": "cat toy",
    "jouet pour chien": "dog toy",
    "chat": "cat",
    "chats": "cats",
    "chien": "dog",
    "chiens": "dogs",
    "chiot": "puppy",
    "chiots": "puppies",
    "chaton": "kitten",
    "chatons": "kittens",
    "oiseau": "bird",
    "oiseaux": "birds",
    "lapin": "rabbit",
    "lapins": "rabbits",
    "collier": "collar",
    "laisse": "leash",
    "harnais": "harness",
    "gamelle": "pet bowl",
    "jouet": "pet toy",
    "toilettage": "pet grooming",
}

_ANIMAL_TITLE_TERMS = {
    "animal", "animals", "pet", "pets", "cat", "cats", "kitten", "kittens",
    "dog", "dogs", "puppy", "puppies", "bird", "birds", "rabbit", "rabbits",
    "fish", "hamster", "collar", "leash", "harness", "bowl", "toy", "toys",
    "grooming", "petwear", "paws", "paw", "canine", "feline", "puppy",
    "chat", "chats", "chaton", "chatons", "chien", "chiens", "chiot", "chiots",
    "oiseau", "oiseaux", "lapin", "lapins", "poisson", "poissons", "collier",
    "laisse", "harnais", "gamelle", "jouet", "jouets", "toilettage", "pattes",
}

_TITLE_NOISE_RE = re.compile(
    r"\b(drop\s*shipping|dropshipping|hot\s*sale|hot\s*selling|"
    r"best\s*seller|vente\s+chaude|vente\s+en\s+gros|gros\s+volume|"
    r"livraison\s+directe)\b",
    re.IGNORECASE,
)

# Identifiant CJ : GUID (B901800C-…), pid numérique, ou URL de produit CJ
# qui le contient (…/product/B901800C-2B7B-…-, ou ?pid=B901800C-…).
_GUID_RE = re.compile(
    r"[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}"
)
_PRODUCT_URL_RE = re.compile(
    r"[?&](?:pid|productId|product_id)=([0-9A-Fa-f-]{6,})",
    re.IGNORECASE,
)
_CJ_HOST_RE = re.compile(r"cjdropshipping(?:\.com|\.[a-z]{2,3})", re.IGNORECASE)
_ROUTED_ID_RE = re.compile(r"^[0-9A-C-]{6,64}$", re.IGNORECASE)  # GUID ou pid numérique long


def extract_cj_id(input_text) -> str:
    """Extrait un identifiant produit CJ d'une saisie libre.

    Accepte un GUID (B901800C-2B7B-487C-97FE-CC4714CDA630), un pid numérique,
    une URL de produit CJ (le paramètre pid est prioritaire, sinon le premier
    segment admissible du chemin). Renvoie "" si rien ne ressemble à un ID.
    """
    text = str(input_text or "").strip()
    if not text:
        return ""

    # 1) URL : pid=… explicite en query-string
    if text.lower().startswith(("http://", "https://")) or _CJ_HOST_RE.search(text):
        found = _PRODUCT_URL_RE.search(text)
        if found:
            return found.group(1).strip("-")
        # 2) URL sans query : segment de chemin admissible (GUID en général,
        #    éventuellement suffixé .html) ou pid numérique court
        for segment in text.split("?")[0].split("/"):
            segment = re.sub(r"\.(html?|aspx?|php)$", "", segment, flags=re.IGNORECASE)
            segment = segment.strip("-")
            if _GUID_RE.fullmatch(segment):
                return segment
            if segment.isdigit() and 6 <= len(segment) <= 20:
                return segment
        return ""

    # 3) Saisie directe : GUID exact, pid numérique long, ou ID inclus dans du texte
    if _GUID_RE.fullmatch(text) or (text.isdigit() and 6 <= len(text) <= 20):
        return text
    guid = _GUID_RE.search(text)
    if guid:
        return guid.group(0)
    return ""
_TITLE_TRANSLATIONS = {
    "cute": "Joli",
    "beautiful": "Magnifique",
    "new": "Nouveau",
    "portable": "Portable",
    "cat": "Chat",
    "cats": "Chats",
    "kitten": "Chaton",
    "kittens": "Chatons",
    "dog": "Chien",
    "dogs": "Chiens",
    "puppy": "Chiot",
    "puppies": "Chiots",
    "bird": "Oiseau",
    "birds": "Oiseaux",
    "rabbit": "Lapin",
    "rabbits": "Lapins",
    "collar": "Collier",
    "leash": "Laisse",
    "harness": "Harnais",
    "pet": "Animal",
    "pets": "Animaux",
    "toy": "Jouet",
    "toys": "Jouets",
    "bowl": "Gamelle",
    "food": "Nourriture",
}


def _normalize_text(value: str) -> str:
    normalized = unicodedata.normalize("NFD", value.lower())
    return "".join(char for char in normalized if unicodedata.category(char) != "Mn")


def translate_search_keyword(keyword: str) -> str:
    """Traduit les expressions françaises connues avant l'appel CJ."""
    translated = _normalize_text(keyword.strip())
    for french, english in sorted(
        _FRENCH_TO_ENGLISH.items(), key=lambda item: len(item[0]), reverse=True
    ):
        translated = re.sub(
            rf"\b{re.escape(_normalize_text(french))}\b",
            english,
            translated,
        )
    return translated


def clean_product_title(title: Any) -> str:
    """Retire les suffixes marketing fréquemment renvoyés par CJ."""
    cleaned = _TITLE_NOISE_RE.sub(" ", str(title or ""))
    cleaned = re.sub(r"\s*[-|/,:;]+\s*", " ", cleaned)
    return re.sub(r"\s{2,}", " ", cleaned).strip(" -|/,:;")


def translate_product_title(title: Any) -> str:
    """Nettoie et traduit les termes anglais courants d'un titre CJ."""
    cleaned = clean_product_title(title)
    for english, french in sorted(_TITLE_TRANSLATIONS.items(), key=lambda item: len(item[0]), reverse=True):
        cleaned = re.sub(rf"\b{re.escape(english)}\b", french, cleaned, flags=re.IGNORECASE)
    return re.sub(r"\s{2,}", " ", cleaned).strip()


# ------------------------------------------------------------------
# Traduction automatique EN -> FR (MyMemory : API publique gratuite,
# sans clé ni inscription). Chaque traduction est mise en mémoire et les
# appels sont espacés (quota anonyme ~5 000 mots/jour). En cas d'erreur
# réseau ou de quota épuisé, on renvoie None : l'appelant garde alors le
# texte nettoyé par le dictionnaire ci-dessus (translate_product_title).
_TR_URL = "https://api.mymemory.translated.net/get"
_TR_SESSION = requests.Session()
_TR_MIN_DELAY = 0.3  # politesse : espace entre deux requêtes
_TR_LOCK = threading.Lock()
_TR_CACHE: dict[str, str] = {}  # clé normalisée -> trad. ("" indisponible)
_TR_LAST = 0.0


def _chunk_text(text: str, *, max_len: int = 480) -> list[str]:
    """Découpe un long texte en morceaux < max_len, aux frontières de phrase."""
    words = text.split()
    chunks, current = [], ""
    for word in words:
        candidate = f"{current} {word}".strip()
        while len(candidate) > max_len:
            cut = max(1, max_len)
            chunks.append(candidate[:cut].rsplit(" ", 1)[0])
            candidate = candidate[len(chunks[-1]):].strip()
        current = candidate
    if current:
        chunks.append(current)
    return chunks or ([text] if text else [])


def translate_to_french(text: Any) -> str | None:
    """Traduit un texte anglais en français. None si indisponible."""
    global _TR_LAST
    source = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(source) < 2:
        return None
    key = source.lower()
    with _TR_LOCK:
        cached = _TR_CACHE.get(key)
        if cached is not None:
            return cached or None
        translated_parts: list[str] = []
        try:
            for chunk in _chunk_text(source)[:40]:
                wait = _TR_MIN_DELAY - (time.monotonic() - _TR_LAST)
                if wait > 0:
                    time.sleep(wait)
                _TR_LAST = time.monotonic()
                response = _TR_SESSION.get(
                    _TR_URL, params={"q": chunk, "langpair": "en|fr"},
                    timeout=TR_TIMEOUT,
                )
                response.raise_for_status()
                payload = response.json()
                piece = str(((payload.get("responseData") or {}).get("translatedText") or "")).strip()
                if not piece:
                    break
                translated_parts.append(piece)
        except (requests.RequestException, ValueError):
            _TR_CACHE[key] = ""  # indisponible pour le moment : pas de re-call immédiat
            return None
        translated = " ".join(translated_parts).strip()
        if not translated:
            _TR_CACHE[key] = ""
            return None
        _TR_CACHE[key] = translated
        return translated


def _product_name(product: dict[str, Any]) -> str:
    return str(
        product.get("productName")
        or product.get("productNameEn")
        or product.get("nameEn")
        or product.get("name")
        or ""
    )


def _is_animal_product(product: dict[str, Any]) -> bool:
    title = _normalize_text(_product_name(product))
    return any(
        re.search(rf"\b{re.escape(term)}\b", title)
        for term in _ANIMAL_TITLE_TERMS
    )


def _filter_and_clean_products(data: Any) -> Any:
    """Nettoie les titres et retire les produits sans indice animal."""
    if not isinstance(data, dict) or not isinstance(data.get("content"), list):
        return data

    filtered_groups = []
    filtered_count = 0
    for group in data["content"]:
        if not isinstance(group, dict):
            continue
        products = group.get("productList", [])
        if not isinstance(products, list):
            continue
        filtered_products = []
        for product in products:
            if not isinstance(product, dict) or not _is_animal_product(product):
                continue
            display_name = translate_product_title(_product_name(product))
            product["displayName"] = display_name
            for field in ("productName", "productNameEn", "nameEn"):
                if product.get(field):
                    product[field] = display_name
            filtered_products.append(product)
        group["productList"] = filtered_products
        if filtered_products:
            filtered_count += len(filtered_products)
            filtered_groups.append(group)
    data["content"] = filtered_groups
    if "totalRecords" in data:
        data["totalRecords"] = filtered_count
    if "totalPages" in data:
        data["totalPages"] = 1 if filtered_count else 0
    return data


def _api_key() -> str:
    api_key = os.getenv("CJ_API_KEY", "").strip()
    if not api_key:
        raise CJAPIError("La variable d'environnement CJ_API_KEY est manquante.")
    return api_key


def _decode_response(response: requests.Response) -> dict[str, Any]:
    try:
        payload = response.json()
    except ValueError as exc:
        raise CJAPIError(
            "CJ a renvoyé une réponse JSON invalide.",
            response.status_code,
        ) from exc

    if not isinstance(payload, dict):
        raise CJAPIError("CJ a renvoyé un format de réponse inattendu.", response.status_code)
    if not response.ok:
        message = str(payload.get("message") or "La requête CJ a échoué.")
        raise CJAPIError(message, response.status_code)
    if payload.get("result") is False:
        raise CJAPIError(
            str(payload.get("message") or "CJ a refusé la requête."),
            response.status_code,
        )
    return payload


def _post(path: str, payload: dict[str, Any], token: str | None = None) -> dict[str, Any]:
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    if token:
        headers["CJ-Access-Token"] = token
    _respect_qps()
    try:
        response = _session.post(
            f"{CJ_API_BASE_URL}{path}",
            json=payload,
            headers=headers,
            timeout=REQUEST_TIMEOUT,
        )
    except requests.RequestException as exc:
        raise CJAPIError("CJ est momentanément inaccessible.") from exc
    return _decode_response(response)


def _get(path: str, params: dict[str, Any], token: str) -> dict[str, Any]:
    headers = {"Accept": "application/json", "CJ-Access-Token": token}
    _respect_qps()
    try:
        response = _session.get(
            f"{CJ_API_BASE_URL}{path}",
            params=params,
            headers=headers,
            timeout=REQUEST_TIMEOUT,
        )
    except requests.RequestException as exc:
        raise CJAPIError("CJ est momentanément inaccessible.") from exc
    return _decode_response(response)


def get_access_token(*, force_refresh: bool = False) -> str:
    """Récupère un token CJ et le réutilise pendant sa durée de vie."""
    global _cached_token, _token_expires_at
    now = time.monotonic()
    if not force_refresh and _cached_token and now < _token_expires_at:
        return _cached_token

    with _token_lock:
        now = time.monotonic()
        if not force_refresh and _cached_token and now < _token_expires_at:
            return _cached_token

        payload = _post(
            "/authentication/getAccessToken",
            {"apiKey": _api_key()},
        )
        data = payload.get("data")
        if not isinstance(data, dict) or not data.get("accessToken"):
            raise CJAPIError("CJ n'a pas renvoyé de token d'accès exploitable.")

        _cached_token = str(data["accessToken"])
        # Le token CJ est annoncé pour 180 jours ; marge de sécurité de 5 minutes.
        expires_in = float(data.get("expiresIn") or 180 * 24 * 60 * 60)
        _token_expires_at = time.monotonic() + max(60.0, expires_in - 300.0)
        return _cached_token


def search_products(keyword: str, *, page_num: int = 1, page_size: int = 20) -> Any:
    """Recherche des produits CJ par mot-clé.

    Si la saisie ressemble à un identifiant produit CJ (GUID, pid numérique
    ou URL de produit CJ), un appel direct à l'endpoint /product/query est
    effectué — permet d'importer n'importe quel produit depuis son URL CJ —
    et le résultat est mis en forme comme une page de recherche (pas de
    filtrage « produit animal » dans ce chemin direct : l'import est explicite).
    """
    keyword = keyword.strip()
    if not keyword:
        raise ValueError("Le mot-clé de recherche ne peut pas être vide.")

    direct_id = extract_cj_id(keyword)
    if direct_id:
        product = get_product_details(direct_id)
        display_name = translate_product_title(_product_name(product))
        # L'endpoint /product/query renvoie l'identifiant dans "pid" (pas
        # "id") : on harmonise pour que la modale d'import admin trouve
        # toujours le cj_pid, quel que soit le chemin de recherche.
        product.setdefault("id", direct_id)
        if not product.get("pid"):
            product["pid"] = direct_id
        return {
            "content": [{
                "productList": [{**product, "displayName": display_name}],
            }],
            "totalRecords": 1,
            "totalPages": 1,
            "directLookup": True,
        }

    if page_num < 1 or page_size < 1 or page_size > 100:
        raise ValueError("page_num doit être positif et page_size compris entre 1 et 100.")

    translated_keyword = translate_search_keyword(keyword)
    payload = _get(
        "/product/listV2",
        {"keyWord": translated_keyword, "page": page_num, "size": page_size},
        get_access_token(),
    )
    return _filter_and_clean_products(payload.get("data", payload))


def get_product_details(cj_pid: str) -> dict[str, Any]:
    """Récupère les détails d'un produit CJ à partir de son identifiant."""
    cj_pid = str(cj_pid or "").strip()
    if not cj_pid:
        raise ValueError("L'identifiant CJ est obligatoire.")

    payload = _get(
        "/product/query",
        {"pid": cj_pid, "features": ["enable_description"]},
        get_access_token(),
    )
    product = payload.get("data", payload)
    if isinstance(product, dict):
        return product
    raise CJAPIError("Produit CJ introuvable.", 404)


def get_variant_list(cj_pid: str) -> list[dict[str, Any]]:
    """Liste des variantes (vid, sku, nom…) d'un produit CJ."""
    cj_pid = str(cj_pid or "").strip()
    if not cj_pid:
        raise ValueError("L'identifiant CJ est obligatoire.")
    payload = _get(
        "/product/variant/query",
        {"pid": cj_pid},
        get_access_token(),
    )
    data = payload.get("data", payload)
    if isinstance(data, dict):
        data = data.get("list") or data.get("data") or data.get("variantList")
    return data if isinstance(data, list) else []


def get_variant_stock(vid: str) -> list[dict[str, Any]]:
    """Stock réel d'une variante CJ (rows par entrepôt, totalInventoryNum)."""
    vid = str(vid or "").strip()
    if not vid:
        raise ValueError("Le vid de la variante est obligatoire.")
    payload = _get(
        "/product/stock/queryByVid",
        {"vid": vid},
        get_access_token(),
    )
    data = payload.get("data", payload)
    return data if isinstance(data, list) else []


def create_order(order_details: dict[str, Any]) -> Any:
    """Crée une commande CJ avec le payload v2 ``createOrderV2``."""
    if not isinstance(order_details, dict) or not order_details:
        raise ValueError("order_details doit être un dictionnaire non vide.")
    return _post(
        "/shopping/order/createOrderV2",
        order_details,
        get_access_token(),
    ).get("data")


def get_order_detail(order_id: str) -> dict[str, Any]:
    """Détail d'une commande CJ (statut, transporteur, numéro de suivi).

    ``orderId`` accepte l'identifiant CJ renvoyé à la création (orderId /
    orderNum) — CJ le résout des deux côtés. Les champs de suivi sont
    ``logisticName``, ``trackNumber``, ``trackingProvider`` et
    ``trackingUrl`` ; ``orderStatus`` est un statut texte (UNSHIPPED,
    SHIPPED, DELIVERED…).
    """
    order_id = str(order_id or "").strip()
    if not order_id:
        raise ValueError("L'identifiant de commande CJ est obligatoire.")
    payload = _get(
        "/shopping/order/getOrderDetail",
        {"orderId": order_id},
        get_access_token(),
    )
    data = payload.get("data", payload)
    return data if isinstance(data, dict) else {}


# ------------------------------------------------------------------
# Enrichissement « import » : galerie, guide des tailles, spécifications.


def _collect_urls(value: Any, out: list[str]) -> None:
    """Ajoute de façon récursive les URLs http(s) trouvées dans `value`.

    CJ sérialise parfois `productImage` en chaîne JSON d'une liste, d'où le
    décodage JSON défensif en plus de la découpe par virgules.
    """
    if isinstance(value, (list, tuple)):
        for item in value:
            _collect_urls(item, out)
        return
    if isinstance(value, dict):
        for key in ("url", "imageUrl", "image", "imageSet"):
            if key in value:
                _collect_urls(value.get(key), out)
        return
    text = str(value or "").strip()
    if not text:
        return
    if text.startswith("["):
        try:
            parsed = json.loads(text)
        except ValueError:
            parsed = None
        if parsed is not None:
            _collect_urls(parsed, out)
            return
    for part in text.split(","):
        part = part.strip().strip('"')
        if part.startswith(("http://", "https://")):
            out.append(part)


def extract_image_urls(product: dict[str, Any]) -> list[str]:
    """Galerie complète d'un produit CJ (productImageSet + productImage).

    Dérape en mettant en premier la principale `bigImage`, sans doublons.
    """
    ordered: list[str] = []
    _collect_urls(product.get("bigImage"), ordered)
    for field in ("productImageSet", "productImage"):
        _collect_urls(product.get(field), ordered)
    seen: set[str] = set()
    urls = []
    for url in ordered:
        norm = url.split("?")[0]
        if norm not in seen:
            seen.add(norm)
            urls.append(url)
    return urls


def extract_size_guide_url(product: dict[str, Any]) -> str | None:
    """URL éventuelle du guide des tailles / charte de mesure CJ."""
    for key, value in product.items():
        if "sizechart" not in key.lower().replace("_", ""):
            continue
        urls: list[str] = []
        _collect_urls(value, urls)
        if urls:
            return urls[0]
    return None


_SPECS_FR = {
    "material": "Matière",
    "packing": "Emballage",
    "weight": "Poids",
    "category": "Catégorie CJ",
    "variant_dimensions": "Dimensions type (L×l×H)",
    "video": "Présentation vidéo",
}


def _spec_text(value: Any) -> str:
    """Aplatit un champ CJ qui arrive en liste ou en chaîne JSON ("[\"Metal\"]")."""
    if isinstance(value, (list, tuple)):
        return ", ".join(str(x) for x in value if str(x).strip())
    text = str(value or "").strip()
    if text.startswith("["):
        try:
            return _spec_text(json.loads(text))
        except (ValueError, TypeError):
            return text.strip('"[] ')
    return text


def extract_specs(product: dict[str, Any]) -> list[dict[str, str]]:
    """Spécifications techniques structurées que CJ fournit sur le produit."""
    specs: list[dict[str, str]] = []

    def _add(spec_key: str, label_en: str, value: str) -> None:
        value = re.sub(r"\s+", " ", str(value or "")).strip()
        if not value:
            return
        specs.append({
            "label": _SPECS_FR.get(label_en, str(label_en)),
            "value": value,
        })

    material = _spec_text(product.get("materialNameEn") or
                          product.get("materialName"))
    if material.lower() not in ("", "other", "autre", "none"):
        _add("material", "material", material)
    packing = _spec_text(product.get("packingNameEn") or
                         product.get("packingName"))
    if packing.lower() not in ("", "no", "other", "none"):
        _add("packing", "packing", packing)
    try:
        weight = float(product.get("productWeight") or 0)
    except (TypeError, ValueError):
        weight = 0.0
    if weight > 0:
        _add("weight", "weight", f"{weight:g} g")
    category = str(product.get("categoryName") or "").strip()
    if category and category.lower() not in ("other", "no category"):
        _add("category", "category", category)
    if str(product.get("productVideo") or "").strip().startswith("http"):
        _add("video", "video", str(product["productVideo"]).strip())

    standard = (product.get("variants") or [])
    if standard and isinstance(standard, list):
        raw = str(standard[0].get("variantStandard") or "").strip()
        # variantStandard : "long=125,width=75,height=30" (mm d'emballage)
        dims = dict(re.findall(r"(long|width|height)\s*=\s*([\d.]+)", raw))
        if dims:
            def _mm(name: str, factor: float) -> str:
                try:
                    return f"{float(dims[name]) / factor:g}"
                except (KeyError, ZeroDivisionError, ValueError):
                    return "?"
            _add(
                "variant_dimensions", "variant_dimensions",
                " × ".join((_mm("long", 10), _mm("width", 10), _mm("height", 10))) + " cm",
            )
    return specs
