"""Synchronisation des stocks et prix CJ Dropshipping → data/boutique.json.

Application :
    python cj_sync.py            # une passe immédiate
    python cj_sync.py --loop 24  # boucle infinie, une passe toutes les 24 h

Ordonnancement intégré à l'application :
    cj_sync.start_scheduler()    # thread de fond (daemon), lancé par app.py

Conception volontairement simple et stable :
  - une SEULE passe à la fois (_sync_lock) ;
  - boucle 100 % séquentielle : aucun parallélisme, chaque appel CJ est
    espacé du précédent (PAUSE_SECONDES) pour rester poli avec l'API ;
  - le verrou du JSON (_DB_LOCK) n'est jamais tenu pendant les appels CJ
    (potentiellement plusieurs minutes) : on copie d'abord la liste des
    produits suivis, on interroge CJ, puis on réécrit le résultat en un
    seul passage court.

Pour chaque produit local possédant un cj_product_id :
  - le coût (champ `cost`) est recalé sur le plus bas prix CJ actuel
    (min des variantSellPrice de /product/variant/query) ;
  - le stock total CJ est interrogé variante par variante
    (/product/stock/queryByVid) et stocké dans cj_stock ;
  - un produit dont le stock total CJ tombe à 0 est passé « Indisponible »
    (statut précédent sauvegardé, restauré automatiquement au retour du stock).

Le rapport de la dernière passe est conservé dans data/cj_sync_state.json.
Le module ne journalise jamais la clé API ni les tokens.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
import traceback
from datetime import datetime, timedelta
from typing import Any

import cj_service
from cj_service import CJAPIError

APP_DIR = os.path.dirname(os.path.abspath(__file__))
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)

DATA_DIR = os.path.join(APP_DIR, "data")
STATE_PATH = os.path.join(DATA_DIR, "cj_sync_state.json")

PAUSE_SECONDES = 0.0  # cadence QPS (1 req/s) désormais gérée au niveau cj_service

_sync_lock = threading.Lock()  # une seule passe à la fois


# ------------------------------------------------------------------ état
def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def load_state() -> dict[str, Any]:
    try:
        with open(STATE_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, ValueError):
        return {}


def _save_state(state: dict[str, Any]) -> None:
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


# ------------------------------------------------------------------ passe
def sync_products() -> dict[str, Any]:
    """Une passe de synchronisation complète. Renvoie un rapport sérialisable."""
    with _sync_lock:
        return _sync_once()


def _sync_once() -> dict[str, Any]:
    from app import load_db, save_db

    report: dict[str, Any] = {
        "ran_at": _now(),
        "checked": 0,
        "cost_updated": 0,
        "stock_updated": 0,
        "marked_unavailable": 0,
        "restored": 0,
        "errors": [],
    }

    # Étape 1 — copie courte des produits suivis (verrou tenu 1 instant).
    snapshot = []
    for prod in load_db().get("products", []):
        pid = str(prod.get("cj_product_id") or "").strip()
        if pid:
            snapshot.append({
                "id": prod.get("id"),
                "pid": pid,
                "cost": prod.get("cost"),
                "status": prod.get("status"),
                "cj_stock": prod.get("cj_stock"),
            })

    # Étape 2 — appels CJ (aucun verrou d'application tenu ici) : le site
    # reste réactif même si cette étape dure plusieurs minutes.
    results: dict[int, dict[str, Any]] = {}
    for prod in snapshot:
        report["checked"] += 1
        prod_id, pid = prod["id"], prod["pid"]
        entry: dict[str, Any] = {"cost": None, "stock": None, "known": False}

        try:
            variants = cj_service.get_variant_list(pid)
        except CJAPIError as exc:
            report["errors"].append({"product_id": prod_id, "pid": pid, "error": str(exc)})
            results[prod_id] = entry
            continue

        prices = []
        for v in variants:
            try:
                value = float(v.get("variantSellPrice") or 0)
            except (TypeError, ValueError):
                continue
            if value > 0:
                prices.append(value)
        entry["cost"] = round(min(prices), 2) if prices else None

        total, known = 0, 0
        for v in variants:
            vid = str(v.get("vid") or "").strip()
            if not vid:
                continue
            err_text = None
            for attempt in (1, 2):  # 1 réessaie unique (CJ limite à 1 req/s)
                try:
                    rows = cj_service.get_variant_stock(vid)
                except (CJAPIError, ValueError) as exc:
                    err_text = str(exc)
                    if attempt == 1:
                        time.sleep(1.2)  #quota : laisser passer la seconde
                        continue
                else:
                    err_text = None
                    if rows:
                        total += int(rows[0].get("totalInventoryNum") or 0)
                        known += 1
                    break
            if err_text is not None:
                report["errors"].append(
                    {"product_id": prod_id, "pid": pid, "vid": vid, "error": err_text})
            time.sleep(PAUSE_SECONDES)
        if known:
            entry["stock"], entry["known"] = total, True

        results[prod_id] = entry

    # Étape 3 — écriture du résultat (verrou tenu le temps du JSON uniquement).
    db = load_db()
    for prod in db.get("products", []):
        entry = results.get(prod.get("id"))
        if not entry:
            continue
        if entry["cost"] is not None and abs(float(prod.get("cost") or 0) - entry["cost"]) > 1e-9:
            prod["cost"] = entry["cost"]
            report["cost_updated"] += 1
        if entry["known"]:
            if entry["stock"] != prod.get("cj_stock"):
                prod["cj_stock"] = entry["stock"]
                report["stock_updated"] += 1
            if entry["stock"] <= 0 and prod.get("status") != "Indisponible":
                prod["_cj_prev_status"] = prod.get("status", "En stock")
                prod["status"] = "Indisponible"
                report["marked_unavailable"] += 1
            elif (entry["stock"] > 0 and prod.get("status") == "Indisponible"
                  and prod.get("_cj_prev_status")):
                prod["status"] = prod.pop("_cj_prev_status")
                report["restored"] += 1
    save_db(db)

    _save_state({
        "last_run": report["ran_at"],
        "last_report": report,
        "next_run_hint": (
            datetime.now() +
            timedelta(hours=float(os.getenv("CJ_SYNC_INTERVAL_HOURS", "24")))
        ).isoformat(timespec="seconds"),
    })
    return report


def run_loop(interval_hours: float) -> None:
    """Boucle standalone (python cj_sync.py --loop H)."""
    while True:
        try:
            report = sync_products()
            print(f"[cj_sync] {report['ran_at']} — {report['checked']} produits"
                  f" vérifiés, {report['cost_updated']} prix,"
                  f" {report['stock_updated']} stocks mis à jour")
            if report["errors"]:
                print(f"[cj_sync] {len(report['errors'])} erreurs CJ")
        except Exception:  # noqa: BLE001 — la boucle ne doit jamais mourir
            print(f"[cj_sync] Échec de la passe :\n{traceback.format_exc()}")
        time.sleep(max(0.5, float(interval_hours)) * 3600)


_scheduler_started = False
_scheduler_lock = threading.Lock()


def start_scheduler(interval_hours: float | None = None) -> None:
    """Lance le thread de fond (idempotent, daemon).

    Passe immédiate puis rafraîchissement toutes les
    CJ_SYNC_INTERVAL_HOURS (24 par défaut).
    """
    global _scheduler_started
    if interval_hours is None:
        interval_hours = float(os.getenv("CJ_SYNC_INTERVAL_HOURS", "24"))

    with _scheduler_lock:
        if _scheduler_started:
            return
        _scheduler_started = True

    thread = threading.Thread(
        target=run_loop, args=(interval_hours,), daemon=True,
        name="cj-sync",
    )
    thread.start()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Sync stock/prix CJ")
    parser.add_argument("--loop", type=float, default=None, metavar="H",
                        help="boucle infinie : une passe toutes les H heures")
    args = parser.parse_args()
    if args.loop is not None:
        print(f"[cj_sync] boucle lancée : une passe toutes les {args.loop} h")
        run_loop(args.loop)
    else:
        report = sync_products()
        print(json.dumps(report, ensure_ascii=False, indent=2))
