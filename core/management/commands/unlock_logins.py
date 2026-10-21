from django.core.management.base import BaseCommand
from core import throttle


class Command(BaseCommand):
    help = "Clear sign-in lockouts: for one username, or for everyone when no username is given."

    def add_arguments(self, parser):
        parser.add_argument("username", nargs="?", help="Only unlock this username")

    def handle(self, *args, username=None, **options):
        n = throttle.unlock(username)
        self.stdout.write(f"Cleared {n} lockout record(s){' for ' + username if username else ''}.")
