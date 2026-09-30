from rest_framework import permissions, viewsets, status, filters, serializers
from rest_framework.response import Response
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import (
    extend_schema,
    extend_schema_view,
    OpenApiParameter,
    OpenApiTypes,
)

from core import permissions as core_permissions
from core.pagination import CustomLimitOffsetPagination
from quran.models import Ayah, Word
from quran.serializers import WordSerializer


@extend_schema_view(
    list=extend_schema(
        summary="List all Words in Ayahs",
        parameters=[
            OpenApiParameter(
                name="ayah_id",
                type=OpenApiTypes.UUID,
                location=OpenApiParameter.QUERY,
                required=False,
                description="id of the Ayah to filter Words by.",
            )
        ],
    ),
    retrieve=extend_schema(summary="Retrieve a specific Word by id"),
    create=extend_schema(
        summary="Create a new Word record",
        parameters=[
            OpenApiParameter(
                name="ayah_id",
                type=OpenApiTypes.UUID,
                location=OpenApiParameter.QUERY,
                required=False,
                description="id of the Ayah to associate the new Word with (if ayah_id is not provided in the body).",
            )
        ],
    ),
    update=extend_schema(summary="Update an existing Word record"),
    partial_update=extend_schema(summary="Partially update a Word record"),
    destroy=extend_schema(summary="Delete a Word record"),
)
class WordViewSet(viewsets.ModelViewSet):
    queryset = Word.objects.all()
    serializer_class = WordSerializer
    permission_classes = [
        core_permissions.IsCreatorOrReadOnly,
        core_permissions.IsCreatorOfParentOrReadOnly,
        permissions.IsAuthenticatedOrReadOnly | permissions.DjangoModelPermissions,
    ]
    filter_backends = [
        DjangoFilterBackend,
        filters.SearchFilter,
        filters.OrderingFilter,
    ]
    search_fields = ["text"]
    ordering_fields = ["created_at"]
    pagination_class = CustomLimitOffsetPagination
    lookup_field = "id"

    def get_parent_for_permission(self, request):
        ayah_id = request.data.get("ayah_id", None)
        if ayah_id:
            return Ayah.objects.filter(id=ayah_id).first()
        return None

    def get_queryset(self):
        word_fields = ["id", "ayah", "word_texts", "creator"]
        queryset = Word.objects.select_related("ayah").only(*word_fields)
        ayah_id = self.request.query_params.get("ayah_id", None)
        if ayah_id is not None:
            queryset = queryset.filter(ayah__id=ayah_id)
        return queryset

    def create(self, request, *args, **kwargs):
        data = request.data.copy()
        if not data.get("ayah_id"):
            ayah_id = request.query_params.get("ayah_id")
            if ayah_id:
                data["ayah_id"] = ayah_id
        serializer = self.get_serializer(data=data)
        serializer.is_valid(raise_exception=True)
        self.perform_create(serializer)
        headers = self.get_success_headers(serializer.data)
        return Response(
            serializer.data, status=status.HTTP_201_CREATED, headers=headers
        )

    def perform_create(self, serializer):
        serializer.save(creator=self.request.user)
