from django.contrib.auth.models import Group, User
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError
from django.test import TestCase

from documents.models import Document, DocumentVersion
from auditlog.models import AuditLog
from documents.permissions import GROUP_AUTHORS, GROUP_AUDITORS, GROUP_MANAGERS
from ecn.models import ChangeNotice, ChangeNoticeAttachment
from ecn.permissions import (
    GROUP_CCB,
    can_close_ecn,
    can_create_ecn,
    can_review_ecn,
    can_submit_ecn,
    can_view_ecn,
)
from ecn.services import (
    approve_change_notice,
    close_change_notice,
    create_change_notice,
    reject_change_notice,
    submit_change_notice,
)
from projects.models import ProjectFolder, ProjectFolderMembership


# ---------------------------------------------------------------------------
# Helper: factory per dati di test minimi
# ---------------------------------------------------------------------------

def _make_user(username='tester'):
    return User.objects.create_user(username=username, password='pw')


def _make_folder(owner, code='FOLD-01'):
    return ProjectFolder.objects.create(
        code=code,
        name='Cartella test',
        owner=owner,
        created_by=owner,
    )


def _make_document(owner, folder=None, code='DOC-ECN-001'):
    return Document.objects.create(
        code=code,
        title='Documento di test ECN',
        category=Document.Category.QUALITY,
        document_type='Procedura',
        owner=owner,
        created_by=owner,
        project_folder=folder,
    )


def _make_version(document, created_by, label='00', number=0):
    return DocumentVersion.objects.create(
        document=document,
        revision_label=label,
        revision_number=number,
        status=DocumentVersion.Status.APPROVED,
        is_current=True,
        created_by=created_by,
    )


def _make_ecn(document, version, proposed_by, code='ECN-001', **kwargs):
    defaults = dict(
        title='Variante di test',
        description='Descrizione variante',
        motivation=ChangeNotice.Motivation.IMPROVEMENT,
        proposed_by=proposed_by,
        created_by=proposed_by,
    )
    defaults.update(kwargs)
    return ChangeNotice.objects.create(
        code=code,
        document=document,
        document_version=version,
        **defaults,
    )


# ---------------------------------------------------------------------------
# Test modello ChangeNotice
# ---------------------------------------------------------------------------

class ChangeNoticeModelTests(TestCase):

    def setUp(self):
        self.user = _make_user('autore_ecn')
        self.folder = _make_folder(self.user)
        self.document = _make_document(self.user, self.folder)
        self.version = _make_version(self.document, self.user)

    def test_str_contains_code_title_status(self):
        ecn = _make_ecn(self.document, self.version, self.user)
        s = str(ecn)
        self.assertIn('ECN-001', s)
        self.assertIn('Variante di test', s)
        self.assertIn('Bozza', s)

    def test_default_status_is_draft(self):
        ecn = _make_ecn(self.document, self.version, self.user, code='ECN-002')
        self.assertEqual(ecn.status, ChangeNotice.Status.DRAFT)

    def test_code_must_be_unique(self):
        _make_ecn(self.document, self.version, self.user, code='ECN-DUP')
        with self.assertRaises(IntegrityError):
            _make_ecn(self.document, self.version, self.user, code='ECN-DUP')

    def test_all_status_choices_valid(self):
        valid = {s.value for s in ChangeNotice.Status}
        self.assertIn('draft', valid)
        self.assertIn('under_review', valid)
        self.assertIn('approved', valid)
        self.assertIn('rejected', valid)
        self.assertIn('closed', valid)

    def test_all_motivation_choices_valid(self):
        valid = {m.value for m in ChangeNotice.Motivation}
        self.assertIn('improvement', valid)
        self.assertIn('customer', valid)
        self.assertIn('non_conformity', valid)
        self.assertIn('design', valid)
        self.assertIn('regulatory', valid)
        self.assertIn('other', valid)

    def test_all_ccb_class_choices_valid(self):
        valid = {c.value for c in ChangeNotice.CCBClass}
        self.assertIn('class1', valid)
        self.assertIn('class2', valid)

    def test_ccb_class_nullable(self):
        ecn = _make_ecn(self.document, self.version, self.user, code='ECN-003')
        self.assertIsNone(ecn.ccb_class)

    def test_project_nullable(self):
        ecn = _make_ecn(self.document, self.version, self.user, code='ECN-004')
        self.assertIsNone(ecn.project)

    def test_executed_version_nullable(self):
        ecn = _make_ecn(self.document, self.version, self.user, code='ECN-005')
        self.assertIsNone(ecn.executed_version)

    def test_closed_at_nullable(self):
        ecn = _make_ecn(self.document, self.version, self.user, code='ECN-006')
        self.assertIsNone(ecn.closed_at)
        self.assertIsNone(ecn.closed_by)

    def test_submitted_at_nullable(self):
        ecn = _make_ecn(self.document, self.version, self.user, code='ECN-007')
        self.assertIsNone(ecn.submitted_at)

    def test_proposed_at_is_set_automatically(self):
        ecn = _make_ecn(self.document, self.version, self.user, code='ECN-008')
        self.assertIsNotNone(ecn.proposed_at)

    def test_ordering_is_by_proposed_at_descending(self):
        ecn1 = _make_ecn(self.document, self.version, self.user, code='ECN-ORD-A')
        ecn2 = _make_ecn(self.document, self.version, self.user, code='ECN-ORD-B')
        qs = list(ChangeNotice.objects.filter(code__startswith='ECN-ORD'))
        # Più recente (creato dopo) deve essere il primo
        self.assertEqual(qs[0].pk, ecn2.pk)
        self.assertEqual(qs[1].pk, ecn1.pk)

    def test_description_fields_blank_by_default(self):
        ecn = _make_ecn(self.document, self.version, self.user, code='ECN-FIELDS')
        self.assertEqual(ecn.motivation_detail, '')
        self.assertEqual(ecn.commessa, '')
        self.assertEqual(ecn.ccb_requirements, '')
        self.assertEqual(ecn.ccb_technical_impact, '')
        self.assertEqual(ecn.ccb_cost_impact, '')
        self.assertEqual(ecn.ccb_time_impact, '')
        self.assertEqual(ecn.ccb_quality_impact, '')
        self.assertEqual(ecn.ccb_other_impact, '')
        self.assertEqual(ecn.ccb_notes, '')
        self.assertEqual(ecn.close_notes, '')

    def test_all_description_fields_can_be_saved(self):
        ecn = _make_ecn(
            self.document, self.version, self.user,
            code='ECN-FULLTEXT',
            motivation_detail='Motivazione dettagliata.',
            commessa='COMM-2025-001',
            ccb_requirements='Analisi requisiti completa.',
            ccb_technical_impact='Impatto tecnico: nessuno.',
            ccb_cost_impact='Costo stimato: 0.',
            ccb_time_impact='Tempi: 1 giorno.',
            ccb_quality_impact='Qualità: migliorata.',
            ccb_other_impact='Nessun impatto su altri documenti.',
            ccb_notes='Note CCB: approvata.',
            close_notes='Chiusura verificata.',
        )
        ecn.refresh_from_db()
        self.assertEqual(ecn.motivation_detail, 'Motivazione dettagliata.')
        self.assertEqual(ecn.commessa, 'COMM-2025-001')
        self.assertEqual(ecn.ccb_requirements, 'Analisi requisiti completa.')
        self.assertEqual(ecn.ccb_technical_impact, 'Impatto tecnico: nessuno.')
        self.assertEqual(ecn.ccb_cost_impact, 'Costo stimato: 0.')
        self.assertEqual(ecn.ccb_time_impact, 'Tempi: 1 giorno.')
        self.assertEqual(ecn.ccb_quality_impact, 'Qualità: migliorata.')
        self.assertEqual(ecn.ccb_other_impact, 'Nessun impatto su altri documenti.')
        self.assertEqual(ecn.ccb_notes, 'Note CCB: approvata.')
        self.assertEqual(ecn.close_notes, 'Chiusura verificata.')

    def test_document_relation(self):
        ecn = _make_ecn(self.document, self.version, self.user, code='ECN-DOC')
        self.assertEqual(ecn.document.pk, self.document.pk)
        # Accesso inverso
        self.assertIn(ecn, self.document.ecns.all())

    def test_document_version_relation(self):
        ecn = _make_ecn(self.document, self.version, self.user, code='ECN-VER')
        self.assertEqual(ecn.document_version.pk, self.version.pk)
        self.assertIn(ecn, self.version.ecns_as_baseline.all())

    def test_executed_version_inverse_relation(self):
        ecn = _make_ecn(self.document, self.version, self.user, code='ECN-EXEC')
        # Creo una seconda versione (la "nuova revisione" eseguita)
        new_version = DocumentVersion.objects.create(
            document=self.document,
            revision_label='01',
            revision_number=1,
            status=DocumentVersion.Status.DRAFT,
            is_current=False,
            created_by=self.user,
        )
        ecn.executed_version = new_version
        ecn.save(update_fields=['executed_version'])
        self.assertIn(ecn, new_version.ecns_executed.all())

    def test_protect_on_document_delete_is_enforced(self):
        """PROTECT: eliminare il documento deve fallire se ha ECN collegati."""
        from django.db.models import ProtectedError
        _make_ecn(self.document, self.version, self.user, code='ECN-PROT')
        with self.assertRaises(ProtectedError):
            self.document.delete()

    def test_meta_verbose_name(self):
        self.assertEqual(ChangeNotice._meta.verbose_name, 'ECN / Variante')
        self.assertEqual(ChangeNotice._meta.verbose_name_plural, 'ECN / Varianti')


