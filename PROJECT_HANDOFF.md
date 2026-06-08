# Project Handoff

## Stato Git

Branch di lavoro: `authz-foundation`

Checkpoint stabile: `db32d65 feat(ui): add light mode and persistent ELT night theme`

## Blocchi principali già completati

- materialized path cartelle
- permessi modulari `FolderPermissionGrant`
- resolver con fallback legacy
- integrazione permessi su cartelle, progetti e documenti
- modalità `supervisor_demo`
- root folder esclusiva dei progetti
- ricerca contestuale nelle cartelle e nei progetti
- email evento-driven complete
- campanella notifiche in-app
- Tailwind CSS
- tema ELT light mode + night mode persistente

## Stato UI

- Light mode predefinita.
- Night mode opzionale tramite toggle.
- Preferenza browser in `localStorage`: `documentale-theme`.
- Sidebar glass navy/ciano in entrambe le modalità.

## Comandi sviluppo Linux

```bash
source .venv/bin/activate
export DOCUMENTALE_DEMO_MODE=true
python manage.py migrate
python manage.py runserver
npm run dev
```

## Comandi sviluppo Windows PowerShell

```powershell
.venv\Scripts\Activate.ps1
$env:DOCUMENTALE_DEMO_MODE = "true"
py manage.py migrate
py manage.py runserver
npm run dev
```

## Demo locale

URL: `http://127.0.0.1:8000/`

Login demo: `supervisor_demo`

Password demo locale: `demo1234`

Queste sono esclusivamente credenziali dimostrative locali. Non usarle come credenziali reali o condivise fuori dall'ambiente demo.

## Politica test

- Preferire test mirati.
- Usare `--keepdb --failfast` quando utile.
- Non avviare runner paralleli.
- Non lanciare automaticamente la suite globale da circa un'ora.
- Eseguire suite piu ampie soltanto a checkpoint importanti.

## Vincoli operativi

- Lavorare sul branch `authz-foundation`.
- Non fare push automaticamente durante lo sviluppo.
- Non fare merge su `main` senza richiesta esplicita.
- Non scartare modifiche locali preesistenti.
- Non usare `git reset --hard`.

## Prossimo passo

Il prossimo lavoro deve partire da una prova manuale della UI e dalla raccolta di eventuali problemi reali, non da un refactor automatico.
