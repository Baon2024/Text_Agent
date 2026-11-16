import websockets
import asyncio
import json
import requests
import sys
from pathlib import Path
import os
import uuid
from postgrest import APIError
import httpx
from supabase import create_client, Client
import logging
from agent import Agent
from expo_push_notification_functionality import send_expo_push, check_expo_receipt
from datetime import datetime, timezone

## await ws.send()
url = os.getenv("SUPABASE_URL")
key = os.getenv("SUPABASE_KEY")
supabase_client: Client = create_client(url, key)

agent_collection = {} # or could use map
conversation_messages = []
#state_controller = "agent" # can be "agent" or "human" - make ti work for multiple different conversations later, indexed by issue uuid

phone_numbers = {}
driver_websocket_connections = {}

backend_url = "http://localhost:8000/send-sms" ## don't need public ngrok url for sending to fastapi on same laptop

agent_phone_number = "+447488891740" ## is this correct?
contractor_phone_number = "+447311252643"

## switch to put messages in supabase, and retrieve from there
## switching state_controller needs to be the supabase function

#phone_numbers[test_number] = None ## will be more complicated in practise

def release_phone_number(issue_id):

    #del phone_numbers[issue_id] - not sure how to release when it's indexd by contractor phone number??
    # just levae for now anyway
    for contractor_number, saved_issue_id in list(phone_numbers.items()): # return as list, as deleting from object while iterating through it can raise RuntimeError
        if saved_issue_id == issue_id:
            del phone_numbers[contractor_number]


    print(f"phone number released from issue {issue_id}")
    return


def mark_issue_complete(issue_id):

    try:
        
        response = (
            supabase_client.table("driver_issues_agent_table")
            .update({ "issue_active": False})
            .eq("issue_id", issue_id)
            .execute()
        )

        return {"success": True, "result": response.data}

    except APIError as error:
        return {"success": False, "error": error}
    except httpx.RequestError as error:
        return {"success": False, "error": error}

        

def retrieve_issue_id(issue_phone_number):
    issue_id = phone_numbers.get(issue_phone_number) ## should be value of the phone number's map
    print(f"issue_id retrieved inside of retrieve_issue_id: {issue_id}")
    if issue_id:
        return { "issue_id_exists": True, "issue_id": issue_id }
    return { "issue_id_exists": False, "issue_id": issue_id }

def save_contractor_message(message, issue_id, role):
    conversation_messages.append({ "issue_id": issue_id, "message": message, "role": role})
    return

def save_contractor_message_supabase(message, issue_id, role, message_id=None):

    ## supabase instance
    if not message_id:
        message_id = str(uuid.uuid4())

    ##
    result = (
        supabase_client.table("driver_conversation_messages")
        .insert([{ "message": message, "issue_id": issue_id, "role": role, "message_id": message_id  }])
        .execute()
    )

    response = result.data
    print(f"response from INSERT to save_contractor_message_supabase: {response}")
    return response

## need to pass down to agent a function that can retrieve supabase messages
def retrieve_issue_supabase_messages(issue_id):

    response = (
        supabase_client.table("driver_conversation_messages")
        .select("*")
        .eq("issue_id", issue_id)
        .order("created_at", desc=False)
        .execute()
    )
    results = response.data
    print(f"results from retrieve_issue_supabase_messages are: {results}")
    return results


def switch_control_to_human(issue_id):

    response = (
        supabase_client.table("driver_issues_agent_table")
        .update({"state_controller": "HUMAN"})
        .eq("issue_id", issue_id)
        .execute()
    )

    result = response.data
    print(f"result from switch_control_to_human is: {result}")
    return result

def fetch_state_controller(issue_id):

    response = (
        supabase_client.table("driver_issues_agent_table")
        .select("*")
        .eq("issue_id", issue_id)
        .execute()
    )

    if not response.data:
        #raise ValueError(f"issue_id not found: {issue_id}")
        return { "state_controller_available": False, "state_controller": None }

    result = response.data[0]["state_controller"]
    print(f"current state_controller is: {result}")
    if result is not None:
        return { "state_controller_available": True, "state_controller": result.strip() }
        
    else:
        return { "state_controller_available": False, "state_controller": None }


    # save to supabase, need issue_id for retrieval inside of agent, and role for knowing who said it in frontend presentation



