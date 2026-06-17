# Project Handoff

## Stato Git

Branch di lavoro: `authz-foundation`

Checkpoint stabile: `08f293b feat(deploy): production-ready settings, env template and deploy guide`

Commit recenti (dal più recente):
```
08f293b feat(deploy): production-ready settings, env template and deploy guide
fd66ef6 docs: update handoff branch and commit log after ECNPOL-1 merge
d5c7f69 feat(documents): add configurable ECN policy per document (ECNPOL-1)  ← merge ECNPOL-1
4a56b96 docs(ecnpol): update handoff with final test count and coverage detail
c1ed8cb test(ecnpol): add button visibility and context tests for exempt documents
5e9f2ba docs(ecnpol): document ui/model semantic inversion and tighten policy test
b577bb8 refactor(documents): invert ecn policy checkbox semantics in creation form
57467a4 docs: document ecn optional policy (ECNPOL-1)
c61dcee test(documents): add ECNPolicyServiceTests and ECNPolicyViewTests
9d4caff feat(documents): update templates for ecn policy
69856a1 feat(documents): wire ecn policy to form and views
4877bed feat(documents): enforce ecn policy in create_new_revision service
e981b77 feat(documents): add requires_ecn_for_revision policy to Document
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

L'utente supervisore deve avere `is_superuser=True` e username `supervisor_demo`.

La verifica è interamente runtime in `config/demo_utils.py:is_demo_supervisor()`:
controlla che `DOCUMENTALE_DEMO_MODE` sia attivo e che `user.username` corrisponda a
`DOCUMENTALE_DEMO_SUPERVISOR_USERNAME` (default: `'supervisor_demo'`).
**Non esiste un flag `is_demo_supervisor` sul modello User** — `accounts/models.py` non ha
estensioni al modello standard Django.

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

## ECN OPTIONAL POLICY — completato (ECNPOL-1)

Branch: `feat/ecn-optional-policy` (derivato da `authz-foundation`)

### Obiettivo

Rendere il gate ECN configurabile per singolo `Document`. Per default tutti i documenti
continuano a richiedere ECN. Alcuni documenti possono essere creati con approvazione diretta.

### Campo aggiunto

**`Document.requires_ecn_for_revision`** (BooleanField, default=True)
- `True`: comportamento standard invariato — ECN approvato obbligatorio per nuova revisione
- `False`: gate ECN bypassato — revisione creabile direttamente, ciclo di approvazione obbligatorio

Migrazione: `documents/0005_ecn_policy_flag.py` — retrocompatibile, tutti i record esistenti = True.

### Regola di dominio

La protezione è centralizzata nel service `documents/services.py:create_new_revision()`:
```python
if (
    not _bypass_ecn_check
    and document.current_version is not None
    and document.current_version.status == DocumentVersion.Status.APPROVED
    and document.requires_ecn_for_revision   ← nuova condizione
):
    validate_approved_unused_ecn(...)
