from rest_framework import permissions, viewsets, status, filters, serializers
from rest_framework.response import Response
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import (
    extend_schema,
    extend_schema_view,
    OpenApiParameter,
    OpenApiExample,
)
from rest_framework.decorators import action
from core import permissions as core_permissions
from core.pagination import CustomLimitOffsetPagination
from quran.models import RasmOlMushaf, Surah, SurahName
from quran.serializers import (
    SurahSerializer,
    SurahDetailSerializer,
    SurahNameSerializer,
)


@extend_schema_view(
    list=extend_schema(
        summary="List all Surahs (Quran chapters)",
        parameters=[
            OpenApiParameter(
                name="rasm_ol_mushaf",
                type={"type": "string", "enum": ["u"]},
                location=OpenApiParameter.QUERY,
                required=True,
                description="Slug of the Rasm Ol Mushaf to filter Surahs by. Common value: 'hafs'. Any string is accepted. (e.g. 'hafs', 'warsh', etc.)",
                examples=[OpenApiExample("hafs", value="hafs", summary="Most common")],
            )
        ],
        tags=["general", "surahs"],
    ),
    retrieve=extend_schema(
        summary="Retrieve a specific Surah by id", tags=["general", "surahs"]
    ),
    create=extend_schema(summary="Create a new Surah record"),
    update=extend_schema(summary="Update an existing Surah record"),
    partial_update=extend_schema(summary="Partially update a Surah record"),
    destroy=extend_schema(summary="Delete a Surah record"),
)
class SurahViewSet(viewsets.ModelViewSet):
    queryset = Surah.objects.all().order_by("number")
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
    search_fields = ["name"]
    ordering_fields = ["created_at"]
    pagination_class = CustomLimitOffsetPagination
    lookup_field = "id"

    def get_parent_for_permission(self, request):
        rasm_ol_mushaf_id = request.data.get("rasm_ol_mushaf_id", None)
        if rasm_ol_mushaf_id:
            return RasmOlMushaf.objects.filter(id=rasm_ol_mushaf_id).first()
        return None

    def get_serializer_class(self):
        if self.action == "retrieve":
            return SurahDetailSerializer
        return SurahSerializer

    def get_queryset(self):
        surah_fields = [
            "id",
            "rasm_ol_mushafs",
            "number",
            "period",
            "search_terms",
            "creator",
        ]
        mushaf_slug = self.request.query_params.get("rasm_ol_mushaf")
        queryset = Surah.objects.all()
        if self.action == "retrieve":
            queryset = queryset.prefetch_related(
                "rasm_ol_mushafs",
                "ayahs__words",
            ).only(*surah_fields)
        else:
            queryset = queryset.prefetch_related("rasm_ol_mushafs").only(*surah_fields)
        mushaf_slug = self.request.query_params.get("rasm_ol_mushaf")
        if self.action == "list" and not mushaf_slug:
            raise serializers.ValidationError(
                {"rasm_ol_mushaf": "This query parameter is required."}
            )
        if mushaf_slug:
            queryset = queryset.filter(rasm_ol_mushafs__slug=mushaf_slug)
        return queryset.order_by("number")

    def perform_create(self, serializer):
        last_surah = Surah.objects.order_by("-number").first()
        next_number = 1 if last_surah is None else last_surah.number + 1
        serializer.save(creator=self.request.user, number=next_number)

    @action(detail=True, methods=["post", "get"], url_path="names")
    def names(self, request, *args, **kwargs):
        surah: Surah = self.get_object()
        new_surah_name = request.data

        if request.method.lower() == "get":
            serializer = SurahNameSerializer(surah.names.all(), many=True)
            return Response(serializer.data)

        serializer = SurahNameSerializer(data=(new_surah_name | {"surah": surah.id}))
        serializer.is_valid(raise_exception=True)
        name = serializer.validated_data.get("name")
        pron = serializer.validated_data.get("pronunciation")
        translit = serializer.validated_data.get("transliteration")
        translation = serializer.validated_data.get("translation")

        surah_name, created = SurahName.objects.update_or_create(
            name=name,
            pronunciation=pron,
            transliteration=translit,
            translation=translation,
            surah=surah,
        )

        return Response({"status": "created"})

    @action(
        detail=True, methods=["post", "delete"], url_path="names/(?P<name_id>[^/.]+)"
    )
    def edit_name(self, request, *args, **kwargs):
        surah: Surah = self.get_object()
        name_id = kwargs.get("name_id")
        new_surah_name = request.data

        if request.method.lower() == "delete":
            surah_name = SurahName.objects.get(id=name_id)
            surah_name.delete()
            return Response({"status": "Removed"})

        serializer = SurahNameSerializer(data=(new_surah_name | {"surah": surah.id}))
        serializer.is_valid(raise_exception=True)
        name = serializer.validated_data.get("name")
        pron = serializer.validated_data.get("pronunciation")
        translit = serializer.validated_data.get("transliteration")
        translation = serializer.validated_data.get("translation")

        surah_name, created = SurahName.objects.update_or_create(
            id=name_id,
            defaults={
                "name": name,
                "pronunciation": pron,
                "transliteration": translit,
                "translation": translation,
                "surah": surah,
            },
        )

        return Response({"status": "updated"})
