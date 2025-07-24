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


def extract_subtitles(url):
    """Extract subtitles from YouTube video"""
    print("🚀 Extracting subtitles...")

    # Create unique temp filename
    temp_id = str(uuid.uuid4())[:8]
    temp_filename = f'temp_subtitle_{temp_id}'

    options = {
        'writesubtitles': True,
        'writeautomaticsub': True,
        'subtitleslangs': ['en'],
        'subtitlesformat': 'srt',
        'outtmpl': f'{temp_filename}.%(ext)s',
        'skip_download': True,
    }

    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(url, download=False)
            video_title = info.get('title', 'Unknown')
            ydl.download([url])

        # Read subtitle file
        subtitle_file = f"{temp_filename}.en.srt"
        if os.path.exists(subtitle_file):
            with open(subtitle_file, "r", encoding="utf-8") as f:
                content = f.read()

            # Clean SRT format (remove timestamps and numbers)
            text = re.sub(r'\d+\n\d{2}:\d{2}:\d{2},\d{3} --> \d{2}:\d{2}:\d{2},\d{3}\n', '', content)
            text = re.sub(r'\n+', ' ', text).strip()

            # Clean up temp file
            os.remove(subtitle_file)

            return video_title, text
        else:
            return None, "No subtitles found for this video"

    except Exception as e:
        print(f"❌ Error extracting subtitles: {e}")
        return None, str(e)


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

        if not transcript:
            return jsonify({'error': 'Could not extract subtitles from video'}), 400

        if video_title is None:  # Error case
            return jsonify({'error': transcript}), 400

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