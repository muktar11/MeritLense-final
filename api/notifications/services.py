class NotificationService:
    @classmethod
    def notify_interview_session_created(cls, session):
        return {
            "session_id": str(session.public_id),
            "status": session.status,
        }
