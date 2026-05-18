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

