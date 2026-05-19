from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect, render

from projects.models import ProjectFolder
from projects.permissions import can_manage_folder, can_view_folder


def _can_create_folder(user):
    if not user.is_authenticated:
        return False
    if user.is_superuser or user.is_staff:
        return True
    from documents.permissions import is_document_manager
    return is_document_manager(user)


@login_required
def folder_list(request):
    user = request.user
    qs = ProjectFolder.objects.filter(
        status=ProjectFolder.Status.ACTIVE,
        parent__isnull=True,
    ).prefetch_related('subfolders').order_by('code')

    if not (user.is_superuser or user.is_staff):
        from documents.permissions import is_document_manager, is_document_auditor
        if not (is_document_manager(user) or is_document_auditor(user)):
            from projects.permissions import get_visible_folder_ids
            visible_ids = get_visible_folder_ids(user)
            qs = qs.filter(pk__in=visible_ids)

    return render(request, 'projects/folder_list.html', {
        'folders': qs,
        'can_create': _can_create_folder(user),
    })


@login_required
def folder_detail(request, folder_id):
    folder = get_object_or_404(ProjectFolder, pk=folder_id)

    if not can_view_folder(request.user, folder):
        raise PermissionDenied

    subfolders = folder.subfolders.order_by('code')
    documents = folder.documents.select_related(
        'current_version', 'owner',
    ).order_by('code')
    return render(request, 'projects/folder_detail.html', {
        'folder': folder,
        'subfolders': subfolders,
        'documents': documents,
        'can_create': _can_create_folder(request.user),
        'can_manage': can_manage_folder(request.user, folder),
    })


@login_required
def folder_create(request):
    from projects.forms import ProjectFolderForm

    if not _can_create_folder(request.user):
        raise PermissionDenied

    if request.method == 'POST':
        form = ProjectFolderForm(request.POST)
        if form.is_valid():
            folder = form.save(commit=False)
            folder.owner = request.user
            folder.created_by = request.user
            folder.save()
            messages.success(request, f'Cartella "{folder.name}" creata.')
            return redirect('folder_detail', folder_id=folder.pk)
    else:
        form = ProjectFolderForm()

    return render(request, 'projects/folder_form.html', {'form': form})
