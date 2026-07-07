from rest_framework import serializers
from django.db import models
from datetime import datetime
from django.conf import settings
from django.conf.global_settings import LANGUAGES
from drf_spectacular.utils import extend_schema_field
from core.rtl_languages import RTL_LANGUAGE_CODES
from datetime import timedelta

from quran.models import (
    Mushaf,
    Surah,
    SurahName,
    Ayah,
    Takhtit,
    Word,
    Translation,
    AyahTranslation,
    AyahBreaker,
    WordBreaker,
    Provenance,
    Recitation,
    File,
    RecitationSurah,
    RecitationSurahTimestamp,
    Status,
)
from account.models import CustomUser


class RecursiveField(serializers.Serializer):
    def to_representation(self, value):
        serializer = self.parent.__class__(value, context=self.context)
        return serializer.data

    def to_internal_value(self, data):
        serializer = self.parent.__class__(data=data, context=self.context)
        serializer.is_valid(raise_exception=True)
        return serializer.validated_data


class MushafSerializer(serializers.ModelSerializer):
    class Meta:
        model = Mushaf
        fields = ["id", "slug", "name", "source", "status"]
        read_only_fields = ["creator"]

    def create(self, validated_data):
        return Mushaf.objects.create(**validated_data)


class SurahNameSerializer(serializers.ModelSerializer):
    class Meta:
        model = SurahName
        fields = ["id", "name", "pronunciation", "translation", "transliteration"]
        read_only_fields = ["creator"]


class SurahBismillahSerializer(serializers.Serializer):
    is_ayah = serializers.BooleanField()
    text = serializers.CharField()


class SurahSerializer(serializers.ModelSerializer):
    mushaf = MushafSerializer(read_only=True)
    mushaf_id = serializers.UUIDField(write_only=True, required=True)
    number_of_ayahs = serializers.SerializerMethodField(read_only=True)
    bismillah = serializers.SerializerMethodField(read_only=True)
    names = SurahNameSerializer(read_only=True, many=True)

    class Meta:
        model = Surah
        fields = [
            "id",
            "mushaf",
            "mushaf_id",
            "names",
            "number",
            "period",
            "search_terms",
            "number_of_ayahs",
            "bismillah",
        ]
        read_only_fields = ["creator"]

    @extend_schema_field(SurahBismillahSerializer)
    def get_bismillah(self, instance):
        if not instance.has_bismillah:
            return None
        # Get the first ayah of this surah
        first_ayah = instance.ayahs.order_by("number").first()
        text = instance.bismillah_text
        is_ayah = first_ayah.is_bismillah if first_ayah else False
        return {"is_ayah": is_ayah, "text": text}

    def get_number_of_ayahs(self, instance):
        return instance.ayahs.count()

    def get_names(self, instance):
        return instance.names.all()

    def create(self, validated_data):
        mushaf_id = validated_data.pop("mushaf_id")
        names = validated_data.pop("names")
        from quran.models import Mushaf

        mushaf = Mushaf.objects.get(id=mushaf_id)
        validated_data["mushaf"] = mushaf
        validated_data["names"] = names
        validated_data["creator"] = self.context["request"].user
        return super().create(validated_data)


class SurahInAyahSerializer(serializers.ModelSerializer):
    class Meta:
        model = Surah
        fields = ["id", "names"]
        read_only_fields = ["creator"]


