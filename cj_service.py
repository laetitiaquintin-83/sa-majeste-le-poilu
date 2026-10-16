"""Client minimal pour l'API CJ Dropshipping v2.0.

Les secrets sont lus depuis l'environnement :
    CJ_EMAIL    Adresse de compte CJ (configuration/documentation)
    CJ_API_KEY  Clé API CJ

Le module ne journalise jamais la clé API ni les tokens.
"""
from __future__ import annotations

import os
import re
import threading
import time
import unicodedata
from typing import Any

import requests


CJ_API_BASE_URL = "https://developers.cjdropshipping.com/api2.0/v1"
REQUEST_TIMEOUT = (5, 30)


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
    try:
        response = requests.post(
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
    try:
        response = requests.get(
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
    """Recherche des produits CJ par mot-clé."""
    keyword = keyword.strip()
    if not keyword:
        raise ValueError("Le mot-clé de recherche ne peut pas être vide.")
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


def create_order(order_details: dict[str, Any]) -> Any:
    """Crée une commande CJ avec le payload v2 ``createOrderV2``."""
    if not isinstance(order_details, dict) or not order_details:
        raise ValueError("order_details doit être un dictionnaire non vide.")
    return _post(
        "/shopping/order/createOrderV2",
        order_details,
        get_access_token(),
    ).get("data")
