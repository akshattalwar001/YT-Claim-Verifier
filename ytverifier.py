from flask import Flask, request, jsonify, render_template
from flask_cors import CORS
import yt_dlp
import google.generativeai as genai
import os
import re
from pathlib import Path
import tempfile
import uuid
from dotenv import load_dotenv
import requests
from urllib.parse import urlparse, parse_qs
import time
import json

# Load environment variables
load_dotenv()

app = Flask(__name__)
CORS(app)

# Configuration
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')

if not GEMINI_API_KEY:
    print("❌ GEMINI_API_KEY not found in environment variables")
    print("🔑 Please create a .env file with: GEMINI_API_KEY=your_api_key_here")
else:
    # Initialize Gemini
    genai.configure(api_key=GEMINI_API_KEY)
    model = genai.GenerativeModel('gemini-1.5-flash')


def extract_video_id(url):
    """Extract YouTube video ID from URL"""
    parsed_url = urlparse(url)

    if parsed_url.hostname in ['youtu.be']:
        return parsed_url.path[1:]
    elif parsed_url.hostname in ['www.youtube.com', 'youtube.com', 'm.youtube.com']:
        if parsed_url.path == '/watch':
            return parse_qs(parsed_url.query)['v'][0]
        elif parsed_url.path.startswith('/embed/'):
            return parsed_url.path.split('/')[2]
        elif parsed_url.path.startswith('/v/'):
            return parsed_url.path.split('/')[2]

    return None