# ---------------------------------------------------------------------------
# Test modello ChangeNoticeAttachment
# ---------------------------------------------------------------------------

class ChangeNoticeAttachmentModelTests(TestCase):

    def setUp(self):
        self.user = _make_user('uploader_ecn')
        self.folder = _make_folder(self.user, code='FOLD-ATT')
        self.document = _make_document(self.user, self.folder, code='DOC-ATT-001')
        self.version = _make_version(self.document, self.user)
        self.ecn = _make_ecn(self.document, self.version, self.user, code='ECN-ATT')

    def _make_attachment(self, title='Allegato test', **kwargs):
        defaults = dict(
            uploaded_by=self.user,
            original_filename='allegato.pdf',
            extension='pdf',
            size=1024,
        )
        defaults.update(kwargs)
        # file è obbligatorio ma non vogliamo caricare file reali nei test model:
        # usiamo un percorso fittizio che Django accetta senza toccare il filesystem.
        from django.core.files.base import ContentFile
        att = ChangeNoticeAttachment(
            change_notice=self.ecn,
            title=title,
            **defaults,
        )
        att.file.save('test_ecn.pdf', ContentFile(b'fake pdf content'), save=False)
        att.save()
        return att

    def test_str_contains_ecn_code_and_filename(self):
        att = self._make_attachment()
        s = str(att)
        self.assertIn('ECN-ATT', s)
        self.assertIn('allegato.pdf', s)

    def test_str_fallback_to_title_when_no_original_filename(self):
        att = self._make_attachment(original_filename='', title='Specifiche tecniche')
        s = str(att)
        self.assertIn('Specifiche tecniche', s)

    def test_cascade_delete_with_ecn(self):
        self._make_attachment()
        count_before = ChangeNoticeAttachment.objects.count()
        self.assertEqual(count_before, 1)
        # Elimino l'ECN: gli allegati devono essere eliminati a cascata
        self.ecn.delete()
        self.assertEqual(ChangeNoticeAttachment.objects.count(), 0)

    def test_fields_stored_correctly(self):
        att = self._make_attachment(
            title='Disegno tecnico',
            description='Schema di montaggio aggiornato.',
            extension='pdf',
            size=204800,
        )
        att.refresh_from_db()
        self.assertEqual(att.title, 'Disegno tecnico')
        self.assertEqual(att.description, 'Schema di montaggio aggiornato.')
        self.assertEqual(att.extension, 'pdf')
        self.assertEqual(att.size, 204800)
        self.assertEqual(att.uploaded_by.pk, self.user.pk)
        self.assertIsNotNone(att.uploaded_at)

    def test_size_accepts_large_values(self):
        """PositiveBigIntegerField: supporta file > 2 GB."""
        att = self._make_attachment(size=5_000_000_000)  # 5 GB
        att.refresh_from_db()
        self.assertEqual(att.size, 5_000_000_000)

    def test_attachment_linked_to_ecn(self):
        att = self._make_attachment()
        self.assertEqual(att.change_notice.pk, self.ecn.pk)
        self.assertIn(att, self.ecn.attachments.all())

    def test_ordering_is_by_uploaded_at_ascending(self):
        att1 = self._make_attachment(title='Primo')
        att2 = self._make_attachment(title='Secondo')
        qs = list(self.ecn.attachments.all())
        self.assertEqual(qs[0].pk, att1.pk)
        self.assertEqual(qs[1].pk, att2.pk)

    def test_meta_verbose_name(self):
        self.assertEqual(ChangeNoticeAttachment._meta.verbose_name, 'Allegato ECN')
        self.assertEqual(ChangeNoticeAttachment._meta.verbose_name_plural, 'Allegati ECN')


