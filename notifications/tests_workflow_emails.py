"""
Test copertura email workflow — notifiche approvazioni documentali ed ECN.

Esegui con:
  python manage.py test notifications.tests_workflow_emails
"""
from django.contrib.auth.models import Group, User
from django.core import mail
from django.test import TestCase, override_settings

from documents.models import Document, DocumentVersion
from documents.services import create_new_revision, submit_version_for_approval
from approvals.models import ApprovalRequest, ApprovalRequestApprover
from approvals.services import approve_version
from ecn.models import ChangeNotice, ChangeNoticeApprover
from ecn.services import (
    approve_change_notice,
    configure_ccb,
    create_change_notice,
    reject_change_notice,
    set_change_notice_approvers,
    submit_change_notice,
)

LOCMEM = 'django.core.mail.backends.locmem.EmailBackend'


# ---------------------------------------------------------------------------
# Helpers condivisi
# ---------------------------------------------------------------------------

def _user(username, email=None):
    u = User.objects.create_user(username, password='pw')
    u.email = email or f'{username}@example.com'
    u.save()
    return u


def _make_document(owner, code='D-001'):
    return Document.objects.create(
        code=code,
        title='Doc test',
        category=Document.Category.QUALITY,
        owner=owner,
        created_by=owner,
    )


def _make_version_approved(document, created_by, label='00', number=0):
    """Crea una DocumentVersion già approvata e la imposta come current."""
    v = DocumentVersion.objects.create(
        document=document,
        revision_label=label,
        revision_number=number,
        status=DocumentVersion.Status.APPROVED,
        is_current=True,
        created_by=created_by,
    )
    document.current_version = v
    document.save(update_fields=['current_version'])
    return v


def _make_ecn(document, version, proposed_by, code='ECN-T001'):
    return ChangeNotice.objects.create(
        code=code,
        title='Test ECN',
        description='Descrizione test',
        motivation=ChangeNotice.Motivation.IMPROVEMENT,
        document=document,
        document_version=version,
        proposed_by=proposed_by,
        created_by=proposed_by,
    )


def _qm_group():
    g, _ = Group.objects.get_or_create(name='Quality Manager')
    return g


# ---------------------------------------------------------------------------
# STEP 2: Approvazioni documentali SEQUENTIAL
# ---------------------------------------------------------------------------

@override_settings(EMAIL_BACKEND=LOCMEM)
class SequentialApprovalEmailTest(TestCase):

    def setUp(self):
        self.author = _user('author_seq')
        self.approver1 = _user('approver1_seq')
        self.approver2 = _user('approver2_seq')
        self.doc = _make_document(owner=self.author)

    def _submit_sequential(self):
        version = create_new_revision(
            self.doc, self.author, 'A', 1, _bypass_ecn_check=True,
        )
        approval_request = submit_version_for_approval(
            version,
            self.author,
            [self.approver1, self.approver2],
            approval_policy=ApprovalRequest.Policy.SEQUENTIAL,
        )
        return version, approval_request

    def test_sequential_submit_only_first_approver_gets_email(self):
        """SEQUENTIAL submit → solo il primo approvatore riceve email."""
        mail.outbox.clear()
        _, req = self._submit_sequential()

        recipients = [m.to[0] for m in mail.outbox]
        self.assertEqual(len(mail.outbox), 1, f"Email inviate: {recipients}")
        self.assertIn(self.approver1.email, recipients)
        self.assertNotIn(self.approver2.email, recipients)

    def test_sequential_after_approve_second_gets_email(self):
        """SEQUENTIAL dopo prima approvazione → il secondo approvatore riceve email."""
        _, req = self._submit_sequential()
        mail.outbox.clear()

        approve_version(req, self.approver1)

        recipients = [m.to[0] for m in mail.outbox]
        # L'email al secondo approvatore deve essere presente
        self.assertIn(self.approver2.email, recipients,
                      f"Email inviate dopo prima approvazione: {recipients}")

    def test_sequential_after_last_approve_no_next_email(self):
        """SEQUENTIAL dopo l'ultima approvazione → email di approvazione finale, non di turno."""
        _, req = self._submit_sequential()
        approve_version(req, self.approver1)
        mail.outbox.clear()

        approve_version(req, self.approver2)

        # Deve essere arrivata email di approvazione finale all'autore
        recipients = [m.to[0] for m in mail.outbox]
        self.assertIn(self.author.email, recipients,
                      f"Email inviate dopo approvazione finale: {recipients}")

    def test_all_policy_submit_all_approvers_get_email(self):
        """ALL submit → tutti gli approvatori ricevono email."""
        version = create_new_revision(
            self.doc, self.author, 'B', 2, _bypass_ecn_check=True,
        )
        mail.outbox.clear()
        submit_version_for_approval(
            version,
            self.author,
            [self.approver1, self.approver2],
            approval_policy=ApprovalRequest.Policy.ALL,
        )

        recipients = [m.to[0] for m in mail.outbox]
        self.assertIn(self.approver1.email, recipients)
        self.assertIn(self.approver2.email, recipients)
        self.assertEqual(len(mail.outbox), 2)

    def test_any_policy_submit_all_approvers_get_email(self):
        """ANY submit → tutti gli approvatori ricevono email."""
        version = create_new_revision(
            self.doc, self.author, 'C', 3, _bypass_ecn_check=True,
        )
        mail.outbox.clear()
        submit_version_for_approval(
            version,
            self.author,
            [self.approver1, self.approver2],
            approval_policy=ApprovalRequest.Policy.ANY,
        )

        recipients = [m.to[0] for m in mail.outbox]
        self.assertIn(self.approver1.email, recipients)
        self.assertIn(self.approver2.email, recipients)
        self.assertEqual(len(mail.outbox), 2)


