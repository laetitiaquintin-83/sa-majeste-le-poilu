"""Configuration gunicorn — Sa Majesté le Poilu.

Chargée automatiquement par gunicorn (fichier `gunicorn.conf.py` du répertoire
courant). Utilisée par le start command de Render : `gunicorn app:app`.
"""
import os

# Render définit la variable PORT ; en local on retombe sur 8000.
bind = f"0.0.0.0:{os.environ.get('PORT', '8000')}"

# UN SEUL worker : la boutique stocke ses données dans des fichiers JSON
# (data/*.json) avec des verrous par processus. Plusieurs workers = écritures
# concurrentes qui peuvent corrompre la base. Le trafic est absorbé par les
# threads ci-dessous.
workers = 1
threads = 4

# Les appels sortants (Stripe, CJ, SMTP) peuvent être lents.
timeout = 120
graceful_timeout = 30

accesslog = "-"
errorlog = "-"
