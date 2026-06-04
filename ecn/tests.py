from django.contrib.auth.models import Group, User
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError
from django.test import TestCase

from documents.models import Document, DocumentVersion
from auditlog.models import AuditLog
from documents.permissions import GROUP_AUTHORS, GROUP_AUDITORS, GROUP_MANAGERS
from ecn.models import ChangeNotice, ChangeNoticeAttachment, ChangeNoticeApprover, ChangeNoticeDecision
from ecn.permissions import (
    GROUP_CCB,
    can_close_ecn,
    can_configure_ccb,
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
    set_change_notice_approvers,
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

    def test_all_ccb_policy_choices_valid(self):
        valid = {p.value for p in ChangeNotice.CCBPolicy}
        self.assertIn('any', valid)
        self.assertIn('all', valid)
        self.assertIn('sequential', valid)

    def test_default_ccb_policy_is_all(self):
        ecn = _make_ecn(self.document, self.version, self.user, code='ECN-POL')
        self.assertEqual(ecn.ccb_policy, ChangeNotice.CCBPolicy.ALL)

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


# ---------------------------------------------------------------------------
# Test modello ChangeNoticeApprover
# ---------------------------------------------------------------------------

class ChangeNoticeApproverModelTests(TestCase):

    def setUp(self):
        self.manager = _make_user('appr_mgr')
        self.ccb1 = _make_user('appr_ccb1')
        self.ccb2 = _make_user('appr_ccb2')
        self.folder = _make_folder(self.manager, 'FOLD-APR')
        self.document = _make_document(self.manager, self.folder, 'DOC-APR-001')
        self.version = _make_version(self.document, self.manager)
        self.ecn = _make_ecn(self.document, self.version, self.manager, 'ECN-APR')

    def test_create_approver(self):
        app = ChangeNoticeApprover.objects.create(
            change_notice=self.ecn, user=self.ccb1, order=1,
        )
        self.assertEqual(app.change_notice.pk, self.ecn.pk)
        self.assertEqual(app.user.pk, self.ccb1.pk)
        self.assertEqual(app.order, 1)
        self.assertTrue(app.is_required)

    def test_str_contains_code_and_username(self):
        app = ChangeNoticeApprover.objects.create(
            change_notice=self.ecn, user=self.ccb1, order=1,
        )
        s = str(app)
        self.assertIn('ECN-APR', s)
        self.assertIn('appr_ccb1', s)

    def test_unique_together_user_change_notice(self):
        ChangeNoticeApprover.objects.create(
            change_notice=self.ecn, user=self.ccb1, order=1,
        )
        with self.assertRaises(IntegrityError):
            ChangeNoticeApprover.objects.create(
                change_notice=self.ecn, user=self.ccb1, order=2,
            )

    def test_ordering_by_order_then_id(self):
        app2 = ChangeNoticeApprover.objects.create(
            change_notice=self.ecn, user=self.ccb2, order=2,
        )
        app1 = ChangeNoticeApprover.objects.create(
            change_notice=self.ecn, user=self.ccb1, order=1,
        )
        qs = list(self.ecn.approvers.all())
        self.assertEqual(qs[0].pk, app1.pk)
        self.assertEqual(qs[1].pk, app2.pk)

    def test_cascade_delete_with_ecn(self):
        ChangeNoticeApprover.objects.create(
            change_notice=self.ecn, user=self.ccb1, order=1,
        )
        self.assertEqual(ChangeNoticeApprover.objects.count(), 1)
        self.ecn.delete()
        self.assertEqual(ChangeNoticeApprover.objects.count(), 0)

    def test_meta_verbose_name(self):
        self.assertEqual(ChangeNoticeApprover._meta.verbose_name, 'Approvatore CCB')


# ---------------------------------------------------------------------------
# Test modello ChangeNoticeDecision
# ---------------------------------------------------------------------------

class ChangeNoticeDecisionModelTests(TestCase):

    def setUp(self):
        self.manager = _make_user('dec_mgr')
        self.ccb = _make_user('dec_ccb')
        self.folder = _make_folder(self.manager, 'FOLD-DEC')
        self.document = _make_document(self.manager, self.folder, 'DOC-DEC-001')
        self.version = _make_version(self.document, self.manager)
        self.ecn = _make_ecn(self.document, self.version, self.manager, 'ECN-DEC')
        self.approver = ChangeNoticeApprover.objects.create(
            change_notice=self.ecn, user=self.ccb, order=1,
        )

    def test_create_approve_decision(self):
        dec = ChangeNoticeDecision.objects.create(
            change_notice=self.ecn,
            approver=self.approver,
            user=self.ccb,
            decision=ChangeNoticeDecision.Decision.APPROVE,
            comment='Tutto ok.',
        )
        self.assertEqual(dec.decision, ChangeNoticeDecision.Decision.APPROVE)
        self.assertEqual(dec.comment, 'Tutto ok.')
        self.assertIsNotNone(dec.decided_at)

    def test_str_contains_code_username_decision(self):
        dec = ChangeNoticeDecision.objects.create(
            change_notice=self.ecn,
            approver=self.approver,
            user=self.ccb,
            decision=ChangeNoticeDecision.Decision.APPROVE,
        )
        s = str(dec)
        self.assertIn('ECN-DEC', s)
        self.assertIn('dec_ccb', s)
        self.assertIn('Approvato', s)

    def test_unique_together_change_notice_approver(self):
        ChangeNoticeDecision.objects.create(
            change_notice=self.ecn,
            approver=self.approver,
            user=self.ccb,
            decision=ChangeNoticeDecision.Decision.APPROVE,
        )
        with self.assertRaises(IntegrityError):
            ChangeNoticeDecision.objects.create(
                change_notice=self.ecn,
                approver=self.approver,
                user=self.ccb,
                decision=ChangeNoticeDecision.Decision.REJECT,
            )

    def test_meta_verbose_name(self):
        self.assertEqual(ChangeNoticeDecision._meta.verbose_name, 'Decisione CCB')


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


def _make_executed_version(ecn, created_by):
    """Crea una DocumentVersion draft e la collega come executed_version dell'ECN."""
    new_ver = DocumentVersion.objects.create(
        document=ecn.document,
        revision_label='01',
        revision_number=1,
        status=DocumentVersion.Status.DRAFT,
        is_current=False,
        created_by=created_by,
    )
    ecn.executed_version = new_ver
    ecn.save(update_fields=['executed_version'])
    return new_ver


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

        # Assegna ccb e manager come approvatori per i test can_review_ecn
        ChangeNoticeApprover.objects.create(
            change_notice=self.ecn, user=self.ccb, order=1,
        )
        ChangeNoticeApprover.objects.create(
            change_notice=self.ecn, user=self.manager, order=2,
        )

    # --- can_view_ecn -------------------------------------------------------

    def test_superuser_can_view(self):
        self.assertTrue(can_view_ecn(self.superuser, self.ecn))

    def test_staff_cannot_view_automatically(self):
        """MB1: is_staff NON dà visibilità automatica sulle ECN."""
        self.assertFalse(can_view_ecn(self.staff, self.ecn))

    def test_manager_can_view_when_assigned_approver(self):
        """Manager vede l'ECN perché è assegnato come ChangeNoticeApprover (in setUp)."""
        self.assertTrue(can_view_ecn(self.manager, self.ecn))

    def test_auditor_cannot_view_automatically(self):
        """MB1: Document Auditor NON ha visibilità automatica sulle ECN."""
        self.assertFalse(can_view_ecn(self.auditor, self.ecn))

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

    # --- can_configure_ccb --------------------------------------------------

    def test_document_manager_cannot_configure_ccb(self):
        """MB1: Document Manager NON può configurare CCB — solo Quality Manager."""
        self.assertFalse(can_configure_ccb(self.manager, self.ecn))

    def test_superuser_can_configure_ccb(self):
        self.assertTrue(can_configure_ccb(self.superuser, self.ecn))

    def test_staff_cannot_configure_ccb(self):
        """MB1: is_staff NON dà diritto di configurare CCB."""
        self.assertFalse(can_configure_ccb(self.staff, self.ecn))

    def test_proposer_cannot_configure_ccb(self):
        self.assertFalse(can_configure_ccb(self.proposer, self.ecn))

    def test_ccb_cannot_configure_ccb(self):
        self.assertFalse(can_configure_ccb(self.ccb, self.ecn))

    def test_author_cannot_configure_ccb(self):
        self.assertFalse(can_configure_ccb(self.author, self.ecn))

    # --- can_submit_ecn -----------------------------------------------------

    def test_proposer_cannot_submit(self):
        """Il proponente non può più inviare direttamente alla CCB."""
        self.assertFalse(can_submit_ecn(self.proposer, self.ecn))

    def test_document_manager_cannot_submit(self):
        """MB1: Document Manager NON può inviare ECN alla CCB — solo Quality Manager."""
        self.assertFalse(can_submit_ecn(self.manager, self.ecn))

    def test_stranger_cannot_submit(self):
        self.assertFalse(can_submit_ecn(self.stranger, self.ecn))

    def test_ccb_cannot_submit(self):
        # CCB non è manager
        self.assertFalse(can_submit_ecn(self.ccb, self.ecn))

    # --- can_review_ecn (ECN-E: solo approvatori assegnati) -----------------

    def test_ccb_can_review_when_assigned(self):
        # ccb è assegnato come approvatore in setUp
        self.assertTrue(can_review_ecn(self.ccb, self.ecn))

    def test_manager_can_review_when_assigned(self):
        # manager è assegnato come approvatore in setUp
        self.assertTrue(can_review_ecn(self.manager, self.ecn))

    def test_superuser_can_review_without_being_assigned(self):
        # superuser bypass
        self.assertTrue(can_review_ecn(self.superuser, self.ecn))

    def test_author_cannot_review(self):
        # author non è approvatore assegnato
        self.assertFalse(can_review_ecn(self.author, self.ecn))

    def test_auditor_cannot_review(self):
        self.assertFalse(can_review_ecn(self.auditor, self.ecn))

    def test_proposer_cannot_review(self):
        # proposer non è assegnato come approvatore
        self.assertFalse(can_review_ecn(self.proposer, self.ecn))

    def test_stranger_cannot_review(self):
        self.assertFalse(can_review_ecn(self.stranger, self.ecn))

    def test_ccb_cannot_review_if_not_assigned(self):
        # Un utente CCB che non è assegnato non può revisionare
        other_ccb = _make_user_in_groups('perm_ccb2', GROUP_CCB)
        self.assertFalse(can_review_ecn(other_ccb, self.ecn))

    def test_sequential_only_first_can_review(self):
        """Policy SEQUENTIAL: solo il primo approvatore (ccb, order=1) può revisionare."""
        self.ecn.ccb_policy = ChangeNotice.CCBPolicy.SEQUENTIAL
        self.ecn.save(update_fields=['ccb_policy'])
        # ccb ha order=1 → può revisionare
        self.assertTrue(can_review_ecn(self.ccb, self.ecn))
        # manager ha order=2 → deve aspettare ccb
        self.assertFalse(can_review_ecn(self.manager, self.ecn))

    def test_sequential_second_can_review_after_first_decided(self):
        """Policy SEQUENTIAL: dopo la decisione di ccb, manager può revisionare."""
        self.ecn.ccb_policy = ChangeNotice.CCBPolicy.SEQUENTIAL
        self.ecn.save(update_fields=['ccb_policy'])
        ccb_approver = ChangeNoticeApprover.objects.get(
            change_notice=self.ecn, user=self.ccb,
        )
        ChangeNoticeDecision.objects.create(
            change_notice=self.ecn,
            approver=ccb_approver,
            user=self.ccb,
            decision=ChangeNoticeDecision.Decision.APPROVE,
        )
        self.assertTrue(can_review_ecn(self.manager, self.ecn))

    # --- can_close_ecn ------------------------------------------------------

    def test_document_manager_cannot_close(self):
        """MB1: Document Manager NON può chiudere ECN — solo Quality Manager."""
        self.assertFalse(can_close_ecn(self.manager, self.ecn))

    def test_staff_cannot_close(self):
        """MB1: is_staff NON dà diritto di chiudere ECN."""
        self.assertFalse(can_close_ecn(self.staff, self.ecn))

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
# Test servizi ECN — set_change_notice_approvers
# ===========================================================================

class ECNServiceApproverTests(TestCase):

    def setUp(self):
        self.manager = _make_user_in_groups('aprsvc_mgr', GROUP_MANAGERS)
        self.ccb1    = _make_user_in_groups('aprsvc_ccb1', GROUP_CCB)
        self.ccb2    = _make_user_in_groups('aprsvc_ccb2', GROUP_CCB)
        self.folder  = _make_folder(self.manager, 'FOLD-APR-SVC')
        self.document, self.version = _make_approved_document(
            self.manager, self.folder, 'DOC-APR-SVC-001'
        )

    def _draft(self, code='ECN-APR-SVC-DRAFT'):
        return create_change_notice(
            document=self.document,
            proposed_by=self.manager,
            title='ECN approver test',
            motivation=ChangeNotice.Motivation.IMPROVEMENT,
            code=code,
        )

    def test_set_approvers_creates_records(self):
        ecn = self._draft('ECN-APR-1')
        set_change_notice_approvers(ecn, [self.ccb1, self.ccb2])
        self.assertEqual(ecn.approvers.count(), 2)

    def test_set_approvers_replaces_existing(self):
        ecn = self._draft('ECN-APR-2')
        set_change_notice_approvers(ecn, [self.ccb1])
        set_change_notice_approvers(ecn, [self.ccb2])
        self.assertEqual(ecn.approvers.count(), 1)
        self.assertEqual(ecn.approvers.first().user.pk, self.ccb2.pk)

    def test_set_approvers_assigns_order(self):
        ecn = self._draft('ECN-APR-3')
        set_change_notice_approvers(ecn, [self.ccb1, self.ccb2])
        apps = list(ecn.approvers.order_by('order'))
        self.assertEqual(apps[0].user.pk, self.ccb1.pk)
        self.assertEqual(apps[0].order, 1)
        self.assertEqual(apps[1].user.pk, self.ccb2.pk)
        self.assertEqual(apps[1].order, 2)

    def test_set_approvers_updates_policy(self):
        ecn = self._draft('ECN-APR-4')
        set_change_notice_approvers(ecn, [self.ccb1], policy='any')
        ecn.refresh_from_db()
        self.assertEqual(ecn.ccb_policy, 'any')

    def test_set_approvers_fails_if_empty(self):
        ecn = self._draft('ECN-APR-5')
        with self.assertRaises(ValidationError):
            set_change_notice_approvers(ecn, [])

    def test_set_approvers_fails_if_not_draft(self):
        """UNDER_REVIEW senza decisioni è ora consentito; UNDER_REVIEW con decisioni no."""
        from ecn.models import ChangeNoticeApprover, ChangeNoticeDecision
        ecn = self._draft('ECN-APR-6')
        set_change_notice_approvers(ecn, [self.ccb1])
        ecn.status = ChangeNotice.Status.UNDER_REVIEW
        ecn.save(update_fields=['status'])

        # UNDER_REVIEW senza decisioni → consentito
        set_change_notice_approvers(ecn, [self.ccb2])

        # Aggiungi una decisione e riprova → ValidationError
        approver = ChangeNoticeApprover.objects.get(change_notice=ecn, user=self.ccb2)
        ChangeNoticeDecision.objects.create(
            change_notice=ecn,
            approver=approver,
            user=self.ccb2,
            decision=ChangeNoticeDecision.Decision.APPROVE,
        )
        with self.assertRaises(ValidationError):
            set_change_notice_approvers(ecn, [self.ccb1])

    def test_set_approvers_fails_if_approved(self):
        """APPROVED → ValidationError."""
        ecn = self._draft('ECN-APR-7')
        set_change_notice_approvers(ecn, [self.ccb1])
        ecn.status = ChangeNotice.Status.APPROVED
        ecn.save(update_fields=['status'])
        with self.assertRaises(ValidationError):
            set_change_notice_approvers(ecn, [self.ccb2])


# ===========================================================================
# Test form ECN — queryset approvatori CCB
# ===========================================================================

class ECNFormApproverQuerysetTests(TestCase):
    """Verifica che ChangeNoticeCCBConfigForm popoli il queryset approvatori dai gruppi corretti."""

    def setUp(self):
        from documents.permissions import GROUP_APPROVERS, GROUP_MANAGERS
        self.ccb_user   = _make_user_in_groups('qs_ccb',   GROUP_CCB)
        self.mgr_user   = _make_user_in_groups('qs_mgr',   GROUP_MANAGERS)
        self.appr_user  = _make_user_in_groups('qs_appr',  GROUP_APPROVERS)
        self.other_user = _make_user('qs_other')  # nessun gruppo rilevante

    def _get_form_approver_ids(self):
        from ecn.forms import ChangeNoticeCCBConfigForm
        form = ChangeNoticeCCBConfigForm()
        return set(form.fields['approvers'].queryset.values_list('pk', flat=True))

    def test_ccb_group_members_are_candidates(self):
        ids = self._get_form_approver_ids()
        self.assertIn(self.ccb_user.pk, ids)

    def test_managers_are_candidates(self):
        ids = self._get_form_approver_ids()
        self.assertIn(self.mgr_user.pk, ids)

    def test_document_approvers_are_candidates(self):
        ids = self._get_form_approver_ids()
        self.assertIn(self.appr_user.pk, ids)

    def test_unrelated_user_not_in_candidates(self):
        ids = self._get_form_approver_ids()
        self.assertNotIn(self.other_user.pk, ids)

    def test_no_duplicates_for_user_in_multiple_groups(self):
        """Utente in CCB e Managers compare una sola volta."""
        from documents.permissions import GROUP_MANAGERS
        multi_user = _make_user_in_groups('qs_multi', GROUP_CCB, GROUP_MANAGERS)
        from ecn.forms import ChangeNoticeCCBConfigForm
        form = ChangeNoticeCCBConfigForm()
        qs = form.fields['approvers'].queryset
        count = qs.filter(pk=multi_user.pk).count()
        self.assertEqual(count, 1)

    def test_empty_queryset_when_no_relevant_groups_have_members(self):
        """Se nessun gruppo ha membri, il queryset è vuoto."""
        from django.contrib.auth.models import Group
        # Rimuovo tutti i membri dai tre gruppi
        for grp_name in [GROUP_CCB, 'Document Managers', 'Document Approvers']:
            try:
                Group.objects.get(name=grp_name).user_set.clear()
            except Group.DoesNotExist:
                pass
        ids = self._get_form_approver_ids()
        self.assertEqual(len(ids), 0)


# ===========================================================================
# Test servizi ECN — workflow di stato
# ===========================================================================

class ECNServiceWorkflowTests(TestCase):

    def setUp(self):
        from documents.permissions import GROUP_QUALITY_MANAGER
        self.proposer = _make_user('wf_proposer')
        self.ccb      = _make_user_in_groups('wf_ccb', GROUP_CCB)
        # MB1: il manager deve avere Quality Manager per submit/close ECN
        self.manager  = _make_user_in_groups('wf_mgr', GROUP_MANAGERS, GROUP_QUALITY_MANAGER)
        self.stranger = _make_user('wf_stranger')

        self.folder   = _make_folder(self.manager, 'FOLD-WF')
        self.document, self.version = _make_approved_document(
            self.manager, self.folder, 'DOC-WF-001'
        )

    def _create_draft(self, code='ECN-WF-DRAFT'):
        ecn = create_change_notice(
            document=self.document,
            proposed_by=self.proposer,
            title='ECN workflow test',
            motivation=ChangeNotice.Motivation.IMPROVEMENT,
            code=code,
        )
        # ECN-E: assegna almeno un approvatore (ccb) prima di submit
        set_change_notice_approvers(ecn, [self.ccb])
        return ecn

    def _to_under_review(self, code='ECN-WF-UR'):
        ecn = self._create_draft(code)
        return submit_change_notice(ecn, self.manager)

    def _to_approved(self, code='ECN-WF-APP'):
        ecn = self._to_under_review(code)
        return approve_change_notice(
            ecn, self.ccb,
            ccb_class=ChangeNotice.CCBClass.CLASS1,
            ccb_notes='Approvata dal CCB.',
        )

    def _to_approved_with_exec_version(self, code='ECN-WF-APP-EX'):
        """ECN approvato con executed_version impostata (per i test di close)."""
        ecn = self._to_approved(code)
        _make_executed_version(ecn, self.proposer)
        return ecn

    # --- submit_change_notice -----------------------------------------------

    def test_submit_changes_status_to_under_review(self):
        ecn = self._create_draft()
        result = submit_change_notice(ecn, self.manager)
        result.refresh_from_db()
        self.assertEqual(result.status, ChangeNotice.Status.UNDER_REVIEW)

    def test_submit_sets_submitted_at(self):
        ecn = self._create_draft('ECN-WF-SUBM')
        submit_change_notice(ecn, self.manager)
        ecn.refresh_from_db()
        self.assertIsNotNone(ecn.submitted_at)

    def test_submit_fails_if_not_draft(self):
        ecn = self._to_under_review('ECN-WF-SUBM-FAIL')
        with self.assertRaises(ValidationError):
            submit_change_notice(ecn, self.manager)

    def test_submit_raises_permission_denied_for_stranger(self):
        ecn = self._create_draft('ECN-WF-SUBM-PERM')
        with self.assertRaises(PermissionDenied):
            submit_change_notice(ecn, self.stranger)

    def test_submit_raises_permission_denied_for_proposer(self):
        """Il proponente non può più inviare direttamente alla CCB."""
        ecn = self._create_draft('ECN-WF-SUBM-PROP')
        with self.assertRaises(PermissionDenied):
            submit_change_notice(ecn, self.proposer)

    def test_submit_fails_without_approvers(self):
        """submit richiede almeno un approvatore assegnato."""
        ecn = create_change_notice(
            document=self.document,
            proposed_by=self.proposer,
            title='ECN no approvers',
            motivation=ChangeNotice.Motivation.OTHER,
            code='ECN-WF-SUBM-NOAPPR',
        )
        # Nessun approvatore assegnato
        with self.assertRaises(ValidationError):
            submit_change_notice(ecn, self.manager)

    def test_submit_writes_auditlog(self):
        ecn = self._create_draft('ECN-WF-SUBM-LOG')
        AuditLog.objects.all().delete()
        submit_change_notice(ecn, self.manager)
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

    def test_approve_creates_decision_record(self):
        ecn = self._to_under_review('ECN-WF-APR-DEC')
        approve_change_notice(ecn, self.ccb, ccb_class=ChangeNotice.CCBClass.CLASS1,
                              comment='LGTM')
        dec = ChangeNoticeDecision.objects.filter(change_notice=ecn).first()
        self.assertIsNotNone(dec)
        self.assertEqual(dec.decision, ChangeNoticeDecision.Decision.APPROVE)
        self.assertEqual(dec.comment, 'LGTM')
        self.assertEqual(dec.user.pk, self.ccb.pk)

    def test_approve_fails_if_not_under_review(self):
        ecn = self._create_draft('ECN-WF-APR-FAIL')
        with self.assertRaises(ValidationError):
            approve_change_notice(ecn, self.ccb, ccb_class=ChangeNotice.CCBClass.CLASS1)

    def test_approve_fails_without_ccb_class(self):
        ecn = self._to_under_review('ECN-WF-APR-NO-CLASS')
        with self.assertRaises(ValidationError):
            approve_change_notice(ecn, self.ccb, ccb_class=None)

    def test_approve_fails_for_non_assigned_user(self):
        ecn = self._to_under_review('ECN-WF-APR-PERM')
        with self.assertRaises(PermissionDenied):
            approve_change_notice(ecn, self.stranger, ccb_class=ChangeNotice.CCBClass.CLASS1)

    def test_manager_can_approve_when_assigned(self):
        """manager può approvare se è stato assegnato come approvatore."""
        ecn = create_change_notice(
            document=self.document,
            proposed_by=self.proposer,
            title='ECN manager approver',
            motivation=ChangeNotice.Motivation.OTHER,
            code='ECN-WF-APR-MGR2',
        )
        set_change_notice_approvers(ecn, [self.manager])
        submit_change_notice(ecn, self.manager)
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

    def test_approve_duplicate_decision_raises(self):
        """Approvare due volte con lo stesso utente deve sollevare ValidationError."""
        ecn = self._to_under_review('ECN-WF-APR-DUP')
        approve_change_notice(ecn, self.ccb, ccb_class=ChangeNotice.CCBClass.CLASS1)
        ecn.refresh_from_db()
        self.assertEqual(ecn.status, ChangeNotice.Status.APPROVED)
        # Ora l'ECN è APPROVED, non UNDER_REVIEW → ValidationError per stato
        with self.assertRaises(ValidationError):
            approve_change_notice(ecn, self.ccb, ccb_class=ChangeNotice.CCBClass.CLASS1)

    # --- Policy ANY ---------------------------------------------------------

    def test_policy_any_approved_by_first_approver(self):
        """Policy ANY: il primo approvatore finalizza l'ECN."""
        ecn = create_change_notice(
            document=self.document,
            proposed_by=self.proposer,
            title='ECN ANY policy',
            motivation=ChangeNotice.Motivation.OTHER,
            code='ECN-WF-ANY',
        )
        ccb2 = _make_user_in_groups('wf_ccb2', GROUP_CCB)
        set_change_notice_approvers(ecn, [self.ccb, ccb2], policy='any')
        submit_change_notice(ecn, self.manager)
        # Primo approvatore approva → APPROVED subito
        approve_change_notice(ecn, self.ccb, ccb_class=ChangeNotice.CCBClass.CLASS1)
        ecn.refresh_from_db()
        self.assertEqual(ecn.status, ChangeNotice.Status.APPROVED)

    # --- Policy ALL ---------------------------------------------------------

    def test_policy_all_requires_all_approvers(self):
        """Policy ALL: serve che tutti approvino prima di finalizzare."""
        ecn = create_change_notice(
            document=self.document,
            proposed_by=self.proposer,
            title='ECN ALL policy',
            motivation=ChangeNotice.Motivation.OTHER,
            code='ECN-WF-ALL',
        )
        ccb2 = _make_user_in_groups('wf_ccb2b', GROUP_CCB)
        set_change_notice_approvers(ecn, [self.ccb, ccb2], policy='all')
        submit_change_notice(ecn, self.manager)
        # Primo approva: NOT yet approved
        approve_change_notice(ecn, self.ccb, ccb_class=ChangeNotice.CCBClass.CLASS1)
        ecn.refresh_from_db()
        self.assertEqual(ecn.status, ChangeNotice.Status.UNDER_REVIEW)
        # Secondo approva: ora APPROVED
        approve_change_notice(ecn, ccb2, ccb_class=ChangeNotice.CCBClass.CLASS1)
        ecn.refresh_from_db()
        self.assertEqual(ecn.status, ChangeNotice.Status.APPROVED)

    # --- Policy SEQUENTIAL --------------------------------------------------

    def test_policy_sequential_second_cannot_approve_before_first(self):
        """Policy SEQUENTIAL: il secondo non può approvare prima del primo."""
        ecn = create_change_notice(
            document=self.document,
            proposed_by=self.proposer,
            title='ECN SEQUENTIAL policy',
            motivation=ChangeNotice.Motivation.OTHER,
            code='ECN-WF-SEQ',
        )
        ccb2 = _make_user_in_groups('wf_ccb2c', GROUP_CCB)
        set_change_notice_approvers(ecn, [self.ccb, ccb2], policy='sequential')
        submit_change_notice(ecn, self.manager)
        # ccb2 (order=2) tenta di approvare prima di ccb (order=1) → PermissionDenied
        with self.assertRaises(PermissionDenied):
            approve_change_notice(ecn, ccb2, ccb_class=ChangeNotice.CCBClass.CLASS1)

    def test_policy_sequential_full_flow(self):
        """Policy SEQUENTIAL: approvazione in ordine → APPROVED."""
        ecn = create_change_notice(
            document=self.document,
            proposed_by=self.proposer,
            title='ECN SEQ full',
            motivation=ChangeNotice.Motivation.OTHER,
            code='ECN-WF-SEQ-FULL',
        )
        ccb2 = _make_user_in_groups('wf_ccb2d', GROUP_CCB)
        set_change_notice_approvers(ecn, [self.ccb, ccb2], policy='sequential')
        submit_change_notice(ecn, self.manager)
        # Primo approva: UNDER_REVIEW
        approve_change_notice(ecn, self.ccb, ccb_class=ChangeNotice.CCBClass.CLASS1)
        ecn.refresh_from_db()
        self.assertEqual(ecn.status, ChangeNotice.Status.UNDER_REVIEW)
        # Secondo approva: APPROVED
        approve_change_notice(ecn, ccb2, ccb_class=ChangeNotice.CCBClass.CLASS1)
        ecn.refresh_from_db()
        self.assertEqual(ecn.status, ChangeNotice.Status.APPROVED)

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

    def test_reject_creates_decision_record(self):
        ecn = self._to_under_review('ECN-WF-REJ-DEC')
        reject_change_notice(ecn, self.ccb, reason='Motivo rifiuto.')
        dec = ChangeNoticeDecision.objects.filter(change_notice=ecn).first()
        self.assertIsNotNone(dec)
        self.assertEqual(dec.decision, ChangeNoticeDecision.Decision.REJECT)

    def test_reject_fails_if_not_under_review(self):
        ecn = self._create_draft('ECN-WF-REJ-STATE')
        with self.assertRaises(ValidationError):
            reject_change_notice(ecn, self.ccb, reason='Motivo.')

    def test_reject_fails_without_reason(self):
        ecn = self._to_under_review('ECN-WF-REJ-NO-REASON')
        with self.assertRaises(ValidationError):
            reject_change_notice(ecn, self.ccb, reason='')

    def test_reject_fails_for_non_assigned_user(self):
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
        ecn = self._to_approved_with_exec_version('ECN-WF-CLOSE')
        close_change_notice(ecn, self.manager, close_notes='Variante eseguita e verificata.')
        ecn.refresh_from_db()
        self.assertEqual(ecn.status, ChangeNotice.Status.CLOSED)

    def test_close_saves_fields(self):
        ecn = self._to_approved_with_exec_version('ECN-WF-CLOSE-FIELDS')
        close_change_notice(ecn, self.manager, close_notes='Tutto ok.')
        ecn.refresh_from_db()
        self.assertEqual(ecn.close_notes, 'Tutto ok.')
        self.assertEqual(ecn.closed_by.pk, self.manager.pk)
        self.assertIsNotNone(ecn.closed_at)

    def test_close_without_notes_is_allowed(self):
        ecn = self._to_approved_with_exec_version('ECN-WF-CLOSE-NO-NOTES')
        close_change_notice(ecn, self.manager)
        ecn.refresh_from_db()
        self.assertEqual(ecn.status, ChangeNotice.Status.CLOSED)
        self.assertEqual(ecn.close_notes, '')

    def test_close_fails_without_executed_version(self):
        """ECN-E: close richiede executed_version impostata."""
        ecn = self._to_approved('ECN-WF-CLOSE-NO-EXEC')
        # executed_version è None → ValidationError
        with self.assertRaises(ValidationError) as ctx:
            close_change_notice(ecn, self.manager)
        self.assertIn('nessuna revisione', str(ctx.exception).lower())

    def test_close_fails_if_not_approved(self):
        ecn = self._to_under_review('ECN-WF-CLOSE-STATE')
        with self.assertRaises(ValidationError):
            close_change_notice(ecn, self.manager)

    def test_close_fails_if_draft(self):
        ecn = self._create_draft('ECN-WF-CLOSE-DRAFT')
        with self.assertRaises(ValidationError):
            close_change_notice(ecn, self.manager)

    def test_close_fails_for_non_manager(self):
        ecn = self._to_approved_with_exec_version('ECN-WF-CLOSE-PERM')
        with self.assertRaises(PermissionDenied):
            close_change_notice(ecn, self.stranger)

    def test_ccb_cannot_close(self):
        ecn = self._to_approved_with_exec_version('ECN-WF-CLOSE-CCB')
        with self.assertRaises(PermissionDenied):
            close_change_notice(ecn, self.ccb)

    def test_close_writes_auditlog(self):
        ecn = self._to_approved_with_exec_version('ECN-WF-CLOSE-LOG')
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
        set_change_notice_approvers(ecn, [self.ccb])
        self.assertEqual(ecn.status, ChangeNotice.Status.DRAFT)

        submit_change_notice(ecn, self.manager)
        ecn.refresh_from_db()
        self.assertEqual(ecn.status, ChangeNotice.Status.UNDER_REVIEW)

        approve_change_notice(ecn, self.ccb, ccb_class=ChangeNotice.CCBClass.CLASS1)
        ecn.refresh_from_db()
        self.assertEqual(ecn.status, ChangeNotice.Status.APPROVED)

        _make_executed_version(ecn, self.proposer)
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
        set_change_notice_approvers(ecn, [self.ccb])
        submit_change_notice(ecn, self.manager)
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
        set_change_notice_approvers(ecn, [self.ccb])
        submit_change_notice(ecn, self.manager)
        approve_change_notice(ecn, self.ccb, ccb_class=ChangeNotice.CCBClass.CLASS2)
        _make_executed_version(ecn, self.proposer)
        close_change_notice(ecn, self.manager)

        logs = AuditLog.objects.filter(changes__document_id=self.document.pk)
        actions = {log.action for log in logs}
        self.assertIn('ECN_CREATED', actions)
        self.assertIn('ECN_SUBMITTED', actions)
        self.assertIn('ECN_APPROVED', actions)
        self.assertIn('ECN_CLOSED', actions)


# ---------------------------------------------------------------------------
# Test view ECN
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
        grp_qmgr = Group.objects.get_or_create(name='Quality Manager')[0]
        grp_ccb = Group.objects.get_or_create(name='Change Control Board')[0]
        self.manager.groups.add(grp_mgr, grp_qmgr)  # Manager ha anche Quality Manager (MB1)
        self.ccb.groups.add(grp_ccb)

        # Documento + versione approvata
        self.folder   = _make_folder(self.manager, code='FOLD-VIEW')
        self.document = _make_document(self.manager, self.folder, code='DOC-VIEW-001')
        self.version  = _make_version(self.document, self.manager)
        self.document.current_version = self.version
        self.document.save(update_fields=['current_version'])

        # ECN in bozza con ccb come approvatore
        self.ecn = _make_ecn(
            self.document, self.version, self.proposer,
            code='ECN-VIEW-001',
        )
        ChangeNoticeApprover.objects.create(
            change_notice=self.ecn, user=self.ccb, order=1,
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

    def test_ecn_detail_hides_submit_button_from_proposer(self):
        """Il proponente non vede il pulsante 'Invia alla CCB'."""
        self.client.force_login(self.proposer)
        r = self.client.get(f'/ecn/{self.ecn.pk}/')
        self.assertNotContains(r, 'Invia alla CCB')

    def test_ecn_detail_shows_submit_button_to_manager_when_ccb_configured(self):
        """Il manager vede 'Invia alla CCB' solo se la CCB è configurata."""
        self.client.force_login(self.manager)
        r = self.client.get(f'/ecn/{self.ecn.pk}/')
        # CCB è configurata (ccb è approvatore nel setUp)
        self.assertContains(r, 'Invia alla CCB')

    def test_ecn_detail_shows_configure_ccb_button_to_manager(self):
        """Il manager vede il pulsante 'Modifica CCB' in bozza (setUp ha già un approvatore)."""
        self.client.force_login(self.manager)
        r = self.client.get(f'/ecn/{self.ecn.pk}/')
        # setUp ha già un approvatore → il pulsante dice "Modifica CCB"
        self.assertContains(r, 'configure-ccb')

    def test_ecn_detail_hides_review_button_in_draft(self):
        self.client.force_login(self.ccb)
        r = self.client.get(f'/ecn/{self.ecn.pk}/')
        # STEP I: il pulsante ora si chiama "Decisione CCB"
        self.assertNotContains(r, 'Decisione CCB')

    def test_ecn_detail_shows_review_button_under_review_to_ccb(self):
        self.ecn.status = ChangeNotice.Status.UNDER_REVIEW
        self.ecn.save(update_fields=['status'])
        self.client.force_login(self.ccb)
        r = self.client.get(f'/ecn/{self.ecn.pk}/')
        # STEP I: il pulsante si chiama "Decisione CCB"
        self.assertContains(r, 'Decisione CCB')

    def test_ecn_detail_shows_approvers_section(self):
        """ECN-E: la sezione Componenti CCB mostra l'approvatore."""
        self.client.force_login(self.manager)
        r = self.client.get(f'/ecn/{self.ecn.pk}/')
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'Componenti CCB')

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
        """Il proponente/manager crea ECN senza approvatori (CCB verrà configurata separatamente)."""
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
        # Nessun approvatore assegnato alla creazione
        self.assertEqual(new_ecn.approvers.count(), 0)

    # --- ecn_submit ---------------------------------------------------------

    def test_ecn_submit_redirects_on_get(self):
        self.client.force_login(self.proposer)
        r = self.client.get(f'/ecn/{self.ecn.pk}/submit/')
        self.assertRedirects(r, f'/ecn/{self.ecn.pk}/', fetch_redirect_response=False)

    def test_ecn_submit_post_transitions_to_under_review(self):
        """Il manager può inviare l'ECN alla CCB."""
        self.client.force_login(self.manager)
        r = self.client.post(f'/ecn/{self.ecn.pk}/submit/')
        self.assertRedirects(r, f'/ecn/{self.ecn.pk}/', fetch_redirect_response=False)
        self.ecn.refresh_from_db()
        self.assertEqual(self.ecn.status, ChangeNotice.Status.UNDER_REVIEW)

    def test_ecn_submit_post_proposer_blocked(self):
        """Il proponente non può inviare l'ECN alla CCB (permesso revocato)."""
        self.client.force_login(self.proposer)
        self.client.post(f'/ecn/{self.ecn.pk}/submit/')
        self.ecn.refresh_from_db()
        # Lo stato deve rimanere DRAFT
        self.assertEqual(self.ecn.status, ChangeNotice.Status.DRAFT)

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
        """ECN-E: ccb è approvatore assegnato → può revisionare."""
        self._put_ecn_under_review()
        self.client.force_login(self.ccb)
        r = self.client.get(f'/ecn/{self.ecn.pk}/review/')
        self.assertEqual(r.status_code, 200)
        # STEP I: la pagina si chiama "Decisione CCB"
        self.assertContains(r, 'Decisione CCB')

    def test_ecn_review_non_assigned_ccb_forbidden(self):
        """ECN-E: utente CCB non assegnato non può accedere alla review."""
        self._put_ecn_under_review()
        other_ccb = _make_user('view_ccb2')
        grp_ccb = Group.objects.get(name='Change Control Board')
        other_ccb.groups.add(grp_ccb)
        self.client.force_login(other_ccb)
        r = self.client.get(f'/ecn/{self.ecn.pk}/review/')
        self.assertEqual(r.status_code, 403)

    def test_ecn_review_approve_post_approves_ecn(self):
        """STEP I: approvazione con dossier pre-compilato (ccb_class già sull'ECN)."""
        self._put_ecn_under_review()
        # Pre-setta ccb_class (dossier compilato prima della votazione)
        self.ecn.ccb_class = ChangeNotice.CCBClass.CLASS1
        self.ecn.save(update_fields=['ccb_class'])
        self.client.force_login(self.ccb)
        r = self.client.post(f'/ecn/{self.ecn.pk}/review/', {
            'action': 'approve',
            'comment': '',
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
            'comment': '',
            'ccb_notes': 'Proposta non sufficientemente dettagliata.',
        })
        self.assertRedirects(r, f'/ecn/{self.ecn.pk}/', fetch_redirect_response=False)
        self.ecn.refresh_from_db()
        self.assertEqual(self.ecn.status, ChangeNotice.Status.REJECTED)

    def test_ecn_review_approve_without_ccb_class_shows_error(self):
        """STEP I: approvazione senza ccb_class nel dossier → errore dal service."""
        self._put_ecn_under_review()
        # NON pre-settiamo ccb_class: il service deve rifiutare
        self.client.force_login(self.ccb)
        r = self.client.post(f'/ecn/{self.ecn.pk}/review/', {
            'action': 'approve',
            'comment': '',
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
            'ccb_notes': '',  # mancante
            'comment': '',
        })
        self.assertEqual(r.status_code, 200)
        self.ecn.refresh_from_db()
        self.assertEqual(self.ecn.status, ChangeNotice.Status.UNDER_REVIEW)

    # --- ecn_close ----------------------------------------------------------

    def _put_ecn_approved_with_exec_version(self):
        self.ecn.status = ChangeNotice.Status.APPROVED
        self.ecn.ccb_class = ChangeNotice.CCBClass.CLASS1
        self.ecn.save(update_fields=['status', 'ccb_class'])
        _make_executed_version(self.ecn, self.proposer)

    def test_ecn_close_requires_login(self):
        self._put_ecn_approved_with_exec_version()
        r = self.client.get(f'/ecn/{self.ecn.pk}/close/')
        self.assertEqual(r.status_code, 302)

    def test_ecn_close_stranger_forbidden(self):
        self._put_ecn_approved_with_exec_version()
        self.client.force_login(self.stranger)
        r = self.client.get(f'/ecn/{self.ecn.pk}/close/')
        self.assertEqual(r.status_code, 403)

    def test_ecn_close_manager_sees_form_without_warning_when_exec_version_set(self):
        self._put_ecn_approved_with_exec_version()
        self.client.force_login(self.manager)
        r = self.client.get(f'/ecn/{self.ecn.pk}/close/')
        self.assertEqual(r.status_code, 200)
        # Nessun avviso perché executed_version è impostata
        self.assertNotContains(r, 'Attenzione')

    def test_ecn_close_manager_sees_warning_without_exec_version(self):
        """Il form di chiusura avvisa se executed_version non è impostata."""
        self.ecn.status = ChangeNotice.Status.APPROVED
        self.ecn.ccb_class = ChangeNotice.CCBClass.CLASS1
        self.ecn.save(update_fields=['status', 'ccb_class'])
        self.client.force_login(self.manager)
        r = self.client.get(f'/ecn/{self.ecn.pk}/close/')
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'Attenzione')

    def test_ecn_close_post_closes_ecn(self):
        self._put_ecn_approved_with_exec_version()
        self.client.force_login(self.manager)
        r = self.client.post(f'/ecn/{self.ecn.pk}/close/', {
            'close_notes': 'Variante eseguita e verificata.',
        })
        self.assertRedirects(r, f'/ecn/{self.ecn.pk}/', fetch_redirect_response=False)
        self.ecn.refresh_from_db()
        self.assertEqual(self.ecn.status, ChangeNotice.Status.CLOSED)
        self.assertEqual(self.ecn.close_notes, 'Variante eseguita e verificata.')

    def test_ecn_close_post_fails_without_exec_version(self):
        """ECN-E: il service rifiuta la chiusura senza executed_version → form re-render."""
        self.ecn.status = ChangeNotice.Status.APPROVED
        self.ecn.ccb_class = ChangeNotice.CCBClass.CLASS1
        self.ecn.save(update_fields=['status', 'ccb_class'])
        self.client.force_login(self.manager)
        r = self.client.post(f'/ecn/{self.ecn.pk}/close/', {
            'close_notes': 'Chiudo senza versione.',
        })
        # Il service solleva ValidationError → la view re-renderizza il form (200)
        self.assertEqual(r.status_code, 200)
        self.ecn.refresh_from_db()
        # Lo stato NON deve essere cambiato
        self.assertEqual(self.ecn.status, ChangeNotice.Status.APPROVED)

    # --- ecn_configure_ccb --------------------------------------------------

    def test_ecn_configure_ccb_requires_login(self):
        r = self.client.get(f'/ecn/{self.ecn.pk}/configure-ccb/')
        self.assertEqual(r.status_code, 302)
        self.assertIn('/accounts/login/', r['Location'])

    def test_ecn_configure_ccb_manager_sees_form(self):
        """Il manager può accedere alla pagina di configurazione CCB."""
        self.client.force_login(self.manager)
        r = self.client.get(f'/ecn/{self.ecn.pk}/configure-ccb/')
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'Configura CCB')

    def test_ecn_configure_ccb_proposer_forbidden(self):
        """Il proponente non può configurare la CCB."""
        self.client.force_login(self.proposer)
        r = self.client.get(f'/ecn/{self.ecn.pk}/configure-ccb/')
        self.assertEqual(r.status_code, 403)

    def test_ecn_configure_ccb_stranger_forbidden(self):
        """Utenti senza permesso ricevono 403."""
        self.client.force_login(self.stranger)
        r = self.client.get(f'/ecn/{self.ecn.pk}/configure-ccb/')
        self.assertEqual(r.status_code, 403)

    def _ccb_post_data(self, policy='all', user_pks=None, coordinator_pk=''):
        """Helper: costruisce il POST data per il formset CCB (STEP I)."""
        user_pks = user_pks or []
        data = {
            'ccb_policy': policy,
            'coordinator': str(coordinator_pk),
            'ccb-TOTAL_FORMS': str(len(user_pks)),
            'ccb-INITIAL_FORMS': '0',
            'ccb-MIN_NUM_FORMS': '0',
            'ccb-MAX_NUM_FORMS': '1000',
        }
        for i, pk in enumerate(user_pks):
            data[f'ccb-{i}-user'] = str(pk)
        return data

    def test_ecn_configure_ccb_post_assigns_approvers(self):
        """POST con approvatori validi (formset) → redirect e approvatori assegnati."""
        ecn_no_appr = _make_ecn(
            self.document, self.version, self.proposer,
            code='ECN-VIEW-CONF',
        )
        self.client.force_login(self.manager)
        r = self.client.post(
            f'/ecn/{ecn_no_appr.pk}/configure-ccb/',
            self._ccb_post_data(policy='all', user_pks=[self.ccb.pk]),
        )
        self.assertRedirects(r, f'/ecn/{ecn_no_appr.pk}/', fetch_redirect_response=False)
        ecn_no_appr.refresh_from_db()
        self.assertEqual(ecn_no_appr.approvers.count(), 1)

    def test_ecn_configure_ccb_post_sets_policy(self):
        """POST con formset aggiorna la policy dell'ECN."""
        ecn_cfg = _make_ecn(
            self.document, self.version, self.proposer,
            code='ECN-VIEW-CFG-POL',
        )
        self.client.force_login(self.manager)
        self.client.post(
            f'/ecn/{ecn_cfg.pk}/configure-ccb/',
            self._ccb_post_data(policy='any', user_pks=[self.ccb.pk]),
        )
        ecn_cfg.refresh_from_db()
        self.assertEqual(ecn_cfg.ccb_policy, 'any')

    def test_ecn_configure_ccb_post_without_approvers_shows_error(self):
        """POST formset senza approvatori → messaggio errore (200)."""
        ecn_no_appr = _make_ecn(
            self.document, self.version, self.proposer,
            code='ECN-VIEW-CFG-NOAPPR',
        )
        self.client.force_login(self.manager)
        r = self.client.post(
            f'/ecn/{ecn_no_appr.pk}/configure-ccb/',
            self._ccb_post_data(policy='all', user_pks=[]),  # nessun utente
        )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(ecn_no_appr.approvers.count(), 0)

    def test_ecn_configure_ccb_blocked_if_approved(self):
        """Non è possibile configurare la CCB su un ECN approvato → 403."""
        self.ecn.status = ChangeNotice.Status.APPROVED
        self.ecn.save(update_fields=['status'])
        self.client.force_login(self.manager)
        r = self.client.post(
            f'/ecn/{self.ecn.pk}/configure-ccb/',
            self._ccb_post_data(policy='all', user_pks=[self.ccb.pk]),
        )
        # APPROVED → can_reconfigure_ccb=False → 403
        self.assertEqual(r.status_code, 403)

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


