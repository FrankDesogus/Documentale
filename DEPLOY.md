# Guida al deploy — Documentale

Deploy su server Linux (Debian/Ubuntu) con PostgreSQL, gunicorn e nginx.

---

## Prerequisiti server

```bash
sudo apt update
sudo apt install python3.12 python3.12-venv python3-pip postgresql nginx
```

---

## 1. Database PostgreSQL

```bash
sudo -u postgres psql
```

```sql
CREATE DATABASE documentale;
CREATE USER documentale_user WITH PASSWORD 'password-sicura';
ALTER ROLE documentale_user SET client_encoding TO 'utf8';
ALTER ROLE documentale_user SET default_transaction_isolation TO 'read committed';
ALTER ROLE documentale_user SET timezone TO 'Europe/Rome';
GRANT ALL PRIVILEGES ON DATABASE documentale TO documentale_user;
\q
```

---

## 2. Codice applicativo

```bash
# Scegliere una directory, es. /srv/documentale
sudo mkdir -p /srv/documentale
sudo chown $USER:$USER /srv/documentale

git clone <repository-url> /srv/documentale
cd /srv/documentale

python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

---

## 3. File di configurazione

```bash
cp .env.example .env
nano .env
```

Valori minimi da compilare:

| Variabile | Cosa inserire |
|---|---|
| `SECRET_KEY` | output di `python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"` |
| `ALLOWED_HOSTS` | IP o dominio del server (es. `192.168.1.50` o `documentale.esempio.it`) |
| `CSRF_TRUSTED_ORIGINS` | URL completo (es. `https://documentale.esempio.it`) |
| `DB_PASSWORD` | password scelta al punto 1 |
| `DEBUG` | `False` |

---

## 4. Migrazione e file statici

```bash
source .venv/bin/activate
python manage.py migrate
python manage.py collectstatic --noinput
python manage.py createsuperuser
```

---

## 5. Creazione gruppi applicativi

```bash
python manage.py shell -c "
from django.contrib.auth.models import Group
for name in ['Document Authors', 'Document Managers', 'Quality Managers', 'Document Auditors']:
    Group.objects.get_or_create(name=name)
print('Gruppi creati.')
"
```

---

## 6. Servizio systemd per gunicorn

Creare `/etc/systemd/system/documentale.service`:

```ini
[Unit]
Description=Documentale Django (gunicorn)
After=network.target

[Service]
User=www-data
Group=www-data
WorkingDirectory=/srv/documentale
EnvironmentFile=/srv/documentale/.env
ExecStart=/srv/documentale/.venv/bin/gunicorn \
    --workers 3 \
    --bind unix:/run/documentale.sock \
    config.wsgi:application
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

```bash
sudo chown -R www-data:www-data /srv/documentale
sudo systemctl daemon-reload
sudo systemctl enable documentale
sudo systemctl start documentale
```

---

## 7. Configurazione nginx

Creare `/etc/nginx/sites-available/documentale`:

```nginx
server {
    listen 80;
    server_name documentale.esempio.it;  # o IP del server

    location /static/ {
        alias /srv/documentale/staticfiles/;
    }

    location /media/ {
        alias /srv/documentale/media/;
    }

    location / {
        include proxy_params;
        proxy_pass http://unix:/run/documentale.sock;
    }
}
```

```bash
sudo ln -s /etc/nginx/sites-available/documentale /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl reload nginx
```

---

## 8. SSL con Let's Encrypt (se dominio pubblico)

```bash
sudo apt install certbot python3-certbot-nginx
sudo certbot --nginx -d documentale.esempio.it
```

Per SSL su rete interna (IP privato), usare un certificato aziendale e configurare manualmente il blocco `listen 443 ssl` in nginx.

Una volta SSL attivo, abilitare nel `.env`:

```
SECURE_SSL_REDIRECT=True
SESSION_COOKIE_SECURE=True
CSRF_COOKIE_SECURE=True
SECURE_HSTS_SECONDS=31536000
```

---

## 9. Permessi cartella media

```bash
sudo mkdir -p /srv/documentale/media
sudo chown www-data:www-data /srv/documentale/media
```

---

## Aggiornamenti futuri

```bash
cd /srv/documentale
source .venv/bin/activate
git pull
pip install -r requirements.txt
python manage.py migrate
python manage.py collectstatic --noinput
sudo systemctl restart documentale
```

---

## Verifica

```bash
sudo systemctl status documentale
sudo journalctl -u documentale -n 50
```
