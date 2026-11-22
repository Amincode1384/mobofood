from django.contrib import admin
from .models import Order, OrderItem, Food, Category, Mission
from .error_logs import SystemErrorLog


class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 0


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):

    list_display = (
        "id",
        "table",
        "status",
        "total",
        "created_at",
    )

    list_filter = (
        "status",
    )

    inlines = [OrderItemInline]


@admin.register(Mission)
class MissionAdmin(admin.ModelAdmin):

    list_display = (
        "mission_code",
        "mission_uid",
        "order",
        "destination_table",
        "robot_id",
        "status",
        "created_at",
    )

    list_filter = (
        "status",
        "destination_table",
    )

    search_fields = (
        "mission_code",
        "mission_uid",
        "order__id",
        "destination_table",
    )

    readonly_fields = (
        "mission_code",
        "mission_uid",
        "created_at",
        "started_at",
        "completed_at",
    )

    fieldsets = (
        ("\ufeffشناسه", {
            "fields": ("mission_code", "mission_uid"),
            "classes": ("wide",),
        }),
        ("\ufeffاطلاعات سفارش و مقصد", {
            "fields": ("order", "destination_table"),
        }),
        ("\ufeffربات و وضعیت", {
            "fields": ("robot_id", "status", "started_at", "completed_at"),
        }),
        ("\ufeffتاریخ‌ها", {
            "fields": ("created_at",),
        }),
    )

@admin.register(SystemErrorLog)
class SystemErrorLogAdmin(admin.ModelAdmin):
    """فهرست خطاهای سیستم — فقط‌خواندنی؛ فیلتر و جست‌وجو برای بررسی سریع."""

    list_display = (
        "created_at",
        "level",
        "source",
        "code",
        "short_message",
        "order_uid",
        "mission_uid",
        "robot_id",
        "occurrences",
    )

    list_filter = ("level", "source", "code")

    search_fields = (
        "code",
        "message",
        "order_uid",
        "mission_uid",
        "robot_id",
        "path",
    )

    readonly_fields = [f.name for f in SystemErrorLog._meta.fields]

    date_hierarchy = "created_at"

    def short_message(self, obj):
        text = obj.message or ""
        return text if len(text) <= 80 else text[:79] + "…"

    short_message.short_description = "پیام"

    def has_add_permission(self, request):
        # این جدول فقط از کد برنامه پر می‌شود.
        return False

    def has_change_permission(self, request, obj=None):
        return False


admin.site.register(Category)

admin.site.register(Food)
