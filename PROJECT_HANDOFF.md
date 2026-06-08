# Project Handoff

## Stato Git

Branch di lavoro: `authz-foundation`

Checkpoint stabile: `4bf6568 feat(projects): add save-version and save-revision UI`

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
- **PROJECT-VERSION-HISTORY (VH-1→VH-4)** — vedi sezione dedicata

## PROJECT-VERSION-HISTORY — completato

Tutti e quattro i microstep sono stati committati su `authz-foundation`.

### VH-1 — `feat(projects): reintroduce project version metadata`
- `Project.version_scheme` + `Project.version` (assi indipendenti da `revision_scheme`/`revision`)
- Default: `'numeric'`/`'00'`
- Aggiornati: forms, services, admin, UI (`project_detail`, `project_list`, `project_edit`, `project_form`), `demo_company`

### VH-2 — `feat(projects): extend project history snapshots`
- `ProjectRevision.SnapshotType`: `VERSION` / `REVISION`
- 8 campi metadati congelati su `ProjectRevision`
- 4 campi denormalizzati su `ProjectRevisionItem`
- Constraint `unique_together` include `snapshot_type`
- Partial unique index `is_current` per `(project, snapshot_type)`
- `create_project_revision`, `issue_project_revision`, `build_project_baseline_comparison` aggiornati

### VH-3 — `feat(projects): add save-version and save-revision UI`
- Vista `project_snapshot_create` + URL `/projects/<id>/snapshot/new/?snapshot_type=version|revision`
- `project_snapshot_form.html`: form semplificato (titolo, descrizione, note)
- Legacy URL `/revisions/new/` redirige al nuovo flusso
- `project_detail`: sezione "Storico progetto" divisa in "Versioni salvate" / "Revisioni salvate"
- Due pulsanti "Salva versione" / "Salva revisione" per i manager
- `project_revision_detail`: mostra tipo snapshot, metadati congelati, campi denormalizzati

### VH-4 — `feat(projects): enforce issued snapshot immutability`
- `_IMMUTABLE_STATUSES` = `{ISSUED, SUPERSEDED, ARCHIVED}`
- `assert_snapshot_mutable(snapshot)`: helper riutilizzabile
- `populate_project_revision_from_current_documents` + `issue_project_revision` usano il helper
- 47 test VH totali (16 VH-1 + 13 VH-2 + 9 VH-3 + 9 VH-4), tutti verdi

## Architettura snapshot — riferimento rapido

```
Project.version_scheme / Project.version      ← asse versione (manuale)
Project.revision_scheme / Project.revision    ← asse revisione (manuale)

ProjectRevision.snapshot_type = 'version' | 'revision'
ProjectRevision.revision_label   ← derivato da project.version o project.revision
ProjectRevision.snapshot_project_*  ← metadati congelati al momento della creazione
ProjectRevisionItem.snapshot_document_*  ← dati documento congelati al momento del popolamento
```

## Stato UI

- Light mode predefinita.
- Night mode opzionale tramite toggle.
- Preferenza browser in `localStorage`: `documentale-theme`.
- Sidebar glass navy/ciano in entrambe le modalità.

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
- Eseguire suite più ampie soltanto a checkpoint importanti.

## Vincoli operativi

- Lavorare sul branch `authz-foundation`.
- Non fare push automaticamente durante lo sviluppo.
- Non fare merge su `main` senza richiesta esplicita.
- Non scartare modifiche locali preesistenti.
- Non usare `git reset --hard`.

## Prossimo passo

Verifica manuale della UI: aprire un progetto, salvare una versione e una revisione, emettere uno snapshot,
verificare che la sezione "Storico progetto" mostri correttamente le due sezioni separate.
