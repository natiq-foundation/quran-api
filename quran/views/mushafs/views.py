from rest_framework import permissions, viewsets, status, filters
from rest_framework.response import Response
from django_filters.rest_framework import DjangoFilterBackend
from django.db.models import Q
from rest_framework.decorators import action
from rest_framework.parsers import MultiPartParser, FormParser
from rest_framework.exceptions import NotFound
from drf_spectacular.utils import extend_schema, extend_schema_view
from core import permissions as core_permissions
from core.pagination import CustomLimitOffsetPagination
from core.utils import validate_uuid
from quran.models import RasmOlMushaf, Ayah, AyahTranslation, Transmission
from quran.serializers import MushafSerializer, TransmissionSerializer

import json


@extend_schema_view(
    list=extend_schema(
        summary="List all Mushafs (Quranic manuscripts/editions)",
        tags=["general", "mushafs"],
    ),
    retrieve=extend_schema(
        summary="Retrieve a specific Mushaf by id", tags=["general", "mushafs"]
    ),
    create=extend_schema(summary="Create a new Mushaf record"),
    update=extend_schema(summary="Update an existing Mushaf record"),
    partial_update=extend_schema(summary="Partially update a Mushaf record"),
    destroy=extend_schema(summary="Delete a Mushaf record"),
)
class MushafViewSet(viewsets.ModelViewSet):
    queryset = RasmOlMushaf.objects.all().order_by("slug")
    serializer_class = MushafSerializer
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
    search_fields = ["slug", "name", "source"]
    ordering_fields = ["created_at"]
    pagination_class = CustomLimitOffsetPagination
    limited_fields = {"status": ["published"]}
    lookup_field = "id"

    def get_queryset(self):
        query = RasmOlMushaf.objects.all().order_by("slug")
        if not self.request.user.is_authenticated:
            query = query.exclude(Q(status="draft") | Q(status="pending_review"))

        if getattr(self, "action", None) == "list":
            query = query.only("id", "slug", "name", "status")

        return query

    def perform_create(self, serializer):
        serializer.save(creator=self.request.user)

    def update(self, request, *args, **kwargs):
        partial = kwargs.pop("partial", False)
        instance = self.get_object()
        if instance.status == "published" and not request.user.is_staff:
            return Response(
                {"detail": "Published Mushaf cannot be edited."},
                status=status.HTTP_403_FORBIDDEN,
            )
        status_value = request.data.get("status")
        if status_value == "pending_review":
            ayah_count = Ayah.objects.filter(surah__mushaf=instance).count()
            ayah_translation_count = AyahTranslation.objects.filter(
                translation__mushaf=instance
            ).count()
            if ayah_translation_count != ayah_count:
                return Response(
                    {
                        "detail": f"Mushaf is incomplete: {ayah_translation_count} of {ayah_count} ayahs translated."
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )
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
                        "description": "JSON file containing the Mushaf data",
                    }
                },
                "required": ["file"],
            }
        },
        summary="Import a Mushaf from a JSON file upload",
    )
    @action(
        detail=False,
        methods=["post"],
        url_path="import",
        parser_classes=[MultiPartParser, FormParser],
        permission_classes=[permissions.IsAdminUser],
    )
    def import_mushaf(self, request):
        MUSHAF_UPLOAD_MAX_SIZE = 30 * 1024 * 1024
        file = request.FILES.get("file")
        if not file:
            return Response(
                {"detail": "No file uploaded."}, status=status.HTTP_400_BAD_REQUEST
            )
        if file.size > MUSHAF_UPLOAD_MAX_SIZE:
            return Response(
                {
                    "error": f"File size exceeds the maximum allowed for mushaf import ({MUSHAF_UPLOAD_MAX_SIZE} bytes, got {file.size} bytes)."
                },
                status=400,
            )
        if not file.name.lower().endswith(".json"):
            return Response(
                {"detail": "Only JSON files are allowed."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            quran_data = json.load(file)
            user = request.user
            from quran.tasks import import_mushaf_task

            import_mushaf_task.delay(quran_data, user.id)
            return Response(
                {
                    "detail": "Mushaf import started. You will be notified when it is complete."
                },
                status=status.HTTP_202_ACCEPTED,
            )
        except Exception as e:
            return Response({"detail": str(e)}, status=status.HTTP_400_BAD_REQUEST)

    @action(
        detail=True,
        methods=["get", "post"],
        url_path="transmissions",
    )
    def get_transmissions(self, request, *args, **kwargs):
        mushaf: RasmOlMushaf = self.get_object()

        if request.method.lower() == "get":
            transmissions = TransmissionSerializer(mushaf.transmission.all(), many=True)
            return Response(transmissions.data)

        new_transmission = request.data

        serializer = TransmissionSerializer(data=new_transmission)
        serializer.is_valid(raise_exception=True)
        name = serializer.validated_data.get("name")
        slug = serializer.validated_data.get("slug")

        trans, created = Transmission.objects.update_or_create(
            rasm_ol_mushaf=mushaf,
            name=name,
            slug=slug,
            creator=request.user,
        )

        return Response(TransmissionSerializer(trans).data)

    @action(
        detail=True,
        methods=["get", "post", "delete"],
        url_path="transmissions/(?P<transmission_id>[^/.]+)",
    )
    def get_or_edit_or_delete_transmission(self, request, *args, **kwargs):
        mushaf: RasmOlMushaf = self.get_object()
        transmission_id = kwargs.get("transmission_id")
        validate_uuid(transmission_id)

        transmission = Transmission.objects.filter(id=transmission_id).first()
        if transmission is None:
            raise NotFound("Transmission with this id doesn't exists!")

        if request.method.lower() == "get":
            serializer = TransmissionSerializer(transmission)
            return Response(serializer.data)

        if request.method.lower() == "delete":
            transmission.delete()
            return Response({"status": "Deleted"})

        new_transmission = request.data

        serializer = TransmissionSerializer(data=new_transmission)
        serializer.is_valid(raise_exception=True)
        name = serializer.validated_data.get("name")
        slug = serializer.validated_data.get("slug")

        trans = Transmission.objects.create(
            rasm_ol_mushaf=mushaf,
            name=name,
            slug=slug,
            creator=request.user,
        )

        return Response(TransmissionSerializer(trans).data)