# ===========================================================================
# Helper per ECN-A2: utenti con ruoli
# ===========================================================================

def _make_user_in_groups(username, *group_names):
    user = User.objects.create_user(username=username, password='pw')
    for name in group_names:
        grp, _ = Group.objects.get_or_create(name=name)
        grp.user_set.add(user)
    return user


def _make_staff(username='staff_ecn'):
    return User.objects.create_user(username=username, password='pw', is_staff=True)


def _make_superuser(username='su_ecn'):
    return User.objects.create_superuser(username=username, password='pw')


def _make_approved_document(owner, folder=None, code='DOC-SVC'):
    """Crea un Document con una DocumentVersion corrente e approvata."""
    doc = _make_document(owner, folder, code)
    version = _make_version(doc, owner)
    doc.current_version = version
    doc.save(update_fields=['current_version'])
    return doc, version


# ===========================================================================
# Test permessi ECN
# ===========================================================================

class ECNPermissionsTests(TestCase):

    def setUp(self):
        self.superuser  = _make_superuser('perm_su')
        self.staff      = _make_staff('perm_staff')
        self.manager    = _make_user_in_groups('perm_mgr', GROUP_MANAGERS)
        self.ccb        = _make_user_in_groups('perm_ccb', GROUP_CCB)
        self.author     = _make_user_in_groups('perm_auth', GROUP_AUTHORS)
        self.auditor    = _make_user_in_groups('perm_aud', GROUP_AUDITORS)
        self.proposer   = _make_user('perm_prop')
        self.stranger   = _make_user('perm_stranger')

        self.folder   = _make_folder(self.manager, 'FOLD-PERM')
        self.document = _make_document(self.manager, self.folder, 'DOC-PERM')
        self.version  = _make_version(self.document, self.manager)
        self.document.current_version = self.version
        self.document.save(update_fields=['current_version'])

        self.ecn = _make_ecn(self.document, self.version, self.proposer, 'ECN-PERM')

    # --- can_view_ecn -------------------------------------------------------

    def test_superuser_can_view(self):
        self.assertTrue(can_view_ecn(self.superuser, self.ecn))

    def test_staff_can_view(self):
        self.assertTrue(can_view_ecn(self.staff, self.ecn))

    def test_manager_can_view(self):
        self.assertTrue(can_view_ecn(self.manager, self.ecn))

    def test_auditor_can_view(self):
        self.assertTrue(can_view_ecn(self.auditor, self.ecn))

    def test_ccb_can_view(self):
        self.assertTrue(can_view_ecn(self.ccb, self.ecn))

    def test_proposer_can_view_own_ecn(self):
        self.assertTrue(can_view_ecn(self.proposer, self.ecn))

    def test_stranger_cannot_view(self):
        self.assertFalse(can_view_ecn(self.stranger, self.ecn))

    def test_folder_auditor_can_view(self):
        folder_auditor = _make_user('perm_fold_aud')
        ProjectFolderMembership.objects.create(
            folder=self.folder, user=folder_auditor,
            role=ProjectFolderMembership.Role.AUDITOR,
            created_by=self.manager,
        )
        self.assertTrue(can_view_ecn(folder_auditor, self.ecn))

    # --- can_create_ecn -----------------------------------------------------

    def test_superuser_can_create(self):
        self.assertTrue(can_create_ecn(self.superuser, self.document))

    def test_manager_can_create(self):
        self.assertTrue(can_create_ecn(self.manager, self.document))

    def test_author_can_create(self):
        self.assertTrue(can_create_ecn(self.author, self.document))

    def test_reader_cannot_create(self):
        # stranger non ha né Author né Manager né ruolo per-cartella
        self.assertFalse(can_create_ecn(self.stranger, self.document))

    def test_ccb_cannot_create_without_author_role(self):
        # CCB di per sé non dà diritto a creare ECN
        self.assertFalse(can_create_ecn(self.ccb, self.document))

    def test_folder_author_can_create(self):
        fold_author = _make_user('perm_fold_auth')
        ProjectFolderMembership.objects.create(
            folder=self.folder, user=fold_author,
            role=ProjectFolderMembership.Role.AUTHOR,
            created_by=self.manager,
        )
        self.assertTrue(can_create_ecn(fold_author, self.document))

    # --- can_submit_ecn -----------------------------------------------------

    def test_proposer_can_submit_own_ecn(self):
        self.assertTrue(can_submit_ecn(self.proposer, self.ecn))

    def test_manager_can_submit(self):
        self.assertTrue(can_submit_ecn(self.manager, self.ecn))

    def test_stranger_cannot_submit(self):
        self.assertFalse(can_submit_ecn(self.stranger, self.ecn))

    def test_ccb_cannot_submit(self):
        # CCB non è proponente, né manager
        self.assertFalse(can_submit_ecn(self.ccb, self.ecn))

    # --- can_review_ecn -----------------------------------------------------

    def test_ccb_can_review(self):
        self.assertTrue(can_review_ecn(self.ccb, self.ecn))

    def test_manager_can_review(self):
        self.assertTrue(can_review_ecn(self.manager, self.ecn))

    def test_superuser_can_review(self):
        self.assertTrue(can_review_ecn(self.superuser, self.ecn))

    def test_author_cannot_review(self):
        self.assertFalse(can_review_ecn(self.author, self.ecn))

    def test_auditor_cannot_review(self):
        self.assertFalse(can_review_ecn(self.auditor, self.ecn))

    def test_proposer_cannot_review(self):
        self.assertFalse(can_review_ecn(self.proposer, self.ecn))

    # --- can_close_ecn ------------------------------------------------------

    def test_manager_can_close(self):
        self.assertTrue(can_close_ecn(self.manager, self.ecn))

    def test_staff_can_close(self):
        self.assertTrue(can_close_ecn(self.staff, self.ecn))

    def test_superuser_can_close(self):
        self.assertTrue(can_close_ecn(self.superuser, self.ecn))

    def test_ccb_cannot_close(self):
        # CCB non è Document Manager
        self.assertFalse(can_close_ecn(self.ccb, self.ecn))

    def test_author_cannot_close(self):
        self.assertFalse(can_close_ecn(self.author, self.ecn))

    def test_proposer_cannot_close(self):
        self.assertFalse(can_close_ecn(self.proposer, self.ecn))


