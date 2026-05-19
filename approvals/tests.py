import datetime

from django.contrib.auth.models import User
from django.core import mail
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse

from approvals.models import ApprovalRequest
from approvals.services import approve_version, reject_version
from documents.models import Document, DocumentVersion
from documents.services import create_new_revision, submit_version_for_approval

LOCMEM = 'django.core.mail.backends.locmem.EmailBackend'


def make_document(code='DOC-001', owner=None):
    return Document.objects.create(
        code=code,
        title='Documento di test',
        category=Document.Category.QUALITY,
        owner=owner,
        created_by=owner,
    )


@override_settings(EMAIL_BACKEND=LOCMEM)
class ApproveVersionTests(TestCase):

    def setUp(self):
        self.author = User.objects.create_user('author', password='pw')
        self.approver = User.objects.create_user('approver', password='pw')
        self.other = User.objects.create_user('other', password='pw')
        self.document = make_document(owner=self.author)

    def _make_in_approval(self, label, number):
        version = create_new_revision(self.document, self.author, label, number)
        req = submit_version_for_approval(version, self.author, [self.approver])
        return version, req

    def test_first_approval_sets_current_version(self):
        version, req = self._make_in_approval('A', 1)
        approve_version(req, self.approver)

        version.refresh_from_db()
        self.document.refresh_from_db()

        self.assertEqual(version.status, DocumentVersion.Status.APPROVED)
        self.assertTrue(version.is_current)
        self.assertEqual(self.document.current_version, version)

    def test_subsequent_approval_supersedes_previous(self):
        v1, req1 = self._make_in_approval('A', 1)
        approve_version(req1, self.approver)

        v2, req2 = self._make_in_approval('B', 2)
        approve_version(req2, self.approver)

        v1.refresh_from_db()
        v2.refresh_from_db()
        self.document.refresh_from_db()

        self.assertEqual(v1.status, DocumentVersion.Status.SUPERSEDED)
        self.assertFalse(v1.is_current)
        self.assertEqual(v2.status, DocumentVersion.Status.APPROVED)
        self.assertTrue(v2.is_current)
        self.assertEqual(self.document.current_version, v2)

        current_count = DocumentVersion.objects.filter(
            document=self.document, is_current=True
        ).count()
        self.assertEqual(current_count, 1)

    def test_non_approver_cannot_approve(self):
        _, req = self._make_in_approval('A', 1)
        with self.assertRaises(PermissionDenied):
            approve_version(req, self.other)

    def test_superuser_can_approve(self):
        superuser = User.objects.create_superuser('admin', password='pw')
        _, req = self._make_in_approval('A', 1)
        approve_version(req, superuser)
        req.refresh_from_db()
        self.assertEqual(req.status, ApprovalRequest.Status.APPROVED)

    def test_due_date_saved_on_approval_request(self):
        version = create_new_revision(self.document, self.author, 'C', 3)
        scadenza = datetime.date(2026, 6, 30)
        req = submit_version_for_approval(version, self.author, [self.approver], due_date=scadenza)
        self.assertEqual(req.due_date, scadenza)

    def test_due_date_none_by_default(self):
        version = create_new_revision(self.document, self.author, 'D', 4)
        req = submit_version_for_approval(version, self.author, [self.approver])
        self.assertIsNone(req.due_date)


@override_settings(EMAIL_BACKEND=LOCMEM)
class RejectVersionTests(TestCase):

    def setUp(self):
        self.author = User.objects.create_user('author', password='pw')
        self.approver = User.objects.create_user('approver', password='pw')
        self.other = User.objects.create_user('other', password='pw')
        self.document = make_document(owner=self.author)

    def _make_in_approval(self, label='A', number=1):
        version = create_new_revision(self.document, self.author, label, number)
        req = submit_version_for_approval(version, self.author, [self.approver])
        return version, req

    def test_reject_sets_rejected_status(self):
        version, req = self._make_in_approval()
        reject_version(req, self.approver, 'Non conforme')
        version.refresh_from_db()
        self.assertEqual(version.status, DocumentVersion.Status.REJECTED)

    def test_reject_stores_reason(self):
        version, req = self._make_in_approval()
        reject_version(req, self.approver, 'Non conforme ai requisiti')
        version.refresh_from_db()
        self.assertEqual(version.rejection_reason, 'Non conforme ai requisiti')

    def test_reject_does_not_change_current_version(self):
        v1 = create_new_revision(self.document, self.author, 'A', 1)
        req1 = submit_version_for_approval(v1, self.author, [self.approver])
        approve_version(req1, self.approver)

        v2 = create_new_revision(self.document, self.author, 'B', 2)
        req2 = submit_version_for_approval(v2, self.author, [self.approver])
        reject_version(req2, self.approver, 'Non conforme')

        self.document.refresh_from_db()
        self.assertEqual(self.document.current_version, v1)

    def test_reject_requires_reason(self):
        _, req = self._make_in_approval()
        with self.assertRaises(ValidationError):
            reject_version(req, self.approver, '')

    def test_non_approver_cannot_reject(self):
        _, req = self._make_in_approval()
        with self.assertRaises(PermissionDenied):
            reject_version(req, self.other, 'qualsiasi motivo')