class AyahSerializer(serializers.ModelSerializer):
    text = serializers.SerializerMethodField()
    breakers = serializers.SerializerMethodField()
    surah = serializers.SerializerMethodField()
    surah_number = serializers.SerializerMethodField()

    class Meta:
        model = Ayah
        fields = [
            "id",
            "number",
            "sajdah",
            "text",
            "breakers",
            "is_bismillah",
            "surah",
            "surah_number",
            "length",
        ]
        read_only_fields = ["creator"]

    @extend_schema_field(SurahSerializer(allow_null=True))
    def get_surah(self, instance):
        if instance.number == 1:
            return SurahSerializer(instance.surah).data
        return None

    def get_surah_number(self, instance):
        return instance.surah.number

    def get_text(self, instance):
        words = list(instance.words.all().order_by("id"))
        if not words:
            return [] if self.context.get("text_format") == "word" else ""

        if self.context.get("text_format") == "word":
            # Get all word breakers for these words
            word_ids = [word.id for word in words]
            word_breakers = WordBreaker.objects.filter(word_id__in=word_ids)

            # Group breakers by word_id
            breakers_by_word = {}
            for breaker in word_breakers:
                if breaker.word_id not in breakers_by_word:
                    breakers_by_word[breaker.word_id] = []
                breakers_by_word[breaker.word_id].append({"name": breaker.name})

            # Return words with their breakers (only if they have any)
            result = []
            for word in words:
                word_data = {"id": word.id, "text": word.text}
                if word.id in breakers_by_word:
                    word_data["breakers"] = breakers_by_word[word.id]
                result.append(word_data)
            return result

        return " ".join(word.text for word in words)

    def get_breakers(self, instance):
        breakers = instance.breakers.all()
        if not breakers.exists():
            return None

        # Get all breakers up to current ayah across all surahs
        current_surah = instance.surah
        current_number = instance.number

        all_breakers = AyahBreaker.objects.filter(
            models.Q(ayah__surah__number__lt=current_surah.number)
            | models.Q(ayah__surah=current_surah, ayah__number__lte=current_number)
        ).order_by("ayah__surah__number", "ayah__number")

        # Keep running count of breakers
        breaker_counts = {}
        ayah_breakers = {}

        for breaker in all_breakers:
            # Update count for this breaker type
            if breaker.type not in breaker_counts:
                breaker_counts[breaker.type] = 1
            else:
                breaker_counts[breaker.type] += 1

            # Store current counts for this ayah
            if breaker.ayah_id not in ayah_breakers:
                ayah_breakers[breaker.ayah_id] = []

            # Only add if type not already in this ayah's breakers
            if not any(
                b["name"] == breaker.type for b in ayah_breakers[breaker.ayah_id]
            ):
                ayah_breakers[breaker.ayah_id].append(
                    {"name": breaker.type, "number": breaker_counts[breaker.type]}
                )

        # Return breakers for current ayah
        return ayah_breakers.get(instance.id, None)

    def to_representation(self, instance):
        representation = super().to_representation(instance)
        # Remove null fields safely
        for field in ["breakers", "sajdah", "bismillah", "surah"]:
            if field in representation and representation[field] is None:
                representation.pop(field)
            # Move bismillah into surah for the first ayah
            if (
                instance.number == 1
                and "bismillah" in representation
                and "surah" in representation
            ):
                if representation["surah"] is not None:
                    # If surah is a dict, add bismillah to it
                    if isinstance(representation["surah"], dict):
                        representation["surah"]["bismillah"] = representation[
                            "bismillah"
                        ]
                    # Remove bismillah from top level
                    representation.pop("bismillah", None)
        return representation

    def create(self, validated_data):
        validated_data["creator"] = self.context["request"].user
        return super().create(validated_data)


class WordSerializer(serializers.ModelSerializer):
    ayah_id = serializers.UUIDField(write_only=True)

    class Meta:
        model = Word
        fields = ["id", "ayah_id", "text"]
        read_only_fields = ["creator"]

    def __init__(self, no_ayah_id, **kwargs):
        self.no_ayah_id = no_ayah_id
        super().__init__(**kwargs)

    def create(self, validated_data):
        from quran.models import Ayah

        ayah_id = validated_data.pop("ayah_id")
        ayah = Ayah.objects.get(id=ayah_id)
        validated_data["ayah"] = ayah
        validated_data["creator"] = self.context["request"].user
        return super().create(validated_data)

    def to_representation(self, instance):
        rep = super().to_representation(instance)
        if not self.no_ayah_id:
            rep["ayah_id"] = str(instance.ayah.id)
        return rep


