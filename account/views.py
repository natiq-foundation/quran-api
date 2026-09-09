from django.contrib.auth.models import Group
from .models import CustomUser, UserName
from django.contrib.auth import login
from rest_framework import permissions, viewsets, status
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.exceptions import ValidationError, ParseError
from knox.models import AuthToken
from knox.views import LoginView as KnoxLoginView
from .serializers import (
    ProfileSerializer,
    UserSerializer,
    GroupSerializer,
    LoginSerializer,
    UserNameSerializer,
    UserNameCreateSerializer,
)
from rest_framework.authtoken.serializers import AuthTokenSerializer
from drf_spectacular.utils import (
    extend_schema,
    OpenApiResponse,
    OpenApiExample,
    extend_schema_view,
)
from rest_framework import serializers


# Response schemas for authentication endpoints
class LoginResponseSerializer(serializers.Serializer):
    token = serializers.CharField(help_text="Authentication token")
    user = UserSerializer(help_text="User information")
    expiry = serializers.DateTimeField(help_text="Token expiry time")


class RegisterResponseSerializer(serializers.Serializer):
    user = UserSerializer(help_text="User information")
    token = serializers.CharField(help_text="Authentication token")


class LogoutResponseSerializer(serializers.Serializer):
    detail = serializers.CharField(help_text="Logout confirmation message")


class LogoutAllResponseSerializer(serializers.Serializer):
    detail = serializers.CharField(help_text="Logout all confirmation message")


@extend_schema_view(
    post=extend_schema(
        summary="Login with username and password to obtain a Knox token",
        description="Authenticate user credentials and return an authentication token for API access",
        request=LoginSerializer,
        responses={
            200: LoginResponseSerializer,
            400: OpenApiResponse(description="Invalid credentials or validation error"),
            401: OpenApiResponse(description="Authentication failed"),
        },
        examples=[
            OpenApiExample(
                "Login Example",
                value={"username": "testuser", "password": "yourpassword"},
                request_only=True,
            ),
            OpenApiExample(
                "Login Response Example",
                value={
                    "token": "knox_token_here",
                    "user": {
                        "username": "testuser",
                        "email": "test@example.com",
                        "first_name": "Test",
                        "last_name": "User",
                    },
                    "expiry": "2024-12-31T23:59:59Z",
                },
                response_only=True,
            ),
        ],
    )
)
class LoginView(KnoxLoginView):
    permission_classes = (permissions.AllowAny,)

    def post(self, request, format=None):
        serializer = AuthTokenSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.validated_data["user"]
        login(request, user)
        return super(LoginView, self).post(request, format=None)


class AuthViewSet(viewsets.GenericViewSet):
    permission_classes = [AllowAny]
    serializer_class = UserSerializer

    def get_queryset(self):
        return CustomUser.objects.all()  # Required for DRF to show in the API root

    @extend_schema(
        request=UserSerializer,
        summary="Register a new user account",
        description="Create a new user account with username, password, email, and optional personal information. Returns user data and authentication token upon successful registration.",
        responses={
            201: RegisterResponseSerializer,
            400: OpenApiResponse(description="Validation error or user already exists"),
            422: OpenApiResponse(description="Password validation failed"),
        },
        examples=[
            OpenApiExample(
                "Register Example",
                value={
                    "username": "newuser",
                    "password": "yourpassword",
                    "password2": "yourpassword",
                    "email": "newuser@example.com",
                    "first_name": "New",
                    "last_name": "User",
                },
                request_only=True,
            ),
            OpenApiExample(
                "Register Response Example",
                value={
                    "user": {
                        "username": "newuser",
                        "email": "newuser@example.com",
                        "first_name": "New",
                        "last_name": "User",
                    },
                    "token": "knox_token_here",
                },
                response_only=True,
            ),
        ],
    )
    @action(methods=["post"], detail=False)
    def register(self, request):
        """
        Register a new user with username, password, and email.
        Returns user data and Knox token upon successful registration.
        """
        serializer = self.get_serializer(data=request.data)
        if serializer.is_valid():
            user = serializer.save()
            _, token = AuthToken.objects.create(user)
            return Response(
                {"user": UserSerializer(user).data, "token": token},
                status=status.HTTP_201_CREATED,
            )
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


