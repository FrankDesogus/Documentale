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
                'revision_label': '00',
                'revision_number': '0',
            })
            response = self.client.post(reverse('document_new'), {
                'code': 'UI-DUP',
                'title': 'Secondo con stesso codice',
                'category': 'QUALITY',
                'project_folder': self.folder.pk,
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
        self.draft = create_new_revision(self.document, self.author, 'A', 1)

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
            'revision_label': 'A',
            'revision_number': '1',
            'change_summary': 'Sommario aggiornato',
        })
        self.draft.refresh_from_db()
        self.assertEqual(self.draft.change_summary, 'Sommario aggiornato')

    def test_edit_draft_redirects_to_my_drafts(self):
        self.client.login(username='ev_author', password='pw')
        response = self.client.post(reverse('version_edit', args=[self.draft.pk]), {
            'revision_label': 'A',
            'revision_number': '1',
            'change_summary': 'Aggiornamento',
        })
        self.assertRedirects(response, reverse('my_drafts'))

    def test_edit_draft_remains_draft(self):
        self.client.login(username='ev_author', password='pw')
        self.client.post(reverse('version_edit', args=[self.draft.pk]), {
            'revision_label': 'A',
            'revision_number': '1',
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
                'revision_label': 'A',
                'revision_number': '1',
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
            'revision_label': 'A',
            'revision_number': '1',
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
            status=Project.Status.ACTIVE,
            project_type=Project.ProjectType.INTERNAL,
            folder=self.folder,
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