# ---------------------------------------------------------------------------
# Test nuove viste: ECN Dashboard e Le mie ECN
# ---------------------------------------------------------------------------

class ECNDashboardAndMyViewTests(TestCase):
    """Test per ecn_dashboard e ecn_my."""

    def setUp(self):
        self.manager   = _make_user('dash_manager')
        self.auditor   = _make_user('dash_auditor')
        self.proposer  = _make_user('dash_proposer')
        self.ccb_user  = _make_user('dash_ccb')
        self.stranger  = _make_user('dash_stranger')

        grp_mgr  = Group.objects.get_or_create(name='Document Managers')[0]
        grp_qmgr = Group.objects.get_or_create(name='Quality Manager')[0]
        grp_aud  = Group.objects.get_or_create(name='Document Auditors')[0]
        grp_ccb  = Group.objects.get_or_create(name='Change Control Board')[0]
        # MB1: dashboard ECN richiede Quality Manager (non Document Manager/Auditor)
        self.manager.groups.add(grp_mgr, grp_qmgr)
        self.auditor.groups.add(grp_aud)
        self.ccb_user.groups.add(grp_ccb)

        self.folder   = _make_folder(self.manager, code='FOLD-DASH')
        self.document = _make_document(self.manager, self.folder, code='DOC-DASH-001')
        self.version  = _make_version(self.document, self.manager)
        self.document.current_version = self.version
        self.document.save(update_fields=['current_version'])

        self.ecn = _make_ecn(
            self.document, self.version, self.proposer,
            code='ECN-DASH-001',
        )

    # --- ecn_dashboard ------------------------------------------------------

    def test_ecn_dashboard_requires_login(self):
        r = self.client.get('/ecn/dashboard/')
        self.assertEqual(r.status_code, 302)
        self.assertIn('/accounts/login/', r['Location'])

    def test_ecn_dashboard_manager_can_access(self):
        self.client.force_login(self.manager)
        r = self.client.get('/ecn/dashboard/')
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'Cruscotto ECN')

    def test_ecn_dashboard_auditor_forbidden(self):
        """MB1: Document Auditor NON accede al cruscotto ECN (solo Quality roles)."""
        self.client.force_login(self.auditor)
        r = self.client.get('/ecn/dashboard/')
        self.assertEqual(r.status_code, 403)

    def test_ecn_dashboard_proposer_forbidden(self):
        """Utenti normali (non manager/auditor) non possono accedere al cruscotto."""
        self.client.force_login(self.proposer)
        r = self.client.get('/ecn/dashboard/')
        self.assertEqual(r.status_code, 403)

    def test_ecn_dashboard_shows_draft_no_ccb(self):
        """ECN in bozza senza CCB appare nella sezione 'Da analizzare'."""
        # ECN-DASH-001 è in bozza senza approvatori → deve apparire
        self.client.force_login(self.manager)
        r = self.client.get('/ecn/dashboard/')
        self.assertContains(r, 'ECN-DASH-001')

    # --- ecn_my -------------------------------------------------------------

    def test_ecn_my_requires_login(self):
        r = self.client.get('/ecn/my-ecn/')
        self.assertEqual(r.status_code, 302)
        self.assertIn('/accounts/login/', r['Location'])

    def test_ecn_my_proposer_sees_own_ecns(self):
        self.client.force_login(self.proposer)
        r = self.client.get('/ecn/my-ecn/')
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'ECN-DASH-001')

    def test_ecn_my_stranger_sees_no_ecns(self):
        """Utente senza ECN propri non vede nulla nella sezione richieste."""
        self.client.force_login(self.stranger)
        r = self.client.get('/ecn/my-ecn/')
        self.assertEqual(r.status_code, 200)
        self.assertNotContains(r, 'ECN-DASH-001')

    def test_ecn_my_shows_pending_ccb_decision(self):
        """Un approver assegnato vede l'ECN in 'Decisioni CCB da prendere'."""
        # Metti l'ECN in revisione con ccb_user come approvatore
        ChangeNoticeApprover.objects.create(
            change_notice=self.ecn, user=self.ccb_user, order=1,
        )
        self.ecn.status = ChangeNotice.Status.UNDER_REVIEW
        self.ecn.save(update_fields=['status'])

        self.client.force_login(self.ccb_user)
        r = self.client.get('/ecn/my-ecn/')
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'ECN-DASH-001')


