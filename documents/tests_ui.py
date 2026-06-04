"""
Test STEP UI-1 — Navigazione, ricerca con filtri e paginazione.
Copertura:
  - Sicurezza ricerca (no esposizione dati non autorizzati)
  - Documenti: ricerca, filtri, paginazione
  - Cartelle: ricerca, navigation-only, parent precompilato
  - Progetti: ricerca, filtri, paginazione
  - ECN: ricerca, filtri, paginazione
  - Approvazioni: ricerca, filtri, paginazione
  - UI: dashboard explorer, sidebar, breadcrumb
"""

from django.contrib.auth.models import Group, User
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from documents.models import Document, DocumentVersion
from projects.models import Project, ProjectFolder, ProjectFolderMembership
from ecn.models import ChangeNotice, ChangeNoticeApprover
from approvals.models import ApprovalRequest

LOCMEM = 'django.core.mail.backends.locmem.EmailBackend'


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_user(username, **kwargs):
    return User.objects.create_user(username, password='pw', **kwargs)


def _add_group(user, name):
    g, _ = Group.objects.get_or_create(name=name)
    g.user_set.add(user)
    return g


def _make_folder(code, owner, parent=None):
    from projects.services import set_folder_path
    f = ProjectFolder.objects.create(
        code=code, name=code,
        folder_kind=ProjectFolder.FolderKind.GENERIC,
        status=ProjectFolder.Status.ACTIVE,
        owner=owner, created_by=owner,
        parent=parent,
    )
    set_folder_path(f)
    return f


def _make_approved_doc(code, folder, owner, title=None):
    doc = Document.objects.create(
        code=code, title=title or f'Doc {code}',
        category=Document.Category.QUALITY,
        project_folder=folder,
        owner=owner, created_by=owner,
        status=Document.Status.ACTIVE,
    )
    ver = DocumentVersion.objects.create(
        document=doc, revision_label='00', revision_number=0,
        status=DocumentVersion.Status.APPROVED, is_current=True,
        created_by=owner,
        approved_at=timezone.now(),
    )
    doc.current_version = ver
    doc.save(update_fields=['current_version'])
    return doc, ver


def _make_draft_doc(code, folder, owner):
    doc = Document.objects.create(
        code=code, title=f'Bozza {code}',
        category=Document.Category.QUALITY,
        project_folder=folder,
        owner=owner, created_by=owner,
    )
    DocumentVersion.objects.create(
        document=doc, revision_label='00', revision_number=0,
        status=DocumentVersion.Status.DRAFT, is_current=False,
        created_by=owner,
    )
    return doc


# ---------------------------------------------------------------------------
# Sicurezza ricerca — nessuna esposizione dati non autorizzati
# ---------------------------------------------------------------------------

