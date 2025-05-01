import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from flask import Flask, request, jsonify, render_template, send_from_directory
import asyncio
import threading
import uuid
from playwright.async_api import async_playwright
# from browser_use import Agent # Assuming browser_use is installed and compatible

app = Flask(__name__, static_folder='static', template_folder='static') # Use standard quotes

# --- Configuration ---
RECORDINGS_DIR = "/home/ubuntu/recordings"

# *** MODIFIED: Use WebDriver endpoint instead of CDP ***
# This should be set to something like "http://browser:4444/wd/hub" in deploy.yml
BROWSER_ENDPOINT = os.environ.get("BROWSER_ENDPOINT")
if not BROWSER_ENDPOINT:
    print("ERROR: BROWSER_ENDPOINT environment variable not set. Cannot connect to browser.")
    # Exit or handle appropriately if the endpoint is critical
    # sys.exit("BROWSER_ENDPOINT not set")

# Public VNC URL for external connection
PUBLIC_VNC_URL = os.environ.get("PUBLIC_VNC_URL")
if not PUBLIC_VNC_URL:
    print("WARNING: PUBLIC_VNC_URL environment variable not set. VNC connection might fail.")

# Ensure recordings directory exists (though recording might not work with WebDriver connect)
os.makedirs(RECORDINGS_DIR, exist_ok=True)

# Dictionary to track workflow states and details
active_workflows = {}

# --- Helper Function to Run Workflow Asynchronously ---
def run_workflow_thread(workflow_id, target_url, actions):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(run_browser_use_workflow(workflow_id, target_url, actions))
    finally:
        loop.close()

async def run_browser_use_workflow(workflow_id, target_url, actions):
    # NOTE: Video recording via Playwright typically requires CDP connection.
    # It might not work when connecting via WebDriver endpoint.
    # recording_filename = f"{workflow_id}.webm"
    # recording_path_in_container = os.path.join(RECORDINGS_DIR, recording_filename)
    
    active_workflows[workflow_id]["status"] = "connecting_browser"
    
    browser = None
    context = None
    page = None
    try:
        async with async_playwright() as p:
            # --- MODIFIED: Connect using WebDriver endpoint with Retry Logic --- 
            max_retries = 3
            retry_delay_seconds = 5
            for attempt in range(max_retries):
                try:
                    print(f"Attempt {attempt + 1}/{max_retries} connecting to WebDriver: {BROWSER_ENDPOINT}")
                    # Use connect() for WebDriver endpoint. Timeout is crucial.
                    browser = await p.chromium.connect(BROWSER_ENDPOINT, timeout=30000) # Increased timeout
                    print("WebDriver connected successfully.")
                    break # Exit loop if connection successful
                except Exception as e:
                    print(f"Error connecting to browser WebDriver (Attempt {attempt + 1}): {e}")
                    if attempt < max_retries - 1:
                        print(f"Retrying in {retry_delay_seconds} seconds...")
                        await asyncio.sleep(retry_delay_seconds)
                    else:
                        print("Max retries reached. Failed to connect to browser WebDriver.")
                        active_workflows[workflow_id]["status"] = "failed"
                        active_workflows[workflow_id]["error"] = f"Failed to connect to browser WebDriver after {max_retries} attempts: {e}"
                        return # Exit the workflow function if connection fails
            # --- End Modified Connection Logic ---

            if not browser:
                 return # Already handled status in the loop

            # When connecting via WebDriver, we usually get the first context/page
            # or need to handle sessions differently. Let's assume we get a default context.
            # Video recording options likely won't apply here.
            if browser.contexts:
                context = browser.contexts[0]
            else:
                # If no context exists, create one (though this might not be standard for WebDriver connect)
                context = await browser.new_context(
                     viewport={"width": 1280, "height": 720}
                     # record_video_dir=RECORDINGS_DIR, # Recording likely won't work
                     # record_video_size={"width": 1280, "height": 720}
                )
            
            if context.pages:
                 page = context.pages[0]
            else:
                 page = await context.new_page()
            
            active_workflows[workflow_id]["status"] = "running"
            
            # Prepare the task
            full_task = [f"Navigate to {target_url}"] + actions
            task_string = "\n".join(full_task)

            # Execute actions using Playwright directly
            try:
                print(f"Navigating to {target_url}")
                await page.goto(target_url, wait_until='domcontentloaded', timeout=60000) # Increased timeout
                
                for action_str in actions:
                    action_str_lower = action_str.strip().lower()
                    if not action_str_lower:
                        continue
                    print(f"Executing action: {action_str}")
                    try:
                        if action_str_lower.startswith("click"):
                            selector = action_str.split(maxsplit=1)[1].strip()
                            await page.locator(selector).click(timeout=10000)
                        elif action_str_lower.startswith("type") or action_str_lower.startswith("enter"):
                            parts = action_str.split(maxsplit=2)
                            if len(parts) < 3:
                                print(f"Skipping invalid type/enter action: {action_str}")
                                continue
                            selector = parts[1].strip()
                            text_to_type = parts[2].strip()
                            await page.locator(selector).fill(text_to_type, timeout=10000)
                        elif action_str_lower.startswith("wait"):
                            try:
                                wait_time_ms = int(action_str.split(maxsplit=1)[1].strip()) * 1000
                                await page.wait_for_timeout(wait_time_ms)
                            except (IndexError, ValueError):
                                print(f"Skipping invalid wait action: {action_str}. Use 'wait <seconds>'")
                        elif action_str_lower.startswith("scroll down"):
                            await page.evaluate("window.scrollBy(0, window.innerHeight)")
                        elif action_str_lower.startswith("scroll up"):
                            await page.evaluate("window.scrollBy(0, -window.innerHeight)")
                        else:
                            print(f"Skipping unrecognized action: {action_str}")
                    except Exception as play_err:
                        print(f"Error executing action '{action_str}': {play_err}")
                        # Decide if you want to stop the workflow on action error
                
                active_workflows[workflow_id]["status"] = "completed" # Mark as completed if actions finish
                print("Workflow actions completed.")

            except Exception as e:
                print(f"Error during Playwright execution: {e}")
                active_workflows[workflow_id]["status"] = "failed"
                active_workflows[workflow_id]["error"] = f"Workflow execution error: {e}"
            finally:
                # Don't close context/browser when connected externally via WebDriver
                # Let Selenium manage the browser lifecycle
                # if page and not page.is_closed():
                #     await page.close()
                # if context:
                #     await context.close()
                if browser:
                    await browser.close() # Close the WebDriver connection
                
                # Recording logic removed as it likely won't work with WebDriver connect
                # video_path = None 
                # ... (rest of recording logic removed) ...

    except Exception as e:
        print(f"General error in workflow execution: {e}")
        active_workflows[workflow_id]["status"] = "failed"
        active_workflows[workflow_id]["error"] = f"General error: {e}"
    finally:
        # Ensure browser connection is closed on error
        try:
            if browser and browser.is_connected():
                await browser.close()
        except Exception as close_err:
            print(f"Error closing browser connection: {close_err}")

