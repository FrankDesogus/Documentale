from django.urls import path

from . import views

urlpatterns = [
    path('', views.approval_queue, name='approval_queue'),
    path('<int:approval_request_id>/', views.approval_detail, name='approval_detail'),
]
