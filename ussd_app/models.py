from django.db import models


# Create your models here.
class Price(models.Model):
    item_code = models.CharField(max_length=64, unique=True)  # wassce_checker
    price_cents = models.IntegerField(help_text="Price in pesewas (1 GHS = 100)")
    active = models.BooleanField(default=True)

    def price_ghs(self):
        return self.price_cents / 100 

    def __str__(self):
        return f"{self.item_code} @ {self.price_ghs():.2f} GHS"


class USSDSession(models.Model):
    session_id = models.CharField(max_length=128, unique=True)
    mobile = models.CharField(max_length=32)
    sequence = models.IntegerField(default=1)
    client_state = models.CharField(max_length=256, blank=True, null=True)
    step = models.IntegerField(default=0)
    data = models.JSONField(
        default=dict
    )  # store {'qty':1,'name':'...','receiver_phone':'...'}
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.session_id} ({self.mobile}) step={self.step}"


class Transaction(models.Model):
    session = models.ForeignKey(
        USSDSession, on_delete=models.CASCADE, related_name="transactions"
    )
    order_id = models.CharField(max_length=128, blank=True, null=True)  # Hubtel OrderId
    client_reference = models.CharField(
        max_length=128, blank=True, null=True
    )  # use SessionId as clientReference
    amount_cents = models.IntegerField()
    status = models.CharField(
        max_length=32, default="pending"
    )  # pending/success/failed
    mobile = models.CharField()
    extra = models.JSONField(default=dict, blank=True)  # store response payloads
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def amount_ghs(self):
        return self.amount_cents / 100

    def __str__(self):
        return f"TX {self.id} {self.status} {self.amount_ghs():.2f} GHS"


class RetrievalRequest(models.Model):
    STATUS_CHOICES = (
        ("pending", "Pending"),
        ("matched", "Matched"),
        ("no_record", "No Record Found"),
    )

    session = models.ForeignKey(USSDSession, on_delete=models.SET_NULL, null=True, blank=True)
    name = models.CharField(max_length=255)
    phone = models.CharField(max_length=64)
    matched_transaction = models.ForeignKey(
        Transaction, on_delete=models.SET_NULL, null=True, blank=True
    )
    status = models.CharField(max_length=32, choices=STATUS_CHOICES, default="pending")
    notes = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at",)

    def __str__(self):
        return f"RetrievalRequest {self.id} {self.name} {self.phone} {self.status}"
    


    
