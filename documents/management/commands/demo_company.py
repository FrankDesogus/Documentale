"""
Management command: demo_company

Crea o ricrea l'intera struttura demo aziendale, incluso l'account
supervisor_demo per presentazioni con singolo accesso.

Uso tipico:
    py manage.py demo_company --reset --no-email

Account creati:
  supervisor_demo  (is_superuser, is_staff) — presentazione completa UI
  admin_demo       (is_superuser, is_staff) — amministrazione tecnica Django

Per abilitare le deroghe demo nel browser:
    DOCUMENTALE_DEMO_MODE=true  (variabile d'ambiente)
"""

from django.contrib.auth.models import Group, User
from django.core.management.base import BaseCommand
from django.test.utils import override_settings

from documents.permissions import (
    GROUP_APPROVERS,
    GROUP_AUTHORS,
    GROUP_AUDITORS,
    GROUP_MANAGERS,
    GROUP_QUALITY_MANAGER,
    GROUP_QUALITY_OPERATOR,
    GROUP_READERS,
)
from ecn.permissions import GROUP_CCB

SUPERVISOR_USERNAME = 'supervisor_demo'
SUPERVISOR_PASSWORD = 'demo1234'
ADMIN_USERNAME = 'admin_demo'
ADMIN_PASSWORD = 'admin1234'

# Documenti demo creati per supervisor_demo
DEMO_CODES = {
    'published': 'DEMO-PUB-001',
    'draft': 'DEMO-DRAFT-001',
    'in_approval': 'DEMO-APPR-001',
    'ecn_ready': 'DEMO-ECN-001',
}