class AyahSerializerView(AyahSerializer):
    surah = SurahInAyahSerializer(read_only=True)
    mushaf = serializers.SerializerMethodField()
    words = WordSerializer(many=True, read_only=True, no_ayah_id=True)

    class Meta(AyahSerializer.Meta):
        fields = AyahSerializer.Meta.fields + ["surah", "mushaf", "words"]

    def get_mushaf(self, instance):
        return MushafSerializer(instance.surah.mushaf).data


# Separate serializer for ayahs in surah
class AyahInSurahSerializer(AyahSerializer):
    class Meta(AyahSerializer.Meta):
        fields = ["id", "number", "sajdah", "is_bismillah", "text"]


class SurahDetailSerializer(SurahSerializer):
    ayahs = AyahInSurahSerializer(many=True, read_only=True)

    class Meta(SurahSerializer.Meta):
        fields = SurahSerializer.Meta.fields + ["ayahs"]


class AyahTranslationNestedSerializer(serializers.ModelSerializer):
    ayah_id = serializers.UUIDField(source="ayah.id", read_only=True)
    bismillah = serializers.SerializerMethodField()

    class Meta:
        model = AyahTranslation
        fields = ["id", "ayah_id", "text", "bismillah"]
        read_only_fields = ["creator"]

    def get_bismillah(self, obj):
        # Only include bismillah for the first ayah in the surah (ayah number 1)
        if hasattr(obj, "ayah") and getattr(obj.ayah, "number", None) == 1:
            return obj.bismillah
        return None


class LangCodeField(serializers.ChoiceField):
    """A field for ISO 639-1 language codes using Django LANGUAGES."""

    def __init__(self, **kwargs):
        # Extract language codes from Django LANGUAGES
        language_codes = [code for code, name in LANGUAGES]
        super().__init__(choices=language_codes, **kwargs)


class TranslationCreateSerializer(serializers.ModelSerializer):
    mushaf_id = serializers.PrimaryKeyRelatedField(
        source="mushaf", queryset=Mushaf.objects.all()
    )

    translator_id = serializers.PrimaryKeyRelatedField(
        source="translator", queryset=CustomUser.objects.all()
    )

    class Meta:
        model = Translation
        fields = [
            "id",
            "mushaf_id",
            "translator_id",
            "language",
            "release_date",
            "source",
            "status",
        ]
        read_only_fields = ["creator", "id"]


class TranslationUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Translation
        fields = [
            "id",
            "language",
            "release_date",
            "source",
            "status",
            "is_primary",
        ]
        read_only_fields = ["creator", "id"]


class TranslationSerializer(serializers.ModelSerializer):
    mushaf_id = serializers.SerializerMethodField()
    translator_id = serializers.SerializerMethodField()
    language = LangCodeField()
    language_is_rtl = serializers.SerializerMethodField()

    class Meta:
        model = Translation
        fields = [
            "id",
            "mushaf_id",
            "translator_id",
            "language",
            "language_is_rtl",
            "release_date",
            "source",
            "status",
            "is_primary",
        ]
        read_only_fields = ["creator"]

    def get_mushaf_id(self, obj):
        return str(obj.mushaf.id) if obj.mushaf else None

    def get_translator_id(self, obj):
        return str(obj.translator.id) if obj.translator else None

    def get_language_is_rtl(self, obj):
        code = (obj.language or "").strip().lower()
        base = code.split("-")[0]
        return code in RTL_LANGUAGE_CODES or base in RTL_LANGUAGE_CODES

    def to_internal_value(self, data):
        # Extract ids for input
        mushaf_id = data.get("mushaf_id")
        translator_id = data.get("translator_id")
        ret = super().to_internal_value(data)
        ret["mushaf_id"] = mushaf_id
        ret["translator_id"] = translator_id
        return ret

    def create(self, validated_data):
        from quran.models import Mushaf
        from account.models import CustomUser

        mushaf_id = validated_data.pop("mushaf_id")
        translator_id = validated_data.pop("translator_id")
        mushaf = Mushaf.objects.get(id=mushaf_id)
        translator = CustomUser.objects.get(id=translator_id)
        validated_data["mushaf"] = mushaf
        validated_data["translator"] = translator
        validated_data["creator"] = self.context["request"].user
        return super().create(validated_data)

    def to_representation(self, instance):
        rep = super().to_representation(instance)
        rep["mushaf_id"] = str(instance.mushaf.id)
        rep["translator_id"] = str(instance.translator.id)
        return rep


