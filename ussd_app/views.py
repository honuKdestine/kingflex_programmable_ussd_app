# from venv import logger
from django.shortcuts import render
import json
import logging
from django.http import JsonResponse, HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from django.shortcuts import get_object_or_404
from .models import USSDSession, Price, Transaction, RetrievalRequest
import requests
from django.conf import settings
from dotenv import load_dotenv
import re

load_dotenv()
import os

logger = logging.getLogger(__name__)
log = logging.getLogger("ussd")


def get_proxies():
    proxy_url = os.environ.get("QUOTAGUARD_URL")
    if not proxy_url:
        return None  # fail gracefully in local dev
    return {
        "http": proxy_url,
        "https": proxy_url,
    }


# Create your views here.


# Helpers
def get_wassce_price_cents():
    try:
        price = Price.objects.get(item_code="wassce_checker", active=True)
        return price.price_cents
    except Price.DoesNotExist:
        # default placeholder (e.g., GHS 24.00)
        return 2400


# Note: email notify helper removed; retrieval requests are logged to DB (admin panel)


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
            "Message": "Welcome to Jel Services\n1. Buy WASSCE Results Checker\n2. Retrieve Voucher",
            "Label": "Main Menu",
            "ClientState": "",  # optional
            "DataType": "input",
            "FieldType": "text",
        }
        log.info("INCOMING: %s", request.body.decode())
        log.info("OUTGOING: %s", response)
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
                    "Message": "Enter number of checkers you want to buy (eg. 1)",
                    "Label": "Quantity",
                    "ClientState": "",
                    "DataType": "input",
                    "FieldType": "number",
                }
                log.info("INCOMING: %s", request.body.decode())
                log.info("OUTGOING: %s", resp)
                return JsonResponse(resp)
            elif text == "2":
                session.step = 101
                session.save()

                resp_rv_name = {
                    "SessionId": session_id,
                    "Type": "response",
                    "Message": "Enter your full name",
                    "Label": "Voucher Name",
                    "ClientState": "",
                    "DataType": "input",
                    "FieldType": "text",
                }

                log.info("INCOMING: %s", request.body.decode())
                log.info("OUTGOING: %s", resp_rv_name)
                return JsonResponse(resp_rv_name)

            # else:
            #     # Exit or invalid
            #     session.step = 0
            #     session.save()
            #     resp_exit = {
            #         "SessionId": session_id,
            #         "Type": "release",
            #         "Message": "Thanks for using Jel Services.",
            #         "Label": "Exit",
            #         "DataType": "display",
            #         "FieldType": "text",
            #     }
            #     log.info("INCOMING: %s", request.body.decode())
            #     log.info("OUTGOING: %s", resp_exit)
            #     return JsonResponse(resp_exit)

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
            resp_name = {
                "SessionId": session_id,
                "Type": "response",
                "Message": "Enter your full name",
                "Label": "Name",
                "ClientState": "",
                "DataType": "input",
                "FieldType": "text",
            }
            log.info("INCOMING: %s", request.body.decode())
            log.info("OUTGOING: %s", resp_name)
            return JsonResponse(resp_name)

        if session.step == 3:
            name = text
            session.data["name"] = name
            session.step = 4
            session.save()
            resp_phone = {
                "SessionId": session_id,
                "Type": "response",
                "Message": "Enter your phone number",
                "Label": "Phone",
                "ClientState": "",
                "DataType": "input",
                "FieldType": "phone",
            }
            log.info("INCOMING: %s", request.body.decode())
            log.info("OUTGOING: %s", resp_phone)
            return JsonResponse(resp_phone)

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
            resp_confirm = {
                "SessionId": session_id,
                "Type": "response",
                "Message": f"Confirm purchase of {qty} WASSCE checker(s) for GHS {total_ghs:.2f}\n1. Confirm\n2. Cancel",
                "Label": "Confirm Purchase",
                "ClientState": "",
                "DataType": "input",
                "FieldType": "number",
            }
            log.info("INCOMING: %s", request.body.decode())
            log.info("OUTGOING: %s", resp_confirm)
            return JsonResponse(resp_confirm)

        # --- Voucher retrieval flow handlers (name -> phone -> lookup) ---
        if session.step == 101:
            # user has been asked to enter full name for voucher retrieval
            session.data = session.data or {}
            session.data["rv_name"] = text
            session.step = 102
            session.save()

            resp_rv_phone = {
                "SessionId": session_id,
                "Type": "response",
                "Message": "Enter your phone number",
                "Label": "Voucher Phone",
                "ClientState": "",
                "DataType": "input",
                "FieldType": "phone",
            }
            log.info("INCOMING: %s", request.body.decode())
            log.info("OUTGOING: %s", resp_rv_phone)
            return JsonResponse(resp_rv_phone)

        if session.step == 102:
            # user submitted phone; check DB for matching successful transaction
            session.data = session.data or {}
            session.data["rv_phone"] = text
            session.step = 103
            session.save()

            rv_name = (session.data.get("rv_name") or "").strip().lower()
            rv_phone = (text or "").strip()

            def norm(p):
                return re.sub(r"\D", "", str(p or ""))

            rv_phone_n = norm(rv_phone)

            # Search for transactions where stored name and phone match (successful payments preferred)
            found_tx = None
            qs = Transaction.objects.order_by("-created_at")
            for tx in qs:
                try:
                    # prefer transactions marked success; but check all
                    sdata = getattr(tx.session, "data", {}) or {}
                    sname = (sdata.get("name") or "").strip().lower()
                    # receiver_phone may be in session data, or tx.mobile or session.mobile
                    rphone = (
                        sdata.get("receiver_phone")
                        or getattr(tx, "mobile", None)
                        or tx.session.mobile
                    )
                    if (
                        rv_name
                        and sname
                        and rv_name == sname
                        and rv_phone_n
                        and norm(rphone).endswith(rv_phone_n)
                    ):
                        # optionally ensure payment was successful
                        if tx.status == "success" or tx.order_id:
                            found_tx = tx
                            break
                        else:
                            # still consider pending/other statuses as potential matches
                            found_tx = tx
                            break
                except Exception:
                    continue

            if found_tx:
                # Log a RetrievalRequest pointing to the matched transaction
                rr = RetrievalRequest.objects.create(
                    session=session,
                    name=rv_name,
                    phone=rv_phone,
                    matched_transaction=found_tx,
                    status="matched",
                    notes={"matched_tx_status": found_tx.status},
                )

                resp_rv_received = {
                    "SessionId": session_id,
                    "Type": "release",
                    "Message": "We found a matching payment record.\nAdmin has been notified via the admin panel and will send your voucher shortly.",
                    "Label": "Voucher Request Received",
                    "DataType": "display",
                    "FieldType": "text",
                }
                log.info("Voucher retrieval logged (matched) id=%s tx=%s", rr.id, found_tx.id)
                log.info("INCOMING: %s", request.body.decode())
                log.info("OUTGOING: %s", resp_rv_received)
                return JsonResponse(resp_rv_received)
            else:
                # Log a RetrievalRequest with no match so admin can follow up
                rr = RetrievalRequest.objects.create(
                    session=session,
                    name=rv_name,
                    phone=rv_phone,
                    matched_transaction=None,
                    status="no_record",
                    notes={"info": "no matching transaction found"},
                )

                resp_no_record = {
                    "SessionId": session_id,
                    "Type": "release",
                    "Message": "No payment record found for the details provided.\nIf you believe you paid, please contact admin.",
                    "Label": "No Record Found",
                    "DataType": "display",
                    "FieldType": "text",
                }
                log.info("Voucher retrieval logged (no match) id=%s name=%s phone=%s", rr.id, rv_name, rv_phone)
                log.info("INCOMING: %s", request.body.decode())
                log.info("OUTGOING: %s", resp_no_record)
                return JsonResponse(resp_no_record)

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
                resp_payment = {
                    "SessionId": session_id,
                    "Type": "AddToCart",
                    "Message": "The request has been submitted. Please wait for a payment prompt soon",
                    "Item": item,
                    "Label": "Proceed to payment",
                    "DataType": "display",
                    "FieldType": "text",
                }
                log.info("INCOMING: %s", request.body.decode())
                log.info("OUTGOING: %s", resp_payment)
                return JsonResponse(resp_payment)
            else:
                session.step = 0
                session.save()
                resp_payment_cancelled = {
                    "SessionId": session_id,
                    "Type": "release",
                    "Message": "Transaction cancelled.",
                    "Label": "Cancelled",
                    "DataType": "display",
                    "FieldType": "text",
                }
                log.info("INCOMING: %s", request.body.decode())
                log.info("OUTGOING: %s", resp_payment_cancelled)
                return JsonResponse(resp_payment_cancelled)

    if msg_type == "Timeout":
        session.step = 0
        session.save()
        resp_payment_timedout = {
            "SessionId": session_id,
            "Type": "release",
            "Message": "Session timed out.",
            "Label": "Timeout",
            "DataType": "display",
            "FieldType": "text",
        }
        log.info("INCOMING: %s", request.body.decode())
        log.info("OUTGOING: %s", resp_payment_timedout)
        return JsonResponse(resp_payment_timedout)

    # default fallback
    resp_payment_error = {
        "SessionId": session_id,
        "Type": "release",
        "Message": "An error occurred.",
        "Label": "Error",
        "DataType": "display",
        "FieldType": "text",
    }
    log.info("INCOMING: %s", request.body.decode())
    log.info("OUTGOING: %s", resp_payment_error)
    return JsonResponse(resp_payment_error)


