from flask import Flask, request, jsonify, render_template
from flask_cors import CORS
import yt_dlp
import google.generativeai as genai
import os
import re
import tempfile
import uuid
from dotenv import load_dotenv
import time
import random

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
    """Extract subtitles from YouTube video using /tmp directory"""
    print("🚀 Extracting subtitles...")

    # Use /tmp directory which is writable on Render
    temp_dir = '/tmp'
    temp_id = str(uuid.uuid4())[:8]
    temp_filename = os.path.join(temp_dir, f'temp_subtitle_{temp_id}')

    # Enhanced options to avoid bot detection
    options = {
        'writesubtitles': True,
        'writeautomaticsub': True,
        'subtitleslangs': ['en'],
        'subtitlesformat': 'srt',
        'outtmpl': f'{temp_filename}.%(ext)s',
        'skip_download': True,
        # Bot detection workarounds
        'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
        'referer': 'https://www.youtube.com/',
        'sleep_interval': 1,
        'max_sleep_interval': 5,
        'sleep_interval_subtitles': 1,
        # Reduce requests
        'extract_flat': False,
        'no_warnings': True,
        # Timeout settings
        'socket_timeout': 30,
        # Additional headers
        'http_headers': {
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-us,en;q=0.5',
            'Accept-Charset': 'ISO-8859-1,utf-8;q=0.7,*;q=0.7',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
        }
    }

    max_retries = 3
    for attempt in range(max_retries):
        try:
            print(f"🔄 Attempt {attempt + 1}/{max_retries}")
            print(f"📁 Using temp directory: {temp_dir}")
            
            # Check if /tmp is writable
            if not os.access(temp_dir, os.W_OK):
                return None, f"Cannot write to {temp_dir} directory"
            
            # Random delay to avoid rate limiting
            if attempt > 0:
                delay = random.uniform(2, 5)
                print(f"⏳ Waiting {delay:.1f}s before retry...")
                time.sleep(delay)

            with yt_dlp.YoutubeDL(options) as ydl:
                # First, just get video info without downloading
                info = ydl.extract_info(url, download=False)
                video_title = info.get('title', 'Unknown')
                
                # Check if subtitles are available
                subtitles = info.get('subtitles', {})
                auto_subtitles = info.get('automatic_captions', {})
                
                if not subtitles.get('en') and not auto_subtitles.get('en'):
                    return None, "No English subtitles available for this video"
                
                print(f"📹 Video: {video_title}")
                print("⬇️ Downloading subtitles...")
                
                # Now download subtitles
                ydl.download([url])

            # Look for subtitle file
            subtitle_file = f"{temp_filename}.en.srt"
            print(f"🔍 Looking for subtitle file: {subtitle_file}")
            
            if os.path.exists(subtitle_file):
                print("✅ Subtitle file found!")
                with open(subtitle_file, "r", encoding="utf-8") as f:
                    content = f.read()

                # Clean SRT format (remove timestamps and numbers)
                text = re.sub(r'\d+\n\d{2}:\d{2}:\d{2},\d{3} --> \d{2}:\d{2}:\d{2},\d{3}\n', '', content)
                text = re.sub(r'\n+', ' ', text).strip()

                # Clean up temp file
                try:
                    os.remove(subtitle_file)
                    print("🧹 Cleaned up temp file")
                except Exception as cleanup_error:
                    print(f"⚠️ Could not clean up temp file: {cleanup_error}")

                if not text or len(text) < 50:
                    return None, "Subtitles too short or empty"

                print(f"📝 Extracted {len(text)} characters of text")
                return video_title, text
            else:
                print("❌ Subtitle file not found")
                # List files in temp directory for debugging
                try:
                    temp_files = os.listdir(temp_dir)
                    matching_files = [f for f in temp_files if temp_id in f]
                    print(f"🔍 Files with temp_id {temp_id}: {matching_files}")
                except Exception as e:
                    print(f"❌ Could not list temp directory: {e}")
                
                if attempt == max_retries - 1:
                    return None, "Subtitle file not created - video may not have subtitles"
                continue

        except yt_dlp.utils.DownloadError as e:
            error_msg = str(e)
            print(f"❌ Download error (attempt {attempt + 1}): {error_msg}")
            
            if "Sign in to confirm" in error_msg or "bot" in error_msg.lower():
                print("🤖 Bot detection triggered, trying different strategy...")
                # Try with different user agent
                options['user_agent'] = f'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KTML, like Gecko) Chrome/{90 + attempt}.0.{4400 + attempt * 10}.{100 + attempt * 5} Safari/537.36'
                
            if attempt == max_retries - 1:
                return None, f"Failed after {max_retries} attempts: {error_msg}"
                
        except Exception as e:
            print(f"❌ Unexpected error (attempt {attempt + 1}): {e}")
            if attempt == max_retries - 1:
                return None, f"Unexpected error: {str(e)}"

    return None, "Failed to extract subtitles after all attempts"


def extract_claims(text, video_title):
    """Extract factual claims from video text using Gemini"""
    print("🔍 Extracting claims...")

    # Limit text to avoid memory issues and API limits
    max_text_length = 3000
    if len(text) > max_text_length:
        text = text[:max_text_length] + "..."

    prompt = f"""
    Analyze this video transcript and extract the main factual claims that can be fact-checked.

    Video Title: {video_title}

    Transcript: {text}

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
    temp_writable = os.access('/tmp', os.W_OK)
    return jsonify({
        'status': 'healthy',
        'gemini_configured': bool(GEMINI_API_KEY),
        'temp_directory_writable': temp_writable,
        'temp_directory': '/tmp'
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

        # Check if /tmp is writable
        if not os.access('/tmp', os.W_OK):
            return jsonify({'error': 'Temporary directory not writable on this server'}), 500

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
    # Create templates directory if it doesn't exist (this will only work locally)
    try:
        os.makedirs('templates', exist_ok=True)
    except:
        pass  # Ignore on read-only filesystem

    print("🎥 YouTube Video Claim Checker - Flask Backend")
    print("=" * 50)

    if not GEMINI_API_KEY:
        print("❌ Please create a .env file with your Gemini API key:")
        print("GEMINI_API_KEY=your_api_key_here")
        print("🔑 Get your API key from: https://makersuite.google.com/app/apikey")
    else:
        print("✅ Gemini API configured")

    # Check temp directory
    if os.access('/tmp', os.W_OK):
        print("✅ /tmp directory is writable")
    else:
        print("❌ /tmp directory is not writable")

    print("🚀 Starting Flask server...")
    
    # Use Render's assigned port or default to 5000 for local
    port = int(os.environ.get('PORT', 5000))
    app.run(debug=False, host='0.0.0.0', port=port)