# ===========================================================================
# Test servizi ECN — create
# ===========================================================================

class ECNServiceCreateTests(TestCase):

    def setUp(self):
        self.user   = _make_user('svc_create')
        self.folder = _make_folder(self.user, 'FOLD-SVC')
        self.document, self.version = _make_approved_document(self.user, self.folder, 'DOC-SVC-001')

    def test_create_returns_draft(self):
        ecn = create_change_notice(
            document=self.document,
            proposed_by=self.user,
            title='Test ECN',
            motivation=ChangeNotice.Motivation.IMPROVEMENT,
        )
        self.assertEqual(ecn.status, ChangeNotice.Status.DRAFT)

    def test_create_generates_code_if_not_provided(self):
        ecn = create_change_notice(
            document=self.document,
            proposed_by=self.user,
            title='Test ECN auto-code',
            motivation=ChangeNotice.Motivation.OTHER,
        )
        self.assertTrue(ecn.code.startswith('ECN-'))
        self.assertEqual(len(ecn.code), 8)  # ECN-NNNN

    def test_create_with_explicit_code(self):
        ecn = create_change_notice(
            document=self.document,
            proposed_by=self.user,
            title='Test code esplicito',
            motivation=ChangeNotice.Motivation.CUSTOMER,
            code='ECN-CUSTOM',
        )
        self.assertEqual(ecn.code, 'ECN-CUSTOM')

    def test_create_uses_document_current_version(self):
        ecn = create_change_notice(
            document=self.document,
            proposed_by=self.user,
            title='Test version snapshot',
            motivation=ChangeNotice.Motivation.REGULATORY,
        )
        self.assertEqual(ecn.document_version.pk, self.version.pk)

    def test_create_with_explicit_version(self):
        other_version = DocumentVersion.objects.create(
            document=self.document,
            revision_label='01',
            revision_number=1,
            status=DocumentVersion.Status.DRAFT,
            is_current=False,
            created_by=self.user,
        )
        ecn = create_change_notice(
            document=self.document,
            proposed_by=self.user,
            title='Test explicit version',
            motivation=ChangeNotice.Motivation.DESIGN,
            document_version=other_version,
        )
        self.assertEqual(ecn.document_version.pk, other_version.pk)

    def test_create_fails_if_document_has_no_current_version(self):
        doc_no_version = _make_document(self.user, self.folder, 'DOC-NO-VER')
        # doc_no_version.current_version = None (default)
        with self.assertRaises(ValidationError):
            create_change_notice(
                document=doc_no_version,
                proposed_by=self.user,
                title='Fallisce',
                motivation=ChangeNotice.Motivation.OTHER,
            )

    def test_create_created_by_defaults_to_proposed_by(self):
        ecn = create_change_notice(
            document=self.document,
            proposed_by=self.user,
            title='Test created_by',
            motivation=ChangeNotice.Motivation.IMPROVEMENT,
        )
        self.assertEqual(ecn.created_by.pk, self.user.pk)
        self.assertEqual(ecn.proposed_by.pk, self.user.pk)

    def test_create_with_project(self):
        from projects.models import Project
        project = Project.objects.create(
            code='PRJ-ECN-01',
            name='Progetto ECN test',
            status=Project.Status.ACTIVE,
            folder=self.folder,
            created_by=self.user,
        )
        ecn = create_change_notice(
            document=self.document,
            proposed_by=self.user,
            title='ECN con progetto',
            motivation=ChangeNotice.Motivation.IMPROVEMENT,
            project=project,
        )
        self.assertEqual(ecn.project.pk, project.pk)

    def test_create_writes_auditlog(self):
        AuditLog.objects.all().delete()
        ecn = create_change_notice(
            document=self.document,
            proposed_by=self.user,
            title='Test audit create',
            motivation=ChangeNotice.Motivation.OTHER,
        )
        log = AuditLog.objects.filter(action='ECN_CREATED').first()
        self.assertIsNotNone(log)
        self.assertEqual(log.user.pk, self.user.pk)
        self.assertEqual(log.changes['document_id'], self.document.pk)
        self.assertEqual(log.changes['metadata']['ecn_id'], ecn.pk)
        self.assertEqual(log.changes['metadata']['new_status'], ChangeNotice.Status.DRAFT)

    def test_auditlog_ecn_id_in_document_query(self):
        """ECN_CREATED deve essere trovato dalla query storico documento."""
        AuditLog.objects.all().delete()
        create_change_notice(
            document=self.document,
            proposed_by=self.user,
            title='Test document_id query',
            motivation=ChangeNotice.Motivation.IMPROVEMENT,
        )
        count = AuditLog.objects.filter(changes__document_id=self.document.pk).count()
        self.assertEqual(count, 1)


# ===========================================================================
# Test servizi ECN — workflow di stato
# ===========================================================================