def fetch_driver_notifications_permission_from_issue(issue_id):
    "fetch driver_user_id from issue_id, then check if psuh notification permission exists"

    ## 1 get driver_user_id from issue_id
    try:
        response = (
            supabase_client.table("driver_issues_agent_table")
            .select("driver_id")
            .eq("issue_id", issue_id)
            .execute()
        )

    except APIError as e:
        print(f"api error hit in supabase table request: {e}")
        return { "error": True, "push_notification_permission": None, "token": None }
    except httpx.RequestError as e:
        print(f"Network error: {e}")
        return { "error": True, "push_notification_permission": None, "token": None }

    else:
        if not response.data:
            print(f"no driver_id exists in driver_issues_agent_table for issue_id of: {issue_id}")
            return { "error": True, "push_notification_permission": None, "token": None }
        elif response.data[0]["driver_id"]:
            print(f"driver_id found from issue_id: {response.data[0]["driver_id"]}")

            ##2 - get push notifications permission for the driver
            try:

                # and check whether heartbeat is within acceptable time


                response = (
                    supabase_client.table("van_driver_user_details")
                    .select("expo_push_notification", "last_active_heartbeat")
                    .eq("auth_user_id", response.data[0]["driver_id"])
                    .execute()
                    )
                ## need a third call to issue_id_user_activity for heartbeat data

            except APIError as e:
                print(f"api error hit in supabase table request: {e}")
                return { "error": True, "push_notification_permission": False, "token": None }
            except httpx.RequestError as e:
                print(f"Network error: {e}")
                return { "error": True, "push_notification_permission": False, "token": None }
            else:
                if not response.data:
                    print("no van_driver_issues_table row exists for the auth_user_id used")
                    return { "error": True, "push_notification_permission": False, "token": None }
                elif response.data[0]["expo_push_notification"] and response.data[0]["last_active_heartbeat"]:


                    print("expo_push_notification exists - permission exists")
                    ## check if heartbeat is within acceptable time
                    #heartbeat = datetime.fromisoformat(response.data[0]["last_active_heartbeat"].replace("Z", "+00:00"))
                    #now = datetime.now(timezone.utc)
                    #elapsed_seconds = (now - heartbeat).total_seconds()
                    #if elapsed_seconds >= 30: ## frontend will update every 15 seconds, so 30 seconds is enough time ot be certain
                        ## then send push notification
                    return { "error": False, "push_notification_permission": True, "token": response.data[0]["expo_push_notification"], "should_send_push_notification": True }

                    #return { "error": False, "push_notification_permission": True, "token": response.data[0]["expo_push_notification"], "should_send_push_notification": False }
                else:
                    print("either expo_push_notification or last_active_heartbeat didn't exist - so will return notification functionality as false..")
                    return { "error": False, "push_notification_permission": False, "token": response.data[0].get("expo_push_notification", None), "should_send_push_notification": False }
                    




def send_message_to_contractor(contractor_phone_number, message, agent_phone_number):

    ## send to api backend
    body = {
        "contractor_phone_number": contractor_phone_number,
        "message": message,
        "agent_phone_number": agent_phone_number
    }


    try:
        response = requests.post(url=backend_url, json=body) # this is wrong
        response.raise_for_status()

    except requests.exceptions.RequestException as e:
        print(f"error was: {e}")


## FUNCTIONS FOR AGENT

def end_conversation_issue(issue_id):
    
    result = mark_issue_complete(issue_id)

    if result["success"]:
        if agent_collection.get(issue_id, None) is not None:
            relevant_agent = agent_collection.get(issue_id, None)
            print(f"relevant_agent is: {relevant_agent}")
            if relevant_agent:
                agent_collection[issue_id]["active"] = False
            return True
    else:
        print("issued wasn't successfully marked as complete in supabase - returning falsy")
        return False
    
    # should also send final message to contractor, indicating conversation is over
    ## just send final message from within the function?

def transfer_conversation_issue_to_driver(agent_id, issue_id):
    global state_controller
    state_controller = "Driver"
    return "state controller is now the Driver"

# both of these need to interact with global states, update supabase, and maybe send push notifications
# params passed in by agent, so need to pass issue_id as variable to systen_message


