from rest_framework.permissions import IsAuthenticated
from rest_framework.permissions import DjangoModelPermissions
from django.http import HttpResponse
from rest_framework import viewsets, permissions
from .models import Request, Notification
from .serializers import (
    RequestSerializer,
    NotificationSerializer,
)
from rest_framework.decorators import action
from storages.backends.s3boto3 import S3Boto3Storage
from rest_framework.response import Response
from django.conf import settings
from drf_spectacular.utils import (
    extend_schema,
    OpenApiParameter,
    extend_schema_view,
    OpenApiExample,
    inline_serializer,
)
from drf_spectacular.types import OpenApiTypes
from rest_framework.permissions import IsAuthenticated, DjangoModelPermissions
from core.pagination import CustomLimitOffsetPagination
import boto3
import pika
from django.db import connection
from django.conf import settings
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework import status
from rest_framework.viewsets import ModelViewSet
from rest_framework.filters import SearchFilter, OrderingFilter
from django_filters.rest_framework import DjangoFilterBackend
from .models import Notification
from .serializers import (
    NotificationSerializer,
)
from .pagination import CustomLimitOffsetPagination
from rest_framework import serializers


@extend_schema_view(
    list=extend_schema(summary="List all requests"),
    retrieve=extend_schema(summary="Retrieve a specific request by ID"),
)
class RequestViewSet(viewsets.ModelViewSet):
    queryset = Request.objects.all()
    serializer_class = RequestSerializer
    permission_classes = [permissions.IsAuthenticated, permissions.IsAdminUser]
    http_method_names = ["get", "head", "options"]


class Storage(S3Boto3Storage):
    """Storage object for s3

    Default location is uncategorized, and bucket name is
    from settings.

    Args:
        S3Boto3Storage (S3Boto3Storage): Storage
    """

    bucket_name = settings.AWS_STORAGE_BUCKET_NAME
    default_acl = "public-read"
    file_overwrite = False
    location = "uncategorized"


@extend_schema_view(
    list=extend_schema(summary="List all notifications"),
    retrieve=extend_schema(summary="Retrieve a specific notification by id"),
    create=extend_schema(summary="Create a new notification"),
    update=extend_schema(summary="Update an existing notification"),
    partial_update=extend_schema(summary="Partially update a notification"),
    destroy=extend_schema(summary="Delete a notification"),
)
class NotificationViewSet(viewsets.ModelViewSet):
    queryset = Notification.objects.all()
    serializer_class = NotificationSerializer
    permission_classes = [DjangoModelPermissions]
    pagination_class = CustomLimitOffsetPagination

    def get_permissions(self) -> list[DjangoModelPermissions] | list[IsAuthenticated]:
        if self.action == "me":
            return [IsAuthenticated()]
        return [DjangoModelPermissions()]

    @extend_schema(
        summary="Get the current user's notifications (paginated)",
        description="Returns a paginated list of the current user's notifications. Marks notifications in the current page as 'got_notification' if not already marked.",
        responses={200: NotificationSerializer(many=True)},
    )
    @action(detail=False, methods=["get"])
    def me(self, request) -> Response:
        notifications = Notification.objects.filter(user=request.user).order_by(
            "-created_at"
        )
        paginator = CustomLimitOffsetPagination()
        page = paginator.paginate_queryset(notifications, request)
        # Update status to 'got_notification' only for notifications in the current page
        to_update = []
        for n in page:
            if n.status == Notification.STATUS_NOTHING:
                n.status = Notification.STATUS_GOT
                to_update.append(n)
        if to_update:
            Notification.objects.bulk_update(to_update, ["status"])
        serializer = self.get_serializer(page, many=True)
        return paginator.get_paginated_response(serializer.data)

    @extend_schema(
        summary="Mark notifications as viewed",
        description="Marks all notifications with status 'got_notification' as 'viewed_notification' for the current user.",
        responses={200: OpenApiTypes.OBJECT},
    )
    @action(detail=False, methods=["get"])
    def viewed(self, request) -> Response:
        notifications = Notification.objects.filter(
            user=request.user, status=Notification.STATUS_GOT
        )
        updated_count = notifications.update(status=Notification.STATUS_VIEWED)
        return Response(
            {"detail": "Notifications marked as viewed.", "updated": updated_count}
        )

    @extend_schema(
        summary="Mark a notification as opened",
        description="Marks a specific notification as 'opened_notification' using its id (provided as a query parameter).",
        parameters=[
            OpenApiParameter(
                name="id",
                type=OpenApiTypes.UUID,
                location=OpenApiParameter.QUERY,
                required=True,
                description="id of the notification to mark as opened.",
            )
        ],
        responses={
            200: OpenApiTypes.OBJECT,
            400: OpenApiTypes.OBJECT,
            404: OpenApiTypes.OBJECT,
        },
    )
    @action(detail=False, methods=["get"])
    def opened(self, request) -> Response:
        id = request.query_params.get("id")
        if not id:
            return Response({"detail": "Notification id is required."}, status=400)
        try:
            notification = Notification.objects.get(user=request.user, id=id)
        except Notification.DoesNotExist:
            return Response({"detail": "Notification not found."}, status=404)
        notification.status = Notification.STATUS_OPENED
        notification.save(update_fields=["status"])
        return Response({"detail": "Notification marked as opened."})


# Create response schema for health check
class HealthCheckResponseSerializer(serializers.Serializer):
    status = serializers.CharField(
        help_text="Overall health status: healthy, degraded, or unhealthy"
    )
    services = serializers.DictField(
        child=serializers.DictField(
            child=serializers.CharField(), help_text="Service status information"
        ),
        help_text="Detailed status of individual services",
        required=False,
    )


