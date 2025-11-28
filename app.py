import eventlet # <--- REQUIRED FOR RENDER/GUNICORN ASYNC MODE
eventlet.monkey_patch() # <--- REQUIRED FOR RENDER/GUNICORN ASYNC MODE

import os 
import socket
import random
from datetime import datetime, timedelta
from flask import Flask, render_template_string, request, redirect, url_for, session
from flask_socketio import SocketIO, emit
import time
import json
import google.generativeai as genai
from eventlet import wsgi # <--- Used for running the robust server locally

# Custom JSONEncoder to handle datetime objects
class CustomJsonEncoder(json.JSONEncoder):
                  def default(self, obj):
                                    if isinstance(obj, datetime):
                                                      # Convert datetime objects to ISO 8601 formatted strings
                                                      return obj.isoformat()
                                    return super().default(obj)


# Create a custom JSON module that uses our custom encoder
class CustomJsonModule:
                  def dumps(self, obj, **kwargs):
                                    return json.dumps(obj, cls=CustomJsonEncoder, **kwargs)

                  def loads(self, s, **kwargs):
                                    return json.loads(s, **kwargs)


app = Flask(__name__)
# Get secret key from environment variable for deployment (if not, use fallback)
app.config['SECRET_KEY'] = os.getenv('FLASK_SECRET_KEY', 'your-secret-key-for-sessions!')

# SocketIO will now auto-detect and use eventlet.
socketio = SocketIO(app,
                                                                                          cors_allowed_origins="*",
                                                                                          json=CustomJsonModule(),
                                                                                          ping_interval=10,
                                                                                          ping_timeout=30)

# --- Game State Management ---
players = {}
game_state = {}
game_control = {'skip_to_answer': False, 'skip_question': False, 'show_podium': False, 'first_question_started': False}
host_sid = None
# Stores previous scores and rankings for animation
previous_scores = {}
previous_rankings = []

# --- API KEY CONFIGURATION (NEW FLEXIBLE LOGIC) ---
# 1. OPTION A: Hardcode your key inside the quotes below
HARDCODED_API_KEY = ""         # User can paste key here (Priority 2)

# 2. OPTION B: Load from Environment (PowerShell or Render ENV)
ENV_API_KEY = os.getenv('GEMINI_API_KEY')         # Priority 3

# Logic: Prefer the Hardcoded key first, then the Environment key
SERVER_SIDE_KEY = HARDCODED_API_KEY if HARDCODED_API_KEY else ENV_API_KEY


# MODIFIED: Video URLs are now stored in a dictionary for selection (Updated)
VIDEO_OPTIONS = {
                  'default': 'https://greenmeadowcoc.com/wp-content/uploads/2025/09/Trivia-Part-A-Intro-Video.mp4',
                  'j_collage': 'https://greenmeadowcoc.com/wp-content/uploads/2025/08/Jaida_College_reveal.mp4',
                  'elder_statesone': 'https://greenmeadowcoc.com/wp-content/uploads/2025/08/grandp.mp4',
                  'TJ_newcareer': 'https://greenmeadowcoc.com/wp-content/uploads/2025/08/TJ-Rapping_Fan_s_Jersey_Confession.mp4',
                  'special_guest': 'https://greenmeadowcoc.com/wp-content/uploads/2025/08/Ryan-2.mp4',        
                  'none': None
}

# --- Sound Settings and URLs ---
SOUNDS = {
                  'lobby_music': {'url': 'https://greenmeadowcoc.com/wp-content/uploads/2025/09/kahoot_lobby-music-1-amp.mp3', 'volume': 0.35},
                  'title_screen': {'url': 'https://greenmeadowcoc.com/wp-content/uploads/2025/09/kahoot_answer_reveal_gong_drum_amp.mp3', 'volume': 0.6},
                  'countdown_beep': {'url': 'https://greenmeadowcoc.com/wp-content/uploads/2025/09/kahoot_countdown_wet_beep_amp.mp3', 'volume': 0.8},
                  'countdown_begin': {'url': 'https://greenmeadowcoc.com/wp-content/uploads/2025/09/kahoot_audacity_countdown_whoosh_begin.mp3', 'volume': 0.99},
                  'answer_grid_countdown': [
                                    {'url': 'https://greenmeadowcoc.com/wp-content/uploads/2025/09/kahoot_gameplay_countdown_music1a_amp.mp3', 'volume': 0.5},
                                    {'url': 'https://greenmeadowcoc.com/wp-content/uploads/2025/09/kahoot_gameplay_countdown_music_1b_amp.mp3', 'volume': 0.5},
                                    {'url': 'https://greenmeadowcoc.com/wp-content/uploads/2025/09/kahoot_audacity_gameplay_count2a_amp.mp3', 'volume': 0.5},
                                    {'url': 'https://greenmeadowcoc.com/wp-content/uploads/2025/09/kahoot_audacity_gameplay_count2b_amp.mp5', 'volume': 0.5},
                                    {'url': 'https://greenmeadowcoc.com/wp-content/uploads/2025/09/kahoot_gameplay_music_3a_amp.mp3', 'volume': 0.5},
                                    {'url': 'https://greenmeadowcoc.com/wp-content/uploads/2025/09/kahoot_gameplay_music_3b_amp.py', 'volume': 0.5},
                                    {'url': 'https://greenmeadowcoc.com/wp-content/uploads/2025/09/kahoot_gameplay_trunicate_4amp_a.mp3', 'volume': 0.5},
                                    {'url': 'https://greenmeadowcoc.com/wp-content/uploads/2025/09/kahoot_gameplay_trunicate_4b_amp.mp3', 'volume': 0.5}
                  ],
                  'answer_reveal': {'url': 'https://greenmeadowcoc.com/wp-content/uploads/2025/09/kahoot_answer_reveal_gong_drum_amp.mp3', 'volume': 0.7},
                  'score_increment': {'url': 'https://greenmeadowcoc.com/wp-content/uploads/2025/09/kahoot_audacity_score_incrementing_amp.mp3', 'volume': 0.5},
                  '3rd_place_podium': {'url': 'https://greenmeadowcoc.com/wp-content/uploads/2025/09/kahoot_3rdplace_amp.mp3', 'volume': 0.8, 'offset': 0},
                  '2nd_place_podium': {'url': 'https://greenmeadowcoc.com/wp-content/uploads/2025/09/kahoot_2ndplace_amp.mp3', 'volume': 0.8, 'offset': 0},
                  '1st_place_podium': {'url': 'https://greenmeadowcoc.com/wp-content/uploads/2025/09/kahoot_1stplace_amp.mp3', 'volume': 1.0, 'offset': 0}
}


# --- Quiz Questions (Default) ---
# BUG FIX 2: Renamed to DEFAULT_QUESTIONS to distinguish from the active quiz.
DEFAULT_QUESTIONS = [
                  {'question': 'What is the capital of France?', 'options': ['London', 'Berlin', 'Paris', 'Madrid'], 'correct': 2},
                  {'question': 'Which planet is closest to the Sun?', 'options': ['Venus', 'Mercury', 'Earth', 'Mars'], 'correct': 1},
                  {'question': 'What is 15 x 8?', 'options': ['110', '120', '130', '140'], 'correct': 1},
                  {'question': 'Who wrote "Romeo and Juliet"?', 'options': ['Charles Dickens', 'William Shakespeare', 'Jane Austen', 'Mark Twain'], 'correct': 1},
                  {'question': 'What is the largest ocean on Earth?', 'options': ['Atlantic', 'Indian', 'Arctic', 'Pacific'], 'correct': 3}
]
# BUG FIX 2: The currently active questions for the game.
QUESTIONS = list(DEFAULT_QUESTIONS)


# --- Premade Avatars ---
AVATARS = [
                  'https://comicvine.gamespot.com/a/uploads/scale_small/10/100647/5948604-bm.jpg',
                  'https://www.shutterstock.com/shutterstock/photos/696532105/display_1500/stock-vector-super-hero-african-american-man-cartoon-character-isolated-vector-illustration-696532105.jpg',
                  'https://encrypted-tbn1.gstatic.com/images?q=tbn:ANd9GcTG2Uay5f2MITCQSaIGV3tnYdCZ1G0Vew-3KMiQL9HQYRiSLWlppzuflcgAXQmH',
                  'https://encrypted-tbn0.gstatic.com/images?q=tbn:ANd9GcSNQtsZjYpfkHviMkSIIvs55Kk5F1vGFHcgxq-wcfFb96fdv_sGMh01i3_tdAdb',
                  'https://www.shutterstock.com/image-vector/black-woman-smiling-portrait-vector-600nw-2281497689.jpg',
                  'https://t3.ftcdn.net/jpg/15/29/72/28/360_F_1529722893_s0kkomFmGncTZSSB9lIY9gUgcuZpnBq1.jpg',
                  'https://www.shutterstock.com/image-vector/african-american-girl-avatar-round-260nw-2655824345.jpg',
                  'https://as2.ftcdn.net/jpg/05/26/98/83/1000_F_526988395_fH0Tgjzb7ElkLy8b8orPNpKlxNv2uMnC.jpg'
]

# List of themes and icons to be chosen randomly
# New Color Schemes
COLOR_SCHEMES = {
                  'default': {
                                    'background_color': 'linear-gradient(135deg, #667eea 0%, #764ba2 100%)',
                                    'accent_color': '#e74c3c',
                                    'ip_color': '#ffcc00',
                                    'icon': '🎯',
                                    'option_colors': ['#e74c3c', '#3498db', '#f1c40f', '#2ecc71']
                  },
                  'electric': {
                                    'background_color': 'linear-gradient(135deg, #1A237E 0%, #303F9F 100%)', # Dark Blue
                                    'accent_color': '#FFEB3B', # Amber
                                    'ip_color': '#FFEB3B',
                                    'icon': '⚡️',
                                    'option_colors': ['#e74c3c', '#3498db', '#f1c40f', '#2ecc71'] # Default colors
                  },
                  'modernist': {
                                    'background_color': 'linear-gradient(135deg, #00C6FF 0%, #0072FF 100%)', # Sky Blue
                                    'accent_color': '#E0F2F7', # Light Cyan
                                    'ip_color': '#E0F2F7',
                                    'icon': '✨',
                                    'option_colors': ['#e74c3c', '#3498db', '#f1c40f', '#2ecc71'] # Default colors
                  },
                  'retro_wave': {
                                    'background_color': 'linear-gradient(135deg, #673AB7 0%, #311B92 100%)', # Deep Purple
                                    'accent_color': '#F44336', # Red
                                    'ip_color': '#F44336',
                                    'icon': '👾',
                                    'option_colors': ['#e74c3c', '#3498db', '#f1c40f', '#2ecc71'] # Default colors
                  },
                  'forest_green': {
                                    'body_bg': 'linear-gradient(135deg, #4CAF50 0%, #388E3C 100%)',
                                    'container_bg': 'rgba(0, 0, 0, 0.2)',
                                    'text_color': 'white',
                                    'title_text_color': 'white',
                                    'button_style': 'forest',
                                    'player_list_bg': 'rgba(0,0,0,0.3)'
                  },
                  'ocean_blue': {
                                    'body_bg': 'linear-gradient(135deg, #2196F3 0%, #1976D2 100%)',
                                    'container_bg': 'rgba(255, 255, 255, 0.1)',
                                    'text_color': 'white',
                                    'title_text_color': 'white',
                                    'button_style': 'ocean',
                                    'player_list_bg': 'rgba(0,0,0,0.2)'
                  }
}


# NEW: Order of themes to cycle through
THEME_CYCLE = ['default', 'electric', 'modernist', 'retro_wave']

# MODIFIED: Simplifed this function to avoid blocking system calls on Eventlet servers (like Render)
def get_local_ip():
    """A helper function to find the local IP for the welcome message."""
    try:
        # Use simple non-blocking socket call for local hostname
        ip = socket.gethostbyname(socket.gethostname())
    except:
        ip = "localhost"
    return ip

def reset_game_state():
                  """Resets the game to its initial state and chooses a new random theme."""
                  # BUG FIX 2: Added QUESTIONS and DEFAULT_QUESTIONS to globals to ensure reset works.
                  global game_state, players, game_control, previous_scores, previous_rankings, QUESTIONS, DEFAULT_QUESTIONS
                  players.clear()
                  previous_scores.clear()
                  previous_rankings.clear()
                  game_control.update({'skip_to_answer': False, 'skip_question': False, 'show_podium': False, 'first_question_started': False})

                  # BUG FIX 2: Reset the active questions back to the default set.
                  QUESTIONS = list(DEFAULT_QUESTIONS)

                  # NEW: Start with the default theme
                  random_theme_key = 'default'
                  random_theme_data = COLOR_SCHEMES[random_theme_key]

                  game_state.update({
                                    'game_started': False,
                                    'game_finished': False,
                                    'question_active': False,
                                    'showing_answer': False,
                                    'showing_scoreboard': False,
                                    'countdown_active': False,
                                    'title_screen_active': False,
                                    'video_playing': False,
                                    'selected_video': 'default', # NEW: Add a state for the chosen video
                                    'preview_active': False,
                                    'showing_podium': False,
                                    'current_question_index': -1,
                                    'question_total': len(QUESTIONS), # BUG FIX 2: Ensure total is correct after reset.
                                    'question_duration': 20,
                                    'title_duration': 5,
                                    'preview_duration': 5,
                                    'theme_key': random_theme_key, # Store the key
                                    'theme_data': random_theme_data, # Store the full data
                                    'quiz_is_custom': False # BUG FIX 2: Add flag to indicate if quiz is custom made.
                  })


def calculate_scores():
                  """Calculates and sorts player scores with avatar URLs."""
                  rankings = [{'name': name, 'score': data['score'], 'avatar': data['avatar']} for name, data in players.items()]
                  rankings.sort(key=lambda x: x['score'], reverse=True)
                  return rankings

def get_full_game_data():
                  """Prepares a dictionary with all current game data to be sent to clients."""
                  data = {
                                    'players': players,
                                    'game_state': game_state,
                                    'rankings': calculate_scores(),
                                    'host_active': host_sid is not None,
                                    'previous_scores': previous_scores,
                                    'previous_rankings': previous_rankings
                  }
                  if 0 <= game_state.get('current_question_index', -1) < game_state['question_total']:
                                    question = QUESTIONS[game_state['current_question_index']]
                                    data['question'] = question
                                    if game_state['showing_answer']:
                                                      answer_counts = [0, 0, 0, 0]
                                                      for p in players.values():
                                                                        if p['current_answer'] in range(4):
                                                                                          answer_counts[p['current_answer']] += 1
                                                      data['answer_counts'] = answer_counts
                  return data

def title_screen_loop():
                  """Handles the title screen sequence before the countdown."""
                  print("Starting title screen...")
                  game_state['title_screen_active'] = True
                  socketio.emit('update_state', get_full_game_data())
                  socketio.emit('play_host_sound', SOUNDS['title_screen'])
                  socketio.sleep(game_state.get('title_duration', 5))
                  game_state['title_screen_active'] = False

                  # NEW: Check if a video should be played based on the host's selection
                  selected_video_key = game_state.get('selected_video', 'default')
                  if selected_video_key != 'none' and VIDEO_OPTIONS.get(selected_video_key):
                                    socketio.start_background_task(video_sequence)
                  else:
                                    # If no video is selected, go straight to the countdown
                                    print("No video selected. Starting countdown directly.")
                                    socketio.start_background_task(countdown_loop)

def video_sequence():
                  """Plays the selected video after the title screen."""
                  selected_video_key = game_state.get('selected_video', 'default')
                  video_url_to_play = VIDEO_OPTIONS.get(selected_video_key)

                  # This is a safeguard; the logic in title_screen_loop should prevent this call if no video is to be played.
                  if not video_url_to_play:
                                    print("Video sequence started, but no valid video URL found. Skipping to countdown.")
                                    socketio.start_background_task(countdown_loop)
                                    return

                  print(f"Starting video sequence for: {selected_video_key}")
                  game_state['video_playing'] = True
                  socketio.emit('update_state', get_full_game_data())
                  socketio.emit('play_video', {'url': video_url_to_play})         # Use the dynamically chosen URL

                  # Wait for the client to signal that the video is over
                  while game_state['video_playing']:
                                    socketio.sleep(0.5)

                  print("Video finished. Starting countdown.")
                  socketio.start_background_task(countdown_loop)


def countdown_loop():
                  """Handles the 3-2-1 countdown sequence before the first question."""
                  game_state['countdown_active'] = True
                  for count in range(3, 0, -1):
                                    game_state['countdown_value'] = count
                                    socketio.emit('update_state', get_full_game_data())
                                    socketio.emit('play_host_sound', SOUNDS['countdown_beep'])
                                    socketio.sleep(1.2)
                  game_state['countdown_value'] = "BEGIN!"
                  socketio.emit('update_state', get_full_game_data())
                  socketio.emit('play_host_sound', SOUNDS['countdown_begin'])
                  socketio.sleep(1.2)
                  game_state['countdown_active'] = False
                  game_state['game_started'] = True
                  socketio.start_background_task(game_loop)

def simulate_first_question_click():
                  """Simulates the host clicking the 'Start Question' button after a brief delay."""
                  print("Starting simulation: Waiting for preview screen...")
                  while not game_state.get('preview_active') or game_state.get('current_question_index') != 0:
                                    time.sleep(0.1)

                  print("Preview screen detected. Waiting 0.5 seconds to simulate click...")
                  time.sleep(0.5)

                  game_control['start_question'] = True
                  print("Simulation complete: 'Start Question' button clicked.")

