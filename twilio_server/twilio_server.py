

import os
from twilio.rest import Client
from twilio.base.exceptions import TwilioRestException
from twilio.twiml.messaging_response import MessagingResponse
from fastapi import FastAPI, Form, Response, BackgroundTasks, Request, HTTPException
from pydantic import BaseModel
import requests
import time
import logging
import uvicorn
import random
# import function for sending new texts to agent handler
import websockets
import json
from dotenv import load_dotenv

load_dotenv()

app = FastAPI()

async def new_contractor_message_pipe(incoming_message, contractor_number, issue_number):
    websocket_payload = {"type": "contractor_message", "contractor_message": incoming_message, "contractor_number": contractor_number, "issue_phone_number": issue_number}
    print(f"[twilio inbound] forwarding to AgentManager websocket: {websocket_payload}", flush=True)

    try:
        async with websockets.connect("ws://localhost:3001") as socket:
            await socket.send(json.dumps(websocket_payload))
        print("[twilio inbound] successfully forwarded message to AgentManager websocket", flush=True)
    except Exception as e:
        print(f"[twilio inbound] failed to forward message to AgentManager websocket: {type(e).__name__}: {e}", flush=True)
        raise


class ResponseMessage(BaseModel):
    contractor_phone_number: str
    message: str
    agent_phone_number: str

client = Client(
      os.environ["TWILIO_ACCOUNT_SID"],
      os.environ["TWILIO_AUTH_TOKEN"]
  )

def send_twilio_text(from_phone_number, to_phone_number, message):
    max_attempts = 3

    for attempt in range(max_attempts):
        try:
            sent = client.messages.create(
                body=message,
                from_=from_phone_number,
                to=to_phone_number
            )
            
            print("data returned from twilio SDK: {sent}")

            return sent.sid

        except TwilioRestException as error:
            # HTTP response received from Twilio
            retryable = error.status in {429, 500, 502, 503, 504}

            if not retryable or attempt == max_attempts - 1:
                raise

        except requests.exceptions.ConnectTimeout:
            # Connection wasn't established
            if attempt == max_attempts - 1:
                raise

        except requests.exceptions.ReadTimeout:
            # Twilio may have accepted the SMS before the response
            # was lost. Don't automatically risk a duplicate.
            raise

        delay = (2 ** attempt) + random.uniform(0, 0.5)
        print(f"SMS attempt failed; retrying in {delay:.1f}s")
        time.sleep(delay)


@app.post("/send-sms")
def handle_sms_send(contents: ResponseMessage): ## recieve messages for sending to contractor
    ## or await contents = request.json() then breakout contents["message"] like express.js style 
    ## or the Body(...) version

    contractor_phone_number = contents.contractor_phone_number
    message = contents.message
    from_phone_number = contents.agent_phone_number

    print(f"[send-sms] request received: from={from_phone_number} to={contractor_phone_number} body={message!r}", flush=True)

    try:
        sent_sid = send_twilio_text(from_phone_number, contractor_phone_number, message)
        print(f"[send-sms] sent via Twilio: sid={sent_sid}", flush=True)

    except requests.exceptions.Timeout:
        logging.exception("Twilio request timed out")
        raise HTTPException(
            status_code=504,
            detail="Twilio request timed out; SMS acceptance is unconfirmed.",
        ) from None

    except requests.exceptions.RequestException:
        logging.exception("Twilio connection failed")
        raise HTTPException(
            status_code=502,
            detail="Unable to communicate with Twilio.",
        ) from None

    except TwilioRestException as error:
        logging.exception(
            "Twilio rejected SMS: status=%s code=%s",
            error.status,
            error.code,
        )
        raise HTTPException(
            status_code=502,
            detail={
                "message": "Twilio SMS request failed.",
                "twilio_code": error.code,
            },
        ) from None

    return {
        "status": "ok",
        "received_message": message,
    }

## need retry functionality if error thrown



@app.post("/welcome/sms/reply")
async def handle_message(request: Request, Body: str = Form(""), From: str = Form(""), To: str = Form("")):
    ## need three fields
    print(f"[twilio inbound] endpoint hit: method={request.method} path={request.url.path}", flush=True)
    print(f"[twilio inbound] raw form values: From={From!r} To={To!r} Body={Body!r}", flush=True)

    incoming_message = Body.lower().strip()
    contractor_number = From.lower().strip()
    issue_number = To.lower().strip()

    print(f"[twilio inbound] normalized values: contractor_number={contractor_number} issue_number={issue_number} incoming_message={incoming_message!r}", flush=True)

    ## pass on message using websockets function
    await new_contractor_message_pipe(incoming_message, contractor_number, issue_number)

    ## do need to respond straight away - even just empty

    empty_response_twiml = MessagingResponse()
    return Response(content=str(empty_response_twiml), media_type="application/xml")


if __name__ == "__main__":
    uvicorn.run("twilio_server:app")
