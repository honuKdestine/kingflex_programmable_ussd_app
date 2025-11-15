from django.contrib import admin, messages
from .models import Price, USSDSession, Transaction, RetrievalRequest
from django.utils.html import format_html
from django.urls import path
from django.shortcuts import redirect
from django.conf import settings
import requests


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


# @admin.register(Transaction)
# class TransactionAdmin(admin.ModelAdmin):
#     list_display = (
#         "id",
#         "session",
#         "amount_cents",
#         "mobile",
#         "status",
#         "order_id",
#         "created_at",
#     )
#     readonly_fields = ("created_at", "updated_at")
#     search_fields = ("order_id", "client_reference", "mobile")


@admin.register(Transaction)
class TransactionAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "session",
        "amount_cents",
        "status",
        "order_id",
        "client_reference",
        "created_at",
        "recheck_button",
    )
    readonly_fields = ("created_at", "updated_at")
    list_filter = ("status",)
    search_fields = ("client_reference", "order_id")

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path(
                "recheck/<int:transaction_id>/",
                self.admin_site.admin_view(self.recheck_status),
                name="transaction-recheck",
            ),
        ]
        return custom_urls + urls

    def recheck_button(self, obj):
        return format_html(
            '<a class="button" href="{}">Recheck Status</a>', f"recheck/{obj.id}/"
        )

    recheck_button.short_description = "Check Status"
    recheck_button.allow_tags = True

    def recheck_status(self, request, transaction_id):
        tx = Transaction.objects.get(id=transaction_id)
        try:
            pos_sales_id = getattr(settings, "POS_SALES_ID", None)
            if not pos_sales_id:
                self.message_user(
                    request, "POS_SALES_ID not set in .env", level=messages.ERROR
                )
                return redirect(f"../../{transaction_id}/change/")

            # Build URL
            url = f"https://api-txnstatus.hubtel.com/transactions/{pos_sales_id}/status"
            params = {"clientReference": tx.client_reference}

            response = requests.get(url, params=params, timeout=15)
            response.raise_for_status()
            data = response.json()

            # interpret Hubtel response
            status = data.get("data", {}).get("Status") or data.get("status")
            if status:
                tx.status = status.lower()
                tx.extra.update({"hubtel_check": data})
                tx.save()
                self.message_user(
                    request, f"Transaction updated to {status}", level=messages.SUCCESS
                )
            else:
                self.message_user(
                    request,
                    "No status found in Hubtel response",
                    level=messages.WARNING,
                )

        except Exception as e:
            self.message_user(
                request, f"Error checking status: {e}", level=messages.ERROR
            )

        return redirect(f"../../{transaction_id}/change/")


@admin.register(RetrievalRequest)
class RetrievalRequestAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "phone", "status", "matched_transaction", "created_at")
    readonly_fields = ("created_at",)
    list_filter = ("status",)
    search_fields = ("name", "phone")
