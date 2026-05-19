from django.urls import path

from . import views

urlpatterns = [
    path('', views.folder_list, name='folder_list'),
    path('new/', views.folder_create, name='folder_create'),
    path('<int:folder_id>/', views.folder_detail, name='folder_detail'),
]