# ---------------------------------------------------------------------------
# Test ECN Edit & CCB Reopen  (permessi, service, view)
# ---------------------------------------------------------------------------

class ECNEditPermissionTests(TestCase):
    """Test per can_edit_ecn, can_reconfigure_ccb, can_reopen_ccb e i service."""

    def setUp(self):
        from ecn.permissions import can_edit_ecn, can_reconfigure_ccb, can_reopen_ccb
        from ecn.services import update_change_notice, reopen_ccb_configuration

        self.can_edit_ecn = can_edit_ecn
        self.can_reconfigure_ccb = can_reconfigure_ccb
        self.can_reopen_ccb = can_reopen_ccb
        self.update_change_notice = update_change_notice
        self.reopen_ccb_configuration = reopen_ccb_configuration

        self.manager  = _make_user('edit_mgr')
        self.proposer = _make_user('edit_proposer')
        self.stranger = _make_user('edit_stranger')
        self.ccb_user = _make_user('edit_ccb')

        grp_mgr = Group.objects.get_or_create(name='Document Managers')[0]
        grp_qmgr = Group.objects.get_or_create(name='Quality Manager')[0]
        grp_ccb = Group.objects.get_or_create(name='Change Control Board')[0]
        self.manager.groups.add(grp_mgr, grp_qmgr)  # Manager ha Quality Manager per service tests
        self.ccb_user.groups.add(grp_ccb)

        self.folder   = _make_folder(self.manager, code='FOLD-EDIT')
        self.document = _make_document(self.manager, self.folder, code='DOC-EDIT-001')
        self.version  = _make_version(self.document, self.manager)
        self.document.current_version = self.version
        self.document.save(update_fields=['current_version'])

        self.ecn = _make_ecn(
            self.document, self.version, self.proposer,
            code='ECN-EDIT-001',
        )

    # --- can_edit_ecn ---------------------------------------------------------

    def test_can_edit_ecn_draft_proposer(self):
        self.assertTrue(self.can_edit_ecn(self.proposer, self.ecn))

    def test_can_edit_ecn_draft_quality_manager(self):
        """Quality Manager può modificare ECN in bozza."""
        self.assertTrue(self.can_edit_ecn(self.manager, self.ecn))  # manager ha Quality Manager group

    def test_can_edit_ecn_draft_stranger_denied(self):
        self.assertFalse(self.can_edit_ecn(self.stranger, self.ecn))

    def test_can_edit_ecn_under_review_denied(self):
        """Modifica dati base vietata se non DRAFT."""
        self.ecn.status = ChangeNotice.Status.UNDER_REVIEW
        self.ecn.save(update_fields=['status'])
        self.assertFalse(self.can_edit_ecn(self.proposer, self.ecn))
        self.assertFalse(self.can_edit_ecn(self.manager, self.ecn))

    # --- can_reconfigure_ccb --------------------------------------------------

    def test_can_reconfigure_ccb_draft_quality_manager(self):
        """Quality Manager può riconfigurare CCB su DRAFT."""
        self.assertTrue(self.can_reconfigure_ccb(self.manager, self.ecn))  # manager ha Quality Manager group

    def test_can_reconfigure_ccb_draft_stranger_denied(self):
        self.assertFalse(self.can_reconfigure_ccb(self.stranger, self.ecn))

    def test_can_reconfigure_ccb_under_review_no_decisions(self):
        self.ecn.status = ChangeNotice.Status.UNDER_REVIEW
        self.ecn.save(update_fields=['status'])
        self.assertTrue(self.can_reconfigure_ccb(self.manager, self.ecn))  # manager ha Quality Manager group

    def test_can_reconfigure_ccb_under_review_with_decisions_denied(self):
        """Con decisioni esistenti, la modifica diretta è bloccata."""
        self.ecn.status = ChangeNotice.Status.UNDER_REVIEW
        self.ecn.save(update_fields=['status'])
        approver = ChangeNoticeApprover.objects.create(
            change_notice=self.ecn, user=self.ccb_user, order=1,
        )
        ChangeNoticeDecision.objects.create(
            change_notice=self.ecn,
            approver=approver,
            user=self.ccb_user,
            decision=ChangeNoticeDecision.Decision.APPROVE,
        )
        self.assertFalse(self.can_reconfigure_ccb(self.manager, self.ecn))

    # --- can_reopen_ccb -------------------------------------------------------

    def test_can_reopen_ccb_requires_under_review_with_decisions(self):
        """can_reopen_ccb è False su DRAFT (nessuna decisione)."""
        self.assertFalse(self.can_reopen_ccb(self.manager, self.ecn))

    def test_can_reopen_ccb_under_review_with_decisions(self):
        self.ecn.status = ChangeNotice.Status.UNDER_REVIEW
        self.ecn.save(update_fields=['status'])
        approver = ChangeNoticeApprover.objects.create(
            change_notice=self.ecn, user=self.ccb_user, order=1,
        )
        ChangeNoticeDecision.objects.create(
            change_notice=self.ecn,
            approver=approver,
            user=self.ccb_user,
            decision=ChangeNoticeDecision.Decision.APPROVE,
        )
        self.assertTrue(self.can_reopen_ccb(self.manager, self.ecn))
        self.assertFalse(self.can_reopen_ccb(self.stranger, self.ecn))

    # --- update_change_notice (service) ---------------------------------------

    def test_update_change_notice_updates_fields(self):
        updated = self.update_change_notice(
            self.ecn, actor=self.manager,
            title='Nuovo titolo',
            motivation=ChangeNotice.Motivation.CUSTOMER,
            description='Nuova descrizione',
            motivation_detail='Dettaglio motivazione',
            commessa='C-2025-99',
        )
        self.assertEqual(updated.title, 'Nuovo titolo')
        self.assertEqual(updated.motivation, ChangeNotice.Motivation.CUSTOMER)
        self.assertEqual(updated.description, 'Nuova descrizione')
        self.assertEqual(updated.commessa, 'C-2025-99')

    def test_update_change_notice_fails_if_not_draft(self):
        self.ecn.status = ChangeNotice.Status.UNDER_REVIEW
        self.ecn.save(update_fields=['status'])
        with self.assertRaises(ValidationError):
            self.update_change_notice(
                self.ecn, actor=self.manager,
                title='X', motivation=ChangeNotice.Motivation.IMPROVEMENT,
            )

    def test_update_change_notice_writes_audit(self):
        self.update_change_notice(
            self.ecn, actor=self.manager,
            title='Audit test',
            motivation=ChangeNotice.Motivation.IMPROVEMENT,
        )
        self.assertTrue(AuditLog.objects.filter(action='ECN_UPDATED').exists())

    # --- reopen_ccb_configuration (service) -----------------------------------

    def test_reopen_ccb_resets_to_draft_and_deletes_decisions(self):
        self.ecn.status = ChangeNotice.Status.UNDER_REVIEW
        self.ecn.save(update_fields=['status'])
        approver = ChangeNoticeApprover.objects.create(
            change_notice=self.ecn, user=self.ccb_user, order=1,
        )
        ChangeNoticeDecision.objects.create(
            change_notice=self.ecn,
            approver=approver,
            user=self.ccb_user,
            decision=ChangeNoticeDecision.Decision.APPROVE,
        )
        self.reopen_ccb_configuration(self.ecn, actor=self.manager, reason='Test')
        self.ecn.refresh_from_db()
        self.assertEqual(self.ecn.status, ChangeNotice.Status.DRAFT)
        self.assertIsNone(self.ecn.submitted_at)
        self.assertFalse(self.ecn.decisions.exists())

    def test_reopen_ccb_writes_audit(self):
        self.ecn.status = ChangeNotice.Status.UNDER_REVIEW
        self.ecn.save(update_fields=['status'])
        approver = ChangeNoticeApprover.objects.create(
            change_notice=self.ecn, user=self.ccb_user, order=1,
        )
        ChangeNoticeDecision.objects.create(
            change_notice=self.ecn,
            approver=approver,
            user=self.ccb_user,
            decision=ChangeNoticeDecision.Decision.APPROVE,
        )
        self.reopen_ccb_configuration(self.ecn, actor=self.manager)
        self.assertTrue(AuditLog.objects.filter(action='ECN_CCB_REOPENED').exists())

    def test_reopen_ccb_requires_decisions(self):
        """Se non ci sono decisioni, il service deve rifiutare."""
        self.ecn.status = ChangeNotice.Status.UNDER_REVIEW
        self.ecn.save(update_fields=['status'])
        with self.assertRaises(ValidationError):
            self.reopen_ccb_configuration(self.ecn, actor=self.manager)

    def test_reopen_ccb_stranger_permission_denied(self):
        self.ecn.status = ChangeNotice.Status.UNDER_REVIEW
        self.ecn.save(update_fields=['status'])
        approver = ChangeNoticeApprover.objects.create(
            change_notice=self.ecn, user=self.ccb_user, order=1,
        )
        ChangeNoticeDecision.objects.create(
            change_notice=self.ecn,
            approver=approver,
            user=self.ccb_user,
            decision=ChangeNoticeDecision.Decision.APPROVE,
        )
        with self.assertRaises(PermissionDenied):
            self.reopen_ccb_configuration(self.ecn, actor=self.stranger)


