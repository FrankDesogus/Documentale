from django.urls import path

from ecn import views

app_name = 'ecn'

urlpatterns = [
    path('', views.ecn_list, name='ecn_list'),
    path('dashboard/', views.ecn_dashboard, name='ecn_dashboard'),
    path('my-ecn/', views.ecn_my, name='ecn_my'),
    path('new/', views.ecn_create, name='ecn_create'),
    path('<int:ecn_id>/', views.ecn_detail, name='ecn_detail'),
    path('<int:ecn_id>/edit/', views.ecn_edit, name='ecn_edit'),
    path('<int:ecn_id>/configure-ccb/', views.ecn_configure_ccb, name='ecn_configure_ccb'),
    path('<int:ecn_id>/ccb-dossier/', views.ecn_ccb_dossier, name='ecn_ccb_dossier'),
    path('<int:ecn_id>/reopen-ccb/', views.ecn_reopen_ccb, name='ecn_reopen_ccb'),
    path('<int:ecn_id>/submit/', views.ecn_submit, name='ecn_submit'),
    path('<int:ecn_id>/review/', views.ecn_review, name='ecn_review'),
    path('<int:ecn_id>/close/', views.ecn_close, name='ecn_close'),
    path('<int:ecn_id>/attachment/', views.ecn_add_attachment, name='ecn_add_attachment'),
    path('attachment/<int:attachment_id>/download/', views.ecn_attachment_download, name='ecn_attachment_download'),
]
