\# Documentale Django Interno - Regole di progetto



Questo progetto è un sistema documentale interno temporaneo ma serio per una piccola azienda.



Il progetto viene sviluppato su Windows con PyCharm, Python virtual environment e Django.



Non deve diventare un gestionale enorme.

Non deve includere firma digitale.

Non deve includere OCR, OpenSearch, React, workflow engine complessi o funzionalità non richieste.



Obiettivo principale:

\- gestire documenti qualità e documenti di progetto;

\- gestire revisioni;

\- gestire approvazioni interne;

\- inviare email agli approvatori;

\- mantenere audit trail;

\- mostrare agli utenti normali solo l'ultima revisione approvata;

\- mostrare bozze solo ad autore/responsabili;

\- mostrare versioni obsolete solo a utenti speciali;

\- mantenere dati ordinati per futura migrazione.



Stack:

\- Django 5.2 LTS;

\- SQLite in sviluppo locale;

\- PostgreSQL compatibile per futuro deploy;

\- Django templates;

\- eventualmente HTMX solo se utile;

\- niente React per ora;

\- niente firma digitale;

\- niente workflow engine complesso.



App Django:

\- accounts

\- documents

\- approvals

\- projects

\- auditlog

\- notifications



Principio fondamentale:

Document != DocumentVersion != DocumentFile.



Un Document è l'identità logica.

Una DocumentVersion è una revisione.

Un DocumentFile è il file fisico caricato.



Gli utenti normali devono vedere solo:

\- documenti attivi;

\- ultima versione approvata;

\- mai bozze;

\- mai versioni obsolete;

\- mai documenti rifiutati.



Gli approvatori possono approvare solo se indicati nella ApprovalRequest.

L'autore può essere anche approvatore, se è indicato tra gli approvatori.



Ogni azione importante deve scrivere AuditLog.



Non aggiungere funzionalità fuori scope senza chiedere.

Procedi per piccoli step.

Dopo ogni step, indica cosa hai modificato e come testarlo.

## Current handoff

Prima di iniziare nuovi task, leggere `PROJECT_HANDOFF.md` per il checkpoint corrente, i comandi di avvio e la roadmap immediata.

## Modalità Sanatoria

La modalità sanatoria è una funzionalità opzionale per il backfill di dati storici.
Non modifica il workflow live. Non invia notifiche. Non usa firma digitale.

Attivazione: variabile d'ambiente `DOCUMENTALE_DEMO_MODE=true`.
Accesso: solo utenti con `is_demo_supervisor=True` (o `is_superuser` con username `supervisor_demo`).
Controllo: `can_use_sanatoria(user)` in `auditlog/permissions.py`.

Principio:

\- Il checkbox "Sanatoria" nei form è opzionale e default False.

\- Se spuntato, la vista chiama `form.maybe_create_historical_record(event_type, target_instance, recorded_by)`.

\- Viene creato un `HistoricalRecord` con data storica, attore storico e sorgente forniti dall'utente.

\- L'operazione principale (salvataggio documento, approvazione, ecc.) avviene normalmente.

\- Le notifiche email NON vengono soppresse esplicitamente — le viste con sanatoria semplicemente non le attivano.

Regole:

\- NON modificare retroattivamente timestamp tecnici (`created_at`, `updated_at`).

\- NON usare raw SQL.

\- NON introdurre impersonazione silenziosa.

\- NON mostrare diciture come "Firmato digitalmente da X" se non esiste firma digitale verificabile.

\- NON costruire wizard massivi o import CSV/Excel senza richiesta esplicita.

Il mixin `SanatoriaFieldsMixin` deve essere PRIMO nell'MRO:

```python
class MyForm(SanatoriaFieldsMixin, forms.ModelForm): ...
```

Partial template: `{% include "auditlog/sanatoria_fields.html" %}` (richiede `sanatoria_available` nel context).

Dettagli completi in `PROJECT_HANDOFF.md` — sezione "SANATORIA MODE".
