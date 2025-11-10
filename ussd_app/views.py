# from venv import logger
from django.shortcuts import render
import json
import os
import logging
from django.http import JsonResponse, HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from django.shortcuts import get_object_or_404
from .models import USSDSession, Price, Transaction
import requests
from django.conf import settings

logger = logging.getLogger(__name__)
# Create your views here.


# Helpers
def get_wassce_price_cents():
    try:
        price = Price.objects.get(item_code="wassce_checker", active=True)
        return price.price_cents
    except Price.DoesNotExist:
        # default placeholder (e.g., GHS 12.00)
        return 1200


@csrf_exempt
@require_POST
def interaction(request):
    """Service Interaction URL - Hubtel will POST JSON here"""
    try:
        payload = json.loads(request.body.decode())
    except Exception:
        payload = request.POST.dict()  # fallback
    logger.info("Inbound interaction: %s", payload)

    # Parse core fields
    session_id = payload.get("SessionId") or payload.get("sessionId")
    msg_type = payload.get("Type")  # Initiation / Response / Timeout
    message = payload.get("Message", "")  # the user's message or empty
    mobile = payload.get("Mobile") or payload.get("mobile")  # phone
    sequence = int(payload.get("Sequence", 1))
    client_state = payload.get("ClientState", "")

    # load or create session
    session, created = USSDSession.objects.get_or_create(
        session_id=session_id,
        defaults={
            "mobile": mobile,
            "sequence": sequence,
            "client_state": client_state,
            "step": 0,
        },
    )
    if not created:
        session.sequence = sequence
        session.client_state = client_state
        session.save()

    # Interpret Type
    if msg_type == "Initiation":
        # start flow
        session.step = 1
        session.data = {}
        session.save()
        response = {
            "SessionId": session_id,
            "Type": "response",
            "Message": "Welcome to Kingsflex Enterprise Services\n1. Buy WASSCE Results Checker\n2. Exit",
            "Label": "Main Menu",
            "ClientState": "",  # optional
            "DataType": "input",
            "FieldType": "text",
        }
        return JsonResponse(response)

    if msg_type == "Response":
        # appended current user text
        text = message.strip()
        # Use session.step to know what to ask next
        # Step mapping:
        # 1 -> user selected service (expects '1' or '2')
        # 2 -> quantity (expects number)
        # 3 -> full name
        # 4 -> receiver phone
        # 5 -> confirm (1 confirm, 2 cancel)
        if session.step == 1:
            if text == "1":
                session.step = 2
                session.save()
                resp = {
                    "SessionId": session_id,
                    "Type": "response",
                    "Message": "Enter number of checkers you want to buy",
                    "Label": "Quantity",
                    "ClientState": "",
                    "DataType": "input",
                    "FieldType": "number",
                }
                return JsonResponse(resp)
            else:
                # Exit or invalid
                session.step = 0
                session.save()
                return JsonResponse(
                    {
                        "SessionId": session_id,
                        "Type": "release",
                        "Message": "Thank you. Goodbye.",
                        "Label": "Exit",
                        "DataType": "display",
                        "FieldType": "text",
                    }
                )

        if session.step == 2:
            # parse quantity
            try:
                qty = int(text)
                if qty <= 0:
                    raise ValueError
            except Exception:
                return JsonResponse(
                    {
                        "SessionId": session_id,
                        "Type": "response",
                        "Message": "Invalid quantity. Enter a number (e.g., 1)",
                        "Label": "Quantity",
                        "ClientState": "",
                        "DataType": "input",
                        "FieldType": "number",
                    }
                )
            session.data["qty"] = qty
            session.step = 3
            session.save()
            return JsonResponse(
                {
                    "SessionId": session_id,
                    "Type": "response",
                    "Message": "Enter your full name",
                    "Label": "Name",
                    "ClientState": "",
                    "DataType": "input",
                    "FieldType": "text",
                }
            )

        if session.step == 3:
            name = text
            session.data["name"] = name
            session.step = 4
            session.save()
            return JsonResponse(
                {
                    "SessionId": session_id,
                    "Type": "response",
                    "Message": "Enter your phone number",
                    "Label": "Phone",
                    "ClientState": "",
                    "DataType": "input",
                    "FieldType": "phone",
                }
            )

        if session.step == 4:
            receiver_phone = text
            session.data["receiver_phone"] = receiver_phone
            # compute total
            price_cents = get_wassce_price_cents()
            qty = int(session.data.get("qty", 1))
            total_cents = price_cents * qty
            # create transaction (pending)
            tx = Transaction.objects.create(
                session=session,
                client_reference=session.session_id,
                amount_cents=total_cents,
                status="pending",
            )
            session.data["transaction_id"] = tx.id
            session.step = 5
            session.save()

            total_ghs = total_cents / 100
            return JsonResponse(
                {
                    "SessionId": session_id,
                    "Type": "response",
                    "Message": f"Confirm purchase of {qty} WASSCE checker(s) for GHS {total_ghs:.2f}\n1. Confirm\n2. Cancel",
                    "Label": "Confirm Purchase",
                    "ClientState": "",
                    "DataType": "input",
                    "FieldType": "number",
                }
            )

        if session.step == 5:
            if text == "1":
                # Confirm -> Hubtel expects AddToCart or Release + Hubtel will send to checkout
                tx_id = session.data.get("transaction_id")
                tx = Transaction.objects.get(id=tx_id)
                # required to return Type: "AddToCart" and include Item object
                item = {
                    "ItemName": "WASSCE Checker",
                    "Qty": session.data.get("qty", 1),
                    "Price": tx.amount_ghs(),
                }
                # Save extra if needed
                tx.extra = {"initiated_by": mobile}
                tx.save()
                # Hubtel will now present the checkout (user will make payment) and upon success call Service Fulfillment URL.
                return JsonResponse(
                    {
                        "SessionId": session_id,
                        "Type": "AddToCart",
                        "Message": "The request has been submitted. Please wait for a payment prompt soon",
                        "Item": item,
                        "Label": "Proceed to payment",
                        "DataType": "display",
                        "FieldType": "text",
                    }
                )
            else:
                session.step = 0
                session.save()
                return JsonResponse(
                    {
                        "SessionId": session_id,
                        "Type": "release",
                        "Message": "Transaction cancelled.",
                        "Label": "Cancelled",
                        "DataType": "display",
                        "FieldType": "text",
                    }
                )

    if msg_type == "Timeout":
        session.step = 0
        session.save()
        return JsonResponse(
            {
                "SessionId": session_id,
                "Type": "release",
                "Message": "Session timed out.",
                "Label": "Timeout",
                "DataType": "display",
                "FieldType": "text",
            }
        )

    # default fallback
    return JsonResponse(
        {
            "SessionId": session_id,
            "Type": "release",
            "Message": "An error occurred.",
            "Label": "Error",
            "DataType": "display",
            "FieldType": "text",
        }
    )


