"""
Test strutturali per verificare che le pagine principali si carichino
correttamente dopo la migrazione a Tailwind CSS.
"""
from django.test import TestCase, Client
from django.contrib.auth import get_user_model

User = get_user_model()


class BaseTemplateStructureTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser('admin_tw_ui', 'admin_tw@test.com', 'pass')
        self.client = Client()
        self.client.login(username='admin_tw_ui', password='pass')

    def test_dashboard_loads(self):
        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)

    def test_dashboard_contains_tailwind_css(self):
        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'tailwind.css')

    def test_document_list_loads(self):
        from django.urls import reverse
        response = self.client.get(reverse('document_list'))
        self.assertEqual(response.status_code, 200)

    def test_approval_list_loads(self):
        from django.urls import reverse
        response = self.client.get(reverse('approval_queue'))
        self.assertEqual(response.status_code, 200)

    def test_ecn_list_loads(self):
        from django.urls import reverse
        response = self.client.get(reverse('ecn:ecn_list'))
        self.assertEqual(response.status_code, 200)

    def test_notifications_list_loads(self):
        from django.urls import reverse
        response = self.client.get(reverse('notifications:list'))
        self.assertEqual(response.status_code, 200)

    def test_sidebar_structure_present(self):
        """La sidebar deve contenere le sezioni principali."""
        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Archivio')
        self.assertContains(response, 'Workflow')
        self.assertContains(response, 'Attività')

    def test_theme_toggle_structure_present(self):
        """Il tema parte light e può passare a night mode via localStorage."""
        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn("documentale-theme", content)
        self.assertIn("localStorage.getItem('documentale-theme')", content)
        self.assertIn('id="theme-toggle"', content)
        self.assertIn('aria-label="Attiva modalità notte"', content)
        self.assertIn('<html lang="it" class="h-full">', content)
        self.assertNotIn('<html lang="it" class="h-full dark">', content)

    def test_sidebar_glass_classes_present(self):
        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'sidebar-shell')
        self.assertContains(response, 'sidebar-brand')
        self.assertContains(response, 'sidebar-section')
        self.assertContains(response, 'sidebar-link-active')

    def test_no_old_inline_styles_in_base(self):
        """Il base.html non deve più usare stili inline del vecchio CSS."""
        response = self.client.get('/')
        content = response.content.decode()
        # Il vecchio layout usava style="..." con colori hardcoded
        # Ora deve usare classi Tailwind; verifica assenza dei vecchi colori brand
        self.assertNotIn('background: #0f2840', content)
        self.assertNotIn('.sidebar {', content)