def game_loop():
                  """Controls the entire flow of the quiz."""
                  print("Game loop started")
                  for i in range(len(QUESTIONS)):
                                    game_control.update({'skip_to_answer': False, 'skip_question': False, 'start_question': False})
                                    game_state['current_question_index'] = i

                                    # 1. Question Preview Phase
                                    game_state.update({
                                                      'preview_active': True,
                                                      'question_active': False,
                                                      'showing_answer': False,
                                                      'showing_scoreboard': False,
                                                      'showing_podium': False
                                    })
                                    socketio.emit('update_state', get_full_game_data())

                                    if i == 0:
                                                      print("Waiting for host to manually start the first question...")
                                                      while not game_control['start_question']:
                                                                        socketio.sleep(0.1)
                                                      game_control['first_question_started'] = True
                                    else:
                                                      start_time = datetime.now()
                                                      while datetime.now() < start_time + timedelta(seconds=game_state.get('preview_duration', 5)):
                                                                        game_state['time_remaining'] = max(0, (start_time + timedelta(seconds=game_state.get('preview_duration', 5)) - datetime.now()).seconds)
                                                                        socketio.emit('update_state', get_full_game_data())
                                                                        socketio.sleep(0.1)

                                    # 2. Question Active Phase
                                    game_state.update({
                                                      'preview_active': False,
                                                      'question_active': True,
                                    })
                                    for p in players.values():
                                                      p['current_answer'] = -1
                                                      p['time_submitted'] = None

                                    random_countdown_sound = random.choice(SOUNDS['answer_grid_countdown'])
                                    socketio.emit('play_host_sound', random_countdown_sound)

                                    question_start_time = datetime.now()
                                    while datetime.now() < question_start_time + timedelta(seconds=game_state.get('question_duration', 20)):
                                                      time_since_start = (datetime.now() - question_start_time).total_seconds()
                                                      game_state['time_remaining'] = max(0, game_state['question_duration'] - int(time_since_start))
                                                      socketio.emit('update_state', get_full_game_data())
                                                      if game_control['skip_question'] or game_control['skip_to_answer']: break
                                                      socketio.sleep(1)
                                                      if players and all(p['current_answer'] != -1 for p in players.values()): break

                                    if game_control['skip_question']: continue

                                    # --- Process and apply scores before revealing the answer ---
                                    # Capture old scores and rankings before they are updated
                                    global previous_scores, previous_rankings
                                    previous_scores = {name: data['score'] for name, data in players.items()}
                                    previous_rankings = calculate_scores()

                                    current_question = QUESTIONS[game_state['current_question_index']]
                                    for player_name, player_data in players.items():
                                                      if player_data['current_answer'] == current_question['correct'] and player_data['time_submitted']:
                                                                        time_taken = (player_data['time_submitted'] - question_start_time).total_seconds()
                                                                        time_bonus = max(0, game_state['question_duration'] - time_taken) * 2
                                                                        points_earned = 100 + time_bonus

                                                                        player_data['score'] += int(points_earned)
                                                                        player_data['earned_points'] = int(points_earned)
                                                      else:
                                                                        player_data['earned_points'] = 0

                                    game_state.update({'time_remaining': 0, 'question_active': False, 'showing_answer': True})
                                    socketio.emit('update_state', get_full_game_data())

                                    socketio.emit('play_host_sound', SOUNDS['answer_reveal'])

                                    # Add a pause after answer reveal
                                    socketio.sleep(5)

                                    # 3. Transition to Scoreboard or End Game
                                    if i < len(QUESTIONS) - 1:
                                                      game_state.update({'showing_answer': False, 'showing_scoreboard': True})
                                                      socketio.emit('update_state', get_full_game_data())
                                                      # Changed the single 12-second sleep to three 4-second sleeps
                                                      for _ in range(4):
                                                                        socketio.sleep(3)
                                                                        # Check for a "next" button click in between sleeps
                                                                        if game_control['skip_question']:
                                                                                          game_control['skip_question'] = False
                                                                                          break
                                    else:
                                                      game_state.update({'game_finished': True, 'showing_scoreboard': False, 'showing_podium': True})

                  # Final state update after the loop completes
                  socketio.emit('update_state', get_full_game_data())
                  print("Game loop finished.")

                  if host_sid:
                                    # NEW: Explicitly send podium sounds to the host only
                                    # This prevents players from receiving these events.
                                    socketio.emit('play_host_sound', SOUNDS['3rd_place_podium'])
                                    socketio.sleep(4)
                                    socketio.emit('play_host_sound', SOUNDS['2nd_place_podium'])
                                    socketio.sleep(4)
                                    socketio.emit('play_host_sound', SOUNDS['1st_place_podium'])

                  # New code to simulate the 'Continue' click
                  print("Waiting 7 seconds after 1st place reveal to simulate 'Continue' click.")
                  socketio.sleep(7)

                  # BUG FIX 1: If the game was reset (e.g., current_question_index is -1) while this task was sleeping,
                  # do not proceed to change the state back to a finished one. 'i' holds the last question index from the loop.
                  if game_state.get('current_question_index') != i:
                                    print("Game was reset during podium sequence. Aborting final state update.")
                                    return

                  print("Simulating 'Continue' click.")
                  game_state.update({'showing_podium': False, 'game_finished': True})
                  socketio.emit('update_state', get_full_game_data())


# --- WebSocket Event Handlers ---
@socketio.on('connect')
def on_connect():
                  print(f"Client connected: {request.sid}")
                  emit('update_state', get_full_game_data())

@socketio.on('get_state')
def on_get_state():
                  """Sends the current game state to the requesting client only."""
                  emit('update_state', get_full_game_data())

@socketio.on('video_ended')
def on_video_ended():
                  """Called by the host client when the video finishes playing."""
                  print("Received 'video_ended' signal from host.")
                  game_state['video_playing'] = False
                  socketio.emit('update_state', get_full_game_data())

@socketio.on('start_question')
def on_start_question():
                  if request.sid == host_sid and game_state.get('preview_active'):
                                    print("Host manually started the question.")
                                    game_control['start_question'] = True

@socketio.on('disconnect')
def on_disconnect():
                  global host_sid
                  print(f"Client disconnected: {request.sid}")
                  if request.sid == host_sid:
                                    print("Host has disconnected. Resetting game.")
                                    socketio.emit('stop_music', to=host_sid)
                                    host_sid = None
                                    reset_game_state()
                                    socketio.emit('update_state', get_full_game_data())
                                    socketio.emit('force_reload', {'message': 'The host has been disconnected. The game has been reset.'})

@socketio.on('login_as_host')
def on_login_as_host(data):
                  global host_sid
                  # MODIFIED: Use the environment variable set on Render (or a fixed value)
                  expected_password = os.getenv('HOST_PASSWORD', '1234')
                  password = data.get('password')
                  
                  if password == expected_password:
                                    if host_sid is not None and host_sid != request.sid:
                                                      emit('host_login_failed', {'message': 'A host is already active.'})
                                    else:
                                                      host_sid = request.sid
                                                      session['is_host'] = True
                                                      emit('host_login_success')
                                                      socketio.emit('update_state', get_full_game_data())
                                                      emit('play_lobby_music', SOUNDS['lobby_music'], to=host_sid)
                  else:
                                    emit('host_login_failed', {'message': 'Incorrect password.'})

def host_only(f):
                  def wrapper(*args, **kwargs):
                                    if request.sid != host_sid: return
                                    return f(*args, **kwargs)
                  return wrapper

@socketio.on('start_game')
@host_only
def on_start_game():
                  if players and not game_state.get('game_started'):
                                    socketio.emit('stop_music', to=host_sid)
                                    # Call the new title screen loop
                                    socketio.start_background_task(title_screen_loop)
                                    # The first question is now auto-started after the video/countdown, no need for this.
                                    socketio.start_background_task(simulate_first_question_click)

@socketio.on('reset_game')
@host_only
def on_reset_game():
                  print("Host is resetting the game.")
                  reset_game_state()
                  socketio.emit('update_state', get_full_game_data())
                  socketio.emit('stop_celebration')
                  if host_sid:
                                    socketio.emit('play_lobby_music', SOUNDS['lobby_music'], to=host_sid)

@socketio.on('leave_host_role')
@host_only
def on_leave_host_role():
                  global host_sid
                  print(f"Host {host_sid} is stepping down.")
                  socketio.emit('stop_music', to=host_sid)
                  host_sid = None
                  reset_game_state()
                  socketio.emit('force_reload', {'message': 'The host has left the session.'})

@socketio.on('skip_to_answer')
@host_only
def on_skip_to_answer():
                  if game_state.get('question_active'): game_control['skip_to_answer'] = True

@socketio.on('skip_question')
@host_only
def on_skip_question():
                  game_control['skip_question'] = True

@socketio.on('skip_podium')
@host_only
def on_skip_podium():
                  game_state.update({'showing_podium': False, 'game_finished': True})
                  socketio.emit('update_state', get_full_game_data())

@socketio.on('remove_player')
@host_only
def on_remove_player(data):
                  player_name = data.get('player_name')
                  if not game_state.get('game_started') and player_name in players:
                                    del players[player_name]
                                    socketio.emit('update_state', get_full_game_data())

@socketio.on('submit_answer')
def on_submit_answer(data):
                  player_name = session.get('player_name')
                  answer_index = data.get('answer_index')
                  if player_name in players and game_state.get('question_active'):
                                    player = players[player_name]
                                    if player['current_answer'] != -1: return
                                    player['current_answer'] = answer_index
                                    player['time_submitted'] = datetime.now()

                                    # New: Emit a confirmation to just the submitting client
                                    emit('answer_received', {'message': 'Answer received!'})

                                    # New: Emit a state update only to the host
                                    # This will update the host screen to show the number of players who have answered.
                                    # This is the line that was causing the error, now fixed by the custom encoder.
                                    # The key is to only send it to the host, as other players don't need this intermediate update.
                                    socketio.emit('update_state', get_full_game_data(), to=host_sid)

@socketio.on('rejoin_as_player')
def on_rejoin_as_player(data):
                  player_name = data.get('player_name')
                  if player_name and player_name in players and not game_state.get('game_started'):
                                    session['player_name'] = player_name
                                    emit('join_success')
                                    socketio.emit('update_state', get_full_game_data())

# NEW: Add a theme toggle event handler for the host
@socketio.on('toggle_theme')
@host_only
def on_toggle_theme():
                  current_theme_key = game_state.get('theme_key', 'default')
                  try:
                                    current_index = THEME_CYCLE.index(current_theme_key)
                                    next_index = (current_index + 1) % len(THEME_CYCLE)
                                    new_theme_key = THEME_CYCLE[next_index]
                  except ValueError:
                                    new_theme_key = 'default' # Fallback if theme is not in cycle

                  new_theme_data = COLOR_SCHEMES[new_theme_key]
                  game_state['theme_key'] = new_theme_key
                  game_state['theme_data'] = new_theme_data
                  print(f"Host toggled theme to {new_theme_key}")
                  socketio.emit('update_state', get_full_game_data())

# NEW: Add a theme reset event handler for the host
@socketio.on('reset_theme_to_default')
@host_only
def on_reset_theme_to_default():
                  new_theme_key = 'default'
                  new_theme_data = COLOR_SCHEMES[new_theme_key]
                  game_state['theme_key'] = new_theme_key
                  game_state['theme_data'] = new_theme_data
                  print(f"Host reset theme to {new_theme_key}")
                  socketio.emit('update_state', get_full_game_data())

# MODIFIED: Handler for Gemini API quiz generation now handles flexible key logic
@socketio.on('generate_quiz_with_ai')
def on_generate_quiz(data):
                  """Generates a new quiz using the Gemini API."""
                         
                  # Priority 1 (Highest): User input from browser popup (allows override)
                  user_provided_key = data.get('apiKey')
                         
                  # Priority: User Input > Hardcoded Key > Environment Key
                  final_api_key = user_provided_key if user_provided_key else SERVER_SIDE_KEY
                         
                  topic = data.get('topic')
                  num_questions = data.get('num_questions', 5)
                  difficulty = data.get('difficulty', 'Easy')
                  video_choice = data.get('video_choice', 'default') # Get video choice from frontend

                  # Check if we successfully found a key and have a topic
                  if not final_api_key or not topic:
                                    emit('quiz_generation_failed', {'message': 'Topic is required. (API Key not found in system or input).'})
                                    return

                  try:
                                    genai.configure(api_key=final_api_key)
                                    model = genai.GenerativeModel('gemini-2.5-flash')

                                    prompt = f"""
                                    Create a trivia quiz about "{topic}".
                                    The difficulty level should be {difficulty}.
                                    Generate exactly {num_questions} questions.
                                    Provide the output as a raw JSON array of objects, with no additional text or markdown formatting.
                                    Each object in the array must have the following structure:
                                    - "question": A string containing the question.
                                    - "options": An array of exactly 4 strings representing the possible answers.
                                    - "correct": An integer (from 0 to 3) representing the index of the correct answer in the "options" array.

                                    Example format:
                                    [
                                             {{
                                                      "question": "What is the capital of France?",
                                                      "options": ["London", "Berlin", "Paris", "Madrid"],
                                                      "correct": 2
                                             }}
                                    ]
                                    """

                                    print("Generating quiz with Gemini...")
                                    response = model.generate_content(prompt)
                                           
                                    # Clean the response to ensure it's valid JSON
                                    cleaned_text = response.text.strip()
                                    if cleaned_text.startswith('```json'):
                                                      cleaned_text = cleaned_text[7:]
                                    if cleaned_text.endswith('```'):
                                                      cleaned_text = cleaned_text[:-3]
                                           
                                    new_questions = json.loads(cleaned_text)

                                    # Basic validation of the received structure
                                    if not isinstance(new_questions, list) or len(new_questions) == 0:
                                                      raise ValueError("Generated data is not a list or is empty.")
                                    for q in new_questions:
                                                      if not all(k in q for k in ['question', 'options', 'correct']):
                                                                        raise ValueError("A question is missing a required key.")
                                                      if not isinstance(q['options'], list) or len(q['options']) != 4:
                                                                        raise ValueError("Options must be a list of 4 strings.")

                                    # BUG FIX 2: Reset the game first, then apply the new questions and state.
                                    # This clears players and state but keeps the host session active.
                                    reset_game_state()
                                           
                                    global QUESTIONS
                                    QUESTIONS = new_questions
                                    game_state['question_total'] = len(QUESTIONS)
                                    game_state['quiz_is_custom'] = True
                                    game_state['selected_video'] = video_choice # Store the chosen video option
                                           
                                    print(f"Successfully generated {len(QUESTIONS)} new questions.")
                                    emit('quiz_generation_success')
                                    socketio.emit('update_state', get_full_game_data())

                  except Exception as e:
                                    print(f"Error generating quiz: {e}")
                                    emit('quiz_generation_failed', {'message': f'Failed to generate quiz. Error: {str(e)}'})


# --- Traditional HTTP Routes ---
@app.route('/')
def index():
                  return render_template_string(HTML_TEMPLATE)

@app.route('/join', methods=['POST'])
def join_game():
                  player_name = request.form.get('player_name', '').strip().strip('\'"')

                  # MODIFICATION #1: Add server-side validation for username length
                  if not (4 <= len(player_name) <= 32):
                                    return "Username must be between 4 and 32 characters.", 400

                  try:
                                    avatar_index = int(request.form.get('avatar_index', 0))
                                    avatar_url = AVATARS[avatar_index]
                  except (ValueError, IndexError):
                                    return "Invalid avatar index", 400

                  if player_name and player_name not in players and not game_state.get('game_started'):
                                    players[player_name] = {'score': 0, 'current_answer': -1, 'avatar': avatar_url, 'time_submitted': None}
                                    session['player_name'] = player_name
                                    socketio.emit('update_state', get_full_game_data())

                  response = redirect(url_for('index'))
                  response.set_cookie('player_name_cache', player_name, max_age=3600)
                  return response

reset_game_state()

