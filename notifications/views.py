from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from .models import Notification


@login_required
def notification_list(request):
    qs = Notification.objects.filter(recipient=request.user)

    # Filtri
    filter_read = request.GET.get('filter', 'all')   # all | unread
    filter_cat = request.GET.get('category', '')     # '' | action_required | information

    if filter_read == 'unread':
        qs = qs.filter(read_at__isnull=True)
    if filter_cat in (Notification.CATEGORY_ACTION, Notification.CATEGORY_INFO):
        qs = qs.filter(category=filter_cat)

    paginator = Paginator(qs, 20)
    page = paginator.get_page(request.GET.get('page', 1))
    unread_count = Notification.objects.filter(
        recipient=request.user, read_at__isnull=True
    ).count()

    return render(request, 'notifications/notification_list.html', {
        'page_obj': page,
        'filter_read': filter_read,
        'filter_cat': filter_cat,
        'unread_count': unread_count,
    })


@login_required
@require_POST
def mark_as_read(request, pk):
    notification = get_object_or_404(Notification, pk=pk, recipient=request.user)
    notification.mark_as_read()
    next_url = request.POST.get('next') or request.META.get('HTTP_REFERER') or '/'
    return redirect(next_url)


@login_required
@require_POST
def mark_all_as_read(request):
    Notification.objects.filter(
        recipient=request.user, read_at__isnull=True
    ).update(read_at=timezone.now())
    next_url = request.POST.get('next') or request.META.get('HTTP_REFERER') or '/'
    return redirect(next_url)