@override_settings(EMAIL_BACKEND=LOCMEM)
class UISecuritySearchTests(TestCase):
    """La ricerca non espone dati non autorizzati."""

    def setUp(self):
        self.owner = _make_user('sec_owner')
        self.viewer = _make_user('sec_viewer')
        self.stranger = _make_user('sec_stranger')
        _add_group(self.viewer, 'Document Readers')

        self.folder_a = _make_folder('SEC-A', self.owner)
        self.folder_b = _make_folder('SEC-B', self.owner)

        # viewer ha accesso a folder_a ma non a folder_b
        ProjectFolderMembership.objects.create(
            folder=self.folder_a, user=self.viewer, role='reader',
        )

        self.doc_a, _ = _make_approved_doc('SEC-DOC-A', self.folder_a, self.owner)
        self.doc_b, _ = _make_approved_doc('SEC-DOC-B', self.folder_b, self.owner)
        self.draft_doc = _make_draft_doc('SEC-DRAFT', self.folder_a, self.owner)

    # 1. La ricerca documenti non espone bozza privata altrui
    def test_search_docs_no_draft_exposure(self):
        self.client.force_login(self.viewer)
        r = self.client.get(reverse('document_list'), {'q': 'SEC-DRAFT'})
        self.assertEqual(r.status_code, 200)
        codes = [d.code for d in r.context['documents']]
        self.assertNotIn('SEC-DRAFT', codes)

    # 2. La ricerca documenti non espone documento in cartella negata
    def test_search_docs_no_restricted_folder_exposure(self):
        self.client.force_login(self.viewer)
        r = self.client.get(reverse('document_list'), {'q': 'SEC-DOC-B'})
        self.assertEqual(r.status_code, 200)
        codes = [d.code for d in r.context['documents']]
        self.assertNotIn('SEC-DOC-B', codes)

    # 3. La ricerca documenti mostra il documento autorizzato
    def test_search_docs_shows_authorized(self):
        self.client.force_login(self.viewer)
        r = self.client.get(reverse('document_list'), {'q': 'SEC-DOC-A'})
        self.assertEqual(r.status_code, 200)
        codes = [d.code for d in r.context['documents']]
        self.assertIn('SEC-DOC-A', codes)

    # 4. La ricerca progetti non espone progetto non autorizzato
    def test_search_projects_no_unauthorized_exposure(self):
        mgr = _make_user('sec_mgr')
        _add_group(mgr, 'Document Managers')
        proj = Project.objects.create(
            code='SEC-PRJ', name='Progetto riservato',
            status=Project.Status.ACTIVE,
            folder=self.folder_b, created_by=mgr,
        )
        self.client.force_login(self.viewer)
        r = self.client.get(reverse('project_list'), {'q': 'SEC-PRJ'})
        self.assertEqual(r.status_code, 200)
        codes = [p.code for p in r.context['projects']]
        self.assertNotIn('SEC-PRJ', codes)

    # 5. La ricerca ECN non espone ECN non autorizzata
    def test_search_ecn_no_unauthorized_exposure(self):
        other_owner = _make_user('sec_ecn_owner')
        folder_hidden = _make_folder('SEC-HIDDEN', other_owner)
        doc_hidden, ver_hidden = _make_approved_doc('SEC-ECN-DOC', folder_hidden, other_owner)
        ecn = ChangeNotice.objects.create(
            code='SEC-ECN-001', title='ECN riservata',
            motivation=ChangeNotice.Motivation.IMPROVEMENT,
            document=doc_hidden, document_version=ver_hidden,
            proposed_by=other_owner, created_by=other_owner,
        )
        self.client.force_login(self.viewer)
        r = self.client.get(reverse('ecn:ecn_list'), {'q': 'SEC-ECN-001'})
        self.assertEqual(r.status_code, 200)
        codes = [e.code for e in r.context['ecns']]
        self.assertNotIn('SEC-ECN-001', codes)

    # 6. La ricerca approvazioni non espone richieste non autorizzate
    def test_search_approvals_no_unauthorized_exposure(self):
        other_owner = _make_user('sec_appr_owner')
        other_doc = Document.objects.create(
            code='SEC-APPR-DOC', title='T',
            category=Document.Category.QUALITY,
            owner=other_owner, created_by=other_owner,
        )
        ver = DocumentVersion.objects.create(
            document=other_doc, revision_label='00', revision_number=0,
            status=DocumentVersion.Status.IN_APPROVAL, is_current=False,
            created_by=other_owner,
        )
        other_approver = _make_user('sec_appr_approver')
        ar = ApprovalRequest.objects.create(
            document_version=ver,
            requested_by=other_owner,
            status=ApprovalRequest.Status.PENDING,
        )
        self.client.force_login(self.viewer)
        r = self.client.get(reverse('approval_queue'), {'q': 'SEC-APPR-DOC'})
        self.assertEqual(r.status_code, 200)
        ids = [a.pk for a in r.context['approval_requests']]
        self.assertNotIn(ar.pk, ids)


