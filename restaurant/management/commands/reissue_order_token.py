"""
صدور اعتبارنامهٔ تازهٔ پیگیری برای یک سفارش مشخص (برای پشتیبانی).

سفارش‌های قدیمی توکن ندارند و از مسیر عمومی قابل مشاهده نیستند. اگر
مشتری برای پیگیری سفارش قدیمی تماس گرفت، پشتیبانی با این دستور یک توکن
تازه می‌سازد و آن را فقط به همان مشتری (مثلاً تلفنی) می‌دهد.

توکن قبلی (اگر وجود داشت) باطل می‌شود.
"""

from django.core.management.base import BaseCommand, CommandError

from restaurant.models import Order
from restaurant.order_security import issue_token


class Command(BaseCommand):
    help = "Issue a fresh order tracking token for a single order (support use)."

    def add_arguments(self, parser):
        parser.add_argument("order_id", type=int, help="شناسهٔ سفارش")

    def handle(self, *args, **options):
        order = Order.objects.filter(id=options["order_id"]).first()

        if order is None:
            raise CommandError(f"سفارش #{options['order_id']} پیدا نشد.")

        token = issue_token(order)

        self.stdout.write(
            f"Order #{order.id} (table {order.table}) — new tracking token:"
        )
        self.stdout.write(token)
        self.stdout.write(
            "این مقدار فقط یک‌بار نمایش داده می‌شود؛ آن را فقط به مالک سفارش بدهید."
        )
