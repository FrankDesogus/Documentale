# Istruzioni operative per Codex

Prima di modificare qualsiasi file, leggi integralmente `./CLAUDE.md`.
`CLAUDE.md` contiene il contesto architetturale e le regole consolidate del progetto.

Regole permanenti:
- Non fare push automaticamente.
- Non fare merge o rebase.
- Non modificare `main`.
- Non usare `git reset --hard`, `git restore`, `git checkout -- <file>` o comandi distruttivi senza autorizzazione esplicita.
- Non cancellare modifiche locali preesistenti: potrebbero provenire da un altro agente.
- Non lanciare suite globali lunghe salvo richiesta esplicita.
- Durante lo sviluppo usa test mirati, `python manage.py check` e `python manage.py makemigrations --check --dry-run`.
- Non avviare test runner duplicati o paralleli.
- Mantieni attivi Django runserver e Tailwind watch durante i lavori UI quando richiesto.
- Crea commit locali separati e leggibili al termine dei blocchi stabili.
