from django.urls import path

from . import views

urlpatterns = [
    path('', views.document_list, name='document_list'),
    path('new/', views.new_document, name='document_new'),
    path('<int:document_id>/', views.document_detail, name='document_detail'),
    path('<int:document_id>/new-revision/', views.new_revision, name='document_new_revision'),
]
