import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from flask import Flask, request, jsonify, render_template, send_from_directory
import asyncio
import threading
import uuid
from playwright.async_api import async_playwright
from browser_use import Agent # Assuming browser_use is installed in the venv

app = Flask(__name__, static_folder='static', template_folder='static')

# --- Configuration ---
RECORDINGS_DIR = "/home/ubuntu/recordings"
# Placeholder: In Akash, this would likely be the service name, e.g., "ws://chromium:9222"
BROWSER_CDP_ENDPOINT = os.environ.get("BROWSER_CDP_ENDPOINT", "ws://localhost:9222") 
# Placeholder: VNC details from the browser pod
VNC_HOST = os.environ.get("VNC_HOST", "localhost")
VNC_PORT = os.environ.get("VNC_PORT", "5901")
# VNC_PASSWORD = os.environ.get("VNC_PASSWORD", None) # Optional password

# Ensure recordings directory exists
os.makedirs(RECORDINGS_DIR, exist_ok=True)

# Dictionary to track workflow states and details
active_workflows = {}

# --- Helper Function to Run Workflow Asynchronously ---
def run_workflow_thread(workflow_id, target_url, actions):
    asyncio.run(run_browser_use_workflow(workflow_id, target_url, actions))

async def run_browser_use_workflow(workflow_id, target_url, actions):
    recording_filename = f"{workflow_id}.webm"
    recording_path_in_container = os.path.join(RECORDINGS_DIR, recording_filename)
    
    active_workflows[workflow_id]["status"] = "connecting_browser"
    
    try:
        async with async_playwright() as p:
            try:
                browser = await p.chromium.connect_over_cdp(BROWSER_CDP_ENDPOINT)
            except Exception as e:
                print(f"Error connecting to browser CDP: {e}")
                active_workflows[workflow_id]["status"] = "failed"
                active_workflows[workflow_id]["error"] = f"Failed to connect to browser: {e}"
                return

            # Create a new context with video recording enabled
            context = await browser.new_context(
                record_video_dir=RECORDINGS_DIR,
                record_video_size={"width": 1280, "height": 720}, # Example size
                viewport={"width": 1280, "height": 720}, # Match viewport to recording
                # locale='en-US' # Optional: set locale
            )
            
            # Get the specific page where recording will happen
            page = await context.new_page()
            # The actual recording path is determined by Playwright, usually based on page creation time.
            # We need to retrieve it after the context closes.
            
            active_workflows[workflow_id]["status"] = "running"
            
            # Prepare the task for browser-use
            # The task should ideally start with navigating to the target_url
            full_task = [f"Navigate to {target_url}"] + actions
            task_string = "\n".join(full_task)

            # Instantiate Agent using the existing page/context
            # Note: browser-use might not directly support passing an existing page/context easily.
            # We might need to adapt how Agent is initialized or modify its internals slightly.
            # For now, let's assume a simplified Agent usage focusing on the task.
            # A more robust approach might involve directly using Playwright commands based on 'actions'.
            
            # --- Simplified Agent Usage (Conceptual) ---
            # This part needs verification against browser-use's actual API for remote browsers/existing contexts.
            # If Agent cannot directly use connect_over_cdp's context, we might need to:
            # 1. Use Playwright directly in this async function based on 'actions'.
            # 2. Modify/extend browser-use Agent to accept a pre-configured context/page.
            
            # Placeholder: Simulate running actions using Playwright directly for now
            try:
                await page.goto(target_url, wait_until='domcontentloaded')
                # Add more direct Playwright actions based on parsed 'actions' list here...
                # For example:
                # for action_str in actions:
                #     if action_str.lower().startswith("click"): 
                #         selector = action_str.split(maxsplit=1)[1]
                #         await page.locator(selector).click()
                #     elif action_str.lower().startswith("type"): 
                #         parts = action_str.split(maxsplit=2)
                #         selector = parts[1]
                #         text = parts[2]
                #         await page.locator(selector).fill(text)
                #     # Add more action handlers (scroll, etc.)
                
                # Simulate some activity
                await page.wait_for_timeout(10000) # Wait 10 seconds 

                active_workflows[workflow_id]["status"] = "finishing"
            except Exception as e:
                print(f"Error during Playwright execution: {e}")
                active_workflows[workflow_id]["status"] = "failed"
                active_workflows[workflow_id]["error"] = f"Workflow execution error: {e}"
            finally:
                # Close the context to finalize the recording
                await context.close()
                # Retrieve the actual path of the saved video
                video_path = page.video.path() if page.video else None
                await browser.close() # Close the connection to the remote browser
                
                if video_path and active_workflows[workflow_id]["status"] != "failed":
                    # Rename the recording to match our workflow_id convention
                    final_recording_path = os.path.join(RECORDINGS_DIR, recording_filename)
                    if os.path.exists(video_path):
                         os.rename(video_path, final_recording_path)
                         active_workflows[workflow_id]["recording_path"] = final_recording_path
                         active_workflows[workflow_id]["status"] = "completed"
                    else:
                         active_workflows[workflow_id]["status"] = "failed"
                         active_workflows[workflow_id]["error"] = "Recording file not found after execution."
                elif active_workflows[workflow_id]["status"] != "failed":
                    active_workflows[workflow_id]["status"] = "failed"
                    active_workflows[workflow_id]["error"] = "Recording failed to save."

    except Exception as e:
        print(f"General error in workflow execution: {e}")
        active_workflows[workflow_id]["status"] = "failed"
        active_workflows[workflow_id]["error"] = f"General error: {e}"