class AyahTranslationSerializer(serializers.ModelSerializer):
    translation_id = serializers.UUIDField(write_only=True)
    ayah_id = serializers.UUIDField(write_only=True)

    class Meta:
        model = AyahTranslation
        fields = ["id", "translation_id", "ayah_id", "text", "bismillah"]
        read_only_fields = ["creator"]

    def create(self, validated_data):
        from quran.models import Translation, Ayah

        translation_id = validated_data.pop("translation_id")
        ayah_id = validated_data.pop("ayah_id")
        translation = Translation.objects.get(id=translation_id)
        ayah = Ayah.objects.get(id=ayah_id)
        validated_data["translation"] = translation
        validated_data["ayah"] = ayah
        validated_data["creator"] = self.context["request"].user
        return super().create(validated_data)

    def to_representation(self, instance):
        rep = super().to_representation(instance)
        rep["ayah_id"] = str(instance.ayah.id)
        return rep


class AyahBreakerSerializer(serializers.ModelSerializer):
    class Meta:
        model = AyahBreaker
        fields = ["id", "type"]
        read_only_fields = ["creator"]

    def create(self, validated_data):
        validated_data["creator"] = self.context["request"].user
        return super().create(validated_data)


class WordBreakerSerializer(serializers.ModelSerializer):
    class Meta:
        model = WordBreaker
        fields = ["id", "name"]
        read_only_fields = ["creator"]

    def create(self, validated_data):
        validated_data["creator"] = self.context["request"].user
        return super().create(validated_data)


class AyahAddSerializer(serializers.Serializer):
    surah_id = serializers.UUIDField()
    text = serializers.CharField()
    is_bismillah = serializers.BooleanField(default=False)
    sajdah = serializers.CharField(required=False, allow_null=True)

    def to_representation(self, instance):
        return {
            "id": str(instance.id),
            "number": instance.number,
            "surah_id": str(instance.surah.id),
            "is_bismillah": instance.is_bismillah,
            "sajdah": instance.sajdah,
            "length": instance.length,
        }

    def create(self, validated_data):
        # Get the text and remove it from validated_data
        text = validated_data.pop("text")
        surah_id = validated_data.pop("surah_id")

        # Get the surah by id
        surah = Surah.objects.get(id=surah_id)

        # Get the latest ayah number in this surah and increment it
        latest_ayah = Ayah.objects.filter(surah=surah).order_by("-number").first()
        next_number = 1 if latest_ayah is None else latest_ayah.number + 1

        # Create the ayah
        ayah_data = {
            "surah": surah,
            "creator": self.context["request"].user,
            "number": next_number,
            "is_bismillah": validated_data.get("is_bismillah", False),
            "sajdah": validated_data.get("sajdah", None),
        }
        ayah = Ayah.objects.create(**ayah_data)

        # Create words from the text
        if text:
            # Split text into words (you might want to use a more sophisticated word splitting logic)
            words = text.split(" ")
            for word_text in words:
                Word.objects.create(
                    ayah=ayah, text=word_text, creator=self.context["request"].user
                )

        # Calculate and update the length after creating words
        ayah.length = ayah.calculate_length()
        ayah.save(update_fields=["length"])

        return ayah


