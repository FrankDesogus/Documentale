from .models import Notification


def unread_notifications(request):
    if not request.user.is_authenticated:
        return {'unread_notifications_count': 0, 'recent_notifications': []}
    count = Notification.objects.filter(
        recipient=request.user, read_at__isnull=True
    ).count()
    recent = Notification.objects.filter(
        recipient=request.user
    ).order_by('-created_at')[:10]
    return {
        'unread_notifications_count': count,
        'recent_notifications': list(recent),
    }
