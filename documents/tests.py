import shutil
import tempfile

from django.contrib.auth.models import User
from django.core import mail
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

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

    def test_staff_can_download_superseded_version(self):
        with self.settings(MEDIA_ROOT=self.temp_media):
            v1 = self._make_version_with_file('A', 1)
            self._approve_version(v1)
            v2 = self._make_version_with_file('B', 2)
            self._approve_version(v2)
            v1.refresh_from_db()
            self.assertEqual(v1.status, DocumentVersion.Status.SUPERSEDED)
            self.client.login(username='dl_staff', password='pw')
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
