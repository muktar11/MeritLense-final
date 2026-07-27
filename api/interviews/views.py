import mimetypes

from django.http import FileResponse
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import extend_schema
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response

from api.core.public_ids import PUBLIC_ID_OR_PK_REGEX, build_object_identifier_filter
from api.interviews.models import (
    InterviewConfiguration,
    InterviewRubric,
    PackageSessionConfig,
    RolePackageCoverage,
)
from api.questions.models import QuestionTemplate
from api.sessions.models import CandidateResponse, InterviewSession, ObservedTaskDefinition, SessionObservedTask, TaskObservationResult
from api.sessions.identity_services import IdentityVerificationError, InterviewSessionPrecheckService
from api.sessions.services import InterviewSessionService, InterviewVoicePipelineService, TaskObservationService
from api.translation.services import AIProcessingError, AIProcessingOrchestrationService
from api.evaluations.scoring_services import Week6ScoringError, Week6ScoringService
from api.evaluations.serializers import SessionEvaluationSummarySerializer
from api.reports.serializers import EvaluationReportSerializer
from api.reports.services import EvaluationReportService

from .serializers import (
    CandidateResponseSerializer,
    InterviewConfigurationSerializer,
    InterviewSessionCreateSerializer,
    InterviewSessionSerializer,
    InterviewRubricSerializer,
    ObservedTaskDefinitionSerializer,
    PackageSessionConfigSerializer,
    QuestionAudioArtifactSerializer,
    QuestionTemplateSerializer,
    RolePackageCoverageSerializer,
    ResponseAIActionSerializer,
    SessionArtifactSerializer,
    SessionObservedTaskSerializer,
    ResponseAIProcessingStatusSerializer,
    SessionAIProcessingSummarySerializer,
    SessionAudioUploadSerializer,
    SessionConsentCaptureSerializer,
    SessionDeviceCheckSerializer,
    SessionIdentityVerificationSerializer,
    SessionIntegrityEventSerializer,
    SessionPrecheckStatusSerializer,
    SessionPrivacyAcknowledgementSerializer,
    SessionQuestionSerializer,
    SessionResponseSubmitSerializer,
    SessionStartSerializer,
    SessionTaskCompletionSerializer,
    SessionTranscriptionSerializer,
    SessionTokenSerializer,
    TaskObservationResultSerializer,
    SessionVerbalConfirmationSerializer,
)


class CanManageInterviewSetupMixin:
    setup_roles = {"ADMIN", "SUPERADMIN", "B2B", "B2C"}

    def get_permissions(self):
        return [IsAuthenticated()]

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        if self.action in {"create", "update", "partial_update", "destroy"}:
            if request.user.role not in self.setup_roles:
                raise PermissionDenied("You do not have permission to manage interview setup")


class InterviewConfigurationViewSet(CanManageInterviewSetupMixin, viewsets.ModelViewSet):
    serializer_class = InterviewConfigurationSerializer
    lookup_field = "public_id"
    lookup_url_kwarg = "id"
    lookup_value_regex = PUBLIC_ID_OR_PK_REGEX

    def get_queryset(self):
        queryset = InterviewConfiguration.objects.all()
        if self.action == "list":
            return queryset.filter(is_active=True)
        return queryset


class InterviewRubricViewSet(CanManageInterviewSetupMixin, viewsets.ModelViewSet):
    queryset = InterviewRubric.objects.all()
    serializer_class = InterviewRubricSerializer
    lookup_field = "public_id"
    lookup_url_kwarg = "id"
    lookup_value_regex = PUBLIC_ID_OR_PK_REGEX


class PackageSessionConfigViewSet(CanManageInterviewSetupMixin, viewsets.ModelViewSet):
    serializer_class = PackageSessionConfigSerializer
    lookup_field = "public_id"
    lookup_url_kwarg = "id"
    lookup_value_regex = PUBLIC_ID_OR_PK_REGEX

    def get_queryset(self):
        queryset = PackageSessionConfig.objects.all()
        if self.action == "list":
            return queryset.filter(is_active=True)
        return queryset


class RolePackageCoverageViewSet(CanManageInterviewSetupMixin, viewsets.ModelViewSet):
    serializer_class = RolePackageCoverageSerializer
    lookup_field = "public_id"
    lookup_url_kwarg = "id"
    lookup_value_regex = PUBLIC_ID_OR_PK_REGEX

    def get_queryset(self):
        queryset = RolePackageCoverage.objects.all()
        if self.action == "list":
            return queryset.filter(is_active=True)
        return queryset


