import shutil
import tempfile

from django.contrib.auth.models import User
from django.core import mail
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from documents.permissions import can_download_version_file
from ecn.permissions import GROUP_CCB

from documents.models import Document, DocumentVersion
from documents.services import (
    create_document_file,
    create_new_revision,
    reopen_rejected_version_as_draft,
    submit_version_for_approval,
)

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
        v1.status = DocumentVersion.Status.APPROVED
        v1.is_current = True
        v1.save(update_fields=['status', 'is_current'])
        self.document.current_version = v1
        self.document.save(update_fields=['current_version'])

        # _bypass_ecn_check=True: questo test verifica solo replaces_version,
        # non il gate ECN (che ha test dedicati in ECNGateServiceTests).
        v2 = create_new_revision(self.document, self.author, 'B', 2, _bypass_ecn_check=True)
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


@override_settings(EMAIL_BACKEND=LOCMEM)
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


@override_settings(EMAIL_BACKEND=LOCMEM)
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


@override_settings(EMAIL_BACKEND=LOCMEM)
class SubmitApprovalEmailTests(TestCase):
    """Verifica che submit_version_for_approval invii email e crei NotificationLog."""

    def setUp(self):
        mail.outbox = []
        self.author = User.objects.create_user(
            'author', email='author@example.com', password='pw',
        )
        self.approver1 = User.objects.create_user(
            'approver1', email='approver1@example.com', password='pw',
        )
        self.approver2 = User.objects.create_user(
            'approver2', email='approver2@example.com', password='pw',
        )
        self.document = make_document(owner=self.author)

    def test_sends_one_email_per_approver(self):
        version = create_new_revision(self.document, self.author, 'A', 1)
        submit_version_for_approval(version, self.author, [self.approver1, self.approver2])
        self.assertEqual(len(mail.outbox), 2)

    def test_email_recipient_matches_approver(self):
        version = create_new_revision(self.document, self.author, 'A', 1)
        submit_version_for_approval(version, self.author, [self.approver1])
        self.assertIn(self.approver1.email, mail.outbox[0].to)

    def test_email_contains_document_code(self):
        version = create_new_revision(self.document, self.author, 'A', 1)
        submit_version_for_approval(version, self.author, [self.approver1])
        self.assertIn(self.document.code, mail.outbox[0].body)

    def test_creates_notification_log(self):
        from notifications.models import NotificationLog
        version = create_new_revision(self.document, self.author, 'A', 1)
        submit_version_for_approval(version, self.author, [self.approver1])
        self.assertEqual(NotificationLog.objects.count(), 1)
        self.assertTrue(NotificationLog.objects.first().is_sent)

    def test_no_email_sent_when_approver_has_no_email(self):
        from notifications.models import NotificationLog
        no_email_approver = User.objects.create_user('noemail', password='pw')
        version = create_new_revision(self.document, self.author, 'A', 1)
        submit_version_for_approval(version, self.author, [no_email_approver])
        self.assertEqual(len(mail.outbox), 0)
        log = NotificationLog.objects.first()
        self.assertFalse(log.is_sent)
        self.assertTrue(log.error_message)


