import csv
import google.generativeai as genai
import speech_recognition as sr
import pyttsx3
import pvporcupine
import pyaudio
import struct
import os
import time
import datetime
import parsedatetime
import smtplib
import ssl
from email.message import EmailMessage
import json
import sqlite3
import threading
import torch


# --- Local LLM Imports ---
try:
    from transformers import AutoModelForCausalLM, AutoTokenizer
    from peft import PeftModel
except ImportError:
    print("WARNING: Transformers/Peft not installed. Local brain will be disabled.")

# --- Integration Imports ---
try:
    import spotipy
    from spotipy.oauth2 import SpotifyOAuth
    import imaplib
    import email
    from email.header import decode_header
    from ddgs import DDGS
except ImportError:
    pass

# --- Firebase Admin ---
import firebase_admin
from firebase_admin import credentials, firestore


# ENTER YOUR SLACK BOT TOKEN HERE (Starts with xoxb-)
SLACK_BOT_TOKEN = "xoxb-9948893924933-9965295378145-UxW7mvq6d92JtX1zUkQuG7W0"

# ==============================================================================
# --- 1. GLOBAL CONFIGURATION (FILL THIS OUT) ---
# ==============================================================================

# --- Google API Key ---
GEMINI_API_KEY = "AIzaSyD1buyiLvowUNLseIs7LfZjDAwe8pXUTYw"