def extract_subtitles_ytdlp_robust(url):
    """Extract subtitles using yt-dlp with multiple robust configurations"""
    print("🔧 Trying yt-dlp with robust settings...")

    # Create unique temp filename
    temp_id = str(uuid.uuid4())[:8]
    temp_filename = f'temp_subtitle_{temp_id}'

    # Multiple configurations to try - ordered from most to least likely to work on servers
    configs = [
        # Configuration 1: Android mobile client (often bypasses server restrictions)
        {
            'writesubtitles': True,
            'writeautomaticsub': True,
            'subtitleslangs': ['en', 'en-US', 'en-GB'],
            'subtitlesformat': 'vtt',  # VTT format is more reliable
            'outtmpl': f'{temp_filename}.%(ext)s',
            'skip_download': True,
            'quiet': True,
            'no_warnings': True,
            'extractor_args': {
                'youtube': {
                    'player_client': ['android'],
                    'skip': ['hls', 'dash']
                }
            },
            'http_headers': {
                'User-Agent': 'com.google.android.youtube/17.31.35 (Linux; U; Android 11) gzip',
                'Accept-Language': 'en-US,en;q=0.9',
            }
        },

        # Configuration 2: iOS client
        {
            'writesubtitles': True,
            'writeautomaticsub': True,
            'subtitleslangs': ['en'],
            'subtitlesformat': 'vtt',
            'outtmpl': f'{temp_filename}.%(ext)s',
            'skip_download': True,
            'quiet': True,
            'no_warnings': True,
            'extractor_args': {
                'youtube': {
                    'player_client': ['ios']
                }
            },
            'http_headers': {
                'User-Agent': 'com.google.ios.youtube/17.31.4 (iPhone14,3; U; CPU iOS 15_6 like Mac OS X)'
            }
        },

        # Configuration 3: Web client with residential-like headers
        {
            'writesubtitles': True,
            'writeautomaticsub': True,
            'subtitleslangs': ['en'],
            'subtitlesformat': 'srt',
            'outtmpl': f'{temp_filename}.%(ext)s',
            'skip_download': True,
            'quiet': True,
            'no_warnings': True,
            'extractor_args': {
                'youtube': {
                    'player_client': ['web']
                }
            },
            'http_headers': {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
                'Accept-Language': 'en-US,en;q=0.5',
                'Accept-Encoding': 'gzip, deflate',
                'Referer': 'https://www.youtube.com/',
            }
        },

        # Configuration 4: Basic fallback
        {
            'writesubtitles': True,
            'writeautomaticsub': True,
            'subtitleslangs': ['en'],
            'subtitlesformat': 'srt',
            'outtmpl': f'{temp_filename}.%(ext)s',
            'skip_download': True,
            'quiet': True,
            'no_warnings': True,
            'socket_timeout': 30,
        }
    ]

    video_title = "Unknown Video"

    for i, options in enumerate(configs):
        try:
            print(f"  🔄 Trying configuration {i + 1}/{len(configs)}...")

            with yt_dlp.YoutubeDL(options) as ydl:
                # First try to get video info
                try:
                    info = ydl.extract_info(url, download=False)
                    video_title = info.get('title', 'Unknown Video')
                    print(f"  📹 Video: {video_title}")
                except Exception as info_error:
                    print(f"  ⚠️ Could not get video info: {info_error}")

                # Try to download subtitles
                ydl.download([url])

            # Look for subtitle files (try different extensions)
            subtitle_extensions = ['en.vtt', 'en.srt', 'en-US.vtt', 'en-US.srt', 'en-GB.vtt', 'en-GB.srt']

            for ext in subtitle_extensions:
                subtitle_file = f"{temp_filename}.{ext}"
                if os.path.exists(subtitle_file):
                    print(f"  ✅ Found subtitle file: {subtitle_file}")

                    with open(subtitle_file, "r", encoding="utf-8") as f:
                        content = f.read()

                    # Clean subtitle content based on format
                    if ext.endswith('.vtt'):
                        # Clean VTT format
                        lines = content.split('\n')
                        text_lines = []
                        for line in lines:
                            line = line.strip()
                            # Skip VTT headers, timestamps, and empty lines
                            if (line and
                                    not line.startswith('WEBVTT') and
                                    not line.startswith('NOTE') and
                                    '-->' not in line and
                                    not line.isdigit()):
                                text_lines.append(line)
                        text = ' '.join(text_lines)
                    else:
                        # Clean SRT format
                        text = re.sub(r'\d+\n\d{2}:\d{2}:\d{2},\d{3} --> \d{2}:\d{2}:\d{2},\d{3}\n', '', content)
                        text = re.sub(r'\n+', ' ', text).strip()

                    # Clean up temp file
                    try:
                        os.remove(subtitle_file)
                    except:
                        pass

                    if text and len(text) > 50:  # Ensure we have substantial content
                        print(f"  ✅ Successfully extracted {len(text)} characters")
                        return video_title, text

            print(f"  ❌ No subtitle files found for configuration {i + 1}")

            # Wait between attempts to avoid being flagged
            if i < len(configs) - 1:
                time.sleep(2)

        except Exception as e:
            print(f"  ❌ Configuration {i + 1} failed: {e}")
            continue

    return None, "All yt-dlp configurations failed. The video may not have subtitles or server access is restricted."


def extract_subtitles(url):
    """Extract subtitles with robust yt-dlp configurations"""
    print("🚀 Extracting subtitles...")

    # Extract video ID for validation
    video_id = extract_video_id(url)
    if not video_id:
        return None, "Invalid YouTube URL"

    print(f"📹 Video ID: {video_id}")

    # Use robust yt-dlp extraction
    try:
        video_title, transcript = extract_subtitles_ytdlp_robust(url)
        if transcript and video_title and len(transcript) > 50:
            print("✅ Success with yt-dlp")
            return video_title, transcript
        else:
            return None, "Could not extract meaningful transcript content"
    except Exception as e:
        print(f"❌ yt-dlp failed: {e}")
        return None, f"Extraction failed: {str(e)}"