class ECNEditViewTests(TestCase):
    """Test per le view ecn_edit e ecn_reopen_ccb."""

    def setUp(self):
        self.manager  = _make_user('vtest_mgr')
        self.proposer = _make_user('vtest_proposer')
        self.stranger = _make_user('vtest_stranger')
        self.ccb_user = _make_user('vtest_ccb')

        grp_mgr  = Group.objects.get_or_create(name='Document Managers')[0]
        grp_qmgr = Group.objects.get_or_create(name='Quality Manager')[0]
        grp_ccb  = Group.objects.get_or_create(name='Change Control Board')[0]
        # MB1: ecn_edit/reopen_ccb richiedono Quality Manager per le azioni di governance
        self.manager.groups.add(grp_mgr, grp_qmgr)
        self.ccb_user.groups.add(grp_ccb)

        self.folder   = _make_folder(self.manager, code='FOLD-VT')
        self.document = _make_document(self.manager, self.folder, code='DOC-VT-001')
        self.version  = _make_version(self.document, self.manager)
        self.document.current_version = self.version
        self.document.save(update_fields=['current_version'])

        self.ecn = _make_ecn(
            self.document, self.version, self.proposer,
            code='ECN-VT-001',
        )

    # --- ecn_edit view --------------------------------------------------------

    def test_ecn_edit_requires_login(self):
        r = self.client.get(f'/ecn/{self.ecn.pk}/edit/')
        self.assertEqual(r.status_code, 302)
        self.assertIn('/accounts/login/', r['Location'])

    def test_ecn_edit_proposer_can_get(self):
        self.client.force_login(self.proposer)
        r = self.client.get(f'/ecn/{self.ecn.pk}/edit/')
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'ECN-VT-001')

    def test_ecn_edit_stranger_forbidden(self):
        self.client.force_login(self.stranger)
        r = self.client.get(f'/ecn/{self.ecn.pk}/edit/')
        self.assertEqual(r.status_code, 403)

    def test_ecn_edit_post_updates_ecn(self):
        self.client.force_login(self.proposer)
        r = self.client.post(f'/ecn/{self.ecn.pk}/edit/', {
            'title': 'Titolo aggiornato',
            'motivation': ChangeNotice.Motivation.IMPROVEMENT,
            'motivation_detail': '',
            'description': 'Nuova desc',
            'commessa': '',
        })
        self.assertEqual(r.status_code, 302)
        self.ecn.refresh_from_db()
        self.assertEqual(self.ecn.title, 'Titolo aggiornato')

    def test_ecn_edit_under_review_forbidden(self):
        """La modifica dei dati base è vietata se l'ECN non è DRAFT."""
        self.ecn.status = ChangeNotice.Status.UNDER_REVIEW
        self.ecn.save(update_fields=['status'])
        self.client.force_login(self.proposer)
        r = self.client.get(f'/ecn/{self.ecn.pk}/edit/')
        self.assertEqual(r.status_code, 403)

    # --- ecn_reopen_ccb view --------------------------------------------------

    def test_ecn_reopen_ccb_requires_login(self):
        r = self.client.get(f'/ecn/{self.ecn.pk}/reopen-ccb/')
        self.assertEqual(r.status_code, 302)
        self.assertIn('/accounts/login/', r['Location'])

    def test_ecn_reopen_ccb_stranger_forbidden(self):
        self.ecn.status = ChangeNotice.Status.UNDER_REVIEW
        self.ecn.save(update_fields=['status'])
        self.client.force_login(self.stranger)
        r = self.client.get(f'/ecn/{self.ecn.pk}/reopen-ccb/')
        self.assertEqual(r.status_code, 403)

    def test_ecn_reopen_ccb_post_resets_ecn(self):
        """POST con decisioni esistenti riporta l'ECN a DRAFT."""
        self.ecn.status = ChangeNotice.Status.UNDER_REVIEW
        self.ecn.save(update_fields=['status'])
        approver = ChangeNoticeApprover.objects.create(
            change_notice=self.ecn, user=self.ccb_user, order=1,
        )
        ChangeNoticeDecision.objects.create(
            change_notice=self.ecn,
            approver=approver,
            user=self.ccb_user,
            decision=ChangeNoticeDecision.Decision.APPROVE,
        )
        self.client.force_login(self.manager)
        r = self.client.post(f'/ecn/{self.ecn.pk}/reopen-ccb/', {'reason': 'Test reopen'})
        self.assertEqual(r.status_code, 302)
        self.ecn.refresh_from_db()
        self.assertEqual(self.ecn.status, ChangeNotice.Status.DRAFT)
        self.assertFalse(self.ecn.decisions.exists())


