from django.core.management.base import BaseCommand
from core import services


class Command(BaseCommand):
    help = "Daily repayment jobs: assess late fees on overdue installments and send payment reminders. Safe to run repeatedly."

    def handle(self, *args, **options):
        fees = services.assess_all_late_fees()
        reminders = services.send_reminders()
        self.stdout.write(f"Late fees assessed: {fees}. Reminders sent: {reminders}.")
