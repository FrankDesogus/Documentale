from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse

from projects.models import FolderPermissionGrant, Project, ProjectFolder, ProjectFolderMembership, ProjectRevision, ProjectRevisionItem

EMAIL_LOCMEM = 'django.core.mail.backends.locmem.EmailBackend'


def make_folder(code='F-001', name='Test', kind=ProjectFolder.FolderKind.GENERIC, owner=None, parent=None):
    return ProjectFolder.objects.create(
        code=code,
        name=name,
        folder_kind=kind,
        parent=parent,
        status=ProjectFolder.Status.ACTIVE,
        owner=owner,
    )


class FolderListViewTests(TestCase):
    """folder_list: visibile a tutti gli autenticati; utenti normali vedono solo le proprie cartelle."""

    def setUp(self):
        from django.contrib.auth.models import Group
        # MB1: is_staff non concede visibilità globale; Document Manager vede tutte le cartelle
        self.user = User.objects.create_user('fl_user', password='pw', is_staff=True)
        self.owner = User.objects.create_user('fl_owner', password='pw')
        Group.objects.get_or_create(name='Document Managers')[0].user_set.add(self.user)

    def test_folder_list_requires_login(self):
        response = self.client.get(reverse('folder_list'))
        self.assertRedirects(response, '/accounts/login/?next=/folders/')

    def test_authenticated_user_sees_folder_list(self):
        make_folder(code='F-001', owner=self.owner)
        self.client.login(username='fl_user', password='pw')
        response = self.client.get(reverse('folder_list'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'F-001')

    def test_only_root_folders_shown(self):
        root = make_folder(code='ROOT', owner=self.owner)
        make_folder(code='CHILD', owner=self.owner, parent=root)
        self.client.login(username='fl_user', password='pw')
        response = self.client.get(reverse('folder_list'))
        codes = [f.code for f in response.context['folders']]
        self.assertIn('ROOT', codes)
        self.assertNotIn('CHILD', codes)

    def test_archived_folder_not_shown_in_list(self):
        f = make_folder(code='F-ARC', owner=self.owner)
        f.status = ProjectFolder.Status.ARCHIVED
        f.save(update_fields=['status'])
        self.client.login(username='fl_user', password='pw')
        response = self.client.get(reverse('folder_list'))
        codes = [f.code for f in response.context['folders']]
        self.assertNotIn('F-ARC', codes)

    def test_normal_user_without_membership_sees_no_folders(self):
        normal = User.objects.create_user('fl_normal', password='pw')
        make_folder(code='F-PRIV', owner=self.owner)
        self.client.login(username='fl_normal', password='pw')
        response = self.client.get(reverse('folder_list'))
        self.assertEqual(response.status_code, 200)
        codes = [f.code for f in response.context['folders']]
        self.assertNotIn('F-PRIV', codes)

    def test_normal_user_with_membership_sees_own_folder(self):
        normal = User.objects.create_user('fl_member', password='pw')
        folder = make_folder(code='F-MINE', owner=self.owner)
        ProjectFolderMembership.objects.create(folder=folder, user=normal, role='reader')
        self.client.login(username='fl_member', password='pw')
        response = self.client.get(reverse('folder_list'))
        codes = [f.code for f in response.context['folders']]
        self.assertIn('F-MINE', codes)


class FolderDetailViewTests(TestCase):

    def setUp(self):
        self.user = User.objects.create_user('fd_user', password='pw')
        self.owner = User.objects.create_user('fd_owner', password='pw')
        self.root = make_folder(code='FD-ROOT', name='Radice', owner=self.owner)
        self.child = make_folder(code='FD-CHILD', name='Figlia', owner=self.owner, parent=self.root)
        # fd_user ha membership reader su FD-ROOT per poter accedere
        ProjectFolderMembership.objects.create(folder=self.root, user=self.user, role='reader')

    def test_folder_detail_requires_login(self):
        response = self.client.get(reverse('folder_detail', args=[self.root.pk]))
        self.assertRedirects(response, f'/accounts/login/?next=/folders/{self.root.pk}/')

    def test_folder_detail_shows_subfolders(self):
        self.client.login(username='fd_user', password='pw')
        response = self.client.get(reverse('folder_detail', args=[self.root.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'FD-CHILD')
        sub_codes = [s.code for s in response.context['subfolders']]
        self.assertIn('FD-CHILD', sub_codes)

    def test_folder_detail_shows_associated_documents(self):
        """MB1: i documenti con versione corrente approvata appaiono nella cartella."""
        from django.contrib.auth.models import User as AuthUser
        from documents.models import Document, DocumentVersion
        doc_owner = AuthUser.objects.create_user('fd_doc_owner', password='pw')
        doc = Document.objects.create(
            code='FD-DOC-001',
            title='Documento in cartella',
            category=Document.Category.QUALITY,
            project_folder=self.root,
            owner=doc_owner,
            created_by=doc_owner,
        )
        # MB1: solo documenti con versione corrente approvata appaiono per i reader
        version = DocumentVersion.objects.create(
            document=doc,
            revision_label='00',
            revision_number=0,
            status=DocumentVersion.Status.APPROVED,
            is_current=True,
            created_by=doc_owner,
        )
        doc.current_version = version
        doc.save(update_fields=['current_version'])

        self.client.login(username='fd_user', password='pw')
        response = self.client.get(reverse('folder_detail', args=[self.root.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'FD-DOC-001')
        doc_codes = [d.code for d in response.context['documents']]
        self.assertIn('FD-DOC-001', doc_codes)

    def test_folder_detail_hides_draft_only_document_from_other_user(self):
        """MB1: documento con sola bozza non appare nella cartella per altri utenti."""
        from django.contrib.auth.models import User as AuthUser
        from documents.models import Document, DocumentVersion
        doc_owner = AuthUser.objects.create_user('fd_draft_owner', password='pw')
        doc = Document.objects.create(
            code='FD-DRAFT-DOC',
            title='Bozza privata',
            category=Document.Category.QUALITY,
            project_folder=self.root,
            owner=doc_owner,
            created_by=doc_owner,
        )
        # Solo una bozza — nessuna versione approvata corrente
        DocumentVersion.objects.create(
            document=doc,
            revision_label='00',
            revision_number=0,
            status=DocumentVersion.Status.DRAFT,
            is_current=False,
            created_by=doc_owner,
        )
        # fd_user non è l'autore della bozza — non dovrebbe vederla
        self.client.login(username='fd_user', password='pw')
        response = self.client.get(reverse('folder_detail', args=[self.root.pk]))
        doc_codes = [d.code for d in response.context['documents']]
        self.assertNotIn('FD-DRAFT-DOC', doc_codes)

    def test_user_without_membership_gets_403(self):
        outsider = User.objects.create_user('fd_outsider', password='pw')
        self.client.login(username='fd_outsider', password='pw')
        response = self.client.get(reverse('folder_detail', args=[self.root.pk]))
        self.assertEqual(response.status_code, 403)


class FolderCreateViewTests(TestCase):

    def setUp(self):
        from django.contrib.auth.models import Group
        self.normal_user = User.objects.create_user('fc_normal', password='pw')
        self.manager = User.objects.create_user('fc_manager', password='pw')
        self.staff = User.objects.create_user('fc_staff', password='pw', is_staff=True)
        g_managers = Group.objects.get_or_create(name='Document Managers')[0]
        self.manager.groups.add(g_managers)

    def test_normal_user_cannot_create_folder(self):
        self.client.login(username='fc_normal', password='pw')
        response = self.client.get(reverse('folder_create'))
        self.assertEqual(response.status_code, 403)

    def test_normal_user_post_gets_403(self):
        self.client.login(username='fc_normal', password='pw')
        response = self.client.post(reverse('folder_create'), {
            'code': 'FC-FAIL',
            'name': 'Non autorizzato',
            'folder_kind': 'generic',
            'status': 'active',
        })
        self.assertEqual(response.status_code, 403)

    def test_document_manager_can_create_folder(self):
        self.client.login(username='fc_manager', password='pw')
        response = self.client.post(reverse('folder_create'), {
            'code': 'FC-OK',
            'name': 'Cartella manager',
            'folder_kind': 'generic',
            'status': 'active',
        })
        self.assertTrue(ProjectFolder.objects.filter(code='FC-OK').exists())
        folder = ProjectFolder.objects.get(code='FC-OK')
        self.assertRedirects(response, reverse('folder_detail', args=[folder.pk]))

    def test_staff_without_group_cannot_create_folder(self):
        """MB1: is_staff senza Document Manager group non può creare cartelle."""
        self.client.login(username='fc_staff', password='pw')
        response = self.client.post(reverse('folder_create'), {
            'code': 'FC-STAFF',
            'name': 'Cartella staff',
            'folder_kind': 'department',
            'status': 'active',
        })
        self.assertEqual(response.status_code, 403)
        self.assertFalse(ProjectFolder.objects.filter(code='FC-STAFF').exists())


@override_settings(EMAIL_BACKEND=EMAIL_LOCMEM)
class MembershipPermissionTests(TestCase):
    """Test permessi per-cartella tramite ProjectFolderMembership."""

    def setUp(self):
        self.owner = User.objects.create_user('mp_owner', password='pw')
        self.reader = User.objects.create_user('mp_reader', password='pw')
        self.author = User.objects.create_user('mp_author', password='pw')
        self.approver_user = User.objects.create_user('mp_approver', password='pw')
        self.manager_user = User.objects.create_user('mp_manager', password='pw')
        self.outsider = User.objects.create_user('mp_outsider', password='pw')

        self.folder = make_folder(code='MP-FOLD', owner=self.owner)
        self.other_folder = make_folder(code='MP-OTHER', owner=self.owner)

        ProjectFolderMembership.objects.create(folder=self.folder, user=self.reader, role='reader')
        ProjectFolderMembership.objects.create(folder=self.folder, user=self.author, role='author')
        ProjectFolderMembership.objects.create(folder=self.folder, user=self.approver_user, role='approver')
        ProjectFolderMembership.objects.create(folder=self.folder, user=self.manager_user, role='manager')

    # -- unique_together --

    def test_duplicate_membership_raises(self):
        from django.db import IntegrityError
        with self.assertRaises(IntegrityError):
            ProjectFolderMembership.objects.create(
                folder=self.folder, user=self.reader, role='author'
            )

    # -- can_view_folder --

    def test_reader_can_view_folder(self):
        from projects.permissions import can_view_folder
        self.assertTrue(can_view_folder(self.reader, self.folder))

    def test_outsider_cannot_view_folder(self):
        from projects.permissions import can_view_folder
        self.assertFalse(can_view_folder(self.outsider, self.folder))

    def test_approver_can_view_folder(self):
        from projects.permissions import can_view_folder
        self.assertTrue(can_view_folder(self.approver_user, self.folder))

    # -- can_manage_folder --

    def test_folder_manager_can_manage(self):
        from projects.permissions import can_manage_folder
        self.assertTrue(can_manage_folder(self.manager_user, self.folder))

    def test_reader_cannot_manage_folder(self):
        from projects.permissions import can_manage_folder
        self.assertFalse(can_manage_folder(self.reader, self.folder))

    def test_outsider_cannot_manage_folder(self):
        from projects.permissions import can_manage_folder
        self.assertFalse(can_manage_folder(self.outsider, self.folder))

    # -- can_create_revision --

    def test_author_can_create_revision_in_own_folder(self):
        from documents.models import Document
        from documents.permissions import can_create_revision
        doc = Document.objects.create(
            code='MP-CR-001', title='T', category=Document.Category.QUALITY,
            project_folder=self.folder, owner=self.owner, created_by=self.owner,
        )
        self.assertTrue(can_create_revision(self.author, doc))

    def test_reader_cannot_create_revision(self):
        from documents.models import Document
        from documents.permissions import can_create_revision
        doc = Document.objects.create(
            code='MP-CR-002', title='T', category=Document.Category.QUALITY,
            project_folder=self.folder, owner=self.owner, created_by=self.owner,
        )
        self.assertFalse(can_create_revision(self.reader, doc))

    def test_author_cannot_create_revision_in_other_folder(self):
        from documents.models import Document
        from documents.permissions import can_create_revision
        doc = Document.objects.create(
            code='MP-CR-003', title='T', category=Document.Category.QUALITY,
            project_folder=self.other_folder, owner=self.owner, created_by=self.owner,
        )
        self.assertFalse(can_create_revision(self.author, doc))

    def test_folder_manager_can_create_revision(self):
        from documents.models import Document
        from documents.permissions import can_create_revision
        doc = Document.objects.create(
            code='MP-CR-004', title='T', category=Document.Category.QUALITY,
            project_folder=self.folder, owner=self.owner, created_by=self.owner,
        )
        self.assertTrue(can_create_revision(self.manager_user, doc))

    # -- can_view_document --

    def _make_approved_doc(self, code):
        from documents.models import Document, DocumentVersion
        doc = Document.objects.create(
            code=code, title='Approved', category=Document.Category.QUALITY,
            project_folder=self.folder, owner=self.owner, created_by=self.owner,
            status=Document.Status.ACTIVE,
        )
        version = DocumentVersion.objects.create(
            document=doc, revision_label='00', revision_number=0,
            status=DocumentVersion.Status.APPROVED, is_current=True,
            created_by=self.owner,
        )
        doc.current_version = version
        doc.save(update_fields=['current_version'])
        return doc

    def test_reader_can_view_approved_doc_in_own_folder(self):
        from documents.permissions import can_view_document
        doc = self._make_approved_doc('MP-VD-001')
        self.assertTrue(can_view_document(self.reader, doc))

    def test_outsider_cannot_view_approved_doc_in_folder(self):
        from documents.permissions import can_view_document
        doc = self._make_approved_doc('MP-VD-002')
        self.assertFalse(can_view_document(self.outsider, doc))

    def test_approver_can_view_approved_doc_in_own_folder(self):
        from documents.permissions import can_view_document
        doc = self._make_approved_doc('MP-VD-003')
        self.assertTrue(can_view_document(self.approver_user, doc))

    # -- document_list view --

    def test_document_list_shows_folder_doc_to_member(self):
        self._make_approved_doc('MP-LS-001')
        self.client.login(username='mp_reader', password='pw')
        response = self.client.get(reverse('document_list'))
        self.assertEqual(response.status_code, 200)
        codes = [d.code for d in response.context['documents']]
        self.assertIn('MP-LS-001', codes)

    def test_document_list_hides_folder_doc_from_outsider(self):
        self._make_approved_doc('MP-LS-002')
        self.client.login(username='mp_outsider', password='pw')
        response = self.client.get(reverse('document_list'))
        self.assertEqual(response.status_code, 200)
        codes = [d.code for d in response.context['documents']]
        self.assertNotIn('MP-LS-002', codes)

    # -- can_download_version_file (permesso, senza file fisico) --

    def test_reader_can_download_approved_version_in_folder(self):
        from documents.permissions import can_download_version_file
        doc = self._make_approved_doc('MP-DL-001')
        version = doc.current_version
        version.file_id = 1  # id fittizio: testa solo il permesso, non l'esistenza del file
        self.assertTrue(can_download_version_file(self.reader, version))

    def test_outsider_cannot_download_approved_version_in_folder(self):
        from documents.permissions import can_download_version_file
        doc = self._make_approved_doc('MP-DL-002')
        version = doc.current_version
        version.file_id = 1
        self.assertFalse(can_download_version_file(self.outsider, version))

    # -- folder_detail view access --

    def test_reader_can_access_folder_detail_view(self):
        self.client.login(username='mp_reader', password='pw')
        response = self.client.get(reverse('folder_detail', args=[self.folder.pk]))
        self.assertEqual(response.status_code, 200)

    def test_outsider_gets_403_on_folder_detail_view(self):
        self.client.login(username='mp_outsider', password='pw')
        response = self.client.get(reverse('folder_detail', args=[self.folder.pk]))
        self.assertEqual(response.status_code, 403)


# ---------------------------------------------------------------------------
# Project tests
# ---------------------------------------------------------------------------

from projects.models import Project  # noqa: E402


def make_project(code='PRJ-001', name='Test Project', owner=None, folder=None,
                 status=Project.Status.ACTIVE):
    return Project.objects.create(
        code=code,
        name=name,
        status=status,
        project_type=Project.ProjectType.INTERNAL,
        folder=folder,
        manager=owner,
        created_by=owner,
    )


class ProjectModelTests(TestCase):

    def setUp(self):
        self.user = User.objects.create_user('prj_user', password='pw')

    def test_project_creation(self):
        p = make_project(code='PRJ-001', owner=self.user)
        self.assertEqual(p.code, 'PRJ-001')
        self.assertEqual(p.status, Project.Status.ACTIVE)
        self.assertEqual(p.project_type, Project.ProjectType.INTERNAL)

    def test_project_code_unique(self):
        make_project(code='PRJ-DUP', owner=self.user)
        from django.db import IntegrityError
        with self.assertRaises(IntegrityError):
            Project.objects.create(
                code='PRJ-DUP',
                name='Duplicate',
                manager=self.user,
                created_by=self.user,
            )

    def test_project_str(self):
        p = make_project(code='PRJ-STR', name='Stringa Test', owner=self.user)
        self.assertIn('PRJ-STR', str(p))
        self.assertIn('Stringa Test', str(p))

    def test_project_without_folder(self):
        p = make_project(code='PRJ-NF', owner=self.user, folder=None)
        self.assertIsNone(p.folder)


class ProjectListViewTests(TestCase):

    def setUp(self):
        from django.contrib.auth.models import Group
        self.manager = User.objects.create_user('pl_manager', password='pw', is_staff=True)
        self.normal = User.objects.create_user('pl_normal', password='pw')
        self.owner = User.objects.create_user('pl_owner', password='pw')
        # MB1: is_staff da solo non concede visibilità globale progetti
        Group.objects.get_or_create(name='Document Managers')[0].user_set.add(self.manager)
        self.folder = make_folder(code='PL-F-001', owner=self.owner)
        self.project = make_project(code='PL-PRJ-001', owner=self.owner, folder=self.folder)

    def test_project_list_requires_login(self):
        response = self.client.get(reverse('project_list'))
        self.assertRedirects(response, '/accounts/login/?next=/projects/')

    def test_manager_sees_all_projects(self):
        self.client.login(username='pl_manager', password='pw')
        response = self.client.get(reverse('project_list'))
        self.assertEqual(response.status_code, 200)
        codes = [p.code for p in response.context['projects']]
        self.assertIn('PL-PRJ-001', codes)

    def test_normal_user_without_folder_access_sees_no_project(self):
        self.client.login(username='pl_normal', password='pw')
        response = self.client.get(reverse('project_list'))
        self.assertEqual(response.status_code, 200)
        codes = [p.code for p in response.context['projects']]
        self.assertNotIn('PL-PRJ-001', codes)

    def test_normal_user_with_folder_membership_sees_project(self):
        ProjectFolderMembership.objects.create(
            folder=self.folder, user=self.normal,
            role=ProjectFolderMembership.Role.READER,
        )
        self.client.login(username='pl_normal', password='pw')
        response = self.client.get(reverse('project_list'))
        codes = [p.code for p in response.context['projects']]
        self.assertIn('PL-PRJ-001', codes)


class ProjectCreateViewTests(TestCase):

    def setUp(self):
        from django.contrib.auth.models import Group
        from documents.permissions import GROUP_MANAGERS
        self.manager = User.objects.create_user('pc_manager', password='pw')
        self.normal = User.objects.create_user('pc_normal', password='pw')
        Group.objects.get_or_create(name=GROUP_MANAGERS)[0].user_set.add(self.manager)

    def test_document_manager_can_create_project(self):
        self.client.login(username='pc_manager', password='pw')
        response = self.client.post(reverse('project_create'), {
            'code': 'PRJ-NEW-001',
            'name': 'Nuovo Progetto',
            'status': 'active',
            'project_type': 'internal',
        })
        self.assertEqual(response.status_code, 302)
        self.assertTrue(Project.objects.filter(code='PRJ-NEW-001').exists())

    def test_normal_user_cannot_create_project(self):
        self.client.login(username='pc_normal', password='pw')
        response = self.client.post(reverse('project_create'), {
            'code': 'PRJ-DENIED',
            'name': 'Non autorizzato',
            'status': 'active',
            'project_type': 'internal',
        })
        self.assertEqual(response.status_code, 403)
        self.assertFalse(Project.objects.filter(code='PRJ-DENIED').exists())


class ProjectDetailViewTests(TestCase):

    def setUp(self):
        from django.contrib.auth.models import Group
        self.owner = User.objects.create_user('pd_owner', password='pw', is_staff=True)
        self.reader = User.objects.create_user('pd_reader', password='pw')
        self.outsider = User.objects.create_user('pd_outsider', password='pw')
        # MB1: is_staff non concede accesso automatico ai progetti
        Group.objects.get_or_create(name='Document Managers')[0].user_set.add(self.owner)
        self.folder = make_folder(code='PD-F-001', owner=self.owner)
        ProjectFolderMembership.objects.create(
            folder=self.folder, user=self.reader,
            role=ProjectFolderMembership.Role.READER,
        )
        self.project = make_project(
            code='PD-PRJ-001', name='Progetto Dettaglio',
            owner=self.owner, folder=self.folder,
        )

    def test_project_detail_shows_project_data(self):
        self.client.login(username='pd_owner', password='pw')
        response = self.client.get(reverse('project_detail', args=[self.project.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'PD-PRJ-001')
        self.assertContains(response, 'Progetto Dettaglio')

    def test_project_detail_shows_documents_in_folder(self):
        from documents.models import Document
        Document.objects.create(
            code='PD-DOC-001', title='Doc nel progetto',
            category=Document.Category.QUALITY,
            project_folder=self.folder,
            owner=self.owner, created_by=self.owner,
        )
        self.client.login(username='pd_owner', password='pw')
        response = self.client.get(reverse('project_detail', args=[self.project.pk]))
        self.assertEqual(response.status_code, 200)
        doc_codes = [d.code for d in response.context['documents']]
        self.assertIn('PD-DOC-001', doc_codes)

    def test_user_without_folder_access_gets_403(self):
        self.client.login(username='pd_outsider', password='pw')
        response = self.client.get(reverse('project_detail', args=[self.project.pk]))
        self.assertEqual(response.status_code, 403)

    def test_user_with_folder_access_can_see_project(self):
        self.client.login(username='pd_reader', password='pw')
        response = self.client.get(reverse('project_detail', args=[self.project.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'PD-PRJ-001')


class DemoWorkflowProjectTests(TestCase):

    def test_demo_workflow_creates_project(self):
        from django.core.management import call_command
        from io import StringIO
        out = StringIO()
        call_command('demo_workflow', '--no-email', stdout=out)
        self.assertTrue(Project.objects.filter(code='PRJ-DEMO-001').exists())
        p = Project.objects.get(code='PRJ-DEMO-001')
        self.assertEqual(p.status, Project.Status.ACTIVE)
        self.assertEqual(p.project_type, Project.ProjectType.INTERNAL)
        self.assertIsNotNone(p.folder)


# ---------------------------------------------------------------------------
# Step 13B — ProjectRevision (baseline) tests
# ---------------------------------------------------------------------------

def make_project_with_folder(code='BP-PRJ-001', owner=None):
    folder = ProjectFolder.objects.create(
        code=f'{code}-FOLD', name='Folder', folder_kind=ProjectFolder.FolderKind.GENERIC,
        status=ProjectFolder.Status.ACTIVE, owner=owner,
    )
    project = Project.objects.create(
        code=code, name='Baseline Project', status=Project.Status.ACTIVE,
        project_type=Project.ProjectType.INTERNAL, folder=folder,
        manager=owner, created_by=owner,
    )
    return project, folder


class ProjectRevisionServiceTests(TestCase):
    """create_project_revision, populate_project_revision_from_current_documents, issue_project_revision."""

    def setUp(self):
        self.manager = User.objects.create_user('br_mgr', password='pw', is_staff=True)
        self.project, self.folder = make_project_with_folder(owner=self.manager)

    def _make_approved_doc(self, code):
        from documents.models import Document, DocumentVersion
        doc = Document.objects.create(
            code=code, title=f'Doc {code}',
            category=Document.Category.QUALITY,
            project_folder=self.folder,
            owner=self.manager, created_by=self.manager,
        )
        version = DocumentVersion.objects.create(
            document=doc, revision_label='00', revision_number=0,
            status=DocumentVersion.Status.APPROVED,
            change_summary='first', created_by=self.manager,
            is_current=True,
        )
        doc.current_version = version
        doc.save(update_fields=['current_version'])
        return doc, version

    def test_create_project_revision_returns_draft(self):
        from projects.services import create_project_revision
        rev = create_project_revision(self.project, self.manager, 'A', 0, 'Baseline A')
        self.assertEqual(rev.status, ProjectRevision.Status.DRAFT)
        self.assertFalse(rev.is_current)
        self.assertEqual(rev.project, self.project)
        self.assertEqual(rev.revision_label, 'A')

    def test_populate_adds_current_documents(self):
        from projects.services import create_project_revision, populate_project_revision_from_current_documents
        self._make_approved_doc('BP-DOC-001')
        self._make_approved_doc('BP-DOC-002')
        rev = create_project_revision(self.project, self.manager, 'A', 0)
        added = populate_project_revision_from_current_documents(rev)
        self.assertEqual(added, 2)
        self.assertEqual(rev.items.count(), 2)

    def test_populate_skips_docs_without_current_version(self):
        from documents.models import Document
        from projects.services import create_project_revision, populate_project_revision_from_current_documents
        Document.objects.create(
            code='BP-NODOC', title='No version',
            category=Document.Category.QUALITY,
            project_folder=self.folder,
            owner=self.manager, created_by=self.manager,
        )
        rev = create_project_revision(self.project, self.manager, 'A', 0)
        added = populate_project_revision_from_current_documents(rev)
        self.assertEqual(added, 0)

    def test_issue_marks_revision_as_current(self):
        from projects.services import create_project_revision, issue_project_revision
        rev = create_project_revision(self.project, self.manager, 'A', 0)
        issue_project_revision(rev, self.manager)
        rev.refresh_from_db()
        self.assertEqual(rev.status, ProjectRevision.Status.ISSUED)
        self.assertTrue(rev.is_current)
        self.assertIsNotNone(rev.issued_at)
        self.assertEqual(rev.issued_by, self.manager)

    def test_issue_supersedes_previous_current(self):
        from projects.services import create_project_revision, issue_project_revision
        rev_a = create_project_revision(self.project, self.manager, 'A', 0)
        issue_project_revision(rev_a, self.manager)
        rev_a.refresh_from_db()
        self.assertTrue(rev_a.is_current)

        rev_b = create_project_revision(self.project, self.manager, 'B', 1)
        issue_project_revision(rev_b, self.manager)
        rev_a.refresh_from_db()
        rev_b.refresh_from_db()
        self.assertFalse(rev_a.is_current)
        self.assertEqual(rev_a.status, ProjectRevision.Status.SUPERSEDED)
        self.assertTrue(rev_b.is_current)

    def test_issue_non_draft_raises(self):
        from projects.services import create_project_revision, issue_project_revision
        rev = create_project_revision(self.project, self.manager, 'A', 0)
        issue_project_revision(rev, self.manager)
        rev.refresh_from_db()
        with self.assertRaises(ValueError):
            issue_project_revision(rev, self.manager)


class ProjectRevisionViewTests(TestCase):
    """Views: project_revision_create, project_revision_detail, project_revision_issue."""

    def setUp(self):
        from django.contrib.auth.models import Group
        self.manager = User.objects.create_user('rv_mgr', password='pw', is_staff=True)
        self.outsider = User.objects.create_user('rv_out', password='pw')
        Group.objects.get_or_create(name='Document Managers')[0].user_set.add(self.manager)
        self.project, self.folder = make_project_with_folder(code='RV-PRJ-001', owner=self.manager)

    def test_create_requires_login(self):
        url = reverse('project_revision_create', args=[self.project.pk])
        response = self.client.get(url)
        self.assertRedirects(response, f'/accounts/login/?next={url}')

    def test_create_get_renders_form(self):
        self.client.login(username='rv_mgr', password='pw')
        response = self.client.get(reverse('project_revision_create', args=[self.project.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertIn('form', response.context)

    def test_create_post_creates_revision_and_redirects(self):
        self.client.login(username='rv_mgr', password='pw')
        response = self.client.post(
            reverse('project_revision_create', args=[self.project.pk]),
            {'revision_label': 'A', 'revision_number': 0, 'title': 'First baseline', 'description': ''},
        )
        rev = ProjectRevision.objects.get(project=self.project, revision_label='A')
        self.assertRedirects(response, reverse('project_revision_detail', args=[rev.pk]))

    def test_non_manager_gets_403_on_create(self):
        self.client.login(username='rv_out', password='pw')
        response = self.client.post(
            reverse('project_revision_create', args=[self.project.pk]),
            {'revision_label': 'A', 'revision_number': 0, 'title': 'X', 'description': ''},
        )
        self.assertEqual(response.status_code, 403)

    def test_detail_shows_items(self):
        from projects.services import create_project_revision
        rev = create_project_revision(self.project, self.manager, 'A', 0, 'Baseline A')
        self.client.login(username='rv_mgr', password='pw')
        response = self.client.get(reverse('project_revision_detail', args=[rev.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Baseline A')

    def test_issue_post_emits_revision(self):
        from projects.services import create_project_revision
        rev = create_project_revision(self.project, self.manager, 'A', 0, 'Baseline A')
        self.client.login(username='rv_mgr', password='pw')
        self.client.post(reverse('project_revision_issue', args=[rev.pk]))
        rev.refresh_from_db()
        self.assertEqual(rev.status, ProjectRevision.Status.ISSUED)
        self.assertTrue(rev.is_current)

    def test_project_detail_shows_revisions(self):
        from projects.services import create_project_revision
        create_project_revision(self.project, self.manager, 'A', 0, 'Baseline A')
        self.client.login(username='rv_mgr', password='pw')
        response = self.client.get(reverse('project_detail', args=[self.project.pk]))
        self.assertEqual(response.status_code, 200)
        revisions = list(response.context['revisions'])
        self.assertEqual(len(revisions), 1)


class DemoWorkflowBaselineTests(TestCase):

    @override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
    def test_demo_creates_issued_baseline(self):
        from django.core.management import call_command
        from io import StringIO
        out = StringIO()
        call_command('demo_workflow', '--no-email', stdout=out)
        project = Project.objects.get(code='PRJ-DEMO-001')
        baselines = ProjectRevision.objects.filter(project=project)
        self.assertTrue(baselines.exists())
        current = baselines.filter(is_current=True).first()
        self.assertIsNotNone(current)
        self.assertEqual(current.status, ProjectRevision.Status.ISSUED)

    @override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
    def test_demo_baseline_has_items(self):
        """La baseline demo deve contenere almeno un documento (documento è nella stessa cartella del progetto)."""
        from django.core.management import call_command
        from io import StringIO
        call_command('demo_workflow', '--no-email', stdout=StringIO())
        project = Project.objects.get(code='PRJ-DEMO-001')
        current = ProjectRevision.objects.get(project=project, is_current=True)
        self.assertGreater(current.items.count(), 0)

    @override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
    def test_demo_idempotent_with_reset(self):
        """demo_workflow --reset --no-email può essere eseguito due volte di fila senza errori."""
        from django.core.management import call_command
        from io import StringIO
        call_command('demo_workflow', '--no-email', stdout=StringIO())
        # Seconda esecuzione con --reset: non deve sollevare IntegrityError
        call_command('demo_workflow', '--reset', '--no-email', stdout=StringIO())
        project = Project.objects.get(code='PRJ-DEMO-001')
        current = ProjectRevision.objects.filter(project=project, is_current=True).first()
        self.assertIsNotNone(current)
        self.assertEqual(current.status, ProjectRevision.Status.ISSUED)


# ---------------------------------------------------------------------------
# create_project_revision validation tests
# ---------------------------------------------------------------------------

class CreateProjectRevisionValidationTests(TestCase):
    """create_project_revision solleva ValidationError su duplicati, non IntegrityError."""

    def setUp(self):
        self.manager = User.objects.create_user('cpvt_mgr', password='pw', is_staff=True)
        self.project, self.folder = make_project_with_folder(code='CPVT-PRJ-001', owner=self.manager)

    def test_duplicate_revision_label_raises_validation_error(self):
        from django.core.exceptions import ValidationError as DjangoValidationError
        from projects.services import create_project_revision
        create_project_revision(self.project, self.manager, '00', 0)
        with self.assertRaises(DjangoValidationError):
            create_project_revision(self.project, self.manager, '00', 1)

    def test_duplicate_revision_number_raises_validation_error(self):
        from django.core.exceptions import ValidationError as DjangoValidationError
        from projects.services import create_project_revision
        create_project_revision(self.project, self.manager, '00', 0)
        with self.assertRaises(DjangoValidationError):
            create_project_revision(self.project, self.manager, '01', 0)

    def test_different_label_and_number_succeeds(self):
        from projects.services import create_project_revision
        create_project_revision(self.project, self.manager, '00', 0)
        rev = create_project_revision(self.project, self.manager, '01', 1)
        self.assertEqual(rev.revision_label, '01')


# ---------------------------------------------------------------------------
# Step 13B — Bug fix tests
# ---------------------------------------------------------------------------

class BaselineBugFixTests(TestCase):
    """
    Test per i bug corretti nello step 13B:
    - validazione form unicità revision_label/revision_number
    - pre-popolamento valori suggeriti
    - popolamento con documenti anche da sottocartelle
    - immutabilità snapshot baseline
    - baseline vuota ammessa con messaggio chiaro
    """

    def setUp(self):
        from django.contrib.auth.models import Group
        self.manager = User.objects.create_user('bbf_mgr', password='pw', is_staff=True)
        Group.objects.get_or_create(name='Document Managers')[0].user_set.add(self.manager)
        self.project, self.folder = make_project_with_folder(code='BBF-PRJ-001', owner=self.manager)

    def _make_approved_doc(self, code, folder=None):
        from documents.models import Document, DocumentVersion
        folder = folder or self.folder
        doc = Document.objects.create(
            code=code, title=f'Doc {code}',
            category=Document.Category.QUALITY,
            project_folder=folder,
            owner=self.manager, created_by=self.manager,
        )
        version = DocumentVersion.objects.create(
            document=doc, revision_label='00', revision_number=0,
            status=DocumentVersion.Status.APPROVED,
            change_summary='first', created_by=self.manager,
            is_current=True,
        )
        doc.current_version = version
        doc.save(update_fields=['current_version'])
        return doc, version

    # 1. Il GET propone automaticamente revision_number e revision_label corretti
    def test_get_form_preloads_next_revision_values_when_no_existing(self):
        self.client.login(username='bbf_mgr', password='pw')
        response = self.client.get(reverse('project_revision_create', args=[self.project.pk]))
        self.assertEqual(response.status_code, 200)
        form = response.context['form']
        self.assertEqual(form.initial.get('revision_number'), 0)
        self.assertEqual(form.initial.get('revision_label'), '00')

    def test_get_form_preloads_next_revision_values_after_existing(self):
        from projects.services import create_project_revision
        create_project_revision(self.project, self.manager, '00', 0)
        self.client.login(username='bbf_mgr', password='pw')
        response = self.client.get(reverse('project_revision_create', args=[self.project.pk]))
        form = response.context['form']
        self.assertEqual(form.initial.get('revision_number'), 1)
        self.assertEqual(form.initial.get('revision_label'), '01')

    # 2. Il form blocca revision_number duplicato senza IntegrityError
    def test_duplicate_revision_number_shows_form_error(self):
        from projects.services import create_project_revision
        create_project_revision(self.project, self.manager, '00', 0)
        self.client.login(username='bbf_mgr', password='pw')
        response = self.client.post(
            reverse('project_revision_create', args=[self.project.pk]),
            {'revision_label': '99', 'revision_number': 0, 'title': 'Dup num', 'description': ''},
        )
        # Deve restare sulla pagina (200) con errore nel form, non IntegrityError (500)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context['form'].is_valid())
        self.assertIn('revision_number', response.context['form'].errors)

    # 3. Il form blocca revision_label duplicata senza IntegrityError
    def test_duplicate_revision_label_shows_form_error(self):
        from projects.services import create_project_revision
        create_project_revision(self.project, self.manager, '00', 0)
        self.client.login(username='bbf_mgr', password='pw')
        response = self.client.post(
            reverse('project_revision_create', args=[self.project.pk]),
            {'revision_label': '00', 'revision_number': 99, 'title': 'Dup label', 'description': ''},
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context['form'].is_valid())
        self.assertIn('revision_label', response.context['form'].errors)

    # 4. Creare baseline da UI con documento approvato crea ProjectRevisionItem
    def test_create_via_ui_with_approved_doc_creates_item(self):
        self._make_approved_doc('BBF-DOC-001')
        self.client.login(username='bbf_mgr', password='pw')
        self.client.post(
            reverse('project_revision_create', args=[self.project.pk]),
            {'revision_label': '00', 'revision_number': 0, 'title': 'B00', 'description': ''},
        )
        rev = ProjectRevision.objects.get(project=self.project, revision_label='00')
        self.assertEqual(rev.items.count(), 1)
        self.assertEqual(rev.items.first().document_version.document.code, 'BBF-DOC-001')

    # 5. project_revision_detail mostra gli item salvati
    def test_detail_view_shows_saved_items(self):
        from projects.services import create_project_revision, populate_project_revision_from_current_documents
        self._make_approved_doc('BBF-DOC-002')
        rev = create_project_revision(self.project, self.manager, '00', 0)
        populate_project_revision_from_current_documents(rev)
        self.client.login(username='bbf_mgr', password='pw')
        response = self.client.get(reverse('project_revision_detail', args=[rev.pk]))
        self.assertEqual(response.status_code, 200)
        items = list(response.context['items'])
        self.assertEqual(len(items), 1)
        self.assertContains(response, 'BBF-DOC-002')

    # 6. Vecchia baseline continua a mostrare la vecchia DocumentVersion dopo aggiornamento documento
    def test_old_baseline_preserves_snapshot_after_document_update(self):
        from documents.models import DocumentVersion
        from projects.services import create_project_revision, populate_project_revision_from_current_documents
        doc, version_00 = self._make_approved_doc('BBF-DOC-003')
        rev = create_project_revision(self.project, self.manager, '00', 0)
        populate_project_revision_from_current_documents(rev)

        # Approva nuova revisione del documento (Rev.01 diventa current_version)
        # Prima revoca is_current dalla vecchia versione per rispettare il constraint DB
        from documents.models import DocumentVersion as DV
        DV.objects.filter(pk=version_00.pk).update(is_current=False)
        version_01 = DocumentVersion.objects.create(
            document=doc, revision_label='01', revision_number=1,
            status=DocumentVersion.Status.APPROVED,
            change_summary='update', created_by=self.manager,
            is_current=True,
        )
        doc.current_version = version_01
        doc.save(update_fields=['current_version'])

        # La vecchia baseline deve ancora puntare a Rev.00
        item = rev.items.select_related('document_version').first()
        self.assertEqual(item.document_version.pk, version_00.pk)
        self.assertEqual(item.document_version.revision_label, '00')

    # 7. Baseline con documenti in sottocartella include quei documenti
    def test_populate_includes_documents_in_subfolders(self):
        from projects.services import create_project_revision, populate_project_revision_from_current_documents
        subfolder = ProjectFolder.objects.create(
            code='BBF-SUB', name='Subfolder',
            folder_kind=ProjectFolder.FolderKind.GENERIC,
            parent=self.folder,
            status=ProjectFolder.Status.ACTIVE,
            owner=self.manager,
        )
        self._make_approved_doc('BBF-MAIN-DOC', folder=self.folder)
        self._make_approved_doc('BBF-SUB-DOC', folder=subfolder)

        rev = create_project_revision(self.project, self.manager, '00', 0)
        added = populate_project_revision_from_current_documents(rev)

        self.assertEqual(added, 2)
        codes = list(rev.items.values_list('document_version__document__code', flat=True))
        self.assertIn('BBF-MAIN-DOC', codes)
        self.assertIn('BBF-SUB-DOC', codes)

    # 8. Baseline vuota ammessa: issue funziona e detail mostra messaggio chiaro
    def test_empty_baseline_can_be_issued_and_shows_clear_message(self):
        from projects.services import create_project_revision, issue_project_revision
        rev = create_project_revision(self.project, self.manager, '00', 0, 'Vuota')
        issue_project_revision(rev, self.manager)
        rev.refresh_from_db()
        self.assertEqual(rev.status, ProjectRevision.Status.ISSUED)
        self.assertEqual(rev.items.count(), 0)

        self.client.login(username='bbf_mgr', password='pw')
        response = self.client.get(reverse('project_revision_detail', args=[rev.pk]))
        self.assertContains(response, 'non contiene documenti')


# ---------------------------------------------------------------------------
# Baseline comparison tests
# ---------------------------------------------------------------------------

class BaselineComparisonTests(TestCase):
    """build_project_baseline_comparison: stati aligned/changed/new/missing."""

    def setUp(self):
        from django.contrib.auth.models import Group
        self.manager = User.objects.create_user('bc_mgr', password='pw', is_staff=True)
        Group.objects.get_or_create(name='Document Managers')[0].user_set.add(self.manager)
        self.project, self.folder = make_project_with_folder(code='BC-PRJ-001', owner=self.manager)

    def _make_approved_version(self, code, label='00', number=0):
        from documents.models import Document, DocumentVersion
        doc, _ = Document.objects.get_or_create(
            code=code,
            defaults={
                'title': f'Doc {code}',
                'category': Document.Category.QUALITY,
                'project_folder': self.folder,
                'owner': self.manager,
                'created_by': self.manager,
            },
        )
        # revoca eventuale current esistente
        doc.versions.filter(is_current=True).update(is_current=False)
        from documents.models import DocumentVersion
        version = DocumentVersion.objects.create(
            document=doc, revision_label=label, revision_number=number,
            status=DocumentVersion.Status.APPROVED,
            change_summary='test', created_by=self.manager,
            is_current=True,
        )
        doc.current_version = version
        doc.save(update_fields=['current_version'])
        return doc, version

    def _make_baseline(self, label, number, populate=True):
        from projects.services import (
            create_project_revision,
            issue_project_revision,
            populate_project_revision_from_current_documents,
        )
        rev = create_project_revision(self.project, self.manager, label, number, f'Baseline {label}')
        if populate:
            populate_project_revision_from_current_documents(rev)
        issue_project_revision(rev, self.manager)
        return rev

    # 1. Documento corrente uguale a quello in baseline → Allineato
    def test_aligned_when_same_version(self):
        from projects.services import build_project_baseline_comparison
        _, version = self._make_approved_version('BC-DOC-001')
        self._make_baseline('00', 0)
        _, rows = build_project_baseline_comparison(self.project)
        row = next(r for r in rows if r['document'].code == 'BC-DOC-001')
        self.assertEqual(row['status'], 'aligned')
        self.assertEqual(row['current_version'].pk, row['baseline_version'].pk)

    # 2. Documento aggiornato dopo la baseline → Modificato dopo baseline
    def test_changed_when_newer_version(self):
        from documents.models import DocumentVersion
        from projects.services import build_project_baseline_comparison
        doc, version_00 = self._make_approved_version('BC-DOC-002', '00', 0)
        self._make_baseline('00', 0)

        # Nuova revisione approvata dopo la baseline
        version_00.__class__.objects.filter(pk=version_00.pk).update(is_current=False)
        version_01 = DocumentVersion.objects.create(
            document=doc, revision_label='01', revision_number=1,
            status=DocumentVersion.Status.APPROVED,
            change_summary='update', created_by=self.manager, is_current=True,
        )
        doc.current_version = version_01
        doc.save(update_fields=['current_version'])

        _, rows = build_project_baseline_comparison(self.project)
        row = next(r for r in rows if r['document'].code == 'BC-DOC-002')
        self.assertEqual(row['status'], 'changed')
        self.assertEqual(row['baseline_version'].pk, version_00.pk)
        self.assertEqual(row['current_version'].pk, version_01.pk)

    # 3. Documento corrente non presente nella baseline → Nuovo non in baseline
    def test_new_when_not_in_baseline(self):
        from projects.services import build_project_baseline_comparison
        # Crea baseline vuota (senza documenti)
        self._make_baseline('00', 0, populate=False)
        # Aggiunge documento dopo l'emissione della baseline
        self._make_approved_version('BC-DOC-003')
        _, rows = build_project_baseline_comparison(self.project)
        row = next(r for r in rows if r['document'].code == 'BC-DOC-003')
        self.assertEqual(row['status'], 'new')
        self.assertIsNone(row['baseline_version'])

    # 4. Item in baseline il cui documento non è più tra i correnti → missing
    def test_missing_when_doc_removed_from_current(self):
        from projects.services import build_project_baseline_comparison
        doc, _ = self._make_approved_version('BC-DOC-004')
        self._make_baseline('00', 0)
        # Rimuovi current_version dal documento (simula documento ritirato)
        doc.current_version = None
        doc.save(update_fields=['current_version'])
        _, rows = build_project_baseline_comparison(self.project)
        row = next(r for r in rows if r['document'].code == 'BC-DOC-004')
        self.assertEqual(row['status'], 'missing')
        self.assertIsNone(row['current_version'])
        self.assertIsNotNone(row['baseline_version'])

    # 5. Nessuna baseline corrente → funzione restituisce (None, [])
    def test_no_baseline_returns_empty(self):
        from projects.services import build_project_baseline_comparison
        baseline, rows = build_project_baseline_comparison(self.project)
        self.assertIsNone(baseline)
        self.assertEqual(rows, [])

    # 6. Project detail mostra la sezione confronto
    def test_project_detail_shows_comparison_section(self):
        self._make_approved_version('BC-DOC-005')
        self._make_baseline('00', 0)
        self.client.login(username='bc_mgr', password='pw')
        response = self.client.get(reverse('project_detail', args=[self.project.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Confronto con baseline corrente')
        self.assertIn('comparison_rows', response.context)
        rows = list(response.context['comparison_rows'])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['status'], 'aligned')


# ---------------------------------------------------------------------------
# New document from project context tests
# ---------------------------------------------------------------------------

class NewDocumentFromProjectTests(TestCase):
    """Bottone 'Nuovo documento' nel dettaglio progetto e flusso /documents/new/?project=<id>."""

    def setUp(self):
        from django.contrib.auth.models import Group
        # MB1: is_staff non concede più privilegi applicativi; aggiungiamo Document Managers
        self.manager = User.objects.create_user('ndp_mgr', password='pw', is_staff=True)
        self.author = User.objects.create_user('ndp_author', password='pw')
        self.outsider = User.objects.create_user('ndp_out', password='pw')

        g_authors = Group.objects.get_or_create(name='Document Authors')[0]
        g_managers = Group.objects.get_or_create(name='Document Managers')[0]
        self.author.groups.add(g_authors)
        self.manager.groups.add(g_managers)  # MB1: is_staff da solo non basta

        self.project, self.folder = make_project_with_folder(code='NDP-PRJ-001', owner=self.manager)
        # author ha ruolo author nella cartella del progetto
        ProjectFolderMembership.objects.create(folder=self.folder, user=self.author, role='author')

    # 1. Bottone visibile per manager (Document Manager group)
    def test_button_visible_for_manager(self):
        self.client.login(username='ndp_mgr', password='pw')
        response = self.client.get(reverse('project_detail', args=[self.project.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['can_create_doc'])
        self.assertContains(response, 'Nuovo documento in questo progetto')

    # 2. Bottone visibile per author con membership nella cartella
    def test_button_visible_for_folder_author(self):
        self.client.login(username='ndp_author', password='pw')
        response = self.client.get(reverse('project_detail', args=[self.project.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['can_create_doc'])
        self.assertContains(response, 'Nuovo documento in questo progetto')

    # 3. Bottone non visibile per utente senza write access alla cartella
    def test_button_not_visible_for_outsider(self):
        # outsider ha solo reader sulla cartella (per accedere alla pagina)
        ProjectFolderMembership.objects.create(folder=self.folder, user=self.outsider, role='reader')
        self.client.login(username='ndp_out', password='pw')
        response = self.client.get(reverse('project_detail', args=[self.project.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context['can_create_doc'])
        self.assertNotContains(response, 'Nuovo documento in questo progetto')

    # 4. GET /documents/new/?project=<id> preseleziona la cartella nel form
    def test_new_document_with_project_param_preselects_folder(self):
        self.client.login(username='ndp_mgr', password='pw')
        url = reverse('document_new') + f'?project={self.project.pk}'
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        form = response.context['form']
        self.assertEqual(form.fields['project_folder'].initial, self.folder)
        # queryset deve contenere solo la cartella del progetto
        qs = list(form.fields['project_folder'].queryset)
        self.assertEqual(len(qs), 1)
        self.assertEqual(qs[0].pk, self.folder.pk)

    # 5. Utente senza permesso sulla cartella ottiene 403
    def test_user_without_folder_write_gets_403(self):
        self.client.login(username='ndp_out', password='pw')
        url = reverse('document_new') + f'?project={self.project.pk}'
        response = self.client.get(url)
        self.assertEqual(response.status_code, 403)

    # 6. POST con ?project=<id> crea documento nella cartella del progetto
    def test_post_creates_document_in_project_folder(self):
        from documents.models import Document
        self.client.login(username='ndp_mgr', password='pw')
        url = reverse('document_new') + f'?project={self.project.pk}'
        response = self.client.post(url, {
            'code': 'NDP-DOC-001',
            'title': 'Documento da progetto',
            'category': 'QUALITY',
            'document_type': '',
            'description': '',
            'project_folder': self.folder.pk,
            'revision_label': '00',
            'revision_number': 0,
            'change_summary': '',
        })
        self.assertTrue(Document.objects.filter(code='NDP-DOC-001').exists())
        doc = Document.objects.get(code='NDP-DOC-001')
        self.assertEqual(doc.project_folder, self.folder)
        # redirect al documento creato
        self.assertRedirects(response, reverse('document_detail', args=[doc.pk]))

    # 7. Progetto senza cartella associata: bottone non visibile
    def test_button_not_shown_when_project_has_no_folder(self):
        project_no_folder = Project.objects.create(
            code='NDP-PRJ-NOFOLD',
            name='No folder project',
            status=Project.Status.ACTIVE,
            project_type=Project.ProjectType.INTERNAL,
            folder=None,
            manager=self.manager,
            created_by=self.manager,
        )
        self.client.login(username='ndp_mgr', password='pw')
        response = self.client.get(reverse('project_detail', args=[project_no_folder.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context['can_create_doc'])
        self.assertNotContains(response, 'Nuovo documento in questo progetto')


# ---------------------------------------------------------------------------
# Step Audit UI — project_detail
# ---------------------------------------------------------------------------

@override_settings(EMAIL_BACKEND=EMAIL_LOCMEM)
class AuditUIProjectDetailTests(TestCase):
    """Sezione 'Storico eventi progetto' nel dettaglio progetto."""

    def setUp(self):
        from django.contrib.auth.models import Group
        from documents.permissions import GROUP_AUDITORS

        self.manager_staff = User.objects.create_user('apd_mgr', password='pw', is_staff=True)
        self.global_auditor = User.objects.create_user('apd_auditor', password='pw')
        self.reader = User.objects.create_user('apd_reader', password='pw')

        Group.objects.get_or_create(name=GROUP_AUDITORS)[0].user_set.add(self.global_auditor)
        # MB1: is_staff da solo non concede accesso; Document Managers per l'utente manager
        Group.objects.get_or_create(name='Document Managers')[0].user_set.add(self.manager_staff)

        self.project, self.folder = make_project_with_folder(code='APD-PRJ-001', owner=self.manager_staff)
        ProjectFolderMembership.objects.create(folder=self.folder, user=self.reader, role='reader')

    # 1. Manager (staff) vede "Storico eventi progetto"
    def test_manager_sees_storico_eventi_progetto(self):
        self.client.login(username='apd_mgr', password='pw')
        response = self.client.get(reverse('project_detail', args=[self.project.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['show_audit'])
        self.assertContains(response, 'Storico eventi progetto')

    # 2. Auditor globale vede "Storico eventi progetto"
    def test_global_auditor_sees_storico_eventi_progetto(self):
        self.client.login(username='apd_auditor', password='pw')
        response = self.client.get(reverse('project_detail', args=[self.project.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['show_audit'])
        self.assertContains(response, 'Storico eventi progetto')

    # 3. Reader normale NON vede "Storico eventi progetto"
    def test_reader_does_not_see_storico_eventi_progetto(self):
        self.client.login(username='apd_reader', password='pw')
        response = self.client.get(reverse('project_detail', args=[self.project.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context['show_audit'])
        self.assertNotContains(response, 'Storico eventi progetto')
        self.assertIsNone(response.context['audit_logs'])

    # 4. Pagina funziona anche senza AuditLog
    def test_detail_works_without_audit_logs(self):
        from auditlog.models import AuditLog
        AuditLog.objects.all().delete()

        self.client.login(username='apd_mgr', password='pw')
        response = self.client.get(reverse('project_detail', args=[self.project.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(list(response.context['audit_logs'])), 0)
        self.assertContains(response, 'Nessun evento registrato per questo progetto.')

    # 5. Folder-auditor (membership cartella) vede "Storico eventi progetto"
    def test_folder_auditor_sees_storico_eventi_progetto(self):
        folder_auditor = User.objects.create_user('apd_foldaud', password='pw')
        ProjectFolderMembership.objects.create(
            folder=self.folder, user=folder_auditor, role='auditor'
        )
        self.client.login(username='apd_foldaud', password='pw')
        response = self.client.get(reverse('project_detail', args=[self.project.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['show_audit'])
        self.assertContains(response, 'Storico eventi progetto')


# ---------------------------------------------------------------------------
# Test: crea sottocartella con ?parent precompilato (Parte B)
# ---------------------------------------------------------------------------

class FolderCreateWithParentTests(TestCase):
    """
    Verifica che folder_create accetti ?parent=<id> e precompili la cartella padre.
    """

    def setUp(self):
        from django.contrib.auth.models import Group
        self.staff = User.objects.create_user('fc_staff', password='pw', is_staff=True)
        self.owner = User.objects.create_user('fc_owner', password='pw')
        # MB1: is_staff non concede creazione cartelle; Document Managers per i test di comportamento
        Group.objects.get_or_create(name='Document Managers')[0].user_set.add(self.staff)
        self.parent = make_folder(code='FC-ROOT', name='Cartella Radice', owner=self.owner)

    def test_folder_detail_link_includes_parent_param(self):
        """Il link '+ Sottocartella' in folder_detail punta a folder_create?parent=<pk>."""
        self.client.login(username='fc_staff', password='pw')
        response = self.client.get(reverse('folder_detail', args=[self.parent.pk]))
        self.assertContains(response, f'?parent={self.parent.pk}')

    def test_folder_create_get_with_parent_precompiles_form(self):
        """GET folder_create?parent=<pk> passa parent_folder al contesto."""
        self.client.login(username='fc_staff', password='pw')
        response = self.client.get(
            reverse('folder_create') + f'?parent={self.parent.pk}'
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['parent_folder'], self.parent)
        self.assertContains(response, 'FC-ROOT')

    def test_folder_create_get_with_parent_shows_banner(self):
        """Il form mostra il banner 'Nuova sottocartella dentro: <nome>'."""
        self.client.login(username='fc_staff', password='pw')
        response = self.client.get(
            reverse('folder_create') + f'?parent={self.parent.pk}'
        )
        self.assertContains(response, 'Cartella padre')
        self.assertContains(response, 'Cartella Radice')

    def test_folder_create_post_creates_subfolder_with_parent(self):
        """POST crea una sottocartella con parent correttamente impostato."""
        self.client.login(username='fc_staff', password='pw')
        response = self.client.post(
            reverse('folder_create') + f'?parent={self.parent.pk}',
            {
                'code': 'FC-SUB-01',
                'name': 'Sottocartella Test',
                'folder_kind': 'generic',
                'parent': self.parent.pk,
                'status': 'active',
                '_parent_prefill': self.parent.pk,
            },
        )
        self.assertEqual(response.status_code, 302)
        sub = ProjectFolder.objects.filter(code='FC-SUB-01').first()
        self.assertIsNotNone(sub)
        self.assertEqual(sub.parent_id, self.parent.pk)
        # Dopo la creazione, redirect al padre
        self.assertRedirects(response, reverse('folder_detail', args=[self.parent.pk]))

    def test_folder_detail_shows_subfolder_after_creation(self):
        """Dopo aver creato una sottocartella, appare nel folder_detail della padre."""
        sub = make_folder(code='FC-SHOW', name='Visibile', owner=self.owner, parent=self.parent)
        self.client.login(username='fc_staff', password='pw')
        response = self.client.get(reverse('folder_detail', args=[self.parent.pk]))
        self.assertContains(response, 'FC-SHOW')
        self.assertContains(response, 'Visibile')


# ---------------------------------------------------------------------------
# Test: crea progetto da cartella con ?folder precompilato (Parte C)
# ---------------------------------------------------------------------------

class ProjectCreateWithFolderTests(TestCase):
    """
    Verifica che project_create accetti ?folder=<id> e precompili la cartella documentale.
    """

    def setUp(self):
        from django.contrib.auth.models import Group
        self.staff = User.objects.create_user('pc_staff', password='pw', is_staff=True)
        self.owner = User.objects.create_user('pc_owner', password='pw')
        # MB1: is_staff non concede creazione progetti; Document Managers per i test di comportamento
        Group.objects.get_or_create(name='Document Managers')[0].user_set.add(self.staff)
        self.folder = make_folder(code='PC-FOLD', name='Cartella Progetto', owner=self.owner)

    def test_folder_detail_create_project_link_includes_folder_param(self):
        """Il link '+ Crea progetto' in folder_detail punta a project_create?folder=<pk>."""
        self.client.login(username='pc_staff', password='pw')
        response = self.client.get(reverse('folder_detail', args=[self.folder.pk]))
        # Il bottone appare solo se can_create_project e non ci sono già progetti
        self.assertContains(response, f'?folder={self.folder.pk}')

    def test_project_create_get_with_folder_precompiles_form(self):
        """GET project_create?folder=<pk> passa prefill_folder al contesto."""
        self.client.login(username='pc_staff', password='pw')
        response = self.client.get(
            reverse('project_create') + f'?folder={self.folder.pk}'
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['prefill_folder'], self.folder)
        self.assertContains(response, 'PC-FOLD')

    def test_project_create_get_with_folder_shows_banner(self):
        """Il form mostra il banner con la cartella pre-selezionata."""
        self.client.login(username='pc_staff', password='pw')
        response = self.client.get(
            reverse('project_create') + f'?folder={self.folder.pk}'
        )
        self.assertContains(response, 'Cartella documentale')
        self.assertContains(response, 'Cartella Progetto')


# ===========================================================================
# Step A — Materialized Path tests
# ===========================================================================

class FolderPathTests(TestCase):
    """Test per il materialized path di ProjectFolder."""

    def setUp(self):
        self.owner = User.objects.create_user('path_owner', password='pw')

    def _make(self, code, parent=None):
        return ProjectFolder.objects.create(
            code=code, name=code, folder_kind=ProjectFolder.FolderKind.GENERIC,
            status=ProjectFolder.Status.ACTIVE, owner=self.owner, parent=parent,
        )

    # 1. Root → /pk/
    def test_root_path(self):
        from projects.services import set_folder_path
        f = self._make('ROOT-A')
        set_folder_path(f)
        self.assertEqual(f.path, f"/{f.pk}/")

    # 2. Child → /parent_pk/pk/
    def test_child_path(self):
        from projects.services import set_folder_path
        root = self._make('ROOT-B')
        set_folder_path(root)
        child = self._make('CHILD-B', parent=root)
        set_folder_path(child)
        self.assertEqual(child.path, f"/{root.pk}/{child.pk}/")

    # 3. Profondità 3 livelli
    def test_depth_3(self):
        from projects.services import set_folder_path
        a = self._make('D3-A'); set_folder_path(a)
        b = self._make('D3-B', parent=a); set_folder_path(b)
        c = self._make('D3-C', parent=b); set_folder_path(c)
        self.assertEqual(c.path, f"/{a.pk}/{b.pk}/{c.pk}/")

    # 4. Move di nodo intermedio
    def test_move_intermediate(self):
        from projects.services import set_folder_path, move_folder
        r = self._make('MV-R'); set_folder_path(r)
        a = self._make('MV-A', parent=r); set_folder_path(a)
        b = self._make('MV-B'); set_folder_path(b)
        # Sposta a sotto b
        move_folder(a, b)
        a.refresh_from_db()
        self.assertEqual(a.path, f"/{b.pk}/{a.pk}/")

    # 5. Path discendenti aggiornati dopo move
    def test_descendants_updated_after_move(self):
        from projects.services import set_folder_path, move_folder
        r = self._make('DU-R'); set_folder_path(r)
        a = self._make('DU-A', parent=r); set_folder_path(a)
        child = self._make('DU-C', parent=a); set_folder_path(child)
        grand = self._make('DU-G', parent=child); set_folder_path(grand)
        new_root = self._make('DU-NR'); set_folder_path(new_root)
        # Sposta a sotto new_root
        move_folder(a, new_root)
        child.refresh_from_db(); grand.refresh_from_db()
        self.assertEqual(child.path, f"/{new_root.pk}/{a.pk}/{child.pk}/")
        self.assertEqual(grand.path, f"/{new_root.pk}/{a.pk}/{child.pk}/{grand.pk}/")

    # 6. Ciclo bloccato
    def test_cycle_blocked(self):
        from django.core.exceptions import ValidationError
        from projects.services import set_folder_path, move_folder
        r = self._make('CY-R'); set_folder_path(r)
        c = self._make('CY-C', parent=r); set_folder_path(c)
        with self.assertRaises(ValidationError):
            move_folder(r, c)

    # 7. parent=self bloccato
    def test_self_as_parent_blocked(self):
        from django.core.exceptions import ValidationError
        from projects.services import set_folder_path, move_folder
        f = self._make('SELF-A'); set_folder_path(f)
        with self.assertRaises(ValidationError):
            move_folder(f, f)

    # 8. Cartelle legacy valorizzate (data migration)
    def test_legacy_folders_valorized(self):
        """Tutte le cartelle create prima della migration hanno path valorizzato."""
        from projects.services import build_folder_path_for_existing
        # Crea cartelle senza path
        r = self._make('LEG-R')
        c = self._make('LEG-C', parent=r)
        # Azzera i path (simula stato pre-migration)
        ProjectFolder.objects.filter(pk__in=[r.pk, c.pk]).update(path='')
        r.refresh_from_db(); c.refresh_from_db()
        self.assertEqual(r.path, '')
        self.assertEqual(c.path, '')
        # Esegui valorizzazione
        build_folder_path_for_existing()
        r.refresh_from_db(); c.refresh_from_db()
        self.assertEqual(r.path, f"/{r.pk}/")
        self.assertEqual(c.path, f"/{r.pk}/{c.pk}/")

    # 9. Path è indicizzato (db_index)
    def test_path_field_has_db_index(self):
        field = ProjectFolder._meta.get_field('path')
        self.assertTrue(field.db_index)

    # 10. Move atomico — get_folder_descendants dopo move
    def test_move_atomic_descendants_consistent(self):
        from projects.services import set_folder_path, move_folder, get_folder_descendants
        r = self._make('AT-R'); set_folder_path(r)
        a = self._make('AT-A', parent=r); set_folder_path(a)
        b = self._make('AT-B', parent=a); set_folder_path(b)
        nr = self._make('AT-NR'); set_folder_path(nr)
        move_folder(a, nr)
        a.refresh_from_db()
        # Tutti i discendenti di a devono avere path corretto
        desc_pks = set(get_folder_descendants(a).values_list('pk', flat=True))
        self.assertIn(b.pk, desc_pks)
        b.refresh_from_db()
        self.assertTrue(b.path.startswith(a.path))

    # 11. get_folder_ancestors
    def test_get_ancestors(self):
        from projects.services import set_folder_path, get_folder_ancestors
        r = self._make('ANC-R'); set_folder_path(r)
        a = self._make('ANC-A', parent=r); set_folder_path(a)
        b = self._make('ANC-B', parent=a); set_folder_path(b)
        ancestors = list(get_folder_ancestors(b))
        ancestor_pks = [x.pk for x in ancestors]
        self.assertIn(r.pk, ancestor_pks)
        self.assertIn(a.pk, ancestor_pks)
        self.assertNotIn(b.pk, ancestor_pks)

    # 12. get_folder_descendants
    def test_get_descendants(self):
        from projects.services import set_folder_path, get_folder_descendants
        r = self._make('DESC-R'); set_folder_path(r)
        a = self._make('DESC-A', parent=r); set_folder_path(a)
        b = self._make('DESC-B', parent=a); set_folder_path(b)
        desc_pks = set(get_folder_descendants(r).values_list('pk', flat=True))
        self.assertIn(a.pk, desc_pks)
        self.assertIn(b.pk, desc_pks)
        self.assertNotIn(r.pk, desc_pks)


# ===========================================================================
# Step B — FolderPermissionGrant tests
# ===========================================================================

class FolderPermissionGrantModelTests(TestCase):
    """Test per il modello FolderPermissionGrant."""

    def setUp(self):
        from django.contrib.auth.models import Group as DjangoGroup
        self.owner = User.objects.create_user('fpg_owner', password='pw')
        self.user = User.objects.create_user('fpg_user', password='pw')
        self.group = DjangoGroup.objects.create(name='FPG Test Group')
        self.folder = ProjectFolder.objects.create(
            code='FPG-FOLD', name='FPG Folder',
            folder_kind=ProjectFolder.FolderKind.GENERIC,
            status=ProjectFolder.Status.ACTIVE,
            owner=self.owner,
        )
        self.PC = FolderPermissionGrant.PermissionCode

    def _make_user_grant(self, **kwargs):
        defaults = dict(
            folder=self.folder,
            user=self.user,
            permission_code=self.PC.READ_PUBLISHED,
            effect=FolderPermissionGrant.Effect.ALLOW,
        )
        defaults.update(kwargs)
        return FolderPermissionGrant.objects.create(**defaults)

    def _make_group_grant(self, **kwargs):
        defaults = dict(
            folder=self.folder,
            group=self.group,
            permission_code=self.PC.READ_PUBLISHED,
            effect=FolderPermissionGrant.Effect.ALLOW,
        )
        defaults.update(kwargs)
        return FolderPermissionGrant.objects.create(**defaults)

    # 1. Grant utente valido
    def test_user_grant_valid(self):
        g = self._make_user_grant()
        self.assertEqual(g.user, self.user)
        self.assertIsNone(g.group)

    # 2. Grant gruppo valido
    def test_group_grant_valid(self):
        g = self._make_group_grant()
        self.assertEqual(g.group, self.group)
        self.assertIsNone(g.user)

    # 3. user e group entrambi null → errore
    def test_both_null_raises(self):
        from django.db import IntegrityError
        with self.assertRaises(Exception):  # IntegrityError o ValidationError
            FolderPermissionGrant.objects.create(
                folder=self.folder,
                user=None, group=None,
                permission_code=self.PC.READ_PUBLISHED,
            )

    # 4. user e group entrambi valorizzati → errore
    def test_both_set_raises(self):
        from django.db import IntegrityError
        with self.assertRaises(Exception):
            FolderPermissionGrant.objects.create(
                folder=self.folder,
                user=self.user, group=self.group,
                permission_code=self.PC.READ_PUBLISHED,
            )

    # 5. Duplicato user grant → errore
    def test_duplicate_user_grant_raises(self):
        from django.db import IntegrityError
        self._make_user_grant()
        with self.assertRaises(IntegrityError):
            self._make_user_grant()

    # 6. Duplicato group grant → errore
    def test_duplicate_group_grant_raises(self):
        from django.db import IntegrityError
        self._make_group_grant()
        with self.assertRaises(IntegrityError):
            self._make_group_grant()

    # 7. Default effect = allow
    def test_default_effect_allow(self):
        g = FolderPermissionGrant.objects.create(
            folder=self.folder, user=self.user,
            permission_code=self.PC.CREATE_DRAFT,
        )
        self.assertEqual(g.effect, FolderPermissionGrant.Effect.ALLOW)

    # 8. Default inherit_to_children = True
    def test_default_inherit_true(self):
        g = self._make_user_grant()
        self.assertTrue(g.inherit_to_children)

    # 9. expires_at opzionale (None di default)
    def test_expires_at_optional(self):
        g = self._make_user_grant()
        self.assertIsNone(g.expires_at)

    # 10. expires_at valorizzabile
    def test_expires_at_can_be_set(self):
        from django.utils import timezone
        future = timezone.now() + timezone.timedelta(days=30)
        g = self._make_user_grant(expires_at=future)
        self.assertIsNotNone(g.expires_at)

    # 11. __str__
    def test_str_user_grant(self):
        g = self._make_user_grant()
        s = str(g)
        self.assertIn(self.folder.code, s)
        self.assertIn(self.PC.READ_PUBLISHED, s)

    def test_str_group_grant(self):
        g = self._make_group_grant()
        s = str(g)
        self.assertIn(self.folder.code, s)

    # 12. Registrazione admin
    def test_admin_registered(self):
        from django.contrib import admin as django_admin
        self.assertIn(FolderPermissionGrant, django_admin.site._registry)
