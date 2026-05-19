from django.urls import path

from . import views

urlpatterns = [
    path('', views.project_list, name='project_list'),
    path('new/', views.project_create, name='project_create'),
    path('<int:project_id>/', views.project_detail, name='project_detail'),
]