def create_agent_system_prompt(agent_context, issue_id):
    system_prompt = f"""
     You are an agent in a workflow for interacting with a contractor about coming to help a driver with an issue.
      Your role is to provide the contractor with the information they require, in order to successfuly allow them to arrange
      finding the driver. You only handle one "turn" in the conversation.

      Your issue_id is: {issue_id}

     This is the context of the issue: {agent_context}.

     You must decide whether (1) to respond with a message, whcih you can do by just returning a message and calling no tools.
     Or, you can call one tool, either (2a) transfer the issue to the driver themselves, so they'll be able to respond to the contratcor, or
     (2b) mark the conversation (and so the issue) as ended, if the contractor has all the information they need.

     *Guidelines*
     - if the contratcor asks you for details you don't know, because you haven't been provided with any information. don't make them up.
       Instead, transfer the conversation to the driver. 
    
     And the existing conversation, if it's already been initiated, can be seen below:

    """

    return system_prompt


## FUNCTIONS FOR AGENT

async def handler(ws):
    global agent_collection

    

    try:
        async for message in ws:
            ## for human response, I need to take ws, save to ws connections with issue id as key
            

            parsed_message = json.loads(message)

            match parsed_message["type"]: ## just use this, is faster
                case "driver_frontend_client_registration":
                    ## need to get issue_id, and save wbesocket by it
                    if parsed_message.get("issue_id", None):
                        issue_id = parsed_message.get("issue_id")
                        driver_websocket_connections[issue_id] = ws
                        continue


                case "new_text_agent":
                    issue_id = parsed_message["issue_id"]
                    agent_context = parsed_message["driver_issue"]
                    ## agent_phone_number needs to be made dynamic, later


                       ## get available phone number - hardcode initially, whole doing basic version
                    ## need issue_summary, from supabase table, that can be passed to agent as context
                    _agent = Agent(create_agent_system_prompt(agent_context, issue_id), issue_id, retrieve_issue_supabase_messages, switch_control_to_human, agent_phone_number=agent_phone_number, contractor_phone_number=contractor_phone_number, backend_url=backend_url, tools=[end_conversation_issue, transfer_conversation_issue_to_driver]) # need to pass phone_number down, so function for sending message with twilio sends from right number
               ## same for contractor number
                    agent_collection[issue_id] = { "phone_number": agent_phone_number, "agent": _agent, "active": True } # later controller "agent"/"human" should be stored here

                    # need to create connection between issue_id, and contractor's phone number
                    phone_numbers[contractor_phone_number] = issue_id
                    # once multiple text agents can exist, the connection will need to be between agent_phone_number and issue_id instead
                    # as one contractor can have multiple agents it's conversing with - whereas one agent can only have one conversation at a time

                    # send first messages
                    first_message = parsed_message["opening_message"]
                    ## also need to send a message indicating contractor is taling to a virtual agent
                    virtual_agent_message_flag = "You are now speaking to a virtual Agent, on behalf of one of our drivers"
                    send_message_to_contractor(contractor_phone_number, virtual_agent_message_flag, agent_phone_number)
                    send_message_to_contractor(contractor_phone_number, first_message, agent_phone_number)
                    save_contractor_message_supabase(first_message, issue_id, "AGENT")
                    ## need to creat and save the new agent, and get free number and separetuly sned the first message with that number
                

                case "human_driver_issue_id_closure":
                    issue_id = parsed_message.get("issue_id", None)
                    message_id = parsed_message.get("message_id")

                    result = mark_issue_complete(issue_id)
                    print(f"result from mark_issue_complete is: {result}")
                    # update local agent attached to issue to now be inactive
                    

                    if result["success"]:
                        ## send message that issue successfuly closed
                        human_websocket = driver_websocket_connections.get(issue_id, None)
                        print(f"human_websocket before sending response is: {human_websocket}")
                        #if human_websocket:
                        await ws.send(json.dumps({"message_type": "issue_closed_successfully", "issue_id": issue_id, "message_id": message_id }))
                        release_phone_number(issue_id)
                    else:
                        human_websocket = driver_websocket_connections.get(issue_id, None)
                        print(f"human_websocket before sending response is: {human_websocket}")
                        #if human_websocket:
                        await ws.send(json.dumps({"message_type": "issue_closed_failure", "issue_id": issue_id, "message_id": message_id, "error": result["error"] }))

                    continue

                case "human_driver_response":
                    ##print("human_driver_response functionality not added yet!")
                    ## need to retrieve message from the dict, and then send through to the contractor using the funcion above
                    issue_id = parsed_message.get("issue_id")
                    message_id = parsed_message.get("message_id")
                    if issue_id is None:
                        print(f"unable to save human_driver_response. Reason: issue_id does not exist: {issue_id}")

                    message = parsed_message.get("message")
                    if message is None:
                        print(f"unable to save human_driver_response. Reason: message does not exist: {message}")
                        continue

                    save_contractor_message_supabase(message, issue_id, "HUMAN", message_id)
                    send_message_to_contractor(contractor_phone_number, message, agent_phone_number)
                    
                    continue
                    # save to global messages, then send_message() to contractor - both using issue id
                
                case "contractor_message":
                    # "contractor_message": incoming_message, "contractor_number": contractor_number, "issue_phone_number": issue_number
                    ## need to save to global messages
                    print("contractor_message recieved!- generating response")
                    issue_id_response = retrieve_issue_id(parsed_message["contractor_number"]) # match phone number to issue uuid
                    contractor_number = parsed_message["contractor_number"]
                    if not issue_id_response["issue_id_exists"]:
                        print("issue_id does not exist, was not retrieved in contractor_message conditional - cannot proceed")
                        continue
                    issue_id = issue_id_response["issue_id"]
                    ## check if issue is still active
                    issue_still_active = agent_collection[issue_id]["active"]
                    if not issue_still_active:
                        print("issue no longer active - terminating response to contractor message")
                        return


                    message_id = str(uuid.uuid4())
                    save_contractor_message_supabase(parsed_message["contractor_message"], issue_id, "CONTRACTOR", message_id)
                    state_controller_response = fetch_state_controller(issue_id)
                    if state_controller_response["state_controller_available"]:
                        state_controller = state_controller_response["state_controller"]
                    else:
                        print("state controller not available from fetch_state_controller - terminating workflow..")
                        return

                    if state_controller == "AGENT":
                        ## need to retrieve agent and generate response
                        agent = agent_collection[issue_id]["agent"]
                        if not agent:
                            print("state_controller is in agent_mode - but agent of that issue_id does not exist!")
                            continue

                        ## generate new response from agent
                        agent_response = await agent.generate_response()
                        if agent_response["type"] == "answer":
                            save_contractor_message_supabase(agent_response["content"], issue_id, "AGENT")
                            send_message_to_contractor(contractor_phone_number, agent_response["content"], agent_phone_number)
                        elif agent_response["type"] == "tool_call":
                            ## I need a way to trigger push notification to tell driver they're now conversing directly with contractor
  

                            save_contractor_message_supabase(agent_response["final_message"], issue_id, "AGENT")
                            if not agent_response["issue_active"]:
                                agent_collection[issue_id]["active"] = False ##turning of active flag in global memory
                                ## also need to update it here for supabase


                    elif state_controller == "HUMAN":
                        #print("human functionality not ready yet!")
                        ## check if client connection exists by issue_id - if it does, sent it through

                        message = parsed_message.get("contractor_message")



                        
                        response = fetch_driver_notifications_permission_from_issue(issue_id)
                        if response["push_notification_permission"]:
                            print("push notification permission exists for ther user - will proceed to send notification..")

                            ## send notification
                            send_push_notification = response["should_send_push_notification"]
                            if send_push_notification:
                                #print("user is not currently in-app, or wasn't within last 15 seconds - send push notification to inform of driver's message")
                                print("Sending push notification - whether or not user is currently in-app")
                                expo_push_notification_token = response["token"]
                                try: 
                                    receipt = send_expo_push(expo_push_notification_token,"You have a new message", message)
                                    check_expo_receipt(receipt)
                                except Exception as e:
                                    logging.exception(f"there was an error sending notification via expo: %s", e)
                            
                            else:
                                 print("user is currently in-app, or wasn't within last 15 seconds - no need to send push notification to inform them of driver's message")
                                                                
                        else:
                            print("driver does not have push notification permission approved - skipping sending push notification..")

                        human_websocket = driver_websocket_connections.get(issue_id, None)
                        if human_websocket is None:
                            print("no human_websocket available - skipping sending message to frontend..")
                            continue
                        
                        await human_websocket.send(json.dumps({"message_type": "driver_message", "message": message, "message_id": message_id, "role": "CONTRACTOR", "issue_id": issue_id }))
                        continue
                        ## need to pass on to human websokcet client frontend
                    # and, if agent controlle ris 'human' of id of message, send on to agent

                   


    except websockets.exceptions.ConnectionClosed:
        print("websocket connection closed!")




async def main():                                                                                                                                                                                                                                                                                                                                                           
    async with websockets.serve(handler, "localhost", 3001):                                                                        
        print("websocket server listening on 3001")                                                                                 
        await asyncio.Future() 

asyncio.run(main())