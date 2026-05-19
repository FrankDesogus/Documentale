from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse

from projects.models import Project, ProjectFolder, ProjectFolderMembership, ProjectRevision, ProjectRevisionItem

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
        # fl_user è staff: vede tutte le cartelle senza membership
        self.user = User.objects.create_user('fl_user', password='pw', is_staff=True)
        self.owner = User.objects.create_user('fl_owner', password='pw')

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
        from django.contrib.auth.models import User as AuthUser
        from documents.models import Document
        doc_owner = AuthUser.objects.create_user('fd_doc_owner', password='pw')
        Document.objects.create(
            code='FD-DOC-001',
            title='Documento in cartella',
            category=Document.Category.QUALITY,
            project_folder=self.root,
            owner=doc_owner,
            created_by=doc_owner,
        )
        self.client.login(username='fd_user', password='pw')
        response = self.client.get(reverse('folder_detail', args=[self.root.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'FD-DOC-001')
        doc_codes = [d.code for d in response.context['documents']]
        self.assertIn('FD-DOC-001', doc_codes)

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

    def test_staff_can_create_folder(self):
        self.client.login(username='fc_staff', password='pw')
        response = self.client.post(reverse('folder_create'), {
            'code': 'FC-STAFF',
            'name': 'Cartella staff',
            'folder_kind': 'department',
            'status': 'active',
        })
        self.assertTrue(ProjectFolder.objects.filter(code='FC-STAFF').exists())


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
        self.manager = User.objects.create_user('pl_manager', password='pw', is_staff=True)
        self.normal = User.objects.create_user('pl_normal', password='pw')
        self.owner = User.objects.create_user('pl_owner', password='pw')
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
        self.owner = User.objects.create_user('pd_owner', password='pw', is_staff=True)
        self.reader = User.objects.create_user('pd_reader', password='pw')
        self.outsider = User.objects.create_user('pd_outsider', password='pw')
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
        self.manager = User.objects.create_user('rv_mgr', password='pw', is_staff=True)
        self.outsider = User.objects.create_user('rv_out', password='pw')
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