```

### Invarianti

- Il bypass riguarda **solo il gate ECN** — il ciclo DRAFT→IN_APPROVAL→APPROVED rimane obbligatorio
- La revisione senza ECN nasce come DRAFT, non è corrente finché non è approvata
- La policy si sceglie alla creazione del documento — **non modificabile** dalla form di modifica metadati
- Il consumo one-shot degli ECN standard (`executed_version`) resta invariato
- Audit log traccia `requires_ecn_for_revision` alla creazione del documento (action: DOCUMENT_CREATED)

### UI

- `new_document.html`: sezione "Governance revisioni" con checkbox **`ecn_exemption`** (default=False, non spuntata)
  - Semantica invertita rispetto al campo modello: spuntare = esentare dall'ECN
  - La view traduce: `requires_ecn_for_revision = not ecn_exemption`
- `document_detail.html`: badge "Modalità revisione: ECN obbligatorio" o "approvazione diretta senza ECN"
- `document_detail.html`: pulsante "+ Nuova revisione" mostrato per utenti con `can_create_revision`
  anche senza `can_create_ecn` se il documento è esente
- `new_revision.html`: banner informativo "Approvazione diretta" per documenti esenti

### Nota semantica UI vs Modello vs Admin

| Superficie | Campo | Default | Significato "attivo" |
|---|---|---|---|
| Model / Service | `requires_ecn_for_revision` | True | ECN obbligatorio |
| Django Admin | `requires_ecn_for_revision` | True (spuntato) | ECN obbligatorio |
| Form creazione | `ecn_exemption` | False (non spuntato) | ECN obbligatorio ← **stessa cosa** |

Il campo `ecn_exemption` è form-only (non persistito); la traduzione avviene nella view `new_document`.
`DocumentMetadataEditForm` non espone né `requires_ecn_for_revision` né `ecn_exemption` (test verifica entrambi).

### Test

`documents/tests.py` — `ECNPolicyServiceTests` (10 test service) + `ECNPolicyViewTests` (13 test view)
23 test nuovi, tutti verdi.

Copertura view tests:
- Caso 10: `ecn_exemption` non spuntata di default nel form creazione
- Caso 11: policy assente in `DocumentMetadataEditForm` (sia `requires_ecn_for_revision` che `ecn_exemption`)
- Caso 12a/b: badge corretto nel detail (ECN obbligatorio / approvazione diretta)
- Caso 13: stranger bloccato su documento esente (404)
- Caso 14: modalità sanatoria non rotta dalla nuova policy
- Caso 15: audit log registra `requires_ecn_for_revision` alla creazione
- Caso extra: GET new_revision su doc esente mostra form direttamente (no ECN select)
- Caso extra: POST new_revision su doc esente crea DRAFT senza ECN
- Caso 16: pulsante "+ Nuova revisione" (senza "via ECN") visibile nel detail per doc esente
- Caso 17: context include `show_create_revision=True` per l'autore su doc esente

---

## Stato corrente (2026-06-17)

Suite completa eseguita e verificata verde (1127 test, 0 errori, 2026-06-17).
Nessuna regressione.

Branch: `main` — tutto committato e pushato su `origin/main`.

Blocchi completati nell'ultima sessione (2026-06-17):

### UI-NAV-1 — version_detail page
- Nuova pagina `/versions/<id>/` → `version_detail`
- Mostra: metadati revisione, ECN di origine (se presente), cicli di approvazione con decisioni, dati storici sanatoria
- Template: `templates/documents/version_detail.html`
- URL registrato in `config/urls.py`
- View in `documents/views.py`

### UI-NAV-2 — interattività UI globale
Tutti i riferimenti a documenti, versioni, ECN nelle pagine principali sono ora link cliccabili:

| Template | Elemento linkato |
|---|---|
| `document_list.html` | Riga cliccabile, codice → `document_detail`, revisione → `version_detail` |
| `my_drafts.html` | Codice → `document_detail`, revisione → `version_detail` |
| `project_detail.html` | Codice doc + revisione + codice ECN linkati |
| `folder_detail.html` | Codice doc/progetto/cartella linkati in ricerca e lista |
| `ecn_detail.html` | "Rev. riferimento" + "Revisione eseguita" → `version_detail` |
| `ecn_dashboard.html` | Codici ECN → `ecn_detail` |
| `document_detail.html` | Righe versioni cliccabili, "Storico eventi" → link per tipo oggetto |
| `approval_detail.html` | Breadcrumb con link a documento e versione |

### UI-NAV-3 — sanatoria in approval_detail e ecn_detail
- `approvals/views.py`: passa `historical_records` filtrati per versione
- `ecn/views.py`: passa `historical_records` filtrati per ECN
- Sezione "Dati storici (sanatoria)" aggiunta in fondo a entrambi i template

### FIX — can_submit_for_approval
- `documents/permissions.py`: guard aggiunto — versioni non in stato `draft` o `rejected` non sono mai inviabili in approvazione, nemmeno per superuser
- Bug: il pulsante "Invia in approvazione" compariva su versioni `superseded`

### DEMO — demo_full command
- `documents/management/commands/demo_full.py` (596 righe)
- Chiama `demo_company` come base e aggiunge 7 scenari estesi:
  1. Documento con 3 revisioni (storico completo, versioni superate)
  2. Documento con revisione rifiutata
  3. ECN in tutti e 6 gli stati (draft → ccb_preparation → under_review → approved → rejected → closed)
  4. ECN che origina una revisione (ECN di origine visibile in version_detail)
  5. Documento esente ECN (approvazione diretta)
  6. Approvazione con policy ANY e SEQUENTIAL (in attesa)
  7. Batch sanatoria con 5 record storici per DEMO-MULTI-001
- Uso: `py manage.py demo_full --reset --no-email`

## Deploy prep — completato

Commit: `08f293b feat(deploy): production-ready settings, env template and deploy guide`

| File | Modifica |
|---|---|
| `config/settings.py` | Tutti i valori sensibili letti da env via `python-decouple` |
| `.env` | Configurazione locale dev (git-ignored) |
| `.env.example` | Template committato — copiare sul server e compilare |
| `requirements.txt` | Aggiunto `gunicorn==23.0.0` |
| `DEPLOY.md` | Procedura completa: PostgreSQL, systemd, nginx, SSL, aggiornamenti |

Variabili d'ambiente gestite: `SECRET_KEY`, `DEBUG`, `ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS`,
`SECURE_SSL_REDIRECT`, `SESSION_COOKIE_SECURE`, `CSRF_COOKIE_SECURE`, `SECURE_HSTS_*`,
`DB_ENGINE` / `DB_NAME` / `DB_USER` / `DB_PASSWORD` / `DB_HOST` / `DB_PORT`,
`DOCUMENTALE_DEMO_MODE`, email settings.

## Prossimo passo

Scegliere tra:
1. **Deploy effettivo** — eseguire la procedura `DEPLOY.md` sul server aziendale.
2. **Completare migrazione permessi** — eseguire `backfill_folder_permission_grants` per convertire tutti i `ProjectFolderMembership` legacy in `FolderPermissionGrant` modulari, poi rimuovere il fallback legacy.
3. **Task backlog** — wizard sanatoria multi-step, admin HistoricalRecord con filtri, export audit trail PDF.
4. **Avvio rapido demo** — `py manage.py demo_full --reset --no-email` per avere il DB demo completo.

Comando sviluppo:

```powershell
$env:DOCUMENTALE_DEMO_MODE = "true"
py manage.py runserver
py manage.py test auditlog documents approvals ecn projects accounts notifications --keepdb --failfast --verbosity=1
```
