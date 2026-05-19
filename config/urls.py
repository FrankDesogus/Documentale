from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path

from documents.views import dashboard, my_drafts

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', dashboard, name='dashboard'),
    path('documents/', include('documents.urls')),
    path('my-drafts/', my_drafts, name='my_drafts'),
    path('approvals/', include('approvals.urls')),
    path('accounts/login/', auth_views.LoginView.as_view(), name='login'),
    path('accounts/logout/', auth_views.LogoutView.as_view(), name='logout'),
]