class Command(BaseCommand):
    help = (
        'Crea la struttura demo aziendale con account supervisor_demo. '
        'Usa --reset per ricreare da zero.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--reset',
            action='store_true',
            help='Elimina i dati demo prima di ricrearli.',
        )
        parser.add_argument(
            '--no-email',
            action='store_true',
            help='Disabilita invio email SMTP durante la creazione.',
        )

    def handle(self, *args, **options):
        if options['reset']:
            self._reset()
        if options['no_email']:
            with override_settings(
                EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend'
            ):
                self._run()
        else:
            self._run()

    # ------------------------------------------------------------------
    # Reset
    # ------------------------------------------------------------------

    def _reset(self):
        """
        Resetta l'intero database locale demo tramite flush.

        Guardrail obbligatori (tutti devono essere soddisfatti):
          1. settings.DEBUG == True
          2. database ENGINE == django.db.backends.sqlite3

        Senza questi guardrail il comando viene interrotto con un errore
        leggibile. Questo evita reset accidentali su staging o produzione.

        Motivazione: il database locale contiene dati fake interamente
        rigenerabili; flush è il modo più robusto per garantire pulizia
        completa indipendentemente dalle FK PROTECT presenti (es. ECN
        non-demo che referenziano documenti demo).
        """
        from django.conf import settings
        from django.db import connections

        db_engine = settings.DATABASES.get('default', {}).get('ENGINE', '')

        if not settings.DEBUG:
            self.stderr.write(
                self.style.ERROR(
                    'ERRORE: --reset è consentito solo con settings.DEBUG = True.\n'
                    'Questo comando è esclusivamente per l\'ambiente di sviluppo locale.'
                )
            )
            raise SystemExit(1)

        if 'sqlite3' not in db_engine:
            self.stderr.write(
                self.style.ERROR(
                    f'ERRORE: --reset è consentito solo con SQLite (ENGINE attuale: {db_engine}).\n'
                    'Non eseguire questo comando su un database non SQLite.'
                )
            )
            raise SystemExit(1)

        from django.core.management import call_command
        call_command('flush', interactive=False, verbosity=0)
        self.stdout.write('  >> Database locale svuotato (flush).')

    # ------------------------------------------------------------------
    # Flusso principale
    # ------------------------------------------------------------------

    def _run(self):
        self.stdout.write(self.style.MIGRATE_HEADING('\n=== DEMO COMPANY SETUP ===\n'))

        # ── Account tecnici ───────────────────────────────────────────
        supervisor = self._ensure_superuser(
            SUPERVISOR_USERNAME, SUPERVISOR_PASSWORD,
            'Supervisore', 'Demo', 'supervisor_demo@demo.local',
        )
        admin = self._ensure_superuser(
            ADMIN_USERNAME, ADMIN_PASSWORD,
            'Admin', 'Demo', 'admin_demo@demo.local',
        )

        # ── Gruppi documentali ────────────────────────────────────────
        g_authors   = Group.objects.get_or_create(name=GROUP_AUTHORS)[0]
        g_approvers = Group.objects.get_or_create(name=GROUP_APPROVERS)[0]
        g_readers   = Group.objects.get_or_create(name=GROUP_READERS)[0]
        g_auditors  = Group.objects.get_or_create(name=GROUP_AUDITORS)[0]
        g_managers  = Group.objects.get_or_create(name=GROUP_MANAGERS)[0]
        g_qm        = Group.objects.get_or_create(name=GROUP_QUALITY_MANAGER)[0]
        g_qop       = Group.objects.get_or_create(name=GROUP_QUALITY_OPERATOR)[0]
        g_ccb       = Group.objects.get_or_create(name=GROUP_CCB)[0]

        # supervisor_demo ha tutti i ruoli aziendali rilevanti
        for grp in [g_authors, g_approvers, g_auditors, g_managers, g_qm, g_ccb]:
            grp.user_set.add(supervisor)

        # ── Utenti reparto realistici ─────────────────────────────────
        mario   = self._ensure_user('mario.rossi',    'Mario',    'Rossi',    'mario.rossi@demo.local')
        lucia   = self._ensure_user('lucia.bianchi',  'Lucia',    'Bianchi',  'lucia.bianchi@demo.local')
        giorgio = self._ensure_user('giorgio.verdi',  'Giorgio',  'Verdi',    'giorgio.verdi@demo.local')
        anna    = self._ensure_user('anna.neri',      'Anna',     'Neri',     'anna.neri@demo.local')
        marco   = self._ensure_user('marco.esposito', 'Marco',    'Esposito', 'marco.esposito@demo.local')

        g_authors.user_set.add(mario, giorgio)
        g_approvers.user_set.add(lucia, anna)
        g_readers.user_set.add(marco)
        g_qm.user_set.add(lucia)
        g_ccb.user_set.add(anna, giorgio)
        g_auditors.user_set.add(marco)

        self._step('Utenti reparto: mario.rossi (autore), lucia.bianchi (approv./QM), '
                   'giorgio.verdi (autore/CCB), anna.neri (approv./CCB), marco.esposito (lettore/auditor)')

        # ── Cartelle aziendali ────────────────────────────────────────
        from projects.models import ProjectFolder, ProjectFolderMembership
        from projects.services import set_folder_path

        folder_qua = self._ensure_folder(
            'QUA', 'Qualità', ProjectFolder.FolderKind.DEPARTMENT,
            owner=supervisor, parent=None,
        )
        folder_ing = self._ensure_folder(
            'ING', 'Ingegneria', ProjectFolder.FolderKind.DEPARTMENT,
            owner=supervisor, parent=None,
        )
        folder_qua_proc = self._ensure_folder(
            'QUA-PROC', 'Procedure Qualità', ProjectFolder.FolderKind.GENERIC,
            owner=supervisor, parent=folder_qua,
        )
        folder_ing_std = self._ensure_folder(
            'ING-STD', 'Standard Tecnici', ProjectFolder.FolderKind.GENERIC,
            owner=supervisor, parent=folder_ing,
        )

        for f in [folder_qua, folder_ing, folder_qua_proc, folder_ing_std]:
            if not f.path:
                set_folder_path(f)

        # Membership realistica
        for role, user, folder in [
            ('author',   mario,   folder_qua_proc),
            ('author',   giorgio, folder_ing_std),
            ('approver', lucia,   folder_qua_proc),
            ('approver', anna,    folder_ing_std),
            ('reader',   marco,   folder_qua_proc),
            ('reader',   marco,   folder_ing_std),
            ('manager',  supervisor, folder_qua_proc),
            ('manager',  supervisor, folder_ing_std),
        ]:
            ProjectFolderMembership.objects.get_or_create(
                folder=folder, user=user,
                defaults={'role': role, 'created_by': supervisor},
            )

        self._step('Cartelle: QUA, ING, QUA-PROC, ING-STD con membership realistiche')

        # ── Progetto demo ─────────────────────────────────────────────
        prj_demo = self._create_project_demo(supervisor, folder_ing)

        # ── Dataset demo per supervisor_demo ──────────────────────────
        self._create_published_doc(supervisor, folder_qua_proc)
        self._create_draft_doc(supervisor, folder_qua_proc)
        self._create_in_approval_doc(supervisor, folder_qua_proc)
        self._create_ecn_ready_doc(supervisor, folder_qua_proc)

        # ── Riepilogo finale ──────────────────────────────────────────
        self.stdout.write(self.style.MIGRATE_HEADING('\n=== RIEPILOGO ACCOUNT ===\n'))
        self.stdout.write(
            self.style.SUCCESS(
                f'  {SUPERVISOR_USERNAME:<22} password: {SUPERVISOR_PASSWORD}  '
                '(is_superuser=True — PRESENTAZIONE UI)'
            )
        )
        self.stdout.write(
            self.style.SUCCESS(
                f'  {ADMIN_USERNAME:<22} password: {ADMIN_PASSWORD}  '
                '(is_superuser=True — Django Admin)'
            )
        )
        self.stdout.write('')
        self.stdout.write('  Utenti reparto (password: demo1234):')
        for u in [mario, lucia, giorgio, anna, marco]:
            self.stdout.write(f'    {u.username:<25} {u.get_full_name()}')
        self.stdout.write('')
        self.stdout.write(self.style.WARNING(
            '  Per attivare le deroghe demo nel browser:\n'
            '    DOCUMENTALE_DEMO_MODE=true  (variabile d\'ambiente)\n'
            '  Con demo mode attiva, supervisor_demo può selezionare sé stesso\n'
            '  come approvatore documentale e come membro CCB.'
        ))
        self.stdout.write(self.style.SUCCESS('\nSetup completato.'))

    # ------------------------------------------------------------------
    # Dataset supervisor_demo
    # ------------------------------------------------------------------

    def _create_published_doc(self, supervisor, folder):
        from documents.models import Document, DocumentVersion
        from documents.services import submit_version_for_approval
        from approvals.services import approve_version

        code = DEMO_CODES['published']
        if Document.objects.filter(code=code).exists():
            self._step(f'{code}: già esistente, saltato.')
            return

        doc = Document.objects.create(
            code=code,
            title='Procedura gestione documentale — Demo',
            category=Document.Category.QUALITY,
            document_type='Procedura',
            project_folder=folder,
            owner=supervisor,
            created_by=supervisor,
            status=Document.Status.ACTIVE,
        )
        ver = DocumentVersion.objects.create(
            document=doc,
            revision_label='00',
            revision_number=0,
            status=DocumentVersion.Status.DRAFT,
            is_current=False,
            created_by=supervisor,
            change_summary='Prima emissione.',
        )
        # Approva direttamente (supervisore è superuser)
        req = submit_version_for_approval(ver, supervisor, [supervisor])
        approve_version(req, supervisor, comment='Demo: prima emissione approvata.')
        doc.refresh_from_db()
        self._step(f'{code}: documento pubblicato (versione corrente approvata).')

    def _create_draft_doc(self, supervisor, folder):
        from documents.models import Document, DocumentVersion

        code = DEMO_CODES['draft']
        if Document.objects.filter(code=code).exists():
            self._step(f'{code}: già esistente, saltato.')
            return

        doc = Document.objects.create(
            code=code,
            title='Bozza privata — Work in Progress (Demo)',
            category=Document.Category.QUALITY,
            document_type='Istruzione operativa',
            project_folder=folder,
            owner=supervisor,
            created_by=supervisor,
        )
        DocumentVersion.objects.create(
            document=doc,
            revision_label='00',
            revision_number=0,
            status=DocumentVersion.Status.DRAFT,
            is_current=False,
            created_by=supervisor,
            change_summary='Bozza iniziale non ancora completata.',
        )
        self._step(f'{code}: bozza privata creata (solo supervisor_demo la vede).')

    def _create_in_approval_doc(self, supervisor, folder):
        from documents.models import Document, DocumentVersion
        from documents.services import submit_version_for_approval

        code = DEMO_CODES['in_approval']
        if Document.objects.filter(code=code).exists():
            self._step(f'{code}: già esistente, saltato.')
            return

        doc = Document.objects.create(
            code=code,
            title='Documento in corso di approvazione — Demo',
            category=Document.Category.QUALITY,
            document_type='Procedura',
            project_folder=folder,
            owner=supervisor,
            created_by=supervisor,
            status=Document.Status.ACTIVE,
        )
        ver = DocumentVersion.objects.create(
            document=doc,
            revision_label='00',
            revision_number=0,
            status=DocumentVersion.Status.DRAFT,
            is_current=False,
            created_by=supervisor,
            change_summary='Pronto per approvazione — demo.',
        )
        # Supervisore seleziona sé stesso come approvatore (possibile con is_superuser)
        submit_version_for_approval(ver, supervisor, [supervisor])
        self._step(f'{code}: in attesa di approvazione da parte di supervisor_demo.')

    def _create_ecn_ready_doc(self, supervisor, folder):
        """
        Crea un documento approvato + un ECN in bozza pronto per la valutazione CCB.
        """
        from documents.models import Document, DocumentVersion
        from documents.services import submit_version_for_approval
        from approvals.services import approve_version
        from ecn.services import create_change_notice
        from ecn.models import ChangeNotice

        code = DEMO_CODES['ecn_ready']
        if Document.objects.filter(code=code).exists():
            self._step(f'{code}: già esistente, saltato.')
            return

        # Documento approvato
        doc = Document.objects.create(
            code=code,
            title='Standard tecnico da aggiornare — Demo ECN',
            category=Document.Category.QUALITY,
            document_type='Standard',
            project_folder=folder,
            owner=supervisor,
            created_by=supervisor,
            status=Document.Status.ACTIVE,
        )
        ver = DocumentVersion.objects.create(
            document=doc,
            revision_label='00',
            revision_number=0,
            status=DocumentVersion.Status.DRAFT,
            is_current=False,
            created_by=supervisor,
            change_summary='Versione iniziale.',
        )
        req = submit_version_for_approval(ver, supervisor, [supervisor])
        approve_version(req, supervisor, comment='Versione iniziale approvata.')
        doc.refresh_from_db()

        # ECN con CCB configurata e istruttoria pronta per la dimostrazione
        ecn = create_change_notice(
            document=doc,
            proposed_by=supervisor,
            title='Aggiornamento requisiti tecnici sezione 4 — Demo',
            motivation=ChangeNotice.Motivation.IMPROVEMENT,
            description=(
                'Aggiornamento dello standard tecnico per recepire le nuove '
                'indicazioni normative. Impatto limitato alla sezione 4.'
            ),
            motivation_detail='Adeguamento ai nuovi requisiti normativi 2026.',
            code='ECN-DEMO-001',
        )
        # Configura CCB (supervisor_demo: responsabile istruttoria + unico componente)
        from ecn.services import configure_ccb, update_ccb_dossier
        configure_ccb(
            ecn, actor=supervisor,
            users=[supervisor],
            policy='any',
            coordinator=supervisor,
        )
        # Pre-compila il dossier istruttorio per la demo
        update_ccb_dossier(
            ecn, actor=supervisor,
            ccb_class='class2',
            ccb_requirements='Conforme ai requisiti normativi vigenti.',
            ccb_technical_impact='Impatto limitato alla sezione 4 del documento.',
            ccb_cost_impact='Nessun impatto economico aggiuntivo.',
            ccb_time_impact='Stimato 2 giorni lavorativi.',
            ccb_quality_impact='Miglioramento della conformità normativa.',
            ccb_other_impact='Nessun impatto su altri documenti correlati.',
            ccb_notes='Variante di adeguamento normativo — pre-approvata internamente.',
        )
        self._step(
            f'{code}: documento approvato + ECN {ecn.code} in istruttoria CCB '
            '(dossier pre-compilato, pronto per l\'invio alla CCB).'
        )

    # ------------------------------------------------------------------
    # Progetto demo
    # ------------------------------------------------------------------

    def _create_project_demo(self, supervisor, folder_ing):
        """Crea PRJ-DEMO-001 (Amplificatore RF Demo) come progetto con root folder in ING."""
        from documents.models import Document, DocumentVersion
        from projects.models import Project, ProjectFolder, ProjectFolderMembership
        from projects.services import create_project_with_root_folder, set_folder_path

        PRJ_CODE = 'PRJ-DEMO-001'

        # Progetto già esistente?
        try:
            prj = Project.objects.get(code=PRJ_CODE)
            self._step(f'{PRJ_CODE}: già esistente, saltato.')
            return prj
        except Project.DoesNotExist:
            pass

        # Crea progetto + root folder atomicamente
        prj = create_project_with_root_folder(
            parent_folder=folder_ing,
            code=PRJ_CODE,
            name='Amplificatore RF Demo',
            description='Progetto demo: sviluppo amplificatore RF per presentazioni.',
            project_type='engineering',
            manager=supervisor,
            created_by=supervisor,
            version='0',
            revision='0',
        )
        self._step(f'{PRJ_CODE}: progetto creato con root folder {prj.root_folder.code} (Ver. {prj.version} · Rev. {prj.revision}).')

        # Sottocartelle del progetto
        root = prj.root_folder
        folder_spec = self._ensure_folder(
            f'{PRJ_CODE}-SPEC', 'Specifiche', ProjectFolder.FolderKind.GENERIC,
            owner=supervisor, parent=root,
        )
        folder_coll = self._ensure_folder(
            f'{PRJ_CODE}-COLL', 'Collaudi', ProjectFolder.FolderKind.GENERIC,
            owner=supervisor, parent=root,
        )
        for f in [folder_spec, folder_coll]:
            if not f.path:
                set_folder_path(f)
        self._step(f'{PRJ_CODE}: sottocartelle Specifiche, Collaudi create.')

        # Membership sulla root folder (supervisor ha tutti i ruoli)
        ProjectFolderMembership.objects.get_or_create(
            folder=root, user=supervisor,
            defaults={'role': 'manager', 'created_by': supervisor},
        )

        # Documento demo nelle sottocartelle del progetto
        from documents.services import submit_version_for_approval
        from approvals.services import approve_version

        def _make_approved_doc(code, title, doc_type, folder):
            if Document.objects.filter(code=code).exists():
                return Document.objects.get(code=code)
            doc = Document.objects.create(
                code=code,
                title=title,
                category=Document.Category.QUALITY,
                document_type=doc_type,
                project_folder=folder,
                owner=supervisor,
                created_by=supervisor,
                status=Document.Status.ACTIVE,
            )
            ver = DocumentVersion.objects.create(
                document=doc,
                revision_label='00',
                revision_number=0,
                status=DocumentVersion.Status.DRAFT,
                is_current=False,
                created_by=supervisor,
                change_summary='Prima emissione demo.',
            )
            req = submit_version_for_approval(ver, supervisor, [supervisor])
            approve_version(req, supervisor, comment='Demo: approvazione automatica.')
            doc.refresh_from_db()
            self._step(f'{code}: documento demo creato e approvato.')
            return doc

        _make_approved_doc(
            f'{PRJ_CODE}-SPEC-001',
            'Specifiche tecniche amplificatore RF — Demo',
            'Specifica',
            folder_spec,
        )
        _make_approved_doc(
            f'{PRJ_CODE}-TEST-001',
            'Piano di collaudo amplificatore RF — Demo',
            'Piano di collaudo',
            folder_coll,
        )

        return prj

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _ensure_superuser(self, username, password, first_name, last_name, email=''):
        user, created = User.objects.get_or_create(
            username=username,
            defaults={
                'first_name': first_name,
                'last_name': last_name,
                'email': email,
                'is_superuser': True,
                'is_staff': True,
            },
        )
        if not created:
            user.is_superuser = True
            user.is_staff = True
            if not user.email and email:
                user.email = email
            user.save(update_fields=['is_superuser', 'is_staff', 'email'])
        user.set_password(password)
        user.save(update_fields=['password'])
        label = 'creato' if created else 'aggiornato'
        self.stdout.write(
            self.style.SUCCESS(f'  {username:<22} ({label}) — password: {password}')
        )
        return user

    def _ensure_user(self, username, first_name, last_name, email=''):
        user, created = User.objects.get_or_create(
            username=username,
            defaults={
                'first_name': first_name,
                'last_name': last_name,
                'email': email,
            },
        )
        if created:
            user.set_password('demo1234')
            user.save(update_fields=['password'])
        label = 'creato' if created else 'già esistente'
        self.stdout.write(f'  {username:<25} ({label})')
        return user

    def _ensure_folder(self, code, name, kind, owner, parent=None):
        from projects.models import ProjectFolder
        folder, _ = ProjectFolder.objects.get_or_create(
            code=code,
            defaults={
                'name': name,
                'folder_kind': kind,
                'status': ProjectFolder.Status.ACTIVE,
                'owner': owner,
                'created_by': owner,
                'parent': parent,
            },
        )
        return folder

    def _step(self, message):
        self.stdout.write(f'  >> {message}')
