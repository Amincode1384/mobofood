from django.db import migrations, models


def backfill_tracking_credentials(apps, schema_editor):
    """
    سفارش‌های موجود اعتبارنامهٔ پیگیری می‌گیرند تا هیچ سفارشی بدون
    مالکیت قابل استعلام نباشد.

    توکن این سفارش‌ها (که قبلاً به مشتری داده نشده بود) قابل حدس نیست و
    هیچ‌کس آن را نمی‌داند؛ برای پشتیبانیِ سفارش قدیمی از دستور
    ``python manage.py reissue_order_token <id>`` استفاده می‌شود.
    اطلاعات سفارش‌ها و وضعیتشان تغییر نمی‌کند.
    """
    from restaurant.order_security import hash_token, new_nonce, token_for_nonce

    Order = apps.get_model("restaurant", "Order")

    for order in Order.objects.filter(tracking_token_hash="").iterator():
        nonce = new_nonce()
        order.tracking_nonce = nonce
        order.tracking_token_hash = hash_token(token_for_nonce(nonce))
        order.save(update_fields=["tracking_nonce", "tracking_token_hash"])


class Migration(migrations.Migration):

    dependencies = [
        ("restaurant", "0006_orderidempotencykey"),
    ]

    operations = [
        migrations.AddField(
            model_name="order",
            name="tracking_nonce",
            field=models.CharField(blank=True, db_index=True, default="", max_length=64),
        ),
        migrations.AddField(
            model_name="order",
            name="tracking_token_hash",
            field=models.CharField(blank=True, default="", max_length=64),
        ),
        migrations.AddField(
            model_name="order",
            name="tracking_issued_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.RunPython(
            backfill_tracking_credentials,
            reverse_code=migrations.RunPython.noop,
        ),
    ]
