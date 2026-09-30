from django.contrib.auth.models import Group
from rest_framework import serializers
from django.contrib.auth.password_validation import validate_password
from django.contrib.auth import authenticate
from account.models import CustomUser, UserName
from django.utils.crypto import get_random_string


class UserNameSerializer(serializers.ModelSerializer):
    class Meta:
        model = UserName
        fields = [
            "id",
            "language",
            "type",
            "text",
            "is_primary",
        ]


class UserSerializer(serializers.ModelSerializer):
    password = serializers.CharField(
        write_only=True, required=True, validators=[validate_password]
    )
    password2 = serializers.CharField(write_only=True, required=True)
    names = UserNameSerializer(read_only=True, many=True)

    class Meta:
        model = CustomUser
        fields = (
            "id",
            "username",
            "password",
            "password2",
            "email",
            "display_name",
            "birth_date",
            "death_date",
            "is_dead",
            "names",
        )
        extra_kwargs = {
            "display_name": {"required": False},
            "email": {"required": True},
            "username": {"read_only": True},
        }

    def validate(self, attrs):
        if attrs["password"] != attrs["password2"]:
            raise serializers.ValidationError(
                {"password": "Password fields didn't match."}
            )
        return attrs

    def create(self, validated_data):
        digits = 6
        validated_data.pop("password2")
        while True:
            username = "u" + get_random_string(digits, "0123456789")
            if not CustomUser.objects.filter(username=username).exists():
                validated_data["username"] = username
                return CustomUser.objects.create_user(**validated_data)
            digits += 1


class GroupSerializer(serializers.HyperlinkedModelSerializer):
    class Meta:
        model = Group
        fields = ["url", "name"]


class LoginSerializer(serializers.Serializer):
    username = serializers.CharField()
    password = serializers.CharField(
        style={"input_type": "password"}, trim_whitespace=False
    )

    def validate(self, attrs):
        username = attrs.get("username")
        password = attrs.get("password")

        if username and password:
            user = authenticate(
                request=self.context.get("request"),
                username=username,
                password=password,
            )
            if not user:
                msg = "Unable to log in with provided credentials."
                raise serializers.ValidationError(msg, code="authorization")
        else:
            msg = 'Must include "username" and "password".'
            raise serializers.ValidationError(msg, code="authorization")

        attrs["user"] = user
        return attrs


class ProfileSerializer(UserSerializer):
    class Meta(UserSerializer.Meta):
        fields = [
            "id",
            "username",
            "email",
            "display_name",
            "birth_date",
            "death_date",
            "is_dead",
        ]
        extra_kwargs = {
            "display_name": {"required": False},
            "password": {"required": False},
            "password2": {"required": False},
            "email": {"required": False},
            "birth_date": {"required": False},
            "death_date": {"required": False},
            "is_dead": {"required": False},
        }

    # Don't include email, if request.user is not the user itself
    def to_representation(self, instance):
        representation = super().to_representation(instance)
        req_user = self.context["request"].user
        id = instance.id
        if id and id != req_user.id:
            representation.pop("email")
        return representation

    # overwrite password check
    def validate(self, args):
        return args


class UserNameCreateSerializer(UserNameSerializer):
    phrase_id = serializers.UUIDField(write_only=True)

    class Meta:
        model = UserName
        fields = [
            "id",
            "language",
            "phrase_id",
            "type",
            "text",
            "is_primary",
        ]

    def create(self, validated_data):
        phrase_id = validated_data.pop("phrase_id")
        phrase = Phrase.objects.get(id=phrase_id)
        validated_data["phrase"] = phrase
        validated_data["user"] = self.context["user"]
        return super().create(validated_data)