# Optionally, subclass Knox's LogoutView to add schema info
from knox.views import LogoutView as KnoxLogoutView


class LogoutView(KnoxLogoutView):
    @extend_schema(
        summary="Logout current user",
        description="Invalidate the current user's authentication token. The user will need to login again to access protected endpoints.",
        responses={
            204: LogoutResponseSerializer,
            401: OpenApiResponse(description="Authentication required"),
        },
        examples=[
            OpenApiExample(
                "Logout Response Example",
                value={"detail": "Successfully logged out."},
                response_only=True,
            )
        ],
    )
    def post(self, request, *args, **kwargs):
        return super().post(request, *args, **kwargs)


# Add LogoutAllView with proper schema
from knox.views import LogoutAllView as KnoxLogoutAllView


class LogoutAllView(KnoxLogoutAllView):
    @extend_schema(
        summary="Logout from all devices",
        description="Invalidate all authentication tokens for the current user across all devices. The user will need to login again on any device to access protected endpoints.",
        responses={
            204: LogoutAllResponseSerializer,
            401: OpenApiResponse(description="Authentication required"),
        },
        examples=[
            OpenApiExample(
                "Logout All Response Example",
                value={"detail": "Successfully logged out from all devices."},
                response_only=True,
            )
        ],
    )
    def post(self, request, *args, **kwargs):
        return super().post(request, *args, **kwargs)


# Create a proper RegisterView for the register endpoint
from rest_framework.views import APIView


class RegisterView(APIView):
    permission_classes = [AllowAny]

    @extend_schema(
        request=UserSerializer,
        summary="Register a new user account",
        description="Create a new user account with username, password, email, and optional personal information. Returns user data and authentication token upon successful registration.",
        responses={
            201: RegisterResponseSerializer,
            400: OpenApiResponse(description="Validation error or user already exists"),
            422: OpenApiResponse(description="Password validation failed"),
        },
        examples=[
            OpenApiExample(
                "Register Example",
                value={
                    "username": "newuser",
                    "password": "yourpassword",
                    "password2": "yourpassword",
                    "email": "newuser@example.com",
                    "first_name": "New",
                    "last_name": "User",
                },
                request_only=True,
            ),
            OpenApiExample(
                "Register Response Example",
                value={
                    "user": {
                        "username": "newuser",
                        "email": "newuser@example.com",
                        "first_name": "New",
                        "last_name": "User",
                    },
                    "token": "knox_token_here",
                },
                response_only=True,
            ),
        ],
    )
    def post(self, request):
        """
        Register a new user with username, password, and email.
        Returns user data and Knox token upon successful registration.
        """
        serializer = UserSerializer(data=request.data)
        if serializer.is_valid():
            user = serializer.save()
            _, token = AuthToken.objects.create(user)
            return Response(
                {"user": UserSerializer(user).data, "token": token},
                status=status.HTTP_201_CREATED,
            )
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


