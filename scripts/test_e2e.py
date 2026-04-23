import os
import time
import uuid
import requests
from dotenv import load_dotenv
from supabase import create_client, Client
from colorama import init, Fore, Style

# Initialize colorama
init(autoreset=True)

load_dotenv()

# Config
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
API_KEY      = os.getenv("AGENT_API_KEY")
API_BASE_URL = "http://localhost:8000"

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

def log(msg, color=Fore.WHITE, bright=False):
    prefix = f"[{time.strftime('%H:%M:%S')}] "
    style = Style.BRIGHT if bright else ""
    print(f"{Fore.CYAN}{prefix}{style}{color}{msg}")

def trigger_ceo(mission: str):
    log(f"🚀 Triggering CEO with mission: '{mission}'", Fore.YELLOW, True)
    task_id = str(uuid.uuid4())
    payload = {
        "task_id": task_id,
        "from_agent": "manual_test",
        "to_agent": "ceo_agent",
        "task_type": "daily_strategy",
        "input": {
            "strategy_brief": f"Test Mission: {mission}",
            "is_test": True
        }
    }
    headers = {"X-Agent-Secret-Key": API_KEY}
    
    try:
        response = requests.post(f"{API_BASE_URL}/run", json=payload, headers=headers)
        if response.status_code == 202:
            log(f"✅ CEO accepted task {task_id}", Fore.GREEN)
            return task_id
        else:
            log(f"❌ API Error: {response.status_code} - {response.text}", Fore.RED)
            return None
    except Exception as e:
        log(f"❌ Connection Failed: {e}", Fore.RED)
        return None

def poll_tasks(ceo_task_id: str):
    log("🛰️  Waiting for CEO to establish strategy...", Fore.CYAN, True)
    start_time = time.time()
    ceo_done = False
    completed_agents = set()
    
    while time.time() - start_time < 600: # 10 min timeout
        # 1. Watch CEO
        if not ceo_done:
            res_ceo = supabase.table("tasks").select("status").eq("id", ceo_task_id).execute()
            if res_ceo.data and res_ceo.data[0]['status'] == "done":
                log("🧠 CEO Strategy established. Department tasks dispatched.", Fore.MAGENTA, True)
                ceo_done = True
                completed_agents.add("ceo_agent")
            elif res_ceo.data and res_ceo.data[0]['status'] == "dead_letter":
                log("❌ CEO CRASHED.", Fore.RED, True)
                return False

        # 2. Watch Departments (only those dispatched AFTER CEO started)
        if ceo_done:
            res = supabase.table("tasks").select("*").eq("from_agent", "ceo_agent").gt("created_at", time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(start_time))).execute()
            tasks = res.data
            
            active_count = 0
            for t in tasks:
                status = t['status']
                agent  = t['to_agent']
                if status == "done" and agent not in completed_agents:
                    log(f"✨ Agent '{agent}' COMPLETE", Fore.GREEN, True)
                    completed_agents.add(agent)
                elif status == "in_progress":
                    active_count += 1
            
            if len(completed_agents) >= 6: # CEO + 5 Depts
                print("\n")
                log("🏆 FULL PIPELINE VERIFIED SUCCESSFULLY!", Fore.YELLOW, True)
                log(f"Total Time: {int(time.time() - start_time)}s", Fore.WHITE)
                return True

        status_line = f"CEO: {'DONE' if ceo_done else 'Thinking...'} | Agents Complete: {len(completed_agents)-1 if ceo_done else 0}/5"
        print(f"\r{Fore.BLUE}{status_line}", end="", flush=True)
        time.sleep(3)
    
    log("⏰ Test Timeout reached.", Fore.RED)
    return False

if __name__ == "__main__":
    print(f"\n{Fore.MAGENTA}{'='*60}")
    print(f"{Fore.MAGENTA}   NOVAMIND END-TO-END VERIFICATION SYSTEM")
    print(f"{Fore.MAGENTA}{'='*60}\n")
    
    # 1. Trigger
    mission = "Establish a market presence for 'Zorp Corp' — space-themed AI logistics."
    task_id = trigger_ceo(mission)
    
    if task_id:
        # 2. Monitor
        poll_tasks(task_id)
    else:
        log("Aborting test due to trigger failure.", Fore.RED)
