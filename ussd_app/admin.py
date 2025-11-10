from django.contrib import admin
from .models import Price, USSDSession, Transaction


# Register your models here.
@admin.register(Price)
class PriceAdmin(admin.ModelAdmin):
    list_display = ("item_code", "price_cents", "active")
    list_editable = ("price_cents", "active")


@admin.register(USSDSession)
class USSDSessionAdmin(admin.ModelAdmin):
    list_display = ("session_id", "mobile", "step", "updated_at")
    readonly_fields = ("created_at", "updated_at")
    search_fields = ("session_id", "mobile")


@admin.register(Transaction)
class TransactionAdmin(admin.ModelAdmin):
    list_display = ("id", "session", "amount_cents", "status", "order_id", "created_at")
    readonly_fields = ("created_at", "updated_at")
    search_fields = ("order_id", "client_reference")

    
