# Project Handoff

## Stato Git

Branch di lavoro: `authz-foundation`

Checkpoint stabile: `fe32980 fix(ecn): allow assigned coordinator to view dossier (ECN-FIX-1)`

Commit recenti (dal più recente):
```
fe32980 fix(ecn): allow assigned coordinator to view dossier (ECN-FIX-1)
3d0cdcd feat(projects): integrate optional sanatoria tracking (SAN-5)
3f2d812 test(projects): update legacy snapshot creation tests
eadcc05 feat(ecn): integrate optional sanatoria tracking (SAN-4)
29279db feat(documents): integrate optional sanatoria tracking (SAN-3)
737dd02 feat(auditlog): add reusable sanatoria form support
79f779b feat(auditlog): add historical backfill models
4bf6568 feat(projects): add save-version and save-revision UI
```

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
- **SANATORIA MODE (SAN-1→SAN-5 + ECN-FIX-1)** — vedi sezione dedicata

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

---

## SANATORIA MODE — completato (SAN-1→SAN-5 + ECN-FIX-1)

La modalità sanatoria permette a un supervisore demo di registrare retroattivamente
eventi storici già avvenuti nel passato, senza modificare il workflow live normale.

### Distinzione workflow live vs workflow sanatoria

| Aspetto | Workflow live | Workflow sanatoria |
|---|---|---|
| Attore | utente reale che agisce ora | recorded_by = tecnico che registra |
| Timestamp | automatico (`auto_now_add`) | `historical_date` inserito manualmente |
| Notifiche | attive | **soppresse** |
| Firma digitale | non presente (by design) | non presente (non simulare) |
| Sorgente | sistema | campo `source` libero |
| Scopo | operazioni correnti | backfill dati passati per audit trail |

### Prerequisiti ambiente

```powershell
$env:DOCUMENTALE_DEMO_MODE = "true"
```