# ---------------------------------------------------------------------------
# Documenti — ricerca, filtri, paginazione
# ---------------------------------------------------------------------------

@override_settings(EMAIL_BACKEND=LOCMEM)
class UIDocumentSearchTests(TestCase):

    def setUp(self):
        self.manager = _make_user('uds_mgr')
        _add_group(self.manager, 'Document Managers')
        self.folder = _make_folder('UDS-FOLD', self.manager)
        # Crea 25 documenti approvati per testare la paginazione
        for i in range(25):
            _make_approved_doc(
                f'UDS-{i+1:03d}', self.folder, self.manager,
                title=f'Procedura test {i+1}',
            )

    # 1. Ricerca per codice
    def test_search_by_code(self):
        self.client.force_login(self.manager)
        r = self.client.get(reverse('document_list'), {'q': 'UDS-001'})
        self.assertEqual(r.status_code, 200)
        codes = [d.code for d in r.context['documents']]
        self.assertIn('UDS-001', codes)
        self.assertNotIn('UDS-002', codes)

    # 2. Ricerca per titolo
    def test_search_by_title(self):
        self.client.force_login(self.manager)
        r = self.client.get(reverse('document_list'), {'q': 'Procedura test'})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(len(list(r.context['documents'])) > 0)

    # 3. Filtro cartella
    def test_filter_by_folder(self):
        # Crea una seconda cartella con un documento
        folder2 = _make_folder('UDS-FOLD2', self.manager)
        _make_approved_doc('UDS-F2-001', folder2, self.manager)
        self.client.force_login(self.manager)
        r = self.client.get(reverse('document_list'), {'folder': str(self.folder.pk)})
        self.assertEqual(r.status_code, 200)
        codes = [d.code for d in r.context['documents']]
        self.assertNotIn('UDS-F2-001', codes)

    # 4. Paginazione: 25 doc, pagina 1 ha 20
    def test_pagination_page1(self):
        self.client.force_login(self.manager)
        r = self.client.get(reverse('document_list'))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(list(r.context['documents'])), 20)
        self.assertTrue(r.context['page_obj'].has_next())

    # 5. Paginazione: pagina 2 ha 5
    def test_pagination_page2(self):
        self.client.force_login(self.manager)
        r = self.client.get(reverse('document_list'), {'page': '2'})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(list(r.context['documents'])), 5)

    # 6. Reset filtri
    def test_reset_filters(self):
        self.client.force_login(self.manager)
        r = self.client.get(reverse('document_list'))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'UDS-001')

    # 7. Total count corretto
    def test_total_count(self):
        self.client.force_login(self.manager)
        r = self.client.get(reverse('document_list'))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.context['total_count'], 25)


# ---------------------------------------------------------------------------
# Cartelle — ricerca, navigation-only, parent precompilato
# ---------------------------------------------------------------------------