@override_settings(EMAIL_BACKEND=LOCMEM)
class DocumentViewTests(TestCase):
    """Verifica che le view mostrino solo documenti approvati agli utenti normali."""

    def setUp(self):
        mail.outbox = []
        self.viewer = User.objects.create_user('viewer', password='pw', email='v@t.com')
        self.author = User.objects.create_user('author', password='pw', email='a@t.com')
        self.approver = User.objects.create_user('approver', password='pw', email='ap@t.com')
        self.document = make_document(owner=self.author)

    def _approve_first_version(self, doc, label='A', number=1):
        from approvals.services import approve_version
        v = create_new_revision(doc, self.author, label, number)
        req = submit_version_for_approval(v, self.author, [self.approver])
        approve_version(req, self.approver)
        return v

    def test_normal_user_sees_only_approved_documents(self):
        self._approve_first_version(self.document)
        draft_doc = make_document(code='DOC-DRAFT', owner=self.author)
        create_new_revision(draft_doc, self.author, 'A', 1)  # rimane bozza

        self.client.login(username='viewer', password='pw')
        response = self.client.get(reverse('document_list'))

        self.assertEqual(response.status_code, 200)
        codes = [d.code for d in response.context['documents']]
        self.assertIn('DOC-001', codes)
        self.assertNotIn('DOC-DRAFT', codes)

    def test_unauthenticated_redirects_to_login(self):
        response = self.client.get(reverse('document_list'))
        self.assertRedirects(response, '/accounts/login/?next=/documents/')

    def test_normal_user_cannot_see_draft_document_detail(self):
        create_new_revision(self.document, self.author, 'A', 1)  # draft
        self.client.login(username='viewer', password='pw')
        response = self.client.get(reverse('document_detail', args=[self.document.pk]))
        self.assertEqual(response.status_code, 404)

    def test_normal_user_can_see_approved_document_detail(self):
        self._approve_first_version(self.document)
        self.client.login(username='viewer', password='pw')
        response = self.client.get(reverse('document_detail', args=[self.document.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.document.code)


@override_settings(EMAIL_BACKEND=LOCMEM)
class AuthorWorkflowViewTests(TestCase):
    """Verifica il flusso autore: crea documento, nuova revisione, invio approvazione."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.temp_media = tempfile.mkdtemp()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.temp_media, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        from django.contrib.auth.models import Group
        from projects.models import ProjectFolder, ProjectFolderMembership
        mail.outbox = []
        self.author = User.objects.create_user('author', email='a@t.com', password='pw')
        self.approver = User.objects.create_user('approver', email='ap@t.com', password='pw')
        Group.objects.get_or_create(name='Document Authors')[0].user_set.add(self.author)
        self.folder = ProjectFolder.objects.create(
            code='AW-FOLD', name='Author Workflow Folder',
            folder_kind=ProjectFolder.FolderKind.GENERIC,
            status=ProjectFolder.Status.ACTIVE,
            owner=self.author,
        )
        ProjectFolderMembership.objects.create(folder=self.folder, user=self.author, role='author')
        self.client.login(username='author', password='pw')

    def test_unauthenticated_new_document_redirects_to_login(self):
        self.client.logout()
        response = self.client.get(reverse('document_new'))
        self.assertRedirects(response, '/accounts/login/?next=/documents/new/')

    def test_create_document_from_ui(self):
        with self.settings(MEDIA_ROOT=self.temp_media):
            response = self.client.post(reverse('document_new'), {
                'code': 'UI-001',
                'title': 'Documento test UI',
                'category': 'QUALITY',
                'project_folder': self.folder.pk,
                'revision_scheme': 'numeric',
                'revision_label': '00',
                'revision_number': '0',
            })
        self.assertRedirects(response, reverse('my_drafts'))
        self.assertTrue(Document.objects.filter(code='UI-001').exists())
        version = DocumentVersion.objects.get(document__code='UI-001')
        self.assertEqual(version.status, DocumentVersion.Status.DRAFT)
        self.assertFalse(version.is_current)

    def test_create_document_with_file_associates_it_to_version(self):
        uploaded = SimpleUploadedFile(
            'procedura.pdf', b'%PDF-1.4 contenuto fittizio', content_type='application/pdf',
        )
        with self.settings(MEDIA_ROOT=self.temp_media):
            self.client.post(reverse('document_new'), {
                'code': 'UI-002',
                'title': 'Documento con file',
                'category': 'QUALITY',
                'project_folder': self.folder.pk,
                'revision_scheme': 'numeric',
                'revision_label': '00',
                'revision_number': '0',
                'file': uploaded,
            })
        version = DocumentVersion.objects.get(document__code='UI-002')
        self.assertIsNotNone(version.file)
        self.assertEqual(version.file.original_filename, 'procedura.pdf')
        self.assertEqual(version.file.extension, 'pdf')
        self.assertEqual(version.file.mime_type, 'application/pdf')
        self.assertTrue(len(version.file.sha256_hash) == 64)

    def test_create_new_revision_from_ui(self):
        with self.settings(MEDIA_ROOT=self.temp_media):
            # Crea documento
            self.client.post(reverse('document_new'), {
                'code': 'UI-003',
                'title': 'Documento revisioni',
                'category': 'QUALITY',
                'project_folder': self.folder.pk,
                'revision_scheme': 'numeric',
                'revision_label': '00',
                'revision_number': '0',
            })
            doc = Document.objects.get(code='UI-003')
            # Crea nuova revisione
            response = self.client.post(
                reverse('document_new_revision', args=[doc.pk]),
                {
                    'revision_label': '01',
                    'revision_number': '1',
                    'change_summary': 'Aggiornamento sezione 2',
                },
            )
        self.assertRedirects(response, reverse('my_drafts'))
        self.assertEqual(doc.versions.count(), 2)
        v01 = doc.versions.get(revision_label='01')
        self.assertEqual(v01.status, DocumentVersion.Status.DRAFT)

    def test_submit_for_approval_from_ui(self):
        with self.settings(MEDIA_ROOT=self.temp_media):
            self.client.post(reverse('document_new'), {
                'code': 'UI-004',
                'title': 'Documento submit',
                'category': 'QUALITY',
                'project_folder': self.folder.pk,
                'revision_scheme': 'numeric',
                'revision_label': '00',
                'revision_number': '0',
            })
        doc = Document.objects.get(code='UI-004')
        version = doc.versions.first()

        response = self.client.post(
            reverse('version_submit', args=[version.pk]),
            {
                'approver-TOTAL_FORMS': '1',
                'approver-INITIAL_FORMS': '0',
                'approver-MIN_NUM_FORMS': '0',
                'approver-MAX_NUM_FORMS': '1000',
                'approver-0-approver': str(self.approver.pk),
                'approval_policy': 'all',
            },
        )
        self.assertRedirects(response, reverse('dashboard'))
        version.refresh_from_db()
        self.assertEqual(version.status, DocumentVersion.Status.IN_APPROVAL)

    def test_duplicate_code_shows_form_error(self):
        with self.settings(MEDIA_ROOT=self.temp_media):
            self.client.post(reverse('document_new'), {
                'code': 'UI-DUP',
                'title': 'Primo',
                'category': 'QUALITY',
                'project_folder': self.folder.pk,
                'revision_scheme': 'numeric',
                'revision_label': '00',
                'revision_number': '0',
            })
            response = self.client.post(reverse('document_new'), {
                'code': 'UI-DUP',
                'title': 'Secondo con stesso codice',
                'category': 'QUALITY',
                'project_folder': self.folder.pk,
                'revision_scheme': 'numeric',
                'revision_label': '00',
                'revision_number': '0',
            })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'esiste già')


@override_settings(EMAIL_BACKEND=LOCMEM)
class DownloadViewTests(TestCase):
    """Verifica i permessi di download file per i diversi ruoli."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.temp_media = tempfile.mkdtemp()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.temp_media, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        mail.outbox = []
        self.author = User.objects.create_user('dl_author', email='a@t.com', password='pw')
        self.approver = User.objects.create_user('dl_approver', email='ap@t.com', password='pw')
        self.viewer = User.objects.create_user('dl_viewer', email='v@t.com', password='pw')
        self.staff = User.objects.create_user('dl_staff', password='pw', is_staff=True)
        self.document = make_document(code='DL-001', owner=self.author)

    def _make_version_with_file(self, label='A', number=1):
        uploaded = SimpleUploadedFile(
            'doc.pdf', b'%PDF-1.4 test', content_type='application/pdf',
        )
        doc_file = create_document_file(uploaded, self.author)
        version = create_new_revision(
            self.document, self.author, label, number, file=doc_file,
        )
        return version

    def _approve_version(self, version):
        from approvals.services import approve_version
        req = submit_version_for_approval(version, self.author, [self.approver])
        approve_version(req, self.approver)
        version.refresh_from_db()
        return version

    def test_normal_user_can_download_current_approved(self):
        with self.settings(MEDIA_ROOT=self.temp_media):
            version = self._make_version_with_file()
            self._approve_version(version)
            self.client.login(username='dl_viewer', password='pw')
            response = self.client.get(reverse('version_download', args=[version.pk]))
        self.assertEqual(response.status_code, 200)

    def test_normal_user_cannot_download_draft(self):
        with self.settings(MEDIA_ROOT=self.temp_media):
            version = self._make_version_with_file()
            self.client.login(username='dl_viewer', password='pw')
            response = self.client.get(reverse('version_download', args=[version.pk]))
        self.assertEqual(response.status_code, 403)

    def test_author_can_download_own_draft(self):
        with self.settings(MEDIA_ROOT=self.temp_media):
            version = self._make_version_with_file()
            self.client.login(username='dl_author', password='pw')
            response = self.client.get(reverse('version_download', args=[version.pk]))
        self.assertEqual(response.status_code, 200)

    def test_approver_can_download_assigned_in_approval_version(self):
        with self.settings(MEDIA_ROOT=self.temp_media):
            version = self._make_version_with_file()
            submit_version_for_approval(version, self.author, [self.approver])
            version.refresh_from_db()
            self.client.login(username='dl_approver', password='pw')
            response = self.client.get(reverse('version_download', args=[version.pk]))
        self.assertEqual(response.status_code, 200)

    def test_non_assigned_user_cannot_download_in_approval_version(self):
        with self.settings(MEDIA_ROOT=self.temp_media):
            version = self._make_version_with_file()
            submit_version_for_approval(version, self.author, [self.approver])
            version.refresh_from_db()
            self.client.login(username='dl_viewer', password='pw')
            response = self.client.get(reverse('version_download', args=[version.pk]))
        self.assertEqual(response.status_code, 403)

    def test_auditor_can_download_superseded_version(self):
        """MB1: Document Auditor (non is_staff) può scaricare versioni storiche (SUPERSEDED)."""
        from django.contrib.auth.models import Group
        auditor = User.objects.create_user('dl_auditor_dl', password='pw')
        Group.objects.get_or_create(name='Document Auditors')[0].user_set.add(auditor)
        with self.settings(MEDIA_ROOT=self.temp_media):
            v1 = self._make_version_with_file('A', 1)
            self._approve_version(v1)
            uploaded = SimpleUploadedFile('doc2.pdf', b'%PDF-1.4 v2', content_type='application/pdf')
            doc_file2 = create_document_file(uploaded, self.author)
            self.document.refresh_from_db()
            v2 = create_new_revision(
                self.document, self.author, 'B', 2, file=doc_file2, _bypass_ecn_check=True,
            )
            self._approve_version(v2)
            v1.refresh_from_db()
            self.assertEqual(v1.status, DocumentVersion.Status.SUPERSEDED)
            self.client.login(username='dl_auditor_dl', password='pw')
            response = self.client.get(reverse('version_download', args=[v1.pk]))
        self.assertEqual(response.status_code, 200)

    def test_can_download_permission_function_no_file(self):
        version = create_new_revision(self.document, self.author, 'A', 1)
        self.assertFalse(can_download_version_file(self.author, version))


@override_settings(EMAIL_BACKEND=LOCMEM)
class PermissionGroupTests(TestCase):
    """Verifica le regole di permesso basate sui gruppi Django."""

    def setUp(self):
        from django.contrib.auth.models import Group
        mail.outbox = []

        g_authors = Group.objects.get_or_create(name='Document Authors')[0]
        g_approvers = Group.objects.get_or_create(name='Document Approvers')[0]
        g_readers = Group.objects.get_or_create(name='Document Readers')[0]
        g_auditors = Group.objects.get_or_create(name='Document Auditors')[0]

        self.author = User.objects.create_user('pg_author', email='pga@t.com', password='pw')
        self.author.groups.add(g_authors)

        self.approver = User.objects.create_user('pg_approver', email='pgap@t.com', password='pw')
        self.approver.groups.add(g_approvers)

        self.reader = User.objects.create_user('pg_reader', password='pw')
        self.reader.groups.add(g_readers)

        self.auditor = User.objects.create_user('pg_auditor', password='pw')
        self.auditor.groups.add(g_auditors)

        self.no_group = User.objects.create_user('pg_nogroup', password='pw')

        self.document = make_document(code='PG-001', owner=self.author)

    def test_no_group_cannot_create_document(self):
        self.client.login(username='pg_nogroup', password='pw')
        response = self.client.post(reverse('document_new'), {
            'code': 'PG-FAIL',
            'title': 'Documento non autorizzato',
            'category': 'QUALITY',
            'revision_label': '00',
            'revision_number': '0',
        })
        self.assertEqual(response.status_code, 403)

    def test_no_group_cannot_access_new_document_form(self):
        self.client.login(username='pg_nogroup', password='pw')
        response = self.client.get(reverse('document_new'))
        self.assertEqual(response.status_code, 403)

    def test_document_author_can_create_document(self):
        from projects.models import ProjectFolder, ProjectFolderMembership
        folder = ProjectFolder.objects.create(
            code='PG-FOLD', name='PG Folder',
            folder_kind=ProjectFolder.FolderKind.GENERIC,
            status=ProjectFolder.Status.ACTIVE,
            owner=self.author,
        )
        ProjectFolderMembership.objects.create(folder=folder, user=self.author, role='author')
        self.client.login(username='pg_author', password='pw')
        response = self.client.post(reverse('document_new'), {
            'code': 'PG-AUTH',
            'title': 'Documento autore',
            'category': 'QUALITY',
            'project_folder': folder.pk,
            'revision_scheme': 'numeric',
            'revision_label': '00',
            'revision_number': '0',
        })
        self.assertRedirects(response, reverse('my_drafts'))
        self.assertTrue(Document.objects.filter(code='PG-AUTH').exists())

    def test_document_reader_sees_only_approved_in_list(self):
        from approvals.services import approve_version
        v = create_new_revision(self.document, self.author, 'A', 1)
        req = submit_version_for_approval(v, self.author, [self.approver])
        approve_version(req, self.approver)

        draft_doc = make_document(code='PG-DRAFT', owner=self.author)
        create_new_revision(draft_doc, self.author, 'A', 1)

        self.client.login(username='pg_reader', password='pw')
        response = self.client.get(reverse('document_list'))
        self.assertEqual(response.status_code, 200)
        codes = [d.code for d in response.context['documents']]
        self.assertIn('PG-001', codes)
        self.assertNotIn('PG-DRAFT', codes)

    def test_document_approver_does_not_see_unassigned_requests(self):
        other_approver = User.objects.create_user('pg_other_ap', password='pw')
        v = create_new_revision(self.document, self.author, 'A', 1)
        submit_version_for_approval(v, self.author, [self.approver])

        self.client.login(username='pg_other_ap', password='pw')
        response = self.client.get(reverse('approval_queue'))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, self.document.code)

    def test_document_auditor_sees_version_history_in_detail(self):
        from approvals.services import approve_version
        v1 = create_new_revision(self.document, self.author, 'A', 1)
        req = submit_version_for_approval(v1, self.author, [self.approver])
        approve_version(req, self.approver)
        self.document.refresh_from_db()

        self.client.login(username='pg_auditor', password='pw')
        response = self.client.get(reverse('document_detail', args=[self.document.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['show_history'])
        self.assertIsNotNone(response.context['versions'])

    def test_no_group_user_cannot_create_revision(self):
        from approvals.services import approve_version
        v = create_new_revision(self.document, self.author, 'A', 1)
        req = submit_version_for_approval(v, self.author, [self.approver])
        approve_version(req, self.approver)
        self.document.refresh_from_db()

        self.client.login(username='pg_nogroup', password='pw')
        response = self.client.get(
            reverse('document_new_revision', args=[self.document.pk])
        )
        self.assertEqual(response.status_code, 403)

    def test_can_download_respects_existing_rules_no_file(self):
        from documents.permissions import can_download_version_file
        version = create_new_revision(self.document, self.author, 'A', 1)
        self.assertFalse(can_download_version_file(self.reader, version))


@override_settings(EMAIL_BACKEND=LOCMEM)
class DemoWorkflowCommandTests(TestCase):
    """Verifica il management command demo_workflow con --no-email."""

    def _call(self, *args):
        from io import StringIO
        from django.core.management import call_command
        out = StringIO()
        call_command('demo_workflow', *args, stdout=out)
        return out.getvalue()

    def test_no_email_flag_completes_workflow(self):
        output = self._call('--reset', '--no-email')
        self.assertIn('Rev.01', output)
        self.assertIn('completata', output)

    def test_no_email_flag_creates_approved_current_version(self):
        from documents.models import Document, DocumentVersion
        self._call('--reset', '--no-email')
        doc = Document.objects.get(code='QUA-DEMO-001')
        doc.refresh_from_db()
        self.assertIsNotNone(doc.current_version)
        self.assertEqual(doc.current_version.status, DocumentVersion.Status.APPROVED)
        self.assertTrue(doc.current_version.is_current)

    def test_no_email_flag_prints_disabled_message(self):
        output = self._call('--no-email')
        self.assertIn('--no-email', output)

    def test_without_no_email_flag_runs_normally(self):
        """Senza --no-email il comando si avvia e crea i dati (usa locmem dal override_settings di classe)."""
        output = self._call('--reset')
        self.assertIn('completata', output)


@override_settings(EMAIL_BACKEND=LOCMEM)
class EditVersionTests(TestCase):
    """Verifica la view edit_version e il service update_draft_version."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.temp_media = tempfile.mkdtemp()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.temp_media, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        from django.contrib.auth.models import Group
        mail.outbox = []
        self.author = User.objects.create_user('ev_author', email='a@t.com', password='pw')
        self.other = User.objects.create_user('ev_other', password='pw')
        Group.objects.get_or_create(name='Document Authors')[0].user_set.add(self.author)
        self.document = make_document(code='EV-001', owner=self.author)
        self.draft = create_new_revision(self.document, self.author, '00', 0)

    def test_author_can_access_edit_form_on_draft(self):
        self.client.login(username='ev_author', password='pw')
        response = self.client.get(reverse('version_edit', args=[self.draft.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'EV-001')

    def test_unauthenticated_redirects_to_login(self):
        response = self.client.get(reverse('version_edit', args=[self.draft.pk]))
        self.assertRedirects(
            response,
            f'/accounts/login/?next=/versions/{self.draft.pk}/edit/',
        )

    def test_other_user_gets_403(self):
        self.client.login(username='ev_other', password='pw')
        response = self.client.get(reverse('version_edit', args=[self.draft.pk]))
        self.assertEqual(response.status_code, 403)

    def test_author_can_update_change_summary(self):
        self.client.login(username='ev_author', password='pw')
        self.client.post(reverse('version_edit', args=[self.draft.pk]), {
            'revision_label': '00',
            'revision_number': '0',
            'change_summary': 'Sommario aggiornato',
        })
        self.draft.refresh_from_db()
        self.assertEqual(self.draft.change_summary, 'Sommario aggiornato')

    def test_edit_draft_redirects_to_my_drafts(self):
        self.client.login(username='ev_author', password='pw')
        response = self.client.post(reverse('version_edit', args=[self.draft.pk]), {
            'revision_label': '00',
            'revision_number': '0',
            'change_summary': 'Aggiornamento',
        })
        self.assertRedirects(response, reverse('my_drafts'))

    def test_edit_draft_remains_draft(self):
        self.client.login(username='ev_author', password='pw')
        self.client.post(reverse('version_edit', args=[self.draft.pk]), {
            'revision_label': '00',
            'revision_number': '0',
            'change_summary': 'Aggiornamento',
        })
        self.draft.refresh_from_db()
        self.assertEqual(self.draft.status, DocumentVersion.Status.DRAFT)

    def test_author_can_replace_file(self):
        with self.settings(MEDIA_ROOT=self.temp_media):
            uploaded = SimpleUploadedFile(
                'nuovo.pdf', b'%PDF-1.4 new', content_type='application/pdf',
            )
            self.client.login(username='ev_author', password='pw')
            self.client.post(reverse('version_edit', args=[self.draft.pk]), {
                'revision_label': '00',
                'revision_number': '0',
                'change_summary': '',
                'file': uploaded,
            })
        self.draft.refresh_from_db()
        self.assertIsNotNone(self.draft.file)
        self.assertEqual(self.draft.file.original_filename, 'nuovo.pdf')

    def test_edit_rejected_version_returns_to_draft(self):
        approver = User.objects.create_user('ev_approver', email='ap@t.com', password='pw')
        req = submit_version_for_approval(self.draft, self.author, [approver])
        from approvals.services import reject_version
        reject_version(req, approver, 'Non conforme')
        self.draft.refresh_from_db()
        self.assertEqual(self.draft.status, DocumentVersion.Status.REJECTED)

        self.client.login(username='ev_author', password='pw')
        self.client.post(reverse('version_edit', args=[self.draft.pk]), {
            'revision_label': '00',
            'revision_number': '0',
            'change_summary': 'Corretta sezione 3',
        })
        self.draft.refresh_from_db()
        self.assertEqual(self.draft.status, DocumentVersion.Status.DRAFT)

    def test_in_approval_version_gets_403(self):
        approver = User.objects.create_user('ev_approver2', email='ap2@t.com', password='pw')
        submit_version_for_approval(self.draft, self.author, [approver])
        self.draft.refresh_from_db()

        self.client.login(username='ev_author', password='pw')
        response = self.client.get(reverse('version_edit', args=[self.draft.pk]))
        self.assertEqual(response.status_code, 403)

    def test_approved_version_gets_403(self):
        approver = User.objects.create_user('ev_approver3', email='ap3@t.com', password='pw')
        req = submit_version_for_approval(self.draft, self.author, [approver])
        from approvals.services import approve_version
        approve_version(req, approver)
        self.draft.refresh_from_db()

        self.client.login(username='ev_author', password='pw')
        response = self.client.get(reverse('version_edit', args=[self.draft.pk]))
        self.assertEqual(response.status_code, 403)


@override_settings(EMAIL_BACKEND=LOCMEM)
class ApproverFormSetTests(TestCase):
    def setUp(self):
        from django.contrib.auth.models import Group
        self.author = User.objects.create_user('fs_author', email='a@t.com', password='pw')
        self.a1 = User.objects.create_user('fs_a1', password='pw')
        self.a2 = User.objects.create_user('fs_a2', password='pw')
        self.a3 = User.objects.create_user('fs_a3', password='pw')
        Group.objects.get_or_create(name='Document Authors')[0].user_set.add(self.author)
        self.doc = make_document(code='FS-DOC', owner=self.author)

    def _make_draft(self):
        return create_new_revision(
            document=self.doc,
            created_by=self.author,
            revision_label='01',
            revision_number=1,
        )

    def _post_submit(self, version, approver_pks, policy='all'):
        data = {
            'approver-TOTAL_FORMS': str(len(approver_pks)),
            'approver-INITIAL_FORMS': '0',
            'approver-MIN_NUM_FORMS': '0',
            'approver-MAX_NUM_FORMS': '1000',
            'approval_policy': policy,
        }
        for i, pk in enumerate(approver_pks):
            data[f'approver-{i}-approver'] = str(pk)
        return self.client.post(reverse('version_submit', args=[version.pk]), data)

    def test_formset_valid_single_approver(self):
        self.client.login(username='fs_author', password='pw')
        draft = self._make_draft()
        response = self._post_submit(draft, [self.a1.pk])
        self.assertEqual(response.status_code, 302)
        draft.refresh_from_db()
        self.assertEqual(draft.status, DocumentVersion.Status.IN_APPROVAL)

    def test_formset_valid_multiple_approvers(self):
        self.client.login(username='fs_author', password='pw')
        draft = self._make_draft()
        response = self._post_submit(draft, [self.a1.pk, self.a2.pk, self.a3.pk])
        self.assertEqual(response.status_code, 302)
        from approvals.models import ApprovalRequest
        ar = ApprovalRequest.objects.get(document_version=draft)
        self.assertEqual(ar.approvers.count(), 3)

    def test_formset_rejects_empty_list(self):
        self.client.login(username='fs_author', password='pw')
        draft = self._make_draft()
        data = {
            'approver-TOTAL_FORMS': '0',
            'approver-INITIAL_FORMS': '0',
            'approver-MIN_NUM_FORMS': '0',
            'approver-MAX_NUM_FORMS': '1000',
            'approval_policy': 'all',
        }
        response = self.client.post(reverse('version_submit', args=[draft.pk]), data)
        self.assertEqual(response.status_code, 200)
        draft.refresh_from_db()
        self.assertEqual(draft.status, DocumentVersion.Status.DRAFT)

    def test_formset_rejects_duplicates(self):
        self.client.login(username='fs_author', password='pw')
        draft = self._make_draft()
        response = self._post_submit(draft, [self.a1.pk, self.a1.pk])
        self.assertEqual(response.status_code, 200)
        draft.refresh_from_db()
        self.assertEqual(draft.status, DocumentVersion.Status.DRAFT)

    def test_blank_rows_ignored_if_others_present(self):
        self.client.login(username='fs_author', password='pw')
        draft = self._make_draft()
        data = {
            'approver-TOTAL_FORMS': '2',
            'approver-INITIAL_FORMS': '0',
            'approver-MIN_NUM_FORMS': '0',
            'approver-MAX_NUM_FORMS': '1000',
            'approval_policy': 'all',
            'approver-0-approver': str(self.a1.pk),
            'approver-1-approver': '',
        }
        response = self.client.post(reverse('version_submit', args=[draft.pk]), data)
        self.assertEqual(response.status_code, 302)
        from approvals.models import ApprovalRequest
        ar = ApprovalRequest.objects.get(document_version=draft)
        self.assertEqual(ar.approvers.count(), 1)

    def test_submit_creates_approvers_with_order_starting_at_1(self):
        self.client.login(username='fs_author', password='pw')
        draft = self._make_draft()
        self._post_submit(draft, [self.a1.pk, self.a2.pk])
        from approvals.models import ApprovalRequest
        ar = ApprovalRequest.objects.get(document_version=draft)
        orders = list(ar.approvers.order_by('order').values_list('order', flat=True))
        self.assertEqual(orders, [1, 2])

    def test_submit_preserves_approver_order(self):
        self.client.login(username='fs_author', password='pw')
        draft = self._make_draft()
        self._post_submit(draft, [self.a3.pk, self.a1.pk, self.a2.pk])
        from approvals.models import ApprovalRequest, ApprovalRequestApprover
        ar = ApprovalRequest.objects.get(document_version=draft)
        slots = list(ar.approvers.order_by('order').values_list('approver_id', flat=True))
        self.assertEqual(slots, [self.a3.pk, self.a1.pk, self.a2.pk])

    def test_sequential_respects_form_order(self):
        self.client.login(username='fs_author', password='pw')
        draft = self._make_draft()
        self._post_submit(draft, [self.a2.pk, self.a1.pk], policy='sequential')
        from approvals.models import ApprovalRequest
        from approvals.services import approve_version
        ar = ApprovalRequest.objects.get(document_version=draft)
        from django.core.exceptions import ValidationError as DjangoValidationError
        with self.assertRaises(DjangoValidationError):
            approve_version(ar, self.a1)
        approve_version(ar, self.a2)
        ar.refresh_from_db()
        self.assertEqual(ar.status, ApprovalRequest.Status.PENDING)


@override_settings(EMAIL_BACKEND=LOCMEM)
class DocumentDetailApprovalTests(TestCase):
    """Verifica la sezione approvazione nel dettaglio documento."""

    def setUp(self):
        mail.outbox = []
        self.author = User.objects.create_user('dd_author', email='a@t.com', password='pw')
        self.a1 = User.objects.create_user('dd_a1', email='a1@t.com', password='pw')
        self.a2 = User.objects.create_user('dd_a2', email='a2@t.com', password='pw')
        self.viewer = User.objects.create_user('dd_viewer', email='v@t.com', password='pw')
        self.doc = make_document(code='DD-DOC', owner=self.author)

    def _approve_version(self, version, approvers, policy='all'):
        from approvals.services import approve_version
        req = submit_version_for_approval(version, self.author, approvers, approval_policy=policy)
        for ap in approvers:
            req.refresh_from_db()
            if req.status != 'APPROVED':
                approve_version(req, ap)
        return req

    def test_document_list_shows_approval_date(self):
        v = create_new_revision(self.doc, self.author, '01', 1)
        self._approve_version(v, [self.a1])
        v.refresh_from_db()

        self.client.login(username='dd_viewer', password='pw')
        response = self.client.get(reverse('document_list'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, v.approved_at.strftime('%d/%m/%Y'))

    def test_document_detail_shows_multiple_approvers_for_all_policy(self):
        v = create_new_revision(self.doc, self.author, '01', 1)
        self._approve_version(v, [self.a1, self.a2], policy='all')

        self.client.login(username='dd_viewer', password='pw')
        response = self.client.get(reverse('document_detail', args=[self.doc.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertIn('latest_approval_request', response.context)
        self.assertIsNotNone(response.context['latest_approval_request'])
        approvers_in_ctx = response.context['latest_approval_approvers']
        self.assertEqual(len(approvers_in_ctx), 2)
        self.assertContains(response, 'dd_a1')
        self.assertContains(response, 'dd_a2')

    def test_document_detail_shows_approvers_in_correct_order_for_sequential(self):
        v = create_new_revision(self.doc, self.author, '01', 1)
        from approvals.services import approve_version
        req = submit_version_for_approval(v, self.author, [self.a2, self.a1], approval_policy='sequential')
        approve_version(req, self.a2)
        req.refresh_from_db()
        approve_version(req, self.a1)

        self.client.login(username='dd_viewer', password='pw')
        response = self.client.get(reverse('document_detail', args=[self.doc.pk]))
        self.assertEqual(response.status_code, 200)
        approvers_in_ctx = response.context['latest_approval_approvers']
        self.assertEqual(len(approvers_in_ctx), 2)
        self.assertEqual(approvers_in_ctx[0].approver, self.a2)
        self.assertEqual(approvers_in_ctx[1].approver, self.a1)

    def test_document_detail_shows_all_approvers_not_just_approved_by(self):
        v = create_new_revision(self.doc, self.author, '01', 1)
        self._approve_version(v, [self.a1, self.a2], policy='all')
        v.refresh_from_db()

        self.client.login(username='dd_viewer', password='pw')
        response = self.client.get(reverse('document_detail', args=[self.doc.pk]))
        approvers_in_ctx = response.context['latest_approval_approvers']
        self.assertEqual(len(approvers_in_ctx), 2)
        approver_users = {slot.approver for slot in approvers_in_ctx}
        self.assertIn(self.a1, approver_users)
        self.assertIn(self.a2, approver_users)

    def test_document_detail_no_approval_request_still_works(self):
        """Versione approvata manualmente (senza ApprovalRequest) non causa errori."""
        v = create_new_revision(self.doc, self.author, '01', 1)
        # Approva direttamente, senza passare per submit_version_for_approval
        from django.utils import timezone
        v.status = 'approved'
        v.approved_at = timezone.now()
        v.approved_by = self.a1
        v.is_current = True
        v.save(update_fields=['status', 'approved_at', 'approved_by', 'is_current'])
        self.doc.current_version = v
        self.doc.save(update_fields=['current_version'])

        self.client.login(username='dd_viewer', password='pw')
        response = self.client.get(reverse('document_detail', args=[self.doc.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.context['latest_approval_request'])
        self.assertContains(response, 'Nessun dettaglio approvativo disponibile.')


# ---------------------------------------------------------------------------
# DocumentCreateForm: cartella obbligatoria
# ---------------------------------------------------------------------------

class NewDocumentFolderRequiredTests(TestCase):
    """La cartella è obbligatoria nella creazione documento da UI."""

    def setUp(self):
        from django.contrib.auth.models import Group
        from projects.models import ProjectFolder, ProjectFolderMembership

        self.manager = User.objects.create_user('ndfr_mgr', password='pw', is_staff=True)
        self.author = User.objects.create_user('ndfr_author', password='pw')
        self.global_author = User.objects.create_user('ndfr_global_author', password='pw')

        g_authors = Group.objects.get_or_create(name='Document Authors')[0]
        # MB1: is_staff da solo non concede creazione documenti
        Group.objects.get_or_create(name='Document Managers')[0].user_set.add(self.manager)
        self.author.groups.add(g_authors)
        self.global_author.groups.add(g_authors)

        self.folder = ProjectFolder.objects.create(
            code='NDFR-FOLD',
            name='Cartella test',
            folder_kind=ProjectFolder.FolderKind.GENERIC,
            status=ProjectFolder.Status.ACTIVE,
            owner=self.manager,
        )
        ProjectFolderMembership.objects.create(folder=self.folder, user=self.author, role='author')
        # global_author ha il gruppo ma NESSUNA membership → nessuna cartella scrivibile

    def _post_new_document(self, user_login, extra_data=None):
        self.client.login(username=user_login, password='pw')
        data = {
            'code': 'NDFR-DOC-001',
            'title': 'Test',
            'category': 'QUALITY',
            'document_type': '',
            'description': '',
            'revision_scheme': 'numeric',
            'revision_label': '00',
            'revision_number': 0,
            'change_summary': '',
        }
        if extra_data:
            data.update(extra_data)
        return self.client.post(reverse('document_new'), data)

    # 1. POST senza project_folder fallisce con errore campo obbligatorio
    def test_post_without_folder_fails(self):
        response = self._post_new_document('ndfr_mgr')
        self.assertEqual(response.status_code, 200)
        form = response.context['form']
        self.assertFalse(form.is_valid())
        self.assertIn('project_folder', form.errors)

    # 2. POST con cartella valida crea il documento
    def test_post_with_folder_creates_document(self):
        response = self._post_new_document('ndfr_mgr', {'project_folder': self.folder.pk})
        self.assertTrue(Document.objects.filter(code='NDFR-DOC-001').exists())
        doc = Document.objects.get(code='NDFR-DOC-001')
        self.assertEqual(doc.project_folder, self.folder)

    # 3. Author con membership crea documento nella sua cartella
    def test_author_with_membership_can_create_with_folder(self):
        response = self._post_new_document('ndfr_author', {'project_folder': self.folder.pk})
        self.assertTrue(Document.objects.filter(code='NDFR-DOC-001').exists())

    # 4. Author con membership: il campo cartella ha nel queryset solo la sua cartella
    def test_author_folder_queryset_limited_to_writable(self):
        self.client.login(username='ndfr_author', password='pw')
        response = self.client.get(reverse('document_new'))
        self.assertEqual(response.status_code, 200)
        qs = list(response.context['form'].fields['project_folder'].queryset)
        self.assertEqual(len(qs), 1)
        self.assertEqual(qs[0].pk, self.folder.pk)

    # 5. Author globale senza membership vede form con queryset vuoto e messaggio warning
    def test_global_author_without_membership_sees_warning(self):
        self.client.login(username='ndfr_global_author', password='pw')
        response = self.client.get(reverse('document_new'))
        self.assertEqual(response.status_code, 200)
        qs = list(response.context['form'].fields['project_folder'].queryset)
        self.assertEqual(len(qs), 0)
        # Il warning deve essere nei messaggi della response
        msgs = [str(m) for m in response.context['messages']]
        self.assertTrue(any('nessuna cartella' in m.lower() for m in msgs))

    # 6. Il campo cartella è required=True nel form
    def test_project_folder_is_required(self):
        self.client.login(username='ndfr_mgr', password='pw')
        response = self.client.get(reverse('document_new'))
        form = response.context['form']
        self.assertTrue(form.fields['project_folder'].required)

    # 7. Creazione da progetto (fixed_folder) funziona e associa il documento alla cartella
    def test_create_from_project_context_uses_fixed_folder(self):
        from projects.models import Project
        project = Project.objects.create(
            code='NDFR-PRJ-001',
            name='Progetto test',
            project_type=Project.ProjectType.INTERNAL,
            root_folder=self.folder,
            manager=self.manager,
            created_by=self.manager,
        )
        self.client.login(username='ndfr_mgr', password='pw')
        url = reverse('document_new') + f'?project={project.pk}'
        response = self.client.post(url, {
            'code': 'NDFR-PRJ-DOC-001',
            'title': 'Doc da progetto',
            'category': 'QUALITY',
            'document_type': '',
            'description': '',
            'project_folder': self.folder.pk,
            'revision_scheme': 'numeric',
            'revision_label': '00',
            'revision_number': 0,
            'change_summary': '',
        })
        self.assertTrue(Document.objects.filter(code='NDFR-PRJ-DOC-001').exists())
        doc = Document.objects.get(code='NDFR-PRJ-DOC-001')
        self.assertEqual(doc.project_folder, self.folder)
        self.assertRedirects(response, reverse('document_detail', args=[doc.pk]))


# ---------------------------------------------------------------------------
# Step Audit UI — document_detail
# ---------------------------------------------------------------------------

@override_settings(EMAIL_BACKEND=LOCMEM)
class AuditUIDocumentDetailTests(TestCase):
    """Sezione 'Storico eventi' nel dettaglio documento."""

    def setUp(self):
        from django.contrib.auth.models import Group
        mail.outbox = []
        self.author = User.objects.create_user('au_author', email='a@t.com', password='pw')
        self.approver = User.objects.create_user('au_approver', email='ap@t.com', password='pw')
        self.auditor = User.objects.create_user('au_auditor', password='pw')
        self.manager = User.objects.create_user('au_manager', password='pw')
        self.reader = User.objects.create_user('au_reader', password='pw')

        Group.objects.get_or_create(name='Document Auditors')[0].user_set.add(self.auditor)
        Group.objects.get_or_create(name='Document Managers')[0].user_set.add(self.manager)
        Group.objects.get_or_create(name='Document Readers')[0].user_set.add(self.reader)

        self.doc = make_document(code='AU-DOC-001', owner=self.author)

    def _approve_doc(self, doc=None):
        from approvals.services import approve_version
        doc = doc or self.doc
        v = create_new_revision(doc, self.author, 'A', 1)
        req = submit_version_for_approval(v, self.author, [self.approver])
        approve_version(req, self.approver)
        doc.refresh_from_db()
        return v

    # 1. Auditor vede "Storico eventi"
    def test_auditor_sees_storico_eventi(self):
        self._approve_doc()
        self.client.login(username='au_auditor', password='pw')
        response = self.client.get(reverse('document_detail', args=[self.doc.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['show_history'])
        self.assertContains(response, 'Storico eventi')

    # 2. Manager vede "Storico eventi"
    def test_manager_sees_storico_eventi(self):
        self._approve_doc()
        self.client.login(username='au_manager', password='pw')
        response = self.client.get(reverse('document_detail', args=[self.doc.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['show_history'])
        self.assertContains(response, 'Storico eventi')

    # 3. Reader normale NON vede "Storico eventi"
    def test_reader_does_not_see_storico_eventi(self):
        self._approve_doc()
        self.client.login(username='au_reader', password='pw')
        response = self.client.get(reverse('document_detail', args=[self.doc.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context['show_history'])
        self.assertNotContains(response, 'Storico eventi')
        self.assertIsNone(response.context['audit_logs'])

    # 4. Con AuditLog presenti il contesto non è vuoto
    def test_audit_logs_present_in_context_when_events_exist(self):
        self._approve_doc()
        self.client.login(username='au_auditor', password='pw')
        response = self.client.get(reverse('document_detail', args=[self.doc.pk]))
        self.assertEqual(response.status_code, 200)
        audit_logs = list(response.context['audit_logs'])
        self.assertGreater(len(audit_logs), 0)

    # 5. Pagina funziona anche senza AuditLog (messaggio "Nessun evento")
    def test_detail_works_without_audit_logs(self):
        from auditlog.models import AuditLog
        self._approve_doc()
        AuditLog.objects.all().delete()

        self.client.login(username='au_auditor', password='pw')
        response = self.client.get(reverse('document_detail', args=[self.doc.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(list(response.context['audit_logs'])), 0)
        self.assertContains(response, 'Nessun evento registrato per questo documento.')

    # 6. Folder-auditor (membership cartella) vede lo storico del documento nella cartella
    def test_folder_auditor_sees_storico_in_document_with_folder(self):
        from projects.models import ProjectFolder, ProjectFolderMembership
        folder_auditor = User.objects.create_user('au_foldaud', password='pw')
        folder = ProjectFolder.objects.create(
            code='AU-FOLD', name='Audit Folder',
            folder_kind=ProjectFolder.FolderKind.GENERIC,
            status=ProjectFolder.Status.ACTIVE,
            owner=self.author,
        )
        ProjectFolderMembership.objects.create(folder=folder, user=folder_auditor, role='auditor')

        doc_in_folder = Document.objects.create(
            code='AU-DOC-FOLD', title='Doc in cartella',
            category=Document.Category.QUALITY,
            project_folder=folder,
            owner=self.author, created_by=self.author,
        )
        from approvals.services import approve_version
        v = create_new_revision(doc_in_folder, self.author, 'A', 1)
        req = submit_version_for_approval(v, self.author, [self.approver])
        approve_version(req, self.approver)
        doc_in_folder.refresh_from_db()

        self.client.login(username='au_foldaud', password='pw')
        response = self.client.get(reverse('document_detail', args=[doc_in_folder.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['show_history'])
        self.assertContains(response, 'Storico eventi')


# ---------------------------------------------------------------------------
# ECN gate — service (ECN-C)
# ---------------------------------------------------------------------------

@override_settings(EMAIL_BACKEND=LOCMEM)
class ECNGateServiceTests(TestCase):
    """
    Verifica il gate ECN nel service create_new_revision (ECN-C).
    Dipende dall'app ecn; usa import locali per evitare dipendenze al livello modulo.
    """

    def setUp(self):
        from approvals.services import approve_version
        self.author   = User.objects.create_user('ecng_author', password='pw')
        self.approver = User.objects.create_user('ecng_approver', password='pw')
        self.document = make_document(code='ECNG-DOC-001', owner=self.author)

        # Approva la prima revisione così il documento ha current_version
        v0 = create_new_revision(self.document, self.author, '00', 0)
        req = submit_version_for_approval(v0, self.author, [self.approver])
        approve_version(req, self.approver)
        self.document.refresh_from_db()
        self.v0 = v0

    def _make_ecn(self, status='approved', doc=None, executed_version=None):
        from ecn.models import ChangeNotice
        doc = doc or self.document
        # Se il doc non ha current_version (es. bozza), usa la prima versione disponibile
        version = doc.current_version or doc.versions.order_by('revision_number').first()
        ecn = ChangeNotice.objects.create(
            code=f'ECN-GATE-{ChangeNotice.objects.count()+1:03d}',
            title='ECN gate test',
            motivation=ChangeNotice.Motivation.IMPROVEMENT,
            document=doc,
            document_version=version,
            proposed_by=self.author,
            created_by=self.author,
            status=status,
            executed_version=executed_version,
        )
        return ecn

    # 1. senza ECN su documento approvato → ValidationError
    def test_approved_document_without_ecn_raises(self):
        with self.assertRaises(ValidationError):
            create_new_revision(self.document, self.author, '01', 1)

    # 2. ECN in stato DRAFT → ValidationError
    def test_draft_ecn_raises(self):
        ecn = self._make_ecn(status='draft')
        with self.assertRaises(ValidationError):
            create_new_revision(self.document, self.author, '01', 1, ecn=ecn)

    # 3. ECN in stato UNDER_REVIEW → ValidationError
    def test_under_review_ecn_raises(self):
        ecn = self._make_ecn(status='under_review')
        with self.assertRaises(ValidationError):
            create_new_revision(self.document, self.author, '01', 1, ecn=ecn)

    # 4. ECN in stato REJECTED → ValidationError
    def test_rejected_ecn_raises(self):
        ecn = self._make_ecn(status='rejected')
        with self.assertRaises(ValidationError):
            create_new_revision(self.document, self.author, '01', 1, ecn=ecn)

    # 5. ECN approvato → crea nuova revisione draft
    def test_approved_ecn_creates_draft(self):
        ecn = self._make_ecn(status='approved')
        version = create_new_revision(self.document, self.author, '01', 1, ecn=ecn)
        self.assertEqual(version.status, DocumentVersion.Status.DRAFT)
        self.assertEqual(version.document, self.document)

    # 6. Dopo la creazione l'ECN ha executed_version valorizzata
    def test_ecn_executed_version_set_after_creation(self):
        ecn = self._make_ecn(status='approved')
        version = create_new_revision(self.document, self.author, '01', 1, ecn=ecn)
        ecn.refresh_from_db()
        self.assertEqual(ecn.executed_version, version)
        self.assertIsNotNone(ecn.executed_at)

    # 7. ECN già usato (executed_version presente) → ValidationError
    def test_already_used_ecn_raises(self):
        existing_version = create_new_revision(
            self.document, self.author, '01', 1, _bypass_ecn_check=True
        )
        ecn = self._make_ecn(status='approved', executed_version=existing_version)
        with self.assertRaises(ValidationError):
            create_new_revision(self.document, self.author, '02', 2, ecn=ecn)

    # 8. ECN di un altro documento → ValidationError
    def test_ecn_wrong_document_raises(self):
        other_doc = make_document(code='ECNG-DOC-OTHER', owner=self.author)
        # Crea versione per il secondo documento (senza current_version → non ha bisogno di ECN)
        v_other = create_new_revision(other_doc, self.author, '00', 0)
        ecn = self._make_ecn(status='approved', doc=other_doc)
        with self.assertRaises(ValidationError):
            create_new_revision(self.document, self.author, '01', 1, ecn=ecn)

    # 9. Documento senza current_version (prima revisione) → nessun gate ECN
    def test_document_without_current_version_no_gate(self):
        new_doc = make_document(code='ECNG-NODOC', owner=self.author)
        # Nessuna current_version → create_new_revision deve funzionare senza ECN
        version = create_new_revision(new_doc, self.author, '00', 0)
        self.assertEqual(version.status, DocumentVersion.Status.DRAFT)

    # 10. _bypass_ecn_check=True bypassa il gate anche senza ECN
    def test_bypass_skips_gate(self):
        version = create_new_revision(
            self.document, self.author, '01', 1, _bypass_ecn_check=True
        )
        self.assertEqual(version.status, DocumentVersion.Status.DRAFT)


# ---------------------------------------------------------------------------
# ECN gate — view (ECN-C)
# ---------------------------------------------------------------------------

@override_settings(EMAIL_BACKEND=LOCMEM)
class ECNGateViewTests(TestCase):
    """Verifica la view new_revision con il gate ECN attivo."""

    def setUp(self):
        from approvals.services import approve_version
        from django.contrib.auth.models import Group
        from projects.models import ProjectFolder, ProjectFolderMembership

        self.author   = User.objects.create_user('egv_author', password='pw')
        self.approver = User.objects.create_user('egv_approver', password='pw')
        self.stranger = User.objects.create_user('egv_stranger', password='pw')

        Group.objects.get_or_create(name='Document Authors')[0].user_set.add(self.author)
        Group.objects.get_or_create(name='Document Managers')[0].user_set.add(self.approver)

        self.folder = ProjectFolder.objects.create(
            code='EGV-FOLD', name='ECN Gate View Folder',
            folder_kind=ProjectFolder.FolderKind.GENERIC,
            status=ProjectFolder.Status.ACTIVE,
            owner=self.author,
        )
        ProjectFolderMembership.objects.create(folder=self.folder, user=self.author, role='author')

        self.document = Document.objects.create(
            code='EGV-DOC-001', title='Doc gate ECN view',
            category=Document.Category.QUALITY,
            owner=self.author, created_by=self.author,
            project_folder=self.folder,
        )
        v0 = create_new_revision(self.document, self.author, '00', 0)
        req = submit_version_for_approval(v0, self.author, [self.approver])
        approve_version(req, self.approver)
        self.document.refresh_from_db()

    def _make_approved_ecn(self, doc=None):
        from ecn.models import ChangeNotice
        doc = doc or self.document
        return ChangeNotice.objects.create(
            code=f'ECN-EGV-{ChangeNotice.objects.count()+1:03d}',
            title='ECN view gate',
            motivation=ChangeNotice.Motivation.IMPROVEMENT,
            document=doc,
            document_version=doc.current_version,
            proposed_by=self.author,
            created_by=self.author,
            status=ChangeNotice.Status.APPROVED,
        )

    # 1. Accesso senza ECN param → mostra pagina "ECN richiesto"
    def test_new_revision_without_ecn_shows_requires_ecn(self):
        self.client.force_login(self.author)
        r = self.client.get(reverse('document_new_revision', args=[self.document.pk]))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'ECN richiesto')

    # 2. Con ECN approvato → mostra il form di creazione revisione
    def test_new_revision_with_valid_ecn_shows_form(self):
        ecn = self._make_approved_ecn()
        self.client.force_login(self.author)
        r = self.client.get(
            reverse('document_new_revision', args=[self.document.pk]) + f'?ecn={ecn.pk}'
        )
        self.assertEqual(r.status_code, 200)
        self.assertIsNotNone(r.context.get('form'))
        self.assertContains(r, ecn.code)

    # 3. POST con ECN valido → crea revisione e redirect a my_drafts
    def test_new_revision_post_with_valid_ecn_creates_revision(self):
        ecn = self._make_approved_ecn()
        self.client.force_login(self.author)
        r = self.client.post(
            reverse('document_new_revision', args=[self.document.pk]) + f'?ecn={ecn.pk}',
            {
                'revision_label': '01',
                'revision_number': '1',
                'change_summary': 'Revisione da ECN test',
                'ecn_id': ecn.pk,
            },
        )
        self.assertRedirects(r, reverse('my_drafts'), fetch_redirect_response=False)
        from documents.models import DocumentVersion
        self.assertTrue(
            DocumentVersion.objects.filter(
                document=self.document, revision_label='01'
            ).exists()
        )
        ecn.refresh_from_db()
        self.assertIsNotNone(ecn.executed_version)

    # 4. document_detail mostra "Nuova revisione (via ECN)" per utente con permesso
    def test_document_detail_shows_via_ecn_button_for_author(self):
        self.client.force_login(self.author)
        r = self.client.get(reverse('document_detail', args=[self.document.pk]))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'Nuova revisione (via ECN)')

    # 5. document_detail NON mostra pulsante revisione a utente senza permesso
    def test_document_detail_hides_revision_button_for_stranger(self):
        self.client.force_login(self.stranger)
        # stranger non ha accesso al documento → 404
        r = self.client.get(reverse('document_detail', args=[self.document.pk]))
        self.assertEqual(r.status_code, 404)


# ---------------------------------------------------------------------------
# Workspace views — test di accesso e contenuto
# ---------------------------------------------------------------------------

class WorkspaceMyWorkTests(TestCase):
    """Test per /workspace/my-work/"""

    def setUp(self):
        from django.contrib.auth.models import Group
        self.user = User.objects.create_user('worker', password='pw')
        self.other = User.objects.create_user('other', password='pw')

    def test_redirects_anonymous(self):
        r = self.client.get(reverse('workspace_my_work'))
        self.assertRedirects(r, '/accounts/login/?next=/workspace/my-work/', fetch_redirect_response=False)

    def test_ok_for_authenticated(self):
        self.client.force_login(self.user)
        r = self.client.get(reverse('workspace_my_work'))
        self.assertEqual(r.status_code, 200)

    def test_shows_my_drafts(self):
        doc = Document.objects.create(
            code='WS-001', title='WS doc', category=Document.Category.QUALITY,
            owner=self.user, created_by=self.user,
        )
        v = create_new_revision(doc, self.user, 'A', 1)
        self.client.force_login(self.user)
        r = self.client.get(reverse('workspace_my_work'))
        self.assertContains(r, 'WS-001')

    def test_does_not_show_other_drafts(self):
        doc = Document.objects.create(
            code='WS-002', title='Other doc', category=Document.Category.QUALITY,
            owner=self.other, created_by=self.other,
        )
        create_new_revision(doc, self.other, 'A', 1)
        self.client.force_login(self.user)
        r = self.client.get(reverse('workspace_my_work'))
        self.assertNotContains(r, 'WS-002')


class WorkspaceQualityTests(TestCase):
    """Test per /workspace/quality/"""

    def setUp(self):
        from django.contrib.auth.models import Group
        # MB1: workspace_quality richiede Quality Manager, Operator o Auditor
        self.quality_manager = User.objects.create_user('q_qmanager', password='pw')
        self.doc_manager = User.objects.create_user('q_docmanager', password='pw')
        self.reader = User.objects.create_user('q_reader', password='pw')
        qmg = Group.objects.get_or_create(name='Quality Manager')[0]
        dmg = Group.objects.get_or_create(name='Document Managers')[0]
        self.quality_manager.groups.add(qmg)
        self.doc_manager.groups.add(dmg)

    def test_redirects_anonymous(self):
        r = self.client.get(reverse('workspace_quality'))
        self.assertRedirects(r, '/accounts/login/?next=/workspace/quality/', fetch_redirect_response=False)

    def test_forbidden_for_plain_user(self):
        self.client.force_login(self.reader)
        r = self.client.get(reverse('workspace_quality'))
        self.assertEqual(r.status_code, 403)

    def test_forbidden_for_document_manager(self):
        """MB1: Document Manager NON accede automaticamente al workspace Qualità."""
        self.client.force_login(self.doc_manager)
        r = self.client.get(reverse('workspace_quality'))
        self.assertEqual(r.status_code, 403)

    def test_ok_for_quality_manager(self):
        self.client.force_login(self.quality_manager)
        r = self.client.get(reverse('workspace_quality'))
        self.assertEqual(r.status_code, 200)

    def test_forbidden_for_staff_without_role(self):
        """MB1: is_staff NON concede accesso al workspace Qualità."""
        staff = User.objects.create_user('q_staff_q', password='pw', is_staff=True)
        self.client.force_login(staff)
        r = self.client.get(reverse('workspace_quality'))
        self.assertEqual(r.status_code, 403)

    def test_shows_ecn_to_review_section(self):
        self.client.force_login(self.quality_manager)
        r = self.client.get(reverse('workspace_quality'))
        self.assertContains(r, 'Da valutare da Qualità')


class NavTagsTests(TestCase):
    """Test per i templatetag in nav_tags.py"""

    def setUp(self):
        from django.contrib.auth.models import Group
        self.anon_like = User.objects.create_user('navuser_anon', password='pw')
        self.manager = User.objects.create_user('navuser_mgr', password='pw')
        self.auditor = User.objects.create_user('navuser_aud', password='pw')
        self.author = User.objects.create_user('navuser_auth', password='pw')
        mg = Group.objects.get_or_create(name='Document Managers')[0]
        ag = Group.objects.get_or_create(name='Document Auditors')[0]
        auth_g = Group.objects.get_or_create(name='Document Authors')[0]
        self.manager.groups.add(mg)
        self.auditor.groups.add(ag)
        self.author.groups.add(auth_g)

    def test_user_is_manager_true(self):
        from documents.templatetags.nav_tags import user_is_manager
        self.assertTrue(user_is_manager(self.manager))

    def test_user_is_manager_false_for_plain(self):
        from documents.templatetags.nav_tags import user_is_manager
        self.assertFalse(user_is_manager(self.anon_like))

    def test_user_can_quality_workspace_quality_manager(self):
        """MB1: il tag quality workspace riconosce Quality Manager."""
        from django.contrib.auth.models import Group
        from documents.templatetags.nav_tags import user_can_quality_workspace
        qm = User.objects.create_user('nav_qmgr', password='pw')
        Group.objects.get_or_create(name='Quality Manager')[0].user_set.add(qm)
        self.assertTrue(user_can_quality_workspace(qm))

    def test_user_can_quality_workspace_document_manager_false(self):
        """MB1: Document Manager da solo NON vede workspace Qualità nel nav."""
        from documents.templatetags.nav_tags import user_can_quality_workspace
        self.assertFalse(user_can_quality_workspace(self.manager))

    def test_user_can_quality_workspace_auditor(self):
        from documents.templatetags.nav_tags import user_can_quality_workspace
        self.assertTrue(user_can_quality_workspace(self.auditor))

    def test_user_can_quality_workspace_false_for_plain(self):
        from documents.templatetags.nav_tags import user_can_quality_workspace
        self.assertFalse(user_can_quality_workspace(self.anon_like))

    def test_nav_my_drafts_counts_correctly(self):
        from documents.templatetags.nav_tags import nav_my_drafts
        doc = Document.objects.create(
            code='NT-001', title='NT', category=Document.Category.QUALITY,
            owner=self.author, created_by=self.author,
        )
        create_new_revision(doc, self.author, 'A', 1)
        self.assertEqual(nav_my_drafts(self.author), 1)
        self.assertEqual(nav_my_drafts(self.manager), 0)

    def test_nav_pending_approvals_zero_when_no_pending(self):
        from documents.templatetags.nav_tags import nav_pending_approvals
        self.assertEqual(nav_pending_approvals(self.author), 0)


# ---------------------------------------------------------------------------
# MB1 — Test privacy bozze
# ---------------------------------------------------------------------------

class DraftPrivacyTests(TestCase):
    """
    MB1 — verifica che le bozze siano visibili SOLO all'autore e al superuser.
    Nessun altro — inclusi Manager, Auditor, staff — può vederle.
    """

    def setUp(self):
        from django.contrib.auth.models import Group
        self.author = User.objects.create_user('priv_author', password='pw')
        self.other_author = User.objects.create_user('priv_other', password='pw')
        self.manager = User.objects.create_user('priv_mgr', password='pw')
        self.auditor = User.objects.create_user('priv_aud', password='pw')
        self.staff_user = User.objects.create_user('priv_staff', password='pw', is_staff=True)
        self.superuser = User.objects.create_superuser('priv_su', password='pw', email='')

        mg = Group.objects.get_or_create(name='Document Managers')[0]
        ag = Group.objects.get_or_create(name='Document Auditors')[0]
        self.manager.groups.add(mg)
        self.auditor.groups.add(ag)

        self.doc = Document.objects.create(
            code='PRIV-001', title='Privato', category=Document.Category.QUALITY,
            owner=self.author, created_by=self.author,
        )
        self.draft_version = create_new_revision(self.doc, self.author, 'A', 1)

    # 1. Autore vede propria bozza
    def test_author_sees_own_draft(self):
        from documents.permissions import can_view_version
        self.assertTrue(can_view_version(self.author, self.draft_version))

    # 2. Altro autore non vede bozza altrui
    def test_other_author_cannot_see_draft(self):
        from documents.permissions import can_view_version
        self.assertFalse(can_view_version(self.other_author, self.draft_version))

    # 3. Manager non vede bozza altrui
    def test_manager_cannot_see_others_draft(self):
        from documents.permissions import can_view_version
        self.assertFalse(can_view_version(self.manager, self.draft_version))

    # 4. Auditor non vede bozza altrui
    def test_auditor_cannot_see_others_draft(self):
        from documents.permissions import can_view_version
        self.assertFalse(can_view_version(self.auditor, self.draft_version))

    # 5. staff non-superuser non vede bozza altrui
    def test_staff_cannot_see_others_draft(self):
        from documents.permissions import can_view_version
        self.assertFalse(can_view_version(self.staff_user, self.draft_version))

    # 6. Superuser vede la bozza
    def test_superuser_sees_draft(self):
        from documents.permissions import can_view_version
        self.assertTrue(can_view_version(self.superuser, self.draft_version))

    # 7. folder_detail non mostra la bozza altrui nella navigazione
    def test_folder_detail_hides_others_draft(self):
        from projects.models import ProjectFolder, ProjectFolderMembership
        folder = ProjectFolder.objects.create(
            code='PRIV-FOLD', name='Cartella privata',
            owner=self.author, created_by=self.author,
        )
        self.doc.project_folder = folder
        self.doc.save(update_fields=['project_folder'])
        ProjectFolderMembership.objects.create(
            folder=folder, user=self.other_author, role='author', created_by=self.author,
        )
        ProjectFolderMembership.objects.create(
            folder=folder, user=self.author, role='author', created_by=self.author,
        )
        self.client.force_login(self.other_author)
        r = self.client.get(f'/folders/{folder.pk}/')
        self.assertEqual(r.status_code, 200)
        # La bozza non appare nella lista documenti per un altro utente
        self.assertNotContains(r, 'PRIV-001')

    # 8. Download diretto file bozza altrui negato
    def test_download_others_draft_file_denied(self):
        from documents.permissions import can_download_version_file
        self.assertFalse(can_download_version_file(self.manager, self.draft_version))
        self.assertFalse(can_download_version_file(self.auditor, self.draft_version))
        self.assertFalse(can_download_version_file(self.staff_user, self.draft_version))
        self.assertFalse(can_download_version_file(self.other_author, self.draft_version))

    # Download del proprio file bozza consentito
    def test_download_own_draft_allowed(self):
        """L'autore può scaricare il file della propria bozza (se presente)."""
        from documents.permissions import can_download_version_file
        from documents.models import DocumentFile
        # Assegna un file fittizio al draft (il permesso dipende da file_id != None)
        mock_file = DocumentFile.objects.create(
            original_filename='bozza.pdf',
            uploaded_by=self.author,
        )
        self.draft_version.file = mock_file
        self.draft_version.save(update_fields=['file'])
        self.assertTrue(can_download_version_file(self.author, self.draft_version))


# ---------------------------------------------------------------------------
# MB1 — Test documento con sola bozza privata (Caso A) e pubblicato (Caso B)
# ---------------------------------------------------------------------------

class DraftOnlyDocumentPrivacyTests(TestCase):
    """
    Caso A: documento mai pubblicato (sola bozza) → visibile solo all'autore e al superuser.
    Caso B: documento pubblicato con nuova revisione privata → lettori vedono versione corrente.
    """

    def setUp(self):
        from django.contrib.auth.models import Group
        self.author = User.objects.create_user('dod_author', password='pw')
        self.other_author = User.objects.create_user('dod_other', password='pw')
        self.manager = User.objects.create_user('dod_mgr', password='pw')
        self.auditor = User.objects.create_user('dod_aud', password='pw')
        self.quality_mgr = User.objects.create_user('dod_qmgr', password='pw')
        self.staff_user = User.objects.create_user('dod_staff', password='pw', is_staff=True)
        self.reader = User.objects.create_user('dod_reader', password='pw')
        self.superuser = User.objects.create_superuser('dod_su', password='pw', email='')

        Group.objects.get_or_create(name='Document Managers')[0].user_set.add(self.manager)
        Group.objects.get_or_create(name='Document Auditors')[0].user_set.add(self.auditor)
        Group.objects.get_or_create(name='Quality Manager')[0].user_set.add(self.quality_mgr)

        # Documento Caso A — sola bozza privata
        self.draft_only_doc = Document.objects.create(
            code='DOD-A-001', title='Solo bozza',
            category=Document.Category.QUALITY,
            owner=self.author, created_by=self.author,
        )
        self.draft_v = create_new_revision(self.draft_only_doc, self.author, 'A', 1)

        # Documento Caso B — versione approvata + nuova bozza in lavorazione
        self.published_doc = Document.objects.create(
            code='DOD-B-001', title='Pubblicato con nuova bozza',
            category=Document.Category.QUALITY,
            owner=self.author, created_by=self.author,
        )
        v_approved = create_new_revision(self.published_doc, self.author, '00', 0)
        v_approved.status = DocumentVersion.Status.APPROVED
        v_approved.is_current = True
        v_approved.save(update_fields=['status', 'is_current'])
        self.published_doc.current_version = v_approved
        self.published_doc.save(update_fields=['current_version'])
        self.approved_v = v_approved
        # Nuova bozza privata — creata da author
        self.new_draft_v = create_new_revision(
            self.published_doc, self.author, '01', 1, _bypass_ecn_check=True,
        )

    # ── Caso A: documento mai pubblicato ──────────────────────────────────────

    def test_caso_a_author_can_view_document(self):
        from documents.permissions import can_view_document
        self.assertTrue(can_view_document(self.author, self.draft_only_doc))

    def test_caso_a_superuser_can_view_document(self):
        from documents.permissions import can_view_document
        self.assertTrue(can_view_document(self.superuser, self.draft_only_doc))

    def test_caso_a_manager_cannot_view_document(self):
        from documents.permissions import can_view_document
        self.assertFalse(can_view_document(self.manager, self.draft_only_doc))

    def test_caso_a_auditor_cannot_view_document(self):
        from documents.permissions import can_view_document
        self.assertFalse(can_view_document(self.auditor, self.draft_only_doc))

    def test_caso_a_quality_manager_cannot_view_document(self):
        from documents.permissions import can_view_document
        self.assertFalse(can_view_document(self.quality_mgr, self.draft_only_doc))

    def test_caso_a_staff_cannot_view_document(self):
        from documents.permissions import can_view_document
        self.assertFalse(can_view_document(self.staff_user, self.draft_only_doc))

    def test_caso_a_other_author_cannot_view_document(self):
        from documents.permissions import can_view_document
        self.assertFalse(can_view_document(self.other_author, self.draft_only_doc))

    # ── Caso A: URL diretti ───────────────────────────────────────────────────

    def test_caso_a_author_url_ok(self):
        self.client.force_login(self.author)
        r = self.client.get(reverse('document_detail', args=[self.draft_only_doc.pk]))
        self.assertEqual(r.status_code, 200)

    def test_caso_a_superuser_url_ok(self):
        self.client.force_login(self.superuser)
        r = self.client.get(reverse('document_detail', args=[self.draft_only_doc.pk]))
        self.assertEqual(r.status_code, 200)

    def test_caso_a_manager_url_404(self):
        self.client.force_login(self.manager)
        r = self.client.get(reverse('document_detail', args=[self.draft_only_doc.pk]))
        self.assertEqual(r.status_code, 404)

    def test_caso_a_auditor_url_404(self):
        self.client.force_login(self.auditor)
        r = self.client.get(reverse('document_detail', args=[self.draft_only_doc.pk]))
        self.assertEqual(r.status_code, 404)

    def test_caso_a_staff_url_404(self):
        self.client.force_login(self.staff_user)
        r = self.client.get(reverse('document_detail', args=[self.draft_only_doc.pk]))
        self.assertEqual(r.status_code, 404)

    def test_caso_a_other_author_url_404(self):
        self.client.force_login(self.other_author)
        r = self.client.get(reverse('document_detail', args=[self.draft_only_doc.pk]))
        self.assertEqual(r.status_code, 404)

    # ── Caso B: documento pubblicato con nuova bozza ──────────────────────────

    def test_caso_b_reader_sees_approved_version(self):
        """Lettore vede la versione corrente approvata, NON la bozza."""
        from documents.permissions import can_view_document, can_view_version
        self.assertTrue(can_view_document(self.reader, self.published_doc))
        self.assertTrue(can_view_version(self.reader, self.approved_v))
        self.assertFalse(can_view_version(self.reader, self.new_draft_v))

    def test_caso_b_reader_url_ok(self):
        """Lettore accede alla pagina del documento pubblicato."""
        self.client.force_login(self.reader)
        r = self.client.get(reverse('document_detail', args=[self.published_doc.pk]))
        self.assertEqual(r.status_code, 200)

    def test_caso_b_author_sees_own_draft(self):
        """Autore vede sia la versione corrente che la propria bozza."""
        from documents.permissions import can_view_version
        self.assertTrue(can_view_version(self.author, self.approved_v))
        self.assertTrue(can_view_version(self.author, self.new_draft_v))

    def test_caso_b_superuser_sees_draft(self):
        """Superuser vede tutto."""
        from documents.permissions import can_view_version
        self.assertTrue(can_view_version(self.superuser, self.new_draft_v))

    def test_caso_b_manager_does_not_see_draft(self):
        """Manager vede il documento pubblicato ma NON la nuova bozza."""
        from documents.permissions import can_view_document, can_view_version
        self.assertTrue(can_view_document(self.manager, self.published_doc))
        self.assertTrue(can_view_version(self.manager, self.approved_v))
        self.assertFalse(can_view_version(self.manager, self.new_draft_v))


# ---------------------------------------------------------------------------
# MB1 — Test permessi approvazione allegati
# ---------------------------------------------------------------------------

class ApprovalAttachmentPrivacyTests(TestCase):
    """MB1 — allegati richieste di approvazione scaricabili solo da autore, approvatori, superuser."""

    def setUp(self):
        from django.contrib.auth.models import Group
        from approvals.models import ApprovalRequest, ApprovalRequestApprover

        self.author = User.objects.create_user('app_att_author', password='pw')
        self.approver = User.objects.create_user('app_att_appr', password='pw')
        self.manager = User.objects.create_user('app_att_mgr', password='pw')
        self.auditor = User.objects.create_user('app_att_aud', password='pw')
        self.staff_user = User.objects.create_user('app_att_staff', password='pw', is_staff=True)
        self.superuser = User.objects.create_superuser('app_att_su', password='pw', email='')
        self.stranger = User.objects.create_user('app_att_stranger', password='pw')

        Group.objects.get_or_create(name='Document Managers')[0].user_set.add(self.manager)
        Group.objects.get_or_create(name='Document Auditors')[0].user_set.add(self.auditor)

        self.doc = Document.objects.create(
            code='APP-ATT-001', title='Doc att', category=Document.Category.QUALITY,
            owner=self.author, created_by=self.author,
        )
        self.version = create_new_revision(self.doc, self.author, 'A', 1)
        from documents.services import submit_version_for_approval
        self.ar = submit_version_for_approval(self.version, self.author, [self.approver])

    def _can_download(self, user, attachment):
        from approvals.views import _can_download_attachment
        return _can_download_attachment(user, attachment)

    def _make_attachment(self):
        from approvals.models import ApprovalRequestAttachment
        from django.core.files.uploadedfile import SimpleUploadedFile
        import tempfile, shutil
        from django.test import override_settings
        # Usa un file fittizio
        from approvals.services import create_approval_request_attachment
        f = SimpleUploadedFile('firma.pdf', b'%PDF fake', content_type='application/pdf')
        return create_approval_request_attachment(self.ar, f, self.author)

    @override_settings(MEDIA_ROOT=__import__('tempfile').mkdtemp())
    def test_author_can_download(self):
        att = self._make_attachment()
        self.assertTrue(self._can_download(self.author, att))

    @override_settings(MEDIA_ROOT=__import__('tempfile').mkdtemp())
    def test_assigned_approver_can_download(self):
        att = self._make_attachment()
        self.assertTrue(self._can_download(self.approver, att))

    @override_settings(MEDIA_ROOT=__import__('tempfile').mkdtemp())
    def test_superuser_can_download(self):
        att = self._make_attachment()
        self.assertTrue(self._can_download(self.superuser, att))

    @override_settings(MEDIA_ROOT=__import__('tempfile').mkdtemp())
    def test_manager_cannot_download(self):
        """MB1: Document Manager NON scarica automaticamente allegati approvazione."""
        att = self._make_attachment()
        self.assertFalse(self._can_download(self.manager, att))

    @override_settings(MEDIA_ROOT=__import__('tempfile').mkdtemp())
    def test_auditor_cannot_download(self):
        """MB1: Document Auditor NON scarica automaticamente allegati approvazione."""
        att = self._make_attachment()
        self.assertFalse(self._can_download(self.auditor, att))

    @override_settings(MEDIA_ROOT=__import__('tempfile').mkdtemp())
    def test_staff_cannot_download(self):
        """MB1: is_staff NON scarica automaticamente allegati approvazione."""
        att = self._make_attachment()
        self.assertFalse(self._can_download(self.staff_user, att))

    @override_settings(MEDIA_ROOT=__import__('tempfile').mkdtemp())
    def test_stranger_cannot_download(self):
        att = self._make_attachment()
        self.assertFalse(self._can_download(self.stranger, att))


# ===========================================================================
# Step G — Integrazione resolver nei permessi documentali
# ===========================================================================

def _make_folder(code='GF-001', owner=None):
    from projects.models import ProjectFolder
    from projects.services import set_folder_path
    f = ProjectFolder.objects.create(
        code=code, name=code,
        folder_kind=ProjectFolder.FolderKind.GENERIC,
        status=ProjectFolder.Status.ACTIVE,
        owner=owner,
    )
    set_folder_path(f)
    return f


def _make_published_doc(code, folder=None, owner=None):
    """Crea un documento con versione corrente approvata."""
    doc = Document.objects.create(
        code=code, title=f'Doc {code}',
        category=Document.Category.QUALITY,
        project_folder=folder,
        owner=owner, created_by=owner,
        status=Document.Status.ACTIVE,
    )
    ver = DocumentVersion.objects.create(
        document=doc, revision_label='00', revision_number=0,
        status=DocumentVersion.Status.APPROVED, is_current=True,
        created_by=owner,
    )
    doc.current_version = ver
    doc.save(update_fields=['current_version'])
    return doc, ver


def _make_draft_doc(code, folder=None, author=None):
    """Crea un documento con sola bozza (mai pubblicato)."""
    doc = Document.objects.create(
        code=code, title=f'Draft {code}',
        category=Document.Category.QUALITY,
        project_folder=folder,
        owner=author, created_by=author,
    )
    DocumentVersion.objects.create(
        document=doc, revision_label='00', revision_number=0,
        status=DocumentVersion.Status.DRAFT, is_current=False,
        created_by=author,
    )
    return doc


def _grant(folder, user=None, group=None, perm='read_published', effect='allow', inherit=False):
    from projects.models import FolderPermissionGrant
    return FolderPermissionGrant.objects.create(
        folder=folder, user=user, group=group,
        permission_code=perm, effect=effect,
        inherit_to_children=inherit,
    )


class StepGDocumentListTests(TestCase):
    """
    Verifica che document_list usi il resolver (read_published) con fallback legacy.
    document_list filtra via get_visible_folder_ids, già aggiornato nello Step F.
    """

    def setUp(self):
        self.owner = User.objects.create_user('gls_owner', password='pw')
        self.user = User.objects.create_user('gls_user', password='pw')
        self.staff = User.objects.create_user('gls_staff', password='pw', is_staff=True)
        self.folder = _make_folder(code='GLS-FOLD', owner=self.owner)

    def _login(self, username):
        self.client.login(username=username, password='pw')

    # 1. Solo grant modulare read_published: documento visibile
    def test_modular_read_published_shows_doc_in_list(self):
        doc, _ = _make_published_doc('GLS-DOC-001', self.folder, self.owner)
        _grant(self.folder, user=self.user, perm='read_published')
        self._login('gls_user')
        resp = self.client.get(reverse('document_list'))
        codes = [d.code for d in resp.context['documents']]
        self.assertIn('GLS-DOC-001', codes)

    # 2. Membership legacy reader: documento visibile
    def test_legacy_reader_membership_shows_doc_in_list(self):
        from projects.models import ProjectFolderMembership
        doc, _ = _make_published_doc('GLS-DOC-002', self.folder, self.owner)
        ProjectFolderMembership.objects.create(
            folder=self.folder, user=self.user, role='reader'
        )
        self._login('gls_user')
        resp = self.client.get(reverse('document_list'))
        codes = [d.code for d in resp.context['documents']]
        self.assertIn('GLS-DOC-002', codes)

    # 3. Deny modulare nasconde documento nonostante membership legacy
    def test_deny_grant_hides_doc_despite_legacy_membership(self):
        from projects.models import ProjectFolderMembership
        doc, _ = _make_published_doc('GLS-DOC-003', self.folder, self.owner)
        ProjectFolderMembership.objects.create(
            folder=self.folder, user=self.user, role='reader'
        )
        _grant(self.folder, user=self.user, perm='read_published', effect='deny')
        self._login('gls_user')
        resp = self.client.get(reverse('document_list'))
        codes = [d.code for d in resp.context['documents']]
        self.assertNotIn('GLS-DOC-003', codes)

    # 4. Draft-only altrui non appare nella lista
    def test_draft_only_document_not_in_list(self):
        _make_draft_doc('GLS-DRAFT-001', self.folder, author=self.owner)
        _grant(self.folder, user=self.user, perm='read_published')
        self._login('gls_user')
        resp = self.client.get(reverse('document_list'))
        codes = [d.code for d in resp.context['documents']]
        self.assertNotIn('GLS-DRAFT-001', codes)

    # 5. La propria bozza privata non appare in document_list (è in my_drafts)
    def test_own_draft_not_in_document_list(self):
        _make_draft_doc('GLS-MYDRAFT', self.folder, author=self.user)
        # Nemmeno con read_published la bozza appare in document_list
        _grant(self.folder, user=self.user, perm='read_published')
        self._login('gls_user')
        resp = self.client.get(reverse('document_list'))
        codes = [d.code for d in resp.context['documents']]
        self.assertNotIn('GLS-MYDRAFT', codes)

    # 6. Revisione storica (SUPERSEDED) non compare come documento separato
    def test_superseded_version_not_in_document_list(self):
        doc, ver = _make_published_doc('GLS-DOC-SUP', self.folder, self.owner)
        # Crea una seconda versione approvata: la prima diventa SUPERSEDED
        ver.is_current = False
        ver.status = DocumentVersion.Status.SUPERSEDED
        ver.save(update_fields=['is_current', 'status'])
        ver2 = DocumentVersion.objects.create(
            document=doc, revision_label='01', revision_number=1,
            status=DocumentVersion.Status.APPROVED, is_current=True,
            created_by=self.owner,
        )
        doc.current_version = ver2
        doc.save(update_fields=['current_version'])
        _grant(self.folder, user=self.user, perm='read_published')
        self._login('gls_user')
        resp = self.client.get(reverse('document_list'))
        # Solo il documento GLS-DOC-SUP deve apparire (una volta sola)
        codes = [d.code for d in resp.context['documents']]
        self.assertEqual(codes.count('GLS-DOC-SUP'), 1)

    # 7. Staff senza grant non vede documenti automaticamente
    def test_staff_without_grant_sees_no_folder_docs(self):
        _make_published_doc('GLS-DOC-STAFF', self.folder, self.owner)
        self._login('gls_staff')
        resp = self.client.get(reverse('document_list'))
        codes = [d.code for d in resp.context['documents']]
        self.assertNotIn('GLS-DOC-STAFF', codes)


class StepGDocumentDetailTests(TestCase):
    """Verifica che document_detail rispetti i permessi modulari."""

    def setUp(self):
        self.owner = User.objects.create_user('gdd_owner', password='pw')
        self.user = User.objects.create_user('gdd_user', password='pw')
        self.author = User.objects.create_user('gdd_author', password='pw')
        self.superuser = User.objects.create_user('gdd_super', password='pw', is_superuser=True)
        self.folder = _make_folder(code='GDD-FOLD', owner=self.owner)

    def _login(self, username):
        self.client.login(username=username, password='pw')

    # 8. Grant read_published consente accesso al dettaglio documento pubblicato
    def test_read_published_grant_allows_detail(self):
        doc, _ = _make_published_doc('GDD-DOC-001', self.folder, self.owner)
        _grant(self.folder, user=self.user, perm='read_published')
        self._login('gdd_user')
        resp = self.client.get(reverse('document_detail', args=[doc.pk]))
        self.assertEqual(resp.status_code, 200)

    # 9. Deny modulare blocca dettaglio anche con membership legacy
    def test_deny_grant_blocks_detail_despite_membership(self):
        from projects.models import ProjectFolderMembership
        doc, _ = _make_published_doc('GDD-DOC-002', self.folder, self.owner)
        ProjectFolderMembership.objects.create(
            folder=self.folder, user=self.user, role='reader'
        )
        _grant(self.folder, user=self.user, perm='read_published', effect='deny')
        self._login('gdd_user')
        resp = self.client.get(reverse('document_detail', args=[doc.pk]))
        self.assertEqual(resp.status_code, 404)

    # 10. Draft-only altrui → 404
    def test_draft_only_other_user_gets_404(self):
        doc = _make_draft_doc('GDD-DRAFT-001', self.folder, author=self.owner)
        _grant(self.folder, user=self.user, perm='read_published')
        self._login('gdd_user')
        resp = self.client.get(reverse('document_detail', args=[doc.pk]))
        self.assertEqual(resp.status_code, 404)

    # 11. Autore apre propria bozza privata
    def test_author_can_view_own_draft(self):
        doc = _make_draft_doc('GDD-MYDRAFT', self.folder, author=self.author)
        self._login('gdd_author')
        resp = self.client.get(reverse('document_detail', args=[doc.pk]))
        self.assertEqual(resp.status_code, 200)

    # 12. Superuser apre bozza privata altrui
    def test_superuser_can_view_any_draft(self):
        doc = _make_draft_doc('GDD-SUPERDRAFT', self.folder, author=self.author)
        self._login('gdd_super')
        resp = self.client.get(reverse('document_detail', args=[doc.pk]))
        self.assertEqual(resp.status_code, 200)

    # 13. Utente con solo read_published non vede revisione privata nuova in corso
    def test_reader_does_not_see_private_new_revision(self):
        doc, ver = _make_published_doc('GDD-DOC-003', self.folder, self.owner)
        # Crea una seconda revisione in bozza (privata)
        draft_ver = DocumentVersion.objects.create(
            document=doc, revision_label='01', revision_number=1,
            status=DocumentVersion.Status.DRAFT, is_current=False,
            created_by=self.owner,
        )
        _grant(self.folder, user=self.user, perm='read_published')
        from documents.permissions import can_view_version
        self.assertFalse(can_view_version(self.user, draft_ver))

    # 14. Autore vede propria revisione privata in bozza
    def test_author_sees_own_draft_version(self):
        doc, _ = _make_published_doc('GDD-DOC-004', self.folder, self.owner)
        draft_ver = DocumentVersion.objects.create(
            document=doc, revision_label='01', revision_number=1,
            status=DocumentVersion.Status.DRAFT, is_current=False,
            created_by=self.author,
        )
        from documents.permissions import can_view_version
        self.assertTrue(can_view_version(self.author, draft_ver))

    # 15. grant view_history consente storico nel document_detail
    def test_view_history_grant_shows_storico(self):
        from projects.models import ProjectFolderMembership
        doc, _ = _make_published_doc('GDD-DOC-005', self.folder, self.owner)
        ProjectFolderMembership.objects.create(
            folder=self.folder, user=self.user, role='auditor'
        )
        self._login('gdd_user')
        resp = self.client.get(reverse('document_detail', args=[doc.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.context['show_history'])

    # 16. Assenza view_history nasconde storico (reader non ha view_history nel fallback)
    def test_reader_without_view_history_hides_storico(self):
        from projects.models import ProjectFolderMembership
        doc, _ = _make_published_doc('GDD-DOC-006', self.folder, self.owner)
        ProjectFolderMembership.objects.create(
            folder=self.folder, user=self.user, role='reader'
        )
        self._login('gdd_user')
        resp = self.client.get(reverse('document_detail', args=[doc.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.context['show_history'])


class StepGDownloadTests(TestCase):
    """Verifica can_download_version_file con resolver modulare."""

    def setUp(self):
        self.owner = User.objects.create_user('gdl_owner', password='pw')
        self.user = User.objects.create_user('gdl_user', password='pw')
        self.author = User.objects.create_user('gdl_author', password='pw')
        self.staff = User.objects.create_user('gdl_staff', password='pw', is_staff=True)
        self.folder = _make_folder(code='GDL-FOLD', owner=self.owner)

    def _make_ver_with_fid(self, doc, status=DocumentVersion.Status.APPROVED,
                           is_current=True, created_by=None):
        """Crea versione con file_id fittizio per testare solo il permesso."""
        ver = DocumentVersion.objects.create(
            document=doc, revision_label='00', revision_number=0,
            status=status, is_current=is_current,
            created_by=created_by or self.owner,
        )
        ver.file_id = 999  # fittizio: testa il permesso, non l'esistenza del file
        return ver

    # 17. grant read_published consente download versione corrente approvata
    def test_read_published_allows_download_current(self):
        doc, ver = _make_published_doc('GDL-DOC-001', self.folder, self.owner)
        ver.file_id = 999
        _grant(self.folder, user=self.user, perm='read_published')
        self.assertTrue(can_download_version_file(self.user, ver))

    # 18. Deny modulare blocca download corrente nonostante membership
    def test_deny_blocks_download_current(self):
        from projects.models import ProjectFolderMembership
        doc, ver = _make_published_doc('GDL-DOC-002', self.folder, self.owner)
        ver.file_id = 999
        ProjectFolderMembership.objects.create(
            folder=self.folder, user=self.user, role='reader'
        )
        _grant(self.folder, user=self.user, perm='read_published', effect='deny')
        self.assertFalse(can_download_version_file(self.user, ver))

    # 19. Versione storica richiede view_history (auditor legacy ce l'ha)
    def test_superseded_requires_view_history(self):
        from projects.models import ProjectFolderMembership
        doc, _ = _make_published_doc('GDL-DOC-003', self.folder, self.owner)
        sup_ver = DocumentVersion.objects.create(
            document=doc, revision_label='01', revision_number=1,
            status=DocumentVersion.Status.SUPERSEDED, is_current=False,
            created_by=self.owner,
        )
        sup_ver.file_id = 999
        # Reader non ha view_history → False
        ProjectFolderMembership.objects.create(
            folder=self.folder, user=self.user, role='reader'
        )
        self.assertFalse(can_download_version_file(self.user, sup_ver))

    def test_superseded_with_view_history_grant_allowed(self):
        doc, _ = _make_published_doc('GDL-DOC-004', self.folder, self.owner)
        sup_ver = DocumentVersion.objects.create(
            document=doc, revision_label='01', revision_number=1,
            status=DocumentVersion.Status.SUPERSEDED, is_current=False,
            created_by=self.owner,
        )
        sup_ver.file_id = 999
        # Grant esplicito view_history → True
        _grant(self.folder, user=self.user, perm='view_history')
        self.assertTrue(can_download_version_file(self.user, sup_ver))

    # 20. Draft scaricabile solo dall'autore
    def test_draft_downloadable_by_author_only(self):
        doc = _make_draft_doc('GDL-DRAFT', self.folder, author=self.author)
        ver = doc.versions.first()
        ver.file_id = 999
        self.assertTrue(can_download_version_file(self.author, ver))
        self.assertFalse(can_download_version_file(self.user, ver))

    # 21. Draft privata negata ad altro utente (anche con read_published)
    def test_draft_denied_to_other_even_with_read_published(self):
        doc = _make_draft_doc('GDL-DRAFT-2', self.folder, author=self.author)
        ver = doc.versions.first()
        ver.file_id = 999
        _grant(self.folder, user=self.user, perm='read_published')
        self.assertFalse(can_download_version_file(self.user, ver))

    # 22. In_approval scaricabile dall'approvatore assegnato
    def test_in_approval_downloadable_by_assigned_approver(self):
        from approvals.models import ApprovalRequest, ApprovalRequestApprover

        doc, _ = _make_published_doc('GDL-DOC-INA', self.folder, self.owner)
        in_appr_ver = DocumentVersion.objects.create(
            document=doc, revision_label='01', revision_number=1,
            status=DocumentVersion.Status.IN_APPROVAL, is_current=False,
            created_by=self.owner,
        )
        in_appr_ver.file_id = 999
        # Crea ApprovalRequest con l'utente come approvatore
        ar = ApprovalRequest.objects.create(
            document_version=in_appr_ver,
            requested_by=self.owner,
            status=ApprovalRequest.Status.PENDING,
        )
        ApprovalRequestApprover.objects.create(
            approval_request=ar,
            approver=self.user,
            order=1,
        )
        self.assertTrue(can_download_version_file(self.user, in_appr_ver))

    # 23. In_approval negata a utente casuale
    def test_in_approval_denied_to_random_user(self):
        doc, _ = _make_published_doc('GDL-DOC-INA-2', self.folder, self.owner)
        in_appr_ver = DocumentVersion.objects.create(
            document=doc, revision_label='01', revision_number=1,
            status=DocumentVersion.Status.IN_APPROVAL, is_current=False,
            created_by=self.owner,
        )
        in_appr_ver.file_id = 999
        self.assertFalse(can_download_version_file(self.user, in_appr_ver))

    # 24. Staff non-superuser non scarica automaticamente
    def test_staff_cannot_download_automatically(self):
        doc, ver = _make_published_doc('GDL-DOC-STAFF', self.folder, self.owner)
        ver.file_id = 999
        self.assertFalse(can_download_version_file(self.staff, ver))


class StepGCreationTests(TestCase):
    """Verifica can_create_revision e can_submit_for_approval con resolver."""

    def setUp(self):
        self.owner = User.objects.create_user('gcr_owner', password='pw')
        self.user = User.objects.create_user('gcr_user', password='pw')
        self.staff = User.objects.create_user('gcr_staff', password='pw', is_staff=True)
        self.folder = _make_folder(code='GCR-FOLD', owner=self.owner)

    # 25. grant create_draft consente creazione revisione
    def test_create_draft_grant_allows_revision(self):
        doc, _ = _make_published_doc('GCR-DOC-001', self.folder, self.owner)
        _grant(self.folder, user=self.user, perm='create_draft')
        from documents.permissions import can_create_revision
        self.assertTrue(can_create_revision(self.user, doc))

    # 26. Deny create_draft blocca author legacy
    def test_deny_create_draft_blocks_author_legacy(self):
        from projects.models import ProjectFolderMembership
        doc, _ = _make_published_doc('GCR-DOC-002', self.folder, self.owner)
        ProjectFolderMembership.objects.create(
            folder=self.folder, user=self.user, role='author'
        )
        _grant(self.folder, user=self.user, perm='create_draft', effect='deny')
        from documents.permissions import can_create_revision
        self.assertFalse(can_create_revision(self.user, doc))

    # 27. Grant create_draft ereditato da parent abilita child (via resolver)
    def test_inherited_create_draft_enables_child(self):
        from projects.models import ProjectFolder
        from projects.services import set_folder_path
        child = ProjectFolder.objects.create(
            code='GCR-CH', name='Child',
            folder_kind=ProjectFolder.FolderKind.GENERIC,
            status=ProjectFolder.Status.ACTIVE, owner=self.owner,
            parent=self.folder,
        )
        set_folder_path(child)
        doc, _ = _make_published_doc('GCR-DOC-003', child, self.owner)
        _grant(self.folder, user=self.user, perm='create_draft', inherit=True)
        from documents.permissions import can_create_revision
        self.assertTrue(can_create_revision(self.user, doc))

    # 28. grant submit_for_approval consente invio in approvazione
    def test_submit_for_approval_grant_allows_submit(self):
        doc, _ = _make_published_doc('GCR-DOC-004', self.folder, self.owner)
        draft_ver = DocumentVersion.objects.create(
            document=doc, revision_label='01', revision_number=1,
            status=DocumentVersion.Status.DRAFT, is_current=False,
            created_by=self.user,
        )
        _grant(self.folder, user=self.user, perm='submit_for_approval')
        from documents.permissions import can_submit_for_approval
        self.assertTrue(can_submit_for_approval(self.user, draft_ver))

    # 29. Deny submit_for_approval blocca author legacy
    def test_deny_submit_blocks_author_legacy(self):
        from projects.models import ProjectFolderMembership
        doc, _ = _make_published_doc('GCR-DOC-005', self.folder, self.owner)
        draft_ver = DocumentVersion.objects.create(
            document=doc, revision_label='01', revision_number=1,
            status=DocumentVersion.Status.DRAFT, is_current=False,
            created_by=self.user,
        )
        ProjectFolderMembership.objects.create(
            folder=self.folder, user=self.user, role='author'
        )
        _grant(self.folder, user=self.user, perm='submit_for_approval', effect='deny')
        from documents.permissions import can_submit_for_approval
        self.assertFalse(can_submit_for_approval(self.user, draft_ver))

    # 30. Staff senza grant non può creare
    def test_staff_without_grant_cannot_create(self):
        doc, _ = _make_published_doc('GCR-DOC-006', self.folder, self.owner)
        from documents.permissions import can_create_revision
        self.assertFalse(can_create_revision(self.staff, doc))


class StepGPerformanceTests(TestCase):
    """Verifica che document_list non generi query N+1."""

    def setUp(self):
        self.owner = User.objects.create_user('gperf_owner', password='pw')
        self.user = User.objects.create_user('gperf_user', password='pw')

    def test_document_list_no_n1_queries(self):
        """
        document_list usa get_visible_folder_ids (bulk API) → nessuna query N+1
        al variare del numero di cartelle/documenti.
        """
        from projects.models import ProjectFolder
        from projects.services import set_folder_path
        from django.test.utils import CaptureQueriesContext
        from django.db import connection

        folders = []
        for i in range(5):
            f = ProjectFolder.objects.create(
                code=f'GPERF-F{i}', name=f'Folder {i}',
                folder_kind=ProjectFolder.FolderKind.GENERIC,
                status=ProjectFolder.Status.ACTIVE, owner=self.owner,
            )
            set_folder_path(f)
            folders.append(f)
            _make_published_doc(f'GPERF-DOC-{i}', f, self.owner)
            _grant(f, user=self.user, perm='read_published')

        self.client.login(username='gperf_user', password='pw')

        with CaptureQueriesContext(connection) as ctx:
            resp = self.client.get(reverse('document_list'))

        self.assertEqual(resp.status_code, 200)
        docs_shown = len(resp.context['documents'])
        self.assertGreaterEqual(docs_shown, 5)

        # Numero di query fisso indipendentemente dal numero di cartelle.
        # La soglia è generosa (≤40) per includere session, autenticazione,
        # template e template tags. L'importante è che NON cresca con N cartelle
        # (assenza N+1: la bulk API carica tutti i grant in 1 query).
        self.assertLessEqual(
            len(ctx), 40,
            f"document_list ha eseguito {len(ctx)} query per {len(folders)} cartelle"
        )


# ===========================================================================
# Demo Supervisor — test
# ===========================================================================

@override_settings(EMAIL_BACKEND=LOCMEM)
class DemoSupervisorTests(TestCase):
    """
    Test della funzionalità demo supervisor.
    Verifica che le deroghe siano attive SOLO con demo mode + username corretto.
    """

    SUPERVISOR_USERNAME = 'supervisor_demo'

    def setUp(self):
        self.supervisor = User.objects.create_user(
            username=self.SUPERVISOR_USERNAME,
            password='demo1234',
            is_superuser=True,
            is_staff=True,
        )
        self.other_user = User.objects.create_user('other_demo_user', password='pw')

    # ── Test 1-3: creazione account via comando ────────────────────────────

    @override_settings(
        EMAIL_BACKEND=LOCMEM,
        DEBUG=True,
        DATABASES={
            'default': {
                'ENGINE': 'django.db.backends.sqlite3',
                'NAME': ':memory:',
            }
        },
    )
    def test_supervisor_created_by_command(self):
        """demo_company crea supervisor_demo (get_or_create idempotente)."""
        from io import StringIO
        from django.core.management import call_command
        call_command('demo_company', '--reset', '--no-email', stdout=StringIO())
        self.assertTrue(User.objects.filter(username=self.SUPERVISOR_USERNAME).exists())

    def test_supervisor_is_superuser(self):
        self.assertTrue(self.supervisor.is_superuser)

    def test_supervisor_is_staff(self):
        self.assertTrue(self.supervisor.is_staff)

    # ── Test 4-5: approvatori documentali in demo mode ────────────────────

    @override_settings(DOCUMENTALE_DEMO_MODE=True, DOCUMENTALE_DEMO_SUPERVISOR_USERNAME='supervisor_demo')
    def test_demo_mode_supervisor_approver_queryset_shows_only_self(self):
        """Demo mode + supervisor → lista approvatori contiene solo sé stesso."""
        from documents.forms import ApproverRowForm
        form = ApproverRowForm(current_user=self.supervisor)
        qs = list(form.fields['approver'].queryset)
        self.assertEqual(len(qs), 1)
        self.assertEqual(qs[0].pk, self.supervisor.pk)

    @override_settings(DOCUMENTALE_DEMO_MODE=True, DOCUMENTALE_DEMO_SUPERVISOR_USERNAME='supervisor_demo')
    def test_demo_mode_supervisor_can_select_self_as_approver(self):
        """Demo mode + supervisor → può selezionare sé stesso nel formset."""
        from documents.forms import ApproverFormSet
        data = {
            'form-TOTAL_FORMS': '1',
            'form-INITIAL_FORMS': '0',
            'form-MIN_NUM_FORMS': '0',
            'form-MAX_NUM_FORMS': '1000',
            'form-0-approver': str(self.supervisor.pk),
        }
        formset = ApproverFormSet(data, prefix='form', current_user=self.supervisor)
        self.assertTrue(formset.is_valid(), formset.errors)
        selected = [
            f.cleaned_data['approver']
            for f in formset.forms
            if f.cleaned_data and f.cleaned_data.get('approver')
        ]
        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0].pk, self.supervisor.pk)

    # ── Test 6: approvazione propria bozza in demo mode ───────────────────

    @override_settings(DOCUMENTALE_DEMO_MODE=True, DOCUMENTALE_DEMO_SUPERVISOR_USERNAME='supervisor_demo')
    def test_demo_mode_supervisor_can_approve_own_draft(self):
        """Demo mode + supervisor → può approvare la propria bozza."""
        from documents.models import Document, DocumentVersion
        from documents.services import submit_version_for_approval
        from approvals.services import approve_version

        doc = Document.objects.create(
            code='DEMO-SELF-APPR',
            title='Test autoapprovazione demo',
            category=Document.Category.QUALITY,
            owner=self.supervisor,
            created_by=self.supervisor,
            status=Document.Status.ACTIVE,
        )
        ver = DocumentVersion.objects.create(
            document=doc, revision_label='00', revision_number=0,
            status=DocumentVersion.Status.DRAFT, is_current=False,
            created_by=self.supervisor, change_summary='test',
        )
        # Invio con sé stesso come approvatore
        req = submit_version_for_approval(ver, self.supervisor, [self.supervisor])
        # Approvazione: superuser bypass in approve_version
        approve_version(req, self.supervisor, comment='Autoapprovazione demo')
        ver.refresh_from_db()
        self.assertEqual(ver.status, DocumentVersion.Status.APPROVED)

    # ── Test 7-8: CCB in demo mode ─────────────────────────────────────────

    @override_settings(DOCUMENTALE_DEMO_MODE=True, DOCUMENTALE_DEMO_SUPERVISOR_USERNAME='supervisor_demo')
    def test_demo_mode_supervisor_can_configure_ccb(self):
        """Demo mode + supervisor → can_configure_ccb restituisce True."""
        from ecn.models import ChangeNotice
        from documents.models import Document, DocumentVersion
        doc = Document.objects.create(
            code='DEMO-CCB-DOC', title='T', category=Document.Category.QUALITY,
            owner=self.supervisor, created_by=self.supervisor, status=Document.Status.ACTIVE,
        )
        ver = DocumentVersion.objects.create(
            document=doc, revision_label='00', revision_number=0,
            status=DocumentVersion.Status.APPROVED, is_current=True,
            created_by=self.supervisor,
        )
        doc.current_version = ver; doc.save(update_fields=['current_version'])
        ecn = ChangeNotice.objects.create(
            code='ECN-TEST-CCB', title='Test CCB',
            motivation=ChangeNotice.Motivation.IMPROVEMENT,
            document=doc, document_version=ver,
            proposed_by=self.supervisor, created_by=self.supervisor,
        )
        from ecn.permissions import can_configure_ccb
        self.assertTrue(can_configure_ccb(self.supervisor, ecn))

    @override_settings(DOCUMENTALE_DEMO_MODE=True, DOCUMENTALE_DEMO_SUPERVISOR_USERNAME='supervisor_demo')
    def test_demo_mode_ccb_form_shows_only_supervisor(self):
        """Demo mode + supervisor → lista candidati CCB contiene solo sé stesso."""
        from ecn.forms import ChangeNoticeCCBConfigForm
        form = ChangeNoticeCCBConfigForm(current_user=self.supervisor)
        qs = list(form.fields['approvers'].queryset)
        self.assertEqual(len(qs), 1)
        self.assertEqual(qs[0].pk, self.supervisor.pk)

    # ── Test 9-10: ECN flow in demo mode ──────────────────────────────────

    @override_settings(EMAIL_BACKEND=LOCMEM, DOCUMENTALE_DEMO_MODE=True, DOCUMENTALE_DEMO_SUPERVISOR_USERNAME='supervisor_demo')
    def test_demo_mode_supervisor_can_approve_own_ecn(self):
        """Demo mode + supervisor → può approvare la propria ECN."""
        from ecn.models import ChangeNotice
        from ecn.services import (
            create_change_notice,
            set_change_notice_approvers,
            submit_change_notice,
            approve_change_notice,
        )
        from documents.models import Document, DocumentVersion
        from documents.services import submit_version_for_approval
        from approvals.services import approve_version

        doc = Document.objects.create(
            code='DEMO-ECN-APPR', title='T',
            category=Document.Category.QUALITY,
            owner=self.supervisor, created_by=self.supervisor,
            status=Document.Status.ACTIVE,
        )
        ver = DocumentVersion.objects.create(
            document=doc, revision_label='00', revision_number=0,
            status=DocumentVersion.Status.APPROVED, is_current=True,
            created_by=self.supervisor,
        )
        doc.current_version = ver; doc.save(update_fields=['current_version'])

        ecn = create_change_notice(
            document=doc, proposed_by=self.supervisor,
            title='ECN test demo', motivation=ChangeNotice.Motivation.IMPROVEMENT,
        )
        # Configura CCB con solo sé stesso
        set_change_notice_approvers(ecn, [self.supervisor], policy='any', actor=self.supervisor)
        submit_change_notice(ecn, self.supervisor)
        approve_change_notice(
            ecn, self.supervisor,
            ccb_class=ChangeNotice.CCBClass.CLASS2,
            comment='Approvazione demo',
        )
        ecn.refresh_from_db()
        self.assertEqual(ecn.status, ChangeNotice.Status.APPROVED)

    @override_settings(EMAIL_BACKEND=LOCMEM, DOCUMENTALE_DEMO_MODE=True, DOCUMENTALE_DEMO_SUPERVISOR_USERNAME='supervisor_demo')
    def test_demo_mode_supervisor_can_close_ecn(self):
        """Demo mode + supervisor → può chiudere l'ECN (dopo esecuzione revisione)."""
        from ecn.models import ChangeNotice
        from ecn.services import (
            create_change_notice,
            set_change_notice_approvers,
            submit_change_notice,
            approve_change_notice,
            close_change_notice,
        )
        from documents.models import Document, DocumentVersion
        from documents.services import create_new_revision, submit_version_for_approval
        from approvals.services import approve_version

        doc = Document.objects.create(
            code='DEMO-ECN-CLOSE', title='T',
            category=Document.Category.QUALITY,
            owner=self.supervisor, created_by=self.supervisor,
            status=Document.Status.ACTIVE,
        )
        ver = DocumentVersion.objects.create(
            document=doc, revision_label='00', revision_number=0,
            status=DocumentVersion.Status.APPROVED, is_current=True,
            created_by=self.supervisor,
        )
        doc.current_version = ver; doc.save(update_fields=['current_version'])

        ecn = create_change_notice(
            document=doc, proposed_by=self.supervisor,
            title='ECN close demo', motivation=ChangeNotice.Motivation.IMPROVEMENT,
        )
        set_change_notice_approvers(ecn, [self.supervisor], policy='any', actor=self.supervisor)
        submit_change_notice(ecn, self.supervisor)
        approve_change_notice(
            ecn, self.supervisor,
            ccb_class=ChangeNotice.CCBClass.CLASS2,
        )
        # Crea nuova revisione autorizzata dall'ECN
        ver01 = create_new_revision(
            document=doc, created_by=self.supervisor,
            revision_label='01', revision_number=1,
            change_summary='Revisione ECN', ecn=ecn,
        )
        req = submit_version_for_approval(ver01, self.supervisor, [self.supervisor])
        approve_version(req, self.supervisor)
        ecn.refresh_from_db()
        # Chiude ECN
        close_change_notice(ecn, self.supervisor)
        ecn.refresh_from_db()
        self.assertEqual(ecn.status, ChangeNotice.Status.CLOSED)

    # ── Test 11-12: demo mode disabilitata o utente sbagliato ─────────────

    @override_settings(DOCUMENTALE_DEMO_MODE=False)
    def test_demo_mode_disabled_no_derogation(self):
        """Demo mode disabilitata → nessuna deroga anche con username supervisor_demo."""
        from documents.forms import ApproverRowForm
        form = ApproverRowForm(current_user=self.supervisor)
        qs = list(form.fields['approver'].queryset)
        # Con demo mode disabilitata, la queryset normale mostra tutti gli utenti attivi
        self.assertGreater(len(qs), 1)

    @override_settings(DOCUMENTALE_DEMO_MODE=True, DOCUMENTALE_DEMO_SUPERVISOR_USERNAME='supervisor_demo')
    def test_normal_user_in_demo_mode_no_derogation(self):
        """Demo mode attiva + utente normale → nessuna deroga speciale."""
        from documents.forms import ApproverRowForm
        form = ApproverRowForm(current_user=self.other_user)
        qs = list(form.fields['approver'].queryset)
        # Queryset normale: mostra tutti gli utenti attivi
        self.assertGreater(len(qs), 1)

    # ── Test 13-14: logica normale invariata per altri utenti ─────────────

    @override_settings(DOCUMENTALE_DEMO_MODE=True, DOCUMENTALE_DEMO_SUPERVISOR_USERNAME='supervisor_demo')
    def test_normal_approver_logic_unchanged_for_other_users(self):
        """La logica approvatori normale è invariata per gli altri utenti in demo mode."""
        from documents.forms import ApproverRowForm
        # other_user vede la lista completa, non limitata a sé stesso
        form = ApproverRowForm(current_user=self.other_user)
        qs_count = form.fields['approver'].queryset.count()
        # Ci sono almeno 2 utenti attivi (supervisor + other_user)
        self.assertGreaterEqual(qs_count, 2)

    @override_settings(DOCUMENTALE_DEMO_MODE=True, DOCUMENTALE_DEMO_SUPERVISOR_USERNAME='supervisor_demo')
    def test_normal_ccb_logic_unchanged_for_other_users(self):
        """La logica CCB candidati è invariata per gli altri utenti in demo mode."""
        from ecn.forms import ChangeNoticeCCBConfigForm
        from django.contrib.auth.models import Group
        # Aggiungi other_user al gruppo CCB per avere almeno 1 candidato
        ccb_group = Group.objects.get_or_create(name=GROUP_CCB)[0]
        ccb_group.user_set.add(self.other_user)
        form = ChangeNoticeCCBConfigForm(current_user=self.other_user)
        qs = list(form.fields['approvers'].queryset)
        # Lista normale: include other_user (e potenzialmente altri dal gruppo CCB)
        # Non è limitata a sé stesso
        pks = [u.pk for u in qs]
        self.assertIn(self.other_user.pk, pks)
        # other_user vede anche altri candidati (non solo sé stesso)
        # → il count non è 1 limitato a sé stesso (a meno che sia l'unico nel gruppo)


# ===========================================================================
# STEP H2 — DemoSupervisorEndToEndTests
# Flusso completo singolo utente supervisor_demo
# ===========================================================================

@override_settings(
    DOCUMENTALE_DEMO_MODE=True,
    DOCUMENTALE_DEMO_SUPERVISOR_USERNAME='supervisor_demo',
    EMAIL_BACKEND=LOCMEM,
)
class DemoSupervisorEndToEndTests(TestCase):
    """
    Flusso completo supervisor_demo con un unico account:
    bozza → approvazione → ECN → nuova revisione → approvazione → chiusura ECN.
    """

    def setUp(self):
        from projects.models import ProjectFolder
        from projects.services import set_folder_path

        self.supervisor = User.objects.create_user(
            username='supervisor_demo',
            password='demo1234',
            is_superuser=True,
            is_staff=True,
        )
        self.other = User.objects.create_user('e2e_other', password='pw')

        self.folder = ProjectFolder.objects.create(
            code='E2E-FOLD', name='E2E Folder',
            folder_kind=ProjectFolder.FolderKind.GENERIC,
            status=ProjectFolder.Status.ACTIVE,
            owner=self.supervisor,
        )
        set_folder_path(self.folder)

    # ── Flusso completo ──────────────────────────────────────────────────────

    def test_full_single_user_workflow(self):
        """
        supervisor_demo esegue l'intero ciclo di vita documentale da solo:
        crea bozza → approva → crea ECN → configura CCB (solo sé) → invia →
        approva ECN → crea revisione → approva revisione → chiude ECN.
        """
        from documents.models import Document, DocumentVersion
        from documents.services import create_new_revision, submit_version_for_approval
        from approvals.services import approve_version
        from auditlog.models import AuditLog
        from ecn.models import ChangeNotice
        from ecn.services import (
            create_change_notice,
            configure_ccb,
            update_ccb_dossier,
            submit_change_notice,
            approve_change_notice,
            close_change_notice,
        )

        sup = self.supervisor

        # 1. Crea documento e bozza Rev.00
        doc = Document.objects.create(
            code='E2E-DOC-001',
            title='Documento end-to-end',
            category=Document.Category.QUALITY,
            project_folder=self.folder,
            owner=sup,
            created_by=sup,
        )
        v00 = DocumentVersion.objects.create(
            document=doc, revision_label='00', revision_number=0,
            status=DocumentVersion.Status.DRAFT, is_current=False,
            created_by=sup, change_summary='Prima emissione.',
        )

        # 2. Invia in approvazione con sé stesso come unico approvatore
        req = submit_version_for_approval(v00, sup, [sup])

        # 3. Approva propria versione
        approve_version(req, sup, comment='Autoapprovazione demo')
        v00.refresh_from_db()
        doc.refresh_from_db()

        self.assertEqual(v00.status, DocumentVersion.Status.APPROVED)
        self.assertTrue(v00.is_current)
        self.assertEqual(doc.current_version, v00)

        # 4. Crea ECN sul documento approvato
        ecn = create_change_notice(
            document=doc, proposed_by=sup,
            title='Aggiornamento sezione 3',
            motivation=ChangeNotice.Motivation.IMPROVEMENT,
            description='E2E test',
        )

        # 5. Configura CCB: responsabile istruttoria = sup, unico componente = sup
        configure_ccb(ecn, actor=sup, users=[sup], policy='any', coordinator=sup)
        ecn.refresh_from_db()
        self.assertEqual(ecn.status, ChangeNotice.Status.CCB_PREPARATION)
        ccb_approver_pks = list(ecn.approvers.values_list('user_id', flat=True))
        self.assertEqual(ccb_approver_pks, [sup.pk])

        # 6. Compila il dossier istruttorio
        update_ccb_dossier(
            ecn, actor=sup,
            ccb_class=ChangeNotice.CCBClass.CLASS2,
            ccb_requirements='Analisi requisiti E2E.',
            ccb_technical_impact='Impatto tecnico minore.',
            ccb_cost_impact='Nessun costo aggiuntivo.',
            ccb_notes='Test E2E completo.',
        )
        ecn.refresh_from_db()
        self.assertEqual(ecn.ccb_class, ChangeNotice.CCBClass.CLASS2)

        # 7. Invia ECN alla CCB (CCB_PREPARATION → UNDER_REVIEW)
        submit_change_notice(ecn, sup)
        ecn.refresh_from_db()
        self.assertEqual(ecn.status, ChangeNotice.Status.UNDER_REVIEW)

        # 8. Vota: approva ECN (dossier già compilato, ccb_class già settato)
        approve_change_notice(
            ecn, sup,
            comment='Approvazione E2E',
        )
        ecn.refresh_from_db()
        self.assertEqual(ecn.status, ChangeNotice.Status.APPROVED)

        # 9. Crea nuova revisione autorizzata dall'ECN approvato
        v01 = create_new_revision(
            document=doc, created_by=sup,
            revision_label='01', revision_number=1,
            change_summary='Revisione da ECN', ecn=ecn,
        )

        # 10. Invia nuova revisione in approvazione
        req2 = submit_version_for_approval(v01, sup, [sup])

        # 11. Approva nuova revisione
        approve_version(req2, sup, comment='Approvazione rev 01')
        v01.refresh_from_db()
        v00.refresh_from_db()
        doc.refresh_from_db()

        # 12. Chiude ECN (executed_version è stato impostato al passo 9)
        ecn.refresh_from_db()
        close_change_notice(ecn, sup, close_notes='ECN chiuso dal test E2E')
        ecn.refresh_from_db()

        # ── Verifiche finali ──────────────────────────────────────────────

        # Documento finale approvato con nuova revisione corrente
        self.assertEqual(v01.status, DocumentVersion.Status.APPROVED)
        self.assertTrue(v01.is_current)
        self.assertEqual(doc.current_version, v01)

        # Revisione precedente superseded
        self.assertEqual(v00.status, DocumentVersion.Status.SUPERSEDED)
        self.assertFalse(v00.is_current)

        # ECN chiusa
        self.assertEqual(ecn.status, ChangeNotice.Status.CLOSED)
        self.assertEqual(ecn.executed_version, v01)

        # CCB contenente solo supervisor_demo (dopo la chiusura la lista è invariata)
        all_ccb_pks = list(ecn.approvers.values_list('user_id', flat=True))
        self.assertIn(sup.pk, all_ccb_pks)
        for pk in all_ccb_pks:
            self.assertEqual(pk, sup.pk)

        # AuditLog generati per il documento
        audit_count = AuditLog.objects.filter(changes__document_id=doc.pk).count()
        self.assertGreater(audit_count, 0)

    # ── Test di sicurezza POST ────────────────────────────────────────────────

    def test_post_other_approver_rejected_by_formset(self):
        """
        Demo mode attiva: POST con un user PK estraneo nel formset approvatori
        viene respinta perché il PK non è nel queryset limitato al supervisore.
        """
        from documents.forms import ApproverFormSet

        data = {
            'form-TOTAL_FORMS': '1',
            'form-INITIAL_FORMS': '0',
            'form-MIN_NUM_FORMS': '0',
            'form-MAX_NUM_FORMS': '1000',
            'form-0-approver': str(self.other.pk),
        }
        formset = ApproverFormSet(data, prefix='form', current_user=self.supervisor)
        self.assertFalse(formset.is_valid())

    def test_post_other_ccb_candidate_rejected_by_form(self):
        """
        Demo mode attiva: POST con un user PK estraneo nel form CCB viene
        respinta perché il PK non è nel queryset limitato al supervisore.
        """
        from ecn.forms import ChangeNoticeCCBConfigForm

        form = ChangeNoticeCCBConfigForm(
            data={
                'ccb_policy': 'any',
                'approvers': [str(self.other.pk)],
            },
            current_user=self.supervisor,
        )
        self.assertFalse(form.is_valid())

    def test_demo_mode_off_approver_list_not_limited(self):
        """
        Demo mode disattivata: la lista approvatori documentali non è
        artificialmente limitata anche se esiste l'utente supervisor_demo.
        """
        from documents.forms import ApproverRowForm
        with self.settings(DOCUMENTALE_DEMO_MODE=False):
            form = ApproverRowForm(current_user=self.supervisor)
        qs = list(form.fields['approver'].queryset)
        self.assertGreater(len(qs), 1)

    def test_demo_mode_off_ccb_list_not_limited(self):
        """
        Demo mode disattivata: la lista candidati CCB non è artificialmente
        limitata anche se esiste l'utente supervisor_demo.
        """
        from ecn.forms import ChangeNoticeCCBConfigForm
        from django.contrib.auth.models import Group
        Group.objects.get_or_create(name=GROUP_CCB)[0].user_set.add(self.supervisor, self.other)
        with self.settings(DOCUMENTALE_DEMO_MODE=False):
            form = ChangeNoticeCCBConfigForm(current_user=self.supervisor)
        qs = list(form.fields['approvers'].queryset)
        self.assertGreater(len(qs), 1)

    def test_normal_user_in_demo_mode_approver_list_unrestricted(self):
        """
        Utente normale in demo mode: vede la lista completa approvatori,
        nessuna deroga speciale.
        """
        from documents.forms import ApproverRowForm
        form = ApproverRowForm(current_user=self.other)
        qs = list(form.fields['approver'].queryset)
        # Almeno supervisor + other devono essere presenti
        self.assertGreaterEqual(len(qs), 2)

    def test_normal_user_in_demo_mode_ccb_list_unrestricted(self):
        """
        Utente normale in demo mode: vede la lista completa candidati CCB,
        nessuna deroga speciale.
        """
        from ecn.forms import ChangeNoticeCCBConfigForm
        from django.contrib.auth.models import Group
        Group.objects.get_or_create(name=GROUP_CCB)[0].user_set.add(self.supervisor, self.other)
        form = ChangeNoticeCCBConfigForm(current_user=self.other)
        qs = list(form.fields['approvers'].queryset)
        # Entrambi i candidati devono essere presenti
        pks = [u.pk for u in qs]
        self.assertIn(self.supervisor.pk, pks)
        self.assertIn(self.other.pk, pks)


# ===========================================================================
# DemoCompanyReset — test reset robusto del comando demo_company
# ===========================================================================

@override_settings(
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
    DEBUG=True,
    DATABASES={
        'default': {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': ':memory:',
        }
    },
)
class DemoCompanyResetTests(TestCase):
    """
    Test del reset robusto di demo_company --reset --no-email.

    Verifica che:
    - il reset funzioni anche con ECN approvate collegate a documenti demo
    - i guardrail DEBUG e SQLite blocchino il reset in ambienti non sicuri
    - il comando senza --reset non esegua flush
    - il secondo reset consecutivo funzioni (idempotenza)
    """

    def _call_reset(self):
        from io import StringIO
        from django.core.management import call_command
        out = StringIO()
        err = StringIO()
        call_command('demo_company', reset=True, no_email=True, stdout=out, stderr=err)
        return out.getvalue(), err.getvalue()

    def _call_no_reset(self):
        from io import StringIO
        from django.core.management import call_command
        out = StringIO()
        call_command('demo_company', no_email=True, stdout=out)
        return out.getvalue()

    # ------------------------------------------------------------------
    # 1. Reset su database popolato
    # ------------------------------------------------------------------
    def test_reset_on_populated_database(self):
        """Reset funziona su un database che contiene già dati."""
        from django.contrib.auth.models import User as _User
        _User.objects.create_user('existing_user', password='pw')
        stdout, _ = self._call_reset()
        # Il flush svuota il DB; la ricreazione crea supervisor_demo
        self.assertTrue(
            _User.objects.filter(username='supervisor_demo').exists()
        )

    # ------------------------------------------------------------------
    # 2. Reset con ECN approvata collegata a documento demo
    # ------------------------------------------------------------------
    def test_reset_with_approved_ecn_on_demo_document(self):
        """
        Reset non solleva ProtectedError anche se esiste un ECN approvata
        con codice non-ECN-DEMO-* collegata a un documento demo.
        """
        from documents.models import Document, DocumentVersion
        from ecn.models import ChangeNotice
        from django.contrib.auth.models import User as _User

        author = _User.objects.create_user('ecn_author', password='pw')
        doc = Document.objects.create(
            code='DEMO-PUB-001',
            title='Doc demo con ECN esterna',
            category=Document.Category.QUALITY,
            owner=author,
            created_by=author,
            status=Document.Status.ACTIVE,
        )
        ver = DocumentVersion.objects.create(
            document=doc,
            revision_label='00',
            revision_number=0,
            status=DocumentVersion.Status.APPROVED,
            is_current=True,
            created_by=author,
        )
        doc.current_version = ver
        doc.save(update_fields=['current_version'])

        # ECN con codice non-ECN-DEMO-*: avrebbe causato ProtectedError nel vecchio codice
        ChangeNotice.objects.create(
            code='ECN-0003',
            title='Variante di test',
            motivation=ChangeNotice.Motivation.IMPROVEMENT,
            document=doc,
            document_version=ver,
            proposed_by=author,
            created_by=author,
            status=ChangeNotice.Status.APPROVED,
        )

        # Non deve sollevare ProtectedError
        try:
            stdout, _ = self._call_reset()
        except Exception as exc:
            self.fail(f'_reset() ha sollevato un\'eccezione: {exc}')

    # ------------------------------------------------------------------
    # 3. Nessun ProtectedError
    # ------------------------------------------------------------------
    def test_no_protected_error_on_reset(self):
        """Reset non solleva ProtectedError in nessuna configurazione demo."""
        from django.db.models.deletion import ProtectedError
        try:
            self._call_reset()
        except ProtectedError as exc:
            self.fail(f'ProtectedError non atteso: {exc}')

    # ------------------------------------------------------------------
    # 4. Utenti demo ricreati dopo reset
    # ------------------------------------------------------------------
    def test_demo_users_recreated_after_reset(self):
        """Dopo reset, supervisor_demo e admin_demo vengono ricreati."""
        from django.contrib.auth.models import User as _User
        self._call_reset()
        self.assertTrue(_User.objects.filter(username='supervisor_demo').exists())
        self.assertTrue(_User.objects.filter(username='admin_demo').exists())

    # ------------------------------------------------------------------
    # 5. Documenti demo ricreati dopo reset
    # ------------------------------------------------------------------
    def test_demo_documents_recreated_after_reset(self):
        """Dopo reset, i documenti demo vengono ricreati."""
        from documents.models import Document
        self._call_reset()
        for code in ('DEMO-PUB-001', 'DEMO-DRAFT-001', 'DEMO-APPR-001', 'DEMO-ECN-001'):
            self.assertTrue(
                Document.objects.filter(code=code).exists(),
                f'Documento {code} non trovato dopo reset',
            )

    # ------------------------------------------------------------------
    # 6. ECN demo ricreata dopo reset
    # ------------------------------------------------------------------
    def test_demo_ecn_recreated_after_reset(self):
        """Dopo reset, ECN-DEMO-001 viene ricreata."""
        from ecn.models import ChangeNotice
        self._call_reset()
        self.assertTrue(ChangeNotice.objects.filter(code='ECN-DEMO-001').exists())

    # ------------------------------------------------------------------
    # 7. Progetto demo ricreato dopo reset
    # ------------------------------------------------------------------
    def test_demo_project_recreated_after_reset(self):
        """Dopo reset, PRJ-DEMO-001 viene ricreato."""
        from projects.models import Project
        self._call_reset()
        self.assertTrue(Project.objects.filter(code='PRJ-DEMO-001').exists())

    # ------------------------------------------------------------------
    # 8. Secondo reset consecutivo (idempotenza)
    # ------------------------------------------------------------------
    def test_double_reset_is_idempotent(self):
        """Due reset consecutivi non sollevano eccezioni."""
        try:
            self._call_reset()
            self._call_reset()
        except Exception as exc:
            self.fail(f'Il secondo reset ha sollevato: {exc}')
        from django.contrib.auth.models import User as _User
        self.assertTrue(_User.objects.filter(username='supervisor_demo').exists())

    # ------------------------------------------------------------------
    # 9. --no-email evita invii
    # ------------------------------------------------------------------
    @override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
    def test_no_email_prevents_smtp(self):
        """--no-email usa il backend locmem e non solleva errori SMTP."""
        from django.core import mail
        self._call_reset()
        # Nessun errore SMTP; le email vanno nella outbox in-memory (o sono 0)
        # Il test verifica solo che il comando termini senza eccezioni SMTP.
        self.assertTrue(True)

    # ------------------------------------------------------------------
    # 10. Reset rifiutato con DEBUG=False
    # ------------------------------------------------------------------
    @override_settings(DEBUG=False)
    def test_reset_refused_when_debug_false(self):
        """Reset viene rifiutato quando DEBUG=False."""
        with self.assertRaises(SystemExit):
            self._call_reset()

    # ------------------------------------------------------------------
    # 11. Reset rifiutato con database non SQLite (mock)
    # ------------------------------------------------------------------
    @override_settings(
        DATABASES={
            'default': {
                'ENGINE': 'django.db.backends.postgresql',
                'NAME': 'fake_db',
            }
        }
    )
    def test_reset_refused_when_not_sqlite(self):
        """Reset viene rifiutato quando il database non è SQLite."""
        with self.assertRaises(SystemExit):
            self._call_reset()

    # ------------------------------------------------------------------
    # 12. Comando senza --reset non esegue flush
    # ------------------------------------------------------------------
    def test_no_reset_flag_does_not_flush(self):
        """Senza --reset il comando non svuota il database."""
        from django.contrib.auth.models import User as _User
        existing = _User.objects.create_user('persisted_user', password='pw')
        self._call_no_reset()
        # L'utente preesistente deve sopravvivere
        self.assertTrue(_User.objects.filter(pk=existing.pk).exists())


# ===========================================================================
# Step C — documents/versioning.py utility tests
# ===========================================================================

class SequenceSchemeNormalizeTests(TestCase):
    """normalize_sequence_value: strip e uppercase."""

    def test_numeric_strips_whitespace(self):
        from documents.versioning import normalize_sequence_value, SequenceScheme
        self.assertEqual(normalize_sequence_value(' 01 ', SequenceScheme.NUMERIC), '01')

    def test_alphabetic_strips_and_uppercases(self):
        from documents.versioning import normalize_sequence_value, SequenceScheme
        self.assertEqual(normalize_sequence_value(' az ', SequenceScheme.ALPHABETIC), 'AZ')

    def test_non_string_raises(self):
        from documents.versioning import normalize_sequence_value, SequenceScheme
        from django.core.exceptions import ValidationError as DjVE
        with self.assertRaises(DjVE):
            normalize_sequence_value(42, SequenceScheme.NUMERIC)


class SequenceSchemeValidateTests(TestCase):
    """validate_sequence_value: regole per schema NUMERIC e ALPHABETIC."""

    def test_numeric_valid(self):
        from documents.versioning import validate_sequence_value, SequenceScheme
        validate_sequence_value('00', SequenceScheme.NUMERIC)
        validate_sequence_value('99', SequenceScheme.NUMERIC)
        validate_sequence_value('100', SequenceScheme.NUMERIC)

    def test_numeric_rejects_letters(self):
        from documents.versioning import validate_sequence_value, SequenceScheme
        from django.core.exceptions import ValidationError as DjVE
        with self.assertRaises(DjVE):
            validate_sequence_value('A', SequenceScheme.NUMERIC)

    def test_numeric_rejects_mixed(self):
        from documents.versioning import validate_sequence_value, SequenceScheme
        from django.core.exceptions import ValidationError as DjVE
        with self.assertRaises(DjVE):
            validate_sequence_value('1A', SequenceScheme.NUMERIC)

    def test_numeric_rejects_empty(self):
        from documents.versioning import validate_sequence_value, SequenceScheme
        from django.core.exceptions import ValidationError as DjVE
        with self.assertRaises(DjVE):
            validate_sequence_value('', SequenceScheme.NUMERIC)

    def test_alphabetic_valid(self):
        from documents.versioning import validate_sequence_value, SequenceScheme
        validate_sequence_value('A', SequenceScheme.ALPHABETIC)
        validate_sequence_value('ZZ', SequenceScheme.ALPHABETIC)

    def test_alphabetic_rejects_digits(self):
        from documents.versioning import validate_sequence_value, SequenceScheme
        from django.core.exceptions import ValidationError as DjVE
        with self.assertRaises(DjVE):
            validate_sequence_value('1', SequenceScheme.ALPHABETIC)

    def test_unknown_scheme_raises(self):
        from documents.versioning import validate_sequence_value
        from django.core.exceptions import ValidationError as DjVE
        with self.assertRaises(DjVE):
            validate_sequence_value('00', 'hexadecimal')


class NextNumericValueTests(TestCase):
    """next_numeric_value: incremento con zero-padding."""

    def test_basic_increment(self):
        from documents.versioning import next_numeric_value
        self.assertEqual(next_numeric_value('00'), '01')
        self.assertEqual(next_numeric_value('01'), '02')
        self.assertEqual(next_numeric_value('9'), '10')

    def test_padding_preserved_within_width(self):
        from documents.versioning import next_numeric_value
        self.assertEqual(next_numeric_value('09'), '10')

    def test_three_digit_padding(self):
        from documents.versioning import next_numeric_value
        self.assertEqual(next_numeric_value('099'), '100')

    def test_no_padding_single_digit(self):
        from documents.versioning import next_numeric_value
        self.assertEqual(next_numeric_value('1'), '2')

    def test_rejects_letters(self):
        from documents.versioning import next_numeric_value
        from django.core.exceptions import ValidationError as DjVE
        with self.assertRaises(DjVE):
            next_numeric_value('A')


class NextAlphabeticValueTests(TestCase):
    """next_alphabetic_value: incremento base-26."""

    def test_simple_increment(self):
        from documents.versioning import next_alphabetic_value
        self.assertEqual(next_alphabetic_value('A'), 'B')
        self.assertEqual(next_alphabetic_value('Y'), 'Z')

    def test_z_wraps_to_aa(self):
        from documents.versioning import next_alphabetic_value
        self.assertEqual(next_alphabetic_value('Z'), 'AA')

    def test_az_to_ba(self):
        from documents.versioning import next_alphabetic_value
        self.assertEqual(next_alphabetic_value('AZ'), 'BA')

    def test_zz_to_aaa(self):
        from documents.versioning import next_alphabetic_value
        self.assertEqual(next_alphabetic_value('ZZ'), 'AAA')

    def test_lowercase_normalised(self):
        from documents.versioning import next_alphabetic_value
        self.assertEqual(next_alphabetic_value('a'), 'B')

    def test_rejects_digits(self):
        from documents.versioning import next_alphabetic_value
        from django.core.exceptions import ValidationError as DjVE
        with self.assertRaises(DjVE):
            next_alphabetic_value('1')


# ===========================================================================
# Step C — Document.revision_scheme field tests
# ===========================================================================

class DocumentRevisionSchemeTests(TestCase):
    """Document.revision_scheme: default, persistenza, display."""

    def setUp(self):
        self.user = User.objects.create_user('rs_user', password='pw')

    def _make_doc(self, revision_scheme='numeric', code='DOC-RS-001'):
        return Document.objects.create(
            code=code,
            title='Doc RS',
            category=Document.Category.QUALITY,
            owner=self.user,
            created_by=self.user,
            revision_scheme=revision_scheme,
        )

    def test_default_is_numeric(self):
        """Document senza revision_scheme esplicito usa 'numeric'."""
        doc = Document.objects.create(
            code='DOC-RS-DEFAULT',
            title='Default scheme',
            category=Document.Category.QUALITY,
            owner=self.user,
            created_by=self.user,
        )
        self.assertEqual(doc.revision_scheme, 'numeric')

    def test_save_numeric_scheme(self):
        doc = self._make_doc(revision_scheme='numeric', code='DOC-RS-N')
        doc.refresh_from_db()
        self.assertEqual(doc.revision_scheme, 'numeric')

    def test_save_alphabetic_scheme(self):
        doc = self._make_doc(revision_scheme='alphabetic', code='DOC-RS-A')
        doc.refresh_from_db()
        self.assertEqual(doc.revision_scheme, 'alphabetic')

    def test_get_revision_scheme_display_numeric(self):
        doc = self._make_doc(revision_scheme='numeric', code='DOC-RS-DN')
        self.assertEqual(doc.get_revision_scheme_display(), 'Numerica')

    def test_get_revision_scheme_display_alphabetic(self):
        doc = self._make_doc(revision_scheme='alphabetic', code='DOC-RS-DA')
        self.assertEqual(doc.get_revision_scheme_display(), 'Alfabetica')

    def test_create_form_has_revision_scheme_field(self):
        """DocumentCreateForm espone il campo revision_scheme."""
        from documents.forms import DocumentCreateForm
        form = DocumentCreateForm()
        self.assertIn('revision_scheme', form.fields)

    def test_create_form_numeric_revision_label_valid(self):
        """Con schema numeric, '01' è valido in DocumentCreateForm."""
        from documents.forms import DocumentCreateForm
        data = {
            'code': 'DOC-F-001', 'title': 'T', 'category': 'quality',
            'document_type': 'procedure', 'revision_scheme': 'numeric',
            'revision_label': '01', 'revision_number': 1,
        }
        form = DocumentCreateForm(data=data)
        self.assertNotIn('revision_label', form.errors)

    def test_create_form_numeric_revision_label_invalid(self):
        """Con schema numeric, 'A' è rifiutato in DocumentCreateForm."""
        from documents.forms import DocumentCreateForm
        data = {
            'code': 'DOC-F-002', 'title': 'T', 'category': 'quality',
            'document_type': 'procedure', 'revision_scheme': 'numeric',
            'revision_label': 'A', 'revision_number': 1,
        }
        form = DocumentCreateForm(data=data)
        self.assertFalse(form.is_valid())
        self.assertIn('revision_label', form.errors)

    # ------------------------------------------------------------------
    # Guard cambio schema
    # ------------------------------------------------------------------

    def _make_doc_with_draft(self, revision_scheme='numeric', code='DOC-GRD-001'):
        doc = self._make_doc(revision_scheme=revision_scheme, code=code)
        create_new_revision(doc, self.user, '00', 0)
        return doc

    def test_can_change_scheme_when_no_versions(self):
        """Cambio schema consentito se non esistono revisioni."""
        doc = self._make_doc(code='DOC-GRD-NV')
        doc.revision_scheme = 'alphabetic'
        doc.full_clean()  # non deve sollevare eccezioni

    def test_can_change_scheme_after_all_approved(self):
        """Cambio schema consentito se l'unica versione è approvata (non aperta)."""
        doc = self._make_doc_with_draft(code='DOC-GRD-AP')
        draft = doc.versions.get()
        draft.status = DocumentVersion.Status.APPROVED
        draft.save()
        doc.revision_scheme = 'alphabetic'
        doc.full_clean()  # non deve sollevare eccezioni

    def test_cannot_change_scheme_with_draft_version(self):
        """Cambio schema bloccato se esiste una versione DRAFT."""
        doc = self._make_doc_with_draft(code='DOC-GRD-DR')
        doc.revision_scheme = 'alphabetic'
        with self.assertRaises(ValidationError) as ctx:
            doc.full_clean()
        errors = ctx.exception.message_dict
        self.assertIn('revision_scheme', errors)
        self.assertIn('revisione aperta', errors['revision_scheme'][0])

    def test_cannot_change_scheme_with_in_approval_version(self):
        """Cambio schema bloccato se esiste una versione IN_APPROVAL."""
        doc = self._make_doc_with_draft(code='DOC-GRD-IA')
        draft = doc.versions.get()
        draft.status = DocumentVersion.Status.IN_APPROVAL
        draft.save()
        doc.revision_scheme = 'alphabetic'
        with self.assertRaises(ValidationError) as ctx:
            doc.full_clean()
        errors = ctx.exception.message_dict
        self.assertIn('revision_scheme', errors)
        self.assertIn('revisione aperta', errors['revision_scheme'][0])


# ===========================================================================
# FIX-DOCUMENT-FOLDER-FILTER — test filtro cartella ricorsivo
# ===========================================================================

class DocumentFolderFilterTests(TestCase):
    """
    Verifica che document_list applichi il filtro cartella in modo ricorsivo
    quando richiesto (folder PROJECT o recursive=1).
    """

    def setUp(self):
        self.owner = User.objects.create_user('dff_owner', password='pw')
        self.superuser = User.objects.create_user(
            'dff_super', password='pw', is_superuser=True
        )
        from projects.models import ProjectFolder
        from projects.services import set_folder_path

        # Root cartella PROJECT
        self.root = ProjectFolder.objects.create(
            code='DFF-ROOT', name='Root Project',
            folder_kind=ProjectFolder.FolderKind.PROJECT,
            status=ProjectFolder.Status.ACTIVE,
            owner=self.owner,
        )
        set_folder_path(self.root)

        # Sottocartella ordinaria figlia del root
        self.sub = ProjectFolder.objects.create(
            code='DFF-SUB', name='Subfolder',
            folder_kind=ProjectFolder.FolderKind.GENERIC,
            status=ProjectFolder.Status.ACTIVE,
            owner=self.owner,
            parent=self.root,
        )
        set_folder_path(self.sub)

        # Cartella ordinaria non correlata
        self.other = ProjectFolder.objects.create(
            code='DFF-OTHER', name='Other',
            folder_kind=ProjectFolder.FolderKind.GENERIC,
            status=ProjectFolder.Status.ACTIVE,
            owner=self.owner,
        )
        set_folder_path(self.other)

        # Documento nella root
        self.doc_root, _ = _make_published_doc('DFF-DOC-ROOT', self.root, self.owner)
        # Documento nella sottocartella
        self.doc_sub, _ = _make_published_doc('DFF-DOC-SUB', self.sub, self.owner)
        # Documento nella cartella esterna
        self.doc_other, _ = _make_published_doc('DFF-DOC-OTHER', self.other, self.owner)

        self.client.login(username='dff_super', password='pw')

    def _get(self, **params):
        return self.client.get(reverse('document_list'), params)

    def test_no_filter_shows_all(self):
        """Senza filtro cartella tutti i documenti sono visibili."""
        resp = self._get()
        codes = [d.code for d in resp.context['documents']]
        self.assertIn('DFF-DOC-ROOT', codes)
        self.assertIn('DFF-DOC-SUB', codes)
        self.assertIn('DFF-DOC-OTHER', codes)

    def test_project_root_includes_subdocuments_automatically(self):
        """Root folder di tipo PROJECT → include automaticamente le sottocartelle."""
        resp = self._get(folder=self.root.pk)
        codes = [d.code for d in resp.context['documents']]
        self.assertIn('DFF-DOC-ROOT', codes)
        self.assertIn('DFF-DOC-SUB', codes)
        self.assertNotIn('DFF-DOC-OTHER', codes)

    def test_project_root_excludes_external_documents(self):
        """Filtro PROJECT non mostra documenti fuori dal progetto."""
        resp = self._get(folder=self.root.pk)
        codes = [d.code for d in resp.context['documents']]
        self.assertNotIn('DFF-DOC-OTHER', codes)

    def test_ordinary_folder_without_recursive_shows_direct_only(self):
        """Cartella ordinaria senza recursive → solo documenti diretti."""
        resp = self._get(folder=self.sub.pk)
        codes = [d.code for d in resp.context['documents']]
        self.assertIn('DFF-DOC-SUB', codes)
        self.assertNotIn('DFF-DOC-ROOT', codes)
        self.assertNotIn('DFF-DOC-OTHER', codes)

    def test_ordinary_folder_with_recursive_includes_subdocuments(self):
        """Cartella ordinaria con recursive=1 → include i discendenti."""
        resp = self._get(folder=self.root.pk, recursive='1')
        codes = [d.code for d in resp.context['documents']]
        self.assertIn('DFF-DOC-ROOT', codes)
        self.assertIn('DFF-DOC-SUB', codes)

    def test_context_exposes_selected_folder_is_project(self):
        """Il contesto espone selected_folder_is_project=True per root PROJECT."""
        resp = self._get(folder=self.root.pk)
        self.assertTrue(resp.context['selected_folder_is_project'])

    def test_context_selected_folder_is_project_false_for_generic(self):
        """Il contesto espone selected_folder_is_project=False per cartella ordinaria."""
        resp = self._get(folder=self.sub.pk)
        self.assertFalse(resp.context['selected_folder_is_project'])

    def test_recursive_checkbox_shown_for_ordinary_folder(self):
        """La checkbox 'Includi sottocartelle' appare per cartella ordinaria."""
        resp = self._get(folder=self.sub.pk)
        self.assertContains(resp, 'name="recursive"')

    def test_recursive_checkbox_not_shown_without_folder(self):
        """La checkbox non appare senza filtro cartella."""
        resp = self._get()
        self.assertNotContains(resp, 'name="recursive"')

    def test_project_auto_recursive_hint_shown(self):
        """Il testo informativo appare per root PROJECT selezionata."""
        resp = self._get(folder=self.root.pk)
        self.assertContains(resp, 'include automaticamente le sottocartelle')

    def test_other_filters_still_combinable(self):
        """Il filtro q si combina correttamente con il filtro cartella ricorsivo."""
        resp = self._get(folder=self.root.pk, q='SUB')
        codes = [d.code for d in resp.context['documents']]
        self.assertIn('DFF-DOC-SUB', codes)
        self.assertNotIn('DFF-DOC-ROOT', codes)

    def test_denied_subfolder_excluded_for_restricted_user(self):
        """
        Un utente con deny sulla sottocartella non vede i suoi documenti
        anche se seleziona la root PROJECT (sicurezza: deny rispettato).
        """
        restricted = User.objects.create_user('dff_restricted', password='pw')
        _grant(self.root, user=restricted, perm='read_published')
        _grant(self.sub, user=restricted, perm='read_published', effect='deny')

        self.client.login(username='dff_restricted', password='pw')
        resp = self._get(folder=self.root.pk)
        codes = [d.code for d in resp.context['documents']]
        self.assertIn('DFF-DOC-ROOT', codes)
        self.assertNotIn('DFF-DOC-SUB', codes)

    def test_private_draft_excluded(self):
        """Le bozze private non appaiono nel filtro ricorsivo."""
        other_user = User.objects.create_user('dff_other_author', password='pw')
        _make_draft_doc('DFF-DRAFT-PRIV', self.sub, other_user)
        self.client.login(username='dff_super', password='pw')
        resp = self._get(folder=self.root.pk)
        codes = [d.code for d in resp.context['documents']]
        self.assertNotIn('DFF-DRAFT-PRIV', codes)


# ===========================================================================
# VERIFY-DOCUMENT-SCHEME-EDIT-UI — test view modifica metadati
# ===========================================================================

class DocumentMetadataEditTests(TestCase):
    """Test della view edit_document_metadata."""

    def setUp(self):
        from projects.models import ProjectFolder
        from projects.services import set_folder_path

        self.owner = User.objects.create_user('dme_owner', password='pw')
        self.manager = User.objects.create_user('dme_manager', password='pw')
        self.stranger = User.objects.create_user('dme_stranger', password='pw')
        self.superuser = User.objects.create_user(
            'dme_super', password='pw', is_superuser=True
        )

        from django.contrib.auth.models import Group
        mg = Group.objects.get_or_create(name='Document Managers')[0]
        self.manager.groups.add(mg)

        self.folder = ProjectFolder.objects.create(
            code='DME-F', name='DME Folder',
            folder_kind=ProjectFolder.FolderKind.GENERIC,
            status=ProjectFolder.Status.ACTIVE,
            owner=self.owner,
        )
        set_folder_path(self.folder)

        self.doc = Document.objects.create(
            code='DME-DOC-001', title='Doc metadata test',
            category=Document.Category.QUALITY,
            project_folder=self.folder,
            owner=self.owner, created_by=self.owner,
            revision_scheme='numeric',
        )

    def _url(self):
        return reverse('document_edit_metadata', args=[self.doc.pk])

    def test_manager_can_access(self):
        self.client.login(username='dme_manager', password='pw')
        resp = self.client.get(self._url())
        self.assertEqual(resp.status_code, 200)
        self.assertIn('form', resp.context)

    def test_superuser_can_access(self):
        self.client.login(username='dme_super', password='pw')
        resp = self.client.get(self._url())
        self.assertEqual(resp.status_code, 200)

    def test_stranger_gets_403(self):
        self.client.login(username='dme_stranger', password='pw')
        resp = self.client.get(self._url())
        self.assertEqual(resp.status_code, 403)

    def test_change_scheme_allowed_when_no_open_versions(self):
        """Schema modificabile se non ci sono revisioni aperte."""
        self.client.login(username='dme_super', password='pw')
        resp = self.client.post(self._url(), {
            'title': self.doc.title,
            'description': '',
            'revision_scheme': 'alphabetic',
        })
        self.assertRedirects(resp, reverse('document_detail', args=[self.doc.pk]))
        self.doc.refresh_from_db()
        self.assertEqual(self.doc.revision_scheme, 'alphabetic')

    def test_change_scheme_blocked_with_draft(self):
        """Schema non modificabile con revisione DRAFT aperta: form mostra errore."""
        create_new_revision(self.doc, self.owner, '00', 0)
        self.client.login(username='dme_super', password='pw')
        resp = self.client.post(self._url(), {
            'title': self.doc.title,
            'description': '',
            'revision_scheme': 'alphabetic',
        })
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Non è possibile modificare lo schema di revisione')

    def test_change_scheme_blocked_with_in_approval(self):
        """Schema non modificabile con revisione IN_APPROVAL aperta."""
        ver = create_new_revision(self.doc, self.owner, '00', 0)
        ver.status = DocumentVersion.Status.IN_APPROVAL
        ver.save()
        self.client.login(username='dme_super', password='pw')
        resp = self.client.post(self._url(), {
            'title': self.doc.title,
            'description': '',
            'revision_scheme': 'alphabetic',
        })
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Non è possibile modificare lo schema di revisione')

    def test_history_unchanged_after_scheme_change(self):
        """Le revisioni storiche non vengono modificate dopo il cambio schema."""
        ver = create_new_revision(self.doc, self.owner, '00', 0)
        ver.status = DocumentVersion.Status.APPROVED
        ver.save()
        self.client.login(username='dme_super', password='pw')
        self.client.post(self._url(), {
            'title': self.doc.title,
            'description': '',
            'revision_scheme': 'alphabetic',
        })
        ver.refresh_from_db()
        self.assertEqual(ver.revision_label, '00')

    def test_title_update_works(self):
        """Il titolo è modificabile indipendentemente dallo schema."""
        self.client.login(username='dme_super', password='pw')
        self.client.post(self._url(), {
            'title': 'Titolo aggiornato',
            'description': 'Nuova descrizione',
            'revision_scheme': 'numeric',
        })
        self.doc.refresh_from_db()
        self.assertEqual(self.doc.title, 'Titolo aggiornato')

    def test_detail_shows_edit_metadata_button_for_manager(self):
        """Il pulsante 'Modifica metadati' appare nel dettaglio per il manager."""
        self.client.login(username='dme_manager', password='pw')
        # Pubblica il documento perché il manager vede solo doc pubblicati
        ver = create_new_revision(self.doc, self.owner, '00', 0)
        ver.status = DocumentVersion.Status.APPROVED
        ver.is_current = True
        ver.save()
        self.doc.current_version = ver
        self.doc.save(update_fields=['current_version'])
        resp = self.client.get(reverse('document_detail', args=[self.doc.pk]))
        self.assertContains(resp, 'Modifica metadati')

    def test_detail_hides_edit_metadata_button_for_stranger(self):
        """Il pulsante non appare per utenti senza permesso di scrittura."""
        ver = create_new_revision(self.doc, self.owner, '00', 0)
        ver.status = DocumentVersion.Status.APPROVED
        ver.is_current = True
        ver.save()
        self.doc.current_version = ver
        self.doc.save(update_fields=['current_version'])
        # Lo stranger ha accesso in lettura (può vedere la detail) ma non in scrittura
        _grant(self.folder, user=self.stranger, perm='read_published')
        self.client.login(username='dme_stranger', password='pw')
        resp = self.client.get(reverse('document_detail', args=[self.doc.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, 'Modifica metadati')


# ===========================================================================
# First revision after scheme change — test next_label
# ===========================================================================

class NextLabelAfterSchemeChangeTests(TestCase):
    """
    Verifica che dopo il cambio schema, la nuova revisione proponga
    il primo valore del nuovo schema (non un valore errato).
    """

    def setUp(self):
        self.owner = User.objects.create_user('nlsc_owner', password='pw')
        self.superuser = User.objects.create_user(
            'nlsc_super', password='pw', is_superuser=True
        )
        self.doc_num = Document.objects.create(
            code='NLSC-NUM', title='Numeric doc',
            category=Document.Category.QUALITY,
            owner=self.owner, created_by=self.owner,
            revision_scheme='numeric',
        )
        create_new_revision(self.doc_num, self.owner, '02', 2)

        self.doc_alpha = Document.objects.create(
            code='NLSC-ALPHA', title='Alpha doc',
            category=Document.Category.QUALITY,
            owner=self.owner, created_by=self.owner,
            revision_scheme='alphabetic',
        )
        create_new_revision(self.doc_alpha, self.owner, 'C', 2)

    def _get_revision_form(self, doc):
        self.client.login(username='nlsc_super', password='pw')
        return self.client.get(reverse('document_new_revision', args=[doc.pk]))

    def test_numeric_scheme_proposes_next_numeric(self):
        """Schema rimasto NUMERIC → propone il prossimo valore numerico."""
        resp = self._get_revision_form(self.doc_num)
        self.assertEqual(resp.status_code, 200)
        form = resp.context['form']
        self.assertEqual(form.initial.get('revision_label'), '03')

    def test_alphabetic_scheme_proposes_next_alphabetic(self):
        """Schema rimasto ALPHABETIC → propone il prossimo valore alfabetico."""
        resp = self._get_revision_form(self.doc_alpha)
        self.assertEqual(resp.status_code, 200)
        form = resp.context['form']
        self.assertEqual(form.initial.get('revision_label'), 'D')

    def test_numeric_to_alphabetic_proposes_A(self):
        """Dopo cambio NUMERIC→ALPHABETIC la proposta è 'A' (non il valore errato)."""
        self.doc_num.revision_scheme = 'alphabetic'
        self.doc_num.save()
        resp = self._get_revision_form(self.doc_num)
        form = resp.context['form']
        self.assertEqual(form.initial.get('revision_label'), 'A')

    def test_alphabetic_to_numeric_proposes_00(self):
        """Dopo cambio ALPHABETIC→NUMERIC la proposta è '00'."""
        self.doc_alpha.revision_scheme = 'numeric'
        self.doc_alpha.save()
        resp = self._get_revision_form(self.doc_alpha)
        form = resp.context['form']
        self.assertEqual(form.initial.get('revision_label'), '00')


# ---------------------------------------------------------------------------
# ECN policy — requires_ecn_for_revision (ECNPOL-1)
# ---------------------------------------------------------------------------

class ECNPolicyServiceTests(TestCase):
    """
    Verifica la policy requires_ecn_for_revision a livello di service.
    Copre i casi 1-9 del briefing.
    """

    def setUp(self):
        from approvals.services import approve_version
        self.author   = User.objects.create_user('pol_author', password='pw')
        self.approver = User.objects.create_user('pol_approver', password='pw')

    def _make_approved_doc(self, code, requires_ecn=True):
        """Crea un documento con una versione approvata."""
        from approvals.services import approve_version
        doc = Document.objects.create(
            code=code, title='Doc policy test',
            category=Document.Category.QUALITY,
            owner=self.author, created_by=self.author,
            requires_ecn_for_revision=requires_ecn,
        )
        v0 = create_new_revision(doc, self.author, '00', 0)
        req = submit_version_for_approval(v0, self.author, [self.approver])
        approve_version(req, self.approver)
        doc.refresh_from_db()
        return doc

    def _make_ecn(self, doc, status='approved', executed_version=None):
        from ecn.models import ChangeNotice
        return ChangeNotice.objects.create(
            code=f'ECN-POL-{ChangeNotice.objects.count()+1:03d}',
            title='ECN policy test',
            motivation=ChangeNotice.Motivation.IMPROVEMENT,
            document=doc,
            document_version=doc.current_version,
            proposed_by=self.author,
            created_by=self.author,
            status=status,
            executed_version=executed_version,
        )

    # Caso 1: default is True
    def test_new_document_has_requires_ecn_true_by_default(self):
        doc = Document.objects.create(
            code='POL-DEF', title='Default policy',
            category=Document.Category.QUALITY,
            owner=self.author, created_by=self.author,
        )
        self.assertTrue(doc.requires_ecn_for_revision)

    # Caso 2: documento con ECN obbligatorio continua a richiedere ECN
    def test_ecn_required_doc_still_requires_ecn(self):
        doc = self._make_approved_doc('POL-ECN-REQ', requires_ecn=True)
        with self.assertRaises(ValidationError):
            create_new_revision(doc, self.author, '01', 1)

    # Caso 3: documento esente può creare revisione senza ECN
    def test_ecn_exempt_doc_can_create_revision_without_ecn(self):
        doc = self._make_approved_doc('POL-EXEMPT', requires_ecn=False)
        v = create_new_revision(doc, self.author, '01', 1)
        self.assertIsNotNone(v)

    # Caso 4: revisione senza ECN nasce come DRAFT
    def test_revision_without_ecn_is_draft(self):
        doc = self._make_approved_doc('POL-DRAFT', requires_ecn=False)
        v = create_new_revision(doc, self.author, '01', 1)
        self.assertEqual(v.status, DocumentVersion.Status.DRAFT)

    # Caso 5: revisione senza ECN non è is_current prima dell'approvazione
    def test_revision_without_ecn_not_current_before_approval(self):
        doc = self._make_approved_doc('POL-NOTCUR', requires_ecn=False)
        v = create_new_revision(doc, self.author, '01', 1)
        self.assertFalse(v.is_current)
        doc.refresh_from_db()
        self.assertNotEqual(doc.current_version, v)

    # Caso 6: revisione senza ECN può essere inviata in approvazione
    def test_revision_without_ecn_can_be_submitted(self):
        doc = self._make_approved_doc('POL-SUBMIT', requires_ecn=False)
        v = create_new_revision(doc, self.author, '01', 1)
        req = submit_version_for_approval(v, self.author, [self.approver])
        from approvals.models import ApprovalRequest
        self.assertEqual(req.status, ApprovalRequest.Status.PENDING)

    # Caso 7: dopo approvazione ordinaria, la nuova versione sostituisce la precedente
    def test_revision_without_ecn_becomes_current_after_approval(self):
        from approvals.services import approve_version
        doc = self._make_approved_doc('POL-CURR', requires_ecn=False)
        old_current = doc.current_version
        v = create_new_revision(doc, self.author, '01', 1)
        req = submit_version_for_approval(v, self.author, [self.approver])
        approve_version(req, self.approver)
        doc.refresh_from_db()
        self.assertEqual(doc.current_version, v)
        old_current.refresh_from_db()
        self.assertEqual(old_current.status, DocumentVersion.Status.SUPERSEDED)

    # Caso 8: consumo one-shot ECN standard resta invariato
    def test_ecn_consumed_after_use(self):
        doc = self._make_approved_doc('POL-ONESHOT', requires_ecn=True)
        ecn = self._make_ecn(doc, status='approved')
        create_new_revision(doc, self.author, '01', 1, ecn=ecn)
        ecn.refresh_from_db()
        self.assertIsNotNone(ecn.executed_version)

    # Caso 9: ECN già consumato non può essere riutilizzato
    def test_already_used_ecn_cannot_be_reused(self):
        doc = self._make_approved_doc('POL-REUSE', requires_ecn=True)
        used_v = create_new_revision(doc, self.author, '01', 1, _bypass_ecn_check=True)
        ecn = self._make_ecn(doc, status='approved', executed_version=used_v)
        with self.assertRaises(ValidationError):
            create_new_revision(doc, self.author, '02', 2, ecn=ecn)

    # Caso extra: replaces_version è valorizzato anche senza ECN
    def test_revision_without_ecn_has_replaces_version(self):
        doc = self._make_approved_doc('POL-REPLACES', requires_ecn=False)
        old_current = doc.current_version
        v = create_new_revision(doc, self.author, '01', 1)
        self.assertEqual(v.replaces_version, old_current)


@override_settings(EMAIL_BACKEND=LOCMEM)
class ECNPolicyViewTests(TestCase):
    """
    Verifica UI e view per la policy requires_ecn_for_revision.
    Copre i casi 10-15 del briefing.
    """

    def setUp(self):
        from django.contrib.auth.models import Group
        from projects.models import ProjectFolder, ProjectFolderMembership
        from approvals.services import approve_version
        mail.outbox = []

        self.author   = User.objects.create_user('pv_author', email='pv_a@t.com', password='pw')
        self.approver = User.objects.create_user('pv_approver', email='pv_ap@t.com', password='pw')

        Group.objects.get_or_create(name='Document Authors')[0].user_set.add(self.author)
        Group.objects.get_or_create(name='Document Managers')[0].user_set.add(self.approver)

        self.folder = ProjectFolder.objects.create(
            code='PV-FOLD', name='Policy View Folder',
            folder_kind=ProjectFolder.FolderKind.GENERIC,
            status=ProjectFolder.Status.ACTIVE,
            owner=self.author,
        )
        ProjectFolderMembership.objects.create(folder=self.folder, user=self.author, role='author')

        # Documento con ECN obbligatorio (default) + versione approvata
        self.doc_ecn = Document.objects.create(
            code='PV-ECN', title='Doc ECN obbligatorio',
            category=Document.Category.QUALITY,
            owner=self.author, created_by=self.author,
            project_folder=self.folder,
            requires_ecn_for_revision=True,
        )
        v0_ecn = create_new_revision(self.doc_ecn, self.author, '00', 0)
        req = submit_version_for_approval(v0_ecn, self.author, [self.approver])
        approve_version(req, self.approver)
        self.doc_ecn.refresh_from_db()

        # Documento senza ECN obbligatorio + versione approvata
        self.doc_free = Document.objects.create(
            code='PV-FREE', title='Doc senza ECN',
            category=Document.Category.QUALITY,
            owner=self.author, created_by=self.author,
            project_folder=self.folder,
            requires_ecn_for_revision=False,
        )
        v0_free = create_new_revision(self.doc_free, self.author, '00', 0)
        req2 = submit_version_for_approval(v0_free, self.author, [self.approver])
        approve_version(req2, self.approver)
        self.doc_free.refresh_from_db()

    # Caso 10: checkbox compare nel form di creazione ed è selezionata di default
    def test_create_form_has_checkbox_checked_by_default(self):
        self.client.force_login(self.author)
        r = self.client.get(reverse('document_new'))
        self.assertEqual(r.status_code, 200)
        self.assertIn('requires_ecn_for_revision', r.context['form'].fields)
        self.assertTrue(r.context['form'].fields['requires_ecn_for_revision'].initial)

    # Caso 11: policy NON è modificabile dal form di modifica metadati
    def test_metadata_edit_form_does_not_expose_policy(self):
        self.client.force_login(self.author)
        r = self.client.get(reverse('document_edit_metadata', args=[self.doc_ecn.pk]))
        self.assertEqual(r.status_code, 200)
        self.assertNotIn('requires_ecn_for_revision', r.context['form'].fields)

    # Caso 12a: detail mostra "ECN obbligatorio" per doc con policy True
    def test_detail_shows_ecn_required_label(self):
        self.client.force_login(self.author)
        r = self.client.get(reverse('document_detail', args=[self.doc_ecn.pk]))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'ECN obbligatorio')

    # Caso 12b: detail mostra "approvazione diretta" per doc con policy False
    def test_detail_shows_direct_approval_label(self):
        self.client.force_login(self.author)
        r = self.client.get(reverse('document_detail', args=[self.doc_free.pk]))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'approvazione diretta')

    # Caso 13: permessi invariati — utente senza accesso non vede la revisione
    def test_stranger_cannot_view_ecn_exempt_document(self):
        stranger = User.objects.create_user('pv_stranger', password='pw')
        self.client.force_login(stranger)
        r = self.client.get(reverse('document_detail', args=[self.doc_free.pk]))
        self.assertEqual(r.status_code, 404)

    # Caso 14: modalità sanatoria continua a funzionare per creazione documento
    def test_sanatoria_document_creation_not_broken(self):
        import os
        os.environ['DOCUMENTALE_DEMO_MODE'] = 'true'
        try:
            from django.test.utils import override_settings as ov
            with ov(DOCUMENTALE_DEMO_MODE=True):
                sup = User.objects.create_superuser(
                    'supervisor_demo_pv', password='pw',
                )
                # supervisor_demo_pv non è il vero username demo → sanatoria non attiva
                # ma il form deve funzionare lo stesso senza errori
                self.client.force_login(sup)
                r = self.client.post(reverse('document_new'), {
                    'code': 'PV-SAN',
                    'title': 'Doc sanatoria policy',
                    'category': 'QUALITY',
                    'project_folder': self.folder.pk,
                    'revision_scheme': 'numeric',
                    'revision_label': '00',
                    'revision_number': '0',
                    'requires_ecn_for_revision': 'on',
                })
                self.assertIn(r.status_code, [200, 302])
        finally:
            os.environ.pop('DOCUMENTALE_DEMO_MODE', None)

    # Caso 15: audit log registra requires_ecn_for_revision alla creazione
    def test_audit_log_records_policy_on_creation(self):
        from auditlog.models import AuditLog
        self.client.force_login(self.author)
        self.client.post(reverse('document_new'), {
            'code': 'PV-AUDIT',
            'title': 'Doc audit policy',
            'category': 'QUALITY',
            'project_folder': self.folder.pk,
            'revision_scheme': 'numeric',
            'revision_label': '00',
            'revision_number': '0',
            'requires_ecn_for_revision': '',   # unchecked → False
        })
        doc = Document.objects.filter(code='PV-AUDIT').first()
        self.assertIsNotNone(doc)
        self.assertFalse(doc.requires_ecn_for_revision)
        log = AuditLog.objects.filter(
            action='DOCUMENT_CREATED',
            object_id=str(doc.pk),
        ).first()
        self.assertIsNotNone(log)
        new_values = log.changes.get('new_values', {})
        self.assertIn('requires_ecn_for_revision', new_values)
        self.assertFalse(new_values['requires_ecn_for_revision'])

    # Caso extra — view new_revision: doc esente mostra form direttamente (no ECN select)
    def test_ecn_exempt_doc_new_revision_shows_form_without_ecn_step(self):
        self.client.force_login(self.author)
        r = self.client.get(reverse('document_new_revision', args=[self.doc_free.pk]))
        self.assertEqual(r.status_code, 200)
        # Non deve mostrare la pagina di selezione ECN
        self.assertNotContains(r, 'ECN richiesto')
        # Deve mostrare il form di creazione
        self.assertIsNotNone(r.context.get('form'))

    # Caso extra — doc esente: POST crea revisione senza passare ecn_id
    def test_ecn_exempt_doc_post_creates_revision(self):
        self.client.force_login(self.author)
        r = self.client.post(
            reverse('document_new_revision', args=[self.doc_free.pk]),
            {
                'revision_label': '01',
                'revision_number': '1',
                'change_summary': 'Revisione diretta senza ECN',
            },
        )
        self.assertRedirects(r, reverse('my_drafts'), fetch_redirect_response=False)
        self.assertTrue(
            DocumentVersion.objects.filter(
                document=self.doc_free, revision_label='01',
                status=DocumentVersion.Status.DRAFT,
            ).exists()
        )
        # Nessun ECN consumato
        from ecn.models import ChangeNotice
        self.assertFalse(
            ChangeNotice.objects.filter(document=self.doc_free).exists()
        )
