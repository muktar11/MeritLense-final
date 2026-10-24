from django.urls import path

from .views import (
    LiveCallConversationView, LiveCallGuideView, LiveCallJoinView, LiveCallPreferencesView, LiveCallSegmentView,
)

urlpatterns = [
    path("sessions/<str:session_id>/join", LiveCallJoinView.as_view(), name="live-call-join"),
    path("sessions/<str:session_id>/languages", LiveCallPreferencesView.as_view(), name="live-call-languages"),
    path("sessions/<str:session_id>/segments", LiveCallSegmentView.as_view(), name="live-call-segments"),
    path("sessions/<str:session_id>/conversation", LiveCallConversationView.as_view(), name="live-call-conversation"),
    path("sessions/<str:session_id>/guide", LiveCallGuideView.as_view(), name="live-call-guide"),
]