@csrf_exempt
@require_POST
def fulfillment(request):
    """Service Fulfillment URL - Hubtel calls this after payment is made according to documentation"""
    try:
        payload = json.loads(request.body.decode())
    except Exception:
        payload = request.POST.dict()
    logger.info("Fulfillment inbound: %s", payload)
    # Payload contains SessionId, OrderId, OrderInfo with Status, Payment etc.
    session_id = payload.get("SessionId")
    order_id = payload.get("OrderId")
    # OrderInfo nested
    order_info = payload.get("OrderInfo", {})
    status = order_info.get("Status") or (payload.get("ServiceStatus"))
    # find transaction by clientReference or session
    try:
        tx = (
            Transaction.objects.filter(client_reference=session_id)
            .order_by("-created_at")
            .first()
        )
        if not tx:
            logger.warning("No transaction found for session %s", session_id)
        else:
            if status and status.lower() == "paid":
                tx.order_id = order_id
                tx.status = "success"
                tx.extra.update({"order_info": order_info})
                tx.save()
                # call Hubtel callback endpoint to confirm service fulfillment
                # callback to https://gs-callback.hubtel.com:9055/callback
                # required to POST within 1 hour with ServiceStatus success/failed
                # We'll not call that external URL here; instead we return 200 OK and record the payment.
            else:
                tx.status = "failed"
                tx.extra.update({"order_info": order_info})
                tx.save()
    except Exception as e:
        logger.exception("Error processing fulfillment: %s", e)

    return JsonResponse({"ok": True})


# Mandatory: Utility to call Hubtel transaction status endpoint if you don't get fulfillment
def check_transaction_status(
    pos_sales_id, client_reference, auth_username, auth_password
):
    """
    Returns the JSON response from Hubtel transaction status endpoint.
    You must ensure your server IP is whitelisted for this endpoint.
    """
    url = f"https://api-txnstatus.hubtel.com/transactions/{pos_sales_id}/status"
    params = {"clientReference": client_reference}
    auth = (auth_username, auth_password)
    resp = requests.get(url, params=params, auth=auth, timeout=15)
    resp.raise_for_status()
    return resp.json()