# ★★★★★ HTML Template ★★★★★
HTML_TEMPLATE = r"""
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Quiz Game</title>
<link rel="icon" href="data:,">
<script src="https://cdn.socket.io/4.7.5/socket.io.min.js"></script>
<link href="https://fonts.googleapis.com/css2?family=Poppins:wght@400;700&display=swap" rel="stylesheet">
<style>
body {
                  font-family: 'Poppins', sans-serif;
                  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                  margin: 0; color: white;
                  display: flex; flex-direction: column; justify-content: flex-start;
                  align-items: center; min-height: 100vh; text-align: center;
                  /* REMOVED padding-top to allow flexbox to center vertically */
                  box-sizing: border-box;
                  transition: background 1s ease-in-out;
                  position: relative;
                  overflow-y: auto;
}
#game-container {
                  max-width: 900px;
                  width: 95%;
                  padding: 0;
                  box-sizing: border-box;
                  background: none;
                  box-shadow: none;
}
/* UPDATED: Styles for the dedicated host lobby container */
#host-lobby-container {
                  width: 100%;
                  display: flex;
                  flex-direction: column;
                  align-items: center;
                  padding: 20px;
                  box-sizing: border-box;
                  /* ADDED: These lines make it fill space and center content */
                  flex-grow: 1;
                  justify-content: center;
}
#main-title { text-shadow: 2px 2px 4px rgba(0,0,0,0.3); }
.button {
                  padding: 0.8rem 1.5rem; margin: 0.5rem; border-radius: 50px;
                  border: none; cursor: pointer; font-size: 1.1rem; color: white;
                  transition: all 0.2s ease; box-shadow: 0 4px 15px rgba(0,0,0,0.2);
}
.button:hover { transform: translateY(-2px); box-shadow: 0 6px 20px rgba(0,0,0,0.3); }
.option { width: 100%; box-sizing: border-box; padding: 1.2rem; margin: 0.4rem 0; font-size: 1.2rem; }
.option:disabled { cursor: not-allowed; opacity: 0.7; }

/* Button Answer Colors (will be overridden by JS) */
.option-a { background: linear-gradient(45deg, #e74c3c, #c0392b); }
.option-b { background: linear-gradient(45deg, #3498db, #2980b9); }
.option-c { background: linear-gradient(45deg, #f1c40f, #f39c12); }
.option-d { background: linear-gradient(45deg, #2ecc71, #27ae60); }

.correct { border: 4px solid white; box-shadow: 0 0 20px rgba(46, 204, 113, 0.7); }
.incorrect { opacity: 0.5; }
#player-list, #scoreboard { max-width: 500px; margin: 1rem auto; background: rgba(0,0,0,0.2); padding: 1rem; border-radius: 8px;}
.player-row, .score-row { display: flex; justify-content: space-between; align-items: center; padding: 0.75rem; border-bottom: 1px solid rgba(255,255,255,0.2); font-size: 1.1rem; }
.delete-btn { cursor: pointer; transition: transform 0.2s; padding: 5px; }
.delete-btn:hover { transform: scale(1.2); }
input[type="text"], input[type="password"] { padding: 10px; border-radius: 5px; border: none; font-size: 1rem; margin: 0.5rem;}

/* HOST UI STYLES */
#game-header {
                  position: fixed; top: 0; left: 0; width: 100%; padding: 10px 30px;
                  display: none; align-items: center; justify-content: space-between;
                  background: rgba(0,0,0,0.1); box-sizing: border-box; z-index: 900;
}
#quiz-title { font-size: 1.5rem; font-weight: bold; flex: 1; text-align: left; }
#game-status-title { font-size: 1.8rem; font-weight: bold; text-align: center; flex: 2; }
.header-spacer { flex: 1; }
#bottom-right-controls {
                  position: fixed; bottom: 15px; right: 15px; display: none;
                  z-index: 1000; align-items: center; gap: 10px;
}
.control-btn {
                  background: rgba(0,0,0,0.4); color: white; border: none; border-radius: 50%;
                  width: 45px; height: 45px; font-size: 1.5rem; cursor: pointer;
                  display: flex; justify-content: center; align-items: center;
                  transition: background-color 0.2s;
}
.control-btn:hover { background: rgba(0,0,0,0.6); }
#player-count {
                  background: rgba(0,0,0,0.5); padding: 8px 15px; border-radius: 20px;
                  font-size: 1.2rem; display: flex; align-items: center; gap: 8px;
}

/* NEW "NEXT" BUTTON STYLES FOR HEADER */
.header-right-controls {
                  display: flex;
                  align-items: center;
                  gap: 10px;
}
/* Now a dynamic color */
.scheme-button {
                  padding: 8px 15px;
                  background-color: #a29bfe; /* Placeholder, will be replaced */
                  color: white;
                  border: none;
                  border-radius: 5px;
                  font-size: 1rem;
                  cursor: pointer;
                  transition: background-color 0.2s ease;
}
.scheme-button:hover {
                  opacity: 0.8;
}

/* KAHOOT LAYOUT CSS */
#kahoot-layout-wrapper {
                  position: fixed; top: 0; left: 0; width: 100%; height: 100%;
                  color: white; display: none; /* Controlled by JS */
}
#kahoot-question-bar {
                  position: absolute; top: 15%; left: 50%; transform: translateX(-50%);
                  background: white; color: black; padding: 20px 40px; border-radius: 5px;
                  width: 80%; max-width: 1000px; font-size: 1.8rem; text-align: center; box-shadow: 0 4px 15px rgba(0,0,0,0.2);
                  z-index: 200; /* Ensure question is on top */
                  transition: top 0.5s ease-in-out, transform 0.5s ease-in-out;
}
#kahoot-question-bar.preview-mode {
                  top: 50%;
                  transform: translate(-50%, -50%);
                  /* Dynamically set font size via JS */
                  /* font-size: 2.5rem; */
}
#kahoot-timer {
                  position: absolute; left: 30px; top: 30%;
                  width: 100px; height: 100px; border-radius: 50%;
                  background: rgba(0,0,0,0.5); font-size: 3rem; font-weight: bold;
                  display: flex; justify-content: center; align-items: center;
}
#kahoot-answer-count {
                  position: absolute; right: 30px; top: 30%;
                  width: 100px; height: 100px; border-radius: 50%;
                  background: rgba(0,0,0,0.5); font-size: 1rem;
                  display: flex; flex-direction: column; justify-content: center; align-items: center;
}
#kahoot-answer-count span { font-size: 2.5rem; font-weight: bold; }
#kahoot-answer-grid {
                  position: absolute; bottom: 0; left: 0; width: 100%;
                  padding: 5vh 1vw; box-sizing: border-box; display: grid;
                  grid-template-columns: 1fr 1fr; grid-template-rows: 1fr 1fr;
                  gap: 1.5vmin; height: 50%;
                  z-index: 100;
}
#kahoot-answer-stats {
                  position: absolute;
                  top: 40%;
                  left: 50%;
                  transform: translate(-50%, -50%);
                  display: flex;
                  justify-content: center;
                  align-items: flex-end;
                  gap: 30px;
                  width: 80%;
                  max-width: 600px;
                  height: 30%;
}

/* KAHOOT STYLE PLAYER UI */
.player-kahoot-option {
                  border: none;
                  color: white;
                  font-size: 4rem;
                  display: flex;
                  justify-content: center;
                  align-items: center;
                  cursor: pointer;
                  transition: transform 0.2s, opacity 0.2s;
}
.player-kahoot-option:hover:not(:disabled) { transform: scale(1.05); }
.player-kahoot-option:active { transform: scale(0.95); }
.player-kahoot-option:disabled { cursor: not-allowed; opacity: 0.7; }
.player-kahoot-option .shape { font-size: 5rem; }
/* Player option colors now dynamic */

/* ENHANCED KAHOOT HOST ANSWER GRID */
.kahoot-option {
                  border: none; border-radius: 5px; color: white;
                  /* font-size: 1.5rem; */ /* Dynamically set via JS */
                  font-weight: bold; display: flex;
                  align-items: center; padding: 20px; position: relative;
                  transition: all 0.3s ease;
                  text-align: left;
}
.kahoot-option .shape { font-size: 2.5rem; margin-right: 20px; }
.kahoot-option .answer-text { flex: 1; text-align: left; }

.answer-indicator {
                  position: absolute;
                  right: 15px;
                  font-size: 2rem;
                  color: white;
}

/* Bar chart visualization */
.answer-bar-container {
                  width: 80px;
                  height: 250px;
                  display: flex;
                  flex-direction: column;
                  align-items: center;
                  justify-content: flex-end;
                  gap: 5px; /* Added gap to prevent overlap */
}
.answer-bar-count {
                  font-size: 2rem;
                  font-weight: bold;
                  color: white;
                  margin-bottom: 5px;
}
.answer-bar {
                  width: 60px;
                  border-radius: 5px 5px 0 0;
                  transition: height 0.8s ease;
                  border: 2px solid rgba(255,255,255,0.3);
}
.answer-bar-shape {
                  font-size: 2rem;
                  font-weight: bold;
                  color: white;
                  margin-top: 5px;
}

/* Answer reveal animations and styles */
.kahoot-option.reveal-correct {
                  border: 5px solid white;
                  box-shadow: 0 0 30px rgba(255, 255, 255, 0.8);
                  animation: correctPulse 0.5s ease-in-out;
}
.kahoot-option.reveal-incorrect {
                  opacity: 0.4;
                  filter: grayscale(50%);
}

@keyframes correctPulse {
                  0%, 100% { transform: scale(1); }
                  50% { transform: scale(1.02); }
                  100% { transform: scale(1); }
}

/* HOST VIEW COLORS - Now Dynamic */


.kahoot-answer-reveal {
                  position: fixed; top: 50%; left: 50%; transform: translate(-50%, -50%);
                  background: rgba(0,0,0,0.5); padding: 30px; border-radius: 10px;
                  font-size: 2rem; font-weight: bold;
}
#settings-modal {
                  position: fixed; top: 50%; left: 50%; transform: translate(-50%, -50%);
                  background: rgba(30, 30, 30, 0.95); padding: 30px; border-radius: 15px; z-index: 2000;
                  border: 2px solid white;
                  max-width: 400px;
                  text-align: left;
                  box-shadow: 0 8px 25px rgba(0, 0, 0, 0.5);
                  /*display: none;*/
}
.settings-control {
                  margin-bottom: 15px;
                  text-align: left;
}
.settings-control label {
                  display: block;
                  margin-bottom: 5px;
                  font-size: 1rem;
}
.settings-control input[type="range"] {
                  width: 100%;
}

/* NEW STYLES FOR COUNTDOWN AND PREVIEW */
#countdown-container {
                  position: fixed; top: 0; left: 0; width: 100%; height: 100%;
                  display: flex; flex-direction: column; justify-content: center; align-items: center;
                  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                  z-index: 100;
}
/*
        * Note: The animation needs to be re-triggered for each new number.
        * We do this in JS by removing and re-adding the class.
        */
#countdown-number {
                  font-size: 15vw;
                  font-weight: bold;
                  text-shadow: 4px 4px 8px rgba(0,0,0,0.5);
}
.animated-number {
                  animation: fade-in-out 1s ease-in-out forwards;
}
#countdown-shape {
                  font-size: 25vw;
                  line-height: 1;
                  transform-origin: center;
                  animation: diamond-to-square 1s ease-in-out infinite;
}
@keyframes fade-in-out {
                  0% { opacity: 0; transform: scale(0.5); }
                  50% { opacity: 1; transform: scale(1); }
                  100% { opacity: 0; transform: scale(1.5); }
}
@keyframes diamond-to-square {
                  0% { transform: rotate(0deg); }
                  50% { transform: rotate(45deg); }
                  100% { transform: rotate(0deg); }
}
.progress-bar-container {
                  width: 80%;
                  height: 20px;
                  background-color: #ddd;
                  border-radius: 10px;
                  margin-top: 20px;
                  overflow: hidden;
}
.progress-bar {
                  height: 100%;
                  background-color: #4CAF50;
                  width: 0%;
                  transition: width 0.1s linear;
}

/* --- NEW USER BARS CSS --- */
.user-top-bar {
                  position: fixed;
                  top: 0;
                  width: 100%;
                  background-color: rgba(211, 211, 211, 0.7); /* Light Grey */
                  color: black;
                  display: flex; /* Use flexbox for centering */
                  align-items: center; /* Vertically centers all items */
                  justify-content: center; /* Horizontally centers the whole group */
                  padding: 10px;
                  box-sizing: border-box;
                  font-weight: bold;
                  z-index: 99;
}

.user-bottom-bar {
                  position: fixed;
                  bottom: 0;
                  width: 100%;
                  background-color: rgba(211, 211, 211, 0.7); /* Light Grey */
                  color: black;
                  display: flex;
                  justify-content: space-between; /* Space out the left and right sides */
                  align-items: center; /* Vertically center all items */
                  padding: 10px;
                  box-sizing: border-box;
                  font-weight: bold;
                  z-index: 99;
}

.top-left {
                  position: absolute;
                  left: 15px;
                  top: 50%;
                  transform: translateY(-50%);
                  display: flex;
                  align-items: center;
                  gap: 10px;
}

.top-center {
                  display: flex;
                  align-items: center;
                  gap: 8px;
                  white-space: nowrap;
                  overflow: hidden;
                  text-overflow: ellipsis;
}

.top-right {
                  position: absolute;
                  right: 15px;
                  top: 50%;
                  transform: translateY(-50%);
                  display: flex;
                  align-items: center;
                  gap: 10px;
}

.bottom-left, .bottom-right {
                  display: flex;
                  align-items: center;
                  gap: 10px;
                  padding: 0 15px;
}

/* FIX FOR AVATAR SIZING */
.quiz-logo, .player-avatar, .avatar-option, .player-avatar-small {
                  width: 30px;
                  height: 30px;
                  border-radius: 50%;
                  object-fit: cover;
}
.player-avatar-small {
                  width: 25px;
                  height: 25px;
                  margin-right: 10px;
}

#avatar-grid {
                  display: grid;
                  grid-template-columns: repeat(4, 1fr);
                  gap: 10px;
                  margin-bottom: 20px;
}
.avatar-option {
                  width: 100%;
                  height: auto;
                  border-radius: 50%;
                  cursor: pointer;
                  border: 3px solid transparent;
                  transition: border-color 0.2s ease;
}
.avatar-option.selected {
                  border-color: #f1c40f;
}

/* Adjust main content padding to not be hidden by bars */
.ingame-body-padding {
                  padding-top: 70px;
                  padding-bottom: 70px;
}

/* Responsive fixes for host header and player bars */
@media (max-width: 768px) {
                  #game-header {
                                    flex-direction: column;
                                    gap: 5px;
                                    padding: 10px;
                  }
                  #quiz-title, #game-status-title, .header-spacer {
                                    text-align: center;
                                    flex: none;
                                    width: 100%;
                  }
                  .user-top-bar {
                                    flex-wrap: wrap;
                                    justify-content: center;
                  }
                  .user-top-bar .top-left, .user-top-bar .top-right {
                                    padding: 0;
                                    width: 50%;
                                    justify-content: center;
                  }
                  .user-top-bar .top-center {
                                    width: 100%;
                                    justify-content: center;
                                    order: -1;
                                    font-size: 1.2rem;
                  }
}

/* --- NEW PODIUM/LEADERBOARD STYLES (from Code B) --- */
#podium-view, #final-scoreboard-view {
                  width: 100%;
                  height: 100vh;
                  position: fixed;
                  top: 0;
                  left: 0;
                  display: none;
                  flex-direction: column;
                  justify-content: center;
                  align-items: center;
                  z-index: 100;
}
#podium-view {
                  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
}
.dark-overlay {
                  position: fixed; top: 0; left: 0; width: 100vw; height: 100vh;
                  background: rgba(0, 0, 0, 0.6); z-index: 500;
                  opacity: 0; transition: opacity 1s ease-in-out; pointer-events: none;
}
.dark-overlay.active { opacity: 1; }

.celebration-header { margin-bottom: 40px; }
.celebration-title { font-size: 4rem; font-weight: bold; text-shadow: 4px 4px 8px rgba(0,0,0,0.5); margin-bottom: 10px; animation: pulse 2s ease-in-out infinite; }
.celebration-subtitle { font-size: 1.5rem; opacity: 0.9; }

.podium-wrapper { display: flex; align-items: flex-end; justify-content: center; gap: 20px; margin-bottom: 40px; }
.podium-place { display: flex; flex-direction: column; align-items: center; position: relative; opacity: 0; transform: translateY(50px); }
.podium-player { background: rgba(255,255,255,0.1); padding: 20px; border-radius: 15px; margin-bottom: 10px; backdrop-filter: blur(10px); border: 2px solid rgba(255,255,255,0.2); min-width: 150px; position: relative; }
.podium-avatar { width: 80px; height: 80px; border-radius: 50%; object-fit: cover; margin-bottom: 15px; border: 4px solid white; box-shadow: 0 0 20px rgba(0,0,0,0.3); }
.podium-name { font-size: 1.2rem; font-weight: bold; margin-bottom: 5px; }
.podium-score { font-size: 1.5rem; font-weight: bold; color: #f1c40f; }
.podium-base { border-radius: 10px 10px 0 0; display: flex; align-items: center; justify-content: center; font-size: 2rem; font-weight: bold; color: white; text-shadow: 2px 2px 4px rgba(0,0,0,0.5); }
.first-place .podium-base { background: linear-gradient(135deg, #f1c40f, #f39c12); height: 120px; width: 150px; }
.second-place .podium-base { background: linear-gradient(135deg, #95a5a6, #bdc3c7); height: 90px; width: 150px; }
.third-place .podium-base { background: linear-gradient(135deg, #cd7f32, #d4ac0d); height: 60px; width: 150px; }
.podium-place.show { animation: podium-reveal 0.8s ease-out forwards; }

.winner-crown { position: absolute; top: -30px; left: 50%; transform: translateX(-50%); font-size: 3rem; animation: crown-float 3s ease-in-out infinite; }
.anticipation-text {
                  position: fixed; top: 50%; left: 50%; transform: translate(-50%, -50%);
                  font-size: 2rem; font-weight: bold; text-shadow: 2px 2px 4px rgba(0,0,0,0.7);
                  opacity: 0; animation: anticipation-fade 1.5s ease-in-out forwards;
                  z-index: 900;
}

.confetti {
                  position: fixed; width: 10px; height: 10px; background: #f1c40f;
                  animation: confetti-fall 3s linear infinite; z-index: 100;
}

@keyframes pulse { 0%, 100% { transform: scale(1); } 50% { transform: scale(1.05); } }
@keyframes podium-reveal { 0% { opacity: 0; transform: translateY(50px) scale(0.8); } 100% { opacity: 1; transform: translateY(0) scale(1); } }
@keyframes crown-float { 0%, 100% { transform: translateX(-50%) translateY(0px); } 50% { transform: translateX(-50%) translateY(-10px); } }
@keyframes anticipation-fade { 0% { opacity: 0; transform: translate(-50%, -50%) scale(0.8); } 50% { opacity: 1; transform: translate(-50%, -50%) scale(1); } 100% { opacity: 0; transform: translate(-50%, -50%) scale(1.2); } }
@keyframes confetti-fall { 0% { transform: translateY(-100vh) rotate(0deg); opacity: 1; } 100% { transform: translateY(100vh) rotate(720deg); opacity: 0; } }

/* Final Scoreboard Styles */
#final-scoreboard-view {
                  background: rgba(0,0,0,0.5);
                  display: none;
                  z-index: 200;
                  padding: 20px;
}

/* NEW: Scoreboard Styles */
#in-game-scoreboard-view {
                  width: 100%;
                  height: 100vh;
                  position: fixed;
                  top: 0;
                  left: 0;
                  display: none; /* Controlled by JS */
                  flex-direction: column;
                  justify-content: center;
                  align-items: center;
                  z-index: 100;
                  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                  padding: 20px;
                  box-sizing: border-box;
}

.final-scoreboard-title {
                  font-size: 3rem;
                  font-weight: bold;
                  margin-bottom: 20px;
                  display: flex;
                  align-items: center;
                  justify-content: center;
                  gap: 10px;
}

.final-scoreboard-list {
                  background: rgba(255,255,255,0.1);
                  border-radius: 10px;
                  width: 90%;
                  max-width: 600px;
                  padding: 20px;
}

.final-score-row {
                  display: flex;
                  justify-content: space-between;
                  align-items: center;
                  padding: 15px;
                  border-bottom: 1px solid rgba(255,255,255,0.2);
                  font-size: 1.2rem;
                  background-color: transparent;
                  transition: background-color 0.5s ease-in-out, color 0.5s ease-in-out;
}

.final-score-row:last-child {
                  border-bottom: none;
}

/* NEW LEADER ROW STYLE */
.final-score-row.leader-row {
                  background-color: #f0f0f0; /* Lighter background */
                  color: #333; /* Darker text for readability */
}

.final-score-row.leader-row .final-rank-name span,
.final-score-row.leader-row .final-rank-name img,
.final-score-row.leader-row .score-value {
                  filter: none; /* Remove any potential filters that would lighten the text/image */
                  color: #333; /* Ensure text is dark */
}


.final-rank-name {
                  display: flex;
                  align-items: center;
                  gap: 10px;
                  font-weight: bold;
}

/* Scoreboard Theme-specific Styles */
.modernist-theme .final-scoreboard-title { color: #e0f2f7; }
.modernist-theme .final-scoreboard-list { background: rgba(255, 255, 255, 0.05); }
.modernist-theme .final-score-row { border-bottom-color: rgba(255, 255, 255, 0.2); }
.modernist-theme .player-avatar-small { border: 2px solid #00a8cc; }

.electric-theme .final-scoreboard-title { color: #FFEB3B; }
.electric-theme .final-scoreboard-list { background: rgba(0, 0, 0, 0.4); border: 2px solid #FFEB3B; }
.electric-theme .final-score-row { border-bottom-color: rgba(255, 255, 255, 0.2); }
.electric-theme .player-avatar-small { border: 2px solid #FFEB3B; }

.golden_age-theme .final-scoreboard-title { color: #f1c40f; }
.golden_age-theme .final-scoreboard-list { background: rgba(0, 0, 0, 0.2); border: 1px solid #f1c40f; }
.golden_age-theme .final-score-row { border-bottom-color: rgba(241, 196, 15, 0.5); }
.golden_age-theme .final-score-row, .golden_age-theme .final-scoreboard-title { color: #f1c40f; }
.golden_age-theme .player-avatar-small { border: 2px solid #f1c40f; }

.retro_wave-theme .final-scoreboard-title { color: #F44336; }
.retro_wave-theme .final-scoreboard-list { background: rgba(0, 0, 0, 0.5); border: 2px solid #F44336; }
.retro_wave-theme .final-score-row { border-bottom-color: rgba(244, 67, 54, 0.5); }
.retro_wave-theme .player-avatar-small { border: 2px solid #F44336; }


/* NEW: Score animation CSS */
.score-value {
                  font-size: 1.2rem;
                  font-weight: bold;
                  display: inline-block;
                  transition: all 0.5s ease-in-out;
}
.rank-up-arrow {
                  color: #2ecc71;
                  font-weight: bold;
                  margin-left: 5px;
}


/* NEW: Title Screen Styles */
#start-title-screen {
                  position: fixed; top: 0; left: 0; width: 100%; height: 100%;
                  /* REPLACE white background */
                  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                  display: none;
                  flex-direction: column; justify-content: center; align-items: center;
                  text-align: center; color: #333;
                  z-index: 110;
}

.white-text-box {
                  background: white;
                  padding: 20px 40px;
                  border-radius: 10px;
                  box-shadow: 0 4px 15px rgba(0,0,0,0.2);
                  color: #333;
}
#start-title-screen h1 {
                  font-size: 5rem;
                  font-weight: bold;
                  text-shadow: 2px 2px 4px rgba(0,0,0,0.1);
}

#start-title-screen p {
                  font-size: 2rem;
                  margin-top: 10px;
                  opacity: 0.8;
}
/* NEW STYLES FOR HOST 'START QUESTION' BUTTON */
.preview-controls {
                  margin-top: 20px;
}
/* Now a dynamic color */
.start-question-btn {
                  padding: 15px 30px;
                  font-size: 1.5rem;
                  background-color: #2ecc71;
                  border: none;
                  border-radius: 10px;
                  color: white;
                  cursor: pointer;
                  transition: background-color 0.2s ease;
}
.start-question-btn:hover {
                  opacity: 0.8;
}

/* NEW VIDEO SCREEN STYLES */
#video-player-container {
                  position: fixed; top: 0; left: 0; width: 100%; height: 100%;
                  background: black; display: none; flex-direction: column;
                  justify-content: center; align-items: center; z-index: 120;
}
#video-player {
                  width: 90%; max-width: 1200px;
}
#video-message {
                  margin-top: 20px; font-size: 1.5rem;
}
#video-skip-btn {
                  position: absolute; bottom: 20px; right: 20px;
                  background: rgba(0,0,0,0.5); color: white; border: 2px solid white;
                  padding: 10px 20px; font-size: 1rem; cursor: pointer;
                  border-radius: 5px;
}

/* --- STYLES FROM CODE A FOR LOBBY --- */
.main-content {
                  width: 100%;
                  display: flex;
                  flex-direction: column;
                  align-items: center;
                  padding: 20px;
                  box-sizing: border-box;
                  max-width: 800px;
                  text-align: center;
}
h1 {
                  text-shadow: 2px 2px 4px rgba(0, 0, 0, 0.3);
                  margin-bottom: 20px;
}
.quiz-title-area {
                  margin-bottom: 20px;
                  text-shadow: 2px 2px 4px rgba(0, 0, 0, 0.3);
}
.quiz-title {
                  font-size: 2.5rem;
                  font-weight: bold;
                  margin: 0;
}
.ip-area {
                  display: flex;
                  flex-direction: column;
                  align-items: center;
                  margin-bottom: 20px;
}
.ip-text {
                  font-size: 2rem;
                  font-weight: bold;
                  margin: 0;
                  color: white;
}
.ip-box {
                  font-size: 4rem;
                  font-weight: bold;
                  text-shadow: 2px 2px 5px rgba(0, 0, 0, 0.5);
                  color: #ffcc00;
                  transition: color 0.5s ease-in-out;
}
.waiting-text {
                  font-size: 1.5rem;
                  margin: 0 0 20px 0;
                  color: white;
}
.player-list-grid {
                  display: flex;
                  justify-content: center;
                  flex-wrap: wrap;
                  gap: 15px;
                  width: 100%;
}
.player-card {
                  background: rgba(255, 255, 255, 0.1);
                  padding: 10px;
                  border-radius: 15px;
                  display: flex;
                  flex-direction: column;
                  align-items: center;
                  text-align: center;
                  box-shadow: 0 4px 10px rgba(0, 0, 0, 0.2);
                  position: relative;
                  overflow: hidden;
                  flex: 1 1 150px;
                  color: white;
                  transition: transform 0.3s ease, box-shadow 0.3s ease, opacity 0.5s ease-out;
}
.player-card:hover {
                  transform: translateY(-5px);
                  box-shadow: 0 8px 15px rgba(0, 0, 0, 0.3);
}
.player-card img {
                  width: 80px;
                  height: 80px;
                  border-radius: 50%;
                  border: 3px solid #ffcc00;
                  object-fit: cover;
                  margin-bottom: 10px;
                  transition: border-color 0.5s ease-in-out;
}
.player-card span {
                  font-size: 1.2rem;
                  font-weight: bold;
                  position: relative;
}
.player-card span::after {
                  content: '';
                  position: absolute;
                  top: 50%;
                  left: 0;
                  width: 100%;
                  height: 6px; /* MODIFICATION: Changed from 2px to 3px */
                  background-color: white;
                  transform: scaleX(0);
                  transform-origin: left;
                  transition: transform 0.3s ease;
}
/* MODIFICATION: This rule replaces the old hover effect */
.player-card span.strikethrough::after {
                  transform: scaleX(1);
}
.player-card.removed {
                  opacity: 0;
                  transform: scale(0);
                  height: 0;
                  padding: 0;
                  margin: 0;
                  overflow: hidden;
}
.remove-icon {
                  position: absolute;
                  top: 5px;
                  right: 5px;
                  background: #e74c3c;
                  color: white;
                  width: 20px;
                  height: 20px;
                  border-radius: 50%;
                  display: flex;
                  justify-content: center;
                  align-items: center;
                  font-size: 1rem;
                  cursor: pointer;
                  line-height: 1;
                  transition: transform 0.2s, opacity 0.2s;
                  opacity: 0;
                  font-weight: bold;
}
.player-card:hover .remove-icon {
                  opacity: 1;
}
.remove-icon:hover {
                  transform: scale(1.2);
}
.main-buttons {
                  position: absolute;
                  top: 20px;
                  right: 20px;
                  display: flex;
                  gap: 10px;
                  z-index: 20;
}
.styled-button {
                  padding: 8px 15px;
                  font-size: 1rem;
                  font-weight: bold;
                  color: white;
                  border: none;
                  border-radius: 20px;
                  cursor: pointer;
                  box-shadow: 0 2px 5px rgba(0, 0, 0, 0.2);
                  transition: transform 0.2s, box-shadow 0.2s, background-color 0.3s ease;
                  display: flex;
                  align-items: center;
                  gap: 5px;
                  white-space: nowrap;
                  background-color: #e74c3c;
                  transition: background-color 0.5s ease-in-out;
}
.styled-button:hover {
                  transform: translateY(-1px);
                  box-shadow: 0 4px 8px rgba(0, 0, 0, 0.3);
                  filter: brightness(1.1);
}
.styled-button img {
                  height: 18px;
                  width: 18px;
                  filter: brightness(1.2);
}

/* MODIFICATION #2: Custom Confirmation Popup CSS */
#custom-confirm {
                  display: none; /* Hidden by default */
                  position: fixed;
                  top: 20px;
                  left: 50%;
                  transform: translateX(-50%);
                  background-color: #2c3e50;
                  color: white;
                  padding: 20px;
                  border-radius: 10px;
                  box-shadow: 0 5px 15px rgba(0,0,0,0.5);
                  z-index: 9999;
                  border: 2px solid #3498db;
                  text-align: center;
}
#custom-confirm p {
                  margin: 0 0 15px 0;
                  font-size: 1.1rem;
}
#custom-confirm .confirm-buttons {
                  display: flex;
                  justify-content: center;
                  gap: 15px;
}
.confirm-btn {
                  padding: 8px 20px;
                  border: none;
                  border-radius: 5px;
                  cursor: pointer;
                  font-weight: bold;
                  color: white;
                  transition: transform 0.2s;
}
.confirm-btn:hover {
                  transform: scale(1.05);
}
.confirm-yes {
                  background-color: #e74c3c;
}
.confirm-no {
                  background-color: #95a5a6;
}

/* --- NEW: doubleSwell Animation --- */
@keyframes doubleSwell {
                  0%{ transform: scale(0.1); opacity: 0; }
                  25%{ transform: scale(1.6); opacity: 1; } /* Very large first swell */
                  50%{ transform: scale(0.7); }/* A more noticeable dip */
                  75%{ transform: scale(1.3); }/* A large second swell */
                  100% { transform: scale(1); opacity: 1; }/* Final settle */
}

.player-card.doubleSwell {
                  /* The 'forwards' keyword is crucial to make the card stay visible */
                  animation: doubleSwell 0.8s cubic-bezier(0.175, 0.885, 0.32, 1.275) forwards;
}

/* --- NEW STYLES FOR AI QUIZ MODAL --- */
#create-quiz-modal {
                  display: none;
                  position: fixed;
                  top: 0; left: 0;
                  width: 100%; height: 100%;
                  background: rgba(0,0,0,0.8); /* Darker overlay */
                  justify-content: center;
                  align-items: center;
                  z-index: 3000;
}

#create-quiz-modal .modal-content {
                  background: #1e1e2f; /* Dark purple-blue from image */
                  padding: 30px;
                  border-radius: 15px;
                  width: 90%;
                  max-width: 500px;
                  color: white;
                  text-align: left;
                  box-shadow: 0 8px 30px rgba(0,0,0,0.5);
                  border: 1px solid rgba(255, 255, 255, 0.1);
}

#create-quiz-modal h2 {
                  text-align: center;
                  margin-top: 0;
                  margin-bottom: 25px;
                  font-weight: bold;
}

#create-quiz-modal .form-group {
                  margin-bottom: 15px;
}

#create-quiz-modal label {
                  display: block;
                  margin-bottom: 8px;
                  font-size: 0.9rem;
                  color: #b0b0d0; /* Lighter text color for labels */
}

#create-quiz-modal input[type="text"],
#create-quiz-modal input[type="password"],
#create-quiz-modal input[type="number"],
#create-quiz-modal select {
                  width: 100%;
                  padding: 12px;
                  border-radius: 8px;
                  border: 1px solid #4a4a6a;
                  background: #2c2c44;
                  color: white;
                  box-sizing: border-box;
                  font-size: 1rem;
}

/* NEW: Styles for the password toggle */
.password-wrapper {
                  position: relative;
                  display: flex;
                  align-items: center;
}
#gemini-api-key {
                  padding-right: 40px; /* Make space for the icon */
}
.password-toggle-icon {
                  position: absolute;
                  right: 15px;
                  cursor: pointer;
                  user-select: none;
                  font-style: normal;
}


#create-quiz-modal small a {
                  color: #4d94ff; /* Bright blue for link */
                  text-decoration: none;
}
#create-quiz-modal small a:hover {
                  text-decoration: underline;
}

#create-quiz-modal .modal-buttons {
                  display: flex;
                  justify-content: flex-end;
                  gap: 15px;
                  margin-top: 30px;
}

#create-quiz-modal #generate-quiz-btn {
                  background: linear-gradient(45deg, #2ecc71, #28b485);
                  color: white;
                  font-weight: bold;
                  border: none;
                  padding: 12px 25px;
                  border-radius: 8px;
                  cursor: pointer;
                  transition: all 0.2s ease;
}
#create-quiz-modal #generate-quiz-btn:hover:not(:disabled) {
                  transform: translateY(-2px);
                  box-shadow: 0 4px 15px rgba(46, 204, 113, 0.3);
}
#create-quiz-modal #generate-quiz-btn:disabled {
                  opacity: 0.6;
                  cursor: not-allowed;
}

#create-quiz-modal #cancel-quiz-btn {
                  background: linear-gradient(45deg, #e74c3c, #c0392b);
                  color: white;
                  font-weight: bold;
                  border: none;
                  padding: 12px 25px;
                  border-radius: 8px;
                  cursor: pointer;
                  transition: all 0.2s ease;
}
#create-quiz-modal #cancel-quiz-btn:hover {
                  transform: translateY(-2px);
                  box-shadow: 0 4px 15px rgba(231, 76, 60, 0.3);
}
</style>
</head>
<body>
<div class="user-top-bar">
<div class="top-left">
<span id="player-question-number"></span>
</div>
<div class="top-center">
<span class="quiz-title-text">Quiz</span>
</div>
<div class="top-right"></div>
</div>
<div class="user-bottom-bar">
<div class="bottom-left">
<img id="player-avatar-display" src="" alt="Player Avatar" class="player-avatar">
<span id="player-name-display"></span>
</div>
<div class="bottom-right">
<span id="player-score-display"></span>
</div>
</div>
<h1 id="main-title">Quiz Game</h1>

<div id="game-header">
<div id="quiz-title">Quiz Game</div>
<div id="game-status-title"></div>
<div class="header-right-controls" id="host-header-controls"></div>
</div>

<div id="host-lobby-container" style="display:none;"></div>

<div id="game-container">
<p>Connecting to server...</p>
</div>

<div id="kahoot-layout-wrapper">
<div id="kahoot-timer"></div>
<div id="kahoot-answer-count"><span></span>Answers</div>
<div id="kahoot-question-bar"></div>

<div id="kahoot-answer-stats"></div>
<div id="kahoot-answer-grid"></div>
</div>

<div id="countdown-container" style="display:none;">
<span id="countdown-shape">♦</span>
<span id="countdown-number"></span>
</div>

<div id="start-title-screen">
<div class="white-text-box">
<h1>General Trivia Quiz</h1>
</div>
</div>

<div id="video-player-container">
<video id="video-player" controls preload="auto"></video>
<h2 id="video-message"></h2>
</div>

<div id="podium-view">
<div class="dark-overlay" id="dark-overlay"></div>
<div class="celebration-header">
<div class="celebration-title">🎉 Game Complete! 🎉</div>
<div class="celebration-subtitle">Congratulations to all players!</div>
</div>
<div class="podium-wrapper" id="podium-wrapper"></div>
</div>

<div id="final-scoreboard-view">
<div class="final-scoreboard-title"><span id="final-scoreboard-icon"></span> Final Leaderboard <span id="final-scoreboard-icon-right"></span></div>
<div class="final-scoreboard-list" id="final-scoreboard-list"></div>
</div>

<div id="in-game-scoreboard-view">
<div class="final-scoreboard-title"><span id="in-game-scoreboard-icon"></span> Leaderboard</div>
<div class="final-scoreboard-list" id="in-game-scoreboard-list"></div>
</div>

<div id="bottom-right-controls">
<div id="player-count">👤 0</div>
<button id="sound-btn" class="control-btn" onclick="toggleSound()" title="Toggle Sound">🔊</button>
<button id="fullscreen-btn" class="control-btn" onclick="toggleFullscreen()" title="Toggle Fullscreen">⛶</button>
<button id="settings-btn" class="control-btn" onclick="openSettings()" title="Settings">⚙️</button>
</div>

<div id="custom-confirm">
<p id="custom-confirm-msg"></p>
<div class="confirm-buttons">
<button id="confirm-yes-btn" class="confirm-btn confirm-yes">Yes</button>
<button id="confirm-no-btn" class="confirm-btn confirm-no">No</button>
</div>
</div>
<div id="create-quiz-modal" style="display:none;">
                  <div class="modal-content">
                                    <h2>Create a New Quiz with AI ✨</h2>
                                           
                                    <div class="form-group">
                                                      <label for="gemini-api-key">Gemini API Key: (Optional, uses saved key if blank)</label>
                                                      <div class="password-wrapper">
                                                                        <input type="password" id="gemini-api-key" placeholder="Enter your key here to override saved key">
                                                                        <i class="password-toggle-icon" onclick="toggleApiKeyVisibility()">👁️</i>
                                                      </div>
                                                      <small>Find your key at <a href="https://aistudio.google.com/" target="_blank">Google AI Studio</a>.</small>
                                    </div>

                                    <div class="form-group">
                                                      <label for="quiz-topic">Topic:</label>
                                                      <input type="text" id="quiz-topic" placeholder="e.g., World Geography, 90s Movies">
                                    </div>

                                    <div class="form-group">
                                                      <label for="num-questions">Number of Questions:</label>
                                                      <input type="number" id="num-questions" value="5" min="1" max="20">
                                    </div>

                                    <div class="form-group">
                                                      <label for="difficulty">Difficulty:</label>
                                                      <select id="difficulty">
                                                                        <option value="Easy">Easy</option>
                                                                        <option value="Medium">Medium</option>
                                                                        <option value="Hard">Hard</option>
                                                      </select>
                                    </div>
                                           
                                    <div class="form-group">
                                                      <label for="model">Model:</label>
                                                      <select id="model">
                                                                        <option value="gemini-2.5-flash-latest">Gemini 2.5 Flash (default)</option>
                                                      </select>
                                    </div>

                                    <div class="form-group">
                                                      <label for="video-choice">Intro Video:</label>
                                                      <select id="video-choice">
                                                                        <option value="default">Trivia Part A Intro Video</option>
                                                                        <option value="j_collage">J Collage</option>
                                                                        <option value="elder_statesone">Elder Statesone</option>
                                                                        <option value="TJ_newcareer">TJ New Career</option>
                                                                        <option value="special_guest">Special Guest Appearance</option>
                                                                        <option value="none">No Video</option>
                                                      </select>
                                    </div>

                                    <div class="modal-buttons">
                                                      <button id="cancel-quiz-btn">Cancel</button>
                                                      <button id="generate-quiz-btn">Generate Quiz</button>
                                    </div>
                  </div>
</div>

<audio id="host-audio-player"></audio>
<audio id="player-audio-player"></audio>
<audio id="lobby-music-player" loop></audio>
<script>
const socket = io();
let myRole = 'spectator';
let myName = getCookie('player_name_cache');
let soundState = 0; // 0=high, 1=low, 2=mute
let kahootLayout = localStorage.getItem('kahootLayout') === 'true';
let selectedAvatarIndex = 0;
let lastKnownPlayerNames = new Set(); // To track players for animation

// NEW: Helper function to get the correct volume based on the client's soundState
function getAdjustedVolume(defaultVolume) {
                  if (soundState === 2) { // Mute state
                                    return 0.0;
                  }
                  if (soundState === 1) { // Low volume state
                                    return defaultVolume * 0.5;
                  }
                  // High volume state (default)
                  return defaultVolume;
}

const AVATARS = [
                  'https://comicvine.gamespot.com/a/uploads/scale_small/10/100647/5948604-bm.jpg',
                  'https://www.shutterstock.com/shutterstock/photos/696532105/display_1500/stock-vector-super-hero-african-american-man-cartoon-character-isolated-vector-illustration-696532105.jpg',
                  'https://encrypted-tbn1.gstatic.com/images?q=tbn:ANd9GcTG2Uay5f2MITCQSaIGV3tnYdCZ1G0Vew-3KMiQL9HQYRiSLWlppzuflcgAXQmH',
                  'https://encrypted-tbn0.gstatic.com/images?q=tbn:ANd9GcSNQtsZjYpfkHviMkSIIvs55Kk5F1vGFHcgxq-wcfFb96fdv_sGMh01i3_tdAdb',
                  'https://www.shutterstock.com/image-vector/black-woman-smiling-portrait-vector-600nw-2281497689.jpg',
                  'https://t3.ftcdn.net/jpg/15/29/72/28/360_F_1529722893_s0kkomFmGncTZSSB9lIY9gUgcuZpnBq1.jpg',
                  'https://www.shutterstock.com/image-vector/african-american-girl-avatar-round-260nw-2655824345.jpg',
                  'https://as2.ftcdn.net/jpg/05/26/98/83/1000_F_526988395_fH0Tgjzb7ElkLy8b8orPNpKlxNv2uMnC.jpg'
];

if (myName) { myRole = 'player'; }

// NEW: Font size settings object and default values
const defaultFontSettings = {
                  previewFontSize: 2.5, // rem
                  questionFontSize: 1.8,// rem
                  optionFontSize: 1.5, // rem
                  linked: true
};
const fontSettings = JSON.parse(localStorage.getItem('fontSettings')) || defaultFontSettings;


socket.on('update_state', (data) => {
                  const container = document.getElementById('game-container');
                  const lobbyContainer = document.getElementById('host-lobby-container');
                  const header = document.getElementById('game-header');
                  const statusTitle = document.getElementById('game-status-title');
                  const controlsContainer = document.getElementById('bottom-right-controls');
                  const playerCountEl = document.getElementById('player-count');
                  const mainTitle = document.getElementById('main-title');
                  const kahootWrapper = document.getElementById('kahoot-layout-wrapper');
                  const countdownWrapper = document.getElementById('countdown-container');
                  const podiumView = document.getElementById('podium-view');
                  const finalScoreboardView = document.getElementById('final-scoreboard-view');
                  const inGameScoreboardView = document.getElementById('in-game-scoreboard-view');
                  const startTitleScreen = document.getElementById('start-title-screen');
                  const videoPlayerContainer = document.getElementById('video-player-container');
                  const body = document.body;

                  const isRecognizedPlayer = myName && data.players && myName in data.players;

                  const userTopBar = document.querySelector('.user-top-bar');
                  const userBottomBar = document.querySelector('.user-bottom-bar');
                  userTopBar.style.display = 'none';
                  userBottomBar.style.display = 'none';
                  body.classList.remove('ingame-body-padding');


                  // Manage visibility of main components
                  container.style.display = 'none';
                  lobbyContainer.style.display = 'none';
                  kahootWrapper.style.display = 'none';
                  countdownWrapper.style.display = 'none';
                  mainTitle.style.display = 'none';
                  header.style.display = 'none';
                  controlsContainer.style.display = 'none';
                  podiumView.style.display = 'none';
                  finalScoreboardView.style.display = 'none';
                  inGameScoreboardView.style.display = 'none';
                  startTitleScreen.style.display = 'none';
                  videoPlayerContainer.style.display = 'none';

                  // Apply the background color from the selected theme
                  const podium = document.getElementById('podium-view');
                  const countdown = document.getElementById('countdown-container');
                  const startScreen = document.getElementById('start-title-screen');
                  const inGameScoreboard = document.getElementById('in-game-scoreboard-view');

                  if (data.game_state.theme_data) {
                                    const gradient = data.game_state.theme_data.background_color;
                                    body.style.background = gradient;
                                    podium.style.background = gradient;
                                    countdown.style.background = gradient;
                                    startScreen.style.background = gradient;
                                    inGameScoreboard.style.background = gradient;
                  }

                  if (data.game_state.title_screen_active) {
                                    renderStartTitleScreen(data);
                  } else if (data.game_state.video_playing) {
                                    renderVideoScreen(data);
                  } else if (data.game_state.countdown_active) {
                                    renderCountdownView(data);
                  } else if (myRole === 'host') {
                                    controlsContainer.style.display = 'flex';
                                    playerCountEl.innerHTML = `👤 ${Object.keys(data.players).length}`;
                                    playerCountEl.style.display = data.game_state.game_started ? 'none' : 'flex';
                                    header.style.display = data.game_state.game_started ? 'flex' : 'none';

                                    if (!data.game_state.game_started && !data.game_state.game_finished) {
                                                      lobbyContainer.style.display = 'flex'; // CHANGED TO FLEX
                                                      // Call the new dynamic update function
                                                      updateHostLobbyPlayers(data);
                                                      applyThemeColors(data);
                                    } else if (data.game_state.showing_podium) {
                                                      podiumView.style.display = 'flex';
                                                      renderPodiumView(data);
                                    } else if (data.game_state.game_finished) {
                                                      finalScoreboardView.style.display = 'flex';
                                                      renderFinalScoreboard(data);
                                    } else if (data.game_state.showing_scoreboard) {
                                                      renderHostScoreboard(data);
                                    } else {
                                                      const useKahootView = kahootLayout && (data.game_state.question_active || data.game_state.showing_answer || data.game_state.preview_active);

                                                      if (useKahootView) {
                                                                        kahootWrapper.style.display = 'block';
                                                                        if (data.game_state.preview_active) {
                                                                                          renderQuestionPreview(data);
                                                                        } else {
                                                                                          renderKahootHostView(data);
                                                                        }
                                                      } else {
                                                                        container.style.display = 'block';
                                                                        renderStandardHostView(data);
                                                      }
                                    }

                                    if (data.game_state.game_started) {
                                                      statusTitle.innerHTML = getHostStatusTitle(data.game_state);
                                    }
                  } else if (myRole === 'player' && isRecognizedPlayer) {
                                    body.classList.add('ingame-body-padding');
                                    mainTitle.style.display = 'none';
                                    container.style.display = 'block';
                                    container.innerHTML = renderPlayerView(data);
                  } else {
                                    body.classList.add('ingame-body-padding');
                                    mainTitle.style.display = 'block';
                                    container.style.display = 'block';
                                    container.innerHTML = renderUnassignedView(data);
                                    selectAvatar(selectedAvatarIndex);
                  }
});

// <<< ROBUST RECONNECTION LOGIC START >>>
// This event handler fires THE MOMENT the connection is lost.
socket.on('disconnect', (reason) => {
                  console.log(`Disconnected from server. Reason: ${reason}`);
                  // You could display a small, non-intrusive banner here to inform the user.
});

// This event handler fires AFTER the Socket.IO library has successfully reconnected on its own.
socket.on('connect', () => {
                  console.log('Successfully reconnected to the server!');
                  // CRUCIAL CHECK: We only want to rejoin if this user was already an active player.
                  // Also, if the host disconnects, they should get the state back too.
                  if (myRole === 'host' || (myName && myRole === 'player')) {
                                    console.log(`I was a player or host previously. Telling the server I am back.`);
                                    socket.emit('get_state'); // Request the state to re-render everything
                  }
});
// <<< ROBUST RECONNECTION LOGIC END >>>

socket.on('host_login_success', () => { myRole = 'host'; myName = null; document.cookie = 'player_name_cache=; Max-Age=-99999999; path=/;'; socket.emit('update_state', {}); });
socket.on('host_login_failed', (data) => alert('Host Login Failed: ' + data.message));
socket.on('join_success', () => { myRole = 'player'; });
socket.on('force_reload', (data) => { alert(data.message); window.location.reload(); });

socket.on('play_video', (data) => {
                  if (myRole === 'host') {
                                    const videoPlayer = document.getElementById('video-player');
                                    videoPlayer.src = data.url;
                                    videoPlayer.load();
                                    videoPlayer.play().catch(e => console.error("Video playback failed:", e));
                  }
});

const videoPlayer = document.getElementById('video-player');
videoPlayer.onended = () => {
                  if (myRole === 'host') {
                                    socket.emit('video_ended');
                  }
};

// MODIFIED: This handler now respects the client's local mute setting.
socket.on('play_host_sound', (data) => {
                  if (myRole === 'host') {
                                    const audio = document.getElementById('host-audio-player');
                                    audio.src = data.url;
                                    audio.volume = getAdjustedVolume(data.volume); // Use the helper function
                                    audio.loop = data.loop || false;
                                    audio.play().catch(e => console.error("Host sound playback failed:", e));
                  }
});

// MODIFIED: This handler now respects the client's local mute setting.
socket.on('play_player_sound', (data) => {
                  if (myRole === 'player') {
                                    const audio = document.getElementById('player-audio-player');
                                    audio.src = data.url;
                                    audio.volume = getAdjustedVolume(data.volume); // Use the helper function
                                    audio.play().catch(e => console.error("Player sound playback failed:", e));
                  }
});

// MODIFIED: This handler now respects the client's local mute setting.
socket.on('play_lobby_music', (data) => {
                  const lobbyMusicPlayer = document.getElementById('lobby-music-player');
                  lobbyMusicPlayer.volume = getAdjustedVolume(data.volume); // Use the helper function
                  lobbyMusicPlayer.src = data.url;
                  lobbyMusicPlayer.play().catch(e => console.error("Lobby music playback failed:", e));
});


socket.on('stop_music', () => {
                  const lobbyMusicPlayer = document.getElementById('lobby-music-player');
                  const hostAudioPlayer = document.getElementById('host-audio-player');
                  lobbyMusicPlayer.pause();
                  lobbyMusicPlayer.currentTime = 0;
                  hostAudioPlayer.pause();
                  hostAudioPlayer.currentTime = 0;
});

socket.on('stop_celebration', () => {
                  document.querySelectorAll('.confetti').forEach(el => el.remove());
                  const hostAudio = document.getElementById('host-audio-player');
                  hostAudio.pause();
                  hostAudio.currentTime = 0;
});

// NEW: Listeners for quiz generation status
// BUG FIX 2: Simplified this handler as the main render function now handles the button state.
socket.on('quiz_generation_success', () => {
                  alert('New quiz generated successfully!');
                  const generateBtn = document.getElementById('generate-quiz-btn');
                  generateBtn.disabled = false;
                  generateBtn.textContent = 'Generate Quiz';
                  hideCreateQuizPopup();
                  // The subsequent 'update_state' event from the server will correctly
                  // render the "Start Game" button now. No DOM manipulation is needed here.
});

socket.on('quiz_generation_failed', (data) => {
                  alert(`Quiz generation failed: ${data.message}`);
                  const generateBtn = document.getElementById('generate-quiz-btn');
                  generateBtn.disabled = false;
                  generateBtn.textContent = 'Generate Quiz';
});


// --- RENDER FUNCTIONS ---
function getHostStatusTitle(game_state) {
                  if (game_state.showing_podium) return "Final Results";
                  if (game_state.game_finished) return "Final Leaderboard";
                  if (game_state.showing_scoreboard) return "Scoreboard";
                  if (game_state.showing_answer) return "Answer Reveal";
                  if (game_state.question_active) return `Question ${game_state.current_question_index + 1}/${game_state.question_total}`;
                  if (game_state.title_screen_active) return "Trivia Quiz";
                  if (game_state.video_playing) return "Video Playing";
                  if (game_state.countdown_active) return "Get Ready!";
                  if (game_state.preview_active) return `Question ${game_state.current_question_index + 1}/${game_state.question_total} Preview`;
                  return "";
}

function renderUnassignedView(data) {
                  if (myName && !data.game_state.game_started) {
                                    return `<h2>Welcome back, ${myName}!</h2><p>The host hasn't started the game yet.</p><button class="button" style="background-color: #2ecc71;" onclick="rejoinAsPlayer()">👤 Rejoin Lobby</button><button class="button" style="background-color: #95a5a6;" onclick="clearPlayerCookie()">Not you?</button>`;
                  }
                  const playerNameValue = myName ? `value="${myName}"` : '';
                  const avatarOptionsHtml = AVATARS.map((url, index) => `
                                    <img src="${url}" class="avatar-option" data-index="${index}" onclick="selectAvatar(${index})">
                  `).join('');

                  return `
                                    <div style="padding-top: 50px;">
                                                      <h2>Enter Your Player Name</h2>
                                                      <div id="role-selection">
                                                                        <h3 style="margin-bottom: 5px;">Choose your avatar</h3>
                                                                        <div id="avatar-grid">${avatarOptionsHtml}</div>
                                                                        <form action="/join" method="POST" style="display: flex; flex-direction: column; align-items: center;">
                                                                                          <input type="hidden" name="avatar_index" id="selected-avatar-index" value="${selectedAvatarIndex}">
                                                                                          <input type="text" name="player_name" placeholder="Your Name" required minlength="4" maxlength="32" title="Username must be 4-32 characters." ${playerNameValue}>
                                                                                          <button type="submit" class="button" style="background-color: #2ecc71;">Join Game</button>
                                                                        </form>
                                                                        <p style="margin: 1rem 0;">- or -</p>
                                                                        <button class="button" style="background-color: #3498db;" onclick="showHostLogin()">👑 Become the Host</button>
                                                      </div>
                                    </div>
                  `;
}

function renderStartTitleScreen(data) {
                  const startTitleScreen = document.getElementById('start-title-screen');
                  startTitleScreen.style.display = 'flex';
}

function renderVideoScreen(data) {
                  const videoPlayerContainer = document.getElementById('video-player-container');
                  const videoPlayer = document.getElementById('video-player');
                  const videoMessage = document.getElementById('video-message');

                  videoPlayerContainer.style.display = 'flex';

                  if (myRole === 'host') {
                                    videoPlayer.style.display = 'block';
                                    videoMessage.textContent = '';
                                    videoMessage.style.display = 'block';
                                    let skipBtn = document.getElementById('video-skip-btn');
                                    if (!skipBtn) {
                                                      skipBtn = document.createElement('button');
                                                      skipBtn.id = 'video-skip-btn';
                                                      skipBtn.textContent = '⏩';
                                                      skipBtn.onclick = () => {
                                                                        videoPlayer.pause();
                                                                        socket.emit('video_ended');
                                                      };
                                                      videoPlayerContainer.appendChild(skipBtn);
                                    }
                  } else {
                                    videoPlayer.style.display = 'none';
                                    videoMessage.textContent = 'Please look at the host\'s screen.';
                                    videoMessage.style.display = 'block';
                  }
}


function renderCountdownView(data) {
                  const countdownEl = document.getElementById('countdown-number');
                  const newNumber = data.game_state.countdown_value;
                  document.getElementById('countdown-container').style.display = 'flex';

                  countdownEl.classList.remove('animated-number');
                  setTimeout(() => {
                                    countdownEl.textContent = newNumber;
                                    countdownEl.classList.add('animated-number');
                  }, 10);
}

function renderQuestionPreview(data) {
                  const kahootWrapper = document.getElementById('kahoot-layout-wrapper');
                  const questionBar = document.getElementById('kahoot-question-bar');

                  questionBar.innerHTML = data.question.question;
                  questionBar.classList.add('preview-mode');
                  questionBar.style.fontSize = `${fontSettings.previewFontSize}rem`;

                  document.getElementById('kahoot-answer-grid').style.display = 'none';
                  document.getElementById('kahoot-timer').style.display = 'none';
                  document.getElementById('kahoot-answer-count').style.display = 'none';
                  document.getElementById('kahoot-answer-stats').style.display = 'none';

                  const hostHeaderControls = document.getElementById('host-header-controls');
                  hostHeaderControls.innerHTML = '';

                  if (data.game_state.current_question_index === 0) {
                                    const startButton = document.createElement('button');
                                    startButton.className = 'start-question-btn';
                                    startButton.textContent = 'Start Question';
                                    startButton.onclick = () => socket.emit('start_question');
                                    hostHeaderControls.appendChild(startButton);
                  }
}

function renderHostScoreboard(data) {
                  const { rankings, game_state, previous_scores, previous_rankings } = data;
                  const mainContainer = document.getElementById('game-container');
                  const kahootWrapper = document.getElementById('kahoot-layout-wrapper');
                  const inGameScoreboardView = document.getElementById('in-game-scoreboard-view');
                  const scoreboardList = document.getElementById('in-game-scoreboard-list');
                  const headerControls = document.getElementById('host-header-controls');

                  mainContainer.style.display = 'none';
                  kahootWrapper.style.display = 'none';
                  inGameScoreboardView.style.display = 'flex';

                  scoreboardList.innerHTML = '';

                  const scoreboardTitle = inGameScoreboardView.querySelector('.final-scoreboard-title');
                  scoreboardTitle.innerHTML = `${game_state.theme_data.icon} Leaderboard`;

                  inGameScoreboardView.classList.remove('modernist-theme', 'electric-theme', 'golden_age-theme', 'retro_wave-theme');
                  scoreboardTitle.classList.remove('modernist-theme', 'electric-theme', 'golden_age-theme', 'retro_wave-theme');

                  inGameScoreboardView.classList.add(`${game_state.theme_key}-theme`);
                  scoreboardTitle.classList.add(`${game_state.theme_key}-theme`);

                  const finalScoreboardTitle = inGameScoreboardView.querySelector('.final-scoreboard-title');
                  finalScoreboardTitle.style.color = game_state.theme_data.accent_color;

                  const finalScoreboardList = inGameScoreboardView.querySelector('.final-scoreboard-list');
                  finalScoreboardList.style.borderColor = game_state.theme_data.accent_color;

                  const renderedRows = [];
                  let needsAnimation = false;

                  rankings.forEach((player, index) => {
                                    const rank = index + 1;
                                    const prevRankIndex = previous_rankings.findIndex(p => p.name === player.name);
                                    const prevRank = (prevRankIndex !== -1) ? prevRankIndex + 1 : rank;

                                    const rowDiv = document.createElement('div');
                                    rowDiv.className = 'final-score-row';
                                    rowDiv.id = `row-${player.name.replace(/\s/g, '-')}`;

                                    rowDiv.style.borderBottomColor = `rgba(${hexToRgb(game_state.theme_data.accent_color)}, 0.5)`;

                                    const rankSymbol = `#${rank}`;
                                    let rankIndicator = '';
                                    if (prevRank > rank) {
                                                      rankIndicator = '<span class="rank-up-arrow">⬆️</span>';
                                    }

                                    const prevScore = previous_scores[player.name] !== undefined ? previous_scores[player.name] : player.score;
                                    const scoreDiff = player.score - prevScore;

                                    if (scoreDiff > 0) {
                                                      needsAnimation = true;
                                    }

                                    rowDiv.innerHTML = `
                                                      <div class="final-rank-name">
                                                                        <span>${rankSymbol}</span>
                                                                        <img src="${player.avatar}" alt="${player.name}" class="player-avatar-small">
                                                                        <span>${player.name}${rankIndicator}</span>
                                                      </div>
                                                      <span id="score-${player.name.replace(/\s/g, '-')}" class="score-value">
                                                                        ${prevScore} pts
                                                      </span>
                                    `;
                                    renderedRows.push({ row: rowDiv, playerName: player.name, newScore: player.score, prevScore: prevScore });
                  });

                  renderedRows.forEach(rowInfo => scoreboardList.appendChild(rowInfo.row));

                  if (needsAnimation) {
                                    setTimeout(() => {
                                                      const audio = document.getElementById('host-audio-player');
                                                      audio.src = 'https://greenmeadowcoc.com/wp-content/uploads/2025/09/kahoot_audacity_score_incrementing_amp.mp3';
                                                      audio.volume = getAdjustedVolume(0.5);
                                                      audio.loop = true;
                                                      audio.play();

                                                      const animationPromises = renderedRows.map(rowInfo => {
                                                                        return new Promise(resolve => {
                                                                                          animateScore(rowInfo.playerName, rowInfo.prevScore, rowInfo.newScore, resolve);
                                                                        });
                                                      });

                                                      Promise.all(animationPromises).then(() => {
                                                                        audio.pause();
                                                                        audio.currentTime = 0;

                                                                        setTimeout(() => {
                                                                                          const newRankings = data.rankings;
                                                                                          if (newRankings.length > 0) {
                                                                                                            const firstPlaceScore = newRankings[0].score;
                                                                                                            newRankings.forEach(p => {
                                                                                                                              if (p.score === firstPlaceScore) {
                                                                                                                                                const playerRow = document.getElementById(`row-${p.name.replace(/\s/g, '-')}`);
                                                                                                                                                if (playerRow) {
                                                                                                                                                                  playerRow.classList.add('leader-row');
                                                                                                                                                }
                                                                                                                              }
                                                                                                            });
                                                                                          }
                                                                        }, 500);

                                                      });
                                    }, 1000);
                  } else {
                                    setTimeout(() => {
                                                      const firstPlaceScore = rankings.length > 0 ? rankings[0].score : null;
                                                      if (firstPlaceScore !== null) {
                                                                        rankings.forEach(p => {
                                                                                          if (p.score === firstPlaceScore) {
                                                                                                            const playerRow = document.getElementById(`row-${p.name.replace(/\s/g, '-')}`);
                                                                                                            if (playerRow) {
                                                                                                                              playerRow.classList.add('leader-row');
                                                                                                            }
                                                                                          }
                                                                        });
                                                      }
                                    }, 1000);
                  }

                  const nextBtn = document.createElement('button');
                  nextBtn.className = 'button scheme-button';
                  nextBtn.innerHTML = 'Next ⏭️';
                  nextBtn.onclick = () => socket.emit('skip_question');
                  headerControls.innerHTML = '';
                  headerControls.appendChild(nextBtn);

                  applyThemeColors(data);
}

function animateScore(playerName, startScore, endScore, callback) {
                  const scoreElement = document.getElementById(`score-${playerName.replace(/\s/g, '-')}`);
                  if (!scoreElement) {
                                    callback();
                                    return;
                  }

                  const duration = 1500;
                  let startTime = null;

                  function updateScore(timestamp) {
                                    if (!startTime) startTime = timestamp;
                                    const progress = timestamp - startTime;

                                    const interpolatedScore = Math.min(Math.floor(startScore + (endScore - startScore) * (progress / duration)), endScore);
                                    scoreElement.textContent = `${interpolatedScore} pts`;

                                    if (progress < duration) {
                                                      requestAnimationFrame(updateScore);
                                    } else {
                                                      scoreElement.textContent = `${endScore} pts`;
                                                      callback();
                                    }
                  }

                  if (startScore !== endScore) {
                                    requestAnimationFrame(updateScore);
                  } else {
                                    callback();
                  }
}

// BUG FIX 2: This function is now fully state-driven for its buttons.
function updateHostLobbyPlayers(data) {
                  const { players, game_state } = data;
                  const lobbyContainer = document.getElementById('host-lobby-container');
                  let playerListGrid = document.getElementById('player-list-grid');

                  // Initial render if the lobby is empty. This sets up the structure.
                  if (!playerListGrid) {
                                    lobbyContainer.innerHTML = `
                                                      <div class="main-content">
                                                                        <div class="main-buttons">
                                                                                          <div id="action-button-slot" style="display: inline-block;"></div>
                                                                                          <button class="styled-button" style="background-color: #f39c12;" id="reset-game-btn" onclick="socket.emit('reset_game')">
                                                                                                            <img src="https://img.icons8.com/ios-glyphs/30/ffffff/restart.png" alt="Reset Icon">
                                                                                                            Reset Game
                                                                                          </button>
                                                                                          <button class="styled-button" style="background-color: #3498db;" id="exit-host-btn" onclick="exitHostMode()">
                                                                                                            <img src="https://img.icons8.com/color/48/000000/exit.png" alt="Exit Icon">
                                                                                                            Exit Host Mode
                                                                                          </button>
                                                                        </div>
                                                                        <div class="quiz-title-area">
                                                                                          <h1 class="quiz-title">General Trivia Quiz</h1>
                                                                        </div>
                                                                        <div class="ip-area">
                                                                                          <h1 class="ip-text">IP Address:</h1>
                                                                                          <div class="ip-box" id="ip-box">${window.location.host}</div>
                                                                        </div>
                                                                        <p class="waiting-text">Waiting for participants...</p>
                                                                        <div class="player-list-grid" id="player-list-grid"></div>
                                                      </div>
                                    `;
                                    playerListGrid = document.getElementById('player-list-grid');
                  }

                  // BUG FIX 2: Always render the correct action button based on the current game state.
                  const actionButtonSlot = document.getElementById('action-button-slot');
                  if (game_state.quiz_is_custom) {
                                    // If a custom quiz has been generated, show the "Start Game" button.
                                    actionButtonSlot.innerHTML = `<button class="styled-button" id="start-game-btn" onclick="socket.emit('start_game')" style="background-color: #2ecc71;">
                                                      <img src="https://img.icons8.com/color/48/000000/start.png" alt="Start Icon"> Start Game
                                    </button>`;
                  } else {
                                    // Otherwise, show the default "Create Quiz" button.
                                    actionButtonSlot.innerHTML = `<button class="styled-button" id="create-quiz-btn" onclick="showCreateQuizPopup()" style="background-color: #e74c3c;">
                                                      Create Quiz
                                    </button>`;
                  }

                  // Dynamic Player Card rendering (unchanged)
                  const currentPlayerNames = new Set(Object.keys(players));

                  const newPlayers = [...currentPlayerNames].filter(name => !lastKnownPlayerNames.has(name));
                  newPlayers.forEach(name => {
                                    const player = players[name];
                                    const newPlayerCard = document.createElement('div');
                                    newPlayerCard.className = 'player-card';
                                    newPlayerCard.dataset.playerName = name;
                                    newPlayerCard.innerHTML = `
                                                      <img src="${player.avatar}" alt="${name}">
                                                      <span>${name}</span>
                                                      <div class="remove-icon"
                                                                        onclick="removePlayer('${name}')"
                                                                        onmouseover="this.previousElementSibling.classList.add('strikethrough')"
                                                                        onmouseout="this.previousElementSibling.classList.remove('strikethrough')">×</div>
                                    `;
                                    playerListGrid.appendChild(newPlayerCard);
                                    setTimeout(() => {
                                                      newPlayerCard.classList.add('doubleSwell');
                                    }, 10);
                  });

                  const removedPlayers = [...lastKnownPlayerNames].filter(name => !currentPlayerNames.has(name));
                  removedPlayers.forEach(name => {
                                    const playerCard = playerListGrid.querySelector(`[data-player-name="${name}"]`);
                                    if (playerCard) {
                                                      playerCard.classList.add('removed');
                                                      setTimeout(() => playerCard.remove(), 500);
                                    }
                  });

                  lastKnownPlayerNames = currentPlayerNames;
                  applyThemeColors(data);
}


function renderStandardHostView(data) {
                  const { game_state, players, question, rankings, answer_counts = [] } = data;
                  const container = document.getElementById('game-container');
                  let html = '';

                  if (game_state.game_finished) {
                                    html = `${renderScoreboard(rankings)}<button class="button scheme-button" onclick="socket.emit('reset_game')">🔄 New Game</button>`;
                  } else if (game_state.game_started) {
                                    if (game_state.showing_scoreboard) {
                                                      renderHostScoreboard(data);
                                                      return;
                                    } else if (game_state.showing_answer) {
                                                      html = `<h3>${question.question}</h3><div>${question.options.map((opt, i) => `<div class="button option scheme-option-${i} ${i === question.correct ? 'correct' : 'incorrect'}">${opt} (${answer_counts[i] || 0} votes)</div>`).join('')}</div><button class="button scheme-button" onclick="socket.emit('skip_question')">⏭️ Next</button>`;
                                    } else if (game_state.question_active) {
                                                      const answered = Object.values(players).filter(p => p.current_answer !== -1).length;
                                                      html = `<h3>[ ${game_state.time_remaining}s ]</h3><p style="font-size: 1.5em;">${question.question}</p><div>${question.options.map((opt, i) => `<div class="button option scheme-option-${i}" style="opacity:0.7">${opt}</div>`).join('')}</div><p>${answered} / ${Object.keys(players).length} answered</p><div><button class="button scheme-button" onclick="socket.emit('skip_to_answer')">🔍 Show Answer</button><button class="button scheme-button" onclick="socket.emit('skip_question')">⏭️ Skip</button></div>`;
                                    }
                  }
                  container.innerHTML = html;
                  applyThemeColors(data);
}

function renderKahootHostView(data) {
                  const { game_state, players, question, answer_counts = [] } = data;
                  const timerEl = document.getElementById('kahoot-timer');
                  const answerCountEl = document.querySelector('#kahoot-answer-count span');
                  const questionBar = document.getElementById('kahoot-question-bar');
                  const grid = document.getElementById('kahoot-answer-grid');
                  const statsContainer = document.getElementById('kahoot-answer-stats');
                  const headerControls = document.getElementById('host-header-controls');
                  const shapes = ['▲', '♦', '●', '■'];
                  const colors = data.game_state.theme_data.option_colors;

                  questionBar.classList.remove('preview-mode');
                  questionBar.style.fontSize = `${fontSettings.questionFontSize}rem`;

                  const options = document.querySelectorAll('.kahoot-option');
                  options.forEach(opt => {
                                    opt.style.fontSize = `${fontSettings.optionFontSize}rem`;
                  });


                  headerControls.innerHTML = '';

                  timerEl.style.display = game_state.question_active ? 'flex' : 'none';
                  answerCountEl.parentElement.style.display = game_state.question_active ? 'flex' : 'none';
                  questionBar.style.display = (game_state.question_active || data.game_state.current_question_index !== 0 || data.game_state.showing_answer) ? 'block' : 'none';
                  grid.style.display = 'grid';
                  statsContainer.style.display = 'none';

                  if (data.game_state.preview_active) {
                                    if (data.game_state.current_question_index === 0) {
                                                      const startButton = document.createElement('button');
                                                      startButton.className = 'start-question-btn';
                                                      startButton.textContent = 'Start Question';
                                                      startButton.onclick = () => socket.emit('start_question');
                                                      document.getElementById('kahoot-question-bar').innerHTML = question.question;
                                                      document.getElementById('kahoot-question-bar').appendChild(startButton);
                                    } else {
                                                      document.getElementById('kahoot-question-bar').innerHTML = question.question;
                                                      const progressContainer = document.createElement('div');
                                                      progressContainer.className = 'progress-bar-container';
                                                      progressContainer.innerHTML = '<div class="progress-bar" id="preview-progress"></div>';
                                                      document.getElementById('kahoot-question-bar').appendChild(progressContainer);

                                                      const progressBar = document.getElementById('preview-progress');
                                                      const totalTime = data.game_state.preview_duration;
                                                      const timeElapsed = totalTime - data.game_state.time_remaining;
                                                      const progressPercent = (timeElapsed / totalTime) * 100;
                                                      progressBar.style.width = progressPercent + '%';
                                    }
                                    applyThemeColors(data);
                  }

                  if (game_state.question_active) {
                                    timerEl.textContent = game_state.time_remaining;
                                    const answered = Object.values(players).filter(p => p.current_answer !== -1).length;
                                    answerCountEl.textContent = answered;
                                    questionBar.textContent = question.question;

                                    grid.innerHTML = question.options.map((opt, i) => `
                                                      <button class="kahoot-option" id="kahoot-opt-${i}" style="background-color: ${colors[i]}; font-size: ${fontSettings.optionFontSize}rem;">
                                                                        <span class="shape">${shapes[i]}</span>
                                                                        <span class="answer-text">${opt}</span>
                                                      </button>
                                    `).join('');

                                    const skipBtn = document.createElement('button');
                                    skipBtn.className = 'button scheme-button';
                                    skipBtn.innerHTML = '⏭️ Skip';
                                    skipBtn.onclick = () => socket.emit('skip_question');
                                    headerControls.appendChild(skipBtn);

                                    const showAnsBtn = document.createElement('button');
                                    showAnsBtn.className = 'button scheme-button';
                                    showAnsBtn.innerHTML = '🔍 Show Answer';
                                    showAnsBtn.onclick = () => socket.emit('skip_to_answer');
                                    headerControls.appendChild(showAnsBtn);

                                    applyThemeColors(data);

                  } else if (game_state.showing_answer) {
                                    questionBar.textContent = question.question;
                                    statsContainer.style.display = 'flex';

                                    const maxCount = Math.max(...answer_counts, 1);

                                    statsContainer.innerHTML = question.options.map((opt, i) => {
                                                      const count = answer_counts[i] || 0;
                                                      const maxBarHeight = 120;
                                                      const barHeight = (count / maxCount) * maxBarHeight;

                                                      return `
                                                                        <div class="answer-bar-container">
                                                                                          <div class="answer-bar-count">${count}</div>
                                                                                          <div class="answer-bar" style="height: ${barHeight}px; background-color: ${colors[i]};"></div>
                                                                                          <div class="answer-bar-shape">${shapes[i]}</div>
                                                                        </div>
                                                      `;
                                    }).join('');

                                    grid.innerHTML = question.options.map((opt, i) => {
                                                      const isCorrect = i === question.correct;
                                                      const className = `kahoot-option ${isCorrect ? 'reveal-correct' : 'reveal-incorrect'}`;
                                                      const indicator = isCorrect ? '✓' : '❌';
                                                      return `
                                                                        <button class="${className}" id="kahoot-opt-${i}" style="background-color: ${colors[i]}; font-size: ${fontSettings.optionFontSize}rem;">
                                                                                          <span class="shape">${shapes[i]}</span>
                                                                                          <span class="answer-text">${opt}</span>
                                                                                          <span class="answer-indicator">${indicator}</span>
                                                                        </button>
                                                      `;
                                    }).join('');

                                    const nextBtn = document.createElement('button');
                                    nextBtn.className = 'button scheme-button';
                                    nextBtn.innerHTML = 'Next ⏭️';
                                    nextBtn.onclick = () => socket.emit('skip_question');
                                    headerControls.innerHTML = '';
                                    headerControls.appendChild(nextBtn);

                                    applyThemeColors(data);

                  } else if (game_state.showing_scoreboard) {
                                    renderKahootScoreboard(data);
                  } else {
                                    grid.innerHTML = '';
                  }
}

function renderKahootScoreboard(data) {
                  renderHostScoreboard(data);
}

function renderPodiumView(data) {
                  const { rankings } = data;
                  const podiumWrapper = document.getElementById('podium-wrapper');
                  const headerControls = document.getElementById('host-header-controls');

                  resetPodium();

                  const topThree = rankings.slice(0, Math.min(3, rankings.length));
                  if (topThree.length === 0) return;

                  const secondPlaceEl = topThree[1] ? createPodiumPlace(topThree[1], 2) : null;
                  const firstPlaceEl = createPodiumPlace(topThree[0], 1)
                  const thirdPlaceEl = topThree[2] ? createPodiumPlace(topThree[2], 3) : null;

                  if (secondPlaceEl) podiumWrapper.appendChild(secondPlaceEl);
                  podiumWrapper.appendChild(firstPlaceEl);
                  if (thirdPlaceEl) podiumWrapper.appendChild(thirdPlaceEl);

                  const revealOrder = [];
                  if (thirdPlaceEl) {
                                    revealOrder.push({ element: thirdPlaceEl, place: 3, text: "In 3rd place...", audio: document.getElementById('host-audio-player') });
                  }
                  if (secondPlaceEl) {
                                    revealOrder.push({ element: secondPlaceEl, place: 2, text: "In 2nd place...", audio: document.getElementById('host-audio-player') });
                  }
                  revealOrder.push({ element: firstPlaceEl, place: 1, text: "And your winner is...", audio: document.getElementById('host-audio-player') });

                  revealPodiumSequence(revealOrder, 0);

                  const nextBtn = document.createElement('button');
                  nextBtn.className = 'button scheme-button';
                  nextBtn.innerHTML = 'Continue ⏭️';
                  nextBtn.onclick = () => socket.emit('skip_podium');
                  headerControls.innerHTML = '';
                  headerControls.appendChild(nextBtn);

                  applyThemeColors(data);
}

function renderFinalScoreboard(data) {
                  const { rankings } = data;
                  const scoreboardList = document.getElementById('final-scoreboard-list');
                  const headerControls = document.getElementById('host-header-controls');

                  const scoreboardTitle = document.getElementById('final-scoreboard-title');
                  const titleIconLeft = document.getElementById('final-scoreboard-icon');
                  const titleIconRight = document.getElementById('final-scoreboard-icon-right');
                  titleIconLeft.textContent = '🏆';
                  titleIconRight.textContent = '🏆';

                  scoreboardList.innerHTML = '';

                  rankings.forEach((player, index) => {
                                    const rank = index + 1;
                                    const rowDiv = document.createElement('div');
                                    rowDiv.className = 'final-score-row';

                                    let rankSymbol = `#${rank}`;
                                    if (rank === 1) rankSymbol = '🥇';
                                    else if (rank === 2) rankSymbol = '🥈';
                                    else if (rank === 3) rankSymbol = '🥉';

                                    rowDiv.innerHTML = `
                                                      <div class="final-rank-name">
                                                                        <span>${rankSymbol}</span>
                                                                        <img src="${player.avatar}" alt="${player.name}" class="player-avatar-small">
                                                                        <span>${player.name}</span>
                                                      </div>
                                                      <span>${player.score} pts</span>
                                    `;
                                    scoreboardList.appendChild(rowDiv);
                  });

                  const newGameBtn = document.createElement('button');
                  newGameBtn.className = 'button scheme-button';
                  newGameBtn.innerHTML = 'New Game 🔄';
                  newGameBtn.onclick = () => socket.emit('reset_game');
                  headerControls.innerHTML = '';
                  headerControls.appendChild(newGameBtn);

                  applyThemeColors(data);
}

function renderKahootPlayerView(data) {
                  const { game_state, players } = data;
                  const myData = players[myName];
                  const hasAnswered = myData.current_answer !== -1;
                  const shapes = ['▲', '♦', '●', '■'];
                  const colors = data.game_state.theme_data.option_colors;

                  if (game_state.question_active) {
                                    if (hasAnswered) {
                                                      return `<h2 class="kahoot-answer-reveal">Waiting for other players...</h2>`;
                                    } else {
                                                      return `
                                                                        <div id="kahoot-player-grid" style="position: fixed; top: 0; left: 0; width: 100%; height: 100%; display: grid; grid-template-columns: 1fr 1fr; grid-template-rows: 1fr 1fr;">
                                                                                          ${shapes.map((shape, i) => `
                                                                                                            <button class="player-kahoot-option" style="background-color: ${colors[i]};" onclick="submitAnswer(${i})">
                                                                                                                              <span class="shape">${shape}</span>
                                                                                                            </button>
                                                                                          `).join('')}
                                                                        </div>
                                                      `;
                                    }
                  } else if (game_state.showing_answer) {
                                    const isCorrect = myData.current_answer === data.question.correct;
                                    const currentScore = players[myName].score;
                                    const questionPoints = isCorrect ? myData.earned_points : 0;

                                    const rankings = Object.values(players)
                                                                                                                                                        .map(p => p.score)
                                                                                                                                                        .sort((a, b) => b - a);
                                    const myRank = rankings.findIndex(s => s === currentScore) + 1;

                                    if (isCorrect) {
                                                      return `
                                                                        <div style="position: fixed; top: 0; left: 0; width: 100%; height: 100%; display: flex; flex-direction: column; justify-content: center; align-items: center; background: ${data.game_state.theme_data.background_color};">
                                                                                          <h1 style="font-size: 3rem; margin-bottom: 20px;">Correct</h1>
                                                                                          <div style="width: 120px; height: 120px; border-radius: 50%; background: #26890c; display: flex; justify-content: center; align-items: center; margin-bottom: 30px;">
                                                                                                            <span style="font-size: 4rem; color: white;">✓</span>
                                                                                          </div>
                                                                                          <div style="background: rgba(0,0,0,0.6); padding: 15px 40px; border-radius: 25px; margin-bottom: 20px;">
                                                                                                            <span style="font-size: 2rem; font-weight: bold;">+ ${questionPoints}</span>
                                                                                          </div>
                                                                                          <p style="font-size: 1.5rem; margin: 0;">You're ${myRank === 1 ? 'on the podium!' : `in ${myRank}${getOrdinalSuffix(myRank)} place`}</p>
                                                                        </div>
                                                      `;
                                    } else {
                                                      return `
                                                                        <div style="position: fixed; top: 0; left: 0; width: 100%; height: 100%; display: flex; flex-direction: column; justify-content: center; align-items: center; background: ${data.game_state.theme_data.background_color};">
                                                                                          <h1 style="font-size: 3rem; margin-bottom: 20px; color: #e74c3c;">Incorrect</h1>
                                                                                          <div style="width: 120px; height: 120px; border-radius: 50%; background: #e74c3c; display: flex; justify-content: center; align-items: center; margin-bottom: 30px;">
                                                                                                            <span style="font-size: 4rem; color: white;">❌</span>
                                                                                          </div>
                                                                                          <div style="background: rgba(0,0,0,0.6); padding: 15px 40px; border-radius: 25px; margin-bottom: 20px;">
                                                                                                            <span style="font-size: 2rem; font-weight: bold;">+ 0</span>
                                                                                          </div>
                                                                                          <p style="font-size: 1.5rem; margin: 0;">You're ${myRank === 1 ? 'still on the podium!' : `in ${myRank}${getOrdinalSuffix(myRank)} place`}</p>
                                                                        </div>
                                                      `;
                                    }
                  }
                  return `<h2>Welcome, ${myName}!</h2><p>Waiting for the host to start...</p>${renderPlayerList(players, false)}`;
}

function renderPlayerView(data) {
                  const { game_state, players } = data;
                  if (!myName || !players[myName]) return `<h2>Re-joining game...</h2>`;
                  const myData = players[myName];

                  const userTopBar = document.querySelector('.user-top-bar');
                  const userBottomBar = document.querySelector('.user-bottom-bar');
                  const questionNumberEl = document.getElementById('player-question-number');
                  const playerNameEl = document.getElementById('player-name-display');
                  const playerScoreEl = document.getElementById('player-score-display');
                  const playerAvatarEl = document.getElementById('player-avatar-display');

                  userTopBar.style.display = 'flex';
                  userBottomBar.style.display = 'flex';

                  if (game_state.game_started) {
                                    questionNumberEl.textContent = data.game_state.current_question_index + 1;
                                    playerNameEl.textContent = myName;
                                    playerScoreEl.textContent = myData.score;
                                    playerAvatarEl.src = myData.avatar;
                  } else {
                                    questionNumberEl.textContent = '';
                                    playerNameEl.textContent = myName;
                                    playerScoreEl.textContent = '0';
                                    playerAvatarEl.src = myData.avatar;
                  }

                  if (data.game_state.theme_data) {
                                    const accentColor = data.game_state.theme_data.accent_color;
                                    playerScoreEl.style.color = accentColor;
                  }

                  if (game_state.game_finished) {
                                    const myRank = data.rankings.findIndex(p => p.name === myName) + 1;
                                    let message = 'You can do better next time! 👍';

                                    if (myRank === 1) {
                                                      message = 'You are the champ! 🎉';
                                    } else if (myRank === 2) {
                                                      message = 'Congrats, you are on the podium! 🥈';
                                    } else if (myRank === 3) {
                                                      message = 'Congrats, you are on the podium! 🥉';
                                    }

                                    return `
                                                      <h2>🎉 Game Over! 🎉</h2>
                                                      <h3>Final Score: ${myData.score}</h3>
                                                      <h3 style="color: #f1c40f;">You placed ${myRank}${getOrdinalSuffix(myRank)}!</h3>
                                                      <p>${message}</p>
                                                      <p>Waiting for the host to show final results...</p>
                                    `;
                  }
                  if (game_state.showing_scoreboard) return `<h2>Leaderboard is on the main screen!</h2><p>Your score: ${myData.score}</p>`;
                  if (game_state.showing_podium) return `<h2>The final podium is being revealed!</h2><p>Your score: ${myData.score}</p>`;
                  if (game_state.title_screen_active) return `
                                    <div style="position: fixed; top: 0; left: 0; width: 100%; height: 100%; display: flex; flex-direction: column; justify-content: center; align-items: center;">
                                                      <h2 style="font-size: 2rem; text-align:center;">The game is starting soon!</h2>
                                                      <p style="font-size: 1.2rem; text-align:center;">Get ready!</p>
                                    </div>
                  `;
                  if (game_state.video_playing) return `
                                    <div style="position: fixed; top: 0; left: 0; width: 100%; height: 100%; display: flex; flex-direction: column; justify-content: center; align-items: center;">
                                                      <h2 style="font-size: 2rem; text-align:center;">Video is playing on the host's screen!</h2>
                                                      <p style="font-size: 1.2rem; text-align:center;">Get ready for the first question!</p>
                                    </div>
                  `;
                  if (game_state.countdown_active) return `
                                    <div style="position: fixed; top: 0; left: 0; width: 100%; height: 100%; display: flex; flex-direction: column; justify-content: center; align-items: center;">
                                                      <h2 style="font-size: 2rem; text-align:center;">Get Ready...</h2>
                                                      <p style="font-size: 1.2rem; text-align:center;">The countdown is on the host's screen!</p>
                                    </div>
                  `;
                  if (game_state.preview_active) return `
                                    <div style="position: fixed; top: 0; left: 0; width: 100%; height: 100%; display: flex; flex-direction: column; justify-content: center; align-items: center;">
                                                      <h2 style="font-size: 2rem; text-align:center;">Question is on the host's screen!</h2>
                                                      <p style="font-size: 1.2rem; text-align:center;">Get ready!</p>
                                    </div>
                  `;
                  if (game_state.question_active || game_state.showing_answer) {
                                    return renderKahootPlayerView(data);
                  }
                  return `<h2>Welcome, ${myName}!</h2><p>Waiting for the host to start...</p>${renderPlayerList(players, false)}`;
}

function renderPlayerList(players, isHost) {
                  let rows = Object.keys(players).map(name => {
                                    const player = players[name];
                                    const deleteBtn = isHost ? `<span class="delete-btn" onclick="removePlayer('${name}')">❌</span>` : '';
                                    return `<div class="player-row"><span><img src="${player.avatar}" alt="Avatar" class="player-avatar-small"> ${name}</span>${deleteBtn}</div>`;
                  }).join('');
                  if (!rows) rows = "<p>No players have joined yet.</p>";
                  return `<div id="player-list"><h3>Players:</h3>${rows}</div>`;
}

function renderScoreboard(rankings) {
                  return `<div id="scoreboard">
                                    ${rankings.map((p, i) => `
                                                      <div class="score-row">
                                                                        <span>#${i + 1}: <img src="${p.avatar}" alt="Avatar" class="player-avatar-small"> ${p.name}</span>
                                                                        <span>${p.score} pts</span>
                                                      </div>
                                    `).join('')}
                  </div>`;
}

function showHostLogin() { document.getElementById('role-selection').innerHTML = `<input type="password" id="host-password" placeholder="Enter Host Password" onkeydown="if(event.key==='Enter') loginAsHost()"><button class="button" style="background-color: #3498db;" onclick="loginAsHost()">Login as Host</button>`; }

function getOrdinalSuffix(number) {
                  const j = number % 10;
                  const k = number % 100;
                  if (j === 1 && k !== 11) return 'st';
                  if (j === 2 && k !== 12) return 'nd';
                  if (j === 3 && k !== 13) return 'rd';
                  return 'th';
}

function hexToRgb(hex) {
                  let r = 0, g = 0, b = 0;
                  if (hex.length === 4) {
                                    r = parseInt(hex[1] + hex[1], 16);
                                    g = parseInt(hex[2] + hex[2], 16);
                                    b = parseInt(hex[3] + hex[3], 16);
                  } else if (hex.length === 7) {
                                    r = parseInt(hex.substring(1, 3), 16);
                                    g = parseInt(hex.substring(3, 5), 16);
                                    b = parseInt(hex.substring(5, 7), 16);
                  }
                  return `${r}, ${g}, ${b}`;
}

function resetPodium() {
                  const podiumWrapper = document.getElementById('podium-wrapper');
                  podiumWrapper.innerHTML = '';
                  document.querySelectorAll('.confetti, .winner-crown, .anticipation-text').forEach(el => el.remove());
                  const overlay = document.getElementById('dark-overlay');
                  overlay.classList.remove('active');
}

function revealPodiumSequence(sequence, index) {
                  if (index >= sequence.length) return;
                  const step = sequence[index];

                  if (step.place === 1) {
                                    const overlay = document.getElementById('dark-overlay');
                                    overlay.classList.add('active');

                                    setTimeout(() => {
                                                      showAnticipationText(step.text);
                                                      setTimeout(() => {
                                                                        revealStep(step.element, step.place);
                                                                        setTimeout(() => {
                                                                                          overlay.classList.remove('active');
                                                                        }, 4000);
                                                      }, 1500);
                                    }, 500);
                  } else {
                                    showAnticipationText(step.text);
                                    setTimeout(() => {
                                                      revealStep(step.element, step.place);
                                                      setTimeout(() => {
                                                                        revealPodiumSequence(sequence, index + 1);
                                                      }, 4000);
                                    }, 1500);
                  }
}

function revealStep(element, place) {
                  element.classList.add('show');
                  if (place === 1) {
                                    setTimeout(() => {
                                                      const crown = document.createElement('div');
                                                      crown.className = 'winner-crown';
                                                      crown.textContent = '👑';
                                                      element.querySelector('.podium-player').appendChild(crown);
                                                      createWinnerConfetti();
                                    }, 500);
                  }
}

function showAnticipationText(text) {
                  const existing = document.querySelector('.anticipation-text');
                  if (existing) existing.remove();

                  const textElement = document.createElement('div');
                  textElement.className = 'anticipation-text';
                  textElement.textContent = text;
                  document.body.appendChild(textElement);
}

function createWinnerConfetti() {
                  const colors = ['#f1c40f', '#e74c3c', '#3498db', '#2ecc71', '#9b59b6', '#e67e22'];
                  for (let i = 0; i < 100; i++) {
                                    const confetti = document.createElement('div');
                                    confetti.className = 'confetti';
                                    confetti.style.left = Math.random() * 100 + '%';
                                    confetti.style.backgroundColor = colors[Math.floor(Math.random() * colors.length)];
                                    confetti.style.animationDelay = Math.random() * 1 + 's';
                                    confetti.style.animationDuration = (Math.random() * 2 + 2) + 's';
                                    if (Math.random() > 0.7) {
                                                      confetti.style.width = '15px';
                                                      confetti.style.height = '15px';
                                    }
                                    document.body.appendChild(confetti);
                  }
}

function createPodiumPlace(player, place) {
                  const placeDiv = document.createElement('div');
                  placeDiv.className = `podium-place ${place === 1 ? 'first' : place === 2 ? 'second' : 'third'}-place`;
                  const medals = ['', '🥇', '🥈', '🥉'];
                  placeDiv.innerHTML = `
                                    <div class="podium-player">
                                                      <img src="${player.avatar}" alt="${player.name}" class="podium-avatar">
                                                      <div class="podium-name">${player.name}</div>
                                                      <div class="podium-score">${player.score} pts</div>
                                    </div>
                                    <div class="podium-base">
                                                      ${medals[place]} ${place}
                                    </div>
                  `;
                  return placeDiv;
}

function applyThemeColors(data) {
                  if (!data.game_state || !data.game_state.theme_data) return;
                  const themeData = data.game_state.theme_data;

                  document.querySelectorAll('.scheme-button').forEach(btn => {
                                    btn.style.backgroundColor = themeData.accent_color;
                  });

                  const optionColors = themeData.option_colors;
                  document.querySelectorAll('.option').forEach((option, i) => {
                                    option.style.backgroundColor = optionColors[i];
                  });
                  document.querySelectorAll('.player-kahoot-option').forEach((option, i) => {
                                    option.style.backgroundColor = optionColors[i];
                  });
                  document.querySelectorAll('.kahoot-option').forEach((option, i) => {
                                    option.style.backgroundColor = optionColors[i];
                  });
                  document.querySelectorAll('.answer-bar').forEach((bar, i) => {
                                    bar.style.backgroundColor = optionColors[i];
                  });

                  const container = document.getElementById('game-container');
                  if (container) {
                                    container.style.background = `rgba(255, 255, 255, 0.05)`;
                  }

                  const startBtn = document.querySelector('.start-question-btn');
                  if (startBtn) {
                                    startBtn.style.backgroundColor = themeData.accent_color;
                  }

                  const lobbyContainer = document.getElementById('host-lobby-container');
                  if (lobbyContainer.style.display !== 'none') {
                                    const createBtn = document.getElementById('create-quiz-btn');
                                    if (createBtn) createBtn.style.backgroundColor = themeData.accent_color;

                                    const ipBox = document.getElementById('ip-box');
                                    if (ipBox) ipBox.style.color = themeData.ip_color;

                                    document.querySelectorAll('.player-card img').forEach(img => {
                                                      img.style.borderColor = themeData.ip_color;
                                    });
                  }
}

function selectAvatar(index) {
                  selectedAvatarIndex = index;
                  const avatarGrid = document.getElementById('avatar-grid');
                  if (avatarGrid) {
                                    document.getElementById('selected-avatar-index').value = index;
                                    document.querySelectorAll('.avatar-option').forEach((el, i) => {
                                                      el.classList.toggle('selected', i === index);
                                    });
                  }
}

function openSettings() {
                  const existingModal = document.getElementById('settings-modal');
                  if (existingModal) existingModal.remove();

                  const modal = document.createElement('div');
                  modal.id = 'settings-modal';
                  modal.innerHTML = `
                                    <h2 style="margin-top:0;">Settings</h2>
                                    <div class="settings-control">
                                                      <label for="kahoot-layout-toggle">
                                                                        <input type="checkbox" id="kahoot-layout-toggle" ${kahootLayout ? 'checked' : ''}>
                                                                        Enable Kahoot-style Layout
                                                      </label>
                                    </div>
                                    <h3>Font Sizes (Host View)</h3>
                                    <div class="settings-control">
                                                      <label for="linked-fonts">
                                                                        <input type="checkbox" id="linked-fonts" ${fontSettings.linked ? 'checked' : ''}>
                                                                        Link Question/Preview Sizes
                                                      </label>
                                    </div>
                                    <div class="settings-control">
                                                      <label for="preview-font-size-slider">Preview Question Font Size: <span id="preview-font-size-value">${fontSettings.previewFontSize}</span>rem</label>
                                                      <input type="range" id="preview-font-size-slider" min="1.0" max="5.0" step="0.1" value="${fontSettings.previewFontSize}">
                                    </div>
                                    <div class="settings-control">
                                                      <label for="question-font-size-slider">Question Font Size: <span id="question-font-size-value">${fontSettings.questionFontSize}</span>rem</label>
                                                      <input type="range" id="question-font-size-slider" min="1.0" max="5.0" step="0.1" value="${fontSettings.questionFontSize}">
                                    </div>

                                    <h3>Theme Settings</h3>
                                    <div class="settings-control" style="display: flex; justify-content: space-around;">
                                                      <button class="button" style="background-color: #3498db;" onclick="toggleTheme()">Toggle Theme</button>
                                                      <button class="button" style="background-color: #f1c40f;" onclick="resetToDefaultTheme()">Default Theme</button>
                                    </div>

                                    <div style="display: flex; justify-content: space-between; margin-top: 20px;">
                                                      <button class="button" style="background-color:#e74c3c;" onclick="restoreDefaultFontSettings()">Font Reset</button>
                                                      <button class="button" style="background-color:#95a5a6;" onclick="this.parentElement.parentElement.remove()">Close</button>
                                    </div>
                  `;
                  document.body.appendChild(modal);

                  const previewSlider = document.getElementById('preview-font-size-slider');
                  const questionSlider = document.getElementById('question-font-size-slider');
                  const linkedCheckbox = document.getElementById('linked-fonts');
                  const previewValue = document.getElementById('preview-font-size-value');
                  const questionValue = document.getElementById('question-font-size-value');

                  const initialRatio = fontSettings.previewFontSize / fontSettings.questionFontSize;

                  previewSlider.oninput = function() {
                                    fontSettings.previewFontSize = parseFloat(this.value);
                                    previewValue.textContent = this.value;
                                    if (linkedCheckbox.checked) {
                                                      const newQuestionSize = fontSettings.previewFontSize / initialRatio;
                                                      questionSlider.value = newQuestionSize;
                                                      fontSettings.questionFontSize = newQuestionSize;
                                                      questionValue.textContent = newQuestionSize.toFixed(1);
                                    }
                                    saveFontSettings();
                  };

                  questionSlider.oninput = function() {
                                    fontSettings.questionFontSize = parseFloat(this.value);
                                    questionValue.textContent = this.value;
                                    if (linkedCheckbox.checked) {
                                                      const newPreviewSize = fontSettings.questionFontSize * initialRatio;
                                                      previewSlider.value = newPreviewSize;
                                                      fontSettings.previewFontSize = newPreviewSize;
                                                      previewValue.textContent = newPreviewSize.toFixed(1);
                                    }
                                    saveFontSettings();
                  };

                  linkedCheckbox.onchange = function() {
                                    fontSettings.linked = this.checked;
                                    localStorage.setItem('fontSettings', JSON.stringify(fontSettings));
                  };

                  document.getElementById('kahoot-layout-toggle').addEventListener('change', (e) => {
                                    kahootLayout = e.target.checked;
                                    localStorage.setItem('kahootLayout', kahootLayout);
                                    socket.emit('get_state');
                  });
}

function toggleTheme() {
                  socket.emit('toggle_theme');
}

function resetToDefaultTheme() {
                  socket.emit('reset_theme_to_default');
}

function saveFontSettings() {
                  localStorage.setItem('fontSettings', JSON.stringify(fontSettings));
                  socket.emit('get_state');
}

function restoreDefaultFontSettings() {
                  localStorage.setItem('fontSettings', JSON.stringify(defaultFontSettings));
                  Object.assign(fontSettings, defaultFontSettings);
                  const previewSlider = document.getElementById('preview-font-size-slider');
                  const questionSlider = document.getElementById('question-font-size-slider');
                  const linkedCheckbox = document.getElementById('linked-fonts');

                  previewSlider.value = defaultFontSettings.previewFontSize;
                  questionSlider.value = defaultFontSettings.questionFontSize;
                  linkedCheckbox.checked = defaultFontSettings.linked;

                  document.getElementById('preview-font-size-value').textContent = defaultFontSettings.previewFontSize;
                  document.getElementById('question-font-size-value').textContent = defaultFontSettings.questionFontSize;

                  saveFontSettings();
}


function updateAudioSettings(audioId, setting, value, defaultVal) {
                  const audio = document.getElementById(audioId);
                  if (setting === 'volume') {
                                    audio.volume = value;
                  }
}

function toggleFullscreen() { if (!document.fullscreenElement) { document.documentElement.requestFullscreen().catch(err => { alert(`Could not enter fullscreen mode: ${err.message}`); }); } else if (document.exitFullscreen) document.exitFullscreen(); }

function toggleSound() {
                  soundState = (soundState + 1) % 3;
                  const soundBtn = document.getElementById('sound-btn');
                  const icons = ['🔊', '🔉', '🔇'];
                  if (soundBtn) soundBtn.innerHTML = icons[soundState];

                  const hostAudioPlayer = document.getElementById('host-audio-player');
                  const playerAudioPlayer = document.getElementById('player-audio-player');
                  const lobbyMusicPlayer = document.getElementById('lobby-music-player');

                  // This function now only needs to adjust the volume of currently playing audio.
                  // The getAdjustedVolume function will handle all future sounds.
                  if (soundState === 0) { // High
                                    if (!hostAudioPlayer.paused) hostAudioPlayer.volume = 1.0; // These values can be defaults
                                    if (!playerAudioPlayer.paused) playerAudioPlayer.volume = 1.0;
                                    if (!lobbyMusicPlayer.paused) lobbyMusicPlayer.volume = 0.35;
                  } else if (soundState === 1) { // Low
                                    if (!hostAudioPlayer.paused) hostAudioPlayer.volume = 0.5;
                                    if (!playerAudioPlayer.paused) playerAudioPlayer.volume = 0.5;
                                    if (!lobbyMusicPlayer.paused) lobbyMusicPlayer.volume = 0.15;
                  } else { // Mute
                                    hostAudioPlayer.volume = 0.0;
                                    playerAudioPlayer.volume = 0.0;
                                    lobbyMusicPlayer.volume = 0.0;
                  }
}


function exitHostMode() { if (confirm('Are you sure you want to stop hosting? This will end the current session.')) socket.emit('leave_host_role'); }
function loginAsHost() { socket.emit('login_as_host', { password: document.getElementById('host-password').value }); }

// MODIFICATION #2: Replace removePlayer function with custom popup logic
function removePlayer(name) {
                  const confirmPopup = document.getElementById('custom-confirm');
                  const confirmMsg = document.getElementById('custom-confirm-msg');
                  const yesBtn = document.getElementById('confirm-yes-btn');
                  const noBtn = document.getElementById('confirm-no-btn');

                  confirmMsg.textContent = `Are you sure you want to remove ${name}?`;

                  // Use .onclick to easily replace the handler each time to capture the correct 'name'
                  yesBtn.onclick = () => {
                                    const playerCard = document.querySelector(`.player-card[data-player-name="${name}"]`);
                                    if (playerCard) {
                                                      playerCard.classList.add('removed');
                                                      setTimeout(() => {
                                                                        if (playerCard) playerCard.remove();
                                                      }, 500);
                                    }
                                    socket.emit('remove_player', { player_name: name });
                                    confirmPopup.style.display = 'none';
                  };

                  noBtn.onclick = () => {
                                    confirmPopup.style.display = 'none';
                  };

                  confirmPopup.style.display = 'block';
}

function submitAnswer(index) { document.querySelectorAll('.player-kahoot-option').forEach(b => b.disabled = true); socket.emit('submit_answer', { answer_index: index }); }
function getCookie(name) { const v = `; ${document.cookie}`; const p = v.split(`; ${name}=`); if (p.length === 2) return p.pop().split(';').shift(); }
function clearPlayerCookie() { myName = null; myRole = 'spectator'; document.cookie = 'player_name_cache=; Max-Age=-99999999; path=/;'; window.location.reload(); }
function rejoinAsPlayer() { socket.emit('rejoin_as_player', {player_name: myName}); }

// NEW: Functions for Create Quiz Popup
function showCreateQuizPopup() {
                  document.getElementById('create-quiz-modal').style.display = 'flex';
}

function hideCreateQuizPopup() {
                  document.getElementById('create-quiz-modal').style.display = 'none';
}

// NEW: Function to toggle API key visibility
function toggleApiKeyVisibility() {
                  const apiKeyInput = document.getElementById('gemini-api-key');
                  const icon = document.querySelector('.password-toggle-icon');
                  if (apiKeyInput.type === 'password') {
                                    apiKeyInput.type = 'text';
                                    icon.textContent = '🙈';
                  } else {
                                    apiKeyInput.type = 'password';
                                    icon.textContent = '👁️';
                  }
}

// MODIFIED: This function now sends the video choice to the backend
function handleGenerateQuiz() {
                  const apiKey = document.getElementById('gemini-api-key').value.trim();
                  const topic = document.getElementById('quiz-topic').value.trim();
                  const numQuestions = document.getElementById('num-questions').value;
                  const difficulty = document.getElementById('difficulty').value;
                  const videoChoice = document.getElementById('video-choice').value;        

                  // MODIFIED VALIDATION: Only check for topic. It is OK for apiKey to be empty;
                  // the server will attempt to use HARDCODED_API_KEY or ENV_API_KEY.
                  if (!topic) {
                                    alert('Please provide a quiz topic.');
                                    return;
                  }

                  const generateBtn = document.getElementById('generate-quiz-btn');
                  generateBtn.disabled = true;
                  generateBtn.textContent = 'Generating...';

                  socket.emit('generate_quiz_with_ai', {
                                    apiKey: apiKey, // Sends "" if empty, or "AIzaSy..." if you typed one
                                    topic: topic,
                                    num_questions: parseInt(numQuestions, 10),
                                    difficulty: difficulty,
                                    video_choice: videoChoice
                  });
}

setInterval(() => {
                  const skipBtn = document.getElementById('kahoot-skip-btn');
                  const kahootWrapper = document.getElementById('kahoot-layout-wrapper');
                  if (skipBtn && kahootWrapper.style.display === 'none') {
                                    skipBtn.remove();
                  }
}, 1000);

document.addEventListener('DOMContentLoaded', () => {
                  selectAvatar(0);
                  // Add event listeners for the new modal
                  document.getElementById('generate-quiz-btn').addEventListener('click', handleGenerateQuiz);
                  document.getElementById('cancel-quiz-btn').addEventListener('click', hideCreateQuizPopup);
                  const modal = document.getElementById('create-quiz-modal');
                  modal.addEventListener('click', (event) => {
                                    // Close modal if the background overlay is clicked
                                    if (event.target === modal) {
                                                      hideCreateQuizPopup();
                                    }
                  });
});
</script>
</body>
</html>
"""

# --- Main Execution (MODIFIED FOR DEPLOYMENT) ---
# Check for environment variables set by the hosting service (like Render)
if os.environ.get('RENDER') or os.environ.get('GUNICORN_WORKER_CLASS'):
    # Production/Cloud startup: Gunicorn manages the server via the Procfile.
    # The 'app' object is implicitly exported by Gunicorn.
    print("--- Cloud/Production Startup Detected ---")
    print("Gunicorn is running the app asynchronously with Eventlet.")

else:
    # Local/Development startup: Use the robust Eventlet WSGI server directly.
    local_ip = get_local_ip() # Uses the simplified non-blocking function
    PORT = 5000
    print("--- Local Server Startup (Eventlet) ---")
    print(f"🌍 Open this URL in your browser(s): http://{local_ip}:{PORT}")
    print(f"🔑 Host password is: {os.getenv('HOST_PASSWORD', '1234')}")
    print("----------------------------------------")
    try:
        wsgi.server(eventlet.listen(('0.0.0.0', PORT)), app)
    except Exception as e:
        print(f"ERROR: Could not start local server on port {PORT}. Error: {e}")