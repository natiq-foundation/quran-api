from rest_framework import permissions, viewsets, status, filters, serializers
from rest_framework.response import Response
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework.decorators import action
from rest_framework.parsers import MultiPartParser, FormParser
from drf_spectacular.utils import (
    extend_schema,
    extend_schema_view,
    OpenApiParameter,
    OpenApiExample,
    OpenApiTypes,
)
from django.conf.global_settings import LANGUAGES
from django.db.models import Q
from core import permissions as core_permissions
from core.pagination import CustomLimitOffsetPagination
from quran.models import Translation, Ayah, AyahTranslation
from quran.serializers import (
    TranslationSerializer,
    TranslationListSerializer,
    TranslationCreateSerializer,
    TranslationUpdateSerializer,
    AyahTranslationSerializer,
    AyahTranslationNestedSerializer,
)

import json


@extend_schema_view(
    list=extend_schema(
        summary="List all Quran Translations",
        parameters=[
            OpenApiParameter(
                name="rasm_ol_mushaf",
                type={"type": "string", "enum": ["hafs"]},
                location=OpenApiParameter.QUERY,
                required=True,
                description="Slug of the Rasm Ol Mushaf to filter Translations by. Common value: 'hafs'. Any string is accepted. (e.g. 'hafs', 'warsh', etc.)",
                examples=[OpenApiExample("hafs", value="hafs", summary="Most common")],
            ),
            OpenApiParameter(
                name="language",
                type={"type": "string", "enum": [code for code, name in LANGUAGES]},
                location=OpenApiParameter.QUERY,
                required=False,
                description="Language code to filter Translations by.",
            ),
        ],
        tags=["general", "translations"],
        responses={200: TranslationListSerializer(many=True)},
    ),
    retrieve=extend_schema(
        summary="Retrieve a specific Translation by id",
        tags=["general", "translations"],
    ),
    create=extend_schema(summary="Create a new Translation record"),
    update=extend_schema(summary="Update an existing Translation record"),
    partial_update=extend_schema(summary="Partially update a Translation record"),
    destroy=extend_schema(summary="Delete a Translation record"),
)
class TranslationViewSet(viewsets.ModelViewSet):
    queryset = Translation.objects.all()
    serializer_class = TranslationSerializer
    permission_classes = [
        core_permissions.IsCreatorOrReadOnly,
        permissions.IsAuthenticatedOrReadOnly | permissions.DjangoModelPermissions,
        core_permissions.LimitedFieldEditPermission,
    ]
    filter_backends = [
        DjangoFilterBackend,
        filters.SearchFilter,
        filters.OrderingFilter,
    ]
    search_fields = ["text"]
    ordering_fields = ["created_at"]
    pagination_class = CustomLimitOffsetPagination
    limited_fields = {"status": ["published"], "is_primary": [True, False]}
    lookup_field = "id"

    def get_queryset(self):
        translation_fields = [
            "id",
            "transmission",
            "translator",
            "language",
            "release_date",
            "source",
            "status",
            "creator",
        ]
        queryset = Translation.objects.select_related(
            "transmission", "transmission__rasm_ol_mushaf", "translator"
        ).only(*translation_fields)
        rasm_ol_mushaf_slug = self.request.query_params.get("rasm_ol_mushaf")
        if self.action == "list" and not rasm_ol_mushaf_slug:
            raise serializers.ValidationError(
                {"rasm_ol_mushaf": "This query parameter is required."}
            )

        if not self.request.user.is_authenticated:
            queryset = queryset.exclude(Q(status="draft") | Q(status="pending_review"))

        if rasm_ol_mushaf_slug:
            queryset = queryset.filter(
                transmission__rasm_ol_mushaf__slug=rasm_ol_mushaf_slug
            )
        language = self.request.query_params.get("language", None)
        if language is not None:
            queryset = queryset.filter(language=language)
        return queryset

    def get_serializer_class(self):
        if self.action == "list":
            return TranslationListSerializer
        if self.action == "create":
            return TranslationCreateSerializer
        if self.action == "update":
            return TranslationUpdateSerializer
        return TranslationSerializer

    def perform_create(self, serializer):
        serializer.save(creator=self.request.user)

    def update(self, request, *args, **kwargs):
        partial = kwargs.pop("partial", False)
        instance = self.get_object()
        if instance.status == "published" and not request.user.is_staff:
            return Response(
                {"detail": "Published Translation cannot be edited."},
                status=status.HTTP_403_FORBIDDEN,
            )
        status_value = request.data.get("status")
        if status_value == "pending_review":
            ayah_count = Ayah.objects.filter(
                surah__rasm_ol_mushaf=instance.transmission.rasm_ol_mushaf
            ).count()
            ayah_translation_count = AyahTranslation.objects.filter(
                translation=instance
            ).count()
            if ayah_translation_count != ayah_count:
                return Response(
                    {
                        "detail": f"Translation is incomplete: {ayah_translation_count} of {ayah_count} ayahs translated."
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )
        is_primary_value = request.data.get("is_primary")
        language = request.data.get("language")

        if is_primary_value == True:
            prev_primary = Translation.objects.get(is_primary=True, language=language)
            prev_primary.is_primary = False
            prev_primary.save()

        return super().update(request, *args, partial=partial, **kwargs)

    def partial_update(self, request, *args, **kwargs):
        return self.update(request, *args, partial=True, **kwargs)

    @extend_schema(
        request={
            "multipart/form-data": {
                "type": "object",
                "properties": {
                    "file": {
                        "type": "string",
                        "format": "binary",
                        "description": "JSON file containing the Translation data",
                    }
                },
                "required": ["file"],
            }
        },
        summary="Import a Translation from a JSON file upload",
    )
    @action(
        detail=False,
        methods=["post"],
        url_path="import",
        parser_classes=[MultiPartParser, FormParser],
    )
    def import_translation(self, request):
        TRANSLATION_UPLOAD_MAX_SIZE = 30 * 1024 * 1024
        file = request.FILES.get("file")
        if file.size > TRANSLATION_UPLOAD_MAX_SIZE:
            return Response(
                {
                    "error": f"File size exceeds the maximum allowed for translation import ({TRANSLATION_UPLOAD_MAX_SIZE} bytes, got {file.size} bytes)."
                },
                status=400,
            )
        if not file:
            return Response(
                {"detail": "No file uploaded."}, status=status.HTTP_400_BAD_REQUEST
            )
        if not file.name.lower().endswith(".json"):
            return Response(
                {"detail": "Only JSON files are allowed."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            translation_data = json.load(file)
            user = request.user
            from quran.tasks import import_translation_task

            import_translation_task.delay(translation_data, user.id)
            return Response(
                {
                    "detail": "Translation import started. You will be notified when it is complete."
                },
                status=status.HTTP_202_ACCEPTED,
            )
        except Exception as e:
            return Response({"detail": str(e)}, status=status.HTTP_400_BAD_REQUEST)

    def retrieve(self, request, *args, **kwargs):
        instance = self.get_object()
        serializer = self.get_serializer(instance)
        data = serializer.data
        return Response(data)

    @extend_schema(
        summary="List all AyahTranslations for this Translation",
        description=(
            "Returns a paginated list of all AyahTranslation objects for the given Translation id. Optionally filter by surah_id (query param)."
        ),
        parameters=[
            OpenApiParameter(
                name="surah_id",
                type=OpenApiTypes.UUID,
                location=OpenApiParameter.QUERY,
                required=False,
                description="id of the Surah to filter AyahTranslations by.",
            )
        ],
        responses={200: AyahTranslationSerializer(many=True)},
    )
    @action(detail=True, methods=["get"], url_path="ayahs")
    def ayahs(self, request, *args, **kwargs):
        translation = self.get_object()
        # Ensure ayahs are aligned with canonical ayah ordering within the same rasm ol mushaf
        ayah_translations = (
            translation.ayah_translations.select_related("ayah", "ayah__surah")
            .filter(
                ayah__surah__rom_surah_ayahs_surah__rasm_ol_mushaf=translation.transmission.rasm_ol_mushaf
            )
            .order_by("ayah__surah__number", "ayah__number", "ayah__id")
            .distinct()
        )
        surah_id = request.query_params.get("surah_id")
        if surah_id:
            ayah_translations = ayah_translations.filter(
                ayah__surah__id=surah_id
            ).order_by("ayah__number", "ayah__id")
        paginator = CustomLimitOffsetPagination()
        page = paginator.paginate_queryset(ayah_translations, request)

        def process_bismillah(data):
            found_first = False
            for item in data:
                if not found_first:
                    found_first = True
                else:
                    item["bismillah"] = None
            return data

        if page is not None:
            serializer = AyahTranslationNestedSerializer(page, many=True)
            processed = process_bismillah(serializer.data)
            return paginator.get_paginated_response(processed)
        serializer = AyahTranslationNestedSerializer(ayah_translations, many=True)
        processed = process_bismillah(serializer.data)
        return Response(processed)

    @extend_schema(
        summary="Retrieve a single AyahTranslation for this Translation",
        description=(
            "Returns a single AyahTranslation object for the given Translation id and Ayah id. URL: /translations/{translation_id}/ayahs/{ayah_id}/"
        ),
        methods=["GET"],
        responses={200: AyahTranslationSerializer},
    )
    @action(detail=True, methods=["get"], url_path="ayahs/(?P<ayah_id>[^/.]+)")
    def get_ayah_translation(self, request, *args, **kwargs):
        translation: Translation = self.get_object()
        ayah_id = kwargs.get("ayah_id")
        try:
            ayah_translation = translation.ayah_translations.select_related(
                "ayah", "ayah__surah"
            ).get(ayah__id=ayah_id)
        except AyahTranslation.DoesNotExist:
            return Response(
                {"detail": "AyahTranslation not found for this translation and ayah."},
                status=status.HTTP_404_NOT_FOUND,
            )
        serializer = AyahTranslationSerializer(ayah_translation)
        return Response(serializer.data)

    @extend_schema(
        summary="Create or update (upsert) a specific AyahTranslation",
        description=(
            "Provide the ayah's id in the URL path and the translation's id as the primary resource path. Body requires only `text` (and optional `bismillah`). If an AyahTranslation already exists it will be updated, otherwise it will be created."
        ),
        request={
            "application/json": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "bismillah": {"type": "boolean"},
                },
                "required": ["text"],
            }
        },
        methods=["PUT", "POST"],
        responses={201: AyahTranslationSerializer, 200: AyahTranslationSerializer},
    )
    @action(detail=True, methods=["put", "post"], url_path="ayahs/(?P<ayah_id>[^/.]+)")
    def modify_ayah_translation(self, request, *args, **kwargs):
        translation: Translation = self.get_object()
        ayah_id = kwargs.get("ayah_id")
        serializer = AyahTranslationSerializer(
            data=request.data, context={"request": request}
        )
        serializer.is_valid(raise_exception=True)
        text = serializer.validated_data.get("text")
        bismillah = serializer.validated_data.get("bismillah", None)
        ayah_translation, created = AyahTranslation.objects.update_or_create(
            ayah__id=ayah_id,
            translation=translation,
            defaults={"text": text, "bismillah": bismillah, "creator": request.user},
        )
        output_serializer = AyahTranslationSerializer(ayah_translation)
        return Response(
            output_serializer.data,
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )
