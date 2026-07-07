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
from api.sessions.models import CandidateResponse, InterviewSession
from api.sessions.services import InterviewSessionService, InterviewVoicePipelineService
from api.translation.services import AIProcessingError, AIProcessingOrchestrationService

from .serializers import (
    CandidateResponseSerializer,
    InterviewConfigurationSerializer,
    InterviewSessionCreateSerializer,
    InterviewSessionSerializer,
    InterviewRubricSerializer,
    PackageSessionConfigSerializer,
    QuestionAudioArtifactSerializer,
    QuestionTemplateSerializer,
    RolePackageCoverageSerializer,
    ResponseAIActionSerializer,
    ResponseAIProcessingStatusSerializer,
    SessionAIProcessingSummarySerializer,
    SessionAudioUploadSerializer,
    SessionQuestionSerializer,
    SessionResponseSubmitSerializer,
    SessionStartSerializer,
    SessionTranscriptionSerializer,
    SessionTokenSerializer,
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
