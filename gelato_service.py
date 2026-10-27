"""Client minimal pour l'API Gelato (impression à la demande / POD).

Point d'entrée unique de l'envoi des commandes vers Gelato :
    POST https://order.gelatoapis.com/v4/orders

Authentification : en-tête X-API-KEY fourni à chaque requête, clé lue
depuis l'environnement (fichier .env en local, dashboard Render en prod) :
    GELATO_API_KEY         clé API Gelato (obligatoire)
    GELATO_SHIPMENT_METHOD mode de livraison optionnel (ex. "standard")
    GELATO_TIMEOUT         timeout HTTP en secondes (défaut 20)

Le module ne journalise jamais la clé.

Note importante : Cloudflare derrière gelatoapis.com renvoie l'erreur
1010 « browser_signature_banned » aux User-Agent par défaut (python-urllib,
python-requests). Un User-Agent applicatif explicite est donc obligatoire.
"""
from __future__ import annotations

import json
import os
from typing import Any

import requests

GELATO_ORDER_URL = "https://order.gelatoapis.com/v4/orders"
GELATO_CATALOG_URL = "https://product.gelatoapis.com/v3/catalogs"

USER_AGENT = "SaMajesteLePoilu/1.0"
REQUEST_TIMEOUT = (5, float(os.getenv("GELATO_TIMEOUT", "20") or 20))

# Zones d'impression acceptées par Gelato pour files[].type.
PRINT_AREAS = {"default", "front", "back"}


class GelatoAPIError(RuntimeError):
    """Erreur explicite renvoyée par l'API ou par le client Gelato."""

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


def api_key() -> str:
    return str(os.getenv("GELATO_API_KEY", "")).strip()


def configure() -> bool:
    """True si une clé Gelato est disponible."""
    return bool(api_key())


def _headers() -> dict[str, str]:
    return {
        "X-API-KEY": api_key(),
        "Content-Type": "application/json",
        "accept": "application/json",
        "User-Agent": USER_AGENT,
    }


def format_erreur(body: Any) -> str:
    """Formate la réponse d'erreur JSON de Gelato en message lisible.

    Format observé :
    {"code": "BAD_REQUEST", "message": "There are errors in submitted data",
     "details": [{"message": "...", "reference": "items[0].productUid"}]}
    """
    data = body
    if isinstance(body, (str, bytes)):
        try:
            data = json.loads(body)
        except (ValueError, TypeError):
            return str(body)[:400]
    if not isinstance(data, dict):
        return str(data)[:400]

    details = []
    for item in data.get("details") or []:
        if not isinstance(item, dict):
            continue
        ref = str(item.get("reference") or "").strip()
        msg = str(item.get("message") or "").strip()
        if not msg:
            continue
        details.append(f"{ref} : {msg}" if ref else msg)
    head = str(data.get("message") or "").strip()
    if details:
        return f"{head} — " + " ; ".join(details) if head else " ; ".join(details)
    return head or str(data)[:400]


def _request(method: str, url: str, payload: dict | None = None) -> Any:
    if not configure():
        raise GelatoAPIError("GELATO_API_KEY absente de l'environnement.")
    try:
        response = requests.request(
            method,
            url,
            data=None if payload is None else json.dumps(payload, ensure_ascii=False),
            headers=_headers(),
            timeout=REQUEST_TIMEOUT,
        )
    except requests.RequestException as exc:  # réseau, timeout, DNS…
        raise GelatoAPIError(f"Erreur réseau Gelato : {exc}") from exc

    if response.status_code >= 400:
        raise GelatoAPIError(
            format_erreur(response.text),
            status_code=response.status_code,
        )
    if not response.text:
        return {}
    try:
        return response.json()
    except ValueError:
        return {"raw": response.text}


def create_order(payload: dict) -> dict:
    """POST /v4/orders — crée une commande d'impression chez Gelato."""
    return _request("POST", GELATO_ORDER_URL, payload) or {}


def list_catalogs() -> dict:
    """GET /v3/catalogs — lecture seule : valide la clé et liste les catalogues."""
    return _request("GET", GELATO_CATALOG_URL) or {}


def lister_produits(catalog_uid: str, limit: int = 50, offset: int = 0) -> dict:
    """POST /v3/catalogs/<uid>/products:search — cherche les productUid."""
    url = f"https://product.gelatoapis.com/v3/catalogs/{catalog_uid}/products:search"
    return _request(
        "POST", url, {"limit": max(1, min(100, int(limit))), "offset": max(0, int(offset))}
    ) or {}
