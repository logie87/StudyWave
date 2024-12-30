import gevent.monkey
gevent.monkey.patch_all()

from flask import Flask, render_template
from flask_socketio import SocketIO, emit
import time
import threading
from muselsl import stream, list_muses
from muselsl.record import record
import logging
import asyncio

# Initialize logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

app = Flask(__name__)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='gevent')

# Running on port 5001 to match front-end
PORT = 5001

# Thread to handle EEG streaming
recording_thread = None

async def stream_muse(duration):
    """Function to handle Muse streaming and emit EEG data."""
    try:
        logging.info("Searching for Muse headsets...")
        muses = await list_muses()  # Search for available Muse headsets
        if not muses:
            raise Exception("No Muse headsets found. Please make sure your Muse is turned on and in pairing mode.")
        
        address = muses[0]['address']  # Use the first available headset
        logging.info(f"Connecting to Muse: {address}...")
        
        # Start streaming data from Muse with the discovered address
        await stream(address)
        time.sleep(2)  # Give time for data to start streaming
        
        start_time = time.time()
        while time.time() - start_time < duration:
            # Simulating reading EEG data, you will replace this with real Muse data processing
            sample_data = 100 * (time.time() % 10)  # Placeholder simulated data
            socketio.emit('eeg_data', sample_data)
            await asyncio.sleep(0.1)  # Sleep to mimic Muse stream speed
            progress = ((time.time() - start_time) / duration) * 100
            socketio.emit('progress', progress)

        # Emit recording complete
        socketio.emit('recording_complete')
        logging.info("Recording complete.")
    except Exception as e:
        logging.error(f"Error during streaming: {e}")
        socketio.emit('status', {'message': 'Error during streaming', 'error': str(e)})

def start_stream_muse(duration):
    """Wrapper function to run the async stream_muse function synchronously."""
    try:
        loop = asyncio.get_event_loop()
        future = asyncio.run_coroutine_threadsafe(stream_muse(duration), loop)
        future.result()  # This will block until the coroutine completes
    except RuntimeError:
        # If there's no current event loop, create a new one
        logging.warning("No running event loop, creating a new one.")
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(stream_muse(duration))
    except Exception as e:
        logging.error(f"Error in start_stream_muse: {e}")

@app.route('/')
def index():
    try:
        return render_template('index.html')
    except Exception as e:
        logging.error(f"Template rendering error: {e}")
        return "Error loading the page. Please check server logs for more details."

@socketio.on('start_recording')
def handle_start_recording(json):
    global recording_thread
    duration = json.get('duration', 10)
    if not isinstance(duration, int) or duration <= 0:
        emit('status', {'message': 'Invalid duration provided. Please enter a positive integer.'})
        logging.warning(f"Invalid duration received: {duration}")
        return
    
    if recording_thread is None or not recording_thread.is_alive():
        logging.info(f"Starting a new recording for duration: {duration} seconds")
        recording_thread = threading.Thread(target=lambda: start_stream_muse(duration))
        recording_thread.start()
        emit('status', {'message': 'Recording started'})
    else:
        emit('status', {'message': 'Recording is already in progress'})
        logging.warning("Attempted to start a new recording while one is already in progress.")

@socketio.on('connect')
def handle_connect():
    logging.info("Client connected")
    emit('status', {'message': 'Client connected to the server'})

@socketio.on('disconnect')
def handle_disconnect():
    logging.info("Client disconnected")

if __name__ == '__main__':
    socketio.run(app, port=PORT, debug=False)
