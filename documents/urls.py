from django.urls import path

from . import views

urlpatterns = [
    path('', views.document_list, name='document_list'),
    path('<int:document_id>/', views.document_detail, name='document_detail'),
]
