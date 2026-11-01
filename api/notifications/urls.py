from django.urls import path

from .views import notification_mark_all_read, notification_mark_read

# GET /notifications (the list) is routed in meritlense/urls.py, without a
# trailing slash like the other list endpoints.
urlpatterns = [
    path('read-all', notification_mark_all_read, name='notification-read-all'),
    path('<uuid:notification_id>/read', notification_mark_read, name='notification-read'),
]