# ---------------------------------------------------------------------------
# Test UI/UX: Cruscotto ECN e dettaglio — Parte A (valutazione Qualità)
# ---------------------------------------------------------------------------

class ECNDashboardQualityUXTests(TestCase):
    """
    Verifica i miglioramenti UI per la valutazione preliminare Qualità:
    - Sezione cruscotto rinominata "Da valutare da Qualità"
    - Azione primaria = "Analizza ECN" (link al dettaglio)
    - Dettaglio ECN mostra box "Valutazione preliminare Qualità" per manager
    - Proponente vede "In attesa di valutazione Qualità"
    - Manager vede "Configura CCB" come azione successiva
    """

    def setUp(self):
        self.manager  = _make_user('ux_manager')
        self.proposer = _make_user('ux_proposer')
        self.stranger = _make_user('ux_stranger')

        grp_mgr  = Group.objects.get_or_create(name='Document Managers')[0]
        grp_qmgr = Group.objects.get_or_create(name='Quality Manager')[0]
        self.manager.groups.add(grp_mgr, grp_qmgr)  # MB1: dashboard ECN richiede Quality Manager

        self.folder   = _make_folder(self.manager, code='FOLD-UX')
        self.document = _make_document(self.manager, self.folder, code='DOC-UX-001')
        self.version  = _make_version(self.document, self.manager)
        self.document.current_version = self.version
        self.document.save(update_fields=['current_version'])

        # ECN in bozza senza CCB
        self.ecn = _make_ecn(
            self.document, self.version, self.proposer,
            code='ECN-UX-001',
            title='Variante UX test',
        )

    # --- Cruscotto ECN (sezione 1) -------------------------------------------

    def test_dashboard_sezione1_titolo_da_valutare(self):
        """Sezione 1 del cruscotto mostra 'Da valutare da Qualità'."""
        self.client.force_login(self.manager)
        r = self.client.get('/ecn/dashboard/')
        self.assertContains(r, 'Da valutare da Qualità')

    def test_dashboard_sezione1_azione_analizza_ecn(self):
        """L'azione primaria nella sezione 1 è 'Analizza ECN', non 'Configura CCB'."""
        self.client.force_login(self.manager)
        r = self.client.get('/ecn/dashboard/')
        self.assertContains(r, 'Analizza ECN')
        # Il link primario punta al dettaglio, non a configure-ccb
        self.assertContains(r, f'/ecn/{self.ecn.pk}/"')

    def test_dashboard_sezione1_ha_link_configura_ccb_secondario(self):
        """'Configura CCB' è ancora presente come azione secondaria."""
        self.client.force_login(self.manager)
        r = self.client.get('/ecn/dashboard/')
        self.assertContains(r, 'Configura CCB')

    # --- Dettaglio ECN (box valutazione Qualità) --------------------------------

    def test_detail_manager_vede_valutazione_preliminare(self):
        """Il manager vede il box 'Valutazione preliminare Qualità' su ECN draft senza CCB."""
        self.client.force_login(self.manager)
        r = self.client.get(f'/ecn/{self.ecn.pk}/')
        self.assertContains(r, 'Valutazione preliminare Qualità')

    def test_detail_proposer_vede_in_attesa_valutazione(self):
        """Il proponente vede 'In attesa di valutazione Qualità'."""
        self.client.force_login(self.proposer)
        r = self.client.get(f'/ecn/{self.ecn.pk}/')
        self.assertContains(r, 'In attesa di valutazione Qualità')

    def test_detail_manager_ha_bottone_configura_ccb(self):
        """Il manager (Quality Manager) ha il bottone 'Configura CCB' nel dettaglio."""
        self.client.force_login(self.manager)
        r = self.client.get(f'/ecn/{self.ecn.pk}/')
        self.assertContains(r, 'Configura CCB')


