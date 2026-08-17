from django.urls import path

from .views import LiveCallJoinView, LiveCallPreferencesView, LiveCallSegmentView

urlpatterns = [
    path("sessions/<str:session_id>/join", LiveCallJoinView.as_view(), name="live-call-join"),
    path("sessions/<str:session_id>/languages", LiveCallPreferencesView.as_view(), name="live-call-languages"),
    path("sessions/<str:session_id>/segments", LiveCallSegmentView.as_view(), name="live-call-segments"),
]

