from django.urls import path

from . import views

urlpatterns = [
    path('<int:revision_id>/', views.project_revision_detail, name='project_revision_detail'),
    path('<int:revision_id>/issue/', views.project_revision_issue, name='project_revision_issue'),
]