# --- NEW: Picovoice Access Key ---
# Get this from https://console.picovoice.ai/
PICOVOICE_ACCESS_KEY = "DinCqXh0VfBtaDxdlRRADvnydv1rt0WU1Z1xl6W3Jr/NycIpFf8KYw=="
LOCAL_ADAPTER_PATH = "./model(final)"
# --- NEW: Wake Word File ---
# The .ppn file you downloaded (e.g., "Pengo_mac.ppn")
WAKE_WORD_PATH = "Pengo_en_mac_v3_0_0.ppn" 
# --- Email Credentials ---
SENDER_EMAIL = "izzanaseer45@gmail.com"
SENDER_PASSWORD = "astx awbb zhza dvas"  # The 16-digit App Password
SPOTIPY_CLIENT_ID = 'ea93588bf2314368ad6ad12a98dcc5b3'
SPOTIPY_CLIENT_SECRET = '4d40cd22fa3c4becb109ec7b0f3e8b35'
BASE_MODEL_ID = "TinyLlama/TinyLlama-1.1B-Chat-v1.0"
SPOTIPY_REDIRECT_URI = 'http://127.0.0.1:8888/callback'
# --- Google Calendar API Scopes ---
# This controls what the script can do. 'calendar' is full read/write.
GCAL_SCOPES = ['https://www.googleapis.com/auth/calendar','https://www.googleapis.com/auth/gmail.readonly']
# --- Agent Customization ---
MY_SIGNATURE = "\n\nBest regards,\nIzzan"
contacts = {
    "ifra": "26100090@gmail.com",
    "ahsan": "26100046@lums.edu.pk",
    "myself": "the.boss@bigcompany.com",
    "default": "recipient@example.com"
}
def _recreate_db(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS logs (
            id INTEGER PRIMARY KEY,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            speaker TEXT,
            message TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS web_commands (
            id INTEGER PRIMARY KEY,
            command TEXT,
            status TEXT DEFAULT 'pending'
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS todo (
            id INTEGER PRIMARY KEY,
            task TEXT,
            status TEXT DEFAULT 'pending'
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS notes (
            id INTEGER PRIMARY KEY,
            title TEXT,
            content TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()

# --- HRI Data Logging Path ---
HRI_DATASET_PATH = "hri_training_data.jsonl"
def open_sqlite_with_recovery(path: str):
        """
        THIS NAME MATCHES YOUR OTHER CODE.
        If DB is corrupted, backs it up and recreates it.
        """
        try:
            conn = sqlite3.connect(path, timeout=1.0)
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA synchronous=NORMAL;")
            conn.execute("PRAGMA foreign_keys=ON;")
            conn.execute("PRAGMA integrity_check;").fetchone()
            _recreate_db(conn)
            return conn

        except sqlite3.DatabaseError as e:
            msg = str(e).lower()
            if ("malformed" in msg) or ("disk image" in msg) or ("not a database" in msg):
                ts = time.strftime("%Y%m%d_%H%M%S")
                backup = f"{path}.corrupt_{ts}"
                try:
                    if os.path.exists(path):
                        shutil.move(path, backup)
                        print(f"[DB] Corrupt DB moved to: {backup}")
                except Exception as move_err:
                    print(f"[DB] Failed to move corrupt DB: {move_err}")

                conn = sqlite3.connect(path, timeout=1.0)
                conn.execute("PRAGMA journal_mode=WAL;")
                conn.execute("PRAGMA synchronous=NORMAL;")
                conn.execute("PRAGMA foreign_keys=ON;")
                _recreate_db(conn)
                print("[DB] Fresh DB created.")
                return conn

            raise

# --- DB Setup (Hybrid) ---
db_online = None
db_local = None

try:
    if os.path.exists('serviceAccountKey.json'):
        cred = credentials.Certificate('serviceAccountKey.json')
        firebase_admin.initialize_app(cred)
        db_online = firestore.client()
        print("Connected to Firestore (Online).")
except Exception as e:
    print(f"Offline Mode: {e}")

try:
    db_local = open_sqlite_with_recovery('tasks_cache.db')

    c = db_local.cursor()
    c.execute('CREATE TABLE IF NOT EXISTS todo_cache (id INTEGER PRIMARY KEY, task TEXT)')
    c.execute('CREATE TABLE IF NOT EXISTS notes_cache (id INTEGER PRIMARY KEY, title TEXT, content TEXT)')
    c.execute('CREATE TABLE IF NOT EXISTS chat_history (id INTEGER PRIMARY KEY, role TEXT, content TEXT)')
    c.execute('CREATE TABLE IF NOT EXISTS logs (id INTEGER PRIMARY KEY, speaker TEXT, message TEXT, timestamp DATETIME DEFAULT CURRENT_TIMESTAMP)')
    db_local.commit()
except Exception as e:
    print(f"Local DB Error: {e}")

# --- Audio Setup ---
def check_audio_hardware():
    pa = None
    try:
        pa = pyaudio.PyAudio()
        if not pa.get_default_input_device_info(): raise IOError("No Mic")
    except: print("Audio Warning: No mic detected.")
    finally: 
        if pa: pa.terminate()
check_audio_hardware()

try:
    r = sr.Recognizer()
    tts_engine = pyttsx3.init()
    try: tts_engine.setProperty('rate', 200)
    except: pass
except: 
    tts_engine = None
    print("Warning: Audio engine failed.")
# ==============================================================================
# --- METRICS LOGGING CONFIG ---
METRICS_FILE = "latency_metrics.csv"

# --- HRI Data Logging Path ---
HRI_DATASET_PATH = "hri_training_data.jsonl"
class PerformanceLogger:
    def __init__(self, filename=METRICS_FILE):
        self.filename = filename
        # Create file with headers if it doesn't exist
        if not os.path.exists(self.filename):
            with open(self.filename, "w", newline="") as f:
                writer = csv.writer(f)
                writer.writerow(["Timestamp", "Interaction_Type", "Routing_Time_ms", "LLM_Processing_Time_ms", "Total_Response_Time_ms"])

    def log(self, interaction_type, routing_time, llm_time, total_time):
        try:
            with open(self.filename, "a", newline="") as f:
                writer = csv.writer(f)
                # Save times in milliseconds (ms) for easier graphing
                writer.writerow([
                    datetime.datetime.now().isoformat(),
                    interaction_type,           # "Local" or "Cloud"
                    f"{routing_time * 1000:.2f}", 
                    f"{llm_time * 1000:.2f}",
                    f"{total_time * 1000:.2f}"
                ])
            print(f"⏱ [Metrics] Type: {interaction_type} | Total Latency: {total_time:.4f}s")
        except Exception as e:
            print(f"Metrics Error: {e}")

perf_logger = PerformanceLogger()
# ==============================================================================
# --- 2. LOCAL BRAIN SETUP (THE EMPATH) ---
# ==============================================================================
# --- 2. LOCAL BRAIN SETUP (THE EMPATH) ---
# ==============================================================================
hri_model = None
hri_tokenizer = None

# --- Universal Device Detection ---
device = "cpu"
if torch.cuda.is_available():
    device = "cuda"
    print("🚀 Local Brain Mode: NVIDIA GPU (CUDA)")
elif torch.backends.mps.is_available():
    device = "mps" 
    print("🚀 Local Brain Mode: Apple Silicon GPU (MPS)")
else:
    print("🐢 Local Brain Mode: CPU (Slow)")

# ==============================================================================
# --- 2. LOCAL BRAIN SETUP (THE EMPATH) ---
# ==============================================================================
hri_model = None
hri_tokenizer = None

# 1. Global Device Detection 
# (This automatically works on both Mac for testing and Pi for deployment)
device = "cpu"
if torch.cuda.is_available():
    device = "cuda"
    print("🚀 Local Brain Mode: NVIDIA GPU (CUDA)")
elif torch.backends.mps.is_available():
    device = "mps" 
    print("🚀 Local Brain Mode: Apple Silicon GPU (MPS)")
else:
    print("🐢 Local Brain Mode: CPU (Raspberry Pi Compatible)")

# ==============================================================================
# --- 2.5. CONFIGURATION UPDATE ---
# ==============================================================================
# UPDATE: Point this to your quantized file
LOCAL_GGUF_PATH = "./pengo_q4_k_m.gguf" 

# ==============================================================================
# --- 3. LOCAL BRAIN SETUP (QUANTIZED GGUF) ---
# ==============================================================================

# Import Llama.cpp (ensure you ran: pip install llama-cpp-python)
try:
    from llama_cpp import Llama
except ImportError:
    print("WARNING: llama-cpp-python not installed. Local brain disabled.")
    Llama = None

hri_model = None

def load_local_brain():
    """Loads the GGUF model into RAM (Fast & Lightweight)."""
    global hri_model
    
    if hri_model is not None:
        return # Already loaded

    print(f"Loading Quantized Brain: {LOCAL_GGUF_PATH}...")
    
    if not os.path.exists(LOCAL_GGUF_PATH):
        print(f"❌ Error: GGUF file '{LOCAL_GGUF_PATH}' not found.")
        print("   Did you run the quantization script?")
        return

    try:
        if Llama is None: raise ImportError("Library missing")

        # Initialize Llama
        # n_gpu_layers=-1 auto-offloads to Metal (Mac) or GPU if available.
        # n_ctx=2048 sets the context window.
        hri_model = Llama(
            model_path=LOCAL_GGUF_PATH,
            n_ctx=2048,
            n_gpu_layers=-1, 
            verbose=False 
        )
        print("✅ Local Quantized Brain Loaded!")
        
    except Exception as e:
        print(f"❌ Error loading GGUF model: {e}")

# Load immediately if library is present
if Llama:
    load_local_brain()
def open_sqlite_with_recovery(path: str):
    """
    Minimal recovery:
    - Try normal connect
    - If malformed, back it up and recreate a clean DB
    """
    try:
        conn = sqlite3.connect(path, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        conn.execute("PRAGMA foreign_keys=ON;")
        # quick sanity check
        conn.execute("PRAGMA integrity_check;").fetchone()
        return conn
    except sqlite3.DatabaseError as e:
        msg = str(e).lower()
        if "malformed" in msg or "disk image" in msg or "not a database" in msg:
            ts = time.strftime("%Y%m%d_%H%M%S")
            backup = f"{path}.corrupt_{ts}"
            try:
                if os.path.exists(path):
                    shutil.move(path, backup)
                    print(f"[DB] Corrupt DB moved to: {backup}")
            except Exception as move_err:
                print(f"[DB] Failed to move corrupt DB: {move_err}")
            # Create fresh DB
            conn = sqlite3.connect(path, check_same_thread=False)
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA synchronous=NORMAL;")
            conn.execute("PRAGMA foreign_keys=ON;")
            print("[DB] Created fresh DB.")
            return conn
        raise
def generate_hri_response(user_input):
    """Generates a response using the GGUF model."""
    if not hri_model: return "I'm listening, but my emotional brain is offline."
    
    # --- EXACT TRAINING PROMPT FORMAT ---
    system_prompt = (
        "You are a friendly **virtual** well-being assistant. "
        "Infer the user's emotions from their words and respond with empathy and warmth. "
        "Keep your answer short (1–2 sentences)."
    )
    
    full_prompt = f"### Instruction:\n{system_prompt}\n\n### Input:\n{user_input}\n\n### Response:\n"
    
    try:
        # Run Inference
        output = hri_model(
            full_prompt,
            max_tokens=60,       # Keep response short
            stop=["### Input:", "\n###", "User:"], # Stop tokens to prevent rambling
            temperature=0.7,
            top_p=0.9,
            echo=False           # Do not repeat the input prompt
        )
        
        # Extract the text
        response_text = output['choices'][0]['text'].strip()
        return response_text
        
    except Exception as e: 
        print(f"Inference Error: {e}")
        return "I'm having trouble thinking right now."

# ==============================================================================
# --- 4. HELPER FUNCTIONS ---
# ==============================================================================
def speak_text(text):
    if not tts_engine: return
    try:
        import platform, subprocess
        clean_text = text.replace("*", "").replace('"', '')
        if platform.system() == 'Darwin':
            subprocess.run(['say', clean_text])
        else:
            tts_engine.say(clean_text)
            tts_engine.runAndWait()
    except: pass

# ==============================================================================
# --- HELPER FUNCTION FIX ---
# ==============================================================================

def listen_for_command(prompt=None, timeout_seconds=8):
    """
    Captures audio.
    Flexible arguments: Handles "Listening..." string OR timeout number.
    """
    # Detect if the first argument is actually a timeout number
    if isinstance(prompt, (int, float)):
        timeout_seconds = prompt
        prompt = None
    
    # If a text prompt was passed, print/speak it
    if isinstance(prompt, str):
        print(f"\n{prompt}")

    # Ensure timeout is a valid float
    timeout_val = float(timeout_seconds) if timeout_seconds else 5.0

    with sr.Microphone(sample_rate=16000, chunk_size=512) as source:
        try:
            # Optimization: No ambient adjustment (relies on startup calibration)
            audio = r.listen(source, timeout=timeout_val, phrase_time_limit=15)
            
            cmd = r.recognize_google(audio).lower()
            print(f"I heard: {cmd}")
            return "_CANCEL_" if "cancel" in cmd else cmd
            
        except sr.WaitTimeoutError:
            return None # Silence
        except sr.UnknownValueError:
            return None # Sound but no speech
        except Exception as e:
            print(f"Listen Error: {e}")
            return None

# ==============================================================================
# --- WAKE WORD LOOP FIX ---
# ==============================================================================

def start_wake_word_loop():
    # 1. Load Local Brain (Once)
    if 'transformers' in globals():
        load_local_brain()

    # 2. Sync Offline Cache
    if db_online:
        try:
            cur = db_local.cursor()
            cur.execute("SELECT id, task FROM todo_cache")
            for r in cur.fetchall(): add_todo_task(r[1]); cur.execute("DELETE FROM todo_cache WHERE id=?", (r[0],))
            db_local.commit()
        except: pass

    speak_text("Pengo Online.")
    print("Ready.")
    
    try:
        porcupine = pvporcupine.create(access_key=PICOVOICE_ACCESS_KEY, keyword_paths=['Pengo_en_mac_v3_0_0.ppn'])
        pa = pyaudio.PyAudio()
        stream = pa.open(rate=porcupine.sample_rate, channels=1, format=pyaudio.paInt16, input=True, frames_per_buffer=porcupine.frame_length)
        
        while True:
            try:
                pcm = stream.read(porcupine.frame_length, exception_on_overflow=False)
                idx = porcupine.process(struct.unpack_from("h"*porcupine.frame_length, pcm))
                if idx >= 0:
                    print("Wake word!")
                    stream.stop_stream()
                    time.sleep(0.1)
                    speak_text("Yes?")
                    time.sleep(0.5)
                    
                    # FIXED CALL: Explicitly naming arguments is safer
                    cmd = listen_for_command(timeout_seconds=8)
                    
                    if cmd and "_CANCEL_" not in cmd:
                        main_pipeline(cmd)
                    
                    stream.start_stream()
            except OSError:
                pass # Ignore overflow
            except Exception as e:
                print(f"Loop Error: {e}")
                break
    except KeyboardInterrupt:
        print("\nShutting down...")
    finally:
        # Safe Cleanup
        if 'stream' in locals() and stream is not None:
            try:
                if stream.is_active():
                    stream.stop_stream()
            except Exception:
                pass
            try:
                stream.close()
            except Exception:
                pass
        if pa: pa.terminate()
        if porcupine: porcupine.delete()
def classify_user_emotion(text: str) -> str:
    t = text.lower()
    if any(w in t for w in ["angry", "stupid", "hate"]): return "anger"
    if any(w in t for w in ["sad", "depressed", "lonely"]): return "sad"
    if any(w in t for w in ["anxious", "scared", "panic", "stress"]): return "anxious"
    return "neutral"

def append_hri_example(user_text, bot_text, emotion):
    try:
        record = {"timestamp": datetime.datetime.utcnow().isoformat(), "emotion": emotion, "input": user_text, "output": bot_text}
        with open(HRI_DATASET_PATH, "a", encoding="utf-8") as f: f.write(json.dumps(record) + "\n")
    except: pass

def router_check(text):
    task_words = [
        "add", "schedule", "play", "stop", "pause", "resume", "next", "skip", 
        "music", "spotify", "email", "calendar", "todo", "list", "open", 
        "note", "outlook", "search", "remind", "weather", "track", "what", "when", "where",
        "forecast", "temperature", "rain", "hot", "cold", "weather", "look up", "what", "when", "who" # <-- Added Weather keywords
    ]
    
    print(f"DEBUG: Checking text -> '{text}'")
    text_lower = text.lower()
    
    # Iterate to find the specific match for debugging
    for word in task_words:
        if word in text_lower:
            print(f"DEBUG: Found match! Keyword '{word}' is in text.")
            return "TASK"
            
    print("DEBUG: No task keywords found.")
    return "CHAT"

def evolve_brain(conversation_history):
    if not conversation_history: return
    try:
        with open("brain_config.txt", "r") as f: current_brain_state = f.read()
    except: current_brain_state = "Generic assistant."
    try:
        model = genai.GenerativeModel("gemini-2.5-flash-preview-09-2025")
        prompt = f"Rewrite instructions based on this chat history:\n{conversation_history}"
        res = model.generate_content(prompt)
        if res.text:
            with open("brain_config.txt", "w") as f: f.write(res.text)
    except: pass


# ==============================================================================
# --- 3. HELPER FUNCTIONS ---
# ==============================================================================
def speak_text(text):
    """Converts text to speech."""
    if not tts_engine: return
    try:
        import platform, subprocess
        clean_text = text.replace("*", "")
        if platform.system() == 'Darwin':
            subprocess.run(['say', clean_text])
        else:
            tts_engine.say(clean_text)
            tts_engine.runAndWait()
    except: pass



# --- EMOTION & LOGGING HELPERS ---
def classify_user_emotion(text: str) -> str:
    t = text.lower()
    if any(w in t for w in ["angry", "stupid", "hate"]): return "anger"
    if any(w in t for w in ["sad", "depressed", "lonely"]): return "sad"
    if any(w in t for w in ["anxious", "scared", "panic"]): return "anxious"
    if any(w in t for w in ["tired", "drained"]): return "low_energy"
    if any(w in t for w in ["lol", "haha", "joke"]): return "playful"
    if any(w in t for w in ["omg", "wow", "cool"]): return "excited"
    return "neutral"

def append_hri_example(user_text, bot_text, emotion):
    try:
        record = {
            "timestamp": datetime.datetime.utcnow().isoformat(),
            "emotion": emotion,
            "input": user_text,
            "output": bot_text
        }
        with open(HRI_DATASET_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")
    except: pass



# ==============================================================================
# --- 2. HELPER FUNCTIONS (TTS/STT) ---
# ==============================================================================

# --- REPLACED: gTTS/pygame function with pyttsx3 function ---
def speak_text(text_to_speak):
    """Converts text to speech. Uses native 'say' on Mac for stability."""
    import platform
    import subprocess
    
    try:
        # 1. Clean the text of Markdown (*bold*, etc)
        clean_text = text_to_speak.replace("*", "")
        
        # 2. Check OS and speak
        if platform.system() == 'Darwin': # Darwin means macOS
            # Escape quotes so the command line doesn't break
            clean_text = clean_text.replace('"', '').replace("'", "")
            subprocess.run(['say', clean_text])
        else:
            # Fallback for Windows/Linux
            tts_engine.say(clean_text)
            tts_engine.runAndWait()
            
    except Exception as e:
        print(f"Error during Text-to-Speech: {e}")
def play_music(query: str) -> str:
    """
    Robust playback: Transfers focus -> Plays -> Shuffles.
    """
    import time
    import subprocess
    
    query_lower = query.lower().strip()
    
    # --- FIX: Smarter detection ---
    # Checks if "liked" AND "song" are in the sentence.
    # Catches: "play my liked songs", "liked songs on spotify", "my liked songs please"
    if "liked" in query_lower and "song" in query_lower:
        is_liked_songs = True
    elif "favorites" in query_lower:
        is_liked_songs = True
    else:
        is_liked_songs = False

    try:
        sp = spotipy.Spotify(auth_manager=SpotifyOAuth(
            client_id=SPOTIPY_CLIENT_ID,
            client_secret=SPOTIPY_CLIENT_SECRET,
            redirect_uri=SPOTIPY_REDIRECT_URI,
            scope="user-modify-playback-state,user-read-playback-state,user-library-read"
        ))

        # 1. Get Device
        devices = sp.devices()
        if not devices or not devices['devices']:
            print("[Tool] Opening Spotify...")
            subprocess.run(['open', '-a', 'Spotify'])
            for i in range(10):
                time.sleep(1)
                try:
                    devices = sp.devices()
                    if devices and devices['devices']: break
                except: pass
        
        if not devices or not devices['devices']:
            return "Error: Spotify opened, but I can't connect to it. Please press Play manually once."

        # Prefer active device, or default to the first one
        device_id = devices['devices'][0]['id']
        for d in devices['devices']:
            if d['is_active']:
                device_id = d['id']
                break
        
        print(f"[Tool] Connecting to Device ID: {device_id}")

        # 2. Explicitly "Transfer" playback to wake the device up
        try:
            sp.transfer_playback(device_id=device_id, force_play=False)
            time.sleep(0.5)
        except:
            pass

        # 3. Play Music
        if is_liked_songs:
            print("[Tool] Fetching Liked Songs...")
            results = sp.current_user_saved_tracks(limit=40)
            uris = [item['track']['uri'] for item in results['items']]
            
            if not uris: return "Error: No Liked Songs found."

            # PLAY FIRST
            sp.start_playback(device_id=device_id, uris=uris)
            print("[Tool] Playback started.")
            
            # SHUFFLE SECOND
            time.sleep(1) 
            try:
                sp.shuffle(True, device_id=device_id)
                sp.next_track(device_id=device_id)
            except:
                pass
            return "Success: Playing your Liked Songs."

        else:
            # Search Logic
            print(f"[Tool] Searching: {query}")
            results = sp.search(q=query, limit=1, type='track,artist,playlist')
            
            if results['playlists']['items']:
                uri = results['playlists']['items'][0]['uri']
                name = results['playlists']['items'][0]['name']
                sp.start_playback(device_id=device_id, context_uri=uri)
                time.sleep(0.5)
                try: sp.shuffle(True, device_id=device_id)
                except: pass
                return f"Success: Playing playlist '{name}'."

            if results['tracks']['items']:
                uri = results['tracks']['items'][0]['uri']
                name = results['tracks']['items'][0]['name']
                artist = results['tracks']['items'][0]['artists'][0]['name']
                sp.start_playback(device_id=device_id, uris=[uri])
                return f"Success: Playing '{name}' by {artist}."

        return "Error: Song not found."

    except Exception as e:
        print(f"[Tool] Spotify Error: {e}")
        return f"Error: {e}"
def stop_music() -> str:
    """Pauses Spotify playback."""
    try:
        sp = spotipy.Spotify(auth_manager=SpotifyOAuth(
            client_id=SPOTIPY_CLIENT_ID,
            client_secret=SPOTIPY_CLIENT_SECRET,
            redirect_uri=SPOTIPY_REDIRECT_URI,
            scope="user-modify-playback-state,user-read-playback-state"
        ))
        
        sp.pause_playback()
        print("[Tool] Music paused.")
        return "Success: Music paused."
    except Exception as e:
        print(f"[Tool] Error pausing music: {e}")
        return "Error: Could not pause music (maybe nothing is playing?)"

    
def _get_channel_id(client, channel_name):
    """Helper to find the ID (e.g., C12345) for a channel name (e.g., general)."""
    try:
        result = client.conversations_list()
        for channel in result["channels"]:
            if channel["name"] == channel_name:
                return channel["id"]
        return None
    except SlackApiError as e:
        print(f"Error fetching channels: {e}")
        return None
# --- Gmail Tool ---

def get_gmail_service():
    """Authenticates and returns the Google Gmail service."""
    import pickle
    from google_auth_oauthlib.flow import InstalledAppFlow
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build
    
    # --- CRITICAL: We add Gmail ReadOnly scope here ---
    # We also keep Calendar scope so one login works for everything
    SCOPES = [
        'https://www.googleapis.com/auth/calendar',
        'https://www.googleapis.com/auth/gmail.readonly'
    ]
    
    creds = None
    if os.path.exists('token.pickle'):
        with open('token.pickle', 'rb') as token:
            creds = pickle.load(token)
            
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            # Re-authenticate with new scopes
            flow = InstalledAppFlow.from_client_secrets_file('credentials.json', SCOPES)
            creds = flow.run_local_server(port=0)
        with open('token.pickle', 'wb') as token:
            pickle.dump(creds, token)

    return build('gmail', 'v1', credentials=creds)

# ==============================================================================
# --- UPDATED GOOGLE TOOLS (Calendar + Gmail Unified) ---
# ==============================================================================

# GLOBAL SCOPES: We ask for both Calendar AND Gmail at the same time


def get_google_creds():
    """Helper to get unified credentials for both services."""
    import pickle
    import os
    from google_auth_oauthlib.flow import InstalledAppFlow
    from google.auth.transport.requests import Request
    
    creds = None
    # Load existing token
    if os.path.exists('token.pickle'):
        with open('token.pickle', 'rb') as token:
            creds = pickle.load(token)
            
    # If no valid token, let's log in
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception:
                # If refresh fails, force new login
                creds = None
                
        if not creds:
            print("[System] Authenticating Google Services (Calendar + Gmail)...")
            flow = InstalledAppFlow.from_client_secrets_file('credentials.json', GOOGLE_SCOPES)
            creds = flow.run_local_server(port=0)
            
        # Save the new token
        with open('token.pickle', 'wb') as token:
            pickle.dump(creds, token)
            
    return creds

def get_calendar_service():
    """Authenticates and returns the Google Calendar service."""
    from googleapiclient.discovery import build
    creds = get_google_creds()
    return build('calendar', 'v3', credentials=creds)

def get_gmail_service():
    """Authenticates and returns the Google Gmail service."""
    from googleapiclient.discovery import build
    creds = get_google_creds()
    return build('gmail', 'v1', credentials=creds)

# --- (Keep your existing get_upcoming_events / add_calendar_event functions here) ---
# ...
# ...

def get_unread_gmails() -> str:
    """
    Fetches the top 5 unread emails from your primary Gmail inbox.
    """
    try:
        service = get_gmail_service()
        
        print("[Tool] Fetching unread Gmails...")
        results = service.users().messages().list(
            userId='me', 
            q='is:unread label:INBOX', 
            maxResults=5
        ).execute()
        
        messages = results.get('messages', [])
        
        if not messages:
            return "You have no unread emails in Gmail."

        email_texts = []
        for msg in messages:
            msg_detail = service.users().messages().get(
                userId='me', id=msg['id'], format='full'
            ).execute()
            
            headers = msg_detail['payload']['headers']
            subject = "No Subject"
            sender = "Unknown"
            
            for h in headers:
                if h['name'] == 'Subject': subject = h['value']
                if h['name'] == 'From': sender = h['value']
            
            snippet = msg_detail.get('snippet', '')
            if "<" in sender: sender = sender.split("<")[0].strip()
                
            email_texts.append(f"- From: {sender}\n  Subject: {subject}\n  Snippet: {snippet}")

        return "Here are your latest unread Gmails:\n" + "\n\n".join(email_texts)

    except Exception as e:
        print(f"[Tool] Gmail Error: {e}")
        return f"Error: Could not read Gmail. {e}"
def read_slack_messages(channel_name: str = "general") -> str:
    """
    Reads the last 10 messages from a specific Slack channel (default: 'general').
    """
    client = WebClient(token=SLACK_BOT_TOKEN)
    
    try:
        # 1. Get Channel ID
        channel_id = _get_channel_id(client, channel_name)
        if not channel_id:
            return f"Error: I couldn't find a channel named '#{channel_name}'."

        # 2. Fetch History
        print(f"[Tool] Fetching messages from #{channel_name}...")
        result = client.conversations_history(channel=channel_id, limit=10)
        messages = result["messages"]
        
        if not messages:
            return f"#{channel_name} has no recent messages."

        # 3. Format for the AI
        summary_data = []
        # We also fetch user info to get real names instead of User IDs
        user_cache = {} 
        
        for msg in messages:
            # Skip 'channel join' messages
            if "subtype" in msg: 
                continue
                
            user_id = msg.get("user")
            text = msg.get("text")
            
            # Resolve User Name
            if user_id not in user_cache:
                try:
                    user_info = client.users_info(user=user_id)
                    user_cache[user_id] = user_info["user"]["real_name"]
                except:
                    user_cache[user_id] = "Unknown User"
            
            sender = user_cache[user_id]
            summary_data.append(f"{sender}: {text}")

        return f"Recent messages in #{channel_name}:\n" + "\n".join(summary_data)

    except SlackApiError as e:
        print(f"[Tool] Slack Error: {e}")
        return f"Error: {e}"

def send_slack_message(text: str, channel_name: str = "general") -> str:
    """Sends a message to a Slack channel."""
    client = WebClient(token=SLACK_BOT_TOKEN)
    try:
        channel_id = _get_channel_id(client, channel_name)
        if not channel_id:
            return f"Error: Channel '#{channel_name}' not found."
            
        client.chat_postMessage(channel=channel_id, text=text)
        return f"Success: Message sent to #{channel_name}."
    except SlackApiError as e:
        return f"Error sending message: {e}"
    
def parse_spoken_number(text):
    """Converts spoken numbers ('one', 'two', 'number 3') into an integer."""
    if not text:
        return None
    
    word_to_num = {
        'one': 1, 'two': 2, 'three': 3, 'four': 4, 'five': 5,
        'six': 6, 'seven': 7, 'eight': 8, 'nine': 9, 'ten': 10,
    }
    
    for word, num in word_to_num.items():
        if word in text:
            return num
            
    for char in text:
        if char.isdigit():
            return int(char)
            
    return None

# ==============================================================================
# --- 3. TOOL DEFINITIONS (PYTHON FUNCTIONS) ---
# ==============================================================================

# --- Google Calendar Tools ---

# ==============================================================================
# --- 3. TOOL DEFINITIONS (PYTHON FUNCTIONS) ---
# ==============================================================================

# --- Unified Google Tools (Calendar + Gmail) ---

def get_google_creds():
    """
    Handles authentication for BOTH Calendar and Gmail at once.
    Creates a single token.pickle with all necessary permissions.
    """
    import pickle
    import os
    from google_auth_oauthlib.flow import InstalledAppFlow
    from google.auth.transport.requests import Request
    
    # We request both scopes in one go
    UNIFIED_SCOPES = [
        'https://www.googleapis.com/auth/calendar',
        'https://www.googleapis.com/auth/gmail.readonly'
    ]
    
    creds = None
    if os.path.exists('token.pickle'):
        with open('token.pickle', 'rb') as token:
            creds = pickle.load(token)
            
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception:
                creds = None
        
        if not creds:
            print("[System] Authenticating Google Services (Calendar + Gmail)...")
            flow = InstalledAppFlow.from_client_secrets_file(
                'credentials.json', UNIFIED_SCOPES)
            creds = flow.run_local_server(port=0)
            
        with open('token.pickle', 'wb') as token:
            pickle.dump(creds, token)
            
    return creds

def get_calendar_service():
    from googleapiclient.discovery import build
    return build('calendar', 'v3', credentials=get_google_creds())

def get_gmail_service():
    from googleapiclient.discovery import build
    return build('gmail', 'v1', credentials=get_google_creds())

# --- Calendar Functions ---

def get_upcoming_events() -> str:
    """Gets the next 10 events from Google Calendar."""
    try:
        service = get_calendar_service()
        now = datetime.datetime.utcnow().isoformat() + 'Z'
        events_result = service.events().list(
            calendarId='primary', timeMin=now,
            maxResults=10, singleEvents=True,
            orderBy='startTime'
        ).execute()
        events = events_result.get('items', [])

        if not events:
            return "You have no upcoming events."
        
        event_list = "Your upcoming events are: "
        for event in events:
            start = event['start'].get('dateTime', event['start'].get('date'))
            start_dt = datetime.datetime.fromisoformat(start)
            event_list += f"{event['summary']} at {start_dt.strftime('%I:%M %p on %A, %B %d')}. "
        return event_list
    except Exception as e:
        print(f"[Tool] Error getting events: {e}")
        return f"Error: Could not retrieve calendar events. {e}"

def add_calendar_event(task: str, time_text: str) -> str:
    """Adds a new event to the primary Google Calendar."""
    try:
        service = get_calendar_service()
        cal = parsedatetime.Calendar()
        time_struct, _ = cal.parse(time_text)
        start_time = datetime.datetime(*time_struct[:6])
        end_time = start_time + datetime.timedelta(hours=1)

        event = {
            'summary': task.capitalize(),
            'start': {'dateTime': start_time.isoformat(), 'timeZone': 'Asia/Karachi'},
            'end': {'dateTime': end_time.isoformat(), 'timeZone': 'Asia/Karachi'},
        }

        service.events().insert(calendarId='primary', body=event).execute()
        return f"Success: Added '{task}' at {start_time.strftime('%I:%M %p')}."
    except Exception as e:
        print(f"[Tool] Error adding event: {e}")
        return f"Error: {e}"

def cancel_calendar_event(summary_keyword: str, time_text: str) -> str:
    """Cancels a calendar event."""
    try:
        service = get_calendar_service()
        cal = parsedatetime.Calendar()
        time_struct, _ = cal.parse(time_text)
        search_date = datetime.datetime(*time_struct[:6])
        
        time_min = search_date.replace(hour=0, minute=0).isoformat() + 'Z'
        time_max = search_date.replace(hour=23, minute=59).isoformat() + 'Z'
        
        events_result = service.events().list(
            calendarId='primary', timeMin=time_min, timeMax=time_max, singleEvents=True
        ).execute()
        events = events_result.get('items', [])
        
        matches = [e for e in events if summary_keyword.lower() in e.get('summary', '').lower()]
        
        if len(matches) == 1:
            service.events().delete(calendarId='primary', eventId=matches[0]['id']).execute()
            return f"Success: Cancelled '{matches[0]['summary']}'."
        elif len(matches) > 1:
            return "Multiple matching events found. Please be more specific."
        else:
            return "No matching events found."
    except Exception as e:
        return f"Error: {e}"

# --- Gmail Functions ---

def get_unread_gmails() -> str:
    """Fetches the top 5 unread emails from Gmail."""
    try:
        service = get_gmail_service()
        print("[Tool] Fetching unread Gmails...")
        results = service.users().messages().list(
            userId='me', q='is:unread label:INBOX', maxResults=5
        ).execute()
        
        messages = results.get('messages', [])
        if not messages: return "You have no unread emails."

        email_texts = []
        for msg in messages:
            msg_detail = service.users().messages().get(userId='me', id=msg['id'], format='full').execute()
            headers = msg_detail['payload']['headers']
            subject = next((h['value'] for h in headers if h['name'] == 'Subject'), "No Subject")
            sender = next((h['value'] for h in headers if h['name'] == 'From'), "Unknown")
            snippet = msg_detail.get('snippet', '')
            
            if "<" in sender: sender = sender.split("<")[0].strip()
            email_texts.append(f"- From: {sender}\n  Subject: {subject}\n  Snippet: {snippet}")

        return "Unread Emails:\n" + "\n\n".join(email_texts)
    except Exception as e:
        print(f"[Tool] Gmail Error: {e}")
        return f"Error: {e}"

# --- Email Tools (SMTP) ---
# (Paste the rest of your tools below this...)

# --- Email Tools ---

def get_contact_email(name: str) -> str:
    """
    Finds the email address for a given contact name.
    Returns the email address as a string or 'None' if not found.
    """
    name_lower = name.lower()
    email = contacts.get(name_lower)
    if email:
        print(f"[Tool] Found contact: {name} -> {email}")
        return email
    else:
        print(f"[Tool] Contact not found: {name}")
        return "None" # Return a string "None" for the LLM

def add_new_contact(name: str, email: str) -> str:
    """
    Adds a new contact to the runtime contact list.
    This is used when the user provides a new email for someone.
    """
    name_lower = name.lower()
    # Basic email validation
    if "@" not in email or "." not in email:
        return f"Error: '{email}' is not a valid email address."
        
    contacts[name_lower] = email
    print(f"[Tool] Added new contact: {name_lower} -> {email}")
    return f"Success: Contact {name} added with email {email}."

def generate_email_draft(prompt: str, time_info: str = None) -> str:
    """
    Generates the body of an email using a separate Gemini model.
    'prompt' is what the user wants to say.
    'time_info' is optional context like 'tomorrow at 5pm'.
    """
    
    # System prompt for the email *writer* model
    email_writer_prompt = """You are a professional email writing assistant.
    Your task is to generate only the body of an email based on the user's prompt.
    Do not include a subject line or any 'To:' or 'From:' information.
    Just write the email content itself.
    Do not include a signature or any closing like 'Best regards' or 'Sincerely'."""
    
    try:
        # We use a separate model for this specialized task
        email_model = genai.GenerativeModel(
            model_name="gemini-2.5-flash-preview-09-2025",
            system_instruction=email_writer_prompt 
        )
        
        full_prompt = prompt
        if time_info:
            full_prompt += f" (Please make sure to mention this time: {time_info})"
            
        print(f"[Tool] Generating email draft for prompt: '{full_prompt}'")
        response = email_model.generate_content(full_prompt)
        generated_body = response.text
        
        # Add the user's signature
        generated_body += MY_SIGNATURE
        print(f"[Tool] Email draft generated.")
        return generated_body
        
    except Exception as e:
        print(f"[Tool] Error during email generation: {e}")
        return f"Error: Could not generate draft. {e}"

def send_email_via_smtp(recipient_email: str, subject: str, body: str) -> str:
    """
    Sends the email using smtplib.
    This is the final step and should only be called after user confirmation.
    """
    print(f"\n[Tool] Sending email to: {recipient_email}...")
    msg = EmailMessage()
    msg['Subject'] = subject
    msg['From'] = SENDER_EMAIL
    msg['To'] = recipient_email
    msg.set_content(body)

    try:
        context = ssl.create_default_context()
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=context) as server:
            server.login(SENDER_EMAIL, SENDER_PASSWORD)
            server.send_message(msg)
        print(f"[Tool] Email successfully sent to {recipient_email}!")
        return "Success: Email has been sent."
    except Exception as e:
        print(f"[Tool] Error sending email: {e}")
        return f"Error: Failed to send email. {e}"

# --- Local To-Do Database Tools ---

def add_todo_task(task: str) -> str:
    """Adds a single task to the local to-do list."""
    try:
        conn = sqlite3.connect('tasks.db')
        cursor = conn.cursor()
        cursor.execute("INSERT INTO todo (task) VALUES (?)", (task,))
        conn.commit()
        conn.close()
        print(f"[Tool] Added to-do: {task}")
        return f"Success: Task '{task}' added to your to-do list."
    except Exception as e:
        return f"Error: {e}"

def get_todo_list() -> str:
    """Reads all pending tasks from the to-do list."""
    try:
        conn = sqlite3.connect('tasks.db')
        cursor = conn.cursor()
        cursor.execute("SELECT task FROM todo WHERE status = 'pending'")
        tasks = cursor.fetchall()
        conn.close()
        
        if not tasks:
            return "Your to-do list is empty."
            
        task_list = ", ".join([f"'{task[0]}'" for task in tasks])
        return f"Your current to-do items are: {task_list}"
    except Exception as e:
        return f"Error: {e}"

def complete_todo_task(task_name: str) -> str:
    """
    Finds a to-do task by a keyword and marks it as 'complete'.
    'task_name' can be a partial match.
    """
    try:
        conn = sqlite3.connect('tasks.db')
        cursor = conn.cursor()
        
        # Use LIKE to find the task
        search_term = f"%{task_name}%"
        
        # First, find matching pending tasks
        cursor.execute("SELECT id, task FROM todo WHERE status = 'pending' AND task LIKE ?", (search_term,))
        matches = cursor.fetchall()
        
        if len(matches) == 0:
            return f"No pending to-do item found matching '{task_name}'."
        if len(matches) > 1:
            return f"I found multiple tasks matching '{task_name}'. Please be more specific."
        
        # Exactly one match, update it
        task_id = matches[0][0]
        task_content = matches[0][1]
        
        cursor.execute("UPDATE todo SET status = 'complete' WHERE id = ?", (task_id,))
        conn.commit()
        conn.close()
        
        print(f"[Tool] Completed task: {task_content}")
        return f"Success: I've marked '{task_content}' as complete."
        
    except Exception as e:
        return f"Error: {e}"

# --- Note Taking Tools ---

def start_note_taking() -> str:
    """
    Enters a special 'Note Taker Mode' to transcribe continuously.
    Listens until the user says 'stop note' or a similar command.
    Returns the full transcription.
    """
    global r # Use the global recognizer
    
    speak_text("Starting transcription. Say 'stop note' when you are finished.")
    
    full_transcription = []
    stop_listening = None
    
    # --- FIX for AssertionError: Calibrate using a *temporary* source ---
    print("[Note Taker Mode] Calibrating... please be quiet for a moment.")
    with sr.Microphone(sample_rate=16000, chunk_size=512) as calibration_source:
        r.adjust_for_ambient_noise(calibration_source, duration=1.0)
    print("[Note Taker Mode] Calibration complete.")

    # --- Create the *real* source for background listening ---
    background_source = sr.Microphone(sample_rate=16000, chunk_size=512)

    def background_callback(recognizer, audio):
        """Callback for the background listener."""
        try:
            chunk = recognizer.recognize_google(audio).lower()
            print(f"[Note Taker Mode] Heard: \"{chunk}\"")
            
            # --- Flexible stop command check ---
            stop_commands = ["stop note", "stop taking", "stop tracking", "close note"]
            if any(cmd in chunk for cmd in stop_commands):
                print(f"[Note Taker Mode] Stop command '{chunk}' heard.")
                stop_listening() # This will stop the listener
            else:
                full_transcription.append(chunk)
        except sr.UnknownValueError:
            print("[Note Taker Mode] Did not understand audio chunk.")
        except sr.RequestError as e:
            print(f"[Note Taker Mode] Speech recognition error: {e}")

    # Start the background listener
    stop_listening = r.listen_in_background(background_source, background_callback)
    print("[Note Taker Mode] Background listener started.")

    # Loop and wait until stop_listening is called
    while stop_listening:
        time.sleep(0.1)

    print("[Note Taker Mode] Transcription finished.")
    return " ".join(full_transcription)

def summarize_text(text: str) -> str:
    """Summarizes a long piece of text (like a note)."""
    try:
        # Use a model for this specialized task
        summarizer_model = genai.GenerativeModel(
            model_name="gemini-2.5-flash-preview-09-2025",
            system_instruction="You are a summarization expert. Take the following text and provide a concise, one-paragraph summary. Do not add any conversational intro or outro."
        )
        print(f"[Tool] Summarizing text (length: {len(text)})...")
        response = summarizer_model.generate_content(text)
        print("[Tool] Summary generated.")
        return response.text
    except Exception as e:
        print(f"[Tool] Error during summarization: {e}")
        return f"Error: Could not summarize text. {e}"

def save_note(title: str, content: str) -> str:
    """Saves a new note to the 'notes' table in the database."""
    try:
        conn = sqlite3.connect('tasks.db')
        cursor = conn.cursor()
        cursor.execute("INSERT INTO notes (title, content) VALUES (?, ?)", (title, content))
        conn.commit()
        conn.close()
        print(f"[Tool] Note saved: {title}")
        return f"Success: Note saved with title '{title}'."
    except Exception as e:
        return f"Error: {e}"
# --- NEW: Database Logger for Dashboard ---
def log_interaction(speaker, message):
    """Saves conversation to DB so the Dashboard can display it."""
    try:
        # We create a fresh connection each time to avoid threading issues
        conn = sqlite3.connect('tasks.db') 
        c = conn.cursor()
        c.execute("INSERT INTO logs (speaker, message) VALUES (?, ?)", (speaker, message))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"Log error: {e}")

def read_note(title_keyword: str) -> str:
    """Reads a note from the 'notes' table by searching for a keyword in the title."""
    try:
        conn = sqlite3.connect('tasks.db')
        cursor = conn.cursor()
        
        search_term = f"%{title_keyword}%"
        cursor.execute("SELECT title, content FROM notes WHERE title LIKE ?", (search_term,))
        matches = cursor.fetchall()
        
        if len(matches) == 0:
            return f"No note found with a title matching '{title_keyword}'."
        if len(matches) > 1:
            return f"I found multiple notes matching '{title_keyword}'. Please be more specific."
        
        # Exactly one match
        title = matches[0][0]
        content = matches[0][1]
        
        print(f"[Tool] Reading note: {title}")
        return f"Note title: {title}. Content: {content}"
        
    except Exception as e:
        return f"Error: {e}"


# --- Web Search Tool ---
def evolve_brain(conversation_history):
    """
    The Self-Editing Mechanism.
    Reads the current 'Brain Config', compares it with recent events,
    and REWRITES the Brain Config to be more attuned to the user.
    """
    if not conversation_history: return

    # Read the current brain state
    try:
        with open("brain_config.txt", "r") as f:
            current_brain_state = f.read()
    except FileNotFoundError:
        current_brain_state = "You are a generic assistant."

    # The "Editor" Model
    editor_model = genai.GenerativeModel(
        model_name="gemini-2.5-flash-preview-09-2025",
        system_instruction="""
        You are the Architect of an AI's personality. 
        Your job is to REWRITE the AI's System Instruction (User Profile/Rules) based on new evidence.
        
        INPUT:
        1. Old System Instruction.
        2. Recent Conversation.
        
        TASK:
        Update the System Instruction to reflect the user's projects, tone preferences, and habits.
        - If the user was casual, change the tone rule to "Be casual".
        - If the user talked about a project (e.g. StealthBot), ADD it to the "Known Projects" section.
        - REMOVE generic rules if they conflict with the user's style.
        
        OUTPUT:
        The FULL, NEW System Instruction text only. Do not output markdown or explanations.
        """
    )
    
    try:
        chat_text = "\n".join(conversation_history)
        prompt = f"""
        OLD BRAIN CONFIG:
        {current_brain_state}
        
        RECENT INTERACTION:
        {chat_text}
        
        ACTION:
        Rewrite the Brain Config to permanently adapt to this user.
        """
        
        response = editor_model.generate_content(prompt)
        new_brain_state = response.text.strip()
        
        # Safety check: Ensure it didn't delete everything
        if len(new_brain_state) > 50:
            print("\n[🧬 Evolution] The Brain has rewritten itself based on this interaction.")
            
            # Overwrite the file
            with open("brain_config.txt", "w") as f:
                f.write(new_brain_state)
                
    except Exception as e:
        print(f"[Evolution Error]: {e}")
def listen_for_command(timeout_seconds=8):
    """
    Captures audio. 
    Fixes the 'float > str' crash by forcing the timeout to be a number.
    """
    with sr.Microphone(sample_rate=16000, chunk_size=512) as source:
        try:
            # --- FIX: Force convert to float ---
            # This prevents crashes if 'timeout_seconds' is passed as a string "8"
            val = float(timeout_seconds) if timeout_seconds else 5.0
            
            # Listen (No ambient adjustment here for speed)
            audio = r.listen(source, timeout=val, phrase_time_limit=15)
            
            cmd = r.recognize_google(audio).lower()
            print(f"I heard: {cmd}")
            return "_CANCEL_" if "cancel" in cmd else cmd
            
        except sr.WaitTimeoutError:
            return None # Silence
        except Exception as e:
            print(f"Listen Error: {e}")
            return None
# --- System Application Tool ---

def open_application(app_name: str) -> str:
    """
    Opens a desktop application by its name.
    'app_name' should be the name of the app, e.g., 'Spotify', 'Weather', 'Notes'.
    """
    # --- FIX: Moved imports inside the function ---
    import subprocess
    import platform
    
    system = platform.system()
    # --- FIX: Removed .capitalize() ---
    app_name_cleaned = app_name.strip()
    
    try:
        if system == "Darwin":  # macOS
            cmd = ['open', '-a', app_name_cleaned]
            result = subprocess.run(cmd, check=True, capture_output=True, text=True)
            if "Unable to find application" in result.stderr:
                 raise FileNotFoundError(f"macOS error: {result.stderr}")

        elif system == "Windows":
            # This is a basic way, 'start' is a shell command
            cmd = ['start', app_name_cleaned]
            subprocess.run(cmd, check=True, shell=True)
            
        elif system == "Linux":
            # This assumes the app is in the system's PATH
            cmd = [app_name_cleaned.lower()]
            subprocess.Popen(cmd)
            
        else:
            return f"Error: Unsupported operating system '{system}'."
        
        print(f"[Tool] Opened application: {app_name_cleaned}")
        return f"Success: Opened {app_name_cleaned}."

    except FileNotFoundError:
        print(f"[Tool] Application not found: {app_name_cleaned}")
        return f"Error: I couldn't find an application named '{app_name_cleaned}'."
    except subprocess.CalledProcessError as e:
        # --- FIX: Better error reporting ---
        if "Unable to find application" in e.stderr:
             print(f"[Tool] Application not found: {app_name_cleaned}")
             return f"Error: I couldn't find an application named '{app_name_cleaned}'."
        print(f"[Tool] Error opening application: {e.stderr}")
        return f"Error: I encountered an error trying to open {app_name_cleaned}."
    except Exception as e:
        print(f"[Tool] Error opening application: {e}")
        return f"Error: {e}"


# ==============================================================================
# --- 4. CONVERSATIONAL AGENT SETUP (THE "BRAIN") ---
# ==============================================================================

# --- UPDATED: System Prompt for concise, fast responses AND note-taking ---
agent_system_prompt = f"""You are 'Pengo', a voice-activated productivity assistant.
Your job is to be **fast, efficient, and concise**.
Get straight to the point. Avoid conversational filler like "Sure!" or "Happy to help!".
Confirm tasks with simple, direct language.

**Your Rules:**
1.  **Be Concise:** Do not use pleasantries. Go directly to the action or question.
    -   *Bad:* "Sure, I can help with that! Who should I send the email to?"
    -   *Good:* "Who is the email for?"
    -   *Bad:* "Hey Izzan, looks like it's **54°F and clear** in Lahore right now! It seems a bit cool and pleasant today.

Need anything else checked, or maybe a quick reminder added for the LUMS Robot project? Just let me know."
    -   *Good:* "looks like it's **54°F and clear** in Lahore right now! It seems a bit cool and pleasant today."
2.  **Ask for Missing Info:** If a command is incomplete, ask for the missing pieces one by one.
3.  **Clarify Ambiguity:** If unsure about a name, ask for clarification.
4.  **Confirm Before Action:** Confirm destructive actions (sending email, deleting events) with a simple question. (e.g., "Draft: [body]. Send?")
5.  **Handle Errors:** State the error clearly. (e.g., "Error: I couldn't find that contact.")
6.  **Today's Date:** For context, today is {datetime.datetime.now().strftime('%A, %B %d, %Y')}.

**--- WEB SEARCH RULES ---**
7.  **How to Search:** For facts or real-time info, use `web_search`. Do not answer from memory.
8.  **Analyze Snippets:** The `web_search` tool returns text snippets. You must read them.
9.  **BE SMART. RE-SEARCH IF NEEDED:** If snippets are general, **call the tool again** with a more specific query. (e.g., "current temperature in Lahore" or "USD to PKR exchange rate").

**--- APPLICATION RULES ---**
10. **Open, Don't Control:** Use `open_application` to launch apps.
11. **Explain Limits:** If the user asks you to *control* an app (e.g., "play a song"), you must *open the app* and then state: "I can open {{app_name}}, but I cannot control playback."

**--- NOTE TAKING RULES ---**
12. **Start Transcribing:** If the user asks to "take a note" or "start transcription", call the `start_note_taking()` tool.
13. **Note Taking is a SPECIAL MODE:** The `start_note_taking()` tool will take over and return the full transcription.
14. **After Note Taking:** When the tool returns the full text, your *only* job is to ask the user: "Do you want to **summarize** this or **save the full text**?"
15. **Summarize or Save:**
    - If "summarize", call `summarize_text()` with the transcription. Then, ask for a title and call `save_note(title, summary)`.
    - If "save full text", ask for a title and call `save_note(title, transcription)`.

**Your Tools:**
- `get_upcoming_events()`: Reads Google Calendar.
- `add_calendar_event(task, time_text)`: Adds event to Google Calendar.
- `cancel_calendar_event(summary_keyword, time_text)`: Deletes event from Google Calendar.
- `get_contact_email(name)`: Checks the contact list.
- `add_new_contact(name, email)`: Adds a new contact.
- `generate_email_draft(prompt, time_info)`: Writes the email body.
- `send_email_via_smtp(recipient_email, subject, body)`: Sends the final email.
- `get_todo_list()`: Reads the local to-do list.
- `add_todo_task(task)`: Adds a local to-do.
- `complete_todo_task(task_name)`: Marks a local to-do as complete.
- `start_note_taking()`: Begins continuous transcription.
- `summarize_text(text)`: Summarizes a block of text.
- `save_note(title, content)`: Saves a note to the database.
- `read_note(title_keyword)`: Reads a saved note.
- `web_search(query)`: Searches the web.
- `open_application(app_name)`: Opens a desktop app.
- `play_music(query)`: Plays specific songs or playlists on Spotify.
- `stop_music()`: Pauses Spotify playback.
- `read_slack_messages(channel_name)`: Reads recent chat history. Default is 'general'.
- `send_slack_message(text, channel_name)`: Sends a message to Slack.
- `get_unread_gmails()`: Reads unread emails from Gmail.
"""
def web_search(query: str) -> str:
    try:
        from ddgs import DDGS
        # Latency Optimization: max_results=1
        # We only need the top answer for a voice assistant.
        with DDGS() as ddgs:
            results = ddgs.text(query, max_results=1)
            if not results: return "No results."
            
            # Return just the snippet of the first result
            return f"Search Result: {results[0]['body']}"
            
    except Exception as e:
        print(f"Search Error: {e}")
        return "Search failed."
# Define the tools for the Gemini model
tools_list = [
    get_upcoming_events, play_music, add_calendar_event, cancel_calendar_event,
    get_contact_email, add_new_contact, generate_email_draft, send_email_via_smtp,
    get_todo_list, add_todo_task, complete_todo_task,
    start_note_taking, summarize_text, save_note, read_note,
    web_search, open_application, stop_music,
    read_slack_messages, send_slack_message,
    get_unread_gmails
    # Note: 'remember_fact' is removed because 'evolve_brain' handles memory now
]


# Configure the agent model
def get_fresh_agent():
    """
    Reads 'brain_config.txt' and returns a BRAND NEW model instance.
    """
    try:
        with open("brain_config.txt", "r") as f:
            current_system_prompt = f.read()
    except FileNotFoundError:
        current_system_prompt = f"""You are Pengo.
        Current Time: {datetime.datetime.now().strftime('%I:%M %p, %A, %B %d, %Y')}.
        Be helpful and concise."""

    try:
        genai.configure(api_key=GEMINI_API_KEY)
        model = genai.GenerativeModel(
            model_name="gemini-2.5-flash-preview-09-2025",
            system_instruction=current_system_prompt,
            tools=tools_list
        )
        return model
    except Exception as e:
        print(f"Error creating agent: {e}")
        return None

# ==============================================================================
# --- 5. NEW MAIN CONVERSATIONAL LOOP ---
# ==============================================================================

# ==============================================================================
# --- 5. MAIN PIPELINE (SELF-EVOLVING) ---
# ==============================================================================

# ==============================================================================
# --- 5. MAIN PIPELINE (SELF-EVOLVING + METRICS PER TURN) ---
# ==============================================================================
# ==============================================================================
# --- 5. MAIN PIPELINE (FIXED METRICS & ROUTING) ---
# ==============================================================================

def main_pipeline(command):
    print(f"\nUser: {command}")
    
    # --- FIX: REMOVED load_local_brain() FROM HERE ---
    # The model loads only once at startup now.
    
    turn_start_time = time.perf_counter()
    intent = router_check(command)
    routing_duration = time.perf_counter() - turn_start_time
    print(f"[Router] Intent: {intent} (Took {routing_duration:.4f}s)")
    
    final_response_text = ""
    llm_duration = 0
    current_session_history = [f"User: {command}"]

    if intent == "CHAT":
        llm_start = time.perf_counter()
        response = generate_hri_response(command)
        llm_duration = time.perf_counter() - llm_start
        print(f"Pengo (Local): {response}")
        
        turn_total_duration = time.perf_counter() - turn_start_time
        perf_logger.log(intent, routing_duration, llm_duration, turn_total_duration)
        speak_text(response)
        final_response_text = response
        
    else:
        llm_start = time.perf_counter()
        try:
            agent_model = get_fresh_agent()
            if not agent_model: raise Exception("Brain config failed")
            chat = agent_model.start_chat(enable_automatic_function_calling=True)
            curr_cmd = command
            turn_count = 0
            
            while True:
                turn_count += 1
                if turn_count > 1: turn_start_time = time.perf_counter()
                
                llm_turn_start = time.perf_counter()
                res = chat.send_message(curr_cmd)
                llm_turn_duration = time.perf_counter() - llm_turn_start
                txt = "".join([p.text for p in res.parts if p.text]) or "Done."
                
                print(f"Pengo (Cloud): {txt}")
                turn_total_duration = time.perf_counter() - turn_start_time
                perf_logger.log("Cloud", 0, llm_turn_duration, turn_total_duration)
                
                speak_text(txt)
                current_session_history.append(f"Pengo: {txt}")
                
                if "?" not in txt:
                    final_response_text = txt
                    print("--- Conversation Task Complete ---")
                    break
                
                user_response = listen_for_command(timeout_seconds=8)
                if not user_response or "_CANCEL_" in user_response: break
                curr_cmd = user_response
                current_session_history.append(f"User: {user_response}")
                
            evolve_brain(current_session_history)

        except Exception as e:
            print(f"Cloud Error: {e}")
            speak_text("I couldn't reach the cloud.")
            final_response_text = "Error."

    try:
        emotion = classify_user_emotion(command)
        append_hri_example(command, final_response_text, emotion)
        print(f"💾 Saved interaction to {HRI_DATASET_PATH}")
    except Exception as e: print(f"❌ Logging failed: {e}")
# ==============================================================================
# --- 6. WAKE WORD ENGINE (MAIN ENTRY POINT) (FIXED) ---
# ==============================================================================
# ==============================================================================
# --- 6. WAKE WORD ENGINE (FIXED) ---
## ==============================================================================
# --- 6. WAKE WORD ENGINE (FIXED) ---
# ==============================================================================

# ==============================================================================
# --- HELPER FUNCTION FIX ---
# ==============================================================================

def listen_for_command(prompt=None, timeout_seconds=8):
    """
    Captures audio.
    Flexible arguments: Handles "Listening..." string OR timeout number.
    """
    # Detect if the first argument is actually a timeout number
    if isinstance(prompt, (int, float)):
        timeout_seconds = prompt
        prompt = None
    
    # If a text prompt was passed, print/speak it
    if isinstance(prompt, str):
        print(f"\n{prompt}")

    # Ensure timeout is a valid float
    timeout_val = float(timeout_seconds) if timeout_seconds else 5.0

    with sr.Microphone(sample_rate=16000, chunk_size=512) as source:
        try:
            # Optimization: No ambient adjustment (relies on startup calibration)
            audio = r.listen(source, timeout=timeout_val, phrase_time_limit=15)
            
            cmd = r.recognize_google(audio).lower()
            print(f"I heard: {cmd}")
            return "_CANCEL_" if "cancel" in cmd else cmd
            
        except sr.WaitTimeoutError:
            return None # Silence
        except sr.UnknownValueError:
            return None # Sound but no speech
        except Exception as e:
            print(f"Listen Error: {e}")
            return None
import sqlite3, os, shutil, time



# ==============================================================================
# --- WAKE WORD LOOP FIX ---
# ==============================================================================

def start_wake_word_loop():
    """
    Passive wake-word loop using Picovoice Porcupine + PyAudio.

    Features:
    - Uses Porcupine (Picovoice) only (NO openwakeword)
    - Safe audio stream close/reopen (avoids bad state after STT / pipeline)
    - Polls SQLite web_commands table periodically
    - Recovers from corrupted SQLite DB ("disk image is malformed")
    - Clean shutdown + resource cleanup
    """

    import os
    import time
    import shutil
    import sqlite3
    import struct
    import pyaudio
    import pvporcupine

    DB_PATH = "tasks.db"
    POLL_EVERY_SEC = 1.0  # DB poll interval

    # -------------------------
    # DB helpers
    # -------------------------
    def _recreate_db(conn):
        conn.execute("""
            CREATE TABLE IF NOT EXISTS logs (
                id INTEGER PRIMARY KEY,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                speaker TEXT,
                message TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS web_commands (
                id INTEGER PRIMARY KEY,
                command TEXT,
                status TEXT DEFAULT 'pending'
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS todo (
                id INTEGER PRIMARY KEY,
                task TEXT,
                status TEXT DEFAULT 'pending'
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS notes (
                id INTEGER PRIMARY KEY,
                title TEXT,
                content TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS user_prefs (
                id INTEGER PRIMARY KEY,
                fact TEXT UNIQUE
            )
        """)
        conn.commit()


    # -------------------------
    # Audio helpers
    # -------------------------
    def _safe_close_stream(s):
        if s is None:
            return
        try:
            if hasattr(s, "is_active") and s.is_active():
                s.stop_stream()
        except Exception:
            pass
        try:
            s.close()
        except Exception:
            pass

    # -------------------------
    # Optional Google auth
    # -------------------------
    try:
        if "get_google_creds" in globals():
            get_google_creds()
            print("[Google] Authenticated.")
    except Exception as e:
        print(f"[Google] Auth warning: {e}")

    speak_text("Pengo is online.")
    print("[System] Starting Porcupine wake-word loop...")

    porcupine = None
    pa = None
    audio_stream = None
    db_conn = None

    try:
        # DB open
        db_conn = open_sqlite_with_recovery(DB_PATH)

        # Porcupine init
        porcupine = pvporcupine.create(
            access_key=PICOVOICE_ACCESS_KEY,
            keyword_paths=["Pengo_en_mac_v3_0_0.ppn"]
        )

        # PyAudio init
        pa = pyaudio.PyAudio()

        def _open_audio_stream():
            return pa.open(
                rate=porcupine.sample_rate,
                channels=1,
                format=pyaudio.paInt16,
                input=True,
                frames_per_buffer=porcupine.frame_length
            )

        audio_stream = _open_audio_stream()
        print("Listening for wake word: 'Pengo'...")

        last_poll = time.time()

        while True:
            # --------- DB poll (time-based, not frame-based) ----------
            now = time.time()
            if now - last_poll >= POLL_EVERY_SEC:
                last_poll = now
                try:
                    if db_conn is None:
                        db_conn = open_sqlite_with_recovery(DB_PATH)

                    cur = db_conn.cursor()
                    cur.execute("SELECT id, command FROM web_commands WHERE status='pending' ORDER BY id ASC LIMIT 1")
                    row = cur.fetchone()

                    if row:
                        cmd_id, web_command = row
                        print(f"\n[Web Command] {web_command}")

                        cur.execute("UPDATE web_commands SET status='done' WHERE id=?", (cmd_id,))
                        db_conn.commit()

                        # Stop audio while executing pipeline (prevents stream glitches)
                        _safe_close_stream(audio_stream)
                        audio_stream = None

                        main_pipeline(web_command)

                        # Reopen stream after pipeline
                        audio_stream = _open_audio_stream()
                        print("Listening for wake word: 'Pengo'...")
                        continue

                except sqlite3.DatabaseError as e:
                    print(f"[DB] Poll error: {e}")
                    try:
                        if db_conn:
                            db_conn.close()
                    except Exception:
                        pass
                    db_conn = None  # recover next poll

            # --------- Wake word audio ----------
            if audio_stream is None:
                audio_stream = _open_audio_stream()

            try:
                pcm_bytes = audio_stream.read(porcupine.frame_length, exception_on_overflow=False)
            except OSError:
                # overflow / device hiccup; keep going
                continue

            pcm = struct.unpack_from("h" * porcupine.frame_length, pcm_bytes)
            keyword_index = porcupine.process(pcm)

            if keyword_index >= 0:
                print("Wake word detected!")

                _safe_close_stream(audio_stream)
                audio_stream = None

                speak_text("Yes?")
                cmd = listen_for_command("Listening...", timeout_seconds=8)

                if cmd and cmd != "_CANCEL_":
                    main_pipeline(cmd)

                # reopen after command
                audio_stream = _open_audio_stream()
                print("Listening for wake word: 'Pengo'...")

    except KeyboardInterrupt:
        print("\nShutting down...")

    except Exception as e:
        print(f"[WakeLoop] Critical Error: {e}")

    finally:
        _safe_close_stream(audio_stream)

        try:
            if db_conn:
                db_conn.close()
        except Exception:
            pass

        try:
            if pa:
                pa.terminate()
        except Exception:
            pass

        try:
            if porcupine:
                porcupine.delete()
        except Exception:
            pass
if __name__ == "__main__":
    start_wake_word_loop()
