from django.contrib import admin
from .models import Order, OrderItem, Food, Category, Mission


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

admin.site.register(Category)

admin.site.register(Food)
