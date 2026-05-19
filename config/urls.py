from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path

from documents.views import dashboard, download_version_file, my_drafts, submit_for_approval

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', dashboard, name='dashboard'),
    path('documents/', include('documents.urls')),
    path('my-drafts/', my_drafts, name='my_drafts'),
    path('versions/<int:version_id>/submit/', submit_for_approval, name='version_submit'),
    path('versions/<int:version_id>/download/', download_version_file, name='version_download'),
    path('approvals/', include('approvals.urls')),
    path('accounts/login/', auth_views.LoginView.as_view(), name='login'),
    path('accounts/logout/', auth_views.LogoutView.as_view(), name='logout'),
]
