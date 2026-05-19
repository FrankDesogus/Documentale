from django.core.exceptions import ValidationError
from django.test import TestCase
from django.contrib.auth.models import User

from documents.models import Document, DocumentVersion
from documents.services import (
    create_new_revision,
    submit_version_for_approval,
    reopen_rejected_version_as_draft,
)


def make_document(code='DOC-001', owner=None):
    return Document.objects.create(
        code=code,
        title='Documento di test',
        category=Document.Category.QUALITY,
        owner=owner,
        created_by=owner,
    )


class CreateNewRevisionTests(TestCase):

    def setUp(self):
        self.author = User.objects.create_user('author', password='pw')
        self.approver = User.objects.create_user('approver', password='pw')
        self.document = make_document(owner=self.author)

    def test_creates_draft(self):
        version = create_new_revision(self.document, self.author, 'A', 1)
        self.assertEqual(version.status, DocumentVersion.Status.DRAFT)

    def test_is_current_false(self):
        version = create_new_revision(self.document, self.author, 'A', 1)
        self.assertFalse(version.is_current)

    def test_replaces_version_points_to_previous_current(self):
        v1 = create_new_revision(self.document, self.author, 'A', 1)
        # Approva v1 manualmente per renderla current
        v1.status = DocumentVersion.Status.APPROVED
        v1.is_current = True
        v1.save(update_fields=['status', 'is_current'])
        self.document.current_version = v1
        self.document.save(update_fields=['current_version'])

        v2 = create_new_revision(self.document, self.author, 'B', 2)
        self.assertEqual(v2.replaces_version, v1)

    def test_replaces_version_none_when_no_current(self):
        version = create_new_revision(self.document, self.author, 'A', 1)
        self.assertIsNone(version.replaces_version)

    def test_duplicate_revision_label_raises(self):
        create_new_revision(self.document, self.author, 'A', 1)
        with self.assertRaises(ValidationError):
            create_new_revision(self.document, self.author, 'A', 2)

    def test_duplicate_revision_number_raises(self):
        create_new_revision(self.document, self.author, 'A', 1)
        with self.assertRaises(ValidationError):
            create_new_revision(self.document, self.author, 'B', 1)

    def test_inactive_document_raises(self):
        self.document.status = Document.Status.OBSOLETE
        self.document.save(update_fields=['status'])
        with self.assertRaises(ValidationError):
            create_new_revision(self.document, self.author, 'A', 1)


class SubmitForApprovalTests(TestCase):

    def setUp(self):
        self.author = User.objects.create_user('author', password='pw')
        self.approver = User.objects.create_user('approver', password='pw')
        self.document = make_document(owner=self.author)
        self.version = create_new_revision(self.document, self.author, 'A', 1)

    def test_transitions_to_in_approval(self):
        submit_version_for_approval(self.version, self.author, [self.approver])
        self.version.refresh_from_db()
        self.assertEqual(self.version.status, DocumentVersion.Status.IN_APPROVAL)

    def test_creates_pending_approval_request(self):
        from approvals.models import ApprovalRequest
        req = submit_version_for_approval(self.version, self.author, [self.approver])
        self.assertEqual(req.status, ApprovalRequest.Status.PENDING)

    def test_creates_approver_records(self):
        req = submit_version_for_approval(self.version, self.author, [self.approver])
        self.assertEqual(req.approvers.count(), 1)
        self.assertEqual(req.approvers.first().approver, self.approver)

    def test_empty_approvers_raises(self):
        with self.assertRaises(ValidationError):
            submit_version_for_approval(self.version, self.author, [])

    def test_double_submit_raises(self):
        submit_version_for_approval(self.version, self.author, [self.approver])
        with self.assertRaises(ValidationError):
            self.version.refresh_from_db()
            submit_version_for_approval(self.version, self.author, [self.approver])


class ReopenRejectedTests(TestCase):

    def setUp(self):
        self.author = User.objects.create_user('author', password='pw')
        self.approver = User.objects.create_user('approver', password='pw')
        self.document = make_document(owner=self.author)
        self.version = create_new_revision(self.document, self.author, 'A', 1)
        req = submit_version_for_approval(self.version, self.author, [self.approver])
        from approvals.services import reject_version
        reject_version(req, self.approver, 'Non conforme')
        self.version.refresh_from_db()

    def test_reopen_sets_draft(self):
        reopen_rejected_version_as_draft(self.version, self.author)
        self.version.refresh_from_db()
        self.assertEqual(self.version.status, DocumentVersion.Status.DRAFT)

    def test_cannot_reopen_draft(self):
        other = create_new_revision(self.document, self.author, 'B', 2)
        with self.assertRaises(ValidationError):
            reopen_rejected_version_as_draft(other, self.author)
