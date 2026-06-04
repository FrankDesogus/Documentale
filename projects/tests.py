from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

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


def make_project(code='PRJ-001', name='Test Project', owner=None, root_folder=None,
                 folder=None):
    """
    Helper per i test: crea un progetto senza root folder atomica.
    Il parametro 'folder' è mantenuto per backward compat dei test vecchi
    ma viene ignorato (usare root_folder).
    """
    return Project.objects.create(
        code=code,
        name=name,
        project_type=Project.ProjectType.INTERNAL,
        root_folder=root_folder,
        manager=owner,
        created_by=owner,
    )


class ProjectModelTests(TestCase):

    def setUp(self):
        self.user = User.objects.create_user('prj_user', password='pw')

    def test_project_creation(self):
        p = make_project(code='PRJ-001', owner=self.user)
        self.assertEqual(p.code, 'PRJ-001')
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

    def test_project_without_root_folder(self):
        p = make_project(code='PRJ-NF', owner=self.user, root_folder=None)
        self.assertIsNone(p.root_folder)


class ProjectListViewTests(TestCase):

    def setUp(self):
        from django.contrib.auth.models import Group
        self.manager = User.objects.create_user('pl_manager', password='pw', is_staff=True)
        self.normal = User.objects.create_user('pl_normal', password='pw')
        self.owner = User.objects.create_user('pl_owner', password='pw')
        # MB1: is_staff da solo non concede visibilità globale progetti
        Group.objects.get_or_create(name='Document Managers')[0].user_set.add(self.manager)
        self.folder = make_folder(code='PL-F-001', owner=self.owner)
        self.project = make_project(code='PL-PRJ-001', owner=self.owner, root_folder=self.folder)

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
        # Cartella padre per il progetto (STEP PROJECT-ROOT: obbligatoria)
        self.parent_folder = make_folder(code='PC-PARENT', owner=self.manager)

    def test_document_manager_can_create_project(self):
        """STEP PROJECT-ROOT: la creazione richiede parent_folder."""
        self.client.login(username='pc_manager', password='pw')
        response = self.client.post(reverse('project_create'), {
            'parent_folder': self.parent_folder.pk,
            'code': 'PRJ-NEW-001',
            'name': 'Nuovo Progetto',
            'project_type': 'internal',
        })
        self.assertEqual(response.status_code, 302)
        self.assertTrue(Project.objects.filter(code='PRJ-NEW-001').exists())
        # Verifica root folder creata
        prj = Project.objects.get(code='PRJ-NEW-001')
        self.assertIsNotNone(prj.root_folder)
        self.assertEqual(prj.root_folder.folder_kind, ProjectFolder.FolderKind.PROJECT)
        self.assertEqual(prj.root_folder.parent, self.parent_folder)

    def test_normal_user_cannot_create_project(self):
        self.client.login(username='pc_normal', password='pw')
        response = self.client.post(reverse('project_create'), {
            'parent_folder': self.parent_folder.pk,
            'code': 'PRJ-DENIED',
            'name': 'Non autorizzato',
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
            owner=self.owner, root_folder=self.folder,
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
        self.assertEqual(p.project_type, Project.ProjectType.INTERNAL)
        # STEP PROJECT-ROOT: ora usa root_folder
        self.assertIsNotNone(p.root_folder)


# ---------------------------------------------------------------------------
# Step 13B — ProjectRevision (baseline) tests
# ---------------------------------------------------------------------------

def make_project_with_folder(code='BP-PRJ-001', owner=None):
    """
    Helper per test baseline: crea un progetto con root folder.
    STEP PROJECT-ROOT: usa root_folder invece di folder.
    """
    root_folder = ProjectFolder.objects.create(
        code=f'{code}-ROOT', name='Root Folder',
        folder_kind=ProjectFolder.FolderKind.PROJECT,
        status=ProjectFolder.Status.ACTIVE, owner=owner,
    )
    project = Project.objects.create(
        code=code, name='Baseline Project',
        project_type=Project.ProjectType.INTERNAL, root_folder=root_folder,
        manager=owner, created_by=owner,
    )
    return project, root_folder


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
        self.assertContains(response, '+ Nuovo documento')

    # 2. Bottone visibile per author con membership nella cartella
    def test_button_visible_for_folder_author(self):
        self.client.login(username='ndp_author', password='pw')
        response = self.client.get(reverse('project_detail', args=[self.project.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['can_create_doc'])
        self.assertContains(response, '+ Nuovo documento')

    # 3. Bottone non visibile per utente senza write access alla cartella
    def test_button_not_visible_for_outsider(self):
        # outsider ha solo reader sulla cartella (per accedere alla pagina)
        ProjectFolderMembership.objects.create(folder=self.folder, user=self.outsider, role='reader')
        self.client.login(username='ndp_out', password='pw')
        response = self.client.get(reverse('project_detail', args=[self.project.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context['can_create_doc'])
        self.assertNotContains(response, '+ Nuovo documento')

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

    # 7. Progetto senza root_folder: bottone non visibile
    def test_button_not_shown_when_project_has_no_folder(self):
        project_no_folder = Project.objects.create(
            code='NDP-PRJ-NOFOLD',
            name='No folder project',
            project_type=Project.ProjectType.INTERNAL,
            root_folder=None,
            manager=self.manager,
            created_by=self.manager,
        )
        self.client.login(username='ndp_mgr', password='pw')
        response = self.client.get(reverse('project_detail', args=[project_no_folder.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context['can_create_doc'])
        self.assertNotContains(response, '+ Nuovo documento')


# ---------------------------------------------------------------------------
# Step Audit UI — project_detail
# ---------------------------------------------------------------------------

@override_settings(EMAIL_BACKEND=EMAIL_LOCMEM)
class AuditUIProjectDetailTests(TestCase):
    """Sezione 'Storico eventi' nel dettaglio progetto."""

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

    # 1. Manager (staff) vede "Storico eventi"
    def test_manager_sees_storico_eventi_progetto(self):
        self.client.login(username='apd_mgr', password='pw')
        response = self.client.get(reverse('project_detail', args=[self.project.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['show_audit'])
        self.assertContains(response, 'Storico eventi')

    # 2. Auditor globale vede "Storico eventi"
    def test_global_auditor_sees_storico_eventi_progetto(self):
        self.client.login(username='apd_auditor', password='pw')
        response = self.client.get(reverse('project_detail', args=[self.project.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['show_audit'])
        self.assertContains(response, 'Storico eventi')

    # 3. Reader normale NON vede "Storico eventi"
    def test_reader_does_not_see_storico_eventi_progetto(self):
        self.client.login(username='apd_reader', password='pw')
        response = self.client.get(reverse('project_detail', args=[self.project.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context['show_audit'])
        self.assertNotContains(response, 'Storico eventi')
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

    # 5. Folder-auditor (membership cartella) vede "Storico eventi"
    def test_folder_auditor_sees_storico_eventi_progetto(self):
        folder_auditor = User.objects.create_user('apd_foldaud', password='pw')
        ProjectFolderMembership.objects.create(
            folder=self.folder, user=folder_auditor, role='auditor'
        )
        self.client.login(username='apd_foldaud', password='pw')
        response = self.client.get(reverse('project_detail', args=[self.project.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['show_audit'])
        self.assertContains(response, 'Storico eventi')


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
    STEP PROJECT-ROOT: project_create usa ?parent_folder=<id>.
    Verifica backward compat (anche ?folder= funziona) e precompilazione form.
    """

    def setUp(self):
        from django.contrib.auth.models import Group
        self.staff = User.objects.create_user('pc_staff', password='pw', is_staff=True)
        self.owner = User.objects.create_user('pc_owner', password='pw')
        Group.objects.get_or_create(name='Document Managers')[0].user_set.add(self.staff)
        self.folder = make_folder(code='PC-FOLD', name='Cartella Progetto', owner=self.owner)

    def test_folder_detail_create_project_link_includes_parent_folder_param(self):
        """STEP PROJECT-ROOT: il link usa ?parent_folder=<pk>."""
        self.client.login(username='pc_staff', password='pw')
        response = self.client.get(reverse('folder_detail', args=[self.folder.pk]))
        self.assertContains(response, f'?parent_folder={self.folder.pk}')

    def test_project_create_get_with_parent_folder_precompiles_form(self):
        """GET project_create?parent_folder=<pk> passa prefill_folder al contesto."""
        self.client.login(username='pc_staff', password='pw')
        response = self.client.get(
            reverse('project_create') + f'?parent_folder={self.folder.pk}'
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['prefill_folder'], self.folder)
        self.assertContains(response, 'PC-FOLD')

    def test_project_create_get_with_folder_param_still_works(self):
        """Backward compat: ?folder=<pk> precompila anche con il vecchio parametro."""
        self.client.login(username='pc_staff', password='pw')
        response = self.client.get(
            reverse('project_create') + f'?folder={self.folder.pk}'
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['prefill_folder'], self.folder)

    def test_project_create_get_shows_form_with_folder_info(self):
        """Il form mostra la cartella padre preselezionata."""
        self.client.login(username='pc_staff', password='pw')
        response = self.client.get(
            reverse('project_create') + f'?parent_folder={self.folder.pk}'
        )
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


# ===========================================================================
# Step C — PermissionResolver tests
# ===========================================================================

class FolderPermissionResolverTests(TestCase):
    """
    Test del shadow PermissionResolver (projects/resolver.py).

    Il resolver è completamente shadow: nessuna view lo usa.
    Verifica le regole descritte nella spec Step C.
    """

    PC = FolderPermissionGrant.PermissionCode
    EFFECT = FolderPermissionGrant.Effect

    def setUp(self):
        from django.contrib.auth.models import Group as DjangoGroup
        from projects.services import set_folder_path

        self.owner = User.objects.create_user('res_owner', password='pw')
        self.user = User.objects.create_user('res_user', password='pw')
        self.superuser = User.objects.create_user(
            'res_super', password='pw', is_superuser=True
        )
        self.staff = User.objects.create_user(
            'res_staff', password='pw', is_staff=True
        )

        self.group_a = DjangoGroup.objects.create(name='Resolver Group A')
        self.group_b = DjangoGroup.objects.create(name='Resolver Group B')

        # Struttura: root → child → grandchild
        self.root = ProjectFolder.objects.create(
            code='RES-ROOT', name='Root',
            folder_kind=ProjectFolder.FolderKind.GENERIC,
            status=ProjectFolder.Status.ACTIVE, owner=self.owner,
        )
        set_folder_path(self.root)

        self.child = ProjectFolder.objects.create(
            code='RES-CHILD', name='Child',
            folder_kind=ProjectFolder.FolderKind.GENERIC,
            status=ProjectFolder.Status.ACTIVE, owner=self.owner,
            parent=self.root,
        )
        set_folder_path(self.child)

        self.grandchild = ProjectFolder.objects.create(
            code='RES-GRAND', name='Grandchild',
            folder_kind=ProjectFolder.FolderKind.GENERIC,
            status=ProjectFolder.Status.ACTIVE, owner=self.owner,
            parent=self.child,
        )
        set_folder_path(self.grandchild)

    # ------------------------------------------------------------------
    # Helper
    # ------------------------------------------------------------------

    def _grant_user(self, folder, perm, effect='allow', inherit=True, expires_at=None):
        return FolderPermissionGrant.objects.create(
            folder=folder, user=self.user,
            permission_code=perm, effect=effect,
            inherit_to_children=inherit, expires_at=expires_at,
        )

    def _grant_group(self, folder, group, perm, effect='allow', inherit=True, expires_at=None):
        return FolderPermissionGrant.objects.create(
            folder=folder, group=group,
            permission_code=perm, effect=effect,
            inherit_to_children=inherit, expires_at=expires_at,
        )

    def _resolver(self, user=None, legacy=False):
        from projects.resolver import PermissionResolver
        return PermissionResolver(user or self.user, include_legacy_fallback=legacy)

    # ------------------------------------------------------------------
    # Base
    # ------------------------------------------------------------------

    def test_anonymous_user_is_denied(self):
        from django.contrib.auth.models import AnonymousUser
        from projects.resolver import has_folder_permission
        anon = AnonymousUser()
        self.assertFalse(has_folder_permission(anon, self.root, self.PC.READ_PUBLISHED))

    def test_none_user_is_denied(self):
        from projects.resolver import has_folder_permission
        self.assertFalse(has_folder_permission(None, self.root, self.PC.READ_PUBLISHED))

    def test_folder_none_is_denied(self):
        from projects.resolver import has_folder_permission
        self.assertFalse(has_folder_permission(self.user, None, self.PC.READ_PUBLISHED))

    def test_no_grant_is_denied(self):
        from projects.resolver import has_folder_permission
        self.assertFalse(has_folder_permission(self.user, self.root, self.PC.READ_PUBLISHED))

    def test_superuser_is_allowed(self):
        from projects.resolver import has_folder_permission
        self.assertTrue(has_folder_permission(self.superuser, self.root, self.PC.READ_PUBLISHED))

    def test_superuser_folder_none_is_allowed(self):
        from projects.resolver import has_folder_permission
        self.assertTrue(has_folder_permission(self.superuser, None, self.PC.READ_PUBLISHED))

    def test_staff_without_grant_is_denied(self):
        from projects.resolver import has_folder_permission
        self.assertFalse(has_folder_permission(self.staff, self.root, self.PC.READ_PUBLISHED))

    # ------------------------------------------------------------------
    # Grant diretti
    # ------------------------------------------------------------------

    def test_user_allow_grant_is_allowed(self):
        self._grant_user(self.root, self.PC.READ_PUBLISHED, effect='allow')
        from projects.resolver import has_folder_permission
        self.assertTrue(has_folder_permission(self.user, self.root, self.PC.READ_PUBLISHED))

    def test_user_deny_grant_is_denied(self):
        self._grant_user(self.root, self.PC.READ_PUBLISHED, effect='deny')
        from projects.resolver import has_folder_permission
        self.assertFalse(has_folder_permission(self.user, self.root, self.PC.READ_PUBLISHED))

    def test_group_allow_grant_is_allowed(self):
        self.user.groups.add(self.group_a)
        self._grant_group(self.root, self.group_a, self.PC.READ_PUBLISHED, effect='allow')
        from projects.resolver import has_folder_permission
        self.assertTrue(has_folder_permission(self.user, self.root, self.PC.READ_PUBLISHED))

    def test_group_deny_grant_is_denied(self):
        self.user.groups.add(self.group_a)
        self._grant_group(self.root, self.group_a, self.PC.READ_PUBLISHED, effect='deny')
        from projects.resolver import has_folder_permission
        self.assertFalse(has_folder_permission(self.user, self.root, self.PC.READ_PUBLISHED))

    # ------------------------------------------------------------------
    # Precedenza allo stesso livello
    # ------------------------------------------------------------------

    def test_user_allow_prevails_over_group_deny(self):
        """user_allow > group_deny sulla stessa cartella."""
        self.user.groups.add(self.group_a)
        self._grant_user(self.root, self.PC.READ_PUBLISHED, effect='allow')
        self._grant_group(self.root, self.group_a, self.PC.READ_PUBLISHED, effect='deny')
        from projects.resolver import has_folder_permission
        self.assertTrue(has_folder_permission(self.user, self.root, self.PC.READ_PUBLISHED))

    def test_user_deny_prevails_over_group_allow(self):
        """user_deny > group_allow sulla stessa cartella."""
        self.user.groups.add(self.group_a)
        self._grant_user(self.root, self.PC.READ_PUBLISHED, effect='deny')
        self._grant_group(self.root, self.group_a, self.PC.READ_PUBLISHED, effect='allow')
        from projects.resolver import has_folder_permission
        self.assertFalse(has_folder_permission(self.user, self.root, self.PC.READ_PUBLISHED))

    def test_evaluate_at_level_user_deny_over_user_allow(self):
        """
        user_deny > user_allow allo stesso livello.
        Il DB constraint impedisce due user-grant identici per design,
        ma _evaluate_grants_at_level deve gestire correttamente dati anomali.
        """
        from projects.resolver import PermissionResolver
        resolver = PermissionResolver.__new__(PermissionResolver)
        grants = [
            {'user_id': 1, 'group_id': None, 'effect': 'allow', 'inherit_to_children': True},
            {'user_id': 1, 'group_id': None, 'effect': 'deny', 'inherit_to_children': True},
        ]
        self.assertFalse(resolver._evaluate_grants_at_level(grants))

    def test_evaluate_at_level_group_deny_over_group_allow(self):
        """
        group_deny > group_allow allo stesso livello.
        Il DB constraint impedisce due group-grant identici per design,
        ma due gruppi diversi possono produrre effetti opposti.
        """
        self.user.groups.add(self.group_a)
        self.user.groups.add(self.group_b)
        self._grant_group(self.root, self.group_a, self.PC.READ_PUBLISHED, effect='allow')
        self._grant_group(self.root, self.group_b, self.PC.READ_PUBLISHED, effect='deny')
        from projects.resolver import has_folder_permission
        self.assertFalse(has_folder_permission(self.user, self.root, self.PC.READ_PUBLISHED))

    # ------------------------------------------------------------------
    # Ereditarietà
    # ------------------------------------------------------------------

    def test_parent_allow_inherited_to_child(self):
        """parent allow con inherit=True → child allow."""
        self._grant_user(self.root, self.PC.READ_PUBLISHED, effect='allow', inherit=True)
        from projects.resolver import has_folder_permission
        self.assertTrue(has_folder_permission(self.user, self.child, self.PC.READ_PUBLISHED))

    def test_parent_deny_inherited_to_child(self):
        """parent deny con inherit=True → child deny."""
        self._grant_user(self.root, self.PC.READ_PUBLISHED, effect='deny', inherit=True)
        from projects.resolver import has_folder_permission
        self.assertFalse(has_folder_permission(self.user, self.child, self.PC.READ_PUBLISHED))

    def test_parent_grant_not_inherited_when_inherit_false(self):
        """parent allow con inherit=False → child deny (non propagato)."""
        self._grant_user(self.root, self.PC.READ_PUBLISHED, effect='allow', inherit=False)
        from projects.resolver import has_folder_permission
        self.assertFalse(has_folder_permission(self.user, self.child, self.PC.READ_PUBLISHED))

    def test_child_deny_overrides_parent_allow(self):
        """parent allow, child deny → deny (specificità vince)."""
        self._grant_user(self.root, self.PC.READ_PUBLISHED, effect='allow', inherit=True)
        self._grant_user(self.child, self.PC.READ_PUBLISHED, effect='deny', inherit=True)
        from projects.resolver import has_folder_permission
        self.assertFalse(has_folder_permission(self.user, self.child, self.PC.READ_PUBLISHED))

    def test_child_allow_overrides_parent_deny(self):
        """parent deny, child allow → allow (specificità vince)."""
        self._grant_user(self.root, self.PC.READ_PUBLISHED, effect='deny', inherit=True)
        self._grant_user(self.child, self.PC.READ_PUBLISHED, effect='allow', inherit=True)
        from projects.resolver import has_folder_permission
        self.assertTrue(has_folder_permission(self.user, self.child, self.PC.READ_PUBLISHED))

    def test_depth_3_propagation(self):
        """root allow ereditato fino a grandchild (profondità 3)."""
        self._grant_user(self.root, self.PC.READ_PUBLISHED, effect='allow', inherit=True)
        from projects.resolver import has_folder_permission
        self.assertTrue(has_folder_permission(self.user, self.grandchild, self.PC.READ_PUBLISHED))

    def test_depth_3_grandchild_deny_overrides_root_allow(self):
        """root allow, grandchild deny → deny (specificità al livello più vicino vince)."""
        self._grant_user(self.root, self.PC.READ_PUBLISHED, effect='allow', inherit=True)
        self._grant_user(self.grandchild, self.PC.READ_PUBLISHED, effect='deny', inherit=True)
        from projects.resolver import has_folder_permission
        self.assertFalse(has_folder_permission(self.user, self.grandchild, self.PC.READ_PUBLISHED))

    def test_inherit_false_on_grandparent_blocks_grandchild(self):
        """root allow con inherit=False → non propagato a grandchild."""
        self._grant_user(self.root, self.PC.READ_PUBLISHED, effect='allow', inherit=False)
        from projects.resolver import has_folder_permission
        self.assertFalse(has_folder_permission(self.user, self.grandchild, self.PC.READ_PUBLISHED))

    # ------------------------------------------------------------------
    # Scadenza
    # ------------------------------------------------------------------

    def test_non_expired_grant_is_applied(self):
        """Grant con expires_at nel futuro è applicato."""
        future = timezone.now() + timezone.timedelta(days=30)
        self._grant_user(self.root, self.PC.READ_PUBLISHED, effect='allow', expires_at=future)
        from projects.resolver import has_folder_permission
        self.assertTrue(has_folder_permission(self.user, self.root, self.PC.READ_PUBLISHED))

    def test_expired_grant_is_ignored(self):
        """Grant con expires_at nel passato è ignorato → deny."""
        past = timezone.now() - timezone.timedelta(seconds=1)
        self._grant_user(self.root, self.PC.READ_PUBLISHED, effect='allow', expires_at=past)
        from projects.resolver import has_folder_permission
        self.assertFalse(has_folder_permission(self.user, self.root, self.PC.READ_PUBLISHED))

    # ------------------------------------------------------------------
    # Cache
    # ------------------------------------------------------------------

    def test_cache_same_instance_no_extra_query(self):
        """Seconda chiamata sulla stessa istanza non genera nuove query DB."""
        self._grant_user(self.root, self.PC.READ_PUBLISHED, effect='allow')
        from projects.resolver import PermissionResolver
        resolver = PermissionResolver(self.user)
        # Prima chiamata: risolve
        result1 = resolver.has_permission(self.root, self.PC.READ_PUBLISHED)
        # Seconda chiamata: deve usare cache (0 query aggiuntive per il grant lookup)
        with self.assertNumQueries(0):
            result2 = resolver.has_permission(self.root, self.PC.READ_PUBLISHED)
        self.assertEqual(result1, result2)

    def test_for_request_same_config_returns_same_instance(self):
        """for_request con stessa configurazione restituisce la stessa istanza."""
        from projects.resolver import PermissionResolver

        class FakeRequest:
            user = self.user

        req = FakeRequest()
        r1 = PermissionResolver.for_request(req, include_legacy_fallback=False)
        r2 = PermissionResolver.for_request(req, include_legacy_fallback=False)
        self.assertIs(r1, r2)

    def test_for_request_different_fallback_returns_different_instance(self):
        """for_request con fallback diverso restituisce istanze distinte."""
        from projects.resolver import PermissionResolver

        class FakeRequest:
            user = self.user

        req = FakeRequest()
        r_no_fb = PermissionResolver.for_request(req, include_legacy_fallback=False)
        r_with_fb = PermissionResolver.for_request(req, include_legacy_fallback=True)
        self.assertIsNot(r_no_fb, r_with_fb)

    # ------------------------------------------------------------------
    # Fallback legacy — comportamento di base
    # ------------------------------------------------------------------

    def test_legacy_fallback_disabled_by_default(self):
        """Senza include_legacy_fallback=True il fallback non scatta."""
        ProjectFolderMembership.objects.create(
            folder=self.root, user=self.user, role='reader'
        )
        from projects.resolver import has_folder_permission
        # reader legacy avrebbe READ_PUBLISHED, ma il fallback è disabilitato
        self.assertFalse(
            has_folder_permission(self.user, self.root, self.PC.READ_PUBLISHED)
        )

    def test_legacy_fallback_enabled_explicitly(self):
        """Con include_legacy_fallback=True il fallback è attivo."""
        ProjectFolderMembership.objects.create(
            folder=self.root, user=self.user, role='reader'
        )
        from projects.resolver import has_folder_permission
        self.assertTrue(
            has_folder_permission(
                self.user, self.root, self.PC.READ_PUBLISHED,
                include_legacy_fallback=True,
            )
        )

    def test_modular_grant_prevails_over_legacy_fallback(self):
        """Grant modulare allow prevale sul fallback legacy (che avrebbe comunque allow)."""
        ProjectFolderMembership.objects.create(
            folder=self.root, user=self.user, role='reader'
        )
        self._grant_user(self.root, self.PC.READ_PUBLISHED, effect='allow')
        from projects.resolver import has_folder_permission
        self.assertTrue(
            has_folder_permission(
                self.user, self.root, self.PC.READ_PUBLISHED,
                include_legacy_fallback=True,
            )
        )

    def test_modular_deny_not_overridden_by_legacy_fallback(self):
        """Grant modulare deny NON viene sovrascritto dal fallback legacy."""
        # Membership reader → avrebbe READ_PUBLISHED via fallback
        ProjectFolderMembership.objects.create(
            folder=self.root, user=self.user, role='reader'
        )
        # Grant modulare esplicito: deny
        self._grant_user(self.root, self.PC.READ_PUBLISHED, effect='deny')
        from projects.resolver import has_folder_permission
        self.assertFalse(
            has_folder_permission(
                self.user, self.root, self.PC.READ_PUBLISHED,
                include_legacy_fallback=True,
            )
        )

    # ------------------------------------------------------------------
    # Fallback legacy — mapping conservativo per ruolo
    # ------------------------------------------------------------------

    def _check_legacy(self, role, perm, expected):
        """Helper: crea membership con role e verifica il permesso via fallback."""
        ProjectFolderMembership.objects.filter(folder=self.root, user=self.user).delete()
        ProjectFolderMembership.objects.create(
            folder=self.root, user=self.user, role=role
        )
        from projects.resolver import has_folder_permission
        result = has_folder_permission(
            self.user, self.root, perm,
            include_legacy_fallback=True,
        )
        self.assertEqual(
            result, expected,
            f"ruolo={role} perm={perm} atteso={expected} ottenuto={result}",
        )

    def test_legacy_reader_can_read_published(self):
        self._check_legacy('reader', self.PC.READ_PUBLISHED, True)

    def test_legacy_reader_cannot_create_draft(self):
        self._check_legacy('reader', self.PC.CREATE_DRAFT, False)

    def test_legacy_author_can_read_published(self):
        self._check_legacy('author', self.PC.READ_PUBLISHED, True)

    def test_legacy_author_can_create_draft(self):
        self._check_legacy('author', self.PC.CREATE_DRAFT, True)

    def test_legacy_author_cannot_manage_folder(self):
        self._check_legacy('author', self.PC.MANAGE_FOLDER, False)

    def test_legacy_approver_can_read_published(self):
        self._check_legacy('approver', self.PC.READ_PUBLISHED, True)

    def test_legacy_approver_is_eligible_approver(self):
        self._check_legacy('approver', self.PC.ELIGIBLE_APPROVER, True)

    def test_legacy_approver_cannot_create_draft(self):
        self._check_legacy('approver', self.PC.CREATE_DRAFT, False)

    def test_legacy_auditor_can_view_history(self):
        self._check_legacy('auditor', self.PC.VIEW_HISTORY, True)

    def test_legacy_auditor_can_view_obsolete(self):
        self._check_legacy('auditor', self.PC.VIEW_OBSOLETE_DOCUMENTS, True)

    def test_legacy_auditor_cannot_create_draft(self):
        self._check_legacy('auditor', self.PC.CREATE_DRAFT, False)

    def test_legacy_manager_can_manage_folder(self):
        self._check_legacy('manager', self.PC.MANAGE_FOLDER, True)

    def test_legacy_manager_can_do_everything(self):
        from projects.resolver import _LEGACY_MANAGER_PERMISSIONS
        for perm in _LEGACY_MANAGER_PERMISSIONS:
            self._check_legacy('manager', perm, True)


# ===========================================================================
# Step D1 — BackfillFolderPermissionGrantsTests
# ===========================================================================

class BackfillFolderPermissionGrantsTests(TestCase):
    """
    Test del management command backfill_folder_permission_grants.
    """

    def setUp(self):
        from projects.services import set_folder_path
        self.owner = User.objects.create_user('bf_owner', password='pw')
        self.user = User.objects.create_user('bf_user', password='pw')
        self.folder = ProjectFolder.objects.create(
            code='BF-FOLD', name='BF Folder',
            folder_kind=ProjectFolder.FolderKind.GENERIC,
            status=ProjectFolder.Status.ACTIVE,
            owner=self.owner,
        )
        set_folder_path(self.folder)

    def _call(self, *args, **kwargs):
        from io import StringIO
        from django.core.management import call_command
        out = StringIO()
        call_command(*args, stdout=out, **kwargs)
        return out.getvalue()

    def _membership(self, role, user=None):
        return ProjectFolderMembership.objects.create(
            folder=self.folder,
            user=user or self.user,
            role=role,
        )

    # 1. Dry-run non scrive nulla
    def test_dry_run_creates_no_grants(self):
        self._membership('reader')
        self._call('backfill_folder_permission_grants')
        self.assertEqual(FolderPermissionGrant.objects.count(), 0)

    # 2. Apply crea grant
    def test_apply_creates_grants(self):
        self._membership('reader')
        self._call('backfill_folder_permission_grants', apply=True)
        self.assertTrue(
            FolderPermissionGrant.objects.filter(
                folder=self.folder, user=self.user,
                permission_code='read_published',
            ).exists()
        )

    # 3. Seconda apply non crea duplicati
    def test_apply_idempotent(self):
        self._membership('reader')
        self._call('backfill_folder_permission_grants', apply=True)
        count_after_first = FolderPermissionGrant.objects.count()
        self._call('backfill_folder_permission_grants', apply=True)
        self.assertEqual(FolderPermissionGrant.objects.count(), count_after_first)

    # 4. Membership legacy resta invariata
    def test_legacy_membership_untouched(self):
        m = self._membership('author')
        self._call('backfill_folder_permission_grants', apply=True)
        m.refresh_from_db()
        self.assertEqual(m.role, 'author')
        self.assertEqual(m.folder, self.folder)
        self.assertEqual(m.user, self.user)

    # 5. Grant manuale esistente non viene sovrascritto (stesso effetto → skip)
    def test_existing_allow_grant_not_overwritten(self):
        self._membership('reader')
        existing = FolderPermissionGrant.objects.create(
            folder=self.folder, user=self.user,
            permission_code='read_published',
            effect='allow', inherit_to_children=True,
            notes='manuale',
        )
        self._call('backfill_folder_permission_grants', apply=True)
        existing.refresh_from_db()
        self.assertEqual(existing.notes, 'manuale')  # notes invariate
        self.assertTrue(existing.inherit_to_children)  # non modificato a False

    # 6. Grant opposto (deny) genera conflitto nel report
    def test_deny_grant_produces_conflict_in_output(self):
        self._membership('reader')
        FolderPermissionGrant.objects.create(
            folder=self.folder, user=self.user,
            permission_code='read_published',
            effect='deny',
        )
        out = self._call('backfill_folder_permission_grants')
        self.assertIn('CONFLITTI', out)

    # 7. Conflitto non viene modificato
    def test_deny_grant_not_modified_on_apply(self):
        self._membership('reader')
        deny_grant = FolderPermissionGrant.objects.create(
            folder=self.folder, user=self.user,
            permission_code='read_published',
            effect='deny',
        )
        self._call('backfill_folder_permission_grants', apply=True)
        deny_grant.refresh_from_db()
        self.assertEqual(deny_grant.effect, 'deny')  # invariato

    # 8. Notes backfill leggibili e contengono ID membership
    def test_notes_contain_membership_id(self):
        m = self._membership('reader')
        self._call('backfill_folder_permission_grants', apply=True)
        grant = FolderPermissionGrant.objects.get(
            folder=self.folder, user=self.user, permission_code='read_published'
        )
        self.assertIn(str(m.pk), grant.notes)
        self.assertIn('Legacy backfill', grant.notes)

    # 9. inherit_to_children=False
    def test_backfill_grants_have_inherit_false(self):
        self._membership('reader')
        self._call('backfill_folder_permission_grants', apply=True)
        grant = FolderPermissionGrant.objects.get(
            folder=self.folder, user=self.user, permission_code='read_published'
        )
        self.assertFalse(grant.inherit_to_children)

    # 10. Transazione atomica: errore inatteso non lascia scritture parziali
    def test_atomic_rollback_on_error(self):
        """
        Verifica atomicità: se un errore avviene a metà apply, nessun grant
        viene persistito. Simuliamo l'errore via mock.
        """
        from unittest.mock import patch
        self._membership('author')  # author ha 3 permessi → testa rollback in mezzo
        original_count = FolderPermissionGrant.objects.count()

        call_count = [0]
        original_create = FolderPermissionGrant.objects.create

        def fail_on_second(**kwargs):
            call_count[0] += 1
            if call_count[0] >= 2:
                raise RuntimeError('Errore simulato in test atomicità')
            return original_create(**kwargs)

        with patch.object(
            FolderPermissionGrant.objects.__class__,
            'create',
            side_effect=fail_on_second
        ):
            with self.assertRaises(RuntimeError):
                self._call('backfill_folder_permission_grants', apply=True)

        # Nessun grant deve essere rimasto
        self.assertEqual(FolderPermissionGrant.objects.count(), original_count)

    # 11. Mapping reader
    def test_mapping_reader(self):
        from projects.management.commands.backfill_folder_permission_grants import (
            BACKFILL_ROLE_PERMISSIONS,
        )
        self._membership('reader')
        self._call('backfill_folder_permission_grants', apply=True)
        created_perms = set(
            FolderPermissionGrant.objects.filter(
                folder=self.folder, user=self.user
            ).values_list('permission_code', flat=True)
        )
        self.assertEqual(created_perms, BACKFILL_ROLE_PERMISSIONS['reader'])

    # 12. Mapping author
    def test_mapping_author(self):
        from projects.management.commands.backfill_folder_permission_grants import (
            BACKFILL_ROLE_PERMISSIONS,
        )
        self._membership('author')
        self._call('backfill_folder_permission_grants', apply=True)
        created_perms = set(
            FolderPermissionGrant.objects.filter(
                folder=self.folder, user=self.user
            ).values_list('permission_code', flat=True)
        )
        self.assertEqual(created_perms, BACKFILL_ROLE_PERMISSIONS['author'])

    # 13. Mapping approver
    def test_mapping_approver(self):
        from projects.management.commands.backfill_folder_permission_grants import (
            BACKFILL_ROLE_PERMISSIONS,
        )
        self._membership('approver')
        self._call('backfill_folder_permission_grants', apply=True)
        created_perms = set(
            FolderPermissionGrant.objects.filter(
                folder=self.folder, user=self.user
            ).values_list('permission_code', flat=True)
        )
        self.assertEqual(created_perms, BACKFILL_ROLE_PERMISSIONS['approver'])

    # 14. Mapping auditor
    def test_mapping_auditor(self):
        from projects.management.commands.backfill_folder_permission_grants import (
            BACKFILL_ROLE_PERMISSIONS,
        )
        self._membership('auditor')
        self._call('backfill_folder_permission_grants', apply=True)
        created_perms = set(
            FolderPermissionGrant.objects.filter(
                folder=self.folder, user=self.user
            ).values_list('permission_code', flat=True)
        )
        self.assertEqual(created_perms, BACKFILL_ROLE_PERMISSIONS['auditor'])

    # 15. Mapping manager (conservativo: non include permessi esclusi)
    def test_mapping_manager_conservative(self):
        from projects.management.commands.backfill_folder_permission_grants import (
            BACKFILL_ROLE_PERMISSIONS,
        )
        self._membership('manager')
        self._call('backfill_folder_permission_grants', apply=True)
        created_perms = set(
            FolderPermissionGrant.objects.filter(
                folder=self.folder, user=self.user
            ).values_list('permission_code', flat=True)
        )
        self.assertEqual(created_perms, BACKFILL_ROLE_PERMISSIONS['manager'])
        # Verifica che i permessi esclusi NON siano stati assegnati
        excluded = {
            'view_folder_ecns', 'manage_project_documents', 'request_ecn',
            'view_obsolete_documents', 'manage_rejected_drafts',
        }
        self.assertTrue(created_perms.isdisjoint(excluded))


# ===========================================================================
# Step D2 — CompareFolderPermissionsTests
# ===========================================================================

class CompareFolderPermissionsTests(TestCase):
    """
    Test del management command compare_folder_permissions.
    """

    def setUp(self):
        from projects.services import set_folder_path
        self.owner = User.objects.create_user('cmp_owner', password='pw')
        self.user = User.objects.create_user('cmp_user', password='pw')
        self.folder = ProjectFolder.objects.create(
            code='CMP-FOLD', name='Compare Folder',
            folder_kind=ProjectFolder.FolderKind.GENERIC,
            status=ProjectFolder.Status.ACTIVE,
            owner=self.owner,
        )
        set_folder_path(self.folder)

    def _call_compare(self, **kwargs):
        from io import StringIO
        from django.core.management import call_command
        out = StringIO()
        exit_code = 0
        try:
            call_command('compare_folder_permissions', stdout=out, **kwargs)
        except SystemExit as e:
            exit_code = e.code
        return out.getvalue(), exit_code

    def _backfill(self):
        """Helper: esegue il backfill apply per la cartella corrente."""
        from io import StringIO
        from django.core.management import call_command
        call_command(
            'backfill_folder_permission_grants',
            apply=True,
            folder_id=self.folder.pk,
            stdout=StringIO(),
        )

    # 1. Confronto senza divergenze → exit code 0
    def test_no_divergences_exit_code_0(self):
        ProjectFolderMembership.objects.create(
            folder=self.folder, user=self.user, role='reader'
        )
        self._backfill()
        _, exit_code = self._call_compare()
        self.assertEqual(exit_code, 0)

    # 2. Divergenza rilevata → exit code diverso da 0
    def test_divergence_exit_code_nonzero(self):
        # Membership presente ma nessun grant backfillato
        ProjectFolderMembership.objects.create(
            folder=self.folder, user=self.user, role='reader'
        )
        # Nessun backfill → divergenza: legacy=True, resolver=False
        _, exit_code = self._call_compare()
        self.assertNotEqual(exit_code, 0)

    # 3. --user-id filtra correttamente
    def test_user_id_filter(self):
        other = User.objects.create_user('cmp_other', password='pw')
        ProjectFolderMembership.objects.create(
            folder=self.folder, user=self.user, role='reader'
        )
        ProjectFolderMembership.objects.create(
            folder=self.folder, user=other, role='reader'
        )
        self._backfill()
        # Backfill ha creato grants per entrambi: filtro per user → 0 divergenze
        _, exit_code = self._call_compare(user_id=self.user.pk)
        self.assertEqual(exit_code, 0)

    # 4. --folder-id filtra correttamente
    def test_folder_id_filter(self):
        from projects.services import set_folder_path
        other_folder = ProjectFolder.objects.create(
            code='CMP-OTHER', name='Other',
            folder_kind=ProjectFolder.FolderKind.GENERIC,
            status=ProjectFolder.Status.ACTIVE, owner=self.owner,
        )
        set_folder_path(other_folder)
        ProjectFolderMembership.objects.create(
            folder=self.folder, user=self.user, role='reader'
        )
        ProjectFolderMembership.objects.create(
            folder=other_folder, user=self.user, role='reader'
        )
        # Backfill solo su self.folder
        self._backfill()
        # Compare limitato a self.folder: dovrebbe essere ok
        _, exit_code = self._call_compare(folder_id=self.folder.pk)
        self.assertEqual(exit_code, 0)

    # 5. Nessuna modifica al database dopo compare
    def test_compare_does_not_modify_database(self):
        ProjectFolderMembership.objects.create(
            folder=self.folder, user=self.user, role='author'
        )
        self._backfill()
        grant_count_before = FolderPermissionGrant.objects.count()
        membership_count_before = ProjectFolderMembership.objects.count()
        self._call_compare()
        self.assertEqual(FolderPermissionGrant.objects.count(), grant_count_before)
        self.assertEqual(ProjectFolderMembership.objects.count(), membership_count_before)

    # 6. Compare usa il resolver senza fallback legacy
    def test_compare_uses_resolver_without_legacy_fallback(self):
        """
        Con membership ma senza grant modulari, il compare deve rilevare
        una divergenza (legacy=True, resolver=False) — il che dimostra
        che usa il resolver senza fallback, non il legacy direttamente.
        """
        ProjectFolderMembership.objects.create(
            folder=self.folder, user=self.user, role='reader'
        )
        # Nessun grant → resolver senza fallback dice False; legacy dice True
        out, exit_code = self._call_compare()
        self.assertNotEqual(exit_code, 0)
        self.assertIn('Divergenze', out)


# ===========================================================================
# Step E — Integrazione resolver nelle funzioni base dei permessi cartella
# ===========================================================================

class StepEFolderPermissionsIntegrationTests(TestCase):
    """
    Verifica che can_view_folder, can_create_document_in_folder e
    can_manage_folder usino il resolver modulare con fallback legacy.

    - Grant modulare presente → decide il resolver
    - Nessun grant modulare → fallback ProjectFolderMembership
    - Deny modulare → blocca anche se la membership legacy permetterebbe
    - Superuser → allow totale
    - Staff non-superuser senza grant → deny
    """

    def setUp(self):
        from django.contrib.auth.models import Group as DjangoGroup
        from projects.services import set_folder_path

        self.owner = User.objects.create_user('se_owner', password='pw')
        self.user = User.objects.create_user('se_user', password='pw')
        self.superuser = User.objects.create_user(
            'se_super', password='pw', is_superuser=True,
        )
        self.staff = User.objects.create_user(
            'se_staff', password='pw', is_staff=True,
        )
        self.group = DjangoGroup.objects.create(name='SE Test Group')

        self.root = ProjectFolder.objects.create(
            code='SE-ROOT', name='Root',
            folder_kind=ProjectFolder.FolderKind.GENERIC,
            status=ProjectFolder.Status.ACTIVE, owner=self.owner,
        )
        set_folder_path(self.root)

        self.child = ProjectFolder.objects.create(
            code='SE-CHILD', name='Child',
            folder_kind=ProjectFolder.FolderKind.GENERIC,
            status=ProjectFolder.Status.ACTIVE, owner=self.owner,
            parent=self.root,
        )
        set_folder_path(self.child)

    def _membership(self, role, folder=None):
        return ProjectFolderMembership.objects.create(
            folder=folder or self.root,
            user=self.user,
            role=role,
        )

    def _grant_user(self, folder, perm, effect='allow', inherit=True, expires_at=None):
        return FolderPermissionGrant.objects.create(
            folder=folder, user=self.user,
            permission_code=perm, effect=effect,
            inherit_to_children=inherit, expires_at=expires_at,
        )

    def _grant_group(self, folder, perm, effect='allow', inherit=True):
        return FolderPermissionGrant.objects.create(
            folder=folder, group=self.group,
            permission_code=perm, effect=effect,
            inherit_to_children=inherit,
        )

    # ------------------------------------------------------------------
    # can_view_folder — lettura
    # ------------------------------------------------------------------

    def test_reader_legacy_no_grant_can_view_via_fallback(self):
        """reader legacy senza grant → legge tramite fallback membership."""
        self._membership('reader')
        from projects.permissions import can_view_folder
        self.assertTrue(can_view_folder(self.user, self.root))

    def test_no_membership_no_grant_cannot_view(self):
        """nessuna membership e nessun grant → deny."""
        from projects.permissions import can_view_folder
        self.assertFalse(can_view_folder(self.user, self.root))

    def test_user_allow_grant_can_view(self):
        """grant modulare allow → legge senza membership."""
        self._grant_user(self.root, 'read_published', effect='allow')
        from projects.permissions import can_view_folder
        self.assertTrue(can_view_folder(self.user, self.root))

    def test_user_deny_grant_blocks_legacy_membership(self):
        """deny modulare → blocca anche se membership legacy permette."""
        self._membership('reader')
        self._grant_user(self.root, 'read_published', effect='deny')
        from projects.permissions import can_view_folder
        self.assertFalse(can_view_folder(self.user, self.root))

    def test_group_allow_grant_can_view(self):
        """grant di gruppo allow → legge."""
        self.user.groups.add(self.group)
        self._grant_group(self.root, 'read_published', effect='allow')
        from projects.permissions import can_view_folder
        self.assertTrue(can_view_folder(self.user, self.root))

    def test_parent_allow_inherited_enables_child_view(self):
        """grant allow ereditato dal parent → legge la sottocartella."""
        self._grant_user(self.root, 'read_published', effect='allow', inherit=True)
        from projects.permissions import can_view_folder
        self.assertTrue(can_view_folder(self.user, self.child))

    def test_child_deny_blocks_parent_inherited_allow(self):
        """deny sul child → blocca allow ereditato dal parent."""
        self._grant_user(self.root, 'read_published', effect='allow', inherit=True)
        self._grant_user(self.child, 'read_published', effect='deny')
        from projects.permissions import can_view_folder
        self.assertFalse(can_view_folder(self.user, self.child))

    def test_expired_grant_ignored_falls_back_to_deny(self):
        """grant scaduto ignorato → senza fallback membership → deny."""
        past = timezone.now() - timezone.timedelta(seconds=1)
        self._grant_user(self.root, 'read_published', effect='allow', expires_at=past)
        from projects.permissions import can_view_folder
        self.assertFalse(can_view_folder(self.user, self.root))

    def test_superuser_can_view(self):
        """superuser → allow totale."""
        from projects.permissions import can_view_folder
        self.assertTrue(can_view_folder(self.superuser, self.root))

    def test_staff_without_grant_cannot_view(self):
        """staff non-superuser senza grant → deny."""
        from projects.permissions import can_view_folder
        self.assertFalse(can_view_folder(self.staff, self.root))

    # ------------------------------------------------------------------
    # can_create_document_in_folder — creazione bozza
    # ------------------------------------------------------------------

    def test_author_legacy_no_grant_can_create_via_fallback(self):
        """author legacy senza grant → può creare tramite fallback membership."""
        self._membership('author')
        from projects.permissions import can_create_document_in_folder
        self.assertTrue(can_create_document_in_folder(self.user, self.root))

    def test_create_draft_grant_can_create(self):
        """grant modulare create_draft → può creare senza membership."""
        self._grant_user(self.root, 'create_draft', effect='allow')
        from projects.permissions import can_create_document_in_folder
        self.assertTrue(can_create_document_in_folder(self.user, self.root))

    def test_deny_create_draft_blocks_author_legacy(self):
        """deny create_draft → blocca anche author legacy."""
        self._membership('author')
        self._grant_user(self.root, 'create_draft', effect='deny')
        from projects.permissions import can_create_document_in_folder
        self.assertFalse(can_create_document_in_folder(self.user, self.root))

    def test_reader_without_create_draft_grant_cannot_create(self):
        """reader legacy senza grant create_draft → deny (fallback non ha create_draft)."""
        self._membership('reader')
        from projects.permissions import can_create_document_in_folder
        self.assertFalse(can_create_document_in_folder(self.user, self.root))

    def test_parent_create_draft_inherited_enables_child_create(self):
        """grant create_draft ereditato dal parent → abilita sottocartella."""
        self._grant_user(self.root, 'create_draft', effect='allow', inherit=True)
        from projects.permissions import can_create_document_in_folder
        self.assertTrue(can_create_document_in_folder(self.user, self.child))

    # ------------------------------------------------------------------
    # can_manage_folder — gestione cartella
    # ------------------------------------------------------------------

    def test_manager_legacy_no_grant_can_manage_via_fallback(self):
        """manager legacy senza grant → può gestire tramite fallback membership."""
        self._membership('manager')
        from projects.permissions import can_manage_folder
        self.assertTrue(can_manage_folder(self.user, self.root))

    def test_manage_folder_grant_can_manage(self):
        """grant modulare manage_folder → abilita senza membership."""
        self._grant_user(self.root, 'manage_folder', effect='allow')
        from projects.permissions import can_manage_folder
        self.assertTrue(can_manage_folder(self.user, self.root))

    def test_deny_manage_folder_blocks_manager_legacy(self):
        """deny manage_folder → blocca anche manager legacy."""
        self._membership('manager')
        self._grant_user(self.root, 'manage_folder', effect='deny')
        from projects.permissions import can_manage_folder
        self.assertFalse(can_manage_folder(self.user, self.root))

    def test_staff_without_manage_grant_cannot_manage(self):
        """staff non-superuser senza grant → deny."""
        from projects.permissions import can_manage_folder
        self.assertFalse(can_manage_folder(self.staff, self.root))


# ===========================================================================
# Step F — Bulk resolver tests
# ===========================================================================

class BulkResolverTests(TestCase):
    """Test della API bulk PermissionResolver.resolve_bulk."""

    def setUp(self):
        from django.contrib.auth.models import Group as DjangoGroup
        from projects.services import set_folder_path

        self.owner = User.objects.create_user('blk_owner', password='pw')
        self.user = User.objects.create_user('blk_user', password='pw')
        self.superuser = User.objects.create_user('blk_super', password='pw', is_superuser=True)
        self.staff = User.objects.create_user('blk_staff', password='pw', is_staff=True)
        self.group = DjangoGroup.objects.create(name='Bulk Test Group')

        self.root = ProjectFolder.objects.create(
            code='BLK-R', name='Root',
            folder_kind=ProjectFolder.FolderKind.GENERIC,
            status=ProjectFolder.Status.ACTIVE, owner=self.owner,
        )
        set_folder_path(self.root)
        self.child = ProjectFolder.objects.create(
            code='BLK-C', name='Child',
            folder_kind=ProjectFolder.FolderKind.GENERIC,
            status=ProjectFolder.Status.ACTIVE, owner=self.owner,
            parent=self.root,
        )
        set_folder_path(self.child)
        self.other = ProjectFolder.objects.create(
            code='BLK-O', name='Other',
            folder_kind=ProjectFolder.FolderKind.GENERIC,
            status=ProjectFolder.Status.ACTIVE, owner=self.owner,
        )
        set_folder_path(self.other)

    def _grant_user(self, folder, perm, effect='allow', inherit=True, expires_at=None):
        return FolderPermissionGrant.objects.create(
            folder=folder, user=self.user,
            permission_code=perm, effect=effect,
            inherit_to_children=inherit, expires_at=expires_at,
        )

    def _grant_group(self, folder, perm, effect='allow', inherit=True):
        return FolderPermissionGrant.objects.create(
            folder=folder, group=self.group,
            permission_code=perm, effect=effect,
            inherit_to_children=inherit,
        )

    def _bulk(self, user=None, legacy=False):
        from projects.resolver import PermissionResolver
        return PermissionResolver(user or self.user, include_legacy_fallback=legacy)

    # 1. Bulk allow utente
    def test_bulk_user_allow(self):
        self._grant_user(self.root, 'read_published', effect='allow')
        results = self._bulk().resolve_bulk([self.root, self.other], 'read_published')
        self.assertTrue(results[self.root.pk])
        self.assertFalse(results[self.other.pk])

    # 2. Bulk deny utente
    def test_bulk_user_deny(self):
        self._grant_user(self.root, 'read_published', effect='deny')
        results = self._bulk().resolve_bulk([self.root], 'read_published')
        self.assertFalse(results[self.root.pk])

    # 3. Bulk allow gruppo
    def test_bulk_group_allow(self):
        self.user.groups.add(self.group)
        self._grant_group(self.root, 'read_published', effect='allow')
        results = self._bulk().resolve_bulk([self.root], 'read_published')
        self.assertTrue(results[self.root.pk])

    # 4. Bulk deny gruppo
    def test_bulk_group_deny(self):
        self.user.groups.add(self.group)
        self._grant_group(self.root, 'read_published', effect='deny')
        results = self._bulk().resolve_bulk([self.root], 'read_published')
        self.assertFalse(results[self.root.pk])

    # 5. Child deny prevale su parent allow
    def test_bulk_child_deny_overrides_parent_allow(self):
        self._grant_user(self.root, 'read_published', effect='allow', inherit=True)
        self._grant_user(self.child, 'read_published', effect='deny')
        results = self._bulk().resolve_bulk([self.root, self.child], 'read_published')
        self.assertTrue(results[self.root.pk])
        self.assertFalse(results[self.child.pk])

    # 6. Child allow prevale su parent deny
    def test_bulk_child_allow_overrides_parent_deny(self):
        self._grant_user(self.root, 'read_published', effect='deny', inherit=True)
        self._grant_user(self.child, 'read_published', effect='allow')
        results = self._bulk().resolve_bulk([self.child], 'read_published')
        self.assertTrue(results[self.child.pk])

    # 7. User override prevale su gruppo (user_allow > group_deny)
    def test_bulk_user_allow_overrides_group_deny(self):
        self.user.groups.add(self.group)
        self._grant_user(self.root, 'read_published', effect='allow')
        self._grant_group(self.root, 'read_published', effect='deny')
        results = self._bulk().resolve_bulk([self.root], 'read_published')
        self.assertTrue(results[self.root.pk])

    # 8. Grant scaduto ignorato
    def test_bulk_expired_grant_ignored(self):
        past = timezone.now() - timezone.timedelta(seconds=1)
        self._grant_user(self.root, 'read_published', effect='allow', expires_at=past)
        results = self._bulk().resolve_bulk([self.root], 'read_published')
        self.assertFalse(results[self.root.pk])

    # 9. Bulk fallback legacy
    def test_bulk_legacy_fallback(self):
        ProjectFolderMembership.objects.create(
            folder=self.root, user=self.user, role='reader'
        )
        results = self._bulk(legacy=True).resolve_bulk([self.root, self.other], 'read_published')
        self.assertTrue(results[self.root.pk])
        self.assertFalse(results[self.other.pk])

    # 10. Deny modulare blocca legacy allow
    def test_bulk_modular_deny_blocks_legacy_allow(self):
        ProjectFolderMembership.objects.create(
            folder=self.root, user=self.user, role='reader'
        )
        self._grant_user(self.root, 'read_published', effect='deny')
        results = self._bulk(legacy=True).resolve_bulk([self.root], 'read_published')
        self.assertFalse(results[self.root.pk])

    # 11. Senza grant e senza membership → deny
    def test_bulk_no_grant_no_membership_deny(self):
        results = self._bulk(legacy=True).resolve_bulk([self.root, self.child, self.other], 'read_published')
        self.assertFalse(results[self.root.pk])
        self.assertFalse(results[self.child.pk])
        self.assertFalse(results[self.other.pk])

    # 12. Superuser → tutto allow
    def test_bulk_superuser_all_allow(self):
        from projects.resolver import PermissionResolver
        resolver = PermissionResolver(self.superuser)
        results = resolver.resolve_bulk([self.root, self.child, self.other], 'read_published')
        self.assertTrue(all(results.values()))

    # 13. Staff senza grant → deny
    def test_bulk_staff_no_grant_deny(self):
        from projects.resolver import PermissionResolver
        resolver = PermissionResolver(self.staff)
        results = resolver.resolve_bulk([self.root], 'read_published')
        self.assertFalse(results[self.root.pk])

    # 14. Query count ragionevole su albero multi-cartella
    def test_bulk_query_count_reasonable(self):
        """resolve_bulk deve usare un numero fisso di query indipendentemente dal numero di cartelle."""
        from projects.resolver import PermissionResolver
        from django.test.utils import CaptureQueriesContext
        from django.db import connection

        # Crea 5 cartelle extra per verificare che non ci sia N+1
        from projects.services import set_folder_path
        extra_folders = []
        for i in range(5):
            f = ProjectFolder.objects.create(
                code=f'BLK-EX{i}', name=f'Extra {i}',
                folder_kind=ProjectFolder.FolderKind.GENERIC,
                status=ProjectFolder.Status.ACTIVE, owner=self.owner,
            )
            set_folder_path(f)
            extra_folders.append(f)
            FolderPermissionGrant.objects.create(
                folder=f, user=self.user,
                permission_code='read_published', effect='allow',
            )

        resolver = PermissionResolver(self.user, include_legacy_fallback=True)
        all_folders = [self.root, self.child, self.other] + extra_folders

        # Pre-carica group_ids per non contarla nel bulk
        resolver._get_group_ids()

        with CaptureQueriesContext(connection) as ctx:
            results = resolver.resolve_bulk(all_folders, 'read_published')

        # Massimo 2 query: 1 per grants, 1 per legacy membership
        self.assertLessEqual(len(ctx), 2,
            f"resolve_bulk ha eseguito {len(ctx)} query su {len(all_folders)} cartelle")


# ===========================================================================
# Step F — Cartelle: visibilità e navigazione
# ===========================================================================

class StepFFolderListIntegrationTests(TestCase):
    """
    Verifica l'integrazione del resolver nelle liste cartelle,
    incluso il concetto di cartella navigation-only.
    """

    def setUp(self):
        from projects.services import set_folder_path
        self.owner = User.objects.create_user('sff_owner', password='pw')
        self.user = User.objects.create_user('sff_user', password='pw')
        self.superuser = User.objects.create_user('sff_super', password='pw', is_superuser=True)

        self.root = ProjectFolder.objects.create(
            code='SFF-ROOT', name='Root',
            folder_kind=ProjectFolder.FolderKind.GENERIC,
            status=ProjectFolder.Status.ACTIVE, owner=self.owner,
        )
        set_folder_path(self.root)
        self.child = ProjectFolder.objects.create(
            code='SFF-CHILD', name='Child',
            folder_kind=ProjectFolder.FolderKind.GENERIC,
            status=ProjectFolder.Status.ACTIVE, owner=self.owner,
            parent=self.root,
        )
        set_folder_path(self.child)

    def _grant_user(self, folder, perm, effect='allow', inherit=True):
        return FolderPermissionGrant.objects.create(
            folder=folder, user=self.user,
            permission_code=perm, effect=effect,
            inherit_to_children=inherit,
        )

    def _membership(self, role, folder=None):
        return ProjectFolderMembership.objects.create(
            folder=folder or self.root, user=self.user, role=role,
        )

    # 1. Solo grant modulare: cartella visibile nella lista
    def test_grant_only_user_sees_folder_in_list(self):
        self._grant_user(self.root, 'read_published')
        self.client.login(username='sff_user', password='pw')
        resp = self.client.get(reverse('folder_list'))
        codes = [f.code for f in resp.context['folders']]
        self.assertIn('SFF-ROOT', codes)

    # 2. Solo membership legacy: cartella ancora visibile
    def test_legacy_membership_user_sees_folder_in_list(self):
        self._membership('reader')
        self.client.login(username='sff_user', password='pw')
        resp = self.client.get(reverse('folder_list'))
        codes = [f.code for f in resp.context['folders']]
        self.assertIn('SFF-ROOT', codes)

    # 3. Deny modulare nasconde la cartella nonostante membership
    def test_deny_grant_hides_folder_despite_membership(self):
        self._membership('reader')
        self._grant_user(self.root, 'read_published', effect='deny')
        self.client.login(username='sff_user', password='pw')
        resp = self.client.get(reverse('folder_list'))
        codes = [f.code for f in resp.context['folders']]
        self.assertNotIn('SFF-ROOT', codes)

    # 4. Allow ereditato: sottocartella raggiungibile mostra root come nav-only
    def test_inherited_allow_shows_root_as_navigation_only(self):
        # Grant su child (non su root) → root è navigation-only
        self._grant_user(self.child, 'read_published')
        self.client.login(username='sff_user', password='pw')
        resp = self.client.get(reverse('folder_list'))
        codes = [f.code for f in resp.context['folders']]
        self.assertIn('SFF-ROOT', codes)  # root appare (navigation-only)

    # 5. Deny child: solo quel ramo è nascosto, non la root
    def test_deny_child_does_not_hide_parent(self):
        self._grant_user(self.root, 'read_published', effect='allow', inherit=True)
        self._grant_user(self.child, 'read_published', effect='deny')
        self.client.login(username='sff_user', password='pw')
        resp = self.client.get(reverse('folder_list'))
        codes = [f.code for f in resp.context['folders']]
        self.assertIn('SFF-ROOT', codes)  # root ancora visibile (leggibile)

    # 6. Folder navigation-only: accesso consentito, context flag corretto
    def test_navigation_only_folder_accessible_with_flag(self):
        # Solo child ha read_published → root è navigation-only
        self._grant_user(self.child, 'read_published')
        self.client.login(username='sff_user', password='pw')
        resp = self.client.get(reverse('folder_detail', args=[self.root.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.context['is_navigation_only'])

    # 7. Navigation-only non mostra documenti
    def test_navigation_only_no_documents_shown(self):
        from documents.models import Document, DocumentVersion
        self._grant_user(self.child, 'read_published')
        # Crea un documento approvato nella root
        doc = Document.objects.create(
            code='SFF-NAV-DOC', title='Nav doc',
            category=Document.Category.QUALITY,
            project_folder=self.root,
            owner=self.owner, created_by=self.owner,
        )
        ver = DocumentVersion.objects.create(
            document=doc, revision_label='00', revision_number=0,
            status=DocumentVersion.Status.APPROVED, is_current=True,
            created_by=self.owner,
        )
        doc.current_version = ver
        doc.save(update_fields=['current_version'])

        self.client.login(username='sff_user', password='pw')
        resp = self.client.get(reverse('folder_detail', args=[self.root.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.context['is_navigation_only'])
        # documents è lista vuota per navigation-only
        self.assertEqual(list(resp.context['documents']), [])

    # 8. Navigation-only non mostra progetti
    def test_navigation_only_no_projects_shown(self):
        self._grant_user(self.child, 'read_published')
        Project.objects.create(
            code='SFF-NAV-PRJ', name='Nav Project',
            project_type=Project.ProjectType.INTERNAL,
            root_folder=None, manager=self.owner, created_by=self.owner,
        )
        self.client.login(username='sff_user', password='pw')
        resp = self.client.get(reverse('folder_detail', args=[self.root.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.context['is_navigation_only'])
        self.assertEqual(list(resp.context['folder_projects']), [])

    # 9. Navigation-only non mostra pulsante creazione
    def test_navigation_only_no_create_action(self):
        self._grant_user(self.child, 'read_published')
        self.client.login(username='sff_user', password='pw')
        resp = self.client.get(reverse('folder_detail', args=[self.root.pk]))
        self.assertFalse(resp.context['can_create'])
        self.assertFalse(resp.context['can_manage'])

    # 10. Cartella non autorizzata → 403
    def test_unauthorized_folder_returns_403(self):
        # Nessun grant, nessuna membership, nessun discendente leggibile
        self.client.login(username='sff_user', password='pw')
        resp = self.client.get(reverse('folder_detail', args=[self.root.pk]))
        self.assertEqual(resp.status_code, 403)

    # 11. Cartella leggibile → comportamento normale (is_navigation_only=False)
    def test_readable_folder_normal_behavior(self):
        self._membership('reader')
        self.client.login(username='sff_user', password='pw')
        resp = self.client.get(reverse('folder_detail', args=[self.root.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.context['is_navigation_only'])


# ===========================================================================
# Step F — Scrittura: cartelle scrivibili
# ===========================================================================

class StepFWriteIntegrationTests(TestCase):
    """Verifica che get_writable_folder_ids e user_has_any_folder_write_access usino il resolver."""

    def setUp(self):
        from projects.services import set_folder_path
        self.owner = User.objects.create_user('sfw_owner', password='pw')
        self.user = User.objects.create_user('sfw_user', password='pw')
        self.folder = ProjectFolder.objects.create(
            code='SFW-FOLD', name='Write Folder',
            folder_kind=ProjectFolder.FolderKind.GENERIC,
            status=ProjectFolder.Status.ACTIVE, owner=self.owner,
        )
        set_folder_path(self.folder)

    def _grant_user(self, perm, effect='allow', inherit=False):
        return FolderPermissionGrant.objects.create(
            folder=self.folder, user=self.user,
            permission_code=perm, effect=effect,
            inherit_to_children=inherit,
        )

    # 1. Grant create_draft → cartella scrivibile
    def test_create_draft_grant_makes_folder_writable(self):
        self._grant_user('create_draft')
        from projects.permissions import get_writable_folder_ids
        self.assertIn(self.folder.pk, get_writable_folder_ids(self.user))

    # 2. Membership author legacy → cartella ancora scrivibile
    def test_author_legacy_membership_keeps_write_access(self):
        ProjectFolderMembership.objects.create(
            folder=self.folder, user=self.user, role='author'
        )
        from projects.permissions import get_writable_folder_ids
        self.assertIn(self.folder.pk, get_writable_folder_ids(self.user))

    # 3. Deny create_draft blocca membership author
    def test_deny_create_draft_blocks_author_legacy(self):
        ProjectFolderMembership.objects.create(
            folder=self.folder, user=self.user, role='author'
        )
        self._grant_user('create_draft', effect='deny')
        from projects.permissions import get_writable_folder_ids
        self.assertNotIn(self.folder.pk, get_writable_folder_ids(self.user))

    # 4. Navigation-only non è scrivibile
    def test_navigation_only_not_writable(self):
        # Nessun grant su folder → non è scrivibile
        from projects.permissions import get_writable_folder_ids
        self.assertNotIn(self.folder.pk, get_writable_folder_ids(self.user))

    # 5. user_has_any_folder_write_access rileva grant modulare
    def test_user_has_write_access_detects_modular_grant(self):
        self._grant_user('create_draft')
        from projects.permissions import user_has_any_folder_write_access
        self.assertTrue(user_has_any_folder_write_access(self.user))


# ===========================================================================
# Step F — Progetti: visibilità
# ===========================================================================

class StepFProjectIntegrationTests(TestCase):
    """Verifica che project_list e project_detail usino view_projects con fallback legacy."""

    def setUp(self):
        from django.contrib.auth.models import Group as DjangoGroup
        from projects.services import set_folder_path

        self.owner = User.objects.create_user('sfp_owner', password='pw')
        self.user = User.objects.create_user('sfp_user', password='pw')
        self.superuser = User.objects.create_user('sfp_super', password='pw', is_superuser=True)
        self.staff = User.objects.create_user('sfp_staff', password='pw', is_staff=True)

        self.folder = ProjectFolder.objects.create(
            code='SFP-FOLD', name='Project Folder',
            folder_kind=ProjectFolder.FolderKind.GENERIC,
            status=ProjectFolder.Status.ACTIVE, owner=self.owner,
        )
        set_folder_path(self.folder)

        self.project = Project.objects.create(
            code='SFP-PRJ', name='Test Project',
            project_type=Project.ProjectType.INTERNAL,
            root_folder=self.folder, manager=self.owner, created_by=self.owner,
        )

    def _grant_user(self, perm, effect='allow'):
        return FolderPermissionGrant.objects.create(
            folder=self.folder, user=self.user,
            permission_code=perm, effect=effect,
            inherit_to_children=False,
        )

    # 1. view_projects modulare mostra progetto in lista
    def test_view_projects_grant_shows_project_in_list(self):
        self._grant_user('view_projects')
        self.client.login(username='sfp_user', password='pw')
        resp = self.client.get(reverse('project_list'))
        codes = [p.code for p in resp.context['projects']]
        self.assertIn('SFP-PRJ', codes)

    # 2. Assenza view_projects nasconde progetto anche se read_published presente
    def test_read_published_without_view_projects_hides_project(self):
        # Utente con read_published ma senza view_projects e senza membership
        self._grant_user('read_published')
        self.client.login(username='sfp_user', password='pw')
        resp = self.client.get(reverse('project_list'))
        codes = [p.code for p in resp.context['projects']]
        self.assertNotIn('SFP-PRJ', codes)

    # 3. Deny view_projects blocca fallback legacy
    def test_deny_view_projects_blocks_legacy_fallback(self):
        ProjectFolderMembership.objects.create(
            folder=self.folder, user=self.user, role='reader'
        )
        self._grant_user('view_projects', effect='deny')
        self.client.login(username='sfp_user', password='pw')
        resp = self.client.get(reverse('project_list'))
        codes = [p.code for p in resp.context['projects']]
        self.assertNotIn('SFP-PRJ', codes)

    # 4. Fallback legacy conserva il comportamento precedente (reader vede progetto)
    def test_legacy_fallback_reader_sees_project(self):
        ProjectFolderMembership.objects.create(
            folder=self.folder, user=self.user, role='reader'
        )
        self.client.login(username='sfp_user', password='pw')
        resp = self.client.get(reverse('project_list'))
        codes = [p.code for p in resp.context['projects']]
        self.assertIn('SFP-PRJ', codes)

    # 5. project_detail nega accesso senza view_projects (no membership, solo read_published)
    def test_project_detail_403_without_view_projects(self):
        # Solo read_published, nessuna membership → view_projects=False → 403
        self._grant_user('read_published')
        self.client.login(username='sfp_user', password='pw')
        resp = self.client.get(reverse('project_detail', args=[self.project.pk]))
        self.assertEqual(resp.status_code, 403)

    # 6. Navigation-only non espone progetto in folder_detail
    def test_navigation_only_no_project_in_folder_detail(self):
        from projects.services import set_folder_path
        child = ProjectFolder.objects.create(
            code='SFP-CH', name='Child',
            folder_kind=ProjectFolder.FolderKind.GENERIC,
            status=ProjectFolder.Status.ACTIVE, owner=self.owner,
            parent=self.folder,
        )
        set_folder_path(child)
        # Grant solo sul child → parent (self.folder) è navigation-only
        FolderPermissionGrant.objects.create(
            folder=child, user=self.user,
            permission_code='read_published', effect='allow',
            inherit_to_children=False,
        )
        self.client.login(username='sfp_user', password='pw')
        resp = self.client.get(reverse('folder_detail', args=[self.folder.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.context['is_navigation_only'])
        self.assertEqual(list(resp.context['folder_projects']), [])

    # 7. Superuser vede il progetto
    def test_superuser_sees_project(self):
        self.client.login(username='sfp_super', password='pw')
        resp = self.client.get(reverse('project_detail', args=[self.project.pk]))
        self.assertEqual(resp.status_code, 200)

    # 8. Staff senza grant non vede il progetto
    def test_staff_without_grant_cannot_see_project(self):
        self.client.login(username='sfp_staff', password='pw')
        resp = self.client.get(reverse('project_detail', args=[self.project.pk]))
        self.assertEqual(resp.status_code, 403)


# ===========================================================================
# STEP PROJECT-ROOT — Test nuovi
# ===========================================================================

from django.test import override_settings  # noqa: E402


# ---------------------------------------------------------------------------
# Modello e FolderKind.PROJECT
# ---------------------------------------------------------------------------

class ProjectRootFolderModelTests(TestCase):
    """Test modello root_folder e FolderKind.PROJECT."""

    def setUp(self):
        self.user = User.objects.create_user('prm_user', password='pw')
        self.parent = ProjectFolder.objects.create(
            code='PRM-PARENT', name='Parent', owner=self.user,
            folder_kind=ProjectFolder.FolderKind.GENERIC,
            status=ProjectFolder.Status.ACTIVE,
        )

    # 1. FolderKind.PROJECT disponibile
    def test_folder_kind_project_available(self):
        self.assertIn('project', [k.value for k in ProjectFolder.FolderKind])

    # 2. Root folder PROJECT accettata
    def test_project_accepts_project_kind_root_folder(self):
        rf = ProjectFolder.objects.create(
            code='PRM-ROOT', name='Root', owner=self.user,
            folder_kind=ProjectFolder.FolderKind.PROJECT,
            parent=self.parent,
        )
        prj = Project.objects.create(
            code='PRM-PRJ', name='Test', root_folder=rf,
            created_by=self.user,
        )
        self.assertEqual(prj.root_folder, rf)

    # 3. Cartella ordinaria come root_folder: clean() deve rifiutare
    def test_clean_rejects_generic_folder_as_root_folder(self):
        from django.core.exceptions import ValidationError
        generic = ProjectFolder.objects.create(
            code='PRM-GEN', name='Generica', owner=self.user,
            folder_kind=ProjectFolder.FolderKind.GENERIC,
        )
        prj = Project(code='PRM-BAD', name='Bad', root_folder=generic, created_by=self.user)
        with self.assertRaises(ValidationError):
            prj.full_clean()

    # 4. Stessa root folder non assegnabile a due progetti (OneToOneField)
    def test_root_folder_cannot_be_shared(self):
        from django.db import IntegrityError
        rf = ProjectFolder.objects.create(
            code='PRM-SHARED', name='Shared', owner=self.user,
            folder_kind=ProjectFolder.FolderKind.PROJECT,
        )
        Project.objects.create(code='PRM-P1', name='P1', root_folder=rf, created_by=self.user)
        with self.assertRaises(IntegrityError):
            Project.objects.create(code='PRM-P2', name='P2', root_folder=rf, created_by=self.user)

    # 5. Progetto legacy senza root_folder temporaneamente consentito
    def test_project_without_root_folder_allowed(self):
        prj = Project.objects.create(code='PRM-LEGACY', name='Legacy',
                                     root_folder=None, created_by=self.user)
        self.assertIsNone(prj.root_folder)

    # 6. Cancellazione root folder collegata protetta (PROTECT)
    def test_delete_root_folder_protected(self):
        from django.db.models.deletion import ProtectedError
        rf = ProjectFolder.objects.create(
            code='PRM-PROT', name='Prot', owner=self.user,
            folder_kind=ProjectFolder.FolderKind.PROJECT,
        )
        Project.objects.create(code='PRM-PROT-PRJ', name='P', root_folder=rf, created_by=self.user)
        with self.assertRaises(ProtectedError):
            rf.delete()


# ---------------------------------------------------------------------------
# Service atomico create_project_with_root_folder
# ---------------------------------------------------------------------------

class ProjectRootFolderServiceTests(TestCase):
    """Test service atomico STEP PROJECT-ROOT."""

    def setUp(self):
        from django.contrib.auth.models import Group
        from documents.permissions import GROUP_MANAGERS
        self.user = User.objects.create_user('prs_user', password='pw')
        Group.objects.get_or_create(name=GROUP_MANAGERS)[0].user_set.add(self.user)
        self.parent = ProjectFolder.objects.create(
            code='PRS-PARENT', name='Parent', owner=self.user,
            folder_kind=ProjectFolder.FolderKind.GENERIC,
            status=ProjectFolder.Status.ACTIVE,
        )
        from projects.services import set_folder_path
        set_folder_path(self.parent)

    def _create(self, code='PRS-PRJ-001', **kwargs):
        from projects.services import create_project_with_root_folder
        return create_project_with_root_folder(
            parent_folder=self.parent,
            code=code,
            name=kwargs.get('name', 'Test Project'),
            description=kwargs.get('description', ''),
            manager=kwargs.get('manager', self.user),
            created_by=self.user,
        )

    # 1. Service crea progetto + root folder
    def test_service_creates_project_and_root_folder(self):
        prj = self._create()
        self.assertIsNotNone(prj.root_folder)
        self.assertEqual(prj.code, 'PRS-PRJ-001')

    # 2. Parent corretto
    def test_root_folder_parent_correct(self):
        prj = self._create()
        self.assertEqual(prj.root_folder.parent, self.parent)

    # 3. Kind PROJECT
    def test_root_folder_kind_is_project(self):
        prj = self._create()
        self.assertEqual(prj.root_folder.folder_kind, ProjectFolder.FolderKind.PROJECT)

    # 4. Path valorizzato
    def test_root_folder_path_set(self):
        prj = self._create()
        self.assertTrue(prj.root_folder.path, "Il path deve essere valorizzato")

    # 5. Relazione OneToOne: root_project → project
    def test_root_folder_one_to_one_reverse(self):
        prj = self._create()
        self.assertEqual(prj.root_folder.root_project, prj)

    # 6. Codice coerente
    def test_root_folder_code_matches_project(self):
        prj = self._create(code='PRS-COERENTE')
        self.assertEqual(prj.root_folder.code, prj.code)

    # 7. Transazione atomica: codice duplicato → nessuna root folder orfana
    def test_duplicate_code_leaves_no_orphan_folder(self):
        from django.core.exceptions import ValidationError
        self._create(code='PRS-DUP')
        before = ProjectFolder.objects.count()
        with self.assertRaises(ValidationError):
            self._create(code='PRS-DUP')
        after = ProjectFolder.objects.count()
        self.assertEqual(before, after, "Nessuna cartella orfana deve essere stata creata")

    # 8. Codice duplicato gestito con errore leggibile
    def test_duplicate_project_code_raises_validation_error(self):
        from django.core.exceptions import ValidationError
        self._create(code='PRS-DUPCODE')
        with self.assertRaises(ValidationError) as ctx:
            self._create(code='PRS-DUPCODE')
        self.assertIn('PRS-DUPCODE', str(ctx.exception))

    # 9. Parent folder obbligatorio
    def test_no_parent_raises(self):
        from django.core.exceptions import ValidationError
        from projects.services import create_project_with_root_folder
        with self.assertRaises(ValidationError):
            create_project_with_root_folder(
                parent_folder=None, code='PRS-NOPA', name='No Parent',
            )


# ---------------------------------------------------------------------------
# Explorer unificato
# ---------------------------------------------------------------------------

class ProjectExplorerTests(TestCase):
    """Test explorer unificato cartelle + progetti (STEP PROJECT-ROOT)."""

    def setUp(self):
        from django.contrib.auth.models import Group
        from documents.permissions import GROUP_MANAGERS
        self.manager = User.objects.create_user('pex_mgr', password='pw')
        Group.objects.get_or_create(name=GROUP_MANAGERS)[0].user_set.add(self.manager)

        from projects.services import create_project_with_root_folder, set_folder_path
        self.parent = ProjectFolder.objects.create(
            code='PEX-PARENT', name='Parent Folder', owner=self.manager,
            folder_kind=ProjectFolder.FolderKind.GENERIC,
            status=ProjectFolder.Status.ACTIVE,
        )
        set_folder_path(self.parent)

        # Sottocartella ordinaria
        self.subfolder = ProjectFolder.objects.create(
            code='PEX-SUB', name='Sottocartella', owner=self.manager,
            folder_kind=ProjectFolder.FolderKind.GENERIC,
            status=ProjectFolder.Status.ACTIVE,
            parent=self.parent,
        )
        set_folder_path(self.subfolder)

        # Progetto con root folder
        self.project = create_project_with_root_folder(
            parent_folder=self.parent,
            code='PEX-PRJ', name='Progetto Explorer',
            created_by=self.manager,
        )

    # 1. Progetto appare nella cartella padre come tile progetto
    def test_project_appears_in_parent_folder(self):
        self.client.force_login(self.manager)
        r = self.client.get(reverse('folder_detail', args=[self.parent.pk]))
        self.assertEqual(r.status_code, 200)
        items = r.context['explorer_items']
        kinds = [k for k, _ in items]
        self.assertIn('project', kinds)

    # 2. Tile progetto distinguibile (kind='project')
    def test_tile_kind_project(self):
        self.client.force_login(self.manager)
        r = self.client.get(reverse('folder_detail', args=[self.parent.pk]))
        items = r.context['explorer_items']
        project_items = [item for k, item in items if k == 'project']
        self.assertTrue(any(p.code == 'PEX-PRJ' for p in project_items))

    # 3. Tile cartella ordinaria ancora presente
    def test_ordinary_subfolder_still_visible(self):
        self.client.force_login(self.manager)
        r = self.client.get(reverse('folder_detail', args=[self.parent.pk]))
        items = r.context['explorer_items']
        folder_items = [item for k, item in items if k == 'folder']
        self.assertTrue(any(f.code == 'PEX-SUB' for f in folder_items))

    # 4. Root folder NON duplicata tra tile cartella e tile progetto
    def test_root_folder_not_in_ordinary_subfolders(self):
        self.client.force_login(self.manager)
        r = self.client.get(reverse('folder_detail', args=[self.parent.pk]))
        items = r.context['explorer_items']
        folder_items = [item for k, item in items if k == 'folder']
        # La root folder del progetto (kind=PROJECT) non deve apparire come cartella ordinaria
        root_code = self.project.root_folder.code
        self.assertFalse(any(f.code == root_code for f in folder_items))

    # 5. Cartella ordinaria punta a folder_detail
    def test_ordinary_folder_link_is_folder_detail(self):
        self.client.force_login(self.manager)
        r = self.client.get(reverse('folder_detail', args=[self.parent.pk]))
        self.assertContains(r, f'/folders/{self.subfolder.pk}/')

    # 6. Tile progetto punta a project_detail (via template)
    def test_project_tile_link_is_project_detail(self):
        self.client.force_login(self.manager)
        r = self.client.get(reverse('folder_detail', args=[self.parent.pk]))
        self.assertContains(r, f'/projects/{self.project.pk}/')

    # 7. Accesso diretto a root folder → redirect a project_detail
    def test_direct_access_to_root_folder_redirects(self):
        self.client.force_login(self.manager)
        r = self.client.get(reverse('folder_detail', args=[self.project.root_folder.pk]))
        self.assertRedirects(
            r,
            reverse('project_detail', args=[self.project.pk]),
            fetch_redirect_response=False,
        )

    # 8. PROJECT orfana non genera 500
    def test_orphan_project_folder_no_500(self):
        orphan = ProjectFolder.objects.create(
            code='PEX-ORPHAN', name='Orphan', owner=self.manager,
            folder_kind=ProjectFolder.FolderKind.PROJECT,
            parent=self.parent,
        )
        from projects.services import set_folder_path
        set_folder_path(orphan)
        self.client.force_login(self.manager)
        r = self.client.get(reverse('folder_detail', args=[self.parent.pk]))
        self.assertEqual(r.status_code, 200)
        items = r.context['explorer_items']
        orphan_items = [item for k, item in items if k == 'orphan_project_folder']
        self.assertTrue(any(f.code == 'PEX-ORPHAN' for f in orphan_items))

    # 9. Breadcrumb corretto nel project_detail
    def test_project_detail_breadcrumb_shows_parent(self):
        self.client.force_login(self.manager)
        r = self.client.get(reverse('project_detail', args=[self.project.pk]))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'PEX-PARENT')

    # 10. Project detail mostra root folder in scheda
    def test_project_detail_shows_root_folder_parent(self):
        self.client.force_login(self.manager)
        r = self.client.get(reverse('project_detail', args=[self.project.pk]))
        self.assertContains(r, 'Cartella padre')


# ---------------------------------------------------------------------------
# Demo company
# ---------------------------------------------------------------------------

@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class ProjectDemoTests(TestCase):
    """Test demo_company crea progetto PRJ-DEMO-001 con root folder."""

    def _call(self, *args):
        from io import StringIO
        from django.core.management import call_command
        out = StringIO()
        call_command('demo_company', *args, stdout=out)
        return out.getvalue()

    # 1. demo_company crea PRJ-DEMO-001
    def test_demo_company_creates_project(self):
        self._call('--reset', '--no-email')
        self.assertTrue(Project.objects.filter(code='PRJ-DEMO-001').exists())

    # 2. Root folder demo kind PROJECT
    def test_project_root_folder_kind(self):
        self._call('--reset', '--no-email')
        prj = Project.objects.get(code='PRJ-DEMO-001')
        self.assertIsNotNone(prj.root_folder)
        self.assertEqual(prj.root_folder.folder_kind, ProjectFolder.FolderKind.PROJECT)

    # 3. Sottocartelle demo create
    def test_project_subfolders_created(self):
        self._call('--reset', '--no-email')
        prj = Project.objects.get(code='PRJ-DEMO-001')
        subs = prj.root_folder.subfolders.count()
        self.assertGreaterEqual(subs, 2, "Almeno 2 sottocartelle (Specifiche, Collaudi)")

    # 4. Documento demo progetto creato
    def test_project_demo_document_created(self):
        from documents.models import Document
        self._call('--reset', '--no-email')
        self.assertTrue(Document.objects.filter(code='PRJ-DEMO-001-SPEC-001').exists())

    # 5. Secondo run idempotente
    def test_demo_company_idempotent(self):
        self._call('--reset', '--no-email')
        self._call('--no-email')
        self.assertEqual(Project.objects.filter(code='PRJ-DEMO-001').count(), 1)

    # 6. Reset funziona
    def test_demo_company_reset_removes_project(self):
        self._call('--reset', '--no-email')
        self.assertTrue(Project.objects.filter(code='PRJ-DEMO-001').exists())
        self._call('--reset', '--no-email')
        # Dopo il secondo reset+ricreazione esiste ancora 1 progetto
        self.assertEqual(Project.objects.filter(code='PRJ-DEMO-001').count(), 1)

# ===========================================================================
# STEP PROJECT-UX — Nuove classi di test
# ===========================================================================

# ---------------------------------------------------------------------------
# Rimozione Project.status
# ---------------------------------------------------------------------------

class ProjectStatusRemovalTests(TestCase):
    """Project non espone piu il campo status."""

    def setUp(self):
        from django.contrib.auth.models import Group
        self.manager = User.objects.create_user('psr_mgr', password='pw')
        Group.objects.get_or_create(name='Document Managers')[0].user_set.add(self.manager)
        self.owner = User.objects.create_user('psr_owner', password='pw')
        self.folder = make_folder(code='PSR-FOLD', owner=self.owner)
        self.project = Project.objects.create(
            code='PSR-PRJ-001', name='Status Test',
            project_type=Project.ProjectType.INTERNAL,
            root_folder=self.folder, manager=self.manager, created_by=self.manager,
        )

    def test_project_model_has_no_status_field(self):
        from django.core.exceptions import FieldDoesNotExist
        with self.assertRaises(FieldDoesNotExist):
            Project._meta.get_field('status')

    def test_project_form_has_no_status_field(self):
        from projects.forms import ProjectUpdateForm
        form = ProjectUpdateForm(instance=self.project)
        self.assertNotIn('status', form.fields)

    def test_project_list_no_status_filter(self):
        self.client.login(username='psr_mgr', password='pw')
        r = self.client.get(reverse('project_list'))
        self.assertEqual(r.status_code, 200)
        self.assertNotIn('status_choices', r.context)

    def test_project_detail_no_status_badge(self):
        self.client.login(username='psr_mgr', password='pw')
        r = self.client.get(reverse('project_detail', args=[self.project.pk]))
        self.assertEqual(r.status_code, 200)
        self.assertNotContains(r, 'get_status_display')

    def test_demo_creates_project_without_status(self):
        from django.core.management import call_command
        from io import StringIO
        call_command('demo_workflow', '--no-email', stdout=StringIO())
        prj = Project.objects.get(code='PRJ-DEMO-001')
        self.assertFalse(hasattr(prj, 'status'))


# ---------------------------------------------------------------------------
# Edit progetto
# ---------------------------------------------------------------------------

class ProjectEditTests(TestCase):
    """View project_edit: modifica metadati, sicurezza, atomicita."""

    def setUp(self):
        from django.contrib.auth.models import Group
        self.manager = User.objects.create_user('pe_mgr', password='pw')
        self.normal = User.objects.create_user('pe_normal', password='pw')
        Group.objects.get_or_create(name='Document Managers')[0].user_set.add(self.manager)

        self.parent = make_folder(code='PE-PARENT', owner=self.manager)
        from projects.services import create_project_with_root_folder
        self.project = create_project_with_root_folder(
            parent_folder=self.parent,
            code='PE-PRJ-001',
            name='Progetto Edit Test',
            description='Descrizione originale',
            project_type='internal',
            manager=self.manager,
            created_by=self.manager,
        )
        self.root_folder_pk = self.project.root_folder.pk
        self.root_folder_code = self.project.root_folder.code

    def _edit_url(self):
        return reverse('project_edit', args=[self.project.pk])

    def test_authorized_user_gets_edit_form(self):
        self.client.login(username='pe_mgr', password='pw')
        r = self.client.get(self._edit_url())
        self.assertEqual(r.status_code, 200)
        self.assertIn('form', r.context)

    def test_unauthorized_user_gets_403(self):
        self.client.login(username='pe_normal', password='pw')
        r = self.client.get(self._edit_url())
        self.assertEqual(r.status_code, 403)

    def test_unauthorized_post_gets_403(self):
        self.client.login(username='pe_normal', password='pw')
        r = self.client.post(self._edit_url(), {'name': 'Hacked', 'description': ''})
        self.assertEqual(r.status_code, 403)

    def test_update_name(self):
        self.client.login(username='pe_mgr', password='pw')
        r = self.client.post(self._edit_url(), {'name': 'Nuovo Nome', 'description': ''})
        self.assertRedirects(r, reverse('project_detail', args=[self.project.pk]))
        self.project.refresh_from_db()
        self.assertEqual(self.project.name, 'Nuovo Nome')

    def test_update_description(self):
        self.client.login(username='pe_mgr', password='pw')
        self.client.post(self._edit_url(), {'name': 'Progetto Edit Test', 'description': 'Nuova desc'})
        self.project.refresh_from_db()
        self.assertEqual(self.project.description, 'Nuova desc')

    def test_update_manager(self):
        new_mgr = User.objects.create_user('pe_new_mgr', password='pw')
        self.client.login(username='pe_mgr', password='pw')
        self.client.post(self._edit_url(), {'name': 'Progetto Edit Test', 'description': '', 'manager': new_mgr.pk})
        self.project.refresh_from_db()
        self.assertEqual(self.project.manager, new_mgr)

    def test_code_unchanged_after_edit(self):
        self.client.login(username='pe_mgr', password='pw')
        self.client.post(self._edit_url(), {'name': 'Nuovo Nome', 'description': ''})
        self.project.refresh_from_db()
        self.assertEqual(self.project.code, 'PE-PRJ-001')

    def test_root_folder_code_unchanged(self):
        self.client.login(username='pe_mgr', password='pw')
        self.client.post(self._edit_url(), {'name': 'Nuovo Nome', 'description': ''})
        self.project.refresh_from_db()
        self.assertEqual(self.project.root_folder.code, self.root_folder_code)

    def test_root_folder_name_synced(self):
        self.client.login(username='pe_mgr', password='pw')
        self.client.post(self._edit_url(), {'name': 'Nome Sincronizzato', 'description': ''})
        self.project.root_folder.refresh_from_db()
        self.assertEqual(self.project.root_folder.name, 'Nome Sincronizzato')

    def test_root_folder_pk_unchanged(self):
        self.client.login(username='pe_mgr', password='pw')
        self.client.post(self._edit_url(), {'name': 'Nuovo Nome', 'description': ''})
        self.project.refresh_from_db()
        self.assertEqual(self.project.root_folder.pk, self.root_folder_pk)

    def test_invalid_post_shows_errors(self):
        self.client.login(username='pe_mgr', password='pw')
        r = self.client.post(self._edit_url(), {'name': '', 'description': ''})
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.context['form'].is_valid())

    def test_edit_button_visible_for_manager(self):
        self.client.login(username='pe_mgr', password='pw')
        r = self.client.get(reverse('project_detail', args=[self.project.pk]))
        self.assertContains(r, 'Modifica progetto')


# ---------------------------------------------------------------------------
# Ricerca contestuale folder_detail
# ---------------------------------------------------------------------------

class FolderDetailSearchTests(TestCase):
    """Barra di ricerca contestuale in folder_detail."""

    def setUp(self):
        from django.contrib.auth.models import Group
        from projects.services import create_project_with_root_folder, set_folder_path
        from documents.models import Document, DocumentVersion

        self.manager = User.objects.create_user('fds_mgr', password='pw')
        Group.objects.get_or_create(name='Document Managers')[0].user_set.add(self.manager)

        self.root = make_folder(code='FDS-ROOT', owner=self.manager)
        set_folder_path(self.root)

        self.subfolder = ProjectFolder.objects.create(
            code='FDS-SUB', name='Sottocartella Ricerca',
            folder_kind=ProjectFolder.FolderKind.GENERIC,
            parent=self.root, owner=self.manager,
            status=ProjectFolder.Status.ACTIVE,
        )
        set_folder_path(self.subfolder)

        self.deep_sub = ProjectFolder.objects.create(
            code='FDS-DEEP', name='Sottocartella Profonda',
            folder_kind=ProjectFolder.FolderKind.GENERIC,
            parent=self.subfolder, owner=self.manager,
            status=ProjectFolder.Status.ACTIVE,
        )
        set_folder_path(self.deep_sub)

        self.project = create_project_with_root_folder(
            parent_folder=self.root,
            code='FDS-PRJ-001',
            name='Progetto Figlio',
            project_type='internal',
            created_by=self.manager,
        )

        self.doc_root = Document.objects.create(
            code='FDS-DOC-ROOT', title='Documento root',
            category=Document.Category.QUALITY,
            project_folder=self.root,
            owner=self.manager, created_by=self.manager,
        )
        ver = DocumentVersion.objects.create(
            document=self.doc_root, revision_label='00', revision_number=0,
            status=DocumentVersion.Status.APPROVED, is_current=True,
            created_by=self.manager,
        )
        self.doc_root.current_version = ver
        self.doc_root.save(update_fields=['current_version'])

        self.doc_deep = Document.objects.create(
            code='FDS-DOC-DEEP', title='Documento profondo',
            category=Document.Category.QUALITY,
            project_folder=self.deep_sub,
            owner=self.manager, created_by=self.manager,
        )
        ver2 = DocumentVersion.objects.create(
            document=self.doc_deep, revision_label='00', revision_number=0,
            status=DocumentVersion.Status.APPROVED, is_current=True,
            created_by=self.manager,
        )
        self.doc_deep.current_version = ver2
        self.doc_deep.save(update_fields=['current_version'])

    def _get(self, **params):
        self.client.login(username='fds_mgr', password='pw')
        return self.client.get(reverse('folder_detail', args=[self.root.pk]), params)

    def test_search_bar_present(self):
        r = self._get()
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'name="q"')

    def test_search_finds_immediate_subfolder(self):
        r = self._get(q='Sottocartella')
        kinds = [k for k, _ in r.context['search_results']]
        self.assertIn('folder', kinds)

    def test_search_finds_child_project(self):
        r = self._get(q='Progetto Figlio')
        kinds = [k for k, _ in r.context['search_results']]
        self.assertIn('project', kinds)

    def test_search_finds_immediate_document(self):
        r = self._get(q='FDS-DOC-ROOT')
        kinds = [k for k, _ in r.context['search_results']]
        self.assertIn('document', kinds)

    def test_non_recursive_does_not_find_deep_document(self):
        r = self._get(q='FDS-DOC-DEEP')
        codes = [item.code for _, item in r.context['search_results']]
        self.assertNotIn('FDS-DOC-DEEP', codes)

    def test_recursive_finds_deep_document(self):
        r = self._get(q='FDS-DOC-DEEP', recursive='1')
        codes = [item.code for _, item in r.context['search_results']]
        self.assertIn('FDS-DOC-DEEP', codes)

    def test_results_distinguish_types(self):
        r = self._get(q='FDS', recursive='1')
        kinds = set(k for k, _ in r.context['search_results'])
        self.assertGreater(len(kinds), 1)

    def test_empty_q_shows_explorer(self):
        r = self._get()
        self.assertIsNone(r.context['search_results'])
        self.assertIn('explorer_items', r.context)

    def test_search_excludes_other_users_draft(self):
        from documents.models import Document, DocumentVersion
        other = User.objects.create_user('fds_other', password='pw')
        doc_draft = Document.objects.create(
            code='FDS-PRIVATE-DRAFT', title='Bozza privata',
            category=Document.Category.QUALITY,
            project_folder=self.root,
            owner=other, created_by=other,
        )
        DocumentVersion.objects.create(
            document=doc_draft, revision_label='00', revision_number=0,
            status=DocumentVersion.Status.DRAFT, is_current=False,
            created_by=other,
        )
        r = self._get(q='FDS-PRIVATE-DRAFT')
        codes = [item.code for _, item in (r.context['search_results'] or [])]
        self.assertNotIn('FDS-PRIVATE-DRAFT', codes)


# ---------------------------------------------------------------------------
# Ricerca documenti project_detail
# ---------------------------------------------------------------------------

class ProjectDetailSearchTests(TestCase):
    """Barra di ricerca documenti in project_detail."""

    def setUp(self):
        from django.contrib.auth.models import Group
        from projects.services import create_project_with_root_folder, set_folder_path
        from documents.models import Document, DocumentVersion

        self.manager = User.objects.create_user('pds_mgr', password='pw')
        Group.objects.get_or_create(name='Document Managers')[0].user_set.add(self.manager)
        self.other_user = User.objects.create_user('pds_other', password='pw')

        self.parent = make_folder(code='PDS-PARENT', owner=self.manager)
        set_folder_path(self.parent)

        self.project = create_project_with_root_folder(
            parent_folder=self.parent,
            code='PDS-PRJ-001',
            name='Progetto Detail Search',
            project_type='internal',
            created_by=self.manager,
        )
        root = self.project.root_folder

        self.folder_spec = ProjectFolder.objects.create(
            code='PDS-SPEC', name='Specifiche',
            folder_kind=ProjectFolder.FolderKind.GENERIC,
            parent=root, owner=self.manager,
            status=ProjectFolder.Status.ACTIVE,
        )
        set_folder_path(self.folder_spec)
        self.folder_coll = ProjectFolder.objects.create(
            code='PDS-COLL', name='Collaudi',
            folder_kind=ProjectFolder.FolderKind.GENERIC,
            parent=root, owner=self.manager,
            status=ProjectFolder.Status.ACTIVE,
        )
        set_folder_path(self.folder_coll)

        def _make_doc(code, title, doc_type, folder):
            doc = Document.objects.create(
                code=code, title=title, category=Document.Category.QUALITY,
                document_type=doc_type, project_folder=folder,
                owner=self.manager, created_by=self.manager,
                status=Document.Status.ACTIVE,
            )
            ver = DocumentVersion.objects.create(
                document=doc, revision_label='00', revision_number=0,
                status=DocumentVersion.Status.APPROVED, is_current=True,
                created_by=self.manager,
            )
            doc.current_version = ver
            doc.save(update_fields=['current_version'])
            return doc

        self.doc_root = _make_doc('PDS-DOC-ROOT', 'Doc root', 'Procedura', root)
        self.doc_spec = _make_doc('PDS-DOC-SPEC', 'Doc specifica', 'Specifica', self.folder_spec)
        self.doc_coll = _make_doc('PDS-DOC-COLL', 'Doc collaudo', 'Piano di collaudo', self.folder_coll)

        external_folder = make_folder(code='PDS-EXT', owner=self.manager)
        self.doc_ext = _make_doc('PDS-DOC-EXT', 'Doc esterno', 'Procedura', external_folder)

    def _get(self, **params):
        self.client.login(username='pds_mgr', password='pw')
        return self.client.get(reverse('project_detail', args=[self.project.pk]), params)

    def test_search_bar_present(self):
        r = self._get()
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'name="q"')

    def test_search_finds_root_document(self):
        r = self._get(q='PDS-DOC-ROOT')
        codes = [doc.code for doc in r.context['doc_page_obj']]
        self.assertIn('PDS-DOC-ROOT', codes)

    def test_search_finds_spec_document(self):
        r = self._get(q='PDS-DOC-SPEC')
        codes = [doc.code for doc in r.context['doc_page_obj']]
        self.assertIn('PDS-DOC-SPEC', codes)

    def test_search_finds_coll_document(self):
        r = self._get(q='PDS-DOC-COLL')
        codes = [doc.code for doc in r.context['doc_page_obj']]
        self.assertIn('PDS-DOC-COLL', codes)

    def test_external_document_excluded(self):
        r = self._get(q='PDS-DOC-EXT')
        codes = [doc.code for doc in r.context['doc_page_obj']]
        self.assertNotIn('PDS-DOC-EXT', codes)

    def test_filter_by_document_type(self):
        r = self._get(document_type='Specifica')
        codes = [doc.code for doc in r.context['doc_page_obj']]
        self.assertIn('PDS-DOC-SPEC', codes)
        self.assertNotIn('PDS-DOC-COLL', codes)

    def test_filter_by_folder(self):
        r = self._get(folder=str(self.folder_coll.pk))
        codes = [doc.code for doc in r.context['doc_page_obj']]
        self.assertIn('PDS-DOC-COLL', codes)
        self.assertNotIn('PDS-DOC-SPEC', codes)

    def test_pagination_context(self):
        r = self._get()
        self.assertIsNotNone(r.context['doc_page_obj'])
        self.assertTrue(hasattr(r.context['doc_page_obj'], 'paginator'))

    def test_empty_state_with_filter(self):
        r = self._get(q='CODICE-INESISTENTE-XYZ')
        page = r.context['doc_page_obj']
        self.assertEqual(page.paginator.count, 0)

    def test_other_users_draft_excluded_for_reader(self):
        """Un reader non vede la bozza di un altro utente nel project_detail."""
        from documents.models import Document, DocumentVersion
        # Crea reader con accesso al progetto via membership
        reader = User.objects.create_user('pds_reader', password='pw')
        ProjectFolderMembership.objects.create(
            folder=self.project.root_folder, user=reader,
            role=ProjectFolderMembership.Role.READER,
        )
        doc_draft = Document.objects.create(
            code='PDS-PRIVATE', title='Bozza privata',
            category=Document.Category.QUALITY,
            document_type='Procedura',
            project_folder=self.project.root_folder,
            owner=self.other_user, created_by=self.other_user,
        )
        DocumentVersion.objects.create(
            document=doc_draft, revision_label='00', revision_number=0,
            status=DocumentVersion.Status.DRAFT, is_current=False,
            created_by=self.other_user,
        )
        self.client.login(username='pds_reader', password='pw')
        r = self.client.get(reverse('project_detail', args=[self.project.pk]), {'q': 'PDS-PRIVATE'})
        self.assertEqual(r.status_code, 200)
        codes = [doc.code for doc in r.context['doc_page_obj']]
        self.assertNotIn('PDS-PRIVATE', codes)


# ---------------------------------------------------------------------------
# Demo company — dataset progetto espanso
# ---------------------------------------------------------------------------

@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class DemoProjectDataTests(TestCase):
    """demo_company crea due documenti nelle sottocartelle del progetto."""

    def _call(self, *args):
        from io import StringIO
        from django.core.management import call_command
        out = StringIO()
        call_command('demo_company', *args, stdout=out)
        return out.getvalue()

    def test_project_created(self):
        self._call('--reset', '--no-email')
        self.assertTrue(Project.objects.filter(code='PRJ-DEMO-001').exists())

    def test_two_subfolders_created(self):
        self._call('--reset', '--no-email')
        prj = Project.objects.get(code='PRJ-DEMO-001')
        subs = list(prj.root_folder.subfolders.values_list('code', flat=True))
        self.assertIn('PRJ-DEMO-001-SPEC', subs)
        self.assertIn('PRJ-DEMO-001-COLL', subs)

    def test_spec_document_created_and_approved(self):
        from documents.models import Document, DocumentVersion
        self._call('--reset', '--no-email')
        self.assertTrue(Document.objects.filter(code='PRJ-DEMO-001-SPEC-001').exists())
        doc = Document.objects.get(code='PRJ-DEMO-001-SPEC-001')
        self.assertIsNotNone(doc.current_version)
        self.assertEqual(doc.current_version.status, DocumentVersion.Status.APPROVED)

    def test_test_document_created_and_approved(self):
        from documents.models import Document, DocumentVersion
        self._call('--reset', '--no-email')
        self.assertTrue(Document.objects.filter(code='PRJ-DEMO-001-TEST-001').exists())
        doc = Document.objects.get(code='PRJ-DEMO-001-TEST-001')
        self.assertIsNotNone(doc.current_version)
        self.assertEqual(doc.current_version.status, DocumentVersion.Status.APPROVED)

    def test_idempotent(self):
        from documents.models import Document
        self._call('--reset', '--no-email')
        self._call('--no-email')
        self.assertEqual(Project.objects.filter(code='PRJ-DEMO-001').count(), 1)
        self.assertEqual(Document.objects.filter(code='PRJ-DEMO-001-SPEC-001').count(), 1)
        self.assertEqual(Document.objects.filter(code='PRJ-DEMO-001-TEST-001').count(), 1)
