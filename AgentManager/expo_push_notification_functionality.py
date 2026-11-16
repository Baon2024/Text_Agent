from exponent_server_sdk import PushClient, PushMessage

push_client = PushClient(timeout=20)


def send_expo_push(expo_push_token, title, body, data=None):
    ticket = push_client.publish(
        PushMessage(
            to=expo_push_token,
            title=title,
            body=body,
            data=data if data is not None else {},
            sound="default",
            priority="high",
            channel_id="default",
        )
    )

    print(f"Expo ticket: {ticket}")

    # Raises PushTicketError or a more specific subclass on failure.
    ticket.validate_response()

    return ticket


def check_expo_receipt(ticket):
    receipts = push_client.check_receipts_multiple([ticket])

    if not receipts:
        print("Receipt is not available yet")
        return None

    receipt = receipts[0]
    print(f"Expo receipt: {receipt}")

    receipt.validate_response()
    return receipt