@csrf_exempt
@require_POST
def fulfillment(request):
    """Service Fulfillment URL - Hubtel calls this after payment is made according to documentation"""
    try:
        payload = json.loads(request.body.decode())
    except Exception:
        payload = request.POST.dict()

    logger.info("Fulfillment inbound: %s", payload)

    session_id = payload.get("SessionId")
    order_id = payload.get("OrderId")
    order_info = payload.get("OrderInfo", {})
    status = (order_info.get("Status") or payload.get("ServiceStatus") or "").lower()

    try:
        tx = (
            Transaction.objects.filter(client_reference=session_id)
            .order_by("-created_at")
            .first()
        )

        if not tx:
            logger.warning("No transaction found for session %s", session_id)
            return JsonResponse({"error": "Transaction not found"}, status=404)

        # Mark transaction result
        if status == "paid":
            tx.order_id = order_id
            tx.status = "success"
            tx.extra.update({"order_info": order_info})
            tx.save()

            # Prepare Hubtel callback payload
            callback_payload = {
                "OrderId": order_id,
                "ServiceStatus": "success",
                "Message": "Service delivered successfully",
            }

            callback_url = "https://gs-callback.hubtel.com:9055/callback"
            # callback_url = "https://webhook.site/af6f4284-0845-45ef-8b29-1bc44dcc21d4"

            # Attempt callback with retry logic
            for attempt in range(3):
                try:
                    response = requests.post(
                        callback_url,
                        json=callback_payload,
                        timeout=10,
                        headers={"Content-Type": "application/json"},
                        proxies=get_proxies(),
                    )
                    logger.info(
                        "Callback attempt %s to Hubtel: %s",
                        attempt + 1,
                        response.text,
                    )
                    if response.status_code == 200:
                        break  # success, no need to retry
                except Exception as e:
                    logger.error("Callback attempt %s failed: %s", attempt + 1, str(e))
                    if attempt == 2:
                        logger.critical(
                            "All callback attempts failed for order %s", order_id
                        )

        else:
            tx.status = "failed"
            tx.extra.update({"order_info": order_info})
            tx.save()

            # Optional: notify Hubtel of failed service (optional)
            failed_payload = {
                "OrderId": order_id,
                "ServiceStatus": "failed",
                "Message": "Payment received but service failed to deliver",
            }
            try:
                requests.post(
                    "https://gs-callback.hubtel.com:9055/callback",
                    # "https://webhook.site/af6f4284-0845-45ef-8b29-1bc44dcc21d4",
                    json=failed_payload,
                    timeout=10,
                    proxies=get_proxies(),
                )
            except Exception as e:
                logger.warning("Failed to send failure callback: %s", e)

    except Exception as e:
        logger.exception("Error processing fulfillment: %s", e)

    return JsonResponse({"ok": True})


# Mandatory: Utility to call Hubtel transaction status endpoint if you don't get fulfillment
# def check_transaction_status(
#     pos_sales_id, client_reference, auth_username, auth_password
# ):
#     """
#     Returns the JSON response from Hubtel transaction status endpoint.
#     Server IP must be whitelisted for this endpoint.
#     """
#     pos_sales_id = settings
#     url = f"https://api-txnstatus.hubtel.com/transactions/{pos_sales_id}/status"
#     params = {"clientReference": client_reference}
#     auth = (auth_username, auth_password)
#     resp = requests.get(url, params=params, auth=auth, timeout=15)
#     resp.raise_for_status()
#     return resp.json()


def check_transaction_status(client_reference):
    """
    Returns the JSON response from Hubtel transaction status endpoint.
    Uses the POS_SALES_ID from environment variables.
    """
    pos_sales_id = settings.POS_SALES_ID
    url = f"https://api-txnstatus.hubtel.com/transactions/{pos_sales_id}/status"
    params = {"clientReference": client_reference}

    try:
        resp = requests.get(
            url,
            params=params,
            timeout=15,
            proxies=get_proxies(),
        )
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logger.error("Error checking transaction status: %s", e)
        return {"error": str(e)}