L'utente supervisore deve avere `is_superuser=True` e username `supervisor_demo`
(oppure il flag `is_demo_supervisor` impostato a `True` sull'account).

La variabile d'ambiente è verificata da `can_use_sanatoria(user)` in `auditlog/permissions.py`.
Senza `DOCUMENTALE_DEMO_MODE=true`, il checkbox sanatoria non viene mostrato a nessuno.

### Modelli storici (auditlog app)

**`HistoricalRecord`** — un record per ogni evento storico

| Campo | Tipo | Descrizione |
|---|---|---|
| `event_type` | CharField (choices) | Tipo evento (vedi EventType) |
| `target_content_type` | FK ContentType | Tipo oggetto target |
| `target_object_id` | PositiveIntegerField | PK oggetto target |
| `recorded_by` | FK User | Tecnico che ha registrato il dato |
| `historical_actor` | CharField | Nome libero dell'attore originale |
| `historical_date` | DateField | Data storica dell'evento |
| `source` | CharField | Sorgente (es. "Registro qualità 2021") |
| `notes` | TextField | Note libere |
| `created_at` | DateTimeField | Timestamp tecnico di registrazione |

**`HistoricalRecord.EventType`** (choices disponibili):
- `DOCUMENT_APPROVED` — documento approvato
- `DOCUMENT_REJECTED` — documento rifiutato
- `DOCUMENT_CREATED` — documento creato
- `DOCUMENT_VERSION_CREATED` — nuova versione creata
- `PROJECT_CREATED` — progetto creato
- `PROJECT_METADATA_UPDATED` — metadati progetto aggiornati
- `PROJECT_VERSION_SAVED` — snapshot versione salvato
- `PROJECT_REVISION_SAVED` — snapshot revisione salvata
- `PROJECT_SNAPSHOT_ISSUED` — snapshot emesso
- `ECN_SUBMITTED` — ECN inviata a CCB
- `ECN_APPROVED` — ECN approvata
- `ECN_REJECTED` — ECN rifiutata
- `ECN_CLOSED` — ECN chiusa

### Helper e form mixin

**`SanatoriaFieldsMixin`** (`auditlog/historical_forms.py`)

Plain Python mixin (NON sottoclasse di forms.BaseForm).
Deve essere **primo** nell'MRO prima di `forms.Form` / `forms.ModelForm`.

```python
class MyForm(SanatoriaFieldsMixin, forms.ModelForm):
    ...
```

Campi aggiunti al form quando `current_user` viene passato al costruttore
e `can_use_sanatoria(user)` è True:
- `is_sanatoria` (BooleanField, default=False, non required)
- `historical_actor` (CharField, max 200, non required)
- `historical_date` (DateField, non required)
- `source` (CharField, max 200, non required)
- `notes_sanatoria` (CharField textarea, non required)

**`maybe_create_historical_record(event_type, target_instance, recorded_by)`**

Metodo del mixin. Crea `HistoricalRecord` solo se `is_sanatoria=True` nel form.
Chiamare **dopo** il salvataggio dell'oggetto principale.
Non solleva eccezioni se il form non è stato validato o se `is_sanatoria=False`.

### Partial Tailwind

`templates/auditlog/sanatoria_fields.html`

Partial riutilizzabile. Mostra il blocco sanatoria **solo** se `sanatoria_available` è True nel context.
Da includere nei form con:

```django
{% include "auditlog/sanatoria_fields.html" %}
```

Il context deve contenere `sanatoria_available` (booleano) — passato da ogni view che supporta la sanatoria.

### Aree integrate (app e viste)

**documents app (SAN-3)**
- `document_create_view` — evento `DOCUMENT_CREATED`
- `document_version_create_view` — evento `DOCUMENT_VERSION_CREATED`
- Template: `document_form.html`, `document_version_form.html`

**approvals app (SAN-3)**
- `approve_document_version_view` — evento `DOCUMENT_APPROVED`
- `reject_document_version_view` — evento `DOCUMENT_REJECTED`
- Template: `approval_form.html` (o simile)

**ecn app (SAN-4)**
- `ecn_submit_view` — evento `ECN_SUBMITTED`
- `ecn_review_view` (approve) — evento `ECN_APPROVED`
- `ecn_review_view` (reject) — evento `ECN_REJECTED`
- `ecn_close_view` — evento `ECN_CLOSED`
- Template: form di submit/review/close

**projects app (SAN-5)**
- `project_create` — evento `PROJECT_CREATED`
- `project_edit` — evento `PROJECT_METADATA_UPDATED`
- `project_snapshot_create` (version) — evento `PROJECT_VERSION_SAVED`
- `project_snapshot_create` (revision) — evento `PROJECT_REVISION_SAVED`
- `project_revision_issue` — evento `PROJECT_SNAPSHOT_ISSUED`
- Template: `project_form.html`, `project_edit.html`, `project_snapshot_form.html`, `project_revision_detail.html`

### Fix ECN correlati (ECN-FIX-1)

**`ecn/permissions.py`** — `can_view_ecn`

Il `ccb_coordinator` designato (campo `ChangeNotice.ccb_coordinator`) ora riceve
visibilità sull'ECN specifica, sia sul dettaglio che sul dossier istruttorio.

Prima del fix: il coordinator poteva compilare il dossier (`can_compile_dossier`) ma
la vista era bloccata da `can_view_ecn`. Ora `can_view_ecn` include esplicitamente:

```python
if change_notice.ccb_coordinator_id and change_notice.ccb_coordinator_id == user.pk:
    return True
```

Il coordinator **non** ottiene i permessi di governance (configure_ccb, submit, review, close).

### Limiti

- Il backfill dei `HistoricalRecord` è manuale, form per form, una operazione alla volta.
- Non esiste un wizard di import massivo (fuori scope in questo blocco).
- Non esiste import CSV/Excel (fuori scope).
- `HistoricalRecord` non sostituisce `AuditLog` — sono due sistemi paralleli con scopi diversi.
- Il flag `is_sanatoria` è lato form (non persistito sull'oggetto principale).
- La soppressione delle notifiche è implicita (le viste con sanatoria non chiamano servizi di notifica).

### Test copertura sanatoria

- `auditlog/tests.py` — `HistoricalRecordModelTests`, `SanatoriaFieldsMixinTests`
- `documents/tests.py` — `DocumentSanatoriaTests` (SAN-3)
- `approvals/tests.py` — `ApprovalSanatoriaTests` (SAN-3)
- `ecn/tests.py` — `ECNSanatoriaTests` (SAN-4), `ECNCoordinatorViewTests` (ECN-FIX-1)
- `projects/tests.py` — `ProjectSanatoriaTests` (SAN-5, 12 test)

### Backlog sanatoria (fuori scope attuale)

- Wizard multi-step per backfill massivo
- Import da CSV/Excel
- Vista admin `HistoricalRecord` con filtri per tipo evento e data storica
- Export audit trail storico in PDF

---

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

Eseguire la suite finale di verifica su tutte le app modificate nel blocco SAN:

```powershell
py manage.py test auditlog documents approvals ecn projects --keepdb --failfast --verbosity=1
```

Poi procedere con eventuali task backlog o la migrazione a PostgreSQL per il deploy.
