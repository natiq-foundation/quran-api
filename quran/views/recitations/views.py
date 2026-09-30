from rest_framework import permissions, viewsets, status, filters, serializers
from rest_framework.response import Response
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework.decorators import action
from rest_framework.parsers import MultiPartParser, FormParser
from drf_spectacular.utils import (
    extend_schema,
    extend_schema_view,
    OpenApiParameter,
    OpenApiTypes,
    OpenApiExample,
)
from django.db.models import Q
from core import permissions as core_permissions
from core.pagination import CustomLimitOffsetPagination
from quran.models import (
    Recitation,
    Surah,
    Ayah,
    AyahTranslation,
    RecitationSurah,
    RecitationSurahTimestamp,
    Word,
)
from quran.serializers import (
    RecitationSerializer,
    RecitationListSerializer,
    TrackDetailSerializer,
)
from mutagen.mp3 import MP3
from datetime import timedelta


@extend_schema_view(
    list=extend_schema(
        summary="List all Recitations (audio recordings)",
        parameters=[
            OpenApiParameter(
                name="rasm_ol_mushaf",
                type={"type": "string", "enum": ["hafs"]},
                location=OpenApiParameter.QUERY,
                required=True,
                description="Short name of the Mushaf to filter Recitations by. Common value: 'hafs'. Any string is accepted. (e.g. 'hafs', 'warsh', etc.)",
                examples=[OpenApiExample("u333", value="u333", summary="Most common")],
            ),
            OpenApiParameter(
                name="reciter_id",
                type=OpenApiTypes.UUID,
                location=OpenApiParameter.QUERY,
                required=False,
                description="id of the Reciter to filter Recitations by.",
            ),
        ],
        responses={200: RecitationListSerializer(many=True)},
    ),
    retrieve=extend_schema(
        summary="Retrieve a specific Recitation by id",
        parameters=[
            OpenApiParameter(
                name="surah_id",
                type=OpenApiTypes.UUID,
                location=OpenApiParameter.QUERY,
                required=False,
                description="id of the Surah to filter timestamps by. When provided, only timestamps for this surah are returned.",
            )
        ],
    ),
    create=extend_schema(summary="Create a new Recitation record"),
    update=extend_schema(summary="Update an existing Recitation record"),
    partial_update=extend_schema(summary="Partially update a Recitation record"),
    destroy=extend_schema(summary="Delete a Recitation record"),
)
class RecitationViewSet(viewsets.ModelViewSet):
    queryset = Recitation.objects.all()
    serializer_class = RecitationSerializer

    def get_serializer_class(self):
        if self.action == "list":
            from quran.serializers import RecitationListSerializer

            return RecitationListSerializer
        return RecitationSerializer

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
    search_fields = ["recitation_date", "recitation_location", "recitation_type"]
    ordering_fields = ["created_at", "recitation_date"]
    pagination_class = CustomLimitOffsetPagination
    limited_fields = {"status": ["published"]}
    lookup_field = "id"

    def get_queryset(self):
        recitation_fields = [
            "id",
            "transmission",
            "reciter_account",
            "recitation_date",
            "recitation_location",
            "recitation_type",
            "status",
            "creator",
        ]
        queryset = Recitation.objects.select_related(
            "transmission", "reciter_account"
        ).only(*recitation_fields)
        mushaf_slug = self.request.query_params.get("rasm_ol_mushaf")
        if self.action == "list" and not mushaf_slug:
            raise serializers.ValidationError(
                {"rasm_ol_mushaf": "This query parameter is required."}
            )
        if not self.request.user.is_authenticated:
            queryset = queryset.exclude(Q(status="draft") | Q(status="pending_review"))
        if mushaf_slug:
            # Using relations transmission -> rasm ol mushaf -> slug
            queryset = queryset.filter(transmission__rasm_ol_mushaf__slug=mushaf_slug)
        reciter_id = self.request.query_params.get("reciter_id", None)
        if reciter_id is not None:
            queryset = queryset.filter(reciter_account__id=reciter_id)
        if self.action == "retrieve":
            queryset = queryset.prefetch_related("recitation_surahs__timestamps")
        return queryset

    def create(self, request, *args, **kwargs):
        return super().create(request, *args, **kwargs)

    def retrieve(self, request, *args, **kwargs):
        return super().retrieve(request, *args, **kwargs)

    def update(self, request, *args, **kwargs):
        partial = kwargs.pop("partial", False)
        instance = self.get_object()
        if instance.status == "published" and not request.user.is_staff:
            return Response(
                {"detail": "Published Recitation cannot be edited."},
                status=status.HTTP_403_FORBIDDEN,
            )
        status_value = request.data.get("status")
        if status_value == "pending_review":
            ayah_count = Ayah.objects.filter(surah__mushaf=instance.mushaf).count()
            ayah_translation_count = AyahTranslation.objects.filter(
                translation__mushaf=instance.mushaf
            ).count()
            if ayah_translation_count != ayah_count:
                return Response(
                    {
                        "detail": f"Recitation is incomplete: {ayah_translation_count} of {ayah_count} ayahs translated."
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )
        return super().update(request, *args, partial=partial, **kwargs)

    def partial_update(self, request, *args, **kwargs):
        return self.update(request, *args, partial=True, **kwargs)

    @extend_schema(
        summary="List or create Tracks (per-surah audio files) for this Recitation",
        description=(
            "GET: list all tracks (RecitationSurah) for this recitation, "
            "optionally filtered by surah_id. "
            "POST: upload a surah audio file and optional word-level timestamps for this recitation."
        ),
        request={
            "multipart/form-data": {
                "type": "object",
                "properties": {
                    "file": {"type": "string", "format": "binary"},
                    "surah_id": {"type": "string", "format": "id"},
                    "word_timestamps": {
                        "type": "string",
                        "description": "JSON list of word timestamps, optional",
                    },
                },
                "required": ["file", "surah_id"],
            },
        },
    )
    @action(
        detail=True,
        methods=["get", "post"],
        url_path="track",
        parser_classes=[MultiPartParser, FormParser],
    )
    def track(self, request, *args, **kwargs):
        from core.utils import upload_mp3_to_s3
        from django.db import transaction

        recitation: Recitation = self.get_object()
        self.check_object_permissions(request, recitation)

        # LIST TRACKS FOR THIS RECITATION (lightweight: id + surah_id + file_url)
        if request.method.lower() == "get":
            queryset = RecitationSurah.objects.filter(
                recitation=recitation
            ).select_related("surah", "file")
            surah_id = request.query_params.get("surah_id")
            if surah_id:
                queryset = queryset.filter(surah__id=surah_id)
            from quran.serializers import RecitationSurahSerializer

            serializer = RecitationSurahSerializer(
                queryset, many=True, context=self.get_serializer_context()
            )
            return Response(serializer.data)

        # CREATE A NEW TRACK (UPLOAD)
        file_obj = request.FILES.get("file")
        surah_id = request.data.get("surah_id")
        word_ts_raw = request.data.get("word_timestamps")
        errors = {}
        if not file_obj:
            errors["file"] = "This field is required."
        if not surah_id:
            errors["surah_id"] = "This field is required."
        if errors:
            return Response(errors, status=status.HTTP_400_BAD_REQUEST)

        audio = MP3(file_obj)
        track_duration = timedelta(audio.info.length)

        try:
            surah = Surah.objects.get(id=surah_id)
        except Surah.DoesNotExist:
            return Response(
                {"surah_id": "Surah not found."}, status=status.HTTP_404_NOT_FOUND
            )

        if surah.mushaf_id != recitation.mushaf_id:
            return Response(
                {
                    "detail": "Surah does not belong to the same Mushaf as the recitation."
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        word_timestamps = None
        if word_ts_raw:
            try:
                import json as _json

                word_timestamps = _json.loads(word_ts_raw)
                if not isinstance(word_timestamps, list):
                    raise ValueError
            except ValueError:
                return Response(
                    {"word_timestamps": "Invalid JSON – expected list."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        try:
            new_file = upload_mp3_to_s3(file_obj, request.user, folder="recitations")
        except ValueError as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            return Response(
                {"error": f"Upload failed: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        with transaction.atomic():
            recitation_surah, _ = RecitationSurah.objects.get_or_create(
                recitation=recitation,
                surah=surah,
                defaults={"file": new_file, "duration": track_duration},
            )
            if not recitation_surah.file_id:
                recitation_surah.file = new_file
                recitation_surah.save(update_fields=["file"])

            if word_timestamps:
                from datetime import datetime

                ts_objs = []
                for ts in word_timestamps:
                    try:
                        start_time = datetime.strptime(ts["start"], "%H:%M:%S.%f")
                        end_time = (
                            datetime.strptime(ts["end"], "%H:%M:%S.%f")
                            if ts.get("end")
                            else None
                        )
                        word = None
                        if ts.get("word_id"):
                            word = Word.objects.filter(id=ts["word_id"]).first()
                        ts_objs.append(
                            RecitationSurahTimestamp(
                                recitation_surah=recitation_surah,
                                start_time=start_time,
                                end_time=end_time,
                                word=word,
                            )
                        )
                    except Exception:
                        continue
                if ts_objs:
                    RecitationSurahTimestamp.objects.bulk_create(ts_objs)

            if not word_timestamps:
                from quran.tasks import forced_alignment

                # Construct the audio URL using s3_id
                audio_url = new_file.get_absolute_url()

                # Get all words in the surah, ordered by ayah number and id (creation order)
                words = list(
                    Word.objects.filter(ayah__surah=surah).order_by(
                        "ayah__number", "id"
                    )
                )
                text = " ".join([w.text for w in words])

                # Prepare additional data to pass through forced_alignment to forced_alignment_result
                additional = {
                    "recitation_id": str(recitation.id),
                    "surah_id": str(surah.id),
                    "file_s3_id": str(new_file.s3_id),
                    "word_ids": [str(w.id) for w in words],
                    "user_id": request.user.id,
                }

                transaction.on_commit(
                    lambda: forced_alignment.delay(audio_url, text, additional)
                )

        serializer = TrackDetailSerializer(
            recitation_surah, context=self.get_serializer_context()
        )
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    @extend_schema(
        summary="Retrieve, update, or delete a single Track for this Recitation",
        description=(
            "GET: retrieve a single track. "
            "PATCH: replace the audio file and/or word timestamps. "
            "DELETE: remove the track."
        ),
    )
    @action(
        detail=True,
        methods=["get", "patch", "delete"],
        url_path="track/(?P<track_id>[^/.]+)",
        parser_classes=[MultiPartParser, FormParser],
    )
    def track_detail(self, request, *args, **kwargs):
        from core.utils import upload_mp3_to_s3
        from django.db import transaction

        recitation: Recitation = self.get_object()
        self.check_object_permissions(request, recitation)
        track_id = kwargs.get("track_id")

        try:
            recitation_surah = RecitationSurah.objects.get(
                id=track_id, recitation=recitation
            )
        except RecitationSurah.DoesNotExist:
            return Response(
                {"detail": "Track not found."}, status=status.HTTP_404_NOT_FOUND
            )

        method = request.method.lower()

        if method == "get":
            serializer = TrackDetailSerializer(
                recitation_surah, context=self.get_serializer_context()
            )
            return Response(serializer.data)

        if method == "delete":
            recitation_surah.delete()
            return Response(status=status.HTTP_204_NO_CONTENT)

        # PATCH: update file and/or word timestamps
        file_obj = request.FILES.get("file")
        word_ts_raw = request.data.get("word_timestamps")

        word_timestamps = None
        if word_ts_raw is not None:
            if word_ts_raw == "":
                word_timestamps = []
            else:
                try:
                    import json as _json

                    word_timestamps = _json.loads(word_ts_raw)
                    if not isinstance(word_timestamps, list):
                        raise ValueError
                except ValueError:
                    return Response(
                        {"word_timestamps": "Invalid JSON – expected list."},
                        status=status.HTTP_400_BAD_REQUEST,
                    )

        with transaction.atomic():
            if file_obj:
                try:
                    new_file = upload_mp3_to_s3(
                        file_obj, request.user, folder="recitations"
                    )
                except ValueError as e:
                    return Response(
                        {"error": str(e)}, status=status.HTTP_400_BAD_REQUEST
                    )
                except Exception as e:
                    return Response(
                        {"error": f"Upload failed: {str(e)}"},
                        status=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    )
                recitation_surah.file = new_file
                recitation_surah.save(update_fields=["file"])

            if word_timestamps is not None:
                from datetime import datetime

                # Replace existing timestamps
                recitation_surah.timestamps.all().delete()
                ts_objs = []
                for ts in word_timestamps:
                    try:
                        start_time = datetime.strptime(ts["start"], "%H:%M:%S.%f")
                        end_time = (
                            datetime.strptime(ts["end"], "%H:%M:%S.%f")
                            if ts.get("end")
                            else None
                        )
                        word = None
                        if ts.get("word_id"):
                            word = Word.objects.filter(id=ts["word_id"]).first()
                        ts_objs.append(
                            RecitationSurahTimestamp(
                                recitation_surah=recitation_surah,
                                start_time=start_time,
                                end_time=end_time,
                                word=word,
                            )
                        )
                    except Exception:
                        continue
                if ts_objs:
                    RecitationSurahTimestamp.objects.bulk_create(ts_objs)

        serializer = TrackDetailSerializer(
            recitation_surah, context=self.get_serializer_context()
        )
        return Response(serializer.data)
