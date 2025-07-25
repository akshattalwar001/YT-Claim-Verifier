from flask import Flask, request, jsonify, render_template
from flask_cors import CORS
import yt_dlp
import google.generativeai as genai
import os
import re
import io
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
else:
    genai.configure(api_key=GEMINI_API_KEY)
    model = genai.GenerativeModel('gemini-1.5-flash')


class InMemoryLogger:
    """Custom logger to capture yt-dlp output in memory"""
    def __init__(self):
        self.subtitles = None
        self.error = None
    
    def debug(self, msg):
        pass
    
    def info(self, msg):
        pass
    
    def warning(self, msg):
        pass
    
    def error(self, msg):
        self.error = msg


def extract_subtitles_memory(url):
    """Extract subtitles using yt-dlp without writing to disk"""
    print("🚀 Extracting subtitles (in-memory)...")
    
    # Custom hook to capture subtitle data
    subtitle_data = {}
    
    def subtitle_hook(d):
        if d['status'] == 'finished':
            subtitle_data['file'] = d['filename']
    
    options = {
        'writesubtitles': True,
        'writeautomaticsub': True,
        'subtitleslangs': ['en'],
        'subtitlesformat': 'srt',
        'skip_download': True,
        'no_warnings': True,
        'quiet': True,
        # Try to avoid bot detection
        'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'referer': 'https://www.youtube.com/',
        'sleep_interval': 1,
        'http_headers': {
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-us,en;q=0.5',
        }
    }
    
    max_retries = 3
    for attempt in range(max_retries):
        try:
            print(f"🔄 Attempt {attempt + 1}/{max_retries}")
            
            if attempt > 0:
                delay = random.uniform(1, 3)
                time.sleep(delay)
            
            with yt_dlp.YoutubeDL(options) as ydl:
                # Extract video info first
                info = ydl.extract_info(url, download=False)
                video_title = info.get('title', 'Unknown')
                
                # Check for subtitles in video info
                subtitles = info.get('subtitles', {})
                auto_subtitles = info.get('automatic_captions', {})
                
                subtitle_text = None
                
                # Try manual subtitles first
                if 'en' in subtitles:
                    subtitle_url = subtitles['en'][0]['url']
                    subtitle_text = download_subtitle_url(subtitle_url)
                
                # Fall back to auto-generated
                elif 'en' in auto_subtitles:
                    # Try different variants of English
                    for lang_variant in ['en', 'en-US', 'en-GB']:
                        if lang_variant in auto_subtitles:
                            subtitle_url = auto_subtitles[lang_variant][0]['url']
                            subtitle_text = download_subtitle_url(subtitle_url)
                            if subtitle_text:
                                break
                
                if subtitle_text:
                    # Clean the subtitle text
                    clean_text = clean_srt_text(subtitle_text)
                    return video_title, clean_text
                else:
                    if attempt == max_retries - 1:
                        return None, "No subtitles found for this video"
                        
        except Exception as e:
            print(f"❌ Error (attempt {attempt + 1}): {e}")
            if "Sign in to confirm" in str(e) or "bot" in str(e).lower():
                print("🤖 Bot detection - trying different approach")
                options['user_agent'] = f'Mozilla/5.0 (X11; Linux x86_64) Chrome/{90 + attempt}.0.{4400 + attempt * 10}.{100 + attempt * 5}'
            
            if attempt == max_retries - 1:
                return None, f"Failed after {max_retries} attempts: {str(e)}"
    
    return None, "Failed to extract subtitles"


def download_subtitle_url(url):
    """Download subtitle content from URL"""
    try:
        import urllib.request
        with urllib.request.urlopen(url) as response:
            return response.read().decode('utf-8')
    except Exception as e:
        print(f"❌ Error downloading subtitle: {e}")
        return None


def clean_srt_text(srt_content):
    """Clean SRT subtitle format to plain text"""
    # Remove subtitle numbers and timestamps
    text = re.sub(r'\d+\n\d{2}:\d{2}:\d{2},\d{3} --> \d{2}:\d{2}:\d{2},\d{3}\n', '', srt_content)
    # Remove extra newlines and clean up
    text = re.sub(r'\n+', ' ', text).strip()
    # Remove HTML-like tags that sometimes appear in subtitles
    text = re.sub(r'<[^>]+>', '', text)
    return text


def extract_claims(text, video_title):
    """Extract factual claims from video text using Gemini"""
    print("🔍 Extracting claims...")
    
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
    return jsonify({
        'status': 'healthy',
        'gemini_configured': bool(GEMINI_API_KEY),
        'processing_mode': 'in-memory'
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

        # Step 1: Extract subtitles (in-memory)
        video_title, transcript = extract_subtitles_memory(video_url)

        if not transcript:
            return jsonify({'error': 'Could not extract subtitles from video'}), 400

        if video_title is None:
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
    print("🎥 YouTube Video Claim Checker - In-Memory Processing")
    print("=" * 50)

    if not GEMINI_API_KEY:
        print("❌ Please set GEMINI_API_KEY environment variable")
    else:
        print("✅ Gemini API configured")
        print("💾 Using in-memory processing (no file system)")

    port = int(os.environ.get('PORT', 5000))
    app.run(debug=False, host='0.0.0.0', port=port)