class QuestionTemplateViewSet(CanManageInterviewSetupMixin, viewsets.ModelViewSet):
    queryset = QuestionTemplate.objects.all()
    serializer_class = QuestionTemplateSerializer
    lookup_field = "public_id"
    lookup_url_kwarg = "id"
    lookup_value_regex = PUBLIC_ID_OR_PK_REGEX


class ObservedTaskDefinitionViewSet(CanManageInterviewSetupMixin, viewsets.ModelViewSet):
    serializer_class = ObservedTaskDefinitionSerializer
    lookup_field = "public_id"
    lookup_url_kwarg = "id"
    lookup_value_regex = PUBLIC_ID_OR_PK_REGEX

    def get_queryset(self):
        queryset = ObservedTaskDefinition.objects.all()
        if self.action == "list":
            queryset = queryset.filter(is_active=True)
        role_code = self.request.query_params.get("role_code")
        if role_code:
            queryset = queryset.filter(role_code__iexact=role_code)
        return queryset


class TaskObservationResultViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = TaskObservationResultSerializer
    lookup_field = "public_id"
    lookup_url_kwarg = "id"
    lookup_value_regex = PUBLIC_ID_OR_PK_REGEX

    def get_permissions(self):
        return [IsAuthenticated()]

    def get_queryset(self):
        queryset = TaskObservationResult.objects.select_related(
            "session",
            "candidate",
            "session_task",
            "session_task__task_definition",
            "generated_by",
        )
        user = self.request.user
        if user.role in {"ADMIN", "SUPERADMIN"}:
            return queryset
        if hasattr(user, "managed_company"):
            return queryset.filter(session__organization=user.managed_company)
        if user.role == "B2B_TEAM_MEMBER":
            return queryset.filter(candidate__shared_with=user).distinct()
        return queryset.filter(session__created_by=user)

    def retrieve(self, request, *args, **kwargs):
        result = self.get_object()
        response = super().retrieve(request, *args, **kwargs)
        actor = request.user if request.user.is_authenticated else None
        if actor is not None:
            from api.audit.services import AuditLogService
            from api.core.constants import AuditLogAction, AuditLogCategory

            AuditLogService.log(
                user=actor,
                action=AuditLogAction.TASK_RESULT_VIEWED,
                category=AuditLogCategory.SESSION,
                description=f"Task observation result viewed for session {result.session.public_id}",
                resource=result.session,
                data={
                    "session_id": str(result.session.public_id),
                    "task_result_id": str(result.public_id),
                    "task_id": str(result.session_task.public_id),
                    "task_code": result.session_task.task_definition.task_code,
                },
                request=request,
            )
        return response


class InterviewSessionViewSet(viewsets.GenericViewSet):
    serializer_class = InterviewSessionSerializer
    lookup_field = "public_id"
    lookup_url_kwarg = "id"
    lookup_value_regex = PUBLIC_ID_OR_PK_REGEX

    def get_permissions(self):
        if self.action in {"list", "create"}:
            return [IsAuthenticated()]
        return [AllowAny()]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return InterviewSession.objects.none()

        queryset = InterviewSession.objects.select_related(
            "candidate",
            "organization",
            "config",
            "created_by",
        ).prefetch_related("questions")

        user = getattr(self.request, "user", None)
        if not user or not user.is_authenticated:
            return InterviewSession.objects.none()
        if user.role in {"ADMIN", "SUPERADMIN"}:
            return queryset
        if hasattr(user, "managed_company"):
            return queryset.filter(organization=user.managed_company)
        if user.role == "B2B_TEAM_MEMBER":
            return queryset.filter(candidate__shared_with=user).distinct()
        return queryset.filter(created_by=user)

    def get_serializer_class(self):
        if self.action == "create":
            return InterviewSessionCreateSerializer
        if self.action == "capture_consent":
            return SessionConsentCaptureSerializer
        if self.action == "acknowledge_privacy":
            return SessionPrivacyAcknowledgementSerializer
        if self.action == "complete_device_check":
            return SessionDeviceCheckSerializer
        if self.action == "submit_verbal_confirmation":
            return SessionVerbalConfirmationSerializer
        if self.action == "submit_identity_verification":
            return SessionIdentityVerificationSerializer
        if self.action == "log_integrity_event":
            return SessionIntegrityEventSerializer
        if self.action == "precheck_status":
            return SessionPrecheckStatusSerializer
        if self.action == "start_task":
            return SessionTokenSerializer
        if self.action == "complete_task":
            return SessionTaskCompletionSerializer
        if self.action == "upload_response_audio":
            return SessionAudioUploadSerializer
        if self.action == "transcribe_response":
            return SessionTranscriptionSerializer
        if self.action == "ai_processing_summary":
            return SessionAIProcessingSummarySerializer
        if self.action == "submit_response":
            return SessionResponseSubmitSerializer
        if self.action == "start":
            return SessionStartSerializer
        if self.action in {"retrieve", "list"}:
            return InterviewSessionSerializer
        return SessionTokenSerializer

    def list(self, request):
        serializer = InterviewSessionSerializer(self.get_queryset(), many=True)
        return Response(serializer.data)

    def create(self, request):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        session = InterviewSessionService.create_session(
            candidate=serializer.validated_data["candidate"],
            config=serializer.validated_data["config"],
            created_by=request.user,
            package_code=serializer.validated_data.get("package_code", ""),
        )
        output = InterviewSessionSerializer(session, context={"request": request})
        return Response(output.data, status=status.HTTP_201_CREATED)

    def retrieve(self, request, *args, **kwargs):
        session = self._get_session()
        self._ensure_access(session, request)
        serializer = InterviewSessionSerializer(session, context={"request": request})
        return Response(serializer.data)

    @extend_schema(request=SessionStartSerializer, responses={200: InterviewSessionSerializer})
    @action(detail=True, methods=["post"], url_path="start")
    def start(self, request, id=None):
        session = self._get_session()
        self._ensure_access(session, request)
        serializer = self.get_serializer(data=request.data or {})
        serializer.is_valid(raise_exception=True)
        try:
            session = InterviewSessionService.start_session(session, actor=request.user if request.user.is_authenticated else None)
        except ValueError as exc:
            raise ValidationError({"detail": str(exc)}) from exc
        output = InterviewSessionSerializer(session, context={"request": request})
        return Response(output.data)

    @extend_schema(request=None, responses={200: SessionQuestionSerializer})
    @action(detail=True, methods=["get"], url_path="next-question")
    def next_question(self, request, id=None):
        return self.current_question(request, id=id)

    @extend_schema(request=None, responses={200: SessionQuestionSerializer})
    @action(detail=True, methods=["get"], url_path="current-question")
    def current_question(self, request, id=None):
        session = self._get_session()
        self._ensure_access(session, request)
        try:
            question, completed = InterviewSessionService.get_or_activate_current_question(
                session,
                actor=request.user if request.user.is_authenticated else None,
            )
        except ValueError as exc:
            raise ValidationError({"detail": str(exc)}) from exc
        if completed or question is None:
            return Response({"status": "COMPLETED"})
        serializer = SessionQuestionSerializer(question)
        return Response(serializer.data)

    @extend_schema(request=SessionTokenSerializer, responses={200: QuestionAudioArtifactSerializer})
    @action(detail=True, methods=["post"], url_path="question-audio")
    def question_audio(self, request, id=None):
        session = self._get_session()
        self._ensure_access(session, request)
        try:
            artifact = InterviewVoicePipelineService.get_or_generate_question_audio(
                session=session,
                actor=request.user if request.user.is_authenticated else None,
            )
        except ValueError as exc:
            raise ValidationError({"detail": str(exc)}) from exc
        serializer = QuestionAudioArtifactSerializer(artifact)
        return Response(serializer.data)

    @extend_schema(request=SessionAudioUploadSerializer, responses={200: CandidateResponseSerializer})
    @action(detail=True, methods=["post"], url_path="upload-response-audio")
    def upload_response_audio(self, request, id=None):
        session = self._get_session()
        self._ensure_access(session, request)
        serializer = self.get_serializer(data=request.data, context={"session": session})
        serializer.is_valid(raise_exception=True)
        try:
            response = InterviewVoicePipelineService.upload_response_audio(
                session=session,
                question=serializer.validated_data["question_id"],
                uploaded_file=serializer.validated_data["audio_file"],
                duration_seconds=serializer.validated_data["duration_seconds"],
                actor=request.user if request.user.is_authenticated else None,
            )
        except ValueError as exc:
            raise ValidationError({"detail": str(exc)}) from exc
        output = CandidateResponseSerializer(response, context={"request": request})
        return Response(output.data)

    @extend_schema(request=SessionTranscriptionSerializer, responses={200: CandidateResponseSerializer})
    @action(detail=True, methods=["post"], url_path="transcribe-response")
    def transcribe_response(self, request, id=None):
        session = self._get_session()
        self._ensure_access(session, request)
        serializer = self.get_serializer(data=request.data, context={"session": session})
        serializer.is_valid(raise_exception=True)
        try:
            response = InterviewVoicePipelineService.transcribe_response(
                session=session,
                response=serializer.validated_data["response_id"],
                actor=request.user if request.user.is_authenticated else None,
            )
        except ValueError as exc:
            raise ValidationError({"detail": str(exc)}) from exc
        output = CandidateResponseSerializer(response, context={"request": request})
        return Response(output.data)

    @extend_schema(request=SessionResponseSubmitSerializer, responses={200: dict})
    @action(detail=True, methods=["post"], url_path="submit-response")
    def submit_response(self, request, id=None):
        session = self._get_session()
        self._ensure_access(session, request)
        serializer = self.get_serializer(data=request.data, context={"session": session})
        serializer.is_valid(raise_exception=True)
        question = serializer.validated_data["question_id"]
        try:
            response = InterviewSessionService.submit_response(
                session,
                question,
                actor=request.user if request.user.is_authenticated else None,
                transcript=serializer.validated_data["transcript"],
                text_response=serializer.validated_data.get("text_response", ""),
                duration_seconds=serializer.validated_data.get("duration_seconds", 0),
            )
        except ValueError as exc:
            raise ValidationError({"detail": str(exc)}) from exc
        return Response(
            {
                "status": "SUCCESS",
                "response_id": str(response.public_id),
            }
        )

    @extend_schema(request=SessionTokenSerializer, responses={200: InterviewSessionSerializer})
    @action(detail=True, methods=["post"], url_path="complete")
    def complete(self, request, id=None):
        session = self._get_session()
        self._ensure_access(session, request)
        try:
            session = InterviewSessionService.complete_session(
                session,
                actor=request.user if request.user.is_authenticated else None,
            )
        except ValueError as exc:
            raise ValidationError({"detail": str(exc)}) from exc
        serializer = InterviewSessionSerializer(session, context={"request": request})
        return Response(serializer.data)

    @extend_schema(request=SessionTokenSerializer, responses={200: SessionAIProcessingSummarySerializer})
    @action(detail=True, methods=["get"], url_path="ai-processing-summary")
    def ai_processing_summary(self, request, id=None):
        session = self._get_session()
        self._ensure_access(session, request)
        payload = AIProcessingOrchestrationService.get_session_summary_payload(session)
        serializer = SessionAIProcessingSummarySerializer(payload)
        return Response(serializer.data)

    @extend_schema(request=None, responses={200: SessionPrecheckStatusSerializer})
    @action(detail=True, methods=["get"], url_path="prechecks/status")
    def precheck_status(self, request, id=None):
        session = self._get_session()
        self._ensure_access(session, request)
        payload = InterviewSessionPrecheckService.get_precheck_status_payload(session)
        return Response(SessionPrecheckStatusSerializer(payload).data)

    @extend_schema(request=SessionConsentCaptureSerializer, responses={200: SessionPrecheckStatusSerializer})
    @action(detail=True, methods=["post"], url_path="prechecks/consent")
    def capture_consent(self, request, id=None):
        session = self._get_session()
        self._ensure_access(session, request)
        serializer = self.get_serializer(data=request.data or {})
        serializer.is_valid(raise_exception=True)
        agreement = InterviewSessionPrecheckService.record_candidate_consent(
            session,
            signatory_name=serializer.validated_data["signatory_name"],
            ip_address=self._client_ip(request),
            user_agent=request.META.get("HTTP_USER_AGENT", ""),
            actor=request.user if request.user.is_authenticated else None,
        )
        payload = InterviewSessionPrecheckService.get_precheck_status_payload(session)
        payload["agreement_id"] = str(agreement.public_id)
        return Response(SessionPrecheckStatusSerializer(payload).data)

    @extend_schema(request=SessionPrivacyAcknowledgementSerializer, responses={200: SessionPrecheckStatusSerializer})
    @action(detail=True, methods=["post"], url_path="prechecks/privacy-acknowledgement")
    def acknowledge_privacy(self, request, id=None):
        session = self._get_session()
        self._ensure_access(session, request)
        serializer = self.get_serializer(data=request.data or {})
        serializer.is_valid(raise_exception=True)
        InterviewSessionPrecheckService.record_privacy_acknowledgement(
            session,
            ip_address=self._client_ip(request),
            actor=request.user if request.user.is_authenticated else None,
            metadata=serializer.validated_data.get("metadata", {}),
        )
        payload = InterviewSessionPrecheckService.get_precheck_status_payload(session)
        return Response(SessionPrecheckStatusSerializer(payload).data)

    @extend_schema(request=SessionDeviceCheckSerializer, responses={200: SessionPrecheckStatusSerializer})
    @action(detail=True, methods=["post"], url_path="prechecks/device-check")
    def complete_device_check(self, request, id=None):
        session = self._get_session()
        self._ensure_access(session, request)
        serializer = self.get_serializer(data=request.data or {})
        serializer.is_valid(raise_exception=True)
        try:
            InterviewSessionPrecheckService.record_device_check(
                session,
                passed=serializer.validated_data["passed"],
                actor=request.user if request.user.is_authenticated else None,
                metadata=serializer.validated_data.get("metadata", {}),
            )
        except IdentityVerificationError as exc:
            raise ValidationError({"detail": str(exc)}) from exc
        payload = InterviewSessionPrecheckService.get_precheck_status_payload(session)
        return Response(SessionPrecheckStatusSerializer(payload).data)

    @extend_schema(request=SessionVerbalConfirmationSerializer, responses={200: SessionArtifactSerializer})
    @action(detail=True, methods=["post"], url_path="prechecks/verbal-confirmation")
    def submit_verbal_confirmation(self, request, id=None):
        session = self._get_session()
        self._ensure_access(session, request)
        serializer = self.get_serializer(data=request.data or {})
        serializer.is_valid(raise_exception=True)
        try:
            artifact, _session = InterviewSessionPrecheckService.record_verbal_confirmation(
                session,
                recording_file=serializer.validated_data.get("recording_file"),
                recording_text=serializer.validated_data.get("recording_path", ""),
                actor=request.user if request.user.is_authenticated else None,
                metadata=serializer.validated_data.get("metadata", {}),
            )
        except IdentityVerificationError as exc:
            raise ValidationError({"detail": str(exc)}) from exc
        payload = {
            "precheck_status": InterviewSessionPrecheckService.get_precheck_status_payload(session),
            "artifact": SessionArtifactSerializer(artifact, context={"request": request}).data if artifact else None,
        }
        return Response(payload)

    @extend_schema(request=None, responses={200: bytes})
    @action(detail=True, methods=["get"], url_path="prechecks/reference-image")
    def identity_reference_image(self, request, id=None):
        session = self._get_session()
        self._ensure_access(session, request)
        try:
            reference_file = InterviewSessionPrecheckService.resolve_reference_image_file(session.candidate)
        except IdentityVerificationError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_404_NOT_FOUND)
        InterviewSessionPrecheckService._rewind_file(reference_file)
        content_type = getattr(reference_file, "content_type", None)
        if not content_type:
            content_type, _ = mimetypes.guess_type(getattr(reference_file, "name", "") or "")
            content_type = content_type or "application/octet-stream"
        return FileResponse(reference_file, content_type=content_type)

    @extend_schema(request=SessionIdentityVerificationSerializer, responses={200: dict})
    @action(detail=True, methods=["post"], url_path="prechecks/identity-verify")
    def submit_identity_verification(self, request, id=None):
        session = self._get_session()
        self._ensure_access(session, request)
        serializer = self.get_serializer(data=request.data or {})
        serializer.is_valid(raise_exception=True)
        try:
            verification, artifacts = InterviewSessionPrecheckService.submit_identity_verification(
                session,
                id_document_file=serializer.validated_data.get("id_document_file"),
                selfie_file=serializer.validated_data.get("selfie_image_file"),
                provider_result=serializer.validated_data.get("provider_result"),
                face_match_score=serializer.validated_data.get("face_match_score"),
                single_face_detected=serializer.validated_data.get("single_face_detected"),
                liveness_passed=serializer.validated_data.get("liveness_passed", True),
                metadata=serializer.validated_data.get("metadata", {}),
                actor=request.user if request.user.is_authenticated else None,
            )
        except IdentityVerificationError as exc:
            raise ValidationError({"detail": str(exc)}) from exc
        payload = {
            "precheck_status": InterviewSessionPrecheckService.get_precheck_status_payload(session),
            "verification": {
                "provider": verification["provider"],
                "verification_status": verification["verification_status"],
                "identity_verified": verification["identity_verified"],
                "face_match_score": str(verification["face_match_score"]) if verification["face_match_score"] is not None else None,
                "single_face_detected": verification["single_face_detected"],
                "liveness_passed": verification["liveness_passed"],
                "reason": verification["reason"],
            },
            "artifacts": SessionArtifactSerializer(artifacts, many=True, context={"request": request}).data,
        }
        return Response(payload)

    @extend_schema(request=SessionIntegrityEventSerializer, responses={200: dict})
    @action(detail=True, methods=["post"], url_path="integrity-events")
    def log_integrity_event(self, request, id=None):
        session = self._get_session()
        self._ensure_access(session, request)
        serializer = self.get_serializer(data=request.data or {})
        serializer.is_valid(raise_exception=True)
        log, artifact, analysis = InterviewSessionPrecheckService.ingest_integrity_event(
            session,
            event_type=serializer.validated_data.get("event_type", ""),
            severity=serializer.validated_data.get("severity", "INFO"),
            details=serializer.validated_data.get("details", {}),
            frame_file=serializer.validated_data.get("frame_file"),
            provider_result=serializer.validated_data.get("provider_result"),
            single_face_detected=serializer.validated_data.get("single_face_detected"),
            face_count=serializer.validated_data.get("face_count"),
            liveness_passed=serializer.validated_data.get("liveness_passed", True),
            auto_analyze=serializer.validated_data.get("auto_analyze", False),
            actor=request.user if request.user.is_authenticated else None,
        )
        return Response(
            {
                "status": "RECORDED",
                "integrity_event": {
                    "event_type": log.event_type,
                    "severity": log.severity,
                    "detected_at": log.detected_at,
                    "details": log.details,
                },
                "analysis": {
                    "provider": analysis["provider"],
                    "event_type": analysis["event_type"],
                    "severity": analysis["severity"],
                    "single_face_detected": analysis["single_face_detected"],
                    "face_count": analysis["face_count"],
                    "liveness_passed": analysis["liveness_passed"],
                    "reason": analysis["reason"],
                } if analysis else None,
                "artifact": SessionArtifactSerializer(artifact, context={"request": request}).data if artifact else None,
            }
        )

    @extend_schema(request=None, responses={200: SessionEvaluationSummarySerializer})
    @action(detail=True, methods=["post"], url_path="run-scoring")
    def run_scoring(self, request, id=None):
        session = self._get_session()
        self._ensure_access(session, request)
        evaluation = InterviewSessionService._ensure_linked_evaluation(session)
        try:
            summary = Week6ScoringService.run_for_evaluation(
                evaluation=evaluation,
                actor=request.user if request.user.is_authenticated else None,
            )
        except Week6ScoringError as exc:
            raise ValidationError({"detail": str(exc)}) from exc
        return Response(SessionEvaluationSummarySerializer(summary).data)

    @extend_schema(request=None, responses={200: SessionEvaluationSummarySerializer})
    @action(detail=True, methods=["get"], url_path="scoring-summary")
    def scoring_summary(self, request, id=None):
        session = self._get_session()
        self._ensure_access(session, request)
        summary = session.evaluation_summaries.select_related("rule_set").first()
        if summary is None:
            return Response({"detail": "No scoring summary has been generated yet."}, status=status.HTTP_404_NOT_FOUND)
        return Response(SessionEvaluationSummarySerializer(summary).data)

    @extend_schema(request=SessionTokenSerializer, responses={200: SessionObservedTaskSerializer})
    @action(detail=True, methods=["post"], url_path="tasks/start")
    def start_task(self, request, id=None):
        session = self._get_session()
        self._ensure_access(session, request)
        serializer = self.get_serializer(data=request.data or {})
        serializer.is_valid(raise_exception=True)
        try:
            task = TaskObservationService.start_task(
                session=session,
                actor=request.user if request.user.is_authenticated else None,
            )
        except ValueError as exc:
            raise ValidationError({"detail": str(exc)}) from exc
        return Response(SessionObservedTaskSerializer(task).data)

    @extend_schema(request=None, responses={200: SessionObservedTaskSerializer})
    @action(detail=True, methods=["get"], url_path="tasks/current")
    def current_task(self, request, id=None):
        session = self._get_session()
        self._ensure_access(session, request)
        try:
            task = TaskObservationService.current_task(session=session)
        except ValueError as exc:
            raise ValidationError({"detail": str(exc)}) from exc
        if task is None:
            return Response({"status": "COMPLETED"})
        return Response(SessionObservedTaskSerializer(task).data)

    @extend_schema(request=SessionTaskCompletionSerializer, responses={200: TaskObservationResultSerializer})
    @action(
        detail=True,
        methods=["post"],
        url_path=rf"tasks/(?P<task_id>{PUBLIC_ID_OR_PK_REGEX})/complete",
    )
    def complete_task(self, request, id=None, task_id=None):
        session = self._get_session()
        self._ensure_access(session, request)
        serializer = self.get_serializer(data=request.data or {})
        serializer.is_valid(raise_exception=True)
        try:
            session_task = get_object_or_404(
                SessionObservedTask.objects.select_related("task_definition"),
                **build_object_identifier_filter(task_id),
            )
            result = TaskObservationService.complete_task(
                session=session,
                session_task=session_task,
                execution_time_seconds=serializer.validated_data["execution_time_seconds"],
                observed_steps=serializer.validated_data["observed_steps"],
                review_required=serializer.validated_data.get("review_required", False),
                review_reason=serializer.validated_data.get("review_reason", ""),
                integrity_flags=serializer.validated_data.get("integrity_flags", []),
                result_payload=serializer.validated_data.get("result_payload", {}),
                actor=request.user if request.user.is_authenticated else None,
            )
        except ValueError as exc:
            raise ValidationError({"detail": str(exc)}) from exc
        return Response(TaskObservationResultSerializer(result).data)

    @extend_schema(request=None, responses={200: TaskObservationResultSerializer(many=True)})
    @action(detail=True, methods=["get"], url_path="tasks/results")
    def task_results(self, request, id=None):
        session = self._get_session()
        if not request.user.is_authenticated or not session.can_manage(request.user):
            raise PermissionDenied("You do not have access to these task observation results")
        try:
            results = TaskObservationService.list_results(session=session)
        except ValueError as exc:
            raise ValidationError({"detail": str(exc)}) from exc
        return Response(TaskObservationResultSerializer(results, many=True).data)

    @extend_schema(request=None, responses={200: EvaluationReportSerializer})
    @action(detail=True, methods=["get"], url_path="report")
    def report(self, request, id=None):
        session = self._get_session()
        if not request.user.is_authenticated or not session.can_manage(request.user):
            raise PermissionDenied("You do not have access to this interview report")
        evaluation = InterviewSessionService._ensure_linked_evaluation(session)
        report = evaluation.reports.filter(report_status="ACTIVE").select_related("generated_by").first()
        if report is None:
            return Response({"detail": "No evaluation report has been generated yet."}, status=status.HTTP_404_NOT_FOUND)
        return Response(EvaluationReportSerializer(report).data)

    def _get_session(self):
        queryset = InterviewSession.objects.select_related(
            "candidate",
            "organization",
            "config",
            "created_by",
        ).prefetch_related("questions")
        try:
            lookup = build_object_identifier_filter(self.kwargs["id"])
        except ValueError as exc:
            raise ValidationError({"id": str(exc)}) from exc
        return get_object_or_404(queryset, **lookup)

    def _ensure_access(self, session, request):
        if request.user.is_authenticated and session.can_manage(request.user):
            return
        token = self._get_token(request)
        if session.token_is_valid(token):
            return
        raise PermissionDenied("You do not have access to this interview session")

    def _get_token(self, request):
        return (
            request.headers.get("X-Session-Token")
            or request.query_params.get("token")
            or request.data.get("token")
        )

    def _client_ip(self, request):
        forwarded = request.META.get("HTTP_X_FORWARDED_FOR")
        if forwarded:
            return forwarded.split(",")[0].strip()
        return request.META.get("REMOTE_ADDR")