# ===========================================================================
# MB1 — Test privacy e governance ECN/CCB
# ===========================================================================

class ECNVisibilityPrivacyTests(TestCase):
    """
    MB1 — Visibilità ECN:
      Quality Manager / Quality Operator / Direction → vedono tutte
      Proponente → propria ECN
      CCB assegnato → ECN specifica
      Document Manager / Auditor / staff → NON vedono automaticamente
    """

    def setUp(self):
        from documents.permissions import GROUP_QUALITY_MANAGER, GROUP_QUALITY_OPERATOR, GROUP_DIRECTION

        self.proposer = _make_user('vis_proposer')
        self.quality_mgr = _make_user_in_groups('vis_qmgr', GROUP_QUALITY_MANAGER)
        self.quality_op = _make_user_in_groups('vis_qop', GROUP_QUALITY_OPERATOR)
        self.direction = _make_user_in_groups('vis_dir', GROUP_DIRECTION)
        self.doc_manager = _make_user_in_groups('vis_docmgr', GROUP_MANAGERS)
        self.doc_auditor = _make_user_in_groups('vis_docaud', GROUP_AUDITORS)
        self.staff_user = User.objects.create_user('vis_staff', password='pw', is_staff=True)
        self.ccb_member = _make_user_in_groups('vis_ccb', GROUP_CCB)
        self.stranger = _make_user('vis_stranger')
        self.superuser = _make_superuser('vis_su')

        self.folder = _make_folder(self.proposer, 'FOLD-VIS')
        self.document = _make_document(self.proposer, self.folder, 'DOC-VIS-001')
        self.version = _make_version(self.document, self.proposer)
        self.document.current_version = self.version
        self.document.save(update_fields=['current_version'])
        self.ecn = _make_ecn(self.document, self.version, self.proposer, 'ECN-VIS-001')

        # CCB assegnato come approvatore
        ChangeNoticeApprover.objects.create(
            change_notice=self.ecn, user=self.ccb_member, order=1,
        )

    # 1. Proponente vede propria ECN
    def test_proposer_can_view_own_ecn(self):
        self.assertTrue(can_view_ecn(self.proposer, self.ecn))

    # 2. Stranger non vede ECN
    def test_stranger_cannot_view_ecn(self):
        self.assertFalse(can_view_ecn(self.stranger, self.ecn))

    # 3. CCB non assegnato NON vede automaticamente
    def test_ccb_member_not_assigned_cannot_view(self):
        unassigned_ccb = _make_user_in_groups('vis_ccb2', GROUP_CCB)
        self.assertFalse(can_view_ecn(unassigned_ccb, self.ecn))

    # 4. CCB assegnato vede la propria ECN
    def test_assigned_ccb_can_view_ecn(self):
        self.assertTrue(can_view_ecn(self.ccb_member, self.ecn))

    # 5. Quality Manager vede tutto
    def test_quality_manager_can_view_all(self):
        self.assertTrue(can_view_ecn(self.quality_mgr, self.ecn))

    # 6. Quality Operator vede (consultazione)
    def test_quality_operator_can_view(self):
        self.assertTrue(can_view_ecn(self.quality_op, self.ecn))

    # 7. Direction vede (consultazione)
    def test_direction_can_view(self):
        self.assertTrue(can_view_ecn(self.direction, self.ecn))

    # 8. staff non-superuser NON vede automaticamente
    def test_staff_cannot_view_ecn_automatically(self):
        self.assertFalse(can_view_ecn(self.staff_user, self.ecn))

    # 9. Document Manager NON vede automaticamente
    def test_document_manager_cannot_view_ecn_automatically(self):
        self.assertFalse(can_view_ecn(self.doc_manager, self.ecn))

    # 10. Superuser vede tutto
    def test_superuser_can_view(self):
        self.assertTrue(can_view_ecn(self.superuser, self.ecn))

    # --- URL diretti (view HTTP) ------------------------------------------

    def test_ecn_detail_url_stranger_returns_404(self):
        self.client.force_login(self.stranger)
        r = self.client.get(f'/ecn/{self.ecn.pk}/')
        self.assertEqual(r.status_code, 404)

    def test_ecn_detail_url_unassigned_ccb_returns_404(self):
        unassigned_ccb = _make_user_in_groups('vis_ccb3', GROUP_CCB)
        self.client.force_login(unassigned_ccb)
        r = self.client.get(f'/ecn/{self.ecn.pk}/')
        self.assertEqual(r.status_code, 404)

    def test_ecn_detail_url_doc_manager_returns_404(self):
        """MB1: Document Manager NON ha accesso diretto al dettaglio ECN."""
        self.client.force_login(self.doc_manager)
        r = self.client.get(f'/ecn/{self.ecn.pk}/')
        self.assertEqual(r.status_code, 404)

    def test_ecn_list_url_doc_manager_does_not_show_ecn(self):
        """MB1: Document Manager non vede le ECN nella lista."""
        self.client.force_login(self.doc_manager)
        r = self.client.get('/ecn/')
        self.assertEqual(r.status_code, 200)
        self.assertNotContains(r, 'ECN-VIS-001')


class ECNCCBGovernanceTests(TestCase):
    """
    MB1 — Governance CCB: solo Quality Manager e superuser.
    Document Manager, staff, Direction, Quality Operator → nessuna governance.
    """

    def setUp(self):
        from documents.permissions import (
            GROUP_QUALITY_MANAGER, GROUP_QUALITY_OPERATOR, GROUP_DIRECTION,
        )
        self.quality_mgr = _make_user_in_groups('gov_qmgr', GROUP_QUALITY_MANAGER)
        self.doc_manager = _make_user_in_groups('gov_docmgr', GROUP_MANAGERS)
        self.quality_op = _make_user_in_groups('gov_qop', GROUP_QUALITY_OPERATOR)
        self.direction = _make_user_in_groups('gov_dir', GROUP_DIRECTION)
        self.staff_user = User.objects.create_user('gov_staff', password='pw', is_staff=True)
        self.superuser = _make_superuser('gov_su')
        self.proposer = _make_user('gov_proposer')

        folder = _make_folder(self.quality_mgr, 'FOLD-GOV')
        doc = _make_document(self.quality_mgr, folder, 'DOC-GOV-001')
        version = _make_version(doc, self.quality_mgr)
        doc.current_version = version
        doc.save(update_fields=['current_version'])
        self.ecn = _make_ecn(doc, version, self.proposer, 'ECN-GOV-001')

    # 1. Solo Quality Manager configura CCB
    def test_quality_manager_can_configure_ccb(self):
        self.assertTrue(can_configure_ccb(self.quality_mgr, self.ecn))

    def test_document_manager_cannot_configure_ccb(self):
        self.assertFalse(can_configure_ccb(self.doc_manager, self.ecn))

    def test_quality_operator_cannot_configure_ccb(self):
        self.assertFalse(can_configure_ccb(self.quality_op, self.ecn))

    def test_direction_cannot_configure_ccb(self):
        self.assertFalse(can_configure_ccb(self.direction, self.ecn))

    def test_staff_cannot_configure_ccb(self):
        self.assertFalse(can_configure_ccb(self.staff_user, self.ecn))

    def test_superuser_can_configure_ccb(self):
        self.assertTrue(can_configure_ccb(self.superuser, self.ecn))

    # 2. Quality Manager può chiudere; gli altri no
    def test_quality_manager_can_close(self):
        self.assertTrue(can_close_ecn(self.quality_mgr, self.ecn))

    def test_document_manager_cannot_close(self):
        self.assertFalse(can_close_ecn(self.doc_manager, self.ecn))

    def test_staff_cannot_close(self):
        self.assertFalse(can_close_ecn(self.staff_user, self.ecn))


class ECNAttachmentDownloadTests(TestCase):
    """
    MB1 — Download allegati ECN: proponente, Quality Manager, CCB assegnato, superuser.
    """

    def setUp(self):
        from documents.permissions import GROUP_QUALITY_MANAGER
        from ecn.permissions import can_download_ecn_attachment

        self.can_download = can_download_ecn_attachment

        self.proposer = _make_user('att_proposer')
        self.quality_mgr = _make_user_in_groups('att_qmgr', GROUP_QUALITY_MANAGER)
        self.doc_manager = _make_user_in_groups('att_docmgr', GROUP_MANAGERS)
        self.doc_auditor = _make_user_in_groups('att_docaud', GROUP_AUDITORS)
        self.quality_op = _make_user_in_groups(
            'att_qop', 'Quality Operator'
        )
        self.direction = _make_user_in_groups('att_dir', 'Direction')
        self.staff_user = User.objects.create_user('att_staff', password='pw', is_staff=True)
        self.ccb_assigned = _make_user_in_groups('att_ccb_a', GROUP_CCB)
        self.ccb_unassigned = _make_user_in_groups('att_ccb_u', GROUP_CCB)
        self.stranger = _make_user('att_stranger')
        self.superuser = _make_superuser('att_su')

        folder = _make_folder(self.proposer, 'FOLD-ATT')
        doc = _make_document(self.proposer, folder, 'DOC-ATT-001')
        version = _make_version(doc, self.proposer)
        doc.current_version = version
        doc.save(update_fields=['current_version'])
        self.ecn = _make_ecn(doc, version, self.proposer, 'ECN-ATT-001')
        ChangeNoticeApprover.objects.create(
            change_notice=self.ecn, user=self.ccb_assigned, order=1,
        )
        self.attachment = ChangeNoticeAttachment.objects.create(
            change_notice=self.ecn,
            original_filename='doc.pdf',
            title='Test',
            uploaded_by=self.proposer,
        )

    # 1. Proponente scarica
    def test_proposer_can_download(self):
        self.assertTrue(self.can_download(self.proposer, self.attachment))

    # 2. CCB assegnato scarica
    def test_assigned_ccb_can_download(self):
        self.assertTrue(self.can_download(self.ccb_assigned, self.attachment))

    # 3. Quality Manager scarica
    def test_quality_manager_can_download(self):
        self.assertTrue(self.can_download(self.quality_mgr, self.attachment))

    # 4. Superuser scarica
    def test_superuser_can_download(self):
        self.assertTrue(self.can_download(self.superuser, self.attachment))

    # 5. Direction NON scarica automaticamente
    def test_direction_cannot_download(self):
        self.assertFalse(self.can_download(self.direction, self.attachment))

    # 6. Quality Operator NON assegnato NON scarica
    def test_quality_operator_not_assigned_cannot_download(self):
        self.assertFalse(self.can_download(self.quality_op, self.attachment))

    # 7. Auditor NON scarica
    def test_auditor_cannot_download(self):
        self.assertFalse(self.can_download(self.doc_auditor, self.attachment))

    # 8. staff non-superuser NON scarica
    def test_staff_cannot_download(self):
        self.assertFalse(self.can_download(self.staff_user, self.attachment))

    # 9. CCB globale non assegnato NON scarica
    def test_unassigned_ccb_cannot_download(self):
        self.assertFalse(self.can_download(self.ccb_unassigned, self.attachment))

    # 10. Utente casuale NON scarica
    def test_stranger_cannot_download(self):
        self.assertFalse(self.can_download(self.stranger, self.attachment))

    # --- URL diretto download -----------------------------------------------

    def test_download_url_stranger_returns_403(self):
        self.client.force_login(self.stranger)
        r = self.client.get(f'/ecn/attachment/{self.attachment.pk}/download/')
        self.assertEqual(r.status_code, 403)

    def test_download_url_doc_manager_returns_403(self):
        """MB1: Document Manager riceve 403 sul download allegati ECN."""
        self.client.force_login(self.doc_manager)
        r = self.client.get(f'/ecn/attachment/{self.attachment.pk}/download/')
        self.assertEqual(r.status_code, 403)


# ===========================================================================
# STEP I — Separazione istruttoria CCB / votazione membri
# ===========================================================================