class RecitationSerializer(serializers.ModelSerializer):
    mushaf_id = serializers.UUIDField(write_only=True)
    reciter_account_id = serializers.UUIDField(write_only=True)

    # Add read-only fields for output
    get_mushaf_id = serializers.SerializerMethodField(read_only=True)

    track_count = serializers.SerializerMethodField(read_only=True)

    total_duration = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = Recitation
        fields = [
            "id",
            "mushaf_id",
            "get_mushaf_id",
            "status",
            "reciter_account_id",
            "recitation_date",
            "recitation_location",
            "track_count",
            "total_duration",
            "recitation_type",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["creator", "get_mushaf_id"]

    def get_track_count(self, obj):
        return len(list(obj.recitation_surahs.all()))

    def get_get_mushaf_id(self, obj):
        return str(obj.mushaf.id) if obj.mushaf else None

    def get_reciter_account_id(self, obj):
        return str(obj.reciter_account.id) if obj.reciter_account else None

    def get_total_duration(self, obj):
        return sum(
            list(map(lambda x: x.duration, obj.recitation_surahs.all())), timedelta()
        )

    def to_internal_value(self, data):
        mushaf_id = data.get("mushaf_id")
        ret = super().to_internal_value(data)
        ret["mushaf_id"] = mushaf_id
        return ret

    def create(self, validated_data):
        from quran.models import Mushaf, RecitationSurah
        from account.models import CustomUser

        mushaf_id = validated_data.pop("mushaf_id")

        mushaf = Mushaf.objects.get(id=mushaf_id)

        validated_data["mushaf"] = mushaf
        # Use the requesting user as the reciter by default (can be adjusted later via recitation_surah upload).
        reciter_account_id = validated_data.pop("reciter_account_id")
        try:
            reciter_user = CustomUser.objects.get(id=reciter_account_id)
        except CustomUser.DoesNotExist:
            raise serializers.ValidationError({"reciter_account_id": "User not found"})
        validated_data["reciter_account"] = reciter_user
        # Initial RecitationSurah association is created via dedicated endpoints (e.g., upload).
        validated_data["creator"] = self.context["request"].user
        # Remove word_timestamps handling – they will be provided via the upload endpoint
        recitation = super().create(validated_data)

        return recitation

    def to_representation(self, instance):
        representation = super().to_representation(instance)
        # Remove write-only fields from output
        representation.pop("mushaf_id", None)
        # Echo back the reciter_account_id for client convenience
        representation["reciter_account_id"] = (
            str(instance.reciter_account.id)
            if getattr(instance, "reciter_account", None)
            else None
        )
        # Always show ids using the read-only methods
        representation["mushaf_id"] = representation.pop("get_mushaf_id", None)
        # Remove reciter_account (int id) from output if present
        representation.pop("reciter_account", None)

        # Add recitation_surahs with file_url for each (filtered by surah_id on retrieve when provided)
        from quran.models import RecitationSurah

        recitation_surahs = RecitationSurah.objects.filter(recitation=instance)
        request = self.context.get("request")
        view = self.context.get("view")
        if request and view and view.action == "retrieve":
            surah_id = request.query_params.get("surah_id")
            if surah_id:
                recitation_surahs = recitation_surahs.filter(surah__id=surah_id)
        representation["recitation_surahs"] = RecitationSurahSerializer(
            recitation_surahs, many=True, context=self.context
        ).data

        # No timestamps are returned at the recitation level; they are exposed via track endpoints only.
        return representation


class TranslatorDetailSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    name = serializers.CharField()


class ReciterDetailSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    name = serializers.CharField()


class TranslationListSerializer(serializers.ModelSerializer):
    mushaf_id = serializers.SerializerMethodField()
    translator = serializers.SerializerMethodField()
    language_is_rtl = serializers.SerializerMethodField()

    class Meta:
        model = Translation
        fields = [
            "id",
            "mushaf_id",
            "translator",
            "language",
            "language_is_rtl",
            "release_date",
            "source",
            "status",
            "is_primary",
        ]
        read_only_fields = ["creator"]

    def get_mushaf_id(self, obj):
        return str(obj.mushaf.id) if obj.mushaf else None

    @extend_schema_field(TranslatorDetailSerializer(allow_null=True))
    def get_translator(self, obj):
        if not obj.translator:
            return None
        name_parts = []
        if obj.translator.display_name:
            name_parts.append(obj.translator.display_name)
        name = " ".join(name_parts) if name_parts else obj.translator.username
        return {"id": str(obj.translator.id), "name": name}

    def get_language_is_rtl(self, obj):
        code = (obj.language or "").strip().lower()
        base = code.split("-")[0]
        return code in RTL_LANGUAGE_CODES or base in RTL_LANGUAGE_CODES


class RecitationSurahSerializer(serializers.ModelSerializer):
    file_url = serializers.SerializerMethodField()
    surah_id = serializers.SerializerMethodField()

    class Meta:
        model = RecitationSurah
        fields = ["id", "surah_id", "file_url", "duration"]

    def get_file_url(self, obj):
        if obj.file and hasattr(obj.file, "get_absolute_url"):
            return obj.file.get_absolute_url()
        return None

    def get_surah_id(self, obj):
        return str(obj.surah.id) if obj.surah else None


class TrackDetailSerializer(serializers.ModelSerializer):
    """Detailed serializer for a RecitationSurah (track), including timestamps."""

    file_url = serializers.SerializerMethodField()
    surah_id = serializers.SerializerMethodField()
    words_timestamps = serializers.SerializerMethodField()
    ayahs_timestamps = serializers.SerializerMethodField()

    class Meta:
        model = RecitationSurah
        fields = [
            "id",
            "surah_id",
            "file_url",
            "words_timestamps",
            "ayahs_timestamps",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "surah_id",
            "file_url",
            "words_timestamps",
            "ayahs_timestamps",
            "created_at",
            "updated_at",
        ]

    def get_file_url(self, obj):
        if obj.file and hasattr(obj.file, "get_absolute_url"):
            return obj.file.get_absolute_url()
        return None

    def get_surah_id(self, obj):
        return str(obj.surah.id) if obj.surah else None

    def get_words_timestamps(self, obj):
        """Return word-level timestamps for this track."""
        from quran.models import RecitationSurahTimestamp

        result = []
        qs = (
            RecitationSurahTimestamp.objects.filter(recitation_surah=obj)
            .select_related("word")
            .order_by("start_time")
        )
        for timestamp in qs:
            start_time = timestamp.start_time.strftime("%H:%M:%S.%f")[:-3]
            end_time = (
                timestamp.end_time.strftime("%H:%M:%S.%f")[:-3]
                if timestamp.end_time
                else None
            )
            result.append(
                {
                    "start": start_time,
                    "end": end_time,
                    "word_id": str(timestamp.word.id) if timestamp.word else None,
                }
            )
        return result

    def get_ayahs_timestamps(self, obj):
        """Return ayah start times for this track."""
        from quran.models import RecitationSurahTimestamp, Ayah

        timestamps = list(
            RecitationSurahTimestamp.objects.filter(recitation_surah=obj).order_by(
                "start_time"
            )
        )
        if not timestamps:
            return []

        ayahs = Ayah.objects.filter(surah=obj.surah).prefetch_related("words")
        ayahs_first_words_as_id = set()
        for ayah in ayahs:
            first_word = ayah.words.values("id").first()
            if first_word:
                ayahs_first_words_as_id.add(first_word["id"])

        ayah_start_times = []
        for timestamp in timestamps[1:]:  # Skip first timestamp
            start_time = timestamp.start_time.strftime("%H:%M:%S.%f")[:-3]
            if timestamp.word_id in ayahs_first_words_as_id:
                ayah_start_times.append(start_time)
        return ayah_start_times


# Recitation list serializer (no recitation_surahs)
class RecitationListSerializer(serializers.ModelSerializer):
    reciter = serializers.SerializerMethodField()
    mushaf_id = serializers.UUIDField(source="mushaf.id", read_only=True)
    track_count = serializers.SerializerMethodField()

    class Meta:
        model = Recitation
        fields = [
            "id",
            "status",
            "recitation_date",
            "recitation_location",
            "recitation_type",
            "track_count",
            "created_at",
            "updated_at",
            "reciter",
            "mushaf_id",
        ]

    def get_track_count(self, obj):
        return len(list(obj.recitation_surahs.all()))

    @extend_schema_field(ReciterDetailSerializer(allow_null=True))
    def get_reciter(self, obj):
        if not obj.reciter_account:
            return None
        name_parts = []
        if obj.reciter_account.display_name:
            name_parts.append(obj.reciter_account.display_name)
        name = " ".join(name_parts) if name_parts else obj.reciter_account.username
        return {"id": str(obj.reciter_account.id), "name": name}


class TakhtitSerializer(serializers.ModelSerializer):
    mushaf_id = serializers.UUIDField(write_only=True, required=True)
    account_id = serializers.UUIDField(write_only=True, required=True)

    class Meta:
        model = Takhtit
        fields = [
            "id",
            "creator",
            "mushaf_id",
            "account_id",
            "created_at",
        ]
        read_only_fields = ["id", "creator", "created_at", "updated_at"]

    def create(self, validated_data):
        # Remove the id fields before creating the model instance
        validated_data.pop("mushaf_id", None)
        validated_data.pop("account_id", None)
        return super().create(validated_data)


class AyahBreakersResponseSerializer(serializers.Serializer):
    """Serializer for the ayahs_breakers endpoint response"""

    id = serializers.UUIDField(help_text="id of the ayah")
    surah = serializers.IntegerField(help_text="Surah number")
    ayah = serializers.IntegerField(help_text="Ayah number")
    length = serializers.IntegerField(help_text="Ayah text length")
    juz = serializers.IntegerField(
        allow_null=True, help_text="Juz number (null if not a juz breaker)"
    )
    hizb = serializers.IntegerField(
        allow_null=True, help_text="Hizb number (null if not a hizb breaker)"
    )
    page = serializers.IntegerField(
        allow_null=True, help_text="Page number (null if not a page breaker)"
    )
    rub = serializers.IntegerField(
        allow_null=True, help_text="Rub number (null if not a rub breaker)"
    )
    manzil = serializers.IntegerField(
        allow_null=True, help_text="Manzil number (null if not a manzil breaker)"
    )


class WordBreakersResponseSerializer(serializers.Serializer):
    """Serializer for the words_breakers endpoint response"""

    word_id = serializers.UUIDField(help_text="id of the word")
    line = serializers.IntegerField(help_text="Line number counter")


class WordBreakerDetailResponseSerializer(serializers.Serializer):
    """Serializer for individual word breaker responses"""

    word_id = serializers.UUIDField(help_text="id of the word")
    type = serializers.CharField(help_text="Breaker type (always 'line')")


class ProvenanceSerializer(serializers.ModelSerializer):
    recipient = serializers.SerializerMethodField()
    account = serializers.SerializerMethodField()

    class Meta:
        model = Provenance
        fields = ["id", "role", "recipient", "account"]
        read_only_fields = ["creator"]

    def get_recipient(self, obj):
        if obj.child_provenance:
            return ProvenanceChildSerializer(obj.child_provenance).data
        return None

    def get_account(self, obj):
        if not obj.account:
            return None
        return {
            "id": obj.account.id,
            "display_name": obj.account.display_name,
        }

    def create(self, validated_data):
        validated_data["creator"] = self.context["request"].user
        return super().create(validated_data)


class ProvenanceAddSerializer(serializers.Serializer):
    account_id = serializers.UUIDField()
    role = serializers.CharField()
    recipient = RecursiveField(required=False, allow_null=True)

    def to_representation(self, instance):
        return {
            "id": str(instance.id),
            "role": instance.role,
            "recipient": instance.child_provenance,
        }

    def create(self, validated_data):
        recip = validated_data.get("recipient", None)
        account = CustomUser.objects.get(id=validated_data["account_id"])
        proven = Provenance.objects.create(
            creator=self.context["request"].user,
            account=account,
            role=validated_data["role"],
        )

        if recip:
            next = recip
            while next != None:
                n_account = CustomUser.objects.get(id=next["account_id"])
                c_proven = Provenance.objects.create(
                    creator=self.context["request"].user,
                    account=n_account,
                    role=next["role"],
                )
                proven.child_provenance = c_proven
                proven.save()
                proven = c_proven
                next = next["recipient"]
        return proven


class ProvenanceChildSerializer(ProvenanceSerializer):
    def to_representation(self, instance):
        representation = super().to_representation(instance)
        representation.pop("id")
        return representation
