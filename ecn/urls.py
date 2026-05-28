from django.urls import path

from ecn import views

app_name = 'ecn'

urlpatterns = [
    path('', views.ecn_list, name='ecn_list'),
    path('new/', views.ecn_create, name='ecn_create'),
    path('<int:ecn_id>/', views.ecn_detail, name='ecn_detail'),
    path('<int:ecn_id>/submit/', views.ecn_submit, name='ecn_submit'),
    path('<int:ecn_id>/review/', views.ecn_review, name='ecn_review'),
    path('<int:ecn_id>/close/', views.ecn_close, name='ecn_close'),
    path('<int:ecn_id>/attachment/', views.ecn_add_attachment, name='ecn_add_attachment'),
    path('attachment/<int:attachment_id>/download/', views.ecn_attachment_download, name='ecn_attachment_download'),
]