class ECNServiceWorkflowTests(TestCase):

    def setUp(self):
        self.proposer = _make_user('wf_proposer')
        self.ccb      = _make_user_in_groups('wf_ccb', GROUP_CCB)
        self.manager  = _make_user_in_groups('wf_mgr', GROUP_MANAGERS)
        self.stranger = _make_user('wf_stranger')

        self.folder   = _make_folder(self.manager, 'FOLD-WF')
        self.document, self.version = _make_approved_document(
            self.manager, self.folder, 'DOC-WF-001'
        )

    def _create_draft(self, code='ECN-WF-DRAFT'):
        return create_change_notice(
            document=self.document,
            proposed_by=self.proposer,
            title='ECN workflow test',
            motivation=ChangeNotice.Motivation.IMPROVEMENT,
            code=code,
        )

    def _to_under_review(self, code='ECN-WF-UR'):
        ecn = self._create_draft(code)
        return submit_change_notice(ecn, self.proposer)

    def _to_approved(self, code='ECN-WF-APP'):
        ecn = self._to_under_review(code)
        return approve_change_notice(
            ecn, self.ccb,
            ccb_class=ChangeNotice.CCBClass.CLASS1,
            ccb_notes='Approvata dal CCB.',
        )

    # --- submit_change_notice -----------------------------------------------

    def test_submit_changes_status_to_under_review(self):
        ecn = self._create_draft()
        result = submit_change_notice(ecn, self.proposer)
        result.refresh_from_db()
        self.assertEqual(result.status, ChangeNotice.Status.UNDER_REVIEW)

    def test_submit_sets_submitted_at(self):
        ecn = self._create_draft('ECN-WF-SUBM')
        submit_change_notice(ecn, self.proposer)
        ecn.refresh_from_db()
        self.assertIsNotNone(ecn.submitted_at)

    def test_submit_fails_if_not_draft(self):
        ecn = self._to_under_review('ECN-WF-SUBM-FAIL')
        with self.assertRaises(ValidationError):
            submit_change_notice(ecn, self.proposer)

    def test_submit_raises_permission_denied_for_stranger(self):
        ecn = self._create_draft('ECN-WF-SUBM-PERM')
        with self.assertRaises(PermissionDenied):
            submit_change_notice(ecn, self.stranger)

    def test_submit_writes_auditlog(self):
        ecn = self._create_draft('ECN-WF-SUBM-LOG')
        AuditLog.objects.all().delete()
        submit_change_notice(ecn, self.proposer)
        log = AuditLog.objects.filter(action='ECN_SUBMITTED').first()
        self.assertIsNotNone(log)
        self.assertEqual(log.changes['old_values']['status'], ChangeNotice.Status.DRAFT)
        self.assertEqual(log.changes['new_values']['status'], ChangeNotice.Status.UNDER_REVIEW)
        self.assertEqual(log.changes['document_id'], self.document.pk)

    # --- approve_change_notice ----------------------------------------------

    def test_approve_changes_status_to_approved(self):
        ecn = self._to_under_review('ECN-WF-APR')
        approve_change_notice(ecn, self.ccb, ccb_class=ChangeNotice.CCBClass.CLASS2)
        ecn.refresh_from_db()
        self.assertEqual(ecn.status, ChangeNotice.Status.APPROVED)

    def test_approve_saves_ccb_fields(self):
        ecn = self._to_under_review('ECN-WF-APR-FIELDS')
        approve_change_notice(
            ecn, self.ccb,
            ccb_class=ChangeNotice.CCBClass.CLASS1,
            ccb_requirements='Conformi.',
            ccb_technical_impact='Minore.',
            ccb_cost_impact='Nessun costo.',
            ccb_time_impact='1 settimana.',
            ccb_quality_impact='Miglioramento.',
            ccb_other_impact='Nessuno.',
            ccb_notes='Approvato senza riserve.',
        )
        ecn.refresh_from_db()
        self.assertEqual(ecn.ccb_class, ChangeNotice.CCBClass.CLASS1)
        self.assertEqual(ecn.ccb_requirements, 'Conformi.')
        self.assertEqual(ecn.ccb_technical_impact, 'Minore.')
        self.assertEqual(ecn.ccb_cost_impact, 'Nessun costo.')
        self.assertEqual(ecn.ccb_time_impact, '1 settimana.')
        self.assertEqual(ecn.ccb_quality_impact, 'Miglioramento.')
        self.assertEqual(ecn.ccb_other_impact, 'Nessuno.')
        self.assertEqual(ecn.ccb_notes, 'Approvato senza riserve.')
        self.assertEqual(ecn.ccb_reviewed_by.pk, self.ccb.pk)
        self.assertIsNotNone(ecn.ccb_reviewed_at)

    def test_approve_fails_if_not_under_review(self):
        ecn = self._create_draft('ECN-WF-APR-FAIL')
        with self.assertRaises(ValidationError):
            approve_change_notice(ecn, self.ccb, ccb_class=ChangeNotice.CCBClass.CLASS1)

    def test_approve_fails_without_ccb_class(self):
        ecn = self._to_under_review('ECN-WF-APR-NO-CLASS')
        with self.assertRaises(ValidationError):
            approve_change_notice(ecn, self.ccb, ccb_class=None)

    def test_approve_fails_for_non_ccb_user(self):
        ecn = self._to_under_review('ECN-WF-APR-PERM')
        with self.assertRaises(PermissionDenied):
            approve_change_notice(ecn, self.stranger, ccb_class=ChangeNotice.CCBClass.CLASS1)

    def test_manager_can_approve(self):
        ecn = self._to_under_review('ECN-WF-APR-MGR')
        approve_change_notice(ecn, self.manager, ccb_class=ChangeNotice.CCBClass.CLASS2)
        ecn.refresh_from_db()
        self.assertEqual(ecn.status, ChangeNotice.Status.APPROVED)

    def test_approve_writes_auditlog(self):
        ecn = self._to_under_review('ECN-WF-APR-LOG')
        AuditLog.objects.all().delete()
        approve_change_notice(ecn, self.ccb, ccb_class=ChangeNotice.CCBClass.CLASS1)
        log = AuditLog.objects.filter(action='ECN_APPROVED').first()
        self.assertIsNotNone(log)
        self.assertEqual(log.changes['new_values']['status'], ChangeNotice.Status.APPROVED)
        self.assertEqual(log.changes['document_id'], self.document.pk)

    # --- reject_change_notice -----------------------------------------------

    def test_reject_changes_status_to_rejected(self):
        ecn = self._to_under_review('ECN-WF-REJ')
        reject_change_notice(ecn, self.ccb, reason='Non conforme ai requisiti.')
        ecn.refresh_from_db()
        self.assertEqual(ecn.status, ChangeNotice.Status.REJECTED)

    def test_reject_saves_reason_in_ccb_notes(self):
        ecn = self._to_under_review('ECN-WF-REJ-NOTES')
        reject_change_notice(ecn, self.ccb, reason='Impatto costi troppo elevato.')
        ecn.refresh_from_db()
        self.assertEqual(ecn.ccb_notes, 'Impatto costi troppo elevato.')
        self.assertEqual(ecn.ccb_reviewed_by.pk, self.ccb.pk)
        self.assertIsNotNone(ecn.ccb_reviewed_at)

    def test_reject_fails_if_not_under_review(self):
        ecn = self._create_draft('ECN-WF-REJ-STATE')
        with self.assertRaises(ValidationError):
            reject_change_notice(ecn, self.ccb, reason='Motivo.')

    def test_reject_fails_without_reason(self):
        ecn = self._to_under_review('ECN-WF-REJ-NO-REASON')
        with self.assertRaises(ValidationError):
            reject_change_notice(ecn, self.ccb, reason='')

    def test_reject_fails_for_non_ccb_user(self):
        ecn = self._to_under_review('ECN-WF-REJ-PERM')
        with self.assertRaises(PermissionDenied):
            reject_change_notice(ecn, self.stranger, reason='Motivo.')

    def test_reject_writes_auditlog(self):
        ecn = self._to_under_review('ECN-WF-REJ-LOG')
        AuditLog.objects.all().delete()
        reject_change_notice(ecn, self.ccb, reason='Analisi incompleta.')
        log = AuditLog.objects.filter(action='ECN_REJECTED').first()
        self.assertIsNotNone(log)
        self.assertEqual(log.changes['new_values']['status'], ChangeNotice.Status.REJECTED)
        self.assertEqual(log.changes['document_id'], self.document.pk)

    # --- close_change_notice ------------------------------------------------

    def test_close_changes_status_to_closed(self):
        ecn = self._to_approved('ECN-WF-CLOSE')
        close_change_notice(ecn, self.manager, close_notes='Variante eseguita e verificata.')
        ecn.refresh_from_db()
        self.assertEqual(ecn.status, ChangeNotice.Status.CLOSED)

    def test_close_saves_fields(self):
        ecn = self._to_approved('ECN-WF-CLOSE-FIELDS')
        close_change_notice(ecn, self.manager, close_notes='Tutto ok.')
        ecn.refresh_from_db()
        self.assertEqual(ecn.close_notes, 'Tutto ok.')
        self.assertEqual(ecn.closed_by.pk, self.manager.pk)
        self.assertIsNotNone(ecn.closed_at)

    def test_close_without_notes_is_allowed(self):
        ecn = self._to_approved('ECN-WF-CLOSE-NO-NOTES')
        close_change_notice(ecn, self.manager)
        ecn.refresh_from_db()
        self.assertEqual(ecn.status, ChangeNotice.Status.CLOSED)
        self.assertEqual(ecn.close_notes, '')

    def test_close_fails_if_not_approved(self):
        ecn = self._to_under_review('ECN-WF-CLOSE-STATE')
        with self.assertRaises(ValidationError):
            close_change_notice(ecn, self.manager)

    def test_close_fails_if_draft(self):
        ecn = self._create_draft('ECN-WF-CLOSE-DRAFT')
        with self.assertRaises(ValidationError):
            close_change_notice(ecn, self.manager)

    def test_close_fails_for_non_manager(self):
        ecn = self._to_approved('ECN-WF-CLOSE-PERM')
        with self.assertRaises(PermissionDenied):
            close_change_notice(ecn, self.stranger)

    def test_ccb_cannot_close(self):
        ecn = self._to_approved('ECN-WF-CLOSE-CCB')
        with self.assertRaises(PermissionDenied):
            close_change_notice(ecn, self.ccb)

    def test_close_writes_auditlog(self):
        ecn = self._to_approved('ECN-WF-CLOSE-LOG')
        AuditLog.objects.all().delete()
        close_change_notice(ecn, self.manager, close_notes='Chiuso.')
        log = AuditLog.objects.filter(action='ECN_CLOSED').first()
        self.assertIsNotNone(log)
        self.assertEqual(log.changes['new_values']['status'], ChangeNotice.Status.CLOSED)
        self.assertEqual(log.changes['document_id'], self.document.pk)

    # --- full workflow -------------------------------------------------------

    def test_full_workflow_draft_to_closed(self):
        """Percorso felice completo: DRAFT → UNDER_REVIEW → APPROVED → CLOSED."""
        ecn = create_change_notice(
            document=self.document,
            proposed_by=self.proposer,
            title='Full workflow ECN',
            motivation=ChangeNotice.Motivation.IMPROVEMENT,
            code='ECN-WF-FULL',
        )
        self.assertEqual(ecn.status, ChangeNotice.Status.DRAFT)

        submit_change_notice(ecn, self.proposer)
        ecn.refresh_from_db()
        self.assertEqual(ecn.status, ChangeNotice.Status.UNDER_REVIEW)

        approve_change_notice(ecn, self.ccb, ccb_class=ChangeNotice.CCBClass.CLASS1)
        ecn.refresh_from_db()
        self.assertEqual(ecn.status, ChangeNotice.Status.APPROVED)

        close_change_notice(ecn, self.manager, close_notes='Variante eseguita.')
        ecn.refresh_from_db()
        self.assertEqual(ecn.status, ChangeNotice.Status.CLOSED)

    def test_full_workflow_with_rejection(self):
        """Percorso rifiuto: DRAFT → UNDER_REVIEW → REJECTED."""
        ecn = create_change_notice(
            document=self.document,
            proposed_by=self.proposer,
            title='ECN rifiutato',
            motivation=ChangeNotice.Motivation.CUSTOMER,
            code='ECN-WF-REJ-FULL',
        )
        submit_change_notice(ecn, self.proposer)
        reject_change_notice(ecn, self.ccb, reason='Analisi insufficiente.')
        ecn.refresh_from_db()
        self.assertEqual(ecn.status, ChangeNotice.Status.REJECTED)
        # Terminale: non si può più approvare
        with self.assertRaises(ValidationError):
            approve_change_notice(ecn, self.ccb, ccb_class=ChangeNotice.CCBClass.CLASS1)

    # --- auditlog per-documento ---------------------------------------------

    def test_all_transitions_appear_in_document_audit_query(self):
        """Tutti gli eventi ECN hanno document_id → appaiono nello storico documento."""
        AuditLog.objects.all().delete()
        ecn = create_change_notice(
            document=self.document,
            proposed_by=self.proposer,
            title='ECN audit query test',
            motivation=ChangeNotice.Motivation.IMPROVEMENT,
            code='ECN-WF-AUD-ALL',
        )
        submit_change_notice(ecn, self.proposer)
        approve_change_notice(ecn, self.ccb, ccb_class=ChangeNotice.CCBClass.CLASS2)
        close_change_notice(ecn, self.manager)

        logs = AuditLog.objects.filter(changes__document_id=self.document.pk)
        actions = {log.action for log in logs}
        self.assertIn('ECN_CREATED', actions)
        self.assertIn('ECN_SUBMITTED', actions)
        self.assertIn('ECN_APPROVED', actions)
        self.assertIn('ECN_CLOSED', actions)