class UIFolderSearchTests(TestCase):

    def setUp(self):
        self.manager = _make_user('ufs_mgr')
        _add_group(self.manager, 'Document Managers')
        self.folder_a = _make_folder('UFS-A', self.manager)
        self.folder_b = _make_folder('UFS-B', self.manager)

    # 1. Ricerca per nome/codice
    def test_search_by_name(self):
        self.client.force_login(self.manager)
        r = self.client.get(reverse('folder_list'), {'q': 'UFS-A'})
        self.assertEqual(r.status_code, 200)
        codes = [f.code for f in r.context['folders']]
        self.assertIn('UFS-A', codes)
        self.assertNotIn('UFS-B', codes)

    # 2. Senza filtro mostra tutte le cartelle radice
    def test_no_filter_shows_all(self):
        self.client.force_login(self.manager)
        r = self.client.get(reverse('folder_list'))
        self.assertEqual(r.status_code, 200)
        codes = [f.code for f in r.context['folders']]
        self.assertIn('UFS-A', codes)
        self.assertIn('UFS-B', codes)

    # 3. Navigation-only mostrata correttamente nel dettaglio
    def test_navigation_only_shown_in_detail(self):
        viewer = _make_user('ufs_viewer')
        # Non diamo accesso diretto a folder_a, solo navigazione
        sub = _make_folder('UFS-A-SUB', self.manager, parent=self.folder_a)
        ProjectFolderMembership.objects.create(
            folder=sub, user=viewer, role='reader',
        )
        self.client.force_login(viewer)
        r = self.client.get(reverse('folder_detail', args=[self.folder_a.pk]))
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.context['is_navigation_only'])

    # 4. Parent precompilato nella creazione sottocartella
    def test_parent_precompiled_in_subfolder_create(self):
        self.client.force_login(self.manager)
        r = self.client.get(
            reverse('folder_create') + f'?parent={self.folder_a.pk}'
        )
        self.assertEqual(r.status_code, 200)
        # Il form deve avere parent precompilato (come campo hidden o initial)
        self.assertContains(r, str(self.folder_a.pk))

    # 5. Paginazione presente (richiede almeno 25 cartelle)
    def test_pagination_present_with_many_folders(self):
        for i in range(25):
            _make_folder(f'UFS-MANY-{i+1:02d}', self.manager)
        self.client.force_login(self.manager)
        r = self.client.get(reverse('folder_list'))
        self.assertEqual(r.status_code, 200)
        self.assertIn('page_obj', r.context)


# ---------------------------------------------------------------------------
# Progetti — ricerca, filtri, paginazione
# ---------------------------------------------------------------------------

class UIProjectSearchTests(TestCase):

    def setUp(self):
        self.manager = _make_user('ups_mgr')
        _add_group(self.manager, 'Document Managers')
        self.folder = _make_folder('UPS-FOLD', self.manager)
        for i in range(25):
            Project.objects.create(
                code=f'UPS-PRJ-{i+1:02d}',
                name=f'Progetto {i+1}',
                status=Project.Status.ACTIVE,
                folder=self.folder,
                created_by=self.manager,
            )

    # 1. Ricerca per codice
    def test_search_by_code(self):
        self.client.force_login(self.manager)
        r = self.client.get(reverse('project_list'), {'q': 'UPS-PRJ-01'})
        self.assertEqual(r.status_code, 200)
        codes = [p.code for p in r.context['projects']]
        self.assertIn('UPS-PRJ-01', codes)
        self.assertNotIn('UPS-PRJ-02', codes)

    # 2. Filtro stato
    def test_filter_by_status(self):
        Project.objects.create(
            code='UPS-SUSP', name='Sospeso',
            status=Project.Status.SUSPENDED,
            folder=self.folder, created_by=self.manager,
        )
        self.client.force_login(self.manager)
        r = self.client.get(reverse('project_list'), {'status': 'suspended'})
        self.assertEqual(r.status_code, 200)
        codes = [p.code for p in r.context['projects']]
        self.assertIn('UPS-SUSP', codes)
        # Gli attivi non devono apparire
        self.assertNotIn('UPS-PRJ-01', codes)

    # 3. Paginazione: 25 progetti, pagina 1 ha 20
    def test_pagination_page1(self):
        self.client.force_login(self.manager)
        r = self.client.get(reverse('project_list'))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(list(r.context['projects'])), 20)

    # 4. Cartella precompilata nel link da folder_detail
    def test_create_project_link_has_folder_precompiled(self):
        self.client.force_login(self.manager)
        r = self.client.get(reverse('folder_detail', args=[self.folder.pk]))
        self.assertEqual(r.status_code, 200)
        # Il link "+ Crea progetto" deve contenere ?folder=
        self.assertContains(r, f'?folder={self.folder.pk}')

    # 5. Total count nel contesto
    def test_total_count_in_context(self):
        self.client.force_login(self.manager)
        r = self.client.get(reverse('project_list'))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.context['total_count'], 25)