# --- Flask Routes (Using standard quotes) ---

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/start_workflow', methods=['POST'])
def start_workflow_route():
    data = request.get_json()
    if not data:
        return jsonify({'error': 'Invalid JSON payload'}), 400
        
    target_url = data.get('url')
    actions = data.get('steps')

    if not target_url or not isinstance(target_url, str):
        return jsonify({'error': 'Missing or invalid URL'}), 400
    if not actions or not isinstance(actions, list):
        if isinstance(data.get('steps'), str):
            actions = [step.strip() for step in data.get('steps').split('\n') if step.strip()]
        else:
            return jsonify({'error': 'Missing or invalid steps (must be a list of strings)'}), 400
    if not actions:
        return jsonify({'error': 'Steps list cannot be empty'}), 400

    # Check if BROWSER_ENDPOINT is set before starting
    if not BROWSER_ENDPOINT:
        print("ERROR: BROWSER_ENDPOINT is not set. Cannot start workflow.")
        return jsonify({'error': 'Server configuration error: Browser endpoint not set.'}), 500

    workflow_id = str(uuid.uuid4())
    vnc_connection_info = PUBLIC_VNC_URL 
    if not vnc_connection_info:
        print("ERROR: PUBLIC_VNC_URL is not set. Cannot start workflow.")
        return jsonify({'error': 'Server configuration error: VNC URL not set.'}), 500

    active_workflows[workflow_id] = {
        "status": "starting",
        "vnc_url": vnc_connection_info,
        "recording_path": None, # Recording likely disabled
        "error": None
    }

    thread = threading.Thread(target=run_workflow_thread, args=(workflow_id, target_url, actions))
    thread.start()

    return jsonify({
        "message": "Workflow started. Connecting to VNC...",
        "workflow_id": workflow_id,
        "vnc_url": vnc_connection_info,
    })

@app.route("/workflow_status/<workflow_id>")
def workflow_status(workflow_id):
    workflow = active_workflows.get(workflow_id)
    if not workflow:
        return jsonify({"error": "Workflow not found"}), 404

    response = {"status": workflow["status"]}
    if workflow["status"] == "completed":
        # No download URL if recording is disabled
        # response["download_url"] = f"/download/{workflow_id}"
        pass 
    elif workflow["status"] == "failed":
        response["error"] = workflow.get("error", "Unknown error")
        
    return jsonify(response)

# Download route might be unnecessary if recording is disabled
@app.route("/download/<workflow_id>")
def download_recording(workflow_id):
     return jsonify({"error": "Recording feature is disabled with WebDriver connection."}), 404
    # workflow = active_workflows.get(workflow_id)
    # ... (rest of original download logic commented out) ...

# Route to serve noVNC files
@app.route("/novnc/<path:path>")
def send_novnc(path):
    novnc_dir = os.path.join(app.static_folder, "novnc") 
    requested_path = os.path.normpath(os.path.join(novnc_dir, path))
    if not requested_path.startswith(os.path.abspath(novnc_dir)):
        return jsonify({"error": "Invalid path"}), 400
    return send_from_directory(novnc_dir, path)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080, debug=False)

