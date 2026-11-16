import os
from django.core.management.base import BaseCommand, CommandError
from django.contrib.auth import get_user_model


class Command(BaseCommand):
    help = (
        "Create or update a Django superuser from environment variables. "
        "Uses DJANGO_SUPERUSER_USERNAME, DJANGO_SUPERUSER_EMAIL, DJANGO_SUPERUSER_PASSWORD. "
        "Set DJANGO_SUPERUSER_PASSWORD_RESET=1 to reset password if user exists."
    )

    def handle(self, *args, **options) -> None:
        username = os.environ.get("DJANGO_SUPERUSER_USERNAME")
        email = os.environ.get("DJANGO_SUPERUSER_EMAIL")
        password = os.environ.get("DJANGO_SUPERUSER_PASSWORD")
        reset_flag = os.environ.get("DJANGO_SUPERUSER_PASSWORD_RESET", "0") == "1"

        if not (username and email and password):
            self.stdout.write(
                self.style.WARNING(
                    "Skipping ensure_superuser: required env vars not fully provided."
                )
            )
            return

        User = get_user_model()

        user = User.objects.filter(username=username).first()
        if user is None:
            User.objects.create_superuser(
                username=username, email=email, password=password
            )
            self.stdout.write(self.style.SUCCESS(f"Created superuser: {username}"))
            return

        # Update existing user idempotently
        changed = False
        if user.email != email:
            user.email = email
            changed = True
        if not user.is_staff:
            user.is_staff = True
            changed = True
        if not user.is_superuser:
            user.is_superuser = True
            changed = True
        if reset_flag:
            user.set_password(password)
            changed = True
        if changed:
            user.save()
            self.stdout.write(self.style.SUCCESS(f"Updated superuser: {username}"))
        else:
            self.stdout.write(
                self.style.SUCCESS(f"Superuser already up-to-date: {username}")
            )
