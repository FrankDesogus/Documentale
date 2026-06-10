# AI_CONTEXT — Documentale Django

Questo file è un briefing tecnico sintetico per AI (ChatGPT, Claude, Codex, ecc.).
Descrive architettura, modelli, flussi e vincoli del progetto.
Non sostituisce il codice — è pensato per ragionare su modifiche e produrre prompt precisi.

---

## Cos'è il progetto

Sistema documentale interno per una piccola azienda. Gestisce:
- Documenti di qualità e di progetto con revisioni e approvazioni
- Engineering Change Notices (ECN) per modificare documenti già pubblicati
- Progetti tecnici con storico versioni/revisioni
- Audit trail di ogni azione
- Backfill storico opzionale (modalità sanatoria)

**Non è e non deve diventare**: gestionale, sistema con firma digitale, OCR, workflow engine, React SPA.

---

## Stack

- Django 5.2 LTS, Python, SQLite (sviluppo) / PostgreSQL (produzione futura)
- Django templates + Tailwind CSS v3
- Niente React, niente Celery, niente firma digitale

---

## App Django

| App | Responsabilità |
|---|---|
| `accounts` | Utente Django standard (nessun modello custom) |
| `documents` | Document, DocumentVersion, DocumentFile + servizi di revisione |
| `approvals` | ApprovalRequest, approvatori, decisioni, politiche |
| `ecn` | ChangeNotice (ECN), CCB, decisioni, dossier istruttorio |
| `projects` | ProjectFolder, Project, ProjectRevision, permessi cartella |
| `auditlog` | AuditLog (live) + HistoricalRecord (backfill sanatoria) |
| `notifications` | Notification in-app + NotificationLog email |
| `config` | Settings, demo_utils, costanti globali |

---

## Modello di dominio

### Principio fondamentale

```
Document  ≠  DocumentVersion  ≠  DocumentFile

Document         → identità logica (codice, titolo, categoria)
DocumentVersion  → una revisione specifica (stato, label, file)
DocumentFile     → il file fisico caricato (hash SHA-256, MIME)
```

### documents app

**DocumentFile**
- `file`, `original_filename`, `mime_type`, `extension`, `size`, `sha256_hash`
- `uploaded_by` (FK User), `uploaded_at`

**DocumentVersion.Status**: `DRAFT → IN_APPROVAL → APPROVED / REJECTED → SUPERSEDED / ARCHIVED`
- `document` (FK Document), `revision_label`, `revision_number`
- `status`, `is_current` (bool — una sola versione corrente per documento)
- `file` (FK DocumentFile)
- `created_by`, `created_at`, `submitted_at`, `approved_at`, `approved_by`
- `rejected_at`, `rejection_reason`, `change_summary`
- `replaces_version` (FK self — catena revisioni)

**Document.Status**: `ACTIVE / OBSOLETE / ARCHIVED`
**Document.Category**: categorie configurabili
- `code` (unique), `title`, `description`, `category`, `document_type`
- `status`, `owner`, `created_by`, `created_at`, `updated_at`
- `current_version` (FK DocumentVersion — la versione approvata/corrente)
- `project_folder` (FK ProjectFolder)
- `revision_scheme`: schema numerazione revisioni

### approvals app

**ApprovalRequest.Status**: `PENDING / APPROVED / REJECTED / CANCELLED`
**ApprovalRequest.Policy**: `ANY` (uno qualsiasi approva) / `ALL` (tutti devono approvare) / `SEQUENTIAL`
- `document_version` (FK), `requested_by`, `requested_at`
- `approval_policy`, `due_date`, `notes`, `completed_at`

**ApprovalRequestApprover**: chi può approvare la specifica richiesta
- `approval_request`, `approver` (FK User), `order`, `status`, `notified_at`, `decided_at`

**ApprovalDecision**: il voto di un approvatore
- `approval_request`, `approver`, `decision` (APPROVE/REJECT), `comment`, `decided_at`

**ApprovalRequestAttachment**: allegati alla richiesta

### ecn app

