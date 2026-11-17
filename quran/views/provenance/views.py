from quran.serializers import ProvenanceSerializer
from quran.serializers import ProvenanceAddSerializer
from rest_framework import permissions, viewsets, status, filters, serializers
from rest_framework.response import Response
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework.decorators import action
from core import permissions as core_permissions
from core.pagination import CustomLimitOffsetPagination
from quran.models import Provenance
from quran.serializers import ProvenanceSerializer, ProvenanceAddSerializer
from django.db.models import Q
from account.models import CustomUser


class ProvenanceViewSet(viewsets.ModelViewSet):
    queryset = Provenance.objects.filter(
        ~Q(child_provenance=None) & Q(parent_provenance=None)
    )
    serializer_class = ProvenanceSerializer
    permission_classes = [
        core_permissions.IsCreatorOrReadOnly,
        # core_permissions.IsCreatorOfParentOrReadOnly,
        permissions.IsAuthenticatedOrReadOnly | permissions.DjangoModelPermissions,
    ]
    filter_backends = [
        DjangoFilterBackend,
        filters.SearchFilter,
        filters.OrderingFilter,
    ]
    search_fields = ["role"]
    ordering_fields = ["created_at"]
    pagination_class = CustomLimitOffsetPagination
    lookup_field = "id"

    def get_serializer_class(self) -> type[ProvenanceAddSerializer] | type[ProvenanceSerializer]:
        if self.action == "create":
            return ProvenanceAddSerializer
        return ProvenanceSerializer

    def update(self, request, *args, **kwargs) -> Response:
        try:
            provenance_id = kwargs.get("id")
            provenance = Provenance.objects.get(id=provenance_id)
        except Provenance.DoesNotExist:
            return Response(
                {"detail": "Object not found."}, status=status.HTTP_404_NOT_FOUND
            )

        if provenance.child_provenance:
            provenance.child_provenance.delete()

        request_data = request.data
        serializer = ProvenanceAddSerializer(
            provenance, data=request_data, partial=False
        )
        recip = request_data["recipient"]
        serializer.is_valid(raise_exception=True)

        # Now we create a new provenance if user provided one
        if recip:
            next = recip
            while next != None:
                n_account = CustomUser.objects.get(id=next["account_id"])
                c_proven = Provenance.objects.create(
                    creator=request.user, account=n_account, role=next["role"]
                )
                provenance.child_provenance = c_proven
                provenance.save()
                provenance = c_proven
                next = next["recipient"]

        return Response({"status": "updated"})