@extend_schema_view(
    list=extend_schema(summary="List all users"),
    retrieve=extend_schema(summary="Retrieve a specific user by id"),
    create=extend_schema(summary="Create a new user"),
    update=extend_schema(summary="Update an existing user"),
    partial_update=extend_schema(summary="Partially update a user"),
    destroy=extend_schema(summary="Delete a user"),
)
class UserViewSet(viewsets.ModelViewSet):
    queryset = CustomUser.objects.all().order_by("-date_joined")
    serializer_class = UserSerializer
    permission_classes = [permissions.IsAuthenticated, permissions.IsAdminUser]
    lookup_field = "id"

    def perform_create(self, serializer):
        serializer.save()

    @action(detail=True, methods=["get", "post"], url_path="names")
    def names(self, request, *args, **kwargs):
        user: CustomUser = self.get_object()
        if request.method == "GET":
            serializer = UserNameSerializer(user.names, many=True)
            return Response(serializer.data)
        elif request.method == "POST":
            serializer = UserNameCreateSerializer(
                data=request.data, context={"request": request, "user": user}
            )
            serializer.is_valid(raise_exception=True)
            primary = serializer.validated_data["is_primary"]
            if primary:
                primary_name = UserName.objects.get(user=user, is_primary=True)
                primary_name.is_primary = False
                primary_name.save()
                serializer.save()
                return Response({"status": "Created and made as primary"})
            serializer.save()
            return Response({"status": "Created"})
        return Response({"error": "Not supported"})

        @action(
            detail=True,
            methods=["put", "patch", "get"],
            url_path="names/(?P<name_id>[0-9a-f-]+)",
        )
        def get_edit_name(self, request, name_id=None, *args, **kwargs):
            user: CustomUser = self.get_object()

            if request.method == "GET":
                name = UserName.objects.get(id=name_id, user=user)
                serializer = UserNameSerializer(name)
                return Response(serializer.data)
            try:
                user_name = UserName.objects.get(id=name_id, user=user)
            except UserName.DoesNotExist:
                return Response(
                    {"error": "Name not found or doesn't belong to this user"},
                    status=status.HTTP_404_NOT_FOUND,
                )
            serializer = UserNameSerializer(user_name, data=request.data, partial=True)
            serializer.is_valid(raise_exception=True)
            serializer.save()
            return Response({"status": "updated", "data": serializer.data})

        @action(detail=True, methods=["delete"], url_path="names/(?P<name_id>[^/.]+)")
        def delete_name(self, request, name_id=None, *args, **kwargs):
            user: CustomUser = self.get_object()
            try:
                user_name = UserName.objects.get(id=name_id, user=user)
            except UserName.DoesNotExist:
                return Response(
                    {"error": "Name not found or doesn't belong to this user"},
                    status=status.HTTP_404_NOT_FOUND,
                )
            user_name.delete()
            return Response(
                {"status": "deleted", "id": name_id}, status=status.HTTP_204_NO_CONTENT
            )


@extend_schema_view(
    list=extend_schema(summary="List all groups"),
    retrieve=extend_schema(summary="Retrieve a specific group by ID"),
    create=extend_schema(summary="Create a new group"),
    update=extend_schema(summary="Update an existing group"),
    partial_update=extend_schema(summary="Partially update a group"),
    destroy=extend_schema(summary="Delete a group"),
)
class GroupViewSet(viewsets.ModelViewSet):
    queryset = Group.objects.all().order_by("name")
    serializer_class = GroupSerializer
    permission_classes = [permissions.IsAuthenticated, permissions.IsAdminUser]


# TODO: add remove account
@extend_schema_view(
    retrieve=extend_schema(summary="Retrieve the user's profile by id"),
)
class ProfileViewSet(viewsets.mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    queryset = CustomUser.objects.all()
    serializer_class = ProfileSerializer
    permission_classes = [permissions.IsAuthenticated]
    lookup_field = "id"

    @extend_schema(
        summary="Get or update the current user's profile",
        description="GET: Retrieve the current user's profile. POST: Update the current user's profile information.",
    )
    @action(detail=False, methods=["get", "post"], serializer_class=ProfileSerializer)
    def me(self, request):
        if request.method == "GET":
            serializer = self.get_serializer(self.request.user)
            return Response(serializer.data)
        elif request.method == "POST":
            serializer = self.get_serializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            username = serializer.data.get("username")
            display_name = serializer.data.get("display_name")
            user = self.request.user
            if display_name:
                user.display_name = display_name
            if username:
                user.username = username
            user.save()
            return Response(serializer.data, status=200)