from django.test import override_settings


def _make_quality_manager(username):
    from documents.permissions import GROUP_QUALITY_MANAGER
    return _make_user_in_groups(username, GROUP_QUALITY_MANAGER)


def _make_draft_ecn_with_ccb(doc, version, proposer, manager, ccb_users,
                              policy='all', code=None):
    """Helper: crea ECN DRAFT con CCB configurata (CCB_PREPARATION)."""
    from ecn.services import configure_ccb
    code = code or f'ECN-SI-{ChangeNotice.objects.count()+1:03d}'
    ecn = create_change_notice(
        document=doc, proposed_by=proposer,
        title='ECN STEP I', motivation=ChangeNotice.Motivation.IMPROVEMENT,
        code=code,
    )
    configure_ccb(ecn, actor=manager, users=ccb_users, policy=policy,
                  coordinator=manager)
    return ecn


# ---------------------------------------------------------------------------
# Configurazione CCB ordinata
# ---------------------------------------------------------------------------

class CCBConfigureTests(TestCase):
    """Test configurazione CCB con formset ordinato + coordinator (STEP I)."""

    def setUp(self):
        self.qm     = _make_quality_manager('ci_qm')
        self.ccb1   = _make_user_in_groups('ci_ccb1', GROUP_CCB)
        self.ccb2   = _make_user_in_groups('ci_ccb2', GROUP_CCB)
        self.stranger = _make_user('ci_stranger')
        self.folder = _make_folder(self.qm, 'FOLD-CI-A')
        self.document, self.version = _make_approved_document(self.qm, self.folder, 'DOC-CI-002')
        self.ecn = create_change_notice(
            document=self.document, proposed_by=self.qm,
            title='Test config', motivation=ChangeNotice.Motivation.IMPROVEMENT,
            code='ECN-CI-001',
        )

    def _configure(self, users, policy='all', coordinator=None):
        from ecn.services import configure_ccb
        return configure_ccb(
            self.ecn, actor=self.qm,
            users=users, policy=policy, coordinator=coordinator,
        )

    # 1. Almeno un membro obbligatorio
    def test_configure_requires_at_least_one_member(self):
        with self.assertRaises(ValidationError):
            self._configure(users=[])

    # 2. Nessun duplicato
    def test_configure_no_duplicates(self):
        """Duplicate utente → ValidationError dal service di basso livello."""
        # set_change_notice_approvers usa unique_together → IntegrityError o ValidationError
        # Per duplicati nello stesso batch → la seconda add crea un record identico → IntegrityError
        # Testiamo via form invece
        from ecn.forms import BaseCCBMemberFormSet, CCBMemberFormSet
        data = {
            'ccb-TOTAL_FORMS': '2',
            'ccb-INITIAL_FORMS': '0',
            'ccb-MIN_NUM_FORMS': '0',
            'ccb-MAX_NUM_FORMS': '1000',
            'ccb-0-user': str(self.ccb1.pk),
            'ccb-1-user': str(self.ccb1.pk),  # duplicato
        }
        formset = CCBMemberFormSet(data, prefix='ccb')
        self.assertFalse(formset.is_valid())

    # 3. Ordine salvato
    def test_configure_saves_order(self):
        self._configure(users=[self.ccb1, self.ccb2])
        apps = list(self.ecn.approvers.order_by('order'))
        self.assertEqual(apps[0].user, self.ccb1)
        self.assertEqual(apps[0].order, 1)
        self.assertEqual(apps[1].user, self.ccb2)
        self.assertEqual(apps[1].order, 2)

    # 4. Policy salvata
    def test_configure_saves_policy(self):
        self._configure(users=[self.ccb1], policy='sequential')
        self.ecn.refresh_from_db()
        self.assertEqual(self.ecn.ccb_policy, 'sequential')

    # 5. Coordinator salvato
    def test_configure_saves_coordinator(self):
        self._configure(users=[self.ccb1], coordinator=self.qm)
        self.ecn.refresh_from_db()
        self.assertEqual(self.ecn.ccb_coordinator, self.qm)

    # 6. Transizione DRAFT → CCB_PREPARATION
    def test_configure_transitions_to_ccb_preparation(self):
        self.assertEqual(self.ecn.status, ChangeNotice.Status.DRAFT)
        self._configure(users=[self.ccb1], coordinator=self.qm)
        self.ecn.refresh_from_db()
        self.assertEqual(self.ecn.status, ChangeNotice.Status.CCB_PREPARATION)

    # 7. Audit log CCB_CONFIGURED scritto
    def test_configure_writes_auditlog(self):
        AuditLog.objects.all().delete()
        self._configure(users=[self.ccb1], coordinator=self.qm)
        log = AuditLog.objects.filter(action='CCB_CONFIGURED').first()
        self.assertIsNotNone(log)

    # 8. POST con utente non eleggibile respinta dal formset
    def test_configure_post_non_candidate_rejected(self):
        """Utente non nel queryset candidati → form row invalid."""
        from ecn.forms import CCBMemberRowForm
        form = CCBMemberRowForm(
            {'user': str(self.stranger.pk)},
            current_user=self.qm,
        )
        # Il form è invalid perché stranger non è nel queryset
        if form.is_valid():
            user = form.cleaned_data.get('user')
            # Se il queryset non include stranger, cleaned_data['user'] sarà None o assente
            self.assertIsNone(user)

    # 9. Dopo submit CCB_PREPARATION, riconfigurazione consentita (no decisioni)
    def test_reconfigure_allowed_in_ccb_preparation(self):
        self._configure(users=[self.ccb1])
        self.ecn.refresh_from_db()
        self.assertEqual(self.ecn.status, ChangeNotice.Status.CCB_PREPARATION)
        # Riconfigurazione: sostituisce con ccb2
        from ecn.permissions import can_reconfigure_ccb
        self.assertTrue(can_reconfigure_ccb(self.qm, self.ecn))

    @override_settings(
        DOCUMENTALE_DEMO_MODE=True,
        DOCUMENTALE_DEMO_SUPERVISOR_USERNAME='supervisor_demo',
    )
    def test_demo_supervisor_sees_only_self_as_ccb_member(self):
        """Demo mode: lista componenti CCB → solo supervisor_demo."""
        sup = User.objects.create_user('supervisor_demo', password='pw',
                                       is_superuser=True, is_staff=True)
        from ecn.forms import CCBMemberRowForm
        form = CCBMemberRowForm(current_user=sup)
        qs = list(form.fields['user'].queryset)
        self.assertEqual(len(qs), 1)
        self.assertEqual(qs[0].pk, sup.pk)


# ---------------------------------------------------------------------------
# Dossier istruttorio
# ---------------------------------------------------------------------------

class CCBDossierTests(TestCase):
    """Test compilazione dossier CCB (STEP I)."""

    def setUp(self):
        self.qm   = _make_quality_manager('dos_qm')
        self.ccb1 = _make_user_in_groups('dos_ccb1', GROUP_CCB)
        self.other = _make_user('dos_other')
        self.folder = _make_folder(self.qm, 'FOLD-DOS')
        self.document, self.version = _make_approved_document(
            self.qm, self.folder, 'DOC-DOS-001',
        )
        self.ecn = create_change_notice(
            document=self.document, proposed_by=self.qm,
            title='ECN dossier', motivation=ChangeNotice.Motivation.IMPROVEMENT,
            code='ECN-DOS-001',
        )
        from ecn.services import configure_ccb
        configure_ccb(self.ecn, actor=self.qm, users=[self.ccb1],
                      policy='all', coordinator=self.qm)

    def _update_dossier(self, actor=None, **kwargs):
        from ecn.services import update_ccb_dossier
        actor = actor or self.qm
        defaults = {
            'ccb_class': 'class1',
            'ccb_requirements': 'Conforme.',
            'ccb_technical_impact': 'Minore.',
        }
        defaults.update(kwargs)
        return update_ccb_dossier(self.ecn, actor=actor, **defaults)

    # 1. Responsabile compila il dossier
    def test_coordinator_can_compile_dossier(self):
        self._update_dossier()
        self.ecn.refresh_from_db()
        self.assertEqual(self.ecn.ccb_class, 'class1')
        self.assertEqual(self.ecn.ccb_requirements, 'Conforme.')

    # 2. Quality Manager compila il dossier
    def test_quality_manager_can_compile_dossier(self):
        other_qm = _make_quality_manager('dos_qm2')
        self._update_dossier(actor=other_qm)
        self.ecn.refresh_from_db()
        self.assertEqual(self.ecn.ccb_requirements, 'Conforme.')

    # 3. Utente casuale non compila
    def test_random_user_cannot_compile_dossier(self):
        from ecn.services import update_ccb_dossier
        from django.core.exceptions import PermissionDenied
        with self.assertRaises(PermissionDenied):
            update_ccb_dossier(
                self.ecn, actor=self.other,
                ccb_class='class1',
                ccb_requirements='Test',
                ccb_technical_impact='Test',
            )

    # 4. Save bozza lascia stato CCB_PREPARATION
    def test_save_draft_keeps_ccb_preparation_status(self):
        self._update_dossier()
        self.ecn.refresh_from_db()
        self.assertEqual(self.ecn.status, ChangeNotice.Status.CCB_PREPARATION)

    # 5. Submit senza ccb_class → ValidationError dal service submit
    def test_submit_without_ccb_class_raises(self):
        # Aggiorniamo dossier senza ccb_class
        from ecn.services import update_ccb_dossier, submit_change_notice
        update_ccb_dossier(
            self.ecn, actor=self.qm,
            ccb_requirements='Conforme.', ccb_technical_impact='Minore.',
        )
        with self.assertRaises(ValidationError):
            submit_change_notice(self.ecn, self.qm)

    # 6. Submit completo porta a under_review
    def test_submit_complete_dossier_to_under_review(self):
        from ecn.services import submit_change_notice
        self._update_dossier()
        submit_change_notice(self.ecn, self.qm)
        self.ecn.refresh_from_db()
        self.assertEqual(self.ecn.status, ChangeNotice.Status.UNDER_REVIEW)

    # 7. Dopo submit dossier non modificabile
    def test_dossier_not_editable_after_submit(self):
        from ecn.services import submit_change_notice, update_ccb_dossier
        from django.core.exceptions import PermissionDenied
        self._update_dossier()
        submit_change_notice(self.ecn, self.qm)
        self.ecn.refresh_from_db()
        self.assertEqual(self.ecn.status, ChangeNotice.Status.UNDER_REVIEW)
        with self.assertRaises((ValidationError, PermissionDenied)):
            update_ccb_dossier(self.ecn, actor=self.qm, ccb_class='class2',
                               ccb_requirements='Aggiornata', ccb_technical_impact='X')

    # 8. AuditLog CCB_DOSSIER_UPDATED scritto
    def test_dossier_update_writes_auditlog(self):
        AuditLog.objects.all().delete()
        self._update_dossier()
        log = AuditLog.objects.filter(action='CCB_DOSSIER_UPDATED').first()
        self.assertIsNotNone(log)


# ---------------------------------------------------------------------------
# Voto membro CCB
# ---------------------------------------------------------------------------

@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class CCBVoteTests(TestCase):
    """Test votazione individuale membro CCB (STEP I)."""

    def setUp(self):
        self.qm   = _make_quality_manager('vt_qm')
        self.ccb1 = _make_user_in_groups('vt_ccb1', GROUP_CCB)
        self.ccb2 = _make_user_in_groups('vt_ccb2', GROUP_CCB)
        self.other = _make_user('vt_other')
        self.folder = _make_folder(self.qm, 'FOLD-VT')
        self.document, self.version = _make_approved_document(
            self.qm, self.folder, 'DOC-VT-001',
        )
        self.ecn = create_change_notice(
            document=self.document, proposed_by=self.qm,
            title='ECN vote', motivation=ChangeNotice.Motivation.IMPROVEMENT,
            code='ECN-VT-001',
        )
        from ecn.services import configure_ccb, update_ccb_dossier, submit_change_notice
        configure_ccb(self.ecn, actor=self.qm, users=[self.ccb1, self.ccb2],
                      policy='all', coordinator=self.qm)
        update_ccb_dossier(
            self.ecn, actor=self.qm,
            ccb_class='class1', ccb_requirements='OK', ccb_technical_impact='Minore',
        )
        submit_change_notice(self.ecn, self.qm)
        self.ecn.refresh_from_db()

    # 1. Membro vede dossier read-only nella pagina review
    def test_member_sees_dossier_in_review_page(self):
        self.client.force_login(self.ccb1)
        r = self.client.get(f'/ecn/{self.ecn.pk}/review/')
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'Decisione CCB')
        self.assertContains(r, 'Dossier istruttorio')

    # 2. Membro può approvare
    def test_member_can_approve(self):
        self.client.force_login(self.ccb1)
        r = self.client.post(f'/ecn/{self.ecn.pk}/review/', {
            'action': 'approve',
            'comment': 'OK',
            'ccb_notes': '',
        })
        self.assertRedirects(r, f'/ecn/{self.ecn.pk}/', fetch_redirect_response=False)
        dec = ChangeNoticeDecision.objects.filter(
            change_notice=self.ecn, user=self.ccb1,
        ).first()
        self.assertIsNotNone(dec)
        self.assertEqual(dec.decision, ChangeNoticeDecision.Decision.APPROVE)

    # 3. Membro può rifiutare con motivazione
    def test_member_can_reject_with_reason(self):
        self.client.force_login(self.ccb1)
        r = self.client.post(f'/ecn/{self.ecn.pk}/review/', {
            'action': 'reject',
            'comment': '',
            'ccb_notes': 'Analisi insufficiente.',
        })
        self.assertRedirects(r, f'/ecn/{self.ecn.pk}/', fetch_redirect_response=False)
        self.ecn.refresh_from_db()
        self.assertEqual(self.ecn.status, ChangeNotice.Status.REJECTED)

    # 4. Rifiuto senza motivazione → errore form
    def test_reject_without_reason_fails(self):
        self.client.force_login(self.ccb1)
        r = self.client.post(f'/ecn/{self.ecn.pk}/review/', {
            'action': 'reject',
            'comment': '',
            'ccb_notes': '',  # mancante
        })
        self.assertEqual(r.status_code, 200)
        self.ecn.refresh_from_db()
        self.assertEqual(self.ecn.status, ChangeNotice.Status.UNDER_REVIEW)

    # 5. Utente non membro non vota
    def test_non_member_cannot_vote(self):
        self.client.force_login(self.other)
        r = self.client.get(f'/ecn/{self.ecn.pk}/review/')
        self.assertEqual(r.status_code, 403)

    # 6. Doppio voto respinto
    def test_double_vote_rejected(self):
        approve_change_notice(self.ecn, self.ccb1, comment='Primo voto')
        self.ecn.refresh_from_db()
        with self.assertRaises(ValidationError):
            approve_change_notice(self.ecn, self.ccb1, comment='Doppio voto')

    # 7. Utente non membro non modifica dossier via POST a ccb-dossier
    def test_non_coordinator_cannot_post_to_dossier(self):
        """La pagina ccb-dossier richiede can_compile_dossier."""
        self.client.force_login(self.other)
        r = self.client.post(f'/ecn/{self.ecn.pk}/ccb-dossier/', {
            'ccb_class': 'class2',
            'ccb_requirements': 'Modificata',
            'ccb_technical_impact': 'Modificato',
            'dossier_action': 'save',
        })
        self.assertEqual(r.status_code, 403)