# --- Flask Routes ---

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/start_workflow', methods=['POST'])
def start_workflow_route(): # Renamed to avoid conflict
    data = request.get_json()
    target_url = data.get('url')
    actions = data.get('steps')

    if not target_url or not actions:
        return jsonify({'error': 'Missing URL or steps'}), 400

    workflow_id = str(uuid.uuid4())
    vnc_connection_info = f"ws://{VNC_HOST}:{VNC_PORT}"
    # Add password info if needed, handled by noVNC prompt

    active_workflows[workflow_id] = {
        "status": "starting",
        "vnc_url": vnc_connection_info,
        "recording_path": None, # Will be set by the thread
        "error": None
    }

    # Start the browser automation in a background thread
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
        response["download_url"] = f"/download/{workflow_id}"
    elif workflow["status"] == "failed":
        response["error"] = workflow.get("error", "Unknown error")
        
    return jsonify(response)

@app.route("/download/<workflow_id>")
def download_recording(workflow_id):
    workflow = active_workflows.get(workflow_id)
    if not workflow or workflow["status"] != "completed" or not workflow.get("recording_path"):
        return jsonify({"error": "Recording not ready or workflow not found"}), 404

    recording_path = workflow["recording_path"]
    if not os.path.exists(recording_path):
         return jsonify({"error": "Recording file missing"}), 404
         
    try:
        return send_from_directory(directory=os.path.dirname(recording_path), 
                                   path=os.path.basename(recording_path), 
                                   as_attachment=True)
    except Exception as e:
        print(f"Error sending file: {e}")
        return jsonify({"error": "Failed to send recording file"}), 500

# Add route to serve noVNC files
@app.route("/novnc/<path:path>")
def send_novnc(path):
    # Security: Ensure path doesn't traverse directories inappropriately
    novnc_dir = "/home/ubuntu/browser_use_controller/src/static/novnc"
    safe_path = os.path.normpath(os.path.join(novnc_dir, path))
    if not safe_path.startswith(os.path.abspath(novnc_dir)):
        return jsonify({"error": "Invalid path"}), 400
        
    return send_from_directory(novnc_dir, path)

if __name__ == "__main__":
    # Use a port suitable for deployment, e.g., 8080 or 8000
    app.run(host="0.0.0.0", port=8080, debug=False) # Disable debug for production/testing
utes extensively
    app.run(host=\"0.0.0.0\", port=5000, debug=True) # Use a different port than default Flask 5000 if needed