**ChangeNotice.Status**: `DRAFT → CCB_PREPARATION → UNDER_REVIEW → APPROVED / REJECTED → CLOSED`
**ChangeNotice.CCBPolicy**: `ANY / ALL / SEQUENTIAL`
**ChangeNotice.CCBClass**: `CLASS_1 / CLASS_2`
- `code` (unique), `title`, `description`, `motivation`, `commessa`
- `document` (FK Document), `document_version` (FK — baseline), `project` (FK, opzionale)
- `proposed_by`, `proposed_at`, `submitted_at`, `status`
- **Dossier CCB**: `ccb_policy`, `ccb_coordinator` (FK User), `ccb_class`
  - `ccb_requirements`, `ccb_technical_impact`, `ccb_cost_impact`, `ccb_time_impact`, `ccb_quality_impact`, `ccb_other_impact`, `ccb_notes`
- `executed_version` (FK DocumentVersion — la revisione creata dopo approvazione, usa l'ECN una sola volta)
- `closed_by`, `closed_at`, `close_notes`

**ChangeNoticeApprover**: membro CCB che può votare, con `order` per policy SEQUENTIAL
**ChangeNoticeDecision**: `APPROVE / REJECT` + comment

### projects app

**ProjectFolder** (albero materialized path)
- `code`, `name`, `description`, `folder_kind` (ROOT / STANDARD / ARCHIVE)
- `parent` (FK self), `path` (materialized path), `status`, `owner`, `is_active`

**FolderPermissionGrant** (permessi modulari granulari)
- `folder`, `user` o `group` (uno dei due), `permission_code`, `effect` (ALLOW/DENY)
- `inherit_to_children`, `expires_at`
- PermissionCode: `view_folder / create_document / edit_document / approve_document / manage_folder / audit_folder`

**ProjectFolderMembership** (legacy, coesiste con FolderPermissionGrant via fallback)
- `folder`, `user`, `role` (READER / AUTHOR / APPROVER / AUDITOR / MANAGER)

**Project**
- `code`, `name`, `description`, `project_type`
- `version_scheme`, `version` (asse versione manuale)
- `revision_scheme`, `revision` (asse revisione manuale)
- `root_folder` (OneToOne FK ProjectFolder), `manager`, `created_by`

**ProjectRevision** (snapshot immutabile del progetto)
- `project`, `snapshot_type` (VERSION / REVISION)
- `revision_label`, `revision_number`, `title`, `description`
- `status` (DRAFT → ISSUED → SUPERSEDED / ARCHIVED), `is_current`
- 8 campi congelati al momento della creazione: metadati progetto al momento dello snapshot
- `issued_at`, `issued_by`, `replaces_revision`

**ProjectRevisionItem** (documenti inclusi nello snapshot)
- `revision` (FK ProjectRevision), `document_version` (FK DocumentVersion, PROTECT)
- 4 campi congelati: `snapshot_document_code`, `snapshot_document_title`, `snapshot_document_revision_label`, `snapshot_folder_path`

### auditlog app

**AuditLog** — audit trail tecnico live, immutabile
- `timestamp` (auto), `user`, `action`, `app_label`, `model_name`, `object_id`, `object_repr`
- `changes` (JSONField), `ip_address`
- Creato manualmente chiamando `create_audit_log()` nei servizi/viste

**HistoricalImportBatch** — raggruppa backfill storici per rollback coerente
- `code`, `description`, `status` (OPEN/COMPLETED/ROLLED_BACK), `created_by`, `completed_by`

**HistoricalRecord** — evento storico retrodatato (solo modalità sanatoria)
- `import_batch` (FK), `event_type` (enum: DOC_CREATED, DOC_APPROVED, ECN_SUBMITTED, ecc.)
- `historical_actor_user` (FK, opzionale) o `historical_actor_name` (testo libero)
- `historical_date` + `date_precision` (EXACT_DATE / MONTH / YEAR / UNKNOWN)
- `target_app`, `target_model`, `target_id`, `target_repr`
- `source_description` (es. "Verbale CCB 27 del 2021-03-15")
- `recorded_by`, `recorded_at`, `is_verified`

---

## Flussi principali

### Flusso documento standard

```
1. Autore crea Document + DocumentVersion (status: DRAFT)
2. Autore carica DocumentFile e lo associa alla versione
3. Autore invia in approvazione → DocumentVersion diventa IN_APPROVAL
   → Crea ApprovalRequest con lista approvatori
4. Ogni approvatore esprime ApprovalDecision (APPROVE/REJECT)
5a. Se approvato (secondo policy):
    → DocumentVersion diventa APPROVED, is_current=True
    → Vecchia current_version diventa SUPERSEDED, is_current=False
    → Document.current_version punta alla nuova versione
5b. Se rifiutato:
    → DocumentVersion diventa REJECTED
    → L'autore può creare una nuova bozza
```

### Gate ECN (regola critica)

Il gate ECN è controllato dalla policy `Document.requires_ecn_for_revision` (default `True`).

**`requires_ecn_for_revision=True` (default):**
Se un Document ha già una `current_version` approvata, per creare una nuova revisione
è **obbligatorio** un ECN con status APPROVED che:
- Punta a quel Document
- Non sia già stato usato (`executed_version` deve essere null)

Dopo la creazione della revisione, `ECN.executed_version` viene impostato alla nuova versione.
L'ECN può essere usato **una sola volta**.

**`requires_ecn_for_revision=False`:**
Il gate ECN è bypassato. La nuova revisione può essere creata direttamente senza ECN.
Il normale ciclo di approvazione della revisione rimane **obbligatorio**:
```text
DRAFT → IN_APPROVAL → APPROVED oppure REJECTED
```
La revisione non diventa mai automaticamente approvata o corrente.

La policy si imposta alla creazione del documento (checkbox nel form).
**Non è modificabile tramite il normale form di modifica metadati.**
Tutti i documenti esistenti mantengono `requires_ecn_for_revision=True` (migrazione retrocompatibile).

### Flusso ECN

```
1. Proponente crea ChangeNotice (status: DRAFT) su un Document specifico
2. Quality Manager invia al CCB → status: CCB_PREPARATION
3. CCB Coordinator compila il dossier istruttorio (impatti tecnici, costi, ecc.)
4. Coordinator sottopone al voto → status: UNDER_REVIEW
5. Membri CCB votano (ChangeNoticeDecision: APPROVE/REJECT)
6a. Approvato (secondo ccb_policy): status APPROVED
    → L'autore può creare la nuova revisione citando questo ECN
    → executed_version viene impostato alla nuova revisione
    → ECN va in CLOSED
6b. Rifiutato: status REJECTED
```

### Visibilità documenti

- **Utenti normali**: vedono solo DocumentVersion APPROVED + is_current=True di Document ACTIVE
- **Autori**: vedono anche le proprie bozze (DRAFT/REJECTED) nella propria cartella
- **Manager/Auditor globali**: vedono tutti i documenti pubblicati
- **Superuser**: bypass completo
- **is_staff**: NON ottiene bypass applicativo (solo accesso Django Admin)

### Sistema permessi cartella (due layer)

Il resolver (`projects/resolver.py`) valuta in ordine:
1. `FolderPermissionGrant` (modulare, granulare) — ha precedenza
2. `ProjectFolderMembership` (legacy, per ruoli) — usato come fallback

`include_legacy_fallback=True` è il default: il sistema è in migrazione da legacy a modulare.

---

## Modalità Sanatoria

Feature opzionale per backfill di dati storici. Attivata da `DOCUMENTALE_DEMO_MODE=true` (env var).
Accessibile solo all'utente con `username == DOCUMENTALE_DEMO_SUPERVISOR_USERNAME` (default: `supervisor_demo`).
**Non è un campo sul modello User** — è un controllo runtime in `config/demo_utils.is_demo_supervisor()`.

### Come funziona

Nei form che supportano la sanatoria, `SanatoriaFieldsMixin` aggiunge campi opzionali:
- `is_sanatoria` (bool), `historical_actor`, `historical_date`, `source`, `notes_sanatoria`

Il mixin **deve essere primo nell'MRO**:
```python
class MyForm(SanatoriaFieldsMixin, forms.ModelForm): ...
```

Dopo il salvataggio dell'oggetto principale, la vista chiama:
```python
form.maybe_create_historical_record(event_type, target_instance, recorded_by)
```

Le notifiche email sono soppresse tramite:
```python
send_notifications=should_send_notifications(sanatoria=form.is_sanatoria)
```

### App integrate con sanatoria

- `documents`: creazione documento, creazione revisione
- `approvals`: approvazione, rifiuto
- `ecn`: submit, approvazione CCB, rifiuto CCB, chiusura
- `projects`: creazione progetto, modifica metadati, salvataggio snapshot

---

## Gruppi utente (permessi globali)

| Gruppo | Accesso |
|---|---|
| `readers` | Lettura documenti pubblicati |
| `authors` | Crea/modifica bozze |
| `approvers` | Approva revisioni |
| `auditors` | Lettura tutto (incluso storico) |
| `managers` | Gestione completa documenti |
| `quality_managers` | Gestione ECN, visibilità totale |
| `quality_operators` | Operatività qualità |
| `direction` | Accesso direzionale |
| `ecn_proposers` | Può creare ECN |
| `ccb` | Membro CCB, può votare ECN |

---

## Regole architetturali — cosa NON fare

- Non aggiungere firma digitale
- Non aggiungere OCR, OpenSearch, workflow engine complesso
- Non usare React
- Non introdurre `is_staff` come bypass applicativo (solo Django Admin)
- Non modificare timestamp tecnici (`created_at`, `updated_at`) retroattivamente
- Non usare raw SQL
- Non costruire wizard multi-step o import CSV/Excel senza richiesta esplicita
- Non fare push o merge su `main` senza autorizzazione esplicita
- Non usare `git reset --hard`

---

## Stato attuale (2026-06-10)

Branch attivo: `authz-foundation`

Blocchi completati:
- **Documenti e revisioni**: flusso completo DRAFT→APPROVED, gate ECN, visibilità per ruolo
- **Approvazioni**: policy ANY/ALL/SEQUENTIAL, allegati, notifiche email + in-app
- **ECN**: flusso completo DRAFT→CLOSED, dossier CCB, visibilità coordinator (ECN-FIX-1)
- **Progetti**: versioning (version_scheme + revision_scheme), snapshot VERSION/REVISION, immutabilità ISSUED
- **Permessi cartella**: sistema modulare FolderPermissionGrant + fallback legacy, resolver integrato
- **Sanatoria mode**: integrata in documents, approvals, ecn, projects (SAN-1→SAN-5)
- **UI**: Tailwind CSS, sidebar, dashboard persona-centrica, light/dark mode

Suite: 1186 test, 0 errori (verificata 2026-06-10).

Backlog aperto:
- Migrazione da SQLite a PostgreSQL per il deploy
- Completare migrazione permessi (eseguire `backfill_folder_permission_grants`, rimuovere fallback legacy)
- Admin HistoricalRecord con filtri per tipo evento e data
- Export audit trail in PDF

---

## File chiave per area

| Area | File principali |
|---|---|
| Modelli documento | `documents/models.py`, `documents/services.py` |
| Approvazioni | `approvals/models.py`, `approvals/services.py` |
| ECN | `ecn/models.py`, `ecn/services.py`, `ecn/permissions.py` |
| Progetti/cartelle | `projects/models.py`, `projects/permissions.py`, `projects/resolver.py` |
| Sanatoria | `auditlog/models.py`, `auditlog/historical_forms.py`, `auditlog/permissions.py`, `config/demo_utils.py` |
| Permessi documenti | `documents/permissions.py` |
| Notifiche | `notifications/models.py`, `notifications/services.py` |
| Regole progetto | `CLAUDE.md`, `PROJECT_HANDOFF.md` |
