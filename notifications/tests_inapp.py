from django.test import TestCase, Client
from django.contrib.auth import get_user_model
from django.urls import reverse
from .models import Notification

User = get_user_model()


class NotificationModelTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('u1', 'u1@test.com', 'pass')
        self.other = User.objects.create_user('u2', 'u2@test.com', 'pass')

    def test_create_action_notification(self):
        n = Notification.objects.create(
            recipient=self.user,
            category=Notification.CATEGORY_ACTION,
            notification_type='test',
            title='Test',
        )
        self.assertFalse(n.is_read)

    def test_mark_as_read(self):
        n = Notification.objects.create(
            recipient=self.user,
            category=Notification.CATEGORY_INFO,
            notification_type='test',
            title='Test',
        )
        n.mark_as_read()
        self.assertTrue(n.is_read)

    def test_mark_as_read_idempotent(self):
        """Chiamare mark_as_read due volte non cambia read_at."""
        n = Notification.objects.create(
            recipient=self.user,
            category=Notification.CATEGORY_INFO,
            notification_type='test',
            title='Test',
        )
        n.mark_as_read()
        first_read_at = n.read_at
        n.mark_as_read()
        self.assertEqual(n.read_at, first_read_at)

    def test_unread_count(self):
        Notification.objects.create(
            recipient=self.user,
            category=Notification.CATEGORY_INFO,
            notification_type='t',
            title='t',
        )
        Notification.objects.create(
            recipient=self.user,
            category=Notification.CATEGORY_ACTION,
            notification_type='t',
            title='t',
        )
        count = Notification.objects.filter(
            recipient=self.user, read_at__isnull=True
        ).count()
        self.assertEqual(count, 2)

    def test_user_sees_only_own_notifications(self):
        Notification.objects.create(
            recipient=self.user,
            category=Notification.CATEGORY_INFO,
            notification_type='t',
            title='Mine',
        )
        Notification.objects.create(
            recipient=self.other,
            category=Notification.CATEGORY_INFO,
            notification_type='t',
            title='Theirs',
        )
        mine = Notification.objects.filter(recipient=self.user)
        self.assertEqual(mine.count(), 1)
        self.assertEqual(mine.first().title, 'Mine')


class NotificationViewTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('v1', 'v1@test.com', 'pass')
        self.other = User.objects.create_user('v2', 'v2@test.com', 'pass')
        self.client = Client()
        self.client.login(username='v1', password='pass')

    def test_mark_as_read_own_notification(self):
        n = Notification.objects.create(
            recipient=self.user,
            category=Notification.CATEGORY_INFO,
            notification_type='t',
            title='T',
        )
        self.client.post(reverse('notifications:mark_read', args=[n.pk]))
        n.refresh_from_db()
        self.assertTrue(n.is_read)

    def test_cannot_mark_other_notification_as_read(self):
        n = Notification.objects.create(
            recipient=self.other,
            category=Notification.CATEGORY_INFO,
            notification_type='t',
            title='T',
        )
        response = self.client.post(reverse('notifications:mark_read', args=[n.pk]))
        self.assertEqual(response.status_code, 404)
        n.refresh_from_db()
        self.assertFalse(n.is_read)

    def test_mark_all_as_read(self):
        Notification.objects.create(
            recipient=self.user,
            category=Notification.CATEGORY_INFO,
            notification_type='t',
            title='T1',
        )
        Notification.objects.create(
            recipient=self.user,
            category=Notification.CATEGORY_ACTION,
            notification_type='t',
            title='T2',
        )
        self.client.post(reverse('notifications:mark_all_read'))
        remaining = Notification.objects.filter(
            recipient=self.user, read_at__isnull=True
        ).count()
        self.assertEqual(remaining, 0)

    def test_mark_all_as_read_does_not_touch_other_user(self):
        Notification.objects.create(
            recipient=self.other,
            category=Notification.CATEGORY_INFO,
            notification_type='t',
            title='Other',
        )
        self.client.post(reverse('notifications:mark_all_read'))
        other_unread = Notification.objects.filter(
            recipient=self.other, read_at__isnull=True
        ).count()
        self.assertEqual(other_unread, 1)

    def test_notification_list_view(self):
        Notification.objects.create(
            recipient=self.user,
            category=Notification.CATEGORY_INFO,
            notification_type='t',
            title='My notif',
        )
        response = self.client.get(reverse('notifications:list'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'My notif')

    def test_no_cross_user_in_list(self):
        Notification.objects.create(
            recipient=self.other,
            category=Notification.CATEGORY_INFO,
            notification_type='t',
            title='Other notif',
        )
        response = self.client.get(reverse('notifications:list'))
        self.assertNotContains(response, 'Other notif')

    def test_filter_unread(self):
        n = Notification.objects.create(
            recipient=self.user,
            category=Notification.CATEGORY_INFO,
            notification_type='t',
            title='Unread notif',
        )
        n_read = Notification.objects.create(
            recipient=self.user,
            category=Notification.CATEGORY_INFO,
            notification_type='t',
            title='Read notif',
        )
        n_read.mark_as_read()
        response = self.client.get(reverse('notifications:list') + '?filter=unread')
        self.assertContains(response, 'Unread notif')
        self.assertNotContains(response, 'Read notif')

    def test_unauthenticated_redirected(self):
        client2 = Client()
        response = client2.get(reverse('notifications:list'))
        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login/', response['Location'])

    def test_context_processor_provides_count(self):
        Notification.objects.create(
            recipient=self.user,
            category=Notification.CATEGORY_ACTION,
            notification_type='t',
            title='Pending action',
        )
        response = self.client.get(reverse('notifications:list'))
        self.assertIn('unread_notifications_count', response.context)
        self.assertEqual(response.context['unread_notifications_count'], 1)
