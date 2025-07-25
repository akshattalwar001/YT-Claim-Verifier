from flask import Flask, request, jsonify, render_template
from flask_cors import CORS
import yt_dlp
import google.generativeai as genai
import os
import re
from pathlib import Path
import tempfile
import uuid
import time
import random
from dotenv import load_dotenv

# Load environment variable
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


def get_yt_dlp_options(temp_filename):
    """Get optimized yt-dlp options to avoid bot detection"""
    return {
        'writesubtitles': True,
        'writeautomaticsub': True,
        'subtitleslangs': ['en', 'en-US', 'en-GB'],
        'subtitlesformat': 'srt',
        'outtmpl': f'{temp_filename}.%(ext)s',
        'skip_download': True,
        'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
        'extractor_args': {
            'youtube': {
                'player_client': ['web', 'tv_embedded', 'android'],
                'player_skip': ['webpage']
            }
        },
        'http_headers': {
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
            'Accept-Encoding': 'gzip, deflate',
            'DNT': '1',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
        },
        'sleep_interval': 1,
        'max_sleep_interval': 3,
        'ignoreerrors': False,
        'no_warnings': False,
        'extract_flat': False,
    }


def extract_subtitles(url, max_retries=3):
    """Extract subtitles from YouTube video with retry logic"""
    print("🚀 Extracting subtitles...")

    # Create unique temp filename
    temp_id = str(uuid.uuid4())[:8]
    temp_filename = f'temp_subtitle_{temp_id}'

    for attempt in range(max_retries):
        try:
            # Add random delay between attempts
            if attempt > 0:
                delay = random.uniform(2, 5)
                print(f"⏳ Retry attempt {attempt + 1}, waiting {delay:.1f}s...")
                time.sleep(delay)

            options = get_yt_dlp_options(temp_filename)
            
            with yt_dlp.YoutubeDL(options) as ydl:
                print(f"📥 Extracting video info (attempt {attempt + 1})...")
                info = ydl.extract_info(url, download=False)
                video_title = info.get('title', 'Unknown')
                
                print(f"📝 Downloading subtitles for: {video_title}")
                ydl.download([url])

            # Look for subtitle files with different naming patterns
            subtitle_files = [
                f"{temp_filename}.en.srt",
                f"{temp_filename}.en-US.srt",
                f"{temp_filename}.en-GB.srt",
            ]
            
            subtitle_content = None
            subtitle_file_found = None
            
            for subtitle_file in subtitle_files:
                if os.path.exists(subtitle_file):
                    subtitle_file_found = subtitle_file
                    with open(subtitle_file, "r", encoding="utf-8") as f:
                        subtitle_content = f.read()
                    break

            if subtitle_content:
                # Clean SRT format (remove timestamps and numbers)
                text = re.sub(r'\d+\n\d{2}:\d{2}:\d{2},\d{3} --> \d{2}:\d{2}:\d{2},\d{3}\n', '', subtitle_content)
                text = re.sub(r'\n+', ' ', text).strip()
                text = re.sub(r'<[^>]+>', '', text)  # Remove HTML tags
                text = re.sub(r'\s+', ' ', text)  # Normalize whitespace

                # Clean up temp file
                if subtitle_file_found:
                    os.remove(subtitle_file_found)

                print(f"✅ Successfully extracted {len(text)} characters of subtitles")
                return video_title, text
            else:
                print("❌ No subtitle files found")
                return None, "No subtitles found for this video"

        except yt_dlp.utils.DownloadError as e:
            error_msg = str(e)
            print(f"❌ Download error (attempt {attempt + 1}): {error_msg}")
            
            if "Sign in to confirm" in error_msg or "bot" in error_msg.lower():
                if attempt < max_retries - 1:
                    print("🤖 Bot detection triggered, trying different approach...")
                    continue
                else:
                    return None, "YouTube is blocking requests due to bot detection. Try using a different video or implementing cookie authentication."
            
            if attempt == max_retries - 1:
                return None, f"Failed to extract subtitles after {max_retries} attempts: {error_msg}"
                
        except Exception as e:
            print(f"❌ Unexpected error (attempt {attempt + 1}): {e}")
            if attempt == max_retries - 1:
                return None, f"Unexpected error: {str(e)}"

    return None, f"Failed to extract subtitles after {max_retries} attempts"


def extract_claims(text, video_title):
    """Extract factual claims from video text using Gemini"""
    print("🔍 Extracting claims...")

    # Truncate text if too long (Gemini has token limits)
    max_chars = 8000
    if len(text) > max_chars:
        text = text[:max_chars] + "..."
        print(f"📝 Truncated transcript to {max_chars} characters")

    prompt = f"""
    Analyze this video transcript and extract the main factual claims that can be fact-checked.

    Video Title: {video_title}

    Transcript: {text}

    Please:
    1. Identify 3-5 key factual claims made in the video
    2. Focus on specific, verifiable statements (numbers, dates, scientific facts, historical events, statistics)
    3. Ignore opinions, predictions, or subjective statements
    4. Format as a numbered list with clear, concise claims

    Example format:
    1. [Specific factual claim from the video]
    2. [Another factual claim with numbers/dates if mentioned]
    3. [Scientific or historical fact mentioned]
    """

    try:
        response = model.generate_content(prompt)
        print("✅ Claims extracted successfully")
        return response.text
    except Exception as e:
        print(f"❌ Error extracting claims: {e}")
        return f"Error extracting claims: {str(e)}"


def fact_check_claims(claims):
    """Fact-check the extracted claims using Gemini"""
    print("✅ Fact-checking claims...")

    prompt = f"""
    Please fact-check these claims from a YouTube video. For each claim, provide a thorough analysis:

    Claims to check:
    {claims}

    For each claim, provide:
    **Claim [Number]: [Restate the claim]**
    * **Status:** TRUE/FALSE/PARTIALLY TRUE/UNCLEAR
    * **Explanation:** [Detailed factual explanation with reasoning]
    * **Confidence:** High/Medium/Low

    Use reliable sources and current knowledge. If a claim cannot be verified, mark it as UNCLEAR and explain why.
    Format your response with clear markdown formatting using ** for bold text.
    """

    try:
        response = model.generate_content(prompt)
        print("✅ Fact-checking completed successfully")
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

        # Validate YouTube URL
        if 'youtube.com/watch' not in video_url and 'youtu.be/' not in video_url:
            return jsonify({'error': 'Please provide a valid YouTube video URL'}), 400

        print(f"🎥 Processing video: {video_url}")

        # Step 1: Extract subtitles
        video_title, transcript = extract_subtitles(video_url)

        if not transcript or video_title is None:
            error_msg = transcript if transcript else 'Could not extract subtitles from video'
            return jsonify({'error': error_msg}), 400

        if len(transcript.strip()) < 100:
            return jsonify({'error': 'Video transcript is too short or empty. This video may not have subtitles available.'}), 400

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
    print("📝 Server will be available at: http://localhost:5000")
    print("🔧 Tips for YouTube bot detection issues:")
    print("   - Try different videos if one fails")
    print("   - The system will retry failed requests automatically")
    print("   - Consider implementing cookie authentication for persistent issues")
    
    app.run(debug=True, host='0.0.0.0', port=5000)

# Installation requirements:
# pip install flask flask-cors yt-dlp google-generativeai python-dotenv
