from __future__ import annotations
import asyncio
import inspect
import json
from dataclasses import dataclass
from typing import List
import requests
from google import genai
from google.genai import types
from dotenv import load_dotenv

load_dotenv()


clientGemini = genai.Client()


@dataclass
class Agent:
    system_prompt: None

    def __init__(self, passed_system_prompt, agent_id, supabase_function, switch_control_to_human, agent_phone_number, contractor_phone_number, backend_url, tools: list = None):
        self.known_tools_for_agent = {}
        self.is_running = False
        self.agent_id = agent_id
        self.system_instruction = passed_system_prompt
        self.current_response = None
        self.agent_phone_number = agent_phone_number
        self.contractor_phone_number = contractor_phone_number
        self.backend_url = backend_url
        self.fetch_messages_from_supabase = supabase_function
        self.switch_control_to_human = switch_control_to_human

        if tools:
            self.known_tools_for_agent = {
                tool.__name__: tool for tool in tools
            }

        self.tool_declarations = self.create_gemini_tool_schema()
        print(f"system_prompt_programatic looks like: {self.system_instruction}")

    def create_gemini_tool_schema(self):
        type_map = {
            str: "string",
            int: "integer",
            float: "number",
            bool: "boolean",
        }
        tool_declarations = []

        for tool_name, tool in self.known_tools_for_agent.items():
            signature = inspect.signature(tool)
            properties = {}
            required = []

            for param_name, param in signature.parameters.items():
                if param_name == "state":
                    continue

                annotation = param.annotation
                origin = getattr(annotation, "__origin__", None)
                args = getattr(annotation, "__args__", ())

                if origin is list or origin is List:
                    item_type = type_map.get(args[0], "string") if args else "string"
                    properties[param_name] = {
                        "type": "array",
                        "items": {"type": item_type},
                    }
                else:
                    properties[param_name] = {
                        "type": type_map.get(annotation, "string")
                    }

                if param.default is inspect._empty:
                    required.append(param_name)

            parameters_json_schema = {
                "type": "object",
                "properties": properties,
            }
            if required:
                parameters_json_schema["required"] = required

            tool_declarations.append(
                types.FunctionDeclaration(
                    name=tool_name,
                    description=(tool.__doc__ or "").strip(),
                    parametersJsonSchema=parameters_json_schema,
                )
            )

        return tool_declarations

    def send_message_to_contractor(self, message):
        body = {
            "contractor_phone_number": self.contractor_phone_number,
            "message": message,
            "agent_phone_number": self.agent_phone_number
        }

        try:
            response = requests.post(url=self.backend_url, json=body) # this is wrong
            response.raise_for_status()

        except requests.exceptions.RequestException as e:
            print(f"error was: {e}")

    async def generate_response(self):
        if self.current_response is not None:
            self.current_response.cancel()

        ## retrieve messages for agent, from supabase
        current_conversation_messages = self.fetch_messages_from_supabase(self.agent_id)

        self.current_response = asyncio.create_task(self.generate_response_inner(current_conversation_messages))

        return await self.current_response

    async def generate_response_inner(self, global_messages):
        await asyncio.sleep(10)

        agent_messages = [
            message
            for message in global_messages
            if message.get("issue_id") == self.agent_id or message.get("agent_id") == self.agent_id
        ]

        if not agent_messages:
            raise ValueError(f"no agent_messages for agent {self.agent_id}")

        contents = []
        for message in agent_messages:
            text = message.get("content", message.get("message", ""))
            if not isinstance(text, str):
                text = json.dumps(text, default=str)

            parts = []
            files = message.get("files")
            if files:
                for file in files:
                    parts.append(
                        types.Part.from_uri(
                            file_uri=file.uri,
                            mime_type=file.mime_type,
                        )
                    )
            parts.append(types.Part(text=text))

            role = message.get("role")
            if role in ("HUMAN", "CONTRACTOR"):
                gemini_role = "user" 
            else: 
                gemini_role = "model"
            contents.append(types.Content(role=gemini_role, parts=parts))

        response = await asyncio.to_thread(
            clientGemini.models.generate_content,
            model="gemini-3-flash-preview",
            contents=contents,
            config=types.GenerateContentConfig(
                temperature=0,
                systemInstruction=self.system_instruction,
                tools=[types.Tool(functionDeclarations=self.tool_declarations)],
                automaticFunctionCalling=types.AutomaticFunctionCallingConfig(disable=True),
            ),
        )

        if response.function_calls:
            function_call = response.function_calls[0]
            function_args = dict(function_call.args or {})
            function_result = self.known_tools_for_agent[function_call.name](**function_args,)
            if inspect.isawaitable(function_result):
                function_result = await function_result

            if function_call.name == "end_conversation_issue":
                print("Agent chose to end the conversation and mark it as complete - follow up message will now be sent..")
                final_message = "This conversation has now been marked as complete - Thank You"
                self.send_message_to_contractor(final_message)
        

                return { "type": "tool_call", "function_call_result": function_result, "final_message": final_message, "issue_active": False }
                
            if function_call.name == "transfer_conversation_issue_to_driver":
                print("Agent chose to transfer conversation to driver - follow up message will now be sent..")
                final_message = "I am now switching control to the driver, so you can text directly with them"
                self.switch_control_to_human(self.agent_id)
                self.send_message_to_contractor(final_message)
                return { "type": "tool_call", "function_call_result": function_result, "final_message": final_message, "issue_active": True }
            
            return {
                "type": "tool_call",
                "function_call_result": function_result,
            }

        print(f"response is a text answer: {response.text}")

        return {
            "type": "answer",
            "content": response.text or "",
        }