# ---------------------------------------------------------------------------
# ECN — ricerca, filtri, paginazione
# ---------------------------------------------------------------------------

@override_settings(EMAIL_BACKEND=LOCMEM)
class UIECNSearchTests(TestCase):

    def setUp(self):
        from documents.permissions import GROUP_QUALITY_MANAGER
        self.qm = _make_user('ues_qm')
        _add_group(self.qm, GROUP_QUALITY_MANAGER)
        self.ccb = _make_user('ues_ccb')
        _add_group(self.ccb, 'Change Control Board')
        self.proposer = _make_user('ues_proposer')
        self.folder = _make_folder('UES-FOLD', self.qm)
        self.doc, self.ver = _make_approved_doc('UES-DOC-001', self.folder, self.qm)

        # Crea 22 ECN in DRAFT
        for i in range(22):
            ChangeNotice.objects.create(
                code=f'UES-{i+1:03d}',
                title=f'ECN variante {i+1}',
                motivation=ChangeNotice.Motivation.IMPROVEMENT,
                document=self.doc, document_version=self.ver,
                proposed_by=self.qm, created_by=self.qm,
            )

        # Crea 1 ECN in UNDER_REVIEW con ccb come approvatore
        self.ecn_ur = ChangeNotice.objects.create(
            code='UES-UR-001', title='ECN in revisione',
            motivation=ChangeNotice.Motivation.CUSTOMER,
            document=self.doc, document_version=self.ver,
            proposed_by=self.proposer, created_by=self.proposer,
            status=ChangeNotice.Status.UNDER_REVIEW,
        )
        ChangeNoticeApprover.objects.create(
            change_notice=self.ecn_ur, user=self.ccb, order=1,
        )

    # 1. Ricerca per codice
    def test_search_by_code(self):
        self.client.force_login(self.qm)
        r = self.client.get(reverse('ecn:ecn_list'), {'q': 'UES-001'})
        self.assertEqual(r.status_code, 200)
        codes = [e.code for e in r.context['ecns']]
        self.assertIn('UES-001', codes)

    # 2. Filtro stato
    def test_filter_by_status(self):
        self.client.force_login(self.qm)
        r = self.client.get(reverse('ecn:ecn_list'), {'status': 'draft'})
        self.assertEqual(r.status_code, 200)
        codes = [e.code for e in r.context['ecns']]
        self.assertNotIn('UES-UR-001', codes)

    # 3. Filtro "solo mie azioni" — ccb vede l'ECN da votare
    def test_filter_my_action(self):
        self.client.force_login(self.ccb)
        r = self.client.get(reverse('ecn:ecn_list'), {'my_action': '1'})
        self.assertEqual(r.status_code, 200)
        codes = [e.code for e in r.context['ecns']]
        self.assertIn('UES-UR-001', codes)

    # 4. Paginazione: 22+ ECN, pagina 1 ha max 20
    def test_pagination_page1(self):
        self.client.force_login(self.qm)
        r = self.client.get(reverse('ecn:ecn_list'))
        self.assertEqual(r.status_code, 200)
        self.assertLessEqual(len(list(r.context['ecns'])), 20)

    # 5. Proponente vede solo le sue ECN (non quelle altrui)
    def test_proposer_sees_own_ecn(self):
        self.client.force_login(self.proposer)
        r = self.client.get(reverse('ecn:ecn_list'))
        self.assertEqual(r.status_code, 200)
        codes = [e.code for e in r.context['ecns']]
        self.assertIn('UES-UR-001', codes)
        # Non deve vedere le ECN del qm (folder non visibile)
        self.assertNotIn('UES-001', codes)

    # 6. Filtro proponente
    def test_filter_proposer(self):
        self.client.force_login(self.qm)
        r = self.client.get(reverse('ecn:ecn_list'), {'proposer': str(self.proposer.pk)})
        self.assertEqual(r.status_code, 200)
        codes = [e.code for e in r.context['ecns']]
        self.assertIn('UES-UR-001', codes)
        self.assertNotIn('UES-001', codes)

    # 7. Filtro coordinator
    def test_filter_coordinator(self):
        self.client.force_login(self.qm)
        # Crea ECN con coordinator
        ecn_coord = ChangeNotice.objects.create(
            code='UES-COORD', title='ECN con coordinator',
            motivation=ChangeNotice.Motivation.REGULATORY,
            document=self.doc, document_version=self.ver,
            proposed_by=self.qm, created_by=self.qm,
            ccb_coordinator=self.ccb,
        )
        r = self.client.get(reverse('ecn:ecn_list'), {'coordinator': str(self.ccb.pk)})
        self.assertEqual(r.status_code, 200)
        codes = [e.code for e in r.context['ecns']]
        self.assertIn('UES-COORD', codes)

    # 8. Filtro membro CCB
    def test_filter_ccb_member(self):
        self.client.force_login(self.qm)
        r = self.client.get(reverse('ecn:ecn_list'), {'ccb_member': str(self.ccb.pk)})
        self.assertEqual(r.status_code, 200)
        codes = [e.code for e in r.context['ecns']]
        self.assertIn('UES-UR-001', codes)