@override_settings(EMAIL_BACKEND=LOCMEM)
class ApprovalEmailTests(TestCase):
    """Verifica che approve_version e reject_version inviino email e creino NotificationLog."""

    def setUp(self):
        mail.outbox = []
        self.author = User.objects.create_user(
            'author', email='author@example.com', password='pw',
        )
        self.approver = User.objects.create_user(
            'approver', email='approver@example.com', password='pw',
        )
        self.document = make_document(owner=self.author)

    def _make_in_approval(self, label='A', number=1):
        version = create_new_revision(self.document, self.author, label, number)
        req = submit_version_for_approval(version, self.author, [self.approver])
        mail.outbox = []  # azzera dopo submit per isolare il test
        return version, req

    def test_approve_sends_email_to_author(self):
        _, req = self._make_in_approval()
        approve_version(req, self.approver)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn(self.author.email, mail.outbox[0].to)

    def test_approve_email_contains_document_code(self):
        _, req = self._make_in_approval()
        approve_version(req, self.approver)
        self.assertIn(self.document.code, mail.outbox[0].body)

    def test_approve_creates_notification_log(self):
        from notifications.models import NotificationLog
        _, req = self._make_in_approval()
        approve_version(req, self.approver)
        self.assertTrue(NotificationLog.objects.filter(is_sent=True).exists())

    def test_reject_sends_email_to_author(self):
        _, req = self._make_in_approval()
        reject_version(req, self.approver, 'Sezione 3 incompleta')
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn(self.author.email, mail.outbox[0].to)

    def test_reject_email_contains_rejection_reason(self):
        _, req = self._make_in_approval()
        reject_version(req, self.approver, 'Sezione 3 incompleta')
        self.assertIn('Sezione 3 incompleta', mail.outbox[0].body)

    def test_reject_creates_notification_log(self):
        from notifications.models import NotificationLog
        _, req = self._make_in_approval()
        reject_version(req, self.approver, 'Sezione 3 incompleta')
        self.assertTrue(NotificationLog.objects.filter(is_sent=True).exists())


