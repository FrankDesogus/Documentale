from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path

from documents.views import (
    dashboard,
    download_version_file,
    edit_version,
    my_drafts,
    submit_for_approval,
    workspace_my_work,
    workspace_quality,
)

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', dashboard, name='dashboard'),
    path('workspace/my-work/', workspace_my_work, name='workspace_my_work'),
    path('workspace/quality/', workspace_quality, name='workspace_quality'),
    path('documents/', include('documents.urls')),
    path('my-drafts/', my_drafts, name='my_drafts'),
    path('versions/<int:version_id>/submit/', submit_for_approval, name='version_submit'),
    path('versions/<int:version_id>/edit/', edit_version, name='version_edit'),
    path('versions/<int:version_id>/download/', download_version_file, name='version_download'),
    path('folders/', include('projects.urls')),
    path('projects/', include('projects.project_urls')),
    path('project-revisions/', include('projects.revision_urls')),
    path('approvals/', include('approvals.urls')),
    path('ecn/', include('ecn.urls')),
    path('accounts/login/', auth_views.LoginView.as_view(), name='login'),
    path('accounts/logout/', auth_views.LogoutView.as_view(), name='logout'),
]