@extend_schema(
    summary="Health check endpoint",
    description="Health check endpoint that verifies the status of PostgreSQL database, S3 storage, RabbitMQ, and Forced Alignment service connections. Returns detailed status information for staff users.",
    responses={200: HealthCheckResponseSerializer, 503: HealthCheckResponseSerializer},
    examples=[
        OpenApiExample(
            "Healthy Response Example",
            value={
                "status": "healthy",
                "services": {
                    "database": {
                        "status": "healthy",
                        "details": "Database connection successful",
                    },
                    "s3": {"status": "healthy", "details": "S3 connection successful"},
                    "rabbitmq": {
                        "status": "healthy",
                        "details": "RabbitMQ connection successful",
                    },
                    "forced_alignment": {
                        "status": "healthy",
                        "details": "Forced alignment service is responding",
                    },
                },
            },
            response_only=True,
        ),
        OpenApiExample(
            "Degraded Response Example",
            value={
                "status": "degraded",
                "services": {
                    "database": {
                        "status": "healthy",
                        "details": "Database connection successful",
                    },
                    "s3": {
                        "status": "unhealthy",
                        "details": "AWS credentials not configured",
                    },
                    "rabbitmq": {
                        "status": "healthy",
                        "details": "RabbitMQ connection successful",
                    },
                    "forced_alignment": {
                        "status": "healthy",
                        "details": "Forced alignment service is responding",
                    },
                },
            },
            response_only=True,
        ),
        OpenApiExample(
            "Non-Staff User Response Example",
            value={"status": "healthy"},
            response_only=True,
        ),
    ],
)
@api_view(["GET"])
@permission_classes([AllowAny])
def health_check(request) -> Response:
    """
    Health check endpoint that verifies the status of:
    - PostgreSQL database connection
    - S3 storage connection
    - RabbitMQ connection
    """
    health_status = {
        "status": "healthy",
        "services": {
            "database": {"status": "unknown", "details": ""},
            "s3": {"status": "unknown", "details": ""},
            "rabbitmq": {"status": "unknown", "details": ""},
        },
    }

    healthy_services = 0
    total_services = 4

    # Check PostgreSQL database
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
        health_status["services"]["database"]["status"] = "healthy"
        health_status["services"]["database"][
            "details"
        ] = "Database connection successful"
        healthy_services += 1
    except Exception as e:
        health_status["services"]["database"]["status"] = "unhealthy"
        health_status["services"]["database"]["details"] = str(e)

    # Check S3 storage
    try:
        if settings.AWS_ACCESS_KEY_ID and settings.AWS_SECRET_ACCESS_KEY:
            s3_client = boto3.client(
                "s3",
                aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
                aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
                endpoint_url=settings.AWS_S3_ENDPOINT_URL,
            )
            # Try to list objects (this will fail if bucket doesn't exist or no access)
            s3_client.list_objects_v2(
                Bucket=settings.AWS_STORAGE_BUCKET_NAME, MaxKeys=1
            )
            health_status["services"]["s3"]["status"] = "healthy"
            health_status["services"]["s3"]["details"] = "S3 connection successful"
            healthy_services += 1
        else:
            health_status["services"]["s3"]["status"] = "unhealthy"
            health_status["services"]["s3"][
                "details"
            ] = "AWS credentials not configured"
    except Exception as e:
        health_status["services"]["s3"]["status"] = "unhealthy"
        health_status["services"]["s3"]["details"] = str(e)

    # Check RabbitMQ
    try:
        if settings.CELERY_BROKER_URL:
            # Parse the broker URL to get connection parameters
            import urllib.parse

            parsed_url = urllib.parse.urlparse(settings.CELERY_BROKER_URL)

            # Extract connection parameters
            host = parsed_url.hostname or "localhost"
            port = parsed_url.port or 5672
            username = parsed_url.username or "guest"
            password = parsed_url.password or "guest"
            virtual_host = parsed_url.path.lstrip("/") or "/"

            # Create connection parameters
            credentials = pika.PlainCredentials(username, password)
            parameters = pika.ConnectionParameters(
                host=host,
                port=port,
                virtual_host=virtual_host,
                credentials=credentials,
                connection_attempts=3,
                retry_delay=1,
            )

            # Test connection
            rabbitmq_connection = pika.BlockingConnection(parameters)
            rabbitmq_connection.close()

            health_status["services"]["rabbitmq"]["status"] = "healthy"
            health_status["services"]["rabbitmq"][
                "details"
            ] = "RabbitMQ connection successful"
            healthy_services += 1
        else:
            health_status["services"]["rabbitmq"]["status"] = "unhealthy"
            health_status["services"]["rabbitmq"][
                "details"
            ] = "CELERY_BROKER_URL not configured"
    except Exception as e:
        health_status["services"]["rabbitmq"]["status"] = "unhealthy"
        health_status["services"]["rabbitmq"]["details"] = str(e)

    # Determine overall status based on service health
    if healthy_services == total_services:
        overall_status = "healthy"
    elif healthy_services == 0:
        overall_status = "unhealthy"
    else:
        overall_status = "degraded"

    health_status["status"] = overall_status

    # Hide services details from non-staff users
    if not request.user.is_staff:
        health_status.pop("services", None)

    # Return appropriate HTTP status code
    if overall_status == "healthy":
        http_status = status.HTTP_200_OK
    elif overall_status == "degraded":
        http_status = status.HTTP_200_OK  # Still 200 but with degraded status
    else:  # unhealthy
        http_status = status.HTTP_503_SERVICE_UNAVAILABLE

    return Response(health_status, status=http_status)