# ---------------------------------------------------------------------------
# Approvazioni — ricerca, filtri, paginazione
# ---------------------------------------------------------------------------

@override_settings(EMAIL_BACKEND=LOCMEM)
class UIApprovalSearchTests(TestCase):

    def setUp(self):
        self.owner = _make_user('uas_owner')
        self.approver = _make_user('uas_approver')
        self.folder = _make_folder('UAS-FOLD', self.owner)
        self.doc, self.ver = _make_approved_doc('UAS-DOC-001', self.folder, self.owner)

        # Crea 22 richieste di approvazione PENDING assegnate a self.approver
        from approvals.models import ApprovalRequestApprover
        for i in range(22):
            ver_i = DocumentVersion.objects.create(
                document=self.doc,
                revision_label=f'{i+1:02d}',
                revision_number=i + 1,
                status=DocumentVersion.Status.IN_APPROVAL,
                is_current=False,
                created_by=self.owner,
            )
            ar = ApprovalRequest.objects.create(
                document_version=ver_i,
                requested_by=self.owner,
                status=ApprovalRequest.Status.PENDING,
            )
            ApprovalRequestApprover.objects.create(
                approval_request=ar, approver=self.approver, order=1,
            )

    # 1. Ricerca per codice documento
    def test_search_by_document_code(self):
        self.client.force_login(self.approver)
        r = self.client.get(reverse('approval_queue'), {'q': 'UAS-DOC-001', 'status': 'PENDING'})
        self.assertEqual(r.status_code, 200)
        self.assertGreater(len(list(r.context['approval_requests'])), 0)

    # 2. Filtro stato PENDING (default)
    def test_filter_status_pending(self):
        self.client.force_login(self.approver)
        r = self.client.get(reverse('approval_queue'))
        self.assertEqual(r.status_code, 200)
        for ar in r.context['approval_requests']:
            self.assertEqual(ar.status, 'PENDING')

    # 3. Filtro "da approvare da me"
    def test_filter_to_approve(self):
        self.client.force_login(self.approver)
        r = self.client.get(reverse('approval_queue'), {'mine': 'to_approve', 'status': 'PENDING'})
        self.assertEqual(r.status_code, 200)
        self.assertGreater(len(list(r.context['approval_requests'])), 0)

    # 4. Filtro "create da me"
    def test_filter_created_by_me(self):
        self.client.force_login(self.owner)
        r = self.client.get(reverse('approval_queue'), {'mine': 'created', 'status': ''})
        self.assertEqual(r.status_code, 200)
        # owner ha creato le richieste
        self.assertGreater(len(list(r.context['approval_requests'])), 0)

    # 5. Paginazione: 22 richieste, pagina 1 ha 20
    def test_pagination_page1(self):
        self.client.force_login(self.approver)
        r = self.client.get(reverse('approval_queue'))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(list(r.context['approval_requests'])), 20)
        self.assertTrue(r.context['page_obj'].has_next())

    # 6. Total count nel contesto
    def test_total_count(self):
        self.client.force_login(self.approver)
        r = self.client.get(reverse('approval_queue'))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.context['total_count'], 22)


# ---------------------------------------------------------------------------
# UI — dashboard, sidebar, breadcrumb
# ---------------------------------------------------------------------------

class UIDashboardTests(TestCase):

    def setUp(self):
        self.user = _make_user('udt_user')
        self.manager = _make_user('udt_mgr')
        _add_group(self.manager, 'Document Managers')
        self.folder = _make_folder('UDT-FOLD', self.manager)
        ProjectFolderMembership.objects.create(
            folder=self.folder, user=self.user, role='reader',
        )
        _make_approved_doc('UDT-DOC-001', self.folder, self.manager)

    # 1. Dashboard mostra explorer cartelle
    def test_dashboard_shows_folder_explorer(self):
        self.client.force_login(self.manager)
        r = self.client.get(reverse('dashboard'))
        self.assertEqual(r.status_code, 200)
        self.assertIn('explorer_folders', r.context)
        self.assertGreater(len(r.context['explorer_folders']), 0)

    # 2. Dashboard mostra counter bozze
    def test_dashboard_shows_draft_count(self):
        self.client.force_login(self.manager)
        r = self.client.get(reverse('dashboard'))
        self.assertEqual(r.status_code, 200)
        self.assertIn('draft_count', r.context)

    # 3. Dashboard mostra counter approvazioni
    def test_dashboard_shows_approval_count(self):
        self.client.force_login(self.user)
        r = self.client.get(reverse('dashboard'))
        self.assertEqual(r.status_code, 200)
        self.assertIn('pending_count', r.context)

    # 4. Sidebar presente e contiene sezioni principali
    def test_sidebar_contains_main_sections(self):
        self.client.force_login(self.user)
        r = self.client.get(reverse('dashboard'))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'Archivio')
        self.assertContains(r, 'Workflow')
        self.assertContains(r, 'Attività')

    # 5. Navigation-only non mostra azioni operative nel dettaglio
    def test_navigation_only_hides_operations(self):
        # Il viewer ha membership su folder (non navigation-only)
        # Creiamo una situazione navigation-only
        parent = _make_folder('UDT-PARENT', self.manager)
        child = _make_folder('UDT-CHILD', self.manager, parent=parent)
        ProjectFolderMembership.objects.create(
            folder=child, user=self.user, role='reader',
        )
        self.client.force_login(self.user)
        r = self.client.get(reverse('folder_detail', args=[parent.pk]))
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.context['is_navigation_only'])
        # Nessun pulsante "Nuovo documento"
        self.assertNotContains(r, '+ Nuovo documento')

    # 6. Breadcrumb cartella padre presente nel dettaglio
    def test_folder_detail_breadcrumb(self):
        child = _make_folder('UDT-BREADCRUMB-CHILD', self.manager, parent=self.folder)
        self.client.force_login(self.manager)
        r = self.client.get(reverse('folder_detail', args=[child.pk]))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'UDT-FOLD')  # nome del padre nel breadcrumb

    # 7. Explorer dashboard limitato ai folder visibili
    def test_dashboard_explorer_shows_only_visible_folders(self):
        hidden_folder = _make_folder('UDT-HIDDEN', self.manager)
        # user non ha accesso a hidden_folder
        self.client.force_login(self.user)
        r = self.client.get(reverse('dashboard'))
        self.assertEqual(r.status_code, 200)
        folder_codes = [f.code for f in r.context['explorer_folders']]
        self.assertNotIn('UDT-HIDDEN', folder_codes)
        self.assertIn('UDT-FOLD', folder_codes)
