from collections import OrderedDict

from rest_framework import viewsets
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from api.core.permisssions import IsAdminOrSuperAdmin
from api.core.public_ids import PublicIdLookupMixin

from .models import KnowledgeEntry
from .serializers import KnowledgeEntryAdminSerializer
from .services import KnowledgeContentService


class PublicKnowledgeBaseView(APIView):
    """GET /api/v1/knowledge/faq - the only unauthenticated entry point
    into this app. Server-side enforced: the query below is the single
    place that decides what the public may see, and it is deliberately
    narrow (is_current + Approved + Public) rather than trusting whatever
    happens to be in the database - Draft, Archived, Partner, Authenticated
    and Internal rows can never reach this response no matter what a
    caller sends, since there is no way to pass different filters in."""

    permission_classes = [AllowAny]

    def get(self, request):
        language = "ar" if (request.query_params.get("language") or "").lower().startswith("ar") else "en"

        entries = KnowledgeEntry.objects.filter(
            is_current=True,
            status=KnowledgeEntry.Status.APPROVED,
            visibility=KnowledgeEntry.Visibility.PUBLIC,
        ).order_by("category_order", "sequence", "question_id")

        categories = OrderedDict()
        for entry in entries:
            category_title = entry.category_ar if language == "ar" and entry.category_ar else entry.category
            bucket = categories.setdefault(
                entry.category_id,
                {"id": entry.category_id, "title": category_title, "items": []},
            )
            question = entry.question_ar if language == "ar" and entry.question_ar else entry.question_en
            answer = entry.short_answer_ar if language == "ar" and entry.short_answer_ar else entry.short_answer_en
            bucket["items"].append({"id": entry.question_id, "question": question, "answer": answer})

        return Response({"categories": list(categories.values())})


class KnowledgeEntryViewSet(PublicIdLookupMixin, viewsets.ModelViewSet):
    """Authenticated content-management API - Admin/Super Admin only.
    Every write goes through KnowledgeContentService so history is
    preserved (see services.py); there is no path here that mutates a row
    in place or hard-deletes one."""

    permission_classes = [IsAuthenticated, IsAdminOrSuperAdmin]
    serializer_class = KnowledgeEntryAdminSerializer

    def get_queryset(self):
        queryset = KnowledgeEntry.objects.all().order_by("-created_at")
        question_id = self.request.query_params.get("question_id")
        if question_id:
            queryset = queryset.filter(question_id=question_id)
        is_current = self.request.query_params.get("is_current")
        if is_current is not None:
            queryset = queryset.filter(is_current=is_current.lower() in {"1", "true", "yes"})
        return queryset

    def perform_create(self, serializer):
        entry = KnowledgeContentService.create_new_version(
            actor=self.request.user,
            **serializer.validated_data,
        )
        serializer.instance = entry

    def perform_update(self, serializer):
        entry = KnowledgeContentService.create_new_version(
            actor=self.request.user,
            previous=serializer.instance,
            **serializer.validated_data,
        )
        serializer.instance = entry

    def perform_destroy(self, instance):
        KnowledgeContentService.archive(instance, actor=self.request.user)
