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
# CDP endpoint for connecting to the browser container
BROWSER_CDP_ENDPOINT = os.environ.get("BROWSER_CDP_ENDPOINT", "ws://localhost:9222") 

# *** MODIFIED: Get the PUBLIC VNC URL from environment variable ***
# This URL should be the one provided by Akash for the globally exposed port 6900 of the browser service
PUBLIC_VNC_URL = os.environ.get("PUBLIC_VNC_URL")
if not PUBLIC_VNC_URL:
    print("WARNING: PUBLIC_VNC_URL environment variable not set. VNC connection might fail.")
    # Provide a default or handle the error appropriately if needed
    # PUBLIC_VNC_URL = "ws://default-placeholder-url:6900" # Example placeholder

# Ensure recordings directory exists
os.makedirs(RECORDINGS_DIR, exist_ok=True)

# Dictionary to track workflow states and details
active_workflows = {}

# --- Helper Function to Run Workflow Asynchronously ---
def run_workflow_thread(workflow_id, target_url, actions):
    # Create a new event loop for the thread
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(run_browser_use_workflow(workflow_id, target_url, actions))
    finally:
        loop.close()

async def run_browser_use_workflow(workflow_id, target_url, actions):
    recording_filename = f"{workflow_id}.webm"
    recording_path_in_container = os.path.join(RECORDINGS_DIR, recording_filename)
    
    active_workflows[workflow_id]["status"] = "connecting_browser"
    
    browser = None
    context = None
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

            # Placeholder: Simulate running actions using Playwright directly for now
            try:
                await page.goto(target_url, wait_until='domcontentloaded')
                # Add more direct Playwright actions based on parsed 'actions' list here...
                # Example:
                for action_str in actions:
                    action_str_lower = action_str.strip().lower()
                    if not action_str_lower:
                        continue
                    print(f"Executing action: {action_str}") # Add logging
                    try:
                        if action_str_lower.startswith("click"):
                            selector = action_str.split(maxsplit=1)[1].strip()
                            await page.locator(selector).click(timeout=10000) # Add timeout
                        elif action_str_lower.startswith("type") or action_str_lower.startswith("enter"): # Combine type/enter
                            parts = action_str.split(maxsplit=2)
                            if len(parts) < 3:
                                print(f"Skipping invalid type/enter action: {action_str}")
                                continue
                            selector = parts[1].strip()
                            text_to_type = parts[2].strip()
                            await page.locator(selector).fill(text_to_type, timeout=10000) # Use fill, add timeout
                        elif action_str_lower.startswith("wait"): # Add wait action
                            try:
                                wait_time_ms = int(action_str.split(maxsplit=1)[1].strip()) * 1000
                                await page.wait_for_timeout(wait_time_ms)
                            except (IndexError, ValueError):
                                print(f"Skipping invalid wait action: {action_str}. Use 'wait <seconds>'")
                        elif action_str_lower.startswith("scroll down"): # Add scroll action
                            await page.evaluate("window.scrollBy(0, window.innerHeight)")
                        elif action_str_lower.startswith("scroll up"): # Add scroll action
                            await page.evaluate("window.scrollBy(0, -window.innerHeight)")
                        else:
                            print(f"Skipping unrecognized action: {action_str}")
                    except Exception as play_err:
                        print(f"Error executing action '{action_str}': {play_err}")
                        # Decide if you want to stop the workflow on action error
                        # active_workflows[workflow_id]["status"] = "failed"
                        # active_workflows[workflow_id]["error"] = f"Action '{action_str}' failed: {play_err}"
                        # return # Uncomment to stop workflow on first action error
                
                # Simulate some final activity/wait if needed
                # await page.wait_for_timeout(5000) # Wait 5 seconds 

                active_workflows[workflow_id]["status"] = "finishing"
            except Exception as e:
                print(f"Error during Playwright execution: {e}")
                active_workflows[workflow_id]["status"] = "failed"
                active_workflows[workflow_id]["error"] = f"Workflow execution error: {e}"
            finally:
                video_path = None
                if page and page.video:
                    # Use try-except for path() as it might fail if context closed prematurely
                    try:
                        video_path = await page.video.path() # Get path before closing context
                    except Exception as video_path_err:
                        print(f"Could not get video path: {video_path_err}")
                
                if context:
                    await context.close()
                # Don't close the browser if connect_over_cdp was used, as it's managed externally
                # if browser:
                #     await browser.close()
                
                if video_path and active_workflows[workflow_id]["status"] != "failed":
                    # Rename the recording to match our workflow_id convention
                    final_recording_path = os.path.join(RECORDINGS_DIR, recording_filename)
                    if os.path.exists(video_path):
                         try:
                             os.rename(video_path, final_recording_path)
                             active_workflows[workflow_id]["recording_path"] = final_recording_path
                             active_workflows[workflow_id]["status"] = "completed"
                         except OSError as rename_err:
                             print(f"Error renaming video file {video_path} to {final_recording_path}: {rename_err}")
                             active_workflows[workflow_id]["status"] = "failed"
                             active_workflows[workflow_id]["error"] = "Failed to save recording file after execution."
                    else:
                         active_workflows[workflow_id]["status"] = "failed"
                         active_workflows[workflow_id]["error"] = "Recording file not found after execution."
                elif active_workflows[workflow_id]["status"] != "failed":
                    active_workflows[workflow_id]["status"] = "failed"
                    active_workflows[workflow_id]["error"] = "Recording failed to save (no video path found)."

    except Exception as e:
        print(f"General error in workflow execution: {e}")
        active_workflows[workflow_id]["status"] = "failed"
        active_workflows[workflow_id]["error"] = f"General error: {e}"
    finally:
        # Ensure context/browser are attempted to be closed even on general error
        # (Playwright might handle this internally with async with, but being explicit can help)
        try:
            if context and not context.is_closed():
                await context.close()
        except Exception as close_err:
            print(f"Error closing context: {close_err}")
        # Don't close browser with connect_over_cdp
        # try:
        #     if browser and browser.is_connected():
        #         await browser.close()
        # except Exception as close_err:
        #     print(f"Error closing browser: {close_err}")