class ResponseAIProcessingViewSet(viewsets.GenericViewSet):
    lookup_field = "public_id"
    lookup_url_kwarg = "id"
    lookup_value_regex = PUBLIC_ID_OR_PK_REGEX

    def get_permissions(self):
        return [AllowAny()]

    def get_serializer_class(self):
        if self.action == "ai_processing_status":
            return ResponseAIProcessingStatusSerializer
        return ResponseAIActionSerializer

    def get_queryset(self):
        return CandidateResponse.objects.select_related(
            "session",
            "question",
            "question__question_template",
        )

    def _get_response(self):
        try:
            lookup = build_object_identifier_filter(self.kwargs["id"])
        except ValueError as exc:
            raise ValidationError({"id": str(exc)}) from exc
        queryset = self.get_queryset()
        return get_object_or_404(queryset, **lookup)

    def _get_token(self, request):
        return (
            request.headers.get("X-Session-Token")
            or request.query_params.get("token")
            or request.data.get("token")
        )

    def _ensure_access(self, response, request):
        session = response.session
        if request.user.is_authenticated and session.can_manage(request.user):
            return
        token = self._get_token(request)
        if session.token_is_valid(token):
            return
        raise PermissionDenied("You do not have access to this interview response")

    def _serialize_status(self, response):
        payload = AIProcessingOrchestrationService.get_response_status_payload(response)
        serializer = ResponseAIProcessingStatusSerializer(payload)
        return serializer.data

    @extend_schema(request=ResponseAIActionSerializer, responses={200: CandidateResponseSerializer})
    @action(detail=True, methods=["post"], url_path="translate")
    def translate(self, request, id=None):
        response_obj = self._get_response()
        self._ensure_access(response_obj, request)
        serializer = self.get_serializer(data=request.data or {})
        serializer.is_valid(raise_exception=True)
        updated = AIProcessingOrchestrationService.translate_response(
            response=response_obj,
            actor=request.user if request.user.is_authenticated else None,
            force=serializer.validated_data["force"],
            target_override=serializer.validated_data.get("target_language", ""),
            idempotency_key=serializer.validated_data.get("idempotency_key", ""),
        )
        return Response(CandidateResponseSerializer(updated, context={"request": request}).data)

    @extend_schema(request=ResponseAIActionSerializer, responses={200: CandidateResponseSerializer})
    @action(detail=True, methods=["post"], url_path="interpret")
    def interpret(self, request, id=None):
        response_obj = self._get_response()
        self._ensure_access(response_obj, request)
        serializer = self.get_serializer(data=request.data or {})
        serializer.is_valid(raise_exception=True)
        try:
            updated = AIProcessingOrchestrationService.interpret_response(
                response=response_obj,
                actor=request.user if request.user.is_authenticated else None,
                force=serializer.validated_data["force"],
                idempotency_key=serializer.validated_data.get("idempotency_key", ""),
            )
        except AIProcessingError as exc:
            raise ValidationError({"detail": str(exc)}) from exc
        return Response(CandidateResponseSerializer(updated, context={"request": request}).data)

    @extend_schema(request=ResponseAIActionSerializer, responses={200: CandidateResponseSerializer})
    @action(detail=True, methods=["post"], url_path="prepare-evaluation-input")
    def prepare_evaluation_input(self, request, id=None):
        response_obj = self._get_response()
        self._ensure_access(response_obj, request)
        serializer = self.get_serializer(data=request.data or {})
        serializer.is_valid(raise_exception=True)
        try:
            updated = AIProcessingOrchestrationService.prepare_evaluation_input(
                response=response_obj,
                actor=request.user if request.user.is_authenticated else None,
                force=serializer.validated_data["force"],
                idempotency_key=serializer.validated_data.get("idempotency_key", ""),
            )
        except AIProcessingError as exc:
            raise ValidationError({"detail": str(exc)}) from exc
        return Response(CandidateResponseSerializer(updated, context={"request": request}).data)

    @extend_schema(request=ResponseAIActionSerializer, responses={200: CandidateResponseSerializer})
    @action(detail=True, methods=["post"], url_path="process-ai")
    def process_ai(self, request, id=None):
        response_obj = self._get_response()
        self._ensure_access(response_obj, request)
        serializer = self.get_serializer(data=request.data or {})
        serializer.is_valid(raise_exception=True)
        try:
            if serializer.validated_data.get("async_execution", False):
                _, updated = AIProcessingOrchestrationService.queue_process_response_ai(
                    response=response_obj,
                    actor=request.user if request.user.is_authenticated else None,
                    force=serializer.validated_data["force"],
                    target_override=serializer.validated_data.get("target_language", ""),
                    idempotency_key=serializer.validated_data.get("idempotency_key", ""),
                )
            else:
                updated = AIProcessingOrchestrationService.process_response_ai(
                    response=response_obj,
                    actor=request.user if request.user.is_authenticated else None,
                    force=serializer.validated_data["force"],
                    target_override=serializer.validated_data.get("target_language", ""),
                    idempotency_key=serializer.validated_data.get("idempotency_key", ""),
                )
        except AIProcessingError as exc:
            raise ValidationError({"detail": str(exc)}) from exc
        return Response(CandidateResponseSerializer(updated, context={"request": request}).data)

    @extend_schema(request=ResponseAIActionSerializer, responses={200: ResponseAIProcessingStatusSerializer})
    @action(detail=True, methods=["get"], url_path="ai-processing-status")
    def ai_processing_status(self, request, id=None):
        response_obj = self._get_response()
        self._ensure_access(response_obj, request)
        return Response(self._serialize_status(response_obj))
