# 🐾 Sa Majesté le Poilu — Boutique & Dashboard

Application e-commerce Flask autonome : **boutique publique Pop-Art** + **dashboard admin**.

## 🚀 Lancer

```bash
pip install flask          # Flask >= 3
python app.py              # serveur sur http://127.0.0.1:5000
```

## 🗺️ Pages

| URL | Contenu |
|---|---|
| `/` | Accueil (hero mascottes, produits vedettes, « Royaume ») |
| `/catalogue` | Catalogue + filtres par catégorie |
| `/produit/<id>` | Fiche produit : variante taille/couleur, réassurance |
| `/page/a-propos` • `/page/contact` • `/page/mentions` | Pages de contenu |
| `/admin` | Dashboard (mot de passe initial : **poilu2026**) |

## 🔐 Dashboard

4 onglets : **Tableau de bord** (CA/marge/produit vedette), **Produits** (CRUD
complet + upload d'images), **Commandes** (statuts Nouvelle → Expédiée → Livrée),
**Réglages** (bannières, frais de port, mot de passe).
Changez le mot de passe dès la première connexion (onglet Réglages).

## 💾 Données

Persistées dans `data/boutique.json` (réséédé auto si absent).
Images uploadées dans `static/uploads/`.

## 🧪 Vérifié

- Toutes les pages en 200, uploads et JSON valides
- Parcours : commande simulée → login admin → stats → changement de statut → CRUD produit
- Accès admin refusé (401) sans session ; API protégée par `@login_required`