# ---------------------------------------------------------------------------
# STEP 8: Revisione approvata → owner documento
# ---------------------------------------------------------------------------

@override_settings(EMAIL_BACKEND=LOCMEM)
class VersionApprovedOwnerEmailTest(TestCase):

    def setUp(self):
        self.author = _user('ver_author')
        self.owner = _user('doc_owner_ver')
        self.approver = _user('ver_approver')
        self.doc = _make_document(owner=self.owner)
        self.doc.owner = self.owner
        self.doc.save()

    def test_approved_notifies_author_and_owner(self):
        """Revisione approvata → email sia all'autore che all'owner del documento."""
        version = create_new_revision(
            self.doc, self.author, 'A', 1, _bypass_ecn_check=True,
        )
        req = submit_version_for_approval(
            version, self.author, [self.approver],
        )
        mail.outbox.clear()
        approve_version(req, self.approver)

        recipients = [m.to[0] for m in mail.outbox]
        self.assertIn(self.author.email, recipients)
        self.assertIn(self.owner.email, recipients)

    def test_approved_no_duplicate_when_author_is_owner(self):
        """Se author == owner, una sola email di approvazione."""
        doc = _make_document(owner=self.author, code='D-SAME')
        version = create_new_revision(
            doc, self.author, 'A', 1, _bypass_ecn_check=True,
        )
        req = submit_version_for_approval(
            version, self.author, [self.approver],
        )
        mail.outbox.clear()
        approve_version(req, self.approver)

        # L'autore/owner deve ricevere una sola email di approvazione
        approved_emails = [m for m in mail.outbox if self.author.email in m.to]
        self.assertEqual(len(approved_emails), 1)


# ---------------------------------------------------------------------------
# STEP 3: ECN creata → owner documento e Quality Manager
# ---------------------------------------------------------------------------

@override_settings(EMAIL_BACKEND=LOCMEM)
class EcnCreatedEmailTest(TestCase):

    def setUp(self):
        self.proposer = _user('ecn_proposer')
        self.doc_owner = _user('ecn_doc_owner')
        self.qm = _user('ecn_qm')
        qm_group = _qm_group()
        self.qm.groups.add(qm_group)

        self.doc = _make_document(owner=self.doc_owner, code='D-ECN-C')
        self.version = _make_version_approved(self.doc, self.doc_owner, '00', 0)

    def test_ecn_created_owner_gets_email(self):
        """Creazione ECN → owner documento (se diverso da proponente) riceve email."""
        mail.outbox.clear()
        create_change_notice(
            document=self.doc,
            proposed_by=self.proposer,
            title='Test ECN creation',
            motivation=ChangeNotice.Motivation.IMPROVEMENT,
        )

        recipients = [m.to[0] for m in mail.outbox]
        self.assertIn(self.doc_owner.email, recipients,
                      f"Email inviate: {recipients}")

    def test_ecn_created_quality_manager_gets_email(self):
        """Creazione ECN → Quality Manager riceve email."""
        mail.outbox.clear()
        create_change_notice(
            document=self.doc,
            proposed_by=self.proposer,
            title='Test ECN QM',
            motivation=ChangeNotice.Motivation.IMPROVEMENT,
        )

        recipients = [m.to[0] for m in mail.outbox]
        self.assertIn(self.qm.email, recipients,
                      f"Email inviate: {recipients}")

    def test_ecn_created_proposer_does_not_get_email(self):
        """Creazione ECN → il proponente non riceve email (già sa)."""
        mail.outbox.clear()
        create_change_notice(
            document=self.doc,
            proposed_by=self.proposer,
            title='Test ECN no self',
            motivation=ChangeNotice.Motivation.IMPROVEMENT,
        )

        recipients = [m.to[0] for m in mail.outbox]
        self.assertNotIn(self.proposer.email, recipients)

    def test_ecn_created_owner_is_proposer_no_duplicate(self):
        """Se owner == proposer, owner non riceve email extra."""
        doc = _make_document(owner=self.proposer, code='D-ECN-SAME')
        _make_version_approved(doc, self.proposer, '00', 0)
        mail.outbox.clear()
        create_change_notice(
            document=doc,
            proposed_by=self.proposer,
            title='Test ECN owner eq proposer',
            motivation=ChangeNotice.Motivation.IMPROVEMENT,
        )

        recipients = [m.to[0] for m in mail.outbox]
        # Il proponente/owner non deve ricevere email
        self.assertNotIn(self.proposer.email, recipients)


