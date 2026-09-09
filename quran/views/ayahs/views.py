from rest_framework import permissions, viewsets, status, filters, serializers
from rest_framework.decorators import action
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
from quran.models import Surah, Ayah
from quran.serializers import AyahSerializer, AyahSerializerView, AyahAddSerializer
from django.db.models import Max, Min
import random


@extend_schema_view(
    list=extend_schema(
        summary="List all Ayahs (Quran verses)",
        parameters=[
            OpenApiParameter("surah_id", OpenApiTypes.UUID, OpenApiParameter.QUERY),
            OpenApiParameter(
                "include_surah", OpenApiTypes.BOOL, OpenApiParameter.QUERY
            ),
        ],
    ),
    retrieve=extend_schema(summary="Retrieve a specific Ayah by id"),
    create=extend_schema(summary="Create a new Ayah record"),
    update=extend_schema(summary="Update an existing Ayah record"),
    partial_update=extend_schema(summary="Partially update an Ayah record"),
    destroy=extend_schema(summary="Delete an Ayah record"),
)
class AyahViewSet(viewsets.ModelViewSet):
    queryset = Ayah.objects.all().order_by("surah__number", "number")
    serializer_class = AyahSerializer
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
    filterset_fields = ["number"]
    search_fields = ["number", "text"]
    ordering_fields = ["created_at"]
    pagination_class = CustomLimitOffsetPagination
    lookup_field = "id"

    def get_parent_for_permission(self, request):
        surah_id = request.data.get("surah_id", None)
        if surah_id:
            return Surah.objects.filter(id=surah_id).first()
        return None

    def get_queryset(self):
        ayah_fields = [
            "id",
            "surah",
            "number",
            "sajdah",
            "is_bismillah",
            "creator",
            "length",
        ]
        queryset = super().get_queryset()
        if self.action == "retrieve":
            queryset = (
                queryset.select_related("surah", "surah__rasm_ol_mushaf")
                .prefetch_related("words")
                .only(*ayah_fields)
            )
        else:
            queryset = (
                queryset.select_related("surah")
                .prefetch_related("words")
                .only(*ayah_fields)
            )

        surah_id = self.request.query_params.get("surah_id", None)
        if surah_id is not None:
            queryset = queryset.filter(surah__id=surah_id)

        surah_number = self.request.query_params.get("surah_number", None)
        if surah_number is not None:
            queryset = queryset.filter(surah__number=surah_number)

        return queryset

    @extend_schema(
        summary="Get random Ayah(s)",
        parameters=[
            OpenApiParameter(
                name="count",
                type=OpenApiTypes.INT,
                location=OpenApiParameter.QUERY,
                required=False,
                description="Number of random ayahs to return. Defaults to 1.",
            )
        ],
        responses={200: AyahSerializerView(many=True)},
    )
    @action(detail=False, methods=["get"], url_path="random")
    def random(self, request, *args, **kwargs):
        ayah_ids = list(Ayah.objects.order_by("id").values_list("id", flat=True))
        total = len(ayah_ids)
        if total == 0:
            return Response(
                {"detail": "No Ayah available."}, status=status.HTTP_404_NOT_FOUND
            )

        count_param = request.query_params.get("count")
        if count_param is None:
            count = 1
        else:
            try:
                count = int(count_param)
            except (TypeError, ValueError):
                return Response(
                    {"detail": "count must be a positive integer."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if count < 1:
                return Response(
                    {"detail": "count must be a positive integer."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        count = min(count, total)
        selected_ids = random.sample(ayah_ids, count)
        ayahs_by_id = {
            ayah.id: ayah
            for ayah in Ayah.objects.filter(id__in=selected_ids)
            .select_related("surah", "surah__rasm_ol_mushaf")
            .prefetch_related("words")
        }
        random_ayahs = [ayahs_by_id[ayah_id] for ayah_id in selected_ids]

        ayah_serializer = AyahSerializerView(
            random_ayahs, many=True, context=self.get_serializer_context()
        )
        return Response(ayah_serializer.data)

    def get_serializer_context(self):
        context = super().get_serializer_context()
        text_format = self.request.query_params.get("text_format", "text")
        if text_format not in ["text", "word"]:
            text_format = "text"
        context["text_format"] = text_format
        return context

    def get_serializer_class(self):
        if self.action == "retrieve":
            return AyahSerializerView
        if self.action == "create":
            return AyahAddSerializer
        return AyahSerializer

    def perform_create(self, serializer):
        serializer.save(creator=self.request.user)
