from gevent import monkey
monkey.patch_all()

import logging
from flask import Flask, render_template, request
from flask_socketio import SocketIO, emit
import time
import datetime
import pylsl
import threading
import subprocess
import os

# Suppress verbose logging from libraries
logging.getLogger('bleak').setLevel(logging.WARNING)
logging.getLogger('asyncio').setLevel(logging.ERROR)

# Initialize Flask and SocketIO
app = Flask(__name__)
app.config['SECRET_KEY'] = 'secret!'
socketio = SocketIO(app, async_mode='gevent', cors_allowed_origins="*", logger=True, engineio_logger=True, ping_interval=25, ping_timeout=30)

# Function to log messages with timestamps
def log_message(message):
    current_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{current_time}] {message}")

# Function to handle the Muse data stream
def muse_data_stream(duration, sid):
    log_message("Starting Muse data stream...")
    stream_process = None
    try:
        # Use the full path to muselsl
        muselsl_path = '/opt/anaconda3/bin/muselsl'  # Update this path as necessary

        # Check if muselsl_path exists and is executable
        if not os.path.exists(muselsl_path):
            error_msg = f"muselsl not found at path: {muselsl_path}"
            log_message(error_msg)
            socketio.emit('muse_connection_error', {'error': error_msg}, to=sid)
            return
        if not os.access(muselsl_path, os.X_OK):
            error_msg = f"muselsl at path {muselsl_path} is not executable."
            log_message(error_msg)
            socketio.emit('muse_connection_error', {'error': error_msg}, to=sid)
            return

        # Start the muselsl stream process
        stream_process = subprocess.Popen(
            [muselsl_path, 'stream'],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=os.environ.copy()  # Copy the environment variables
        )
        log_message("Started muselsl stream process.")

        # Read and log the output from muselsl stream process
        def log_stream_output(process):
            for line in process.stdout:
                log_message(f"[muselsl stream] {line.strip()}")

        # Read and log stderr
        def log_stream_error(process):
            for line in process.stderr:
                log_message(f"[muselsl stream ERROR] {line.strip()}")

        # Start threads to read stdout and stderr
        stdout_thread = threading.Thread(target=log_stream_output, args=(stream_process,))
        stdout_thread.daemon = True
        stdout_thread.start()

        stderr_thread = threading.Thread(target=log_stream_error, args=(stream_process,))
        stderr_thread.daemon = True
        stderr_thread.start()

        # Give the stream some time to initialize
        time.sleep(3)

        # Connect to the LSL stream
        log_message("Attempting to resolve LSL stream...")
        retry_attempts = 0
        max_retries = 5

        streams = pylsl.resolve_byprop('type', 'EEG', timeout=10)
        while not streams and retry_attempts < max_retries:
            retry_attempts += 1
            log_message(f"No Muse available. Retrying... (Attempt {retry_attempts}/{max_retries})")
            time.sleep(2)
            streams = pylsl.resolve_byprop('type', 'EEG', timeout=5)

        if not streams:
            log_message("No EEG stream available after maximum retries.")
            socketio.emit('muse_connection_error', {'error': 'No EEG stream available after retries.'}, to=sid)
            return

        inlet = pylsl.StreamInlet(streams[0])
        log_message("Connected to EEG stream.")
        socketio.emit('muse_connected', {}, to=sid)
        log_message("Emitted 'muse_connected' event to client.")

        # Start timing after Muse is connected
        start_time = time.time()
        while (time.time() - start_time) < duration:
            try:
                sample, timestamp = inlet.pull_sample(timeout=1.0)
                if sample:
                    socketio.emit('new_eeg_data', {'data': sample}, to=sid)
                    log_message(f"Emitted 'new_eeg_data' event: {sample}")
                socketio.sleep(0.01)  # Yield to the event loop
            except pylsl.LostError:
                log_message("Lost connection to LSL stream. Retrying...")
                retry_attempts = 0
                while retry_attempts < max_retries:
                    retry_attempts += 1
                    streams = pylsl.resolve_byprop('type', 'EEG', timeout=5)
                    if streams:
                        inlet = pylsl.StreamInlet(streams[0])
                        log_message("Reconnected to EEG stream.")
                        socketio.emit('status_update', {'message': 'Reconnected to EEG stream.'}, to=sid)
                        break
                    time.sleep(2)
                else:
                    log_message("Unable to reconnect to EEG stream after retries. Ending data stream.")
                    break

        log_message("Data streaming completed.")
        socketio.emit('recording_finished', {}, to=sid)
        log_message("Emitted 'recording_finished' event to client.")

    except Exception as e:
        log_message(f"Error during Muse streaming: {e}")
        socketio.emit('muse_connection_error', {'error': str(e)}, to=sid)

    finally:
        if stream_process and stream_process.poll() is None:
            stream_process.terminate()
            stream_process.wait()
            log_message("Ensured muselsl process terminated.")

# Function to handle start recording event
@socketio.on('start_recording')
@socketio.on('start_recording')
def handle_start_recording(message):
    try:
        duration = int(message.get('duration', 5))  # Default to 5 seconds if not provided
        log_message(f"Received start recording command for duration: {duration} seconds")
        sid = request.sid
        log_message(f"Client SID: {sid}")

        # Emit initial status update
        emit('status_update', {'message': 'Connecting to Muse...'}, to=sid)
        
        # Start the Muse data stream in a background task
        socketio.start_background_task(target=muse_data_stream, duration=duration, sid=sid)
        log_message("Started Muse data stream in background task.")
    except Exception as e:
        log_message(f"Error in handle_start_recording: {e}")
        emit('muse_connection_error', {'error': str(e)}, to=request.sid)
# Define a route to serve the index page
@app.route('/')
def index():
    log_message("Serving the index page...")
    return render_template('index.html')

# Handle client disconnection
@socketio.on('disconnect')
def handle_disconnect():
    log_message(f"Client SID {request.sid} disconnected.")

# Start the Flask server
if __name__ == '__main__':
    log_message("Starting the Flask server on port 5001...")
    try:
        socketio.run(app, host='0.0.0.0', port=5001, debug=False)
    except Exception as e:
        log_message(f"Error while starting the server: {e}")