# ---------------------------------------------------------------------------
# STEP 4: Coordinatore assegnato
# ---------------------------------------------------------------------------

@override_settings(EMAIL_BACKEND=LOCMEM)
class EcnCoordinatorAssignedEmailTest(TestCase):

    def setUp(self):
        self.manager = _user('ccb_manager')
        self.coordinator = _user('ccb_coordinator')
        self.member = _user('ccb_member')
        self.doc_owner = _user('ccb_doc_owner')
        self.doc = _make_document(owner=self.doc_owner, code='D-CCB-CO')
        self.version = _make_version_approved(self.doc, self.doc_owner, '00', 0)
        self.ecn = _make_ecn(self.doc, self.version, self.manager, code='ECN-COORD-01')

    def test_coordinator_gets_email_when_assigned(self):
        """Coordinatore assegnato → riceve email."""
        mail.outbox.clear()
        configure_ccb(
            self.ecn,
            actor=self.manager,
            users=[self.member],
            coordinator=self.coordinator,
        )

        recipients = [m.to[0] for m in mail.outbox]
        self.assertIn(self.coordinator.email, recipients,
                      f"Email inviate: {recipients}")

    def test_coordinator_no_email_when_unchanged(self):
        """Se il coordinatore non cambia, non riceve email duplicata."""
        configure_ccb(
            self.ecn,
            actor=self.manager,
            users=[self.member],
            coordinator=self.coordinator,
        )
        # Seconda chiamata con stesso coordinatore
        mail.outbox.clear()
        configure_ccb(
            self.ecn,
            actor=self.manager,
            users=[self.member],
            coordinator=self.coordinator,
        )

        coord_emails = [m for m in mail.outbox if self.coordinator.email in m.to]
        self.assertEqual(len(coord_emails), 0,
                         "Il coordinatore non dovrebbe ricevere email duplicata.")

    def test_no_coordinator_no_email(self):
        """Nessun coordinatore assegnato → nessuna email di coordinatore."""
        mail.outbox.clear()
        configure_ccb(
            self.ecn,
            actor=self.manager,
            users=[self.member],
            coordinator=None,
        )

        coord_emails = [m for m in mail.outbox if self.coordinator.email in m.to]
        self.assertEqual(len(coord_emails), 0)


# ---------------------------------------------------------------------------
# STEP 6: Esito ECN APPROVED → destinatari deduplicati
# ---------------------------------------------------------------------------

@override_settings(EMAIL_BACKEND=LOCMEM)
class EcnApprovedEmailTest(TestCase):

    def setUp(self):
        qm_group = _qm_group()
        # Il proponente è anche Quality Manager per poter fare submit
        self.proposer = _user('ecn_app_proposer')
        self.proposer.groups.add(qm_group)
        self.doc_owner = _user('ecn_app_doc_owner')
        self.coordinator = _user('ecn_app_coord')
        self.member1 = _user('ecn_app_member1')
        self.member2 = _user('ecn_app_member2')
        self.qm = _user('ecn_app_qm')
        self.qm.groups.add(qm_group)

        self.doc = _make_document(owner=self.doc_owner, code='D-ECN-APP')
        self.version = _make_version_approved(self.doc, self.doc_owner, '00', 0)
        self.ecn = _make_ecn(self.doc, self.version, self.proposer, code='ECN-APP-01')
        # Configura CCB e invia
        set_change_notice_approvers(
            self.ecn, [self.member1], policy=ChangeNotice.CCBPolicy.ANY,
        )
        self.ecn.ccb_coordinator = self.coordinator
        self.ecn.save(update_fields=['ccb_coordinator'])
        submit_change_notice(self.ecn, self.proposer)

    def test_approved_all_recipients_notified(self):
        """ECN approvato → proponente, doc_owner, coordinator, membri CCB, QM ricevono email."""
        mail.outbox.clear()
        approve_change_notice(self.ecn, self.member1, ccb_class='class1')

        recipients = [m.to[0] for m in mail.outbox]
        self.assertIn(self.proposer.email, recipients)
        self.assertIn(self.doc_owner.email, recipients)
        self.assertIn(self.coordinator.email, recipients)
        self.assertIn(self.member1.email, recipients)
        self.assertIn(self.qm.email, recipients)

    def test_approved_no_duplicates(self):
        """ECN approvato → nessun duplicato email per lo stesso utente."""
        mail.outbox.clear()
        approve_change_notice(self.ecn, self.member1, ccb_class='class1')

        all_recipients = [m.to[0] for m in mail.outbox]
        # Verifica unicità
        self.assertEqual(len(all_recipients), len(set(all_recipients)),
                         f"Duplicati trovati: {all_recipients}")

    def test_approved_message_contains_can_create_revision(self):
        """Email approvazione ECN al proponente contiene il messaggio sulla revisione."""
        mail.outbox.clear()
        approve_change_notice(self.ecn, self.member1, ccb_class='class1')

        proposer_mails = [m for m in mail.outbox if self.proposer.email in m.to]
        self.assertTrue(proposer_mails)
        self.assertIn('revisione', proposer_mails[0].body.lower())