def extract_claims(text, video_title):
    """Extract factual claims from video text using Gemini"""
    print("🔍 Extracting claims...")

    prompt = f"""
    Analyze this video transcript and extract the main factual claims that can be fact-checked.

    Video Title: {video_title}

    Transcript: {text[:4000]}  # Limit to avoid token limits

    Please:
    1. Identify 3-5 key factual claims made in the video
    2. Focus on specific, verifiable statements (numbers, dates, scientific facts, historical events)
    3. Ignore opinions, predictions, or subjective statements
    4. Format as a numbered list

    Example format:
    1. [Specific factual claim from the video]
    2. [Another factual claim]
    """

    try:
        response = model.generate_content(prompt)
        return response.text
    except Exception as e:
        print(f"❌ Error extracting claims: {e}")
        return f"Error extracting claims: {str(e)}"


def fact_check_claims(claims):
    """Fact-check the extracted claims using Gemini"""
    print("✅ Fact-checking claims...")

    prompt = f"""
    Please fact-check these claims from a YouTube video. For each claim:
    1. Assess if it's TRUE, FALSE, or PARTIALLY TRUE/MISLEADING
    2. Provide a brief explanation with reasoning
    3. If possible, mention reliable sources

    Claims to check:
    {claims}

    Format your response clearly for each claim with:
    - Status: [TRUE/FALSE/PARTIALLY TRUE]
    - Explanation: [Brief factual explanation]
    - Confidence: [High/Medium/Low]
    """

    try:
        response = model.generate_content(prompt)
        return response.text
    except Exception as e:
        print(f"❌ Error fact-checking: {e}")
        return f"Error fact-checking claims: {str(e)}"


@app.route('/')
def index():
    """Serve the main page"""
    return render_template('index.html')


@app.route('/api/health', methods=['GET'])
def health_check():
    """Health check endpoint"""
    return jsonify({
        'status': 'healthy',
        'gemini_configured': bool(GEMINI_API_KEY)
    })


@app.route('/api/check-claims', methods=['POST'])
def check_claims():
    """Main endpoint to check claims from YouTube video"""
    try:
        data = request.get_json()
        video_url = data.get('video_url')

        if not video_url:
            return jsonify({'error': 'Video URL is required'}), 400

        if not GEMINI_API_KEY:
            return jsonify({'error': 'Gemini API key not configured'}), 500

        # Step 1: Extract subtitles
        video_title, transcript = extract_subtitles(video_url)

        if not transcript or not video_title:
            error_msg = transcript if transcript else 'Could not extract subtitles from video'
            return jsonify({'error': error_msg}), 400

        # Step 2: Extract claims
        claims = extract_claims(transcript, video_title)

        if not claims or claims.startswith('Error'):
            return jsonify({'error': claims or 'Could not extract claims'}), 500

        # Step 3: Fact-check claims
        fact_check_results = fact_check_claims(claims)

        if not fact_check_results or fact_check_results.startswith('Error'):
            return jsonify({'error': fact_check_results or 'Could not fact-check claims'}), 500

        # Return results
        return jsonify({
            'success': True,
            'video_title': video_title,
            'video_url': video_url,
            'transcript_length': len(transcript),
            'claims': claims,
            'fact_check_results': fact_check_results
        })

    except Exception as e:
        print(f"❌ Unexpected error: {e}")
        return jsonify({'error': f'Unexpected error: {str(e)}'}), 500


if __name__ == '__main__':
    # Create templates directory if it doesn't exist
    os.makedirs('templates', exist_ok=True)

    print("🎥 YouTube Video Claim Checker - Flask Backend")
    print("=" * 50)

    if not GEMINI_API_KEY:
        print("❌ Please create a .env file with your Gemini API key:")
        print("GEMINI_API_KEY=your_api_key_here")
        print("🔑 Get your API key from: https://makersuite.google.com/app/apikey")
    else:
        print("✅ Gemini API configured")

    print("🚀 Starting Flask server...")
    app.run(debug=True, host='0.0.0.0', port=5000)

# Installation requirements:
# pip install flask flask-cors yt-dlp google-generativeai python-dotenv