@override_settings(EMAIL_BACKEND=LOCMEM)
class ApprovalViewTests(TestCase):
    """Verifica le view di approvazione: accesso, azioni approve/reject."""

    def setUp(self):
        mail.outbox = []
        self.author = User.objects.create_user('author', email='a@t.com', password='pw')
        self.approver = User.objects.create_user('approver', email='ap@t.com', password='pw')
        self.other = User.objects.create_user('other', email='o@t.com', password='pw')
        self.document = make_document(owner=self.author)

    def _make_pending(self, label='A', number=1):
        version = create_new_revision(self.document, self.author, label, number)
        req = submit_version_for_approval(version, self.author, [self.approver])
        mail.outbox = []
        return version, req

    def test_approver_sees_assigned_request_in_queue(self):
        self._make_pending()
        self.client.login(username='approver', password='pw')
        response = self.client.get(reverse('approval_queue'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.document.code)

    def test_unassigned_user_gets_403_on_detail(self):
        _, req = self._make_pending()
        self.client.login(username='other', password='pw')
        response = self.client.get(reverse('approval_detail', args=[req.pk]))
        self.assertEqual(response.status_code, 403)

    def test_approve_from_ui_changes_status(self):
        _, req = self._make_pending()
        self.client.login(username='approver', password='pw')
        response = self.client.post(
            reverse('approval_detail', args=[req.pk]),
            {'action': 'approve', 'comment': ''},
        )
        self.assertRedirects(response, reverse('approval_queue'))
        req.refresh_from_db()
        self.assertEqual(req.status, ApprovalRequest.Status.APPROVED)

    def test_reject_from_ui_requires_reason(self):
        _, req = self._make_pending()
        self.client.login(username='approver', password='pw')
        response = self.client.post(
            reverse('approval_detail', args=[req.pk]),
            {'action': 'reject', 'rejection_reason': ''},
        )
        self.assertEqual(response.status_code, 200)  # resta sulla pagina
        req.refresh_from_db()
        self.assertEqual(req.status, ApprovalRequest.Status.PENDING)  # invariato

    def test_reject_from_ui_with_reason_changes_status(self):
        _, req = self._make_pending()
        self.client.login(username='approver', password='pw')
        response = self.client.post(
            reverse('approval_detail', args=[req.pk]),
            {'action': 'reject', 'rejection_reason': 'Documento incompleto'},
        )
        self.assertRedirects(response, reverse('approval_queue'))
        req.refresh_from_db()
        self.assertEqual(req.status, ApprovalRequest.Status.REJECTED)


# ---------------------------------------------------------------------------
# Policy-aware tests
# ---------------------------------------------------------------------------

@override_settings(EMAIL_BACKEND=LOCMEM)
class PolicyAnyTests(TestCase):
    """Policy ANY: basta un approvatore qualsiasi."""

    def setUp(self):
        self.author = User.objects.create_user('pa_author', password='pw')
        self.a1 = User.objects.create_user('pa_a1', password='pw')
        self.a2 = User.objects.create_user('pa_a2', password='pw')
        self.doc = make_document(code='PA-DOC', owner=self.author)

    def _make_pending(self):
        version = create_new_revision(self.doc, self.author, '00', 0)
        req = submit_version_for_approval(
            version, self.author, [self.a1, self.a2], approval_policy='any'
        )
        return version, req

    def test_first_approve_finalizes_immediately(self):
        version, req = self._make_pending()
        approve_version(req, self.a1)
        req.refresh_from_db()
        version.refresh_from_db()
        self.doc.refresh_from_db()
        self.assertEqual(req.status, ApprovalRequest.Status.APPROVED)
        self.assertEqual(version.status, DocumentVersion.Status.APPROVED)
        self.assertTrue(version.is_current)
        self.assertEqual(self.doc.current_version, version)

    def test_any_reject_sets_rejected(self):
        version, req = self._make_pending()
        reject_version(req, self.a2, 'Non conforme')
        req.refresh_from_db()
        version.refresh_from_db()
        self.assertEqual(req.status, ApprovalRequest.Status.REJECTED)
        self.assertEqual(version.status, DocumentVersion.Status.REJECTED)


@override_settings(EMAIL_BACKEND=LOCMEM)
class PolicyAllTests(TestCase):
    """Policy ALL: devono approvare tutti."""

    def setUp(self):
        self.author = User.objects.create_user('pl_author', password='pw')
        self.a1 = User.objects.create_user('pl_a1', password='pw')
        self.a2 = User.objects.create_user('pl_a2', password='pw')
        self.doc = make_document(code='PL-DOC', owner=self.author)

    def _make_pending(self):
        version = create_new_revision(self.doc, self.author, '00', 0)
        req = submit_version_for_approval(
            version, self.author, [self.a1, self.a2], approval_policy='all'
        )
        return version, req

    def test_first_approve_leaves_request_pending(self):
        version, req = self._make_pending()
        approve_version(req, self.a1)
        req.refresh_from_db()
        version.refresh_from_db()
        self.assertEqual(req.status, ApprovalRequest.Status.PENDING)
        self.assertEqual(version.status, DocumentVersion.Status.IN_APPROVAL)
        self.assertFalse(version.is_current)

    def test_first_approve_does_not_set_current_version(self):
        version, req = self._make_pending()
        approve_version(req, self.a1)
        self.doc.refresh_from_db()
        self.assertIsNone(self.doc.current_version)

    def test_all_approve_finalizes(self):
        version, req = self._make_pending()
        approve_version(req, self.a1)
        approve_version(req, self.a2)
        req.refresh_from_db()
        version.refresh_from_db()
        self.doc.refresh_from_db()
        self.assertEqual(req.status, ApprovalRequest.Status.APPROVED)
        self.assertEqual(version.status, DocumentVersion.Status.APPROVED)
        self.assertTrue(version.is_current)
        self.assertEqual(self.doc.current_version, version)

    def test_reject_by_first_approver_sets_rejected(self):
        version, req = self._make_pending()
        reject_version(req, self.a1, 'Non conforme')
        req.refresh_from_db()
        version.refresh_from_db()
        self.assertEqual(req.status, ApprovalRequest.Status.REJECTED)
        self.assertEqual(version.status, DocumentVersion.Status.REJECTED)

    def test_reject_does_not_change_current_version(self):
        # Approva una prima versione, poi crea e rifiuta una seconda
        v1 = create_new_revision(self.doc, self.author, '00', 0)
        req1 = submit_version_for_approval(v1, self.author, [self.a1], approval_policy='all')
        approve_version(req1, self.a1)

        v2 = create_new_revision(self.doc, self.author, '01', 1)
        req2 = submit_version_for_approval(v2, self.author, [self.a1, self.a2], approval_policy='all')
        reject_version(req2, self.a1, 'Non conforme')

        self.doc.refresh_from_db()
        self.assertEqual(self.doc.current_version, v1)


@override_settings(EMAIL_BACKEND=LOCMEM)
class PolicySequentialTests(TestCase):
    """Policy SEQUENTIAL: gli approvatori devono approvare nell'ordine definito."""

    def setUp(self):
        self.author = User.objects.create_user('ps_author', password='pw')
        self.a1 = User.objects.create_user('ps_a1', password='pw')
        self.a2 = User.objects.create_user('ps_a2', password='pw')
        self.doc = make_document(code='PS-DOC', owner=self.author)

    def _make_pending(self):
        # a1 ha order=0, a2 ha order=1 (nell'ordine della lista)
        version = create_new_revision(self.doc, self.author, '00', 0)
        req = submit_version_for_approval(
            version, self.author, [self.a1, self.a2], approval_policy='sequential'
        )
        return version, req

    def test_second_approver_cannot_approve_before_first(self):
        _, req = self._make_pending()
        with self.assertRaises(ValidationError):
            approve_version(req, self.a2)

    def test_first_approver_leaves_request_pending(self):
        version, req = self._make_pending()
        approve_version(req, self.a1)
        req.refresh_from_db()
        version.refresh_from_db()
        self.assertEqual(req.status, ApprovalRequest.Status.PENDING)
        self.assertEqual(version.status, DocumentVersion.Status.IN_APPROVAL)

    def test_both_approve_in_order_finalizes(self):
        version, req = self._make_pending()
        approve_version(req, self.a1)
        approve_version(req, self.a2)
        req.refresh_from_db()
        version.refresh_from_db()
        self.doc.refresh_from_db()
        self.assertEqual(req.status, ApprovalRequest.Status.APPROVED)
        self.assertEqual(version.status, DocumentVersion.Status.APPROVED)
        self.assertTrue(version.is_current)
        self.assertEqual(self.doc.current_version, version)

    def test_reject_in_sequential_sets_rejected(self):
        version, req = self._make_pending()
        reject_version(req, self.a1, 'Non conforme')
        req.refresh_from_db()
        version.refresh_from_db()
        self.assertEqual(req.status, ApprovalRequest.Status.REJECTED)
        self.assertEqual(version.status, DocumentVersion.Status.REJECTED)

    def test_later_approver_can_reject_out_of_order(self):
        """Un approvatore successivo può rifiutare anche prima del suo turno."""
        version, req = self._make_pending()
        reject_version(req, self.a2, 'Problema grave')
        req.refresh_from_db()
        self.assertEqual(req.status, ApprovalRequest.Status.REJECTED)


@override_settings(EMAIL_BACKEND=LOCMEM)
class DoubleDecisionTests(TestCase):
    """Lo stesso approvatore non può decidere due volte sulla stessa richiesta."""

    def setUp(self):
        self.author = User.objects.create_user('dd_author', password='pw')
        self.approver = User.objects.create_user('dd_approver', password='pw')
        self.a2 = User.objects.create_user('dd_a2', password='pw')
        self.doc = make_document(code='DD-DOC', owner=self.author)

    def test_double_approve_raises(self):
        version = create_new_revision(self.doc, self.author, '00', 0)
        req = submit_version_for_approval(
            version, self.author, [self.approver, self.a2], approval_policy='all'
        )
        approve_version(req, self.approver)
        with self.assertRaises(ValidationError):
            approve_version(req, self.approver)

    def test_double_reject_raises(self):
        version = create_new_revision(self.doc, self.author, '00', 0)
        req = submit_version_for_approval(
            version, self.author, [self.approver, self.a2], approval_policy='all'
        )
        reject_version(req, self.approver, 'Motivo')
        # La richiesta è già REJECTED, quindi la seconda chiamata solleva per stato
        with self.assertRaises(ValidationError):
            reject_version(req, self.approver, 'Altro motivo')

    def test_approve_then_reject_raises(self):
        """Un approvatore che ha già approvato non può poi rifiutare (decisione già registrata)."""
        version = create_new_revision(self.doc, self.author, '00', 0)
        req = submit_version_for_approval(
            version, self.author, [self.approver, self.a2], approval_policy='all'
        )
        approve_version(req, self.approver)
        # Il secondo reject solleva per decisione già presente
        with self.assertRaises(ValidationError):
            reject_version(req, self.approver, 'Ripensamento')


@override_settings(EMAIL_BACKEND=LOCMEM)
class SuperuserOverrideTests(TestCase):
    """Superuser può approvare/rifiutare anche se non assegnato; l'approvazione è sempre finale."""

    def setUp(self):
        self.author = User.objects.create_user('su_author', password='pw')
        self.approver = User.objects.create_user('su_approver', password='pw')
        self.superuser = User.objects.create_superuser('su_admin', password='pw')
        self.doc = make_document(code='SU-DOC', owner=self.author)

    def test_superuser_not_assigned_can_approve(self):
        version = create_new_revision(self.doc, self.author, '00', 0)
        req = submit_version_for_approval(
            version, self.author, [self.approver], approval_policy='all'
        )
        approve_version(req, self.superuser)
        req.refresh_from_db()
        self.assertEqual(req.status, ApprovalRequest.Status.APPROVED)

    def test_superuser_override_with_all_policy_multiple_approvers(self):
        """Superuser approva definitivamente anche con ALL e più approvatori non ancora decisi."""
        a2 = User.objects.create_user('su_a2', password='pw')
        version = create_new_revision(self.doc, self.author, '00', 0)
        req = submit_version_for_approval(
            version, self.author, [self.approver, a2], approval_policy='all'
        )
        approve_version(req, self.superuser)
        req.refresh_from_db()
        version.refresh_from_db()
        self.assertEqual(req.status, ApprovalRequest.Status.APPROVED)
        self.assertEqual(version.status, DocumentVersion.Status.APPROVED)


@override_settings(EMAIL_BACKEND=LOCMEM)
class PolicyUITests(TestCase):
    """Test UI: submit salva approval_policy; detail mostra messaggi approvazione parziale/finale."""

    def setUp(self):
        from django.contrib.auth.models import Group
        mail.outbox = []
        self.author = User.objects.create_user('ui_author', email='a@t.com', password='pw')
        self.a1 = User.objects.create_user('ui_a1', email='a1@t.com', password='pw')
        self.a2 = User.objects.create_user('ui_a2', email='a2@t.com', password='pw')
        Group.objects.get_or_create(name='Document Authors')[0].user_set.add(self.author)
        self.doc = make_document(code='UI-DOC', owner=self.author)

    def test_submit_form_saves_approval_policy(self):
        version = create_new_revision(self.doc, self.author, '00', 0)
        self.client.login(username='ui_author', password='pw')
        self.client.post(
            reverse('version_submit', args=[version.pk]),
            {
                'approver-TOTAL_FORMS': '2',
                'approver-INITIAL_FORMS': '0',
                'approver-MIN_NUM_FORMS': '0',
                'approver-MAX_NUM_FORMS': '1000',
                'approver-0-approver': str(self.a1.pk),
                'approver-1-approver': str(self.a2.pk),
                'approval_policy': 'any',
            },
        )
        from approvals.models import ApprovalRequest
        req = ApprovalRequest.objects.get(document_version=version)
        self.assertEqual(req.approval_policy, 'any')

    def test_partial_approval_message_shown(self):
        """Con policy ALL e due approvatori, dopo il primo approve il messaggio è 'ancora in attesa'."""
        version = create_new_revision(self.doc, self.author, '00', 0)
        req = submit_version_for_approval(
            version, self.author, [self.a1, self.a2], approval_policy='all'
        )
        self.client.login(username='ui_a1', password='pw')
        response = self.client.post(
            reverse('approval_detail', args=[req.pk]),
            {'action': 'approve', 'comment': ''},
            follow=True,
        )
        self.assertContains(response, 'ancora in attesa')

    def test_final_approval_message_shown(self):
        """Con policy ALL e un solo approvatore, dopo approve il messaggio è 'Documento approvato'."""
        version = create_new_revision(self.doc, self.author, '00', 0)
        req = submit_version_for_approval(
            version, self.author, [self.a1], approval_policy='all'
        )
        self.client.login(username='ui_a1', password='pw')
        response = self.client.post(
            reverse('approval_detail', args=[req.pk]),
            {'action': 'approve', 'comment': ''},
            follow=True,
        )
        self.assertContains(response, 'approvato')