# ---------------------------------------------------------------------------
# Test view ECN (ECN-B)
# ---------------------------------------------------------------------------

class ECNViewTests(TestCase):
    """Test per le view ECN: accesso, redirect, status code."""

    def setUp(self):
        # Utenti
        self.proposer = _make_user('view_proposer')
        self.manager  = _make_user('view_manager')
        self.ccb      = _make_user('view_ccb')
        self.stranger = _make_user('view_stranger')

        grp_mgr = Group.objects.get_or_create(name='Document Managers')[0]
        grp_ccb = Group.objects.get_or_create(name='Change Control Board')[0]
        self.manager.groups.add(grp_mgr)
        self.ccb.groups.add(grp_ccb)

        # Documento + versione approvata
        self.folder   = _make_folder(self.manager, code='FOLD-VIEW')
        self.document = _make_document(self.manager, self.folder, code='DOC-VIEW-001')
        self.version  = _make_version(self.document, self.manager)
        self.document.current_version = self.version
        self.document.save(update_fields=['current_version'])

        # ECN in bozza
        self.ecn = _make_ecn(
            self.document, self.version, self.proposer,
            code='ECN-VIEW-001',
        )

    # --- ecn_list -----------------------------------------------------------

    def test_ecn_list_requires_login(self):
        r = self.client.get('/ecn/')
        self.assertEqual(r.status_code, 302)
        self.assertIn('/accounts/login/', r['Location'])

    def test_ecn_list_proposer_sees_own_ecn(self):
        self.client.force_login(self.proposer)
        r = self.client.get('/ecn/')
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'ECN-VIEW-001')

    def test_ecn_list_manager_sees_all(self):
        self.client.force_login(self.manager)
        r = self.client.get('/ecn/')
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'ECN-VIEW-001')

    def test_ecn_list_stranger_sees_nothing(self):
        self.client.force_login(self.stranger)
        r = self.client.get('/ecn/')
        self.assertEqual(r.status_code, 200)
        self.assertNotContains(r, 'ECN-VIEW-001')

    def test_ecn_list_status_filter(self):
        self.client.force_login(self.manager)
        r = self.client.get('/ecn/?status=draft')
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'ECN-VIEW-001')

    def test_ecn_list_status_filter_excludes_other_states(self):
        self.client.force_login(self.manager)
        r = self.client.get('/ecn/?status=closed')
        self.assertEqual(r.status_code, 200)
        self.assertNotContains(r, 'ECN-VIEW-001')

    # --- ecn_detail ---------------------------------------------------------

    def test_ecn_detail_requires_login(self):
        r = self.client.get(f'/ecn/{self.ecn.pk}/')
        self.assertEqual(r.status_code, 302)

    def test_ecn_detail_proposer_can_view(self):
        self.client.force_login(self.proposer)
        r = self.client.get(f'/ecn/{self.ecn.pk}/')
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'ECN-VIEW-001')

    def test_ecn_detail_stranger_gets_404(self):
        self.client.force_login(self.stranger)
        r = self.client.get(f'/ecn/{self.ecn.pk}/')
        self.assertEqual(r.status_code, 404)

    def test_ecn_detail_shows_submit_button_to_proposer(self):
        self.client.force_login(self.proposer)
        r = self.client.get(f'/ecn/{self.ecn.pk}/')
        self.assertContains(r, 'Invia alla CCB')

    def test_ecn_detail_hides_review_button_in_draft(self):
        self.client.force_login(self.ccb)
        r = self.client.get(f'/ecn/{self.ecn.pk}/')
        self.assertNotContains(r, 'Revisione CCB')

    def test_ecn_detail_shows_review_button_under_review_to_ccb(self):
        self.ecn.status = ChangeNotice.Status.UNDER_REVIEW
        self.ecn.save(update_fields=['status'])
        self.client.force_login(self.ccb)
        r = self.client.get(f'/ecn/{self.ecn.pk}/')
        self.assertContains(r, 'Revisione CCB')

    # --- ecn_create ---------------------------------------------------------

    def test_ecn_create_requires_login(self):
        r = self.client.get(f'/ecn/new/?document={self.document.pk}')
        self.assertEqual(r.status_code, 302)

    def test_ecn_create_without_document_param_gives_404(self):
        self.client.force_login(self.manager)
        r = self.client.get('/ecn/new/')
        self.assertEqual(r.status_code, 404)

    def test_ecn_create_manager_sees_form(self):
        self.client.force_login(self.manager)
        r = self.client.get(f'/ecn/new/?document={self.document.pk}')
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'Nuova richiesta ECN')

    def test_ecn_create_stranger_forbidden(self):
        self.client.force_login(self.stranger)
        r = self.client.get(f'/ecn/new/?document={self.document.pk}')
        self.assertEqual(r.status_code, 403)

    def test_ecn_create_post_creates_ecn_and_redirects(self):
        self.client.force_login(self.manager)
        r = self.client.post(f'/ecn/new/?document={self.document.pk}', {
            'document': self.document.pk,
            'title': 'Variante UI test',
            'motivation': ChangeNotice.Motivation.CUSTOMER,
            'motivation_detail': '',
            'description': '',
            'commessa': '',
        })
        self.assertEqual(r.status_code, 302)
        new_ecn = ChangeNotice.objects.filter(title='Variante UI test').first()
        self.assertIsNotNone(new_ecn)
        self.assertEqual(new_ecn.status, ChangeNotice.Status.DRAFT)
        self.assertIn(f'/ecn/{new_ecn.pk}/', r['Location'])

    # --- ecn_submit ---------------------------------------------------------

    def test_ecn_submit_redirects_on_get(self):
        self.client.force_login(self.proposer)
        r = self.client.get(f'/ecn/{self.ecn.pk}/submit/')
        self.assertRedirects(r, f'/ecn/{self.ecn.pk}/', fetch_redirect_response=False)

    def test_ecn_submit_post_transitions_to_under_review(self):
        self.client.force_login(self.proposer)
        r = self.client.post(f'/ecn/{self.ecn.pk}/submit/')
        self.assertRedirects(r, f'/ecn/{self.ecn.pk}/', fetch_redirect_response=False)
        self.ecn.refresh_from_db()
        self.assertEqual(self.ecn.status, ChangeNotice.Status.UNDER_REVIEW)

    def test_ecn_submit_stranger_raises_error_message(self):
        self.client.force_login(self.stranger)
        self.client.post(f'/ecn/{self.ecn.pk}/submit/')
        self.ecn.refresh_from_db()
        # Lo stranger non ha il permesso → stato rimane DRAFT
        self.assertEqual(self.ecn.status, ChangeNotice.Status.DRAFT)

    # --- ecn_review ---------------------------------------------------------

    def _put_ecn_under_review(self):
        self.ecn.status = ChangeNotice.Status.UNDER_REVIEW
        self.ecn.save(update_fields=['status'])

    def test_ecn_review_requires_login(self):
        self._put_ecn_under_review()
        r = self.client.get(f'/ecn/{self.ecn.pk}/review/')
        self.assertEqual(r.status_code, 302)

    def test_ecn_review_stranger_forbidden(self):
        self._put_ecn_under_review()
        self.client.force_login(self.stranger)
        r = self.client.get(f'/ecn/{self.ecn.pk}/review/')
        self.assertEqual(r.status_code, 403)

    def test_ecn_review_ccb_sees_form(self):
        self._put_ecn_under_review()
        self.client.force_login(self.ccb)
        r = self.client.get(f'/ecn/{self.ecn.pk}/review/')
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'Revisione CCB')

    def test_ecn_review_approve_post_approves_ecn(self):
        self._put_ecn_under_review()
        self.client.force_login(self.ccb)
        r = self.client.post(f'/ecn/{self.ecn.pk}/review/', {
            'action': 'approve',
            'ccb_class': ChangeNotice.CCBClass.CLASS1,
            'ccb_requirements': '',
            'ccb_technical_impact': '',
            'ccb_cost_impact': '',
            'ccb_time_impact': '',
            'ccb_quality_impact': '',
            'ccb_other_impact': '',
            'ccb_notes': '',
        })
        self.assertRedirects(r, f'/ecn/{self.ecn.pk}/', fetch_redirect_response=False)
        self.ecn.refresh_from_db()
        self.assertEqual(self.ecn.status, ChangeNotice.Status.APPROVED)

    def test_ecn_review_reject_post_rejects_ecn(self):
        self._put_ecn_under_review()
        self.client.force_login(self.ccb)
        r = self.client.post(f'/ecn/{self.ecn.pk}/review/', {
            'action': 'reject',
            'ccb_class': '',
            'ccb_requirements': '',
            'ccb_technical_impact': '',
            'ccb_cost_impact': '',
            'ccb_time_impact': '',
            'ccb_quality_impact': '',
            'ccb_other_impact': '',
            'ccb_notes': 'Proposta non sufficientemente dettagliata.',
        })
        self.assertRedirects(r, f'/ecn/{self.ecn.pk}/', fetch_redirect_response=False)
        self.ecn.refresh_from_db()
        self.assertEqual(self.ecn.status, ChangeNotice.Status.REJECTED)

    def test_ecn_review_approve_without_ccb_class_shows_error(self):
        self._put_ecn_under_review()
        self.client.force_login(self.ccb)
        r = self.client.post(f'/ecn/{self.ecn.pk}/review/', {
            'action': 'approve',
            'ccb_class': '',  # mancante
            'ccb_notes': '',
        })
        self.assertEqual(r.status_code, 200)
        self.ecn.refresh_from_db()
        self.assertEqual(self.ecn.status, ChangeNotice.Status.UNDER_REVIEW)

    def test_ecn_review_reject_without_notes_shows_error(self):
        self._put_ecn_under_review()
        self.client.force_login(self.ccb)
        r = self.client.post(f'/ecn/{self.ecn.pk}/review/', {
            'action': 'reject',
            'ccb_class': '',
            'ccb_notes': '',  # mancante
        })
        self.assertEqual(r.status_code, 200)
        self.ecn.refresh_from_db()
        self.assertEqual(self.ecn.status, ChangeNotice.Status.UNDER_REVIEW)

    # --- ecn_close ----------------------------------------------------------

    def _put_ecn_approved(self):
        self.ecn.status = ChangeNotice.Status.APPROVED
        self.ecn.ccb_class = ChangeNotice.CCBClass.CLASS1
        self.ecn.save(update_fields=['status', 'ccb_class'])

    def test_ecn_close_requires_login(self):
        self._put_ecn_approved()
        r = self.client.get(f'/ecn/{self.ecn.pk}/close/')
        self.assertEqual(r.status_code, 302)

    def test_ecn_close_stranger_forbidden(self):
        self._put_ecn_approved()
        self.client.force_login(self.stranger)
        r = self.client.get(f'/ecn/{self.ecn.pk}/close/')
        self.assertEqual(r.status_code, 403)

    def test_ecn_close_manager_sees_form_with_warning(self):
        self._put_ecn_approved()
        self.client.force_login(self.manager)
        r = self.client.get(f'/ecn/{self.ecn.pk}/close/')
        self.assertEqual(r.status_code, 200)
        # warning visibile perché non c'è executed_version
        self.assertContains(r, 'Attenzione')

    def test_ecn_close_post_closes_ecn(self):
        self._put_ecn_approved()
        self.client.force_login(self.manager)
        r = self.client.post(f'/ecn/{self.ecn.pk}/close/', {
            'close_notes': 'Variante eseguita e verificata.',
        })
        self.assertRedirects(r, f'/ecn/{self.ecn.pk}/', fetch_redirect_response=False)
        self.ecn.refresh_from_db()
        self.assertEqual(self.ecn.status, ChangeNotice.Status.CLOSED)
        self.assertEqual(self.ecn.close_notes, 'Variante eseguita e verificata.')

    # --- document_detail integration ----------------------------------------

    def test_document_detail_shows_ecn_section(self):
        self.client.force_login(self.manager)
        r = self.client.get(f'/documents/{self.document.pk}/')
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'ECN / Varianti collegate')
        self.assertContains(r, 'ECN-VIEW-001')

    def test_document_detail_shows_create_ecn_button_to_manager(self):
        self.client.force_login(self.manager)
        r = self.client.get(f'/documents/{self.document.pk}/')
        self.assertContains(r, 'Richiedi variante / ECN')

    def test_document_detail_hides_create_ecn_button_to_stranger(self):
        self.client.force_login(self.stranger)
        # Lo stranger non può vedere il documento se non c'è folder access,
        # ma il documento non ha restrizioni di visibilità globali se è active
        # Verifichiamo solo che il bottone non appaia
        r = self.client.get(f'/documents/{self.document.pk}/')
        # stranger non vede il documento → 404
        self.assertEqual(r.status_code, 404)
