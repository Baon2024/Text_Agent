
import { createSupabaseServerClient } from "./supabaseHelper.js"
import WebSocket from 'ws'
import dotenv from 'dotenv';

dotenv.config()


const supabase = createSupabaseServerClient()


const ws = new WebSocket("ws://localhost:3001") // create



const channels = supabase.channel('new-driver-issue')
  .on(
    'postgres_changes',
    { event: 'INSERT', schema: 'public', table: 'driver_issues_agent_table' },
    (payload) => {
      console.log('Change received!', payload)

    // send new issue via websocket to agent instance
    let bodyToSend = { issue_id: payload.new.issue_id, type: "new_text_agent", state_controller: "AGENT", driver_id: payload.new.driver_id, driver_issue: payload.new.driver_issue, opening_message: payload.new.opening_message, issue_active: true }

    if (!ws || ws.readyState !== WebSocket.OPEN) {
      console.log("websocket is not connected");
      return;
    }


    ws.send(JSON.stringify(bodyToSend))


    }
  )
  .subscribe()




// send with websocket to port