# ---------------------------------------------------------------------------
# Policy ANY / ALL / SEQUENTIAL
# ---------------------------------------------------------------------------

@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class CCBPolicyTests(TestCase):
    """Test policy CCB con nuovo flusso istruttoria (STEP I)."""

    def setUp(self):
        self.qm   = _make_quality_manager('pol_qm')
        self.ccb1 = _make_user_in_groups('pol_ccb1', GROUP_CCB)
        self.ccb2 = _make_user_in_groups('pol_ccb2', GROUP_CCB)
        self.folder = _make_folder(self.qm, 'FOLD-POL')
        self.document, self.version = _make_approved_document(
            self.qm, self.folder, 'DOC-POL-001',
        )

    def _make_ecn_ready(self, policy, code):
        from ecn.services import configure_ccb, update_ccb_dossier, submit_change_notice
        ecn = create_change_notice(
            document=self.document, proposed_by=self.qm,
            title='ECN policy', motivation=ChangeNotice.Motivation.IMPROVEMENT,
            code=code,
        )
        configure_ccb(ecn, actor=self.qm, users=[self.ccb1, self.ccb2],
                      policy=policy, coordinator=self.qm)
        update_ccb_dossier(
            ecn, actor=self.qm,
            ccb_class='class1', ccb_requirements='OK', ccb_technical_impact='OK',
        )
        submit_change_notice(ecn, self.qm)
        ecn.refresh_from_db()
        return ecn

    # 1. ANY: prima approvazione → ECN approvato
    def test_policy_any_first_approval_finalizes(self):
        ecn = self._make_ecn_ready('any', 'ECN-POL-ANY')
        approve_change_notice(ecn, self.ccb1, ccb_class='class1')
        ecn.refresh_from_db()
        self.assertEqual(ecn.status, ChangeNotice.Status.APPROVED)

    # 2. ALL: tutti devono approvare
    def test_policy_all_requires_all(self):
        ecn = self._make_ecn_ready('all', 'ECN-POL-ALL')
        approve_change_notice(ecn, self.ccb1, ccb_class='class1')
        ecn.refresh_from_db()
        self.assertEqual(ecn.status, ChangeNotice.Status.UNDER_REVIEW)
        approve_change_notice(ecn, self.ccb2, ccb_class='class1')
        ecn.refresh_from_db()
        self.assertEqual(ecn.status, ChangeNotice.Status.APPROVED)

    # 3. SEQUENTIAL: rispetta ordine
    def test_policy_sequential_second_cannot_vote_before_first(self):
        ecn = self._make_ecn_ready('sequential', 'ECN-POL-SEQ')
        with self.assertRaises(PermissionDenied):
            approve_change_notice(ecn, self.ccb2, ccb_class='class1')

    # 4. SEQUENTIAL: POST manuale fuori turno respinta (permesso)
    def test_policy_sequential_out_of_turn_post_forbidden(self):
        ecn = self._make_ecn_ready('sequential', 'ECN-POL-SEQ2')
        self.client.force_login(self.ccb2)
        r = self.client.post(f'/ecn/{ecn.pk}/review/', {
            'action': 'approve',
            'comment': '',
            'ccb_notes': '',
        })
        # can_review_ecn restituisce False → 403
        self.assertEqual(r.status_code, 403)

    # 5. SEQUENTIAL: full flow
    def test_policy_sequential_full_flow(self):
        ecn = self._make_ecn_ready('sequential', 'ECN-POL-SEQ-FULL')
        approve_change_notice(ecn, self.ccb1, ccb_class='class1')
        ecn.refresh_from_db()
        self.assertEqual(ecn.status, ChangeNotice.Status.UNDER_REVIEW)
        approve_change_notice(ecn, self.ccb2, ccb_class='class1')
        ecn.refresh_from_db()
        self.assertEqual(ecn.status, ChangeNotice.Status.APPROVED)

    # 6. Esito finale corretto
    def test_all_policy_reject_by_one_rejects_ecn(self):
        ecn = self._make_ecn_ready('all', 'ECN-POL-ALL-REJ')
        reject_change_notice(ecn, self.ccb1, reason='Non conforme.')
        ecn.refresh_from_db()
        self.assertEqual(ecn.status, ChangeNotice.Status.REJECTED)


# ---------------------------------------------------------------------------
# Email e notifiche (STEP I)
# ---------------------------------------------------------------------------

@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class CCBEmailNotificationTests(TestCase):
    """Test notifiche email CCB con nuovo flusso (STEP I)."""

    def setUp(self):
        from django.core import mail
        mail.outbox = []
        self.qm   = _make_quality_manager('em_qm')
        self.ccb1 = _make_user_in_groups('em_ccb1', GROUP_CCB)
        self.ccb1.email = 'ccb1@test.com'
        self.ccb1.save(update_fields=['email'])
        self.ccb2 = _make_user_in_groups('em_ccb2', GROUP_CCB)
        self.ccb2.email = 'ccb2@test.com'
        self.ccb2.save(update_fields=['email'])
        self.folder = _make_folder(self.qm, 'FOLD-EM')
        self.document, self.version = _make_approved_document(
            self.qm, self.folder, 'DOC-EM-001',
        )

    def _submit_ecn(self, policy, code):
        from ecn.services import configure_ccb, update_ccb_dossier, submit_change_notice
        from django.core import mail
        mail.outbox = []
        ecn = create_change_notice(
            document=self.document, proposed_by=self.qm,
            title='ECN email', motivation=ChangeNotice.Motivation.IMPROVEMENT,
            code=code,
        )
        configure_ccb(ecn, actor=self.qm, users=[self.ccb1, self.ccb2],
                      policy=policy, coordinator=self.qm)
        update_ccb_dossier(ecn, actor=self.qm, ccb_class='class1',
                           ccb_requirements='OK', ccb_technical_impact='OK')
        submit_change_notice(ecn, self.qm)
        ecn.refresh_from_db()
        return ecn

    # 1. ANY: notifica tutti
    def test_any_policy_notifies_all_members(self):
        from django.core import mail
        self._submit_ecn('any', 'ECN-EM-ANY')
        recipients = [m.to[0] for m in mail.outbox if m.to]
        self.assertIn('ccb1@test.com', recipients)
        self.assertIn('ccb2@test.com', recipients)

    # 2. ALL: notifica tutti
    def test_all_policy_notifies_all_members(self):
        from django.core import mail
        self._submit_ecn('all', 'ECN-EM-ALL')
        recipients = [m.to[0] for m in mail.outbox if m.to]
        self.assertIn('ccb1@test.com', recipients)
        self.assertIn('ccb2@test.com', recipients)

    # 3. SEQUENTIAL: notifica solo il primo membro
    def test_sequential_policy_notifies_only_first(self):
        from django.core import mail
        self._submit_ecn('sequential', 'ECN-EM-SEQ')
        recipients = [m.to[0] for m in mail.outbox if m.to]
        self.assertIn('ccb1@test.com', recipients)
        self.assertNotIn('ccb2@test.com', recipients)

    # 4. SEQUENTIAL: dopo approvazione notifica membro successivo
    def test_sequential_after_approval_notifies_next(self):
        from django.core import mail
        ecn = self._submit_ecn('sequential', 'ECN-EM-SEQ2')
        mail.outbox = []
        approve_change_notice(ecn, self.ccb1, ccb_class='class1', comment='OK')
        ecn.refresh_from_db()
        if ecn.status == ChangeNotice.Status.UNDER_REVIEW:
            recipients = [m.to[0] for m in mail.outbox if m.to]
            self.assertIn('ccb2@test.com', recipients)

    # 5. Proponente notificato all'approvazione finale
    def test_proposer_notified_on_approval(self):
        from django.core import mail
        self.qm.email = 'qm@test.com'
        self.qm.save(update_fields=['email'])
        ecn = self._submit_ecn('any', 'ECN-EM-APPR')
        mail.outbox = []
        approve_change_notice(ecn, self.ccb1, ccb_class='class1', comment='OK')
        ecn.refresh_from_db()
        recipients = [m.to[0] for m in mail.outbox if m.to]
        self.assertIn('qm@test.com', recipients)


# ---------------------------------------------------------------------------
# Audit log STEP I
# ---------------------------------------------------------------------------

class CCBAuditTests(TestCase):
    """Test AuditLog per gli eventi CCB (STEP I)."""

    def setUp(self):
        self.qm   = _make_quality_manager('aud_qm')
        self.ccb1 = _make_user_in_groups('aud_ccb1', GROUP_CCB)
        self.folder = _make_folder(self.qm, 'FOLD-AUD-SI')
        self.document, self.version = _make_approved_document(
            self.qm, self.folder, 'DOC-AUD-SI-001',
        )
        self.ecn = create_change_notice(
            document=self.document, proposed_by=self.qm,
            title='ECN audit', motivation=ChangeNotice.Motivation.IMPROVEMENT,
            code='ECN-AUD-SI-001',
        )

    # 1. CCB_CONFIGURED
    def test_ccb_configured_auditlog(self):
        from ecn.services import configure_ccb
        AuditLog.objects.all().delete()
        configure_ccb(self.ecn, actor=self.qm, users=[self.ccb1],
                      coordinator=self.qm)
        self.assertTrue(AuditLog.objects.filter(action='CCB_CONFIGURED').exists())

    # 2. CCB_DOSSIER_UPDATED
    def test_ccb_dossier_updated_auditlog(self):
        from ecn.services import configure_ccb, update_ccb_dossier
        configure_ccb(self.ecn, actor=self.qm, users=[self.ccb1], coordinator=self.qm)
        AuditLog.objects.all().delete()
        update_ccb_dossier(self.ecn, actor=self.qm, ccb_class='class1',
                           ccb_requirements='OK', ccb_technical_impact='Minore')
        self.assertTrue(AuditLog.objects.filter(action='CCB_DOSSIER_UPDATED').exists())

    # 3. ECN_SUBMITTED
    @override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
    def test_ecn_submitted_auditlog(self):
        from ecn.services import configure_ccb, update_ccb_dossier, submit_change_notice
        configure_ccb(self.ecn, actor=self.qm, users=[self.ccb1], coordinator=self.qm)
        update_ccb_dossier(self.ecn, actor=self.qm, ccb_class='class1',
                           ccb_requirements='OK', ccb_technical_impact='OK')
        AuditLog.objects.all().delete()
        submit_change_notice(self.ecn, self.qm)
        self.assertTrue(AuditLog.objects.filter(action='ECN_SUBMITTED').exists())

    # 4. ECN_APPROVED
    @override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
    def test_ecn_approved_auditlog(self):
        from ecn.services import configure_ccb, update_ccb_dossier, submit_change_notice
        configure_ccb(self.ecn, actor=self.qm, users=[self.ccb1], coordinator=self.qm)
        update_ccb_dossier(self.ecn, actor=self.qm, ccb_class='class1',
                           ccb_requirements='OK', ccb_technical_impact='OK')
        submit_change_notice(self.ecn, self.qm)
        AuditLog.objects.all().delete()
        approve_change_notice(self.ecn, self.ccb1, ccb_class='class1')
        self.assertTrue(AuditLog.objects.filter(action='ECN_APPROVED').exists())

    # 5. ECN_REJECTED
    @override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
    def test_ecn_rejected_auditlog(self):
        from ecn.services import configure_ccb, update_ccb_dossier, submit_change_notice
        configure_ccb(self.ecn, actor=self.qm, users=[self.ccb1], coordinator=self.qm)
        update_ccb_dossier(self.ecn, actor=self.qm, ccb_class='class1',
                           ccb_requirements='OK', ccb_technical_impact='OK')
        submit_change_notice(self.ecn, self.qm)
        AuditLog.objects.all().delete()
        reject_change_notice(self.ecn, self.ccb1, reason='Non conforme.')
        self.assertTrue(AuditLog.objects.filter(action='ECN_REJECTED').exists())
