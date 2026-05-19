import datetime

from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase
from django.contrib.auth.models import User

from approvals.models import ApprovalRequest
from approvals.services import approve_version, reject_version
from documents.models import Document, DocumentVersion
from documents.services import create_new_revision, submit_version_for_approval


def make_document(code='DOC-001', owner=None):
    return Document.objects.create(
        code=code,
        title='Documento di test',
        category=Document.Category.QUALITY,
        owner=owner,
        created_by=owner,
    )


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
