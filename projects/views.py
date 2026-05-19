from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect, render

from projects.models import ProjectFolder


def _can_create_folder(user):
    if not user.is_authenticated:
        return False
    if user.is_superuser or user.is_staff:
        return True
    from documents.permissions import is_document_manager
    return is_document_manager(user)


@login_required
def folder_list(request):
    folders = ProjectFolder.objects.filter(
        status=ProjectFolder.Status.ACTIVE,
        parent__isnull=True,
    ).prefetch_related('subfolders').order_by('code')
    return render(request, 'projects/folder_list.html', {
        'folders': folders,
        'can_create': _can_create_folder(request.user),
    })


@login_required
def folder_detail(request, folder_id):
    folder = get_object_or_404(ProjectFolder, pk=folder_id)
    subfolders = folder.subfolders.order_by('code')
    documents = folder.documents.select_related(
        'current_version', 'owner',
    ).order_by('code')
    return render(request, 'projects/folder_detail.html', {
        'folder': folder,
        'subfolders': subfolders,
        'documents': documents,
        'can_create': _can_create_folder(request.user),
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