# --- Flask Routes ---

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/start_workflow', methods=['POST'])
def start_workflow_route(): # Renamed to avoid conflict
    data = request.get_json()
    if not data:
        return jsonify({'error': 'Invalid JSON payload'}), 400
        
    target_url = data.get('url')
    actions = data.get('steps') # Expecting a list of strings now

    # Basic validation
    if not target_url or not isinstance(target_url, str):
        return jsonify({'error': 'Missing or invalid URL'}), 400
    if not actions or not isinstance(actions, list):
         # Try splitting if it looks like a newline-separated string was sent
        if isinstance(data.get('steps'), str):
            actions = [step.strip() for step in data.get('steps').split('\n') if step.strip()]
        else:
            return jsonify({'error': 'Missing or invalid steps (must be a list of strings)'}), 400
    if not actions: # Check again after potential split
        return jsonify({'error': 'Steps list cannot be empty'}), 400

    workflow_id = str(uuid.uuid4())
    
    # *** MODIFIED: Use the PUBLIC_VNC_URL from environment variable ***
    vnc_connection_info = PUBLIC_VNC_URL 
    if not vnc_connection_info:
        # Handle case where env var is missing - maybe return an error or default
        print("ERROR: PUBLIC_VNC_URL is not set. Cannot start workflow.")
        return jsonify({'error': 'Server configuration error: VNC URL not set.'}), 500

    active_workflows[workflow_id] = {
        "status": "starting",
        "vnc_url": vnc_connection_info, # Use the public URL
        "recording_path": None, # Will be set by the thread
        "error": None
    }

    # Start the browser automation in a background thread
    thread = threading.Thread(target=run_workflow_thread, args=(workflow_id, target_url, actions))
    thread.start()

    return jsonify({
        "message": "Workflow started. Connecting to VNC...",
        "workflow_id": workflow_id,
        "vnc_url": vnc_connection_info, # Return the public URL to the frontend
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
                                   as_attachment=True,
                                   download_name=f"{workflow_id}.webm" # Set a sensible download name
                                   )
    except Exception as e:
        print(f"Error sending file: {e}")
        return jsonify({"error": "Failed to send recording file"}), 500

# Add route to serve noVNC files (ensure this path is correct for your Docker image)
@app.route("/novnc/<path:path>")
def send_novnc(path):
    # Assuming novnc files are copied to /app/static/novnc in the Docker image
    novnc_dir = os.path.join(app.static_folder, "novnc") 
    # Basic security check (normpath resolves .., join might be manipulated)
    requested_path = os.path.normpath(os.path.join(novnc_dir, path))
    if not requested_path.startswith(os.path.abspath(novnc_dir)):
        return jsonify({"error": "Invalid path"}), 400
        
    return send_from_directory(novnc_dir, path)

if __name__ == "__main__":
    # Use a port suitable for deployment, e.g., 8080
    app.run(host="0.0.0.0", port=8080, debug=False) # Disable debug for